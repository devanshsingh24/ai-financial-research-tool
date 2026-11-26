#!/bin/sh
# FinSight container entrypoint.
#   api      prestart checks (DB reachable, schema migrated, crashed runs marked failed),
#            then uvicorn (WEB_CONCURRENCY workers, or --reload when UVICORN_RELOAD=1)
#   migrate  Alembic + LangGraph migrations as the DB owner, then exit
#   cleanup  delete old checkpoints / idempotency keys every CLEANUP_INTERVAL_HOURS
#   other    run the given command (e.g. "pytest -q", "bash")
set -eu

case "${1:-api}" in
  api)
    python -m backend.prestart
    app_module="${APP_MODULE:-backend.main:app}"   # the e2e tests swap in tests.e2e.fake_app:app
    if [ "${UVICORN_RELOAD:-0}" = "1" ]; then
      exec uvicorn "$app_module" --host 0.0.0.0 --port "${PORT:-8000}" \
        --reload --reload-dir backend --proxy-headers
    fi
    exec uvicorn "$app_module" --host 0.0.0.0 --port "${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
      --timeout-graceful-shutdown 30
    ;;
  migrate)
    exec python -m backend.migrate
    ;;
  cleanup)
    exec python -m backend.housekeeping --loop
    ;;
  *)
    exec "$@"
    ;;
esac
