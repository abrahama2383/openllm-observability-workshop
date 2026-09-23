"""openLLMdemo — 00-base: a chat app over a free, OpenAI-compatible LLM. Zero observability.

This is the starting point for the workshop. It works today, but if the LLM
is slow, errors out, or a user reports a bad answer, you have no visibility
into what happened. The rest of the exercise adds that visibility back,
one step at a time, without touching your application logic.

Default LLM: Pollinations (public, free, no API key). Env vars:
  LLM_BASE_URL   default https://text.pollinations.ai/openai
  LLM_MODEL      default openai
  LLM_API_KEY    default "none" (Pollinations ignores it)
  PORT           default 8080
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openai import APIStatusError, OpenAI

BASE = os.environ.get("LLM_BASE_URL", "https://text.pollinations.ai/openai")
MODEL = os.environ.get("LLM_MODEL", "openai")
PORT = int(os.environ.get("PORT", 8080))

client = OpenAI(base_url=BASE, api_key=os.environ.get("LLM_API_KEY", "none"))
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


def ask(messages):
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
            out, code = {"reply": ask(req["messages"])}, 200
        except APIStatusError as e:
            out, code = {"error": f"upstream {e.status_code}"}, 502
        except Exception as e:
            out, code = {"error": str(e)}, 500
        self._send(code, "application/json", json.dumps(out).encode())


if __name__ == "__main__":
    print(f"openLLMdemo on :{PORT} -> {BASE} model={MODEL} (no instrumentation)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
