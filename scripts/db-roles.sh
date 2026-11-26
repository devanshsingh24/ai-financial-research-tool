#!/usr/bin/env bash
# Re-apply the database roles, passwords and grants (docker/db/init/01-roles.sh) on an
# existing database volume, e.g. after changing APP_DB_PASSWORD / READONLY_DB_PASSWORD in
# .env (then restart: docker compose up -d --force-recreate api cleanup). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE_FILES=${COMPOSE_FILES:-}
# shellcheck disable=SC2086
MSYS_NO_PATHCONV=1 docker compose $COMPOSE_FILES exec -T db bash /docker-entrypoint-initdb.d/01-roles.sh
