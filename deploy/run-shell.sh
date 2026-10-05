#!/bin/sh
set -eu
export IDAX_LOCAL_DEMO_PASSWORD="$(cat /run/secrets/login_password)"
export DATABASE_PASSWORD="$(cat /run/secrets/runtime_password)"
# Extra Spring arguments may be supplied as the container command (used by the IDAX Ledger integration for the
# platform service-token key); with no arguments the behaviour is unchanged.
exec java -jar /app/app.jar --spring.web.resources.static-locations=file:/app/public/ "$@"
