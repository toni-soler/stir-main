#!/bin/sh
set -eu
export IDAX_LOCAL_DEMO_PASSWORD="$(cat /run/secrets/login_password)"
export DATABASE_PASSWORD="$(cat /run/secrets/runtime_password)"
exec java -jar /app/app.jar --spring.web.resources.static-locations=file:/app/public/
