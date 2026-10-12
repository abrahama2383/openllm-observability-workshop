# v5 — Question router

Builds on [`v4-multiagent`](../v4-multiagent). A small LLM reads each question
and routes it to the model or agent chain that fits, so cheap questions stay
cheap and only the hard ones pay for the big model or the research chain.

The router's decision is written onto its span as `router.route` and
`router.reason`, so in Dynatrace you can filter, group, chart and alert on how
questions are being routed — no extra code.

## What changed vs v4

- A new **router** agent classifies each question into one route:

  | Route | Handler | Model |
  |---|---|---|
  | `simple` | chat agent | `LLM_MODEL` (qwen2.5:1.5b) |
  | `reasoning` | reasoner agent | `LLM_MODEL_BIG` (qwen2.5:3b) |
  | `research` | v4 chain: coordinator → researcher (Wikipedia) → reviewer | `LLM_MODEL` |
  | `blocked` | guard agent, fixed refusal | none (no LLM call) |

- Each question is one `chat.workflow` trace; the chosen route shows up as the
  `router.route` span attribute.
- Runs on `:8081` as service **openllmrouter**, so it can run side by side with v4 (`:8080`).
- The UI adds a route selector (`auto` or force a specific route).

## Run

```sh
cp .env.example .env     # fill in your tenant + token
pip install -r requirements.txt
# needs a local Ollama with: ollama pull qwen2.5:1.5b qwen2.5:3b
./run.sh v1              # or v2 / v3
```

Open http://localhost:8081 and ask something. Try "Hi" (simple),
"What is 15% of 200?" (reasoning), "Who was Marie Curie?" (research).

## Request contract

```
POST /chat
{"messages": [...], "conversation_id": "...", "route": "auto"|"simple"|"reasoning"|"research"}
```
Default `route` is `auto` (let the router decide).
