# V2 — Through a local Bindplane collector

```
[chat app] --OTLP, no auth--> [Bindplane collector :4317/:4318] --OTLP+token--> [Dynatrace SaaS]
```

Same instrumentation as v1. The only code change: `Traceloop.init()` points at
`localhost:4318` instead of Dynatrace, and carries no token — the collector
holds the token and does the real export.

## Why put a collector in front?

- **One token, not one per partner.** The token lives in the collector's
  destination config, never in application code or a partner's `.env`.
- **A place to transform data before it leaves the machine.** e.g. redact
  sensitive attributes, add/drop fields, convert cumulative metrics to delta,
  fan out to multiple destinations, add host metrics — all without touching
  the app.

## Set up the collector

You need a [Bindplane](https://bindplane.com) account (SaaS or self-hosted)
and an agent installed on the host running the app —
[install docs](https://docs.bindplane.com/getting-started).

Apply the reference config in [`bindplane/configuration.yaml`](bindplane/configuration.yaml):

```bash
bindplane apply -f bindplane/configuration.yaml
```

It defines one **OTLP source** (`otlp:3`, listening on 4317/gRPC and
4318/HTTP, accepting Traces/Metrics/Logs) routed to a **Dynatrace OTLP
destination** (`dynatrace_otlp:5`). Create that destination yourself in
Bindplane first — it needs your tenant's OTLP endpoint and API token — then
point the config's `destinations:` section at it, and assign the config to
your agent:

```bash
bindplane label agent <agent-id> configuration=OpenLLM --overwrite
```

## Run the app

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

Open http://localhost:8080, send a message, then check Dynatrace the same
way as in v1 — the spans should now carry `openllm.pipeline: v2-bindplane`.

## Exercises

1. **Redact a word.** Add a `transform` processor (OTTL) on the source that
   replaces some attribute value with `*******` before it reaches Dynatrace.
   Reference: `replace_all_patterns(attributes, "value", "(?i)<word>", "*******")`.
2. **Add host metrics.** Add a `host` source (type `host:3`) to the config,
   routed to the same destination, to get `system.cpu.*`, `system.memory.*`,
   `system.disk.*` from the VM. Watch for the delta-temporality gotcha from
   v1 — sum-type host metrics need a `cumulative_to_delta` processor too.

## Your task

Bindplane is still one hop outside the app process. Move on to
[`../v3-oneagent`](../v3-oneagent), which sends straight into a Dynatrace
OneAgent already installed on the host.
