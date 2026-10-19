"""openLLMdemo — v1a: same as v1 (direct to Dynatrace SaaS), but instrumented with
plain OpenTelemetry instead of OpenLLMetry (traceloop-sdk).

Diff this against v1-dynatrace-direct/app.py: Traceloop.init() becomes an explicit
OTel SDK setup (TracerProvider + MeterProvider + OTLP exporters), the openai client
is patched by the official OTel contrib package `opentelemetry-instrumentation-openai-v2`,
and @workflow becomes a manual `tracer.start_as_current_span("chat")`.

Default LLM: local Ollama. Env vars:
  LLM_BASE_URL      default http://127.0.0.1:11434/v1
  LLM_MODEL         default qwen2.5:1.5b
  LLM_API_KEY       default "none" (Ollama ignores it)
  PORT              default 8080
  DT_OTLP_ENDPOINT  Dynatrace OTLP endpoint, e.g. https://<tenant>.live.dynatrace.com/api/v2/otlp
  DT_API_TOKEN      Dynatrace API token with openTelemetryTrace.ingest / metrics.ingest scopes
"""
import os

# Prompt/response capture is OFF by default in OTel (opt-in, unlike OpenLLMetry).
# The latest GenAI semconv puts it on the span as gen_ai.input/output.messages.
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "span_only")
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openai import APIStatusError, OpenAI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider, UpDownCounter
from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

BASE = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
PORT = int(os.environ.get("PORT", 8080))

if os.environ.get("DT_API_TOKEN"):
    endpoint = os.environ["DT_OTLP_ENDPOINT"].rstrip("/")
    headers = {"Authorization": "Api-Token " + os.environ["DT_API_TOKEN"]}
    resource = Resource.create({"service.name": "openllmdemo-v1a", "openllm.pipeline": "v1a-otel"})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", headers=headers)))
    trace.set_tracer_provider(tp)
    # Dynatrace only accepts DELTA temporality; the SDK default is cumulative.
    delta = {Counter: AggregationTemporality.DELTA, Histogram: AggregationTemporality.DELTA,
             UpDownCounter: AggregationTemporality.CUMULATIVE}
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(
        endpoint=endpoint + "/v1/metrics", headers=headers, preferred_temporality=delta))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    OpenAIInstrumentor().instrument()
else:
    print("WARNING: DT_API_TOKEN not set — running uninstrumented, see .env.example")

tracer = trace.get_tracer("openllmdemo")
client = OpenAI(base_url=BASE, api_key=os.environ.get("LLM_API_KEY", "none"))  # patched by OpenAIInstrumentor
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


def ask(messages, conv_id=None):
    with tracer.start_as_current_span("chat") as span:  # replaces Traceloop's @workflow
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
    print(f"openLLMdemo on :{PORT} -> {BASE} model={MODEL} trace_mode=v1a-otel", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
