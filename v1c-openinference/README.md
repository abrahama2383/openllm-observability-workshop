# V1c — OpenInference instead of OpenLLMetry

```
[chat app] --OTLP/HTTP (traces)--> [Dynatrace SaaS /api/v2/otlp]
```

Same app and path as [v1](../v1-dynatrace-direct), instrumented with
[OpenInference](https://github.com/Arize-ai/openinference) (Arize's
OpenTelemetry-based instrumentation) as described in the Dynatrace docs:
[AI/ML: OpenInference](https://docs.dynatrace.com/docs/shortlink/ai-ml-openinference).

The key switch is `OPENINFERENCE_ENABLE_GENAI_SEMCONV=true`: OpenInference then
writes the standard `gen_ai.*` attributes **next to** its own (`llm.*`,
`input.value`, `output.value`), and Dynatrace AI Observability reads the
`gen_ai.*` ones. `app.py` sets it before the instrumentor runs.

## Run it

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # same DT_OTLP_ENDPOINT / DT_API_TOKEN as v1
set -a; . ./.env; set +a
./venv/bin/python app.py
```

Defaults to local Ollama (`qwen2.5:1.5b`). Token scope: `openTelemetryTrace.ingest`
(classic) or `openpipeline:traces:ingest` (platform token, then use
`Authorization: Bearer ...`). Spans land in service `openllmdemo-v1c`.

## What changes in code (vs v1a, plain OTel)

| | v1a OpenTelemetry | v1c OpenInference |
|---|---|---|
| Instrumentor | `opentelemetry.instrumentation.openai_v2.OpenAIInstrumentor` | `openinference.instrumentation.openai.OpenAIInstrumentor` |
| Turn on `gen_ai.*` / content | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` + `OTEL_SEMCONV_STABILITY_OPT_IN` | `OPENINFERENCE_ENABLE_GENAI_SEMCONV=true` (content on by default) |
| Conversation id | `span.set_attribute("gen_ai.conversation.id", ...)` | `with using_session(id):` — also lands as `gen_ai.conversation.id` on the LLM span |
| Hide content | default | `OPENINFERENCE_HIDE_INPUTS` / `_OUTPUTS` / `_INPUT_MESSAGES` / `_OUTPUT_MESSAGES` |
| Install (measured) | 36 packages, 75 MB | 35 packages, 77 MB |

## What arrives in Dynatrace (same questions, measured)

| | v1 OpenLLMetry | v1a OpenTelemetry | v1c OpenInference |
|---|---|---|---|
| LLM span name / kind | `openai.chat` / CLIENT | `chat qwen2.5:1.5b` / CLIENT | `ChatCompletion` / INTERNAL |
| Core `gen_ai.*` (model, provider, operation, tokens, finish reason, response id) | yes | yes | yes |
| `gen_ai.input/output.messages` | yes | yes (opt-in) | yes |
| `gen_ai.conversation.id` on the LLM span | no (Traceloop key) | only on the wrapper span | **yes** (from `using_session`) |
| Cache-read tokens | yes | no | yes |
| Linked to GenAI Smartscape → in AI Observability | yes | yes | yes |
| Extra, non-standard attributes | `traceloop.*` | none | `llm.*`, `input.value`, `output.value` (raw request/response JSON) |
| Metrics | token usage, duration, choices | token usage, duration | **none** (traces only) |

## Plain-text prompt and answer (on by default here)

The GenAI semantic conventions store messages as JSON, e.g.
`[{"role": "user", "parts": [{"type": "text", "content": "What is the capital of Indonesia?"}]}]`.
This app wraps the exporter (`PlainTextMessages` in `app.py`) and rewrites
`gen_ai.input.messages` / `gen_ai.output.messages` to plain text just before export:

| | Standard (`PLAIN_TEXT_MESSAGES=false`) | This app (default) |
|---|---|---|
| `gen_ai.input.messages` | `[{"role": "user", "parts": [{"type": "text", "content": "What is the capital of Indonesia?"}]}]` | `What is the capital of Indonesia?` |
| `gen_ai.output.messages` | `[{"role": "assistant", "parts": [...], "finish_reason": "stop"}]` | `The capital of Indonesia is Jakarta.` |

Several messages (system prompt, history) become one `role: text` line each.
Checked: AI Observability → Prompts still shows input/output and the agent topology,
and the span stays linked to the GenAI entities.

**Trade-off:** this is not the standard format. Roles, tool calls, non-text parts
and `finish_reason` inside the message are dropped, and other tools that parse
the JSON (dt-evals, DQL `parse ... JSON_ARRAY`) won't read it. Set
`PLAIN_TEXT_MESSAGES=false` to send the standard JSON.

## Trade-offs

- **Good:** same span quality as OpenLLMetry with a small install; conversation
  id flows onto every LLM span; covers many frameworks (LangChain, LlamaIndex,
  OpenAI Agents, Bedrock, ...) with the same switch.
- **Watch:** prompt and answer are stored **three times** per span
  (`gen_ai.*_messages`, `llm.*_messages.*`, raw `input/output.value`) — more
  ingest volume and more places to mask. Use the `OPENINFERENCE_HIDE_*` controls
  for sensitive data.
- **Watch:** no OTel metrics; token/latency charts come from spans only.
- **Watch:** `OPENINFERENCE_ENABLE_GENAI_SEMCONV` is per instrumentor. For
  instrumentors without it, Dynatrace documents the Collector `genainormalizer`
  processor (Dynatrace Collector 0.53.1+ / Bindplane OTel Collector 1.1.2+).
