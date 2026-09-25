#!/bin/sh
# Start bgutil PO Token server (port 4416) in background
cd /bgutil/server && node build/main.js &
# Wait briefly for bgutil to bind
sleep 3
# Start the REST API (Railway sets $PORT)
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
