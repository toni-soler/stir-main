#!/bin/sh
set -eu
# Mirrors deploy/provision-audit.sh for the governed-state verifier role: sets idax_governed_verifier's
# password from its own secret, never inline in a migration. Runs as postgres after V20 has created
# the role. The ordinary runtime credential never sees this secret.
export PGPASSWORD="$(cat /run/secrets/postgres_password)"
export GOVERNED_VERIFIER_PASSWORD_VALUE="$(cat /run/secrets/governed_verifier_password)"
psql -h postgres -U postgres -d idax -v ON_ERROR_STOP=1 <<'SQL'
\getenv verifier_password GOVERNED_VERIFIER_PASSWORD_VALUE
SELECT format('ALTER ROLE idax_governed_verifier LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :'verifier_password') \gexec
SQL
