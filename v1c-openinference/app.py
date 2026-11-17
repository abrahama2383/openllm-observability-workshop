"""openLLMdemo — v1c: same as v1 (direct to Dynatrace SaaS), but instrumented with
OpenInference (Arize) instead of OpenLLMetry, following
https://docs.dynatrace.com/docs/shortlink/ai-ml-openinference

Diff this against v1a-otel/app.py: same OTel SDK setup, but the openai client is patched by
`openinference-instrumentation-openai`, and OPENINFERENCE_ENABLE_GENAI_SEMCONV=true makes it
emit gen_ai.* attributes next to its own OpenInference ones (llm.*, input.value, ...), which
is what Dynatrace AI Observability reads.

Default LLM: local Ollama. Env vars:
  LLM_BASE_URL      default http://127.0.0.1:11434/v1
  LLM_MODEL         default qwen2.5:1.5b
  LLM_API_KEY       default "none" (Ollama ignores it)
  PORT              default 8080
  DT_OTLP_ENDPOINT  https://<tenant>.live.dynatrace.com/api/v2/otlp
  DT_API_TOKEN      classic token with openTelemetryTrace.ingest
"""
import os

# Must be set before the instrumentor is imported/used.
os.environ.setdefault("OPENINFERENCE_ENABLE_GENAI_SEMCONV", "true")

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openai import APIStatusError, OpenAI
from openinference.instrumentation import using_session
from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

PLAIN_TEXT_MESSAGES = os.environ.get("PLAIN_TEXT_MESSAGES", "true").lower() == "true"
MESSAGE_KEYS = ("gen_ai.input.messages", "gen_ai.output.messages")


def to_plain_text(value):
    """'[{"role":"user","parts":[{"type":"text","content":"Hi"}]}]' -> 'Hi'.
    Several messages (system prompt, history) -> one 'role: text' line each."""
    try:
        msgs = json.loads(value)
    except (TypeError, ValueError):
        return value
    lines = [(m.get("role", ""), " ".join(p.get("content", "") for p in m.get("parts", [])
                                          if p.get("type") == "text")) for m in msgs]
    return lines[0][1] if len(lines) == 1 else "\n".join(f"{r}: {t}" for r, t in lines)


class PlainTextMessages(SpanExporter):
    """Rewrites gen_ai.input/output.messages to plain text just before export.
    ponytail: rebuilds each span; drops nothing else. Off with PLAIN_TEXT_MESSAGES=false."""

    def __init__(self, inner):
        self.inner = inner

    def export(self, spans):
        out = []
        for s in spans:
            if not any(k in s.attributes for k in MESSAGE_KEYS):
                out.append(s)
                continue
            attrs = {k: to_plain_text(v) if k in MESSAGE_KEYS else v for k, v in s.attributes.items()}
            out.append(ReadableSpan(name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                                    attributes=attrs, events=s.events, links=s.links, kind=s.kind,
                                    status=s.status, start_time=s.start_time, end_time=s.end_time,
                                    instrumentation_scope=s.instrumentation_scope))
        return self.inner.export(out)

    def shutdown(self):
        self.inner.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.inner.force_flush(timeout_millis)


BASE = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
PORT = int(os.environ.get("PORT", 8080))

tracer_provider = TracerProvider(resource=Resource.create(
    {SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "openllmdemo-v1c"), "openllm.pipeline": "v1c-openinference"}))
if os.environ.get("DT_API_TOKEN"):
    exporter = OTLPSpanExporter(
        endpoint=os.environ["DT_OTLP_ENDPOINT"].rstrip("/") + "/v1/traces",
        headers={"Authorization": "Api-Token " + os.environ["DT_API_TOKEN"]})
    if PLAIN_TEXT_MESSAGES:
        exporter = PlainTextMessages(exporter)
    tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(tracer_provider)
    OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
else:
    print("WARNING: DT_API_TOKEN not set — running uninstrumented, see .env.example")

tracer = trace.get_tracer("openllmdemo")
client = OpenAI(base_url=BASE, api_key=os.environ.get("LLM_API_KEY", "none"))  # patched by OpenInference
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


def ask(messages, conv_id=None):
    # using_session() puts session.id on every span created inside it (OpenInference's conversation id)
    with using_session(conv_id or ""), tracer.start_as_current_span("chat") as span:
        if conv_id:
            span.set_attribute("gen_ai.conversation.id", conv_id)
        r = client.chat.completions.create(model=MODEL, messages=messages[-20:])  # ponytail: naive last-20 trim
        return r.choices[0].message.content


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
            out, code = {"reply": ask(req["messages"], req.get("conversation_id"))}, 200
        except APIStatusError as e:
            out, code = {"error": f"upstream {e.status_code}"}, 502
        except Exception as e:
            out, code = {"error": str(e)}, 500
        self._send(code, "application/json", json.dumps(out).encode())


if __name__ == "__main__":
    print(f"openLLMdemo on :{PORT} -> {BASE} model={MODEL} trace_mode=v1c-openinference", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
