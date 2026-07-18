#!/bin/sh
set -eu

umask 0002

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec python -m uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips '*'
