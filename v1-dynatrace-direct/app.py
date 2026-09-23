"""openLLMdemo — v1: instrumented with OpenLLMetry (traceloop-sdk), sent DIRECTLY to Dynatrace SaaS.

Diff this against 00-base/app.py — the only changes are the Traceloop import,
init, and the @workflow decorator. The openai client call is unchanged; the
SDK patches it automatically and emits GenAI semantic-convention spans/metrics.

Default LLM: Pollinations (public, free, no API key). Env vars:
  LLM_BASE_URL      default https://text.pollinations.ai/openai
  LLM_MODEL         default openai
  LLM_API_KEY       default "none" (Pollinations ignores it)
  PORT              default 8080
  DT_OTLP_ENDPOINT  Dynatrace OTLP endpoint, e.g. https://<tenant>.live.dynatrace.com/api/v2/otlp
  DT_API_TOKEN      Dynatrace API token with openTelemetryTrace.ingest / metrics.ingest scopes
"""
import os

os.environ["TRACELOOP_TELEMETRY"] = "false"  # no telemetry to Traceloop itself
# Dynatrace OTLP ingest only accepts DELTA temporality; the SDK default is cumulative,
# which Dynatrace rejects with "Unsupported metric".
os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openai import APIStatusError, OpenAI
from traceloop.sdk import Traceloop
from traceloop.sdk.decorators import workflow

BASE = os.environ.get("LLM_BASE_URL", "https://text.pollinations.ai/openai")
MODEL = os.environ.get("LLM_MODEL", "openai")
PORT = int(os.environ.get("PORT", 8080))

if os.environ.get("DT_API_TOKEN"):
    Traceloop.init(
        app_name="openllmdemo",
        api_endpoint=os.environ["DT_OTLP_ENDPOINT"],
        headers={"Authorization": "Api-Token " + os.environ["DT_API_TOKEN"]},
        resource_attributes={"openllm.pipeline": "v1-direct"},
    )
else:
    print("WARNING: DT_API_TOKEN not set — running uninstrumented, see .env.example")

client = OpenAI(base_url=BASE, api_key=os.environ.get("LLM_API_KEY", "none"))  # auto-instrumented by Traceloop
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


@workflow(name="chat")
def ask(messages, conv_id=None):
    if conv_id:
        Traceloop.set_association_properties({"conversation_id": conv_id})
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
    print(f"openLLMdemo on :{PORT} -> {BASE} model={MODEL} trace_mode=v1-direct")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
