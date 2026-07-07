#!/bin/sh
# Bring the schema up to date, then hand off to the container command (uvicorn
# by default). The image ships no schema, so migrations must run on start.
set -e

echo "==> alembic upgrade head"
alembic upgrade head

exec "$@"
