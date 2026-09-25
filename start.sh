#!/bin/sh
# Start bgutil PO Token server (port 4416) in background — log output to stdout
cd /bgutil/server && node build/main.js > /proc/1/fd/1 2>&1 &
# Wait briefly for bgutil to bind
sleep 5
# Start the REST API (Railway sets $PORT)
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
