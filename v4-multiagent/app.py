"""Multi-agent chat app over any OpenAI-compatible API, instrumented with OpenLLMetry (traceloop-sdk).
Agents (all free, no API keys): coordinator -> researcher (Wikipedia tools) -> reviewer.
POST /chat {"messages": [...], "conversation_id": "...", "mode": "multi"|"simple"}  (default multi)
Default LLM: local Ollama (free, no API key, CPU-only on the VM). Env vars:
  LLM_BASE_URL   default http://127.0.0.1:11434/v1
  LLM_MODEL      default qwen2.5:1.5b
  LLM_API_KEY    default "none" (Ollama ignores it)
  PORT           default 8080
  TRACE_MODE     v3 (OneAgent local OTLP 127.0.0.1:14499, traces only) | v1 (direct to Dynatrace, needs DT_API_TOKEN/DT_OTLP_ENDPOINT) | v2 (via Bindplane collector, BINDPLANE_OTLP_ENDPOINT)
"""
import os

os.environ["TRACELOOP_TELEMETRY"] = "false"  # no telemetry to Traceloop
# Dynatrace OTLP ingest only accepts DELTA temporality; SDK default is cumulative -> "Unsupported metric"
os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")

import json
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from openai import OpenAI, APIStatusError
from traceloop.sdk import Traceloop
from traceloop.sdk.decorators import agent, task, tool, workflow

BASE = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
PORT = int(os.environ.get("PORT", 8080))

# TRACE_MODE: v3 = app -> OneAgent local OTLP | v1 = app -> Dynatrace SaaS directly | v2 = app -> local Bindplane collector -> Dynatrace SaaS
MODE = os.environ.get("TRACE_MODE", "v1")
if MODE == "v3":  # OneAgent local OTLP endpoint: traces only, http/protobuf, no auth, 127.0.0.1:14499
    os.environ.setdefault("TRACELOOP_METRICS_ENABLED", "false")  # endpoint rejects metrics/logs
    Traceloop.init(
        app_name="openllmdemo",
        api_endpoint=os.environ.get("ONEAGENT_OTLP_ENDPOINT", "http://127.0.0.1:14499/otlp"),
        resource_attributes={"openllm.pipeline": "v3-oneagent"},
    )
elif MODE == "v2":
    Traceloop.init(
        app_name="openllmdemo",
        api_endpoint=os.environ.get("BINDPLANE_OTLP_ENDPOINT", "http://localhost:4318"),  # no auth: collector holds the DT token
        resource_attributes={"openllm.pipeline": "v2-bindplane"},
    )
elif os.environ.get("DT_API_TOKEN"):
    Traceloop.init(
        app_name="openllmdemo",
        api_endpoint=os.environ.get("DT_OTLP_ENDPOINT", "https://YOUR_TENANT.live.dynatrace.com/api/v2/otlp"),
        headers={"Authorization": "Api-Token " + os.environ["DT_API_TOKEN"]},
        resource_attributes={"openllm.pipeline": "v1-direct"},
    )

client = OpenAI(base_url=BASE, api_key=os.environ.get("LLM_API_KEY", "none"), timeout=180, max_retries=0)  # auto-instrumented by Traceloop
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


def llm(messages, retries=4):
    """One chat completion; retry on 429/5xx (transient upstream errors)."""
    for i in range(retries):
        try:
            r = client.chat.completions.create(model=MODEL, messages=messages)
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


# ---- agent 2: researcher ----
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


# ---- agent 3: reviewer ----
@agent(name="reviewer")
def reviewer(question, draft, facts):
    return llm([
        {"role": "system", "content": "You are a strict reviewer. Check the draft answer against the facts. "
                                      "Fix wrong or unsupported claims, keep it concise. Reply with the final answer only."},
        {"role": "user", "content": f"Question: {question}\n\nFacts:\n{facts}\n\nDraft answer:\n{draft}"},
    ])


# ---- agent 1: coordinator ----
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
    return final, {"search_query": research["query"], "agents": ["coordinator", "researcher", "reviewer"]}


@workflow(name="chat")
def ask(messages, conv_id=None, mode="multi"):
    if conv_id:
        Traceloop.set_association_properties({"conversation_id": conv_id})
    if mode == "simple":
        return llm(messages[-20:]), {"agents": []}
    return coordinator(messages)


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
            reply, meta = ask(req["messages"], req.get("conversation_id"), req.get("mode", "multi"))
            out, code = {"reply": reply, **meta}, 200
        except APIStatusError as e:
            out, code = {"error": f"upstream {e.status_code}"}, 502
        except Exception as e:
            out, code = {"error": str(e)}, 500
        self._send(code, "application/json", json.dumps(out).encode())


if __name__ == "__main__":
    print(f"openLLMdemo on :{PORT} -> {BASE} model={MODEL} trace_mode={MODE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
