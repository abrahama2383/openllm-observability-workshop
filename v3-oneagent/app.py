"""openLLMdemo — v3: same OpenLLMetry instrumentation, sent to the Dynatrace OneAgent
already installed on this host, over its local-only OTLP endpoint.

Diff this against v1-dynatrace-direct/app.py: Traceloop.init() points at
127.0.0.1:14499 instead of Dynatrace, with no token — the local endpoint
requires none. There's no separate collector process to run.

  [chat app] --OTLP, no auth, traces only--> [OneAgent 127.0.0.1:14499] --> [Dynatrace]

IMPORTANT: this endpoint is TRACES ONLY and is off by default — see README.

Default LLM: Pollinations (public, free, no API key). Env vars:
  LLM_BASE_URL            default https://text.pollinations.ai/openai
  LLM_MODEL               default openai
  LLM_API_KEY             default "none" (Pollinations ignores it)
  PORT                    default 8080
  ONEAGENT_OTLP_ENDPOINT  default http://127.0.0.1:14499/otlp
"""
import os

os.environ["TRACELOOP_TELEMETRY"] = "false"
os.environ.setdefault("TRACELOOP_METRICS_ENABLED", "false")  # the local OneAgent endpoint rejects metrics

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openai import APIStatusError, OpenAI
from traceloop.sdk import Traceloop
from traceloop.sdk.decorators import workflow

BASE = os.environ.get("LLM_BASE_URL", "https://text.pollinations.ai/openai")
MODEL = os.environ.get("LLM_MODEL", "openai")
PORT = int(os.environ.get("PORT", 8080))

Traceloop.init(
    app_name="openllmdemo",
    api_endpoint=os.environ.get("ONEAGENT_OTLP_ENDPOINT", "http://127.0.0.1:14499/otlp"),
    resource_attributes={"openllm.pipeline": "v3-oneagent"},
)

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
    print(f"openLLMdemo on :{PORT} -> {BASE} model={MODEL} trace_mode=v3-oneagent")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
