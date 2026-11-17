#!/bin/sh
# v1c (OpenInference) on :8086 against local Ollama. Logs: app.log
# Reads DT_OTLP_ENDPOINT / DT_API_TOKEN from ~/openLLMdemo/.env (or set them yourself).
cd "$(dirname "$0")"
pkill -f "[o]penLLMv1c/venv/bin/python app.py"; sleep 1
[ -f ~/openLLMdemo/.env ] && { set -a; . ~/openLLMdemo/.env; set +a; }
LLM_BASE_URL=http://127.0.0.1:11434/v1 LLM_MODEL=qwen2.5:1.5b PORT=${PORT:-8086} \
  setsid nohup "$PWD/venv/bin/python" app.py > app.log 2>&1 < /dev/null &
sleep 4; cat app.log
