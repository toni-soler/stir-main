#!/bin/sh
set -eu
# Mirrors deploy/provision-runtime.sh exactly, for a different role: sets stir_auditor's password
# from its own secret, never inline in the STIR Flyway migration (V18 creates the role with LOGIN
# and no password - see that migration's own comment on why). Runs as postgres against the same
# database migrations already provisioned, after module-migrations has applied V18.
export PGPASSWORD="$(cat /run/secrets/postgres_password)"
export STIR_AUDITOR_PASSWORD_VALUE="$(cat /run/secrets/stir_auditor_password)"
psql -h postgres -U postgres -d idax -v ON_ERROR_STOP=1 <<'SQL'
\getenv auditor_password STIR_AUDITOR_PASSWORD_VALUE
SELECT format('ALTER ROLE stir_auditor LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD %L', :'auditor_password') \gexec
SQL
