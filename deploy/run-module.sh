#!/bin/sh
set -eu
export SPRING_DATASOURCE_PASSWORD="$(cat /run/secrets/runtime_password)"
exec java -jar /app/app.jar
