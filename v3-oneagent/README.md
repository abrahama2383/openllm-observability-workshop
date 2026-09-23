# V3 — Through the local Dynatrace OneAgent

```
[chat app] --OTLP, no auth, traces only--> [OneAgent 127.0.0.1:14499] --> [Dynatrace SaaS]
```

Needs a **Dynatrace OneAgent already installed on the host running the app**.
No collector process, no token in the app — OneAgent's local endpoint is
unauthenticated because it's bound to `127.0.0.1` only.

## Enable OneAgent's local OTLP endpoint

It's **off by default**. In Dynatrace:

**Settings → Preferences → Extension Execution Controller**, turn on:
- "Enable Extension Execution Controller"
- "Enable local HTTP Metric, Log and Event Ingest API"

You can scope this environment-wide, to a host group, or to one host
(**Hosts → your host → More (…) → Settings**).

Once enabled, verify the port is listening and accepting requests:

```bash
ss -ltn | grep 14499
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:14499/otlp/v1/traces \
  -H 'Content-Type: application/x-protobuf' --data-binary ''
# expect: 200
```

## Run the app

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

Open http://localhost:8080, send a message, then check Dynatrace — spans
carry `openllm.pipeline: v3-oneagent`, and (unlike v1/v2) they're
automatically linked to the host entity, since OneAgent knows what host
it's running on.

## Limits — read this before you file a bug

- **Traces only.** The endpoint's URL is literally `.../otlp/v1/traces` —
  there is no metrics or logs path. `app.py` disables Traceloop's metrics
  (`TRACELOOP_METRICS_ENABLED=false`) so it doesn't even try. If you need
  metrics and logs alongside traces on one local hop, use v2 (Bindplane)
  instead.
- **`http/protobuf` only** — no gRPC, no JSON.
- **No compression** — don't set `Content-Encoding: gzip`.
- **Not available in containers.** For Kubernetes/container workloads, use
  an ActiveGate as the local hop instead of this endpoint.

## Compare all three

| | v1 direct | v2 Bindplane | v3 OneAgent |
|---|---|---|---|
| Token lives in | app | collector | nowhere (local, unauthenticated) |
| Signals | traces + metrics | traces + metrics + logs | traces only |
| Extra process to run | none | collector | none (OneAgent already there) |
| Transform data in-flight | no | yes (OTTL processors) | no |
| Auto host-linkage | no | no | yes |
