# V1 — Instrumented, sent directly to Dynatrace SaaS

```
[chat app] --OTLP (traces + metrics)--> [Dynatrace SaaS /api/v2/otlp]
```

The app is instrumented with **OpenLLMetry** ([`traceloop-sdk`](https://github.com/traceloop/openllmetry)),
which auto-instruments the `openai` Python client — no manual span code. It
exports OTLP straight to your Dynatrace tenant.

## Create a Dynatrace API token

Dynatrace → **Access Tokens** → create a token with scopes:
- `openTelemetryTrace.ingest`
- `metrics.ingest`

## Run it

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in DT_OTLP_ENDPOINT and DT_API_TOKEN
set -a; . ./.env; set +a
./venv/bin/python app.py
```

Open http://localhost:8080, send a message, then check Dynatrace:

```dql
fetch spans, from: now()-15m
| filter service.name == "openllmdemo"
```

You should see a `chat.workflow` span with a child `openai.chat` span
carrying `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`, and `gen_ai.response.finish_reasons`.

## Gotcha: cumulative vs. delta metrics

The OpenTelemetry SDK defaults to **cumulative** temporality for metrics.
Dynatrace's OTLP ingest only accepts **delta** and rejects the rest with
`Unsupported metric: ... UNSUPPORTED_METRIC_TYPE_MONOTONIC_CUMULATIVE_SUM`.
`app.py` sets `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`
before importing the SDK to fix this — try commenting that line out and
watch metrics get rejected.

## Your task

The app talks to Dynatrace directly — every partner's laptop needs its own
token, and there's no shared place to redact or reroute data before it
leaves the app. Move on to [`../v2-bindplane`](../v2-bindplane), which adds
a local collector in front of Dynatrace.
