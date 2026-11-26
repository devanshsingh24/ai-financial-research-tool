#!/usr/bin/env bash
# Restore the FinSight database from a dump made by scripts/backup.sh.
#
#   scripts/restore.sh backups/finsight_20261126_120000.dump          # asks to confirm
#   scripts/restore.sh --yes backups/finsight_20261126_120000.dump    # no prompt
#   COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml" scripts/restore.sh FILE
#
# REPLACES all data in the database. Steps: stop api + cleanup (no writes during the
# restore), drop and recreate the database, pg_restore, re-apply roles/grants, run the
# migrate service, start api + cleanup again and wait for /health.
set -euo pipefail
cd "$(dirname "$0")/.."

yes=0
if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then yes=1; shift; fi
file=${1:-}
if [ -z "$file" ] || [ ! -f "$file" ]; then
  echo "usage: scripts/restore.sh [--yes] backups/<file>.dump" >&2
  exit 2
fi

COMPOSE_FILES=${COMPOSE_FILES:-}
# shellcheck disable=SC2086
compose() { docker compose $COMPOSE_FILES "$@"; }

db_name=$(compose exec -T db sh -c 'printf %s "$POSTGRES_DB"')
MSYS_NO_PATHCONV=1 compose exec -T db pg_restore --list < "$file" > /dev/null \
  || { echo "$file is not a valid pg_dump custom-format file." >&2; exit 2; }

if [ "$yes" -ne 1 ]; then
  read -r -p "This REPLACES all data in database '$db_name' with $file. Type the database name to continue: " answer
  [ "$answer" = "$db_name" ] || { echo "Aborted."; exit 1; }
fi

echo "Stopping api and cleanup ..."
compose stop api cleanup

echo "Recreating database '$db_name' ..."
MSYS_NO_PATHCONV=1 compose exec -T db sh -c '
  set -e
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
    -c "CREATE DATABASE \"$POSTGRES_DB\""'

echo "Restoring $file ..."
MSYS_NO_PATHCONV=1 compose exec -T db sh -c \
  'pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-owner --no-privileges --exit-on-error' \
  < "$file"

echo "Re-applying roles and grants ..."
MSYS_NO_PATHCONV=1 compose exec -T db bash /docker-entrypoint-initdb.d/01-roles.sh > /dev/null

echo "Running migrations ..."
compose run --rm migrate

echo "Starting api and cleanup ..."
compose up -d api cleanup
port=${API_PORT:-8000}
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${port}/health" > /dev/null 2>&1; then
    echo "Restore complete; /health is OK."
    exit 0
  fi
  sleep 3
done
echo "Restore finished, but /health did not turn healthy; check: docker compose logs api" >&2
exit 1
