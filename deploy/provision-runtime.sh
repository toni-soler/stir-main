#!/bin/sh
set -eu
export PGPASSWORD="$(cat /run/secrets/postgres_password)"
export STIR_RUNTIME_PASSWORD="$(cat /run/secrets/runtime_password)"
psql -h postgres -U postgres -d idax -v ON_ERROR_STOP=1 <<'SQL'
\getenv runtime_password STIR_RUNTIME_PASSWORD
SELECT format('ALTER ROLE idax_backend LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD %L', :'runtime_password') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON DATABASE idax FROM PUBLIC;
GRANT USAGE ON SCHEMA idax_shell TO idax_backend;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA idax_shell TO idax_backend;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA idax_shell TO idax_backend;
SQL
