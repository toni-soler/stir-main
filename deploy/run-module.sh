#!/bin/sh
set -eu
export SPRING_DATASOURCE_PASSWORD="$(cat /run/secrets/runtime_password)"
# Optional service credential read from a secret file, never from a compose environment value. Used by ostris to
# authenticate to the platform for IDAX Ledger delivery.
if [ -n "${OSTRIS_LEDGER_SERVICE_CLIENT_SECRET_FILE:-}" ]; then
  export OSTRIS_LEDGER_SERVICE_CLIENT_SECRET="$(cat "$OSTRIS_LEDGER_SERVICE_CLIENT_SECRET_FILE")"
fi
exec java -jar /app/app.jar
