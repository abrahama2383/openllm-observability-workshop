# OpenLLM Observability Workshop

A hands-on exercise: take a small LLM chat app with **zero observability**,
and add it back in three different ways, so you can feel the trade-offs
instead of just reading about them.

Every stage is the same app — a one-file Python chat server calling a free,
OpenAI-compatible LLM ([Pollinations](https://pollinations.ai), no API key
needed) — instrumented with [OpenLLMetry](https://github.com/traceloop/openllmetry)
(`traceloop-sdk`), sent to [Dynatrace](https://www.dynatrace.com) by a
different path each time.

## Stages

| Stage | What it adds | Read |
|---|---|---|
| [`00-base`](00-base) | The app, uninstrumented. Your starting point. | [README](00-base/README.md) |
| [`v1-dynatrace-direct`](v1-dynatrace-direct) | Instrument it; export straight to Dynatrace SaaS | [README](v1-dynatrace-direct/README.md) |
| [`v2-bindplane`](v2-bindplane) | Route through a local Bindplane collector | [README](v2-bindplane/README.md) |
| [`v3-oneagent`](v3-oneagent) | Route through the Dynatrace OneAgent already on the host | [README](v3-oneagent/README.md) |
| [`v4-multiagent`](v4-multiagent) | Multi-agent app (coordinator → researcher → reviewer, Wikipedia tools, local Ollama) on any of the three paths | [README](v4-multiagent/README.md) |

Do them in order — each stage's README opens with a diff against the
previous one, so you see exactly what instrumentation cost you in code.

## Prerequisites

- Python 3.10+
- A Dynatrace SaaS tenant + an API token (v1, v3) — free trial works
- For v2: a [Bindplane](https://bindplane.com) account and an agent
  installed on the machine running the app
- For v3: Dynatrace OneAgent already installed on the machine running the app

No LLM API key needed — Pollinations is free and anonymous. Swap in any
other OpenAI-compatible provider (OpenRouter, Groq, a local Ollama) via
`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` if you'd rather use one.

## Why three versions

They're not "better" in a strict order — they're different trade-offs:

- **v1 (direct)** — simplest to wire up, but every app instance needs its
  own Dynatrace token, and there's no place to filter or transform data
  before it leaves the process.
- **v2 (Bindplane)** — one token lives in the collector, not the app; you
  get a place to redact, enrich, or fan out data (see the exercises in its
  README); costs you an extra process to run and operate.
- **v3 (OneAgent)** — no extra process, no token anywhere, and spans link
  automatically to the host entity; but it's traces-only, and the local
  endpoint has to be explicitly turned on per tenant.

Pick the one that matches your constraints, or run all three in parallel
against different `openllm.pipeline` values and compare them side by side
in Dynatrace.
