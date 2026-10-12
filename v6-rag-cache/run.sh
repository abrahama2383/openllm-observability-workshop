#!/bin/sh
# Start openllmrouterv6 on :8082. Reads .env, overridable: ./run.sh v1|v2|v3
V=${1:-v1}
case "$V" in v1|v2|v3) ;; *) echo "usage: $0 v1|v2|v3"; exit 1;; esac
cd "$(dirname "$0")"
pkill -f "[p]ython $PWD/app.py" ; sleep 1
set -a; [ -f ./.env ] && . ./.env; set +a
TRACE_MODE=$V PORT=${PORT:-8082} DOCS_DIR=${DOCS_DIR:-./kb} \
  EMBED_MODEL=${EMBED_MODEL:-nomic-embed-text} \
  REDIS_URL=${REDIS_URL:-redis://127.0.0.1:6379/0} \
  nohup python3 "$PWD/app.py" > app.log 2>&1 &
sleep 5; cat app.log
