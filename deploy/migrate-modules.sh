#!/bin/sh
set -eu
password="$(cat /run/secrets/postgres_password)"
for schema in idax_shell stir idax_ledger ostris; do
  history=flyway_schema_history
  if [ "$schema" = idax_shell ]; then history=flyway_schema_history_idax_shell; fi
  flyway -url=jdbc:postgresql://postgres:5432/idax -user=postgres -password="$password" -schemas="$schema" -table="$history" -locations="filesystem:/migrations/$schema" migrate
done
