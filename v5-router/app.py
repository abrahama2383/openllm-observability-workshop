"""Router app: a small LLM reads each question and routes it to the model or agent chain that fits.
Routes (all free, local Ollama, no API keys):
  simple    -> chat agent on LLM_MODEL (fast small talk, short facts)
  reasoning -> reasoner agent on LLM_MODEL_BIG (math, code, step-by-step analysis)
  research  -> v4 chain: coordinator -> researcher (Wikipedia tools) -> reviewer
  blocked   -> guard agent, fixed refusal, no LLM call
POST /chat {"messages": [...], "conversation_id": "...", "route": "auto"|"simple"|"reasoning"|"research"}  (default auto)
Env vars: same as v4, plus
  LLM_MODEL_BIG  default qwen2.5:3b
  PORT           default 8081 (v4 keeps 8080)
"""
import os

os.environ["TRACELOOP_TELEMETRY"] = "false"  # no telemetry to Traceloop
# Dynatrace OTLP ingest only accepts DELTA temporality; SDK default is cumulative -> "Unsupported metric"
os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")

import json
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from openai import OpenAI, APIStatusError
from opentelemetry import trace
from traceloop.sdk import Traceloop
from traceloop.sdk.decorators import agent, task, tool, workflow

BASE = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
MODEL_BIG = os.environ.get("LLM_MODEL_BIG", "qwen2.5:3b")
PORT = int(os.environ.get("PORT", 8081))
APP = "openllmrouter"  # separate service from v4's openllmdemo, so both show side by side

# TRACE_MODE: v3 = app -> OneAgent local OTLP | v1 = app -> Dynatrace SaaS directly | v2 = app -> local Bindplane collector -> Dynatrace SaaS
MODE = os.environ.get("TRACE_MODE", "v1")
if MODE == "v3":  # OneAgent local OTLP endpoint: traces only, http/protobuf, no auth, 127.0.0.1:14499
    os.environ.setdefault("TRACELOOP_METRICS_ENABLED", "false")  # endpoint rejects metrics/logs
    Traceloop.init(
        app_name=APP,
        api_endpoint=os.environ.get("ONEAGENT_OTLP_ENDPOINT", "http://127.0.0.1:14499/otlp"),
        resource_attributes={"openllm.pipeline": "v3-oneagent"},
    )
elif MODE == "v2":
    Traceloop.init(
        app_name=APP,
        api_endpoint=os.environ.get("BINDPLANE_OTLP_ENDPOINT", "http://localhost:4318"),  # no auth: collector holds the DT token
        resource_attributes={"openllm.pipeline": "v2-bindplane"},
    )
elif os.environ.get("DT_API_TOKEN"):
    Traceloop.init(
        app_name=APP,
        api_endpoint=os.environ.get("DT_OTLP_ENDPOINT", "https://YOUR_TENANT.live.dynatrace.com/api/v2/otlp"),
        headers={"Authorization": "Api-Token " + os.environ["DT_API_TOKEN"]},
        resource_attributes={"openllm.pipeline": "v1-direct"},
    )

client = OpenAI(base_url=BASE, api_key=os.environ.get("LLM_API_KEY", "none"), timeout=300, max_retries=0)  # auto-instrumented by Traceloop
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


def llm(messages, model=MODEL, retries=4, **kw):
    """One chat completion; retry on 429/5xx (transient upstream errors)."""
    for i in range(retries):
        try:
            r = client.chat.completions.create(model=model, messages=messages, **kw)
            return (r.choices[0].message.content or "").strip()
        except APIStatusError as e:
            if e.status_code not in (429, 500, 502, 503) or i == retries - 1:
                raise
            time.sleep(8 * (i + 1))


# ---- tools (free public Wikipedia REST API, no key) ----
WIKI_UA = {"User-Agent": "openLLMdemo/1.0 (observability demo)"}


def _get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=WIKI_UA), timeout=15) as r:
        return json.loads(r.read())


@tool(name="wikipedia_search")
def wikipedia_search(query, limit=3):
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": query,
                                "srlimit": limit, "format": "json"})
    hits = _get_json("https://en.wikipedia.org/w/api.php?" + q)["query"]["search"]
    return [h["title"] for h in hits]


@tool(name="wikipedia_summary")
def wikipedia_summary(title):
    d = _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")))
    return d.get("extract", "")[:1200]


# ---- router ----
ROUTES = ("simple", "reasoning", "research", "blocked")
ROUTER_PROMPT = """You are a router. Classify the user's LAST message into exactly one route:
simple    - greetings, small talk, opinions, short questions answerable from common knowledge
reasoning - math, calculations, code, logic puzzles, step-by-step analysis or planning
research  - facts about specific people, places, companies, events, history or science that need looking up
blocked   - requests for weapons, malware, self-harm, illegal activity, or other harmful content
Reply with JSON only: {"route": "<one of simple|reasoning|research|blocked>", "reason": "<max 12 words>"}"""


