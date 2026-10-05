#!/bin/sh
# Sets the IDAX Ledger runtime role password from its secret file. Same pattern as deploy/provision-runtime.sh: the password
# never appears in a command line or a migration. Runs once per deployment, after the ledger migrations.
set -eu
export PGPASSWORD="$(cat /run/secrets/postgres_password)"
export RUNTIME_PASSWORD="$(cat /run/secrets/runtime_password)"
psql -h ledger-postgres -U postgres -d idax -v ON_ERROR_STOP=1 <<'SQL'
\getenv runtime_password RUNTIME_PASSWORD
SELECT format('ALTER ROLE idax_backend LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD %L', :'runtime_password') \gexec
SQL
