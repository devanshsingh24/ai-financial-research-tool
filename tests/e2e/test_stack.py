"""End-to-end tests against the real Docker stack (docker-compose.e2e.yml).

Skipped unless FINSIGHT_E2E=1. Run from the repo root on the host (needs Docker):
    FINSIGHT_E2E=1 pytest tests/e2e -q
The fixture builds and starts project "finsight-e2e" (own volumes, API on 127.0.0.1:8100),
and removes it with its volumes afterwards (FINSIGHT_E2E_KEEP=1 keeps it).
"""

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.getenv("FINSIGHT_E2E") != "1", reason="set FINSIGHT_E2E=1 (needs Docker)")

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "finsight-e2e"
BASE = f"http://127.0.0.1:{os.getenv('E2E_API_PORT', '8100')}"
ALICE = {"X-User-Id": "alice"}
BOB = {"X-User-Id": "bob"}
QUESTION = {"question": "What is NVIDIA's latest revenue?"}  # "latest" -> can't answer locally -> gate


def compose(*args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", "docker-compose.yml", "-f", "docker-compose.e2e.yml", *args]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    return result


def wait_health(expect: int = 200, timeout: float = 180) -> dict:
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=5)
            last = (r.status_code, r.text)
            if r.status_code == expect:
                return r.json()
        except httpx.HTTPError as exc:
            last = repr(exc)
        time.sleep(2)
    raise AssertionError(f"/health never returned {expect}; last: {last}")


def restart_api() -> None:
    compose("restart", "api")
    wait_health()


@pytest.fixture(scope="module")
def stack():
    compose("up", "-d", "--build", "db", "migrate", "api", "cleanup")
    wait_health()
    yield
    if os.getenv("FINSIGHT_E2E_KEEP") != "1":
        compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture
def api(stack):
    with httpx.Client(base_url=BASE, timeout=60) as client:
        yield client


def test_health_reports_postgres(api):
    body = api.get("/health").json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["persistence"] == "postgres"


def test_paused_thread_survives_api_container_restarts(api):
    tid = api.post("/threads", json={"title": "e2e"}, headers=ALICE).json()["thread_id"]
    gate = api.post(f"/threads/{tid}/messages", json=QUESTION, headers=ALICE).json()
    assert gate["status"] == "needs_confirmation" and gate["kind"] == "web_search"

    restart_api()  # the API process (and both workers) die; Postgres keeps everything
    state = api.get(f"/threads/{tid}", headers=ALICE).json()
    assert state["thread"]["status"] == "interrupted"
    assert state["next"] == ["ask_permission"]
    assert state["pending"]["kind"] == "web_search"
    assert state["pending"]["search"] == gate["search"]
    assert tid in [t["thread_id"] for t in api.get("/threads", headers=ALICE).json()]
    assert api.get(f"/threads/{tid}", headers=BOB).status_code == 404  # still isolated

    report = api.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE).json()
    assert report["status"] == "needs_confirmation" and report["kind"] == "report"

    restart_api()  # restart again, now paused at the report gate
    assert api.get(f"/threads/{tid}", headers=ALICE).json()["next"] == ["review_report"]
    done = api.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE).json()
    assert done["status"] == "answered" and done["web_search"]["used"] is True

    state = api.get(f"/threads/{tid}", headers=ALICE).json()
    assert state["thread"]["status"] == "completed"
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]
    history = api.get(f"/threads/{tid}/history", headers=ALICE).json()
    assert len(history) > 5 and history[0]["next"] == []


def test_idempotent_retry_across_workers_and_restart(api):
    headers = {**ALICE, "Idempotency-Key": "e2e-create-1"}
    first = api.post("/threads", json={"title": "idem"}, headers=headers)
    restart_api()
    again = api.post("/threads", json={"title": "idem"}, headers=headers)
    assert first.status_code == again.status_code == 201
    assert again.json()["thread_id"] == first.json()["thread_id"]
    assert again.headers.get("Idempotent-Replayed") == "true"


def test_health_fails_fast_when_db_is_down_and_recovers(api):
    compose("stop", "db")
    try:
        r = httpx.get(f"{BASE}/health", timeout=10)
        assert r.status_code == 503 and r.json()["status"] == "degraded"
    finally:
        compose("start", "db")
    wait_health(200, timeout=120)  # the pool reconnects; no API restart needed


def test_api_refuses_to_start_without_a_database(stack):
    result = compose("run", "--rm", "--no-deps", "-e", "DATABASE_URL=postgresql://nobody:secret@no-such-host:5432/x",
                     "-e", "DB_CONNECT_TIMEOUT=3", "api", check=False, timeout=300)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Cannot reach PostgreSQL" in output and "no-such-host" in output
    assert "secret" not in output  # the password is masked