@agent(name="router")
def route_question(question):
    raw = llm([{"role": "system", "content": ROUTER_PROMPT}, {"role": "user", "content": question}], temperature=0)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    route = str(d.get("route", "")).lower().strip()
    reason = str(d.get("reason", ""))[:200]
    if route not in ROUTES:  # small model went off-script: take the first route word it said, else simple
        route = next((r for r in ROUTES if r in raw.lower()), "simple")
        reason = reason or "fallback: router output not valid JSON"
    span = trace.get_current_span()
    span.set_attribute("router.route", route)
    span.set_attribute("router.reason", reason)
    return route, reason


# ---- route: simple ----
@agent(name="chat")
def chat(messages):
    return llm([{"role": "system", "content": "You are a friendly assistant. Answer briefly."}] + messages[-20:])


# ---- route: reasoning ----
@agent(name="reasoner")
def reasoner(messages):
    return llm([{"role": "system", "content": "Think step by step. Show short working, then give the final answer on its own line starting with 'Answer:'."}]
               + messages[-20:], model=MODEL_BIG)


# ---- route: blocked ----
@agent(name="guard")
def guard(question):
    return "I can't help with that request. Ask me something else — a fact, a calculation, or a quick chat."


# ---- route: research (v4 chain, unchanged) ----
@agent(name="researcher")
def researcher(question):
    query = llm([
        {"role": "system", "content": "Turn the user question into a short Wikipedia search query (max 6 words). Reply with the query only."},
        {"role": "user", "content": question},
    ]).strip('"')
    notes = []
    try:
        for title in wikipedia_search(query)[:2]:
            notes.append(f"[{title}] {wikipedia_summary(title)}")
    except Exception as e:  # tool failure must not kill the chat
        notes.append(f"(wikipedia unavailable: {e})")
    return {"query": query, "facts": "\n\n".join(notes) or "(no results)"}


@agent(name="reviewer")
def reviewer(question, draft, facts):
    return llm([
        {"role": "system", "content": "You are a strict reviewer. Check the draft answer against the facts. "
                                      "Fix wrong or unsupported claims, keep it concise. Reply with the final answer only."},
        {"role": "user", "content": f"Question: {question}\n\nFacts:\n{facts}\n\nDraft answer:\n{draft}"},
    ])


@task(name="draft_answer")
def draft_answer(messages, facts):
    sys_msg = {"role": "system", "content": "Answer the user using the research notes when relevant.\n\nResearch notes:\n" + facts}
    return llm([sys_msg] + messages[-20:])  # ponytail: naive last-20 trim


@agent(name="coordinator")
def coordinator(messages):
    question = messages[-1]["content"]
    research = researcher(question)
    draft = draft_answer(messages, research["facts"])
    final = reviewer(question, draft, research["facts"])
    return final, {"search_query": research["query"]}


# ---- dispatch ----
@workflow(name="chat")
def ask(messages, conv_id=None, forced="auto"):
    if conv_id:
        Traceloop.set_association_properties({"conversation_id": conv_id})
    question = messages[-1]["content"]
    if forced in ROUTES and forced != "blocked":
        route, reason = forced, "forced by user"
    else:
        route, reason = route_question(question)
    span = trace.get_current_span()
    span.set_attribute("router.route", route)
    span.set_attribute("router.forced", forced in ROUTES)
    meta = {"route": route, "reason": reason}
    if route == "blocked":
        return guard(question), {**meta, "model": None, "agents": ["router", "guard"]}
    if route == "reasoning":
        return reasoner(messages), {**meta, "model": MODEL_BIG, "agents": ["router", "reasoner"]}
    if route == "research":
        final, extra = coordinator(messages)
        return final, {**meta, **extra, "model": MODEL, "agents": ["router", "coordinator", "researcher", "reviewer"]}
    return chat(messages), {**meta, "model": MODEL, "agents": ["router", "chat"]}


class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", HTML)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path != "/chat":
            return self._send(404, "text/plain", b"not found")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            t0 = time.time()
            reply, meta = ask(req["messages"], req.get("conversation_id"), req.get("route", "auto"))
            out, code = {"reply": reply, **meta, "seconds": round(time.time() - t0, 1)}, 200
        except APIStatusError as e:
            out, code = {"error": f"upstream {e.status_code}"}, 502
        except Exception as e:
            out, code = {"error": str(e)}, 500
        self._send(code, "application/json", json.dumps(out).encode())


if __name__ == "__main__":
    print(f"openLLMrouter on :{PORT} -> {BASE} models={MODEL},{MODEL_BIG} trace_mode={MODE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
