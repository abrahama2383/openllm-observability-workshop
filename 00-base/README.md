# 00 — Base app (no instrumentation)

A minimal chat web app, in one Python file (stdlib `http.server`), talking to a
free, OpenAI-compatible LLM ([Pollinations](https://pollinations.ai) — public,
no API key required). This is the "before" state for the whole workshop.

## Run it

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

Open http://localhost:8080 and ask something.

## The problem

This app works. It also has **zero observability**. If the LLM is slow,
returns an error, or a partner asks "what did the bot answer at 14:32
yesterday?", you have nothing — no traces, no logs, no metrics.

## Your task

Move on to [`../v1-dynatrace-direct`](../v1-dynatrace-direct) and diff its
`app.py` against this one. You'll see the instrumentation is a handful of
added lines, not a rewrite — that's the point of using an auto-instrumenting
SDK instead of hand-rolling spans.

```bash
diff 00-base/app.py v1-dynatrace-direct/app.py
```
