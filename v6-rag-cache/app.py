"""Router app v6: v5 router + Redis semantic cache + Chroma vector DB (document RAG).
Standalone continuation of v5 (openllmrouter). Does NOT modify v5; runs side by side on :8082.
Routes (all free, local Ollama, no API keys):
  simple    -> chat agent on LLM_MODEL (fast small talk, short facts)
  reasoning -> reasoner agent on LLM_MODEL_BIG (math, code, step-by-step analysis)
  research  -> v4 chain: coordinator -> researcher (Wikipedia tools) -> reviewer
  docs      -> retriever (Chroma vector DB over v6-docs) -> grounded answer   [NEW in v6]
  blocked   -> guard agent, fixed refusal, no LLM call
Before routing, every question is embedded and checked against a Redis semantic cache; a hit
skips the LLM entirely.                                                        [NEW in v6]
Conversation history is persisted per conversation_id in Redis (list conv:<id>) and is the
source of truth for context, so the client may send only the new turn.         [NEW in v6]
POST /chat {"messages":[...], "conversation_id":"...", "route":"auto"|simple|reasoning|research|docs}
Services in Dynatrace: openllmrouterv6 (v4 openllmdemo :8080 and v5 openllmrouter :8081 stay as-is).
Redis + Chroma + embedding calls are auto-instrumented by OpenLLMetry (traceloop-sdk).

Env vars: same as v5, plus
  LLM_MODEL_BIG    default qwen2.5:3b
  EMBED_MODEL      default nomic-embed-text   (ollama pull nomic-embed-text)
  PORT             default 8082 (v4 :8080, v5 :8081)
  REDIS_URL        default redis://127.0.0.1:6379/0
  CHROMA_PATH      default ./.chroma-v6
  DOCS_DIR         default ./v6-docs
  CACHE_THRESHOLD  default 0.92   (cosine >= this = cache hit)
  RAG_THRESHOLD    default 0.35   (min cosine for a doc chunk to count as relevant)
  CONV_MAX         default 40     (messages kept per conversation)
  CONV_TTL         default 86400  (seconds a conversation lives after last turn)

Extra deps vs v5:  pip install redis chromadb
Self-check (no network/SDK):  python app.py --selftest
"""
import os
import re
import sys
import json
import time
import math
import glob
import hashlib
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = "openllmrouterv6"  # separate service from v4 openllmdemo and v5 openllmrouter
BASE = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
MODEL_BIG = os.environ.get("LLM_MODEL_BIG", "qwen2.5:3b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
PORT = int(os.environ.get("PORT", 8082))
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
CHROMA_PATH = os.environ.get("CHROMA_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chroma-v6"))
DOCS_DIR = os.environ.get("DOCS_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "v6-docs"))
CACHE_THRESHOLD = float(os.environ.get("CACHE_THRESHOLD", "0.92"))
RAG_THRESHOLD = float(os.environ.get("RAG_THRESHOLD", "0.35"))
CACHE_SET = "llmcache:keys"
# Keyword pre-filter for the docs route: if a question mentions a KB term, send it straight to
# retrieval instead of asking the LLM router (deterministic + skips the router call).
# ponytail: static list; tune DOCS_KEYWORDS per knowledge base.
DOCS_KEYWORDS = [k.strip().lower() for k in os.environ.get(
    "DOCS_KEYWORDS", "acmepay,merchant onboarding,qris,virtual account,settlement").split(",") if k.strip()]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _store(r, question, qvec, answer):
    key = "llmcache:" + hashlib.sha1(question.encode()).hexdigest()[:16]
    r.set(key, json.dumps({"q": question, "a": answer, "vec": qvec}))
    r.sadd(CACHE_SET, key)


def _lookup(r, qvec):
    """Best semantic match in the cache, or None. ponytail: O(n) scan over all cached vectors;
    swap for RediSearch / a Redis vector index if the cache grows past a few hundred entries."""
    keys = list(r.smembers(CACHE_SET))
    if not keys:
        return None
    best_a, best_s = None, -1.0
    for raw in r.mget(keys):
        if not raw:
            continue
        e = json.loads(raw)
        s = cosine(qvec, e["vec"])
        if s > best_s:
            best_a, best_s = e["a"], s
    return (best_a, best_s) if best_s >= CACHE_THRESHOLD else None


# ---- Redis conversation memory ----
CONV_MAX = int(os.environ.get("CONV_MAX", "40"))      # keep last N messages per conversation
CONV_TTL = int(os.environ.get("CONV_TTL", "86400"))   # expire a conversation after 24h idle


def _mem_load(r, conv_id):
    return [json.loads(x) for x in r.lrange("conv:" + conv_id, 0, -1)]


def _mem_append(r, conv_id, msgs):
    if not msgs:
        return
    key = "conv:" + conv_id
    r.rpush(key, *[json.dumps(m) for m in msgs])
    r.ltrim(key, -CONV_MAX, -1)
    r.expire(key, CONV_TTL)


# --- selftest: pure cache/cosine/memory logic, no network / SDK / server ---
if __name__ == "__main__" and "--selftest" in sys.argv:
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert abs(cosine([1, 0], [0, 1])) < 1e-9
    assert cosine([1, 0], []) == 0.0

    class _FakeRedis:  # mimic the ops the cache + memory use
        def __init__(self): self.store, self.members, self.lists = {}, set(), {}
        def sadd(self, k, v): self.members.add(v)
        def smembers(self, k): return set(self.members)
        def set(self, k, v): self.store[k] = v
        def mget(self, keys): return [self.store.get(k) for k in keys]
        def rpush(self, k, *v): self.lists.setdefault(k, []).extend(v)
        def lrange(self, k, a, b): return self.lists.get(k, [])[a:None if b == -1 else b + 1]
        def ltrim(self, k, a, b): self.lists[k] = self.lists.get(k, [])[a:]
        def expire(self, k, ttl): pass

    r = _FakeRedis()
    _store(r, "What is AcmePay?", [1.0, 0.0, 0.0], "AcmePay is a payment gateway.")
    hit = _lookup(r, [0.999, 0.044, 0.0])   # ~same direction -> hit
    miss = _lookup(r, [0.0, 1.0, 0.0])       # orthogonal -> miss
    assert hit and hit[0] == "AcmePay is a payment gateway." and hit[1] >= CACHE_THRESHOLD, hit
    assert miss is None, miss

    assert _mem_load(r, "c1") == []
    _mem_append(r, "c1", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    _mem_append(r, "c1", [{"role": "user", "content": "bye"}])
    hist = _mem_load(r, "c1")
    assert [m["content"] for m in hist] == ["hi", "hello", "bye"], hist
    print("selftest ok")
    sys.exit(0)

from openai import OpenAI, APIStatusError
from opentelemetry import trace
from traceloop.sdk import Traceloop
from traceloop.sdk.decorators import agent, task, tool, workflow

# TRACE_MODE: v3 = app -> OneAgent local OTLP | v1 = app -> Dynatrace SaaS directly | v2 = app -> local Bindplane collector -> Dynatrace SaaS
MODE = os.environ.get("TRACE_MODE", "v1")
if MODE == "v3":  # OneAgent local OTLP endpoint: traces only, http/protobuf, no auth, 127.0.0.1:14499
    os.environ.setdefault("TRACELOOP_METRICS_ENABLED", "false")  # endpoint rejects metrics/logs
    Traceloop.init(
        app_name=APP,
        api_endpoint=os.environ.get("ONEAGENT_OTLP_ENDPOINT", "http://127.0.0.1:14499/otlp"),
        resource_attributes={"openllm.pipeline": "v6-oneagent"},
    )
elif MODE == "v2":
    Traceloop.init(
        app_name=APP,
        api_endpoint=os.environ.get("BINDPLANE_OTLP_ENDPOINT", "http://localhost:4318"),  # no auth: collector holds the DT token
        resource_attributes={"openllm.pipeline": "v6-bindplane"},
    )
elif os.environ.get("DT_API_TOKEN"):
    Traceloop.init(
        app_name=APP,
        api_endpoint=os.environ.get("DT_OTLP_ENDPOINT", "https://YOUR_TENANT.live.dynatrace.com/api/v2/otlp"),
        headers={"Authorization": "Api-Token " + os.environ["DT_API_TOKEN"]},
        resource_attributes={"openllm.pipeline": "v6-direct"},
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


@task(name="embed")
def embed(text):
    r = client.embeddings.create(model=EMBED_MODEL, input=text[:4000])  # auto-instrumented
    return list(r.data[0].embedding)


# ---- Redis semantic cache ----
import redis  # noqa: E402  (auto-instrumented by OpenLLMetry)

rds = redis.from_url(REDIS_URL, decode_responses=True)


@agent(name="cache")
def cache_lookup(qvec):
    hit = _lookup(rds, qvec)
    span = trace.get_current_span()
    span.set_attribute("cache.hit", bool(hit))
    span.set_attribute("cache.score", round(hit[1], 4) if hit else 0.0)
    return hit


@agent(name="memory")
def memory_load(conv_id):
    hist = _mem_load(rds, conv_id)
    trace.get_current_span().set_attribute("conv.turns", len(hist))
    return hist


# ---- Chroma vector DB + document RAG ----
import chromadb  # noqa: E402  (auto-instrumented by OpenLLMetry)

chroma = chromadb.PersistentClient(path=CHROMA_PATH)
COLL = chroma.get_or_create_collection(name="v6docs")


def _chunk(text, size=800, overlap=120):
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def ingest_docs():
    """Load DOCS_DIR/*.{md,txt} into Chroma once (idempotent on chunk id)."""
    files = sorted(glob.glob(os.path.join(DOCS_DIR, "*.md")) + glob.glob(os.path.join(DOCS_DIR, "*.txt")))
    added = 0
    for path in files:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            chunks = _chunk(f.read())
        for n, ch in enumerate(chunks):
            cid = f"{name}#{n}"
            if COLL.get(ids=[cid])["ids"]:
                continue
            COLL.add(ids=[cid], embeddings=[embed(ch)], documents=[ch], metadatas=[{"source": name}])
            added += 1
    print(f"[rag] {len(files)} files, {COLL.count()} chunks in Chroma (+{added} new)", flush=True)


@agent(name="retriever")
def retriever(question, qvec, k=3):
    res = COLL.query(query_embeddings=[qvec], n_results=k, include=["documents", "metadatas", "distances"])
    docs = res["documents"][0] if res["documents"] else []
    metas = res["metadatas"][0] if res["metadatas"] else []
    dists = res["distances"][0] if res["distances"] else []
    hits = [(d, m.get("source", "?"), 1.0 - dist) for d, m, dist in zip(docs, metas, dists) if (1.0 - dist) >= RAG_THRESHOLD]
    sources = ", ".join(sorted({s for _, s, _ in hits})) or "(none)"
    span = trace.get_current_span()
    span.set_attribute("rag.results", len(hits))
    span.set_attribute("rag.top_score", round(hits[0][2], 4) if hits else 0.0)
    span.set_attribute("rag.sources", sources)
    return "\n\n".join(f"[{s}] {d}" for d, s, _ in hits), sources


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
ROUTES = ("simple", "reasoning", "research", "docs", "blocked")
ROUTER_PROMPT = """You are a router. Classify the user's LAST message into exactly one route.
Decide docs FIRST: if the question is about our internal product/knowledge base (AcmePay: its API,
keys, pricing/fees, onboarding, settlement, SLAs) pick docs, even if it is phrased as "how do I" or "why".
simple    - greetings, small talk, opinions, short questions answerable from common knowledge
reasoning - ONLY math, arithmetic, code, or logic puzzles with no external facts needed
research  - facts about real-world people, places, companies, events, history or science to look up
docs      - anything about our internal product/knowledge base (AcmePay and its API/pricing/onboarding/SLAs)
blocked   - requests for weapons, malware, self-harm, illegal activity, or other harmful content
Reply with JSON only: {"route": "<one of simple|reasoning|research|docs|blocked>", "reason": "<max 12 words>"}"""


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


# ---- route: docs (RAG over Chroma) ----
@agent(name="docs")
def docs_answer(question, qvec):
    context, sources = retriever(question, qvec)
    if not context:
        return "I don't have anything in the knowledge base about that.", sources
    answer = llm([{"role": "system", "content": "Answer ONLY from the context below. If it is not in the context, say you don't know. "
                   "Be concise.\n\nContext:\n" + context},
                  {"role": "user", "content": question}])
    return f"{answer}\n\n(sources: {sources})", sources


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
    span = trace.get_current_span()
    new_user = messages[-1]
    question = new_user["content"]

    # conversation memory: Redis is the source of truth for context (client may send only the new turn)
    if conv_id:
        Traceloop.set_association_properties({"conversation_id": conv_id})
        working = memory_load(conv_id) + [new_user]
    else:
        working = messages

    # semantic cache: embed once, reuse the vector for lookup, docs retrieval and store
    qvec = embed(question)
    hit = cache_lookup(qvec)
    span.set_attribute("cache.hit", bool(hit))
    if hit:
        reply = hit[0]
        out_meta = {"route": "cache", "reason": f"semantic cache hit ({round(hit[1], 3)})",
                    "model": None, "agents": ["memory", "cache"], "cache_hit": True}
        _mem_append(rds, conv_id, [new_user, {"role": "assistant", "content": reply}]) if conv_id else None
        return reply, out_meta

    ql = question.lower()
    kw = next((k for k in DOCS_KEYWORDS if k in ql), None)
    if forced in ROUTES and forced != "blocked":
        route, reason, method = forced, "forced by user", "forced"
    elif kw:  # KB term present -> skip the LLM router, go straight to docs
        route, reason, method = "docs", f"keyword match: {kw}", "keyword"
    else:
        route, reason = route_question(question)
        method = "llm"
    span.set_attribute("router.route", route)
    span.set_attribute("router.method", method)
    span.set_attribute("router.forced", forced in ROUTES)
    meta = {"route": route, "reason": reason, "cache_hit": False}

    if route == "blocked":
        reply, out_meta = guard(question), {**meta, "model": None, "agents": ["router", "guard"]}
    elif route == "reasoning":
        reply = reasoner(working)
        out_meta = {**meta, "model": MODEL_BIG, "agents": ["router", "reasoner"]}
    elif route == "research":
        reply, extra = coordinator(working)
        out_meta = {**meta, **extra, "model": MODEL, "agents": ["router", "coordinator", "researcher", "reviewer"]}
    elif route == "docs":
        reply, sources = docs_answer(question, qvec)
        out_meta = {**meta, "model": MODEL, "sources": sources, "agents": ["router", "docs", "retriever"]}
    else:
        reply = chat(working)
        out_meta = {**meta, "model": MODEL, "agents": ["router", "chat"]}

    if route != "blocked":
        _store(rds, question, qvec, reply)  # cache the fresh answer
    if conv_id:
        _mem_append(rds, conv_id, [new_user, {"role": "assistant", "content": reply}])
    return reply, out_meta


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
    print(f"{APP} on :{PORT} -> {BASE} models={MODEL},{MODEL_BIG} embed={EMBED_MODEL} "
          f"redis={REDIS_URL} chroma={CHROMA_PATH} trace_mode={MODE}", flush=True)
    ingest_docs()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
