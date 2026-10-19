# V1a — Plain OpenTelemetry instead of OpenLLMetry

```
[chat app] --OTLP (traces + metrics)--> [Dynatrace SaaS /api/v2/otlp]
```

Same path as [v1](../v1-dynatrace-direct): straight to Dynatrace with an API
token. The difference is the instrumentation: the official OpenTelemetry SDK
plus the OpenTelemetry contrib package
[`opentelemetry-instrumentation-openai-v2`](https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation-genai/opentelemetry-instrumentation-openai-v2),
no `traceloop-sdk`. Spans land in service `openllmdemo-v1a`, so you can run it
next to v1 and compare.

## Run it

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # same DT_OTLP_ENDPOINT / DT_API_TOKEN as v1
set -a; . ./.env; set +a
./venv/bin/python app.py
```

Defaults to local Ollama (`qwen2.5:1.5b`); override with `LLM_BASE_URL` / `LLM_MODEL`.

## What changes in code (vs v1)

| | v1 OpenLLMetry | v1a OpenTelemetry |
|---|---|---|
| Setup | `Traceloop.init(...)` — one call | ~15 lines: `TracerProvider`, `MeterProvider`, 2 OTLP exporters, delta temporality |
| Patch the openai client | automatic (Traceloop loads ~30 instrumentations) | `OpenAIInstrumentor().instrument()` — only OpenAI |
| App-level span | `@workflow(name="chat")` decorator | `with tracer.start_as_current_span("chat")` |
| Conversation id | `Traceloop.set_association_properties(...)` | `span.set_attribute("gen_ai.conversation.id", ...)` |
| Prompt/response capture | **on by default** (`TRACELOOP_TRACE_CONTENT=false` to turn off) | **off by default**; opt in with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=span_only` + `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` |
| Install (measured) | 92 packages, 115 MB venv | 36 packages, 75 MB venv |

## What changes in Dynatrace (same question, measured)

| | v1 OpenLLMetry | v1a OpenTelemetry |
|---|---|---|
| LLM span name | `openai.chat` | `chat qwen2.5:1.5b` (semconv `{operation} {model}`) |
| Core `gen_ai.*` (model, provider, operation, tokens in/out, finish reason, response id) | yes | yes |
| `gen_ai.input/output.messages` | yes | yes (once opted in) |
| Extras | `gen_ai.usage.total_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.is_streaming`, `gen_ai.openai.api_base`, system fingerprint | none — strictly the semconv |
| Wrapper span | `chat.workflow` + `traceloop.entity.input/output` (function args and return value, i.e. the prompt again) | `chat` with only the attributes you set |
| Conversation id | `traceloop.association.properties.conversation_id` (vendor key) | `gen_ai.conversation.id` (semconv key AI Observability groups on) |
| Metrics | `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, `gen_ai.client.generation.choices` | `gen_ai.client.token.usage`, `gen_ai.client.operation.duration` |

Both show up in AI Observability: it keys on the `gen_ai.*` attributes, not on
the SDK.

## Which one?

- **OpenLLMetry** — fastest start, one line, covers many LLM/vector-DB/framework
  libraries at once, agent/tool decorators (`@agent`, `@tool`, see v4), extra
  token/cache detail. Cost: a big dependency tree and Traceloop-specific
  attributes next to the standard ones; prompts captured unless you turn it off.
- **OpenTelemetry** — upstream CNCF standard, small footprint, only standard
  attributes, privacy-safe default (no content). Cost: you write the SDK setup,
  you add one instrumentor per library, and the GenAI packages are still beta.

## Gotchas hit while building this

- `openai` 3.x no longer installs `httpx`, but `openai-v2` imports it — add it.
- `opentelemetry-util-genai` 1.2b0 renamed a module and breaks
  `openai-v2` 2.4b0 (`No module named 'opentelemetry.util.genai.instruments'`).
  Pinned to `1.1b0` in `requirements.txt`.
- Dynatrace needs **delta** metric temporality; set it on the metric exporter.
