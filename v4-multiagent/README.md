# V4 — Multi-agent app (coordinator → researcher → reviewer)

Same OpenLLMetry instrumentation as v1–v3, but the app is now a small
**multi-agent system** with tool calls — so the trace shows agent hand-offs,
not just one LLM call.

```
POST /chat
  chat.workflow
  └─ coordinator.agent
     ├─ researcher.agent ── openai.chat (make search query)
     │                   ├─ wikipedia_search.tool
     │                   └─ wikipedia_summary.tool ×2
     ├─ draft_answer.task ── openai.chat
     └─ reviewer.agent ───── openai.chat (fact-check the draft)
```

- **LLM:** local [Ollama](https://ollama.com) `qwen2.5:1.5b` (free, CPU-only, no API key).
  Any OpenAI-compatible endpoint works via `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`.
- **Tools:** public Wikipedia REST API (no key).
- **Export path:** pick with `TRACE_MODE` — `v1` direct to Dynatrace, `v2` via
  Bindplane collector, `v3` via local OneAgent. Same pipelines as the earlier stages.

What changed vs v1: `@agent`, `@task`, `@tool` decorators from
`traceloop.sdk.decorators` around each step. That's all the extra
instrumentation — Traceloop + Dynatrace do the rest.

## Run

```bash
ollama pull qwen2.5:1.5b          # once
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env              # fill in, then:
set -a; . ./.env; set +a
./venv/bin/python app.py
```

Open http://localhost:8080 and ask something, or:

```bash
curl -s localhost:8080/chat -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is the capital of Indonesia?"}]}'
```

`"mode": "simple"` in the body skips the agents (single LLM call) — useful to
compare latency and token cost with and without the agent chain.

## What you see in Dynatrace

**Distributed Tracing** — full agent chain in one trace:

![trace waterfall](docs/02-trace-waterfall.png)

**AI Observability → Agents topology** — agents, models and app discovered
from `gen_ai.*` span attributes, no config:

![agents topology](docs/05-agents-topology.png)

**AI Observability → Prompts** — every prompt/response pair per agent:

![prompts stream](docs/07-prompts-stream.png)

> Prompts and responses are captured by default. Set
> `TRACELOOP_TRACE_CONTENT=false` if they may contain sensitive data.
