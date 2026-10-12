#!/bin/sh
# Start the router app on :8081. Reads .env, overridable: ./run.sh v1|v2|v3
V=${1:-v1}
case "$V" in v1|v2|v3) ;; *) echo "usage: $0 v1|v2|v3"; exit 1;; esac
cd "$(dirname "$0")"
pkill -f "[p]ython $PWD/app.py" ; sleep 1
set -a; [ -f ./.env ] && . ./.env; set +a
TRACE_MODE=$V PORT=${PORT:-8081} nohup python3 "$PWD/app.py" > app.log 2>&1 &
sleep 4; cat app.log
