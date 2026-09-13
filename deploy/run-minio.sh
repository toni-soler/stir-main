#!/bin/sh
set -eu
export MINIO_ROOT_USER="$(cat /run/secrets/storage_access_key)"
export MINIO_ROOT_PASSWORD="$(cat /run/secrets/storage_secret_key)"
exec minio server /data --console-address ":9001"
