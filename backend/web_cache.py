"""SQLite cache for Tavily results, so a repeated search costs 0 credits."""

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import DATA_DIR

log = logging.getLogger("finsight.web")

_CACHE_PATH = DATA_DIR / "tavily_cache.sqlite"
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(_CACHE_PATH, check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " key TEXT PRIMARY KEY, query TEXT, topic TEXT, time_range TEXT,"
            " results_json TEXT, created_at TEXT)"
        )
        _conn.commit()
    return _conn


def cache_key(query: str, topic: str, time_range: Optional[str]) -> str:
    raw = json.dumps([query.strip().lower(), topic, time_range])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(query: str, topic: str, time_range: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    with _lock:
        row = _db().execute(
            "SELECT results_json FROM cache WHERE key = ?", (cache_key(query, topic, time_range),)
        ).fetchone()
    return json.loads(row[0]) if row else None


def put(query: str, topic: str, time_range: Optional[str], results: List[Dict[str, Any]]) -> None:
    if not results:
        return  # never cache failures or empty results
    with _lock:
        _db().execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?, ?)",
            (
                cache_key(query, topic, time_range),
                query,
                topic,
                time_range,
                json.dumps(results),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _db().commit()
