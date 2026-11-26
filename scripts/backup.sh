#!/usr/bin/env bash
# Back up the FinSight database to ./backups/finsight_YYYYmmdd_HHMMSS.dump
# (pg_dump custom format: compressed, restorable with scripts/restore.sh).
#
#   scripts/backup.sh                 # dev stack
#   COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml" scripts/backup.sh   # prod
#   KEEP=30 scripts/backup.sh         # keep the newest 30 dumps (default 14)
#
# Runs pg_dump inside the db container as the database owner, so no password is needed
# on the host. Works from Git Bash, WSL, Linux and macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE_FILES=${COMPOSE_FILES:-}
KEEP=${KEEP:-14}
BACKUP_DIR=${BACKUP_DIR:-backups}
# shellcheck disable=SC2086
compose() { docker compose $COMPOSE_FILES "$@"; }

mkdir -p "$BACKUP_DIR"
stamp=$(date +%Y%m%d_%H%M%S)
db_name=$(compose exec -T db sh -c 'printf %s "$POSTGRES_DB"')
file="$BACKUP_DIR/${db_name}_${stamp}.dump"
tmp="$file.partial"

echo "Backing up database '$db_name' to $file ..."
# MSYS_NO_PATHCONV stops Git Bash rewriting the container-side arguments.
MSYS_NO_PATHCONV=1 compose exec -T db sh -c \
  'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --compress=6 --no-owner --no-privileges' \
  > "$tmp"
# A truncated dump must never look like a good one: check it lists its contents.
MSYS_NO_PATHCONV=1 compose exec -T db pg_restore --list < "$tmp" > /dev/null
mv "$tmp" "$file"
echo "Done: $file ($(du -h "$file" | cut -f1))"

# Keep only the newest $KEEP dumps.
mapfile -t old < <(ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | tail -n +"$((KEEP + 1))")
if [ "${#old[@]}" -gt 0 ]; then
  rm -f -- "${old[@]}"
  echo "Removed ${#old[@]} old backup(s); keeping the newest $KEEP."
fi
