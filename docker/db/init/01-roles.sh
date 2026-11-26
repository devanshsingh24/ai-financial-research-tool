#!/bin/bash
# Runs once, when the Postgres data volume is first initialised (docker-entrypoint-initdb.d),
# as the superuser POSTGRES_USER. Creates:
#   finsight          the application database (POSTGRES_DB)
#   finsight_test     a database for the test suite
#   vector extension  in both (the long-term memory index needs pgvector)
#   finsight_app      least-privilege role used by the API and cleanup: read/write rows only,
#                     no CREATE/ALTER/DROP (schema changes go through the "migrate" service)
#   finsight_readonly read-only role for DBeaver and ad-hoc inspection
# Passwords come from the environment (APP_DB_PASSWORD, READONLY_DB_PASSWORD), never this file.
# To re-run it on an existing volume: scripts/db-roles.sh (same SQL, idempotent).
set -euo pipefail

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"
: "${READONLY_DB_PASSWORD:?READONLY_DB_PASSWORD must be set}"
APP_DB_USER="${APP_DB_USER:-finsight_app}"
READONLY_DB_USER="${READONLY_DB_USER:-finsight_readonly}"
TEST_DB="${TEST_DB:-finsight_test}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" \
  -v ro_user="$READONLY_DB_USER" -v ro_password="$READONLY_DB_PASSWORD" \
  -v test_db="$TEST_DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('CREATE ROLE %I LOGIN', :'ro_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro_user') \gexec
ALTER ROLE :"app_user" WITH LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 60;
ALTER ROLE :"ro_user" WITH LOGIN PASSWORD :'ro_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 10;
ALTER ROLE :"ro_user" SET default_transaction_read_only = on;
ALTER ROLE :"ro_user" SET statement_timeout = '60s';
SELECT format('CREATE DATABASE %I', :'test_db')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'test_db') \gexec
SQL

for database in "$POSTGRES_DB" "$TEST_DB"; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" \
    -v db="$database" -v owner="$POSTGRES_USER" -v app_user="$APP_DB_USER" -v ro_user="$READONLY_DB_USER" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
-- Nobody but the owner may create objects in public (PG15+ default; stated explicitly).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"app_user";
GRANT CONNECT ON DATABASE :"db" TO :"ro_user";
GRANT USAGE ON SCHEMA public TO :"app_user", :"ro_user";

-- Tables that exist now (re-runs) ...
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"ro_user";
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO :"ro_user";
-- ... and every table the owner (the migrate service) creates later.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"ro_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO :"ro_user";
SQL
done

echo "FinSight roles ready: $APP_DB_USER (read/write rows), $READONLY_DB_USER (read-only); databases $POSTGRES_DB, $TEST_DB"
