"""A local OpenAI-compatible server for offline tests (stdlib http.server on 127.0.0.1, ephemeral port).

`replies` is a queue of dicts consumed by POST /v1/chat/completions, each one of:
  {"content": "text"}                       a message (streamed in pieces when the request has stream=true)
  {"tool_calls": [{"name": ..., "arguments": "json text"}]}
  {"status": 503, "headers": {...}, "body": {...}}   an HTTP error
  {"delay": seconds, ...}                   sleep before answering (timeouts)
  {"body": <any JSON>}  (no status)          that JSON as the 200 answer (malformed shapes)
  {"sse": [bytes, ...]}                      these raw stream lines, then the connection closes
  {"drip": seconds, ...}                     the answer's body (or stream) written one byte per `seconds`;
                                             with "drip_headers": true the status line and headers too
An empty queue answers {"content": "ok"}. Every request body is kept in `requests`.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import Gateway
from neuroscifi.inference.ledger import Ledger


class _Drip:
    """A response writer that sends one byte at a time (a server that keeps every read alive but never finishes)."""

    def __init__(self, raw, delay):
        self.raw, self.delay = raw, delay

    def write(self, data):
        try:
            for b in bytes(data):
                self.raw.write(bytes([b]))
                self.raw.flush()
                time.sleep(self.delay)
        except OSError:
            pass

    def flush(self):
        pass


class FakeOpenAI:
    def __init__(self, *, key="test-key", models=("fake-model",)):
        self.key, self.models = key, list(models)
        self.replies, self.requests, self.paths, self.hosts = [], [], [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _auth(self):
                if fake.key and self.headers.get("Authorization") != f"Bearer {fake.key}":
                    self._json(401, {"error": {"message": "invalid api key"}})
                    return False
                return True

            def _json(self, code, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if getattr(self, "drip", None):
                    self.wfile = _Drip(self.wfile, self.drip)
                self.wfile.write(data)

            def do_GET(self):
                fake.paths.append(("GET", self.path))
                if self.path == "/health":
                    return self._json(200, {})
                if not self._auth():
                    return
                if self.path == "/v1/models":
                    return self._json(200, {"object": "list", "data": [
                        {"id": m, "object": "model", "owned_by": "fake", "max_model_len": 32768} for m in fake.models]})
                self._json(404, {"error": "not found"})

            def do_POST(self):
                fake.paths.append(("POST", self.path))
                fake.hosts.append(self.headers.get("Host"))
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self._auth():
                    return
                fake.requests.append(body)
                reply = fake.replies.pop(0) if fake.replies else {"content": "ok"}
                if reply.get("delay"):
                    time.sleep(reply["delay"])
                if "status" in reply:
                    return self._json(reply["status"], reply.get("body", {"error": {"message": "fake error"}}),
                                      reply.get("headers"))
                if "body" in reply:
                    return self._json(200, reply["body"])
                if "sse" in reply:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for line in reply["sse"]:
                        self.wfile.write(line)
                    self.close_connection = True
                    return
                self.drip = reply.get("drip")
                if self.drip and reply.get("drip_headers"):
                    self.wfile, self.drip = _Drip(self.wfile, self.drip), None
                content = reply.get("content")
                calls = [{"id": f"call_{i}", "type": "function",
                          "function": {"name": c["name"], "arguments": c["arguments"]}}
                         for i, c in enumerate(reply.get("tool_calls") or [])]
                usage = {"prompt_tokens": 11, "completion_tokens": max(1, len((content or "").split())),
                         "total_tokens": 0}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    if self.drip:
                        self.wfile = _Drip(self.wfile, self.drip)

                    def send(obj):
                        try:
                            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                            self.wfile.flush()
                        except OSError:   # the client stopped reading (deadline, output cap)
                            pass
                    text = content or ""
                    pieces = [text[i:i + 7] for i in range(0, len(text), 7)]
                    for piece in pieces:
                        send({"model": body.get("model"), "choices": [{"index": 0, "delta": {"content": piece},
                                                                       "finish_reason": None}]})
                    for i, call in enumerate(calls):
                        send({"model": body.get("model"), "choices": [{"index": 0, "delta": {"tool_calls": [
                            {"index": i, "id": call["id"], "function": call["function"]}]}}]})
                    send({"model": body.get("model"), "choices": [{"index": 0, "delta": {},
                                                                   "finish_reason": "tool_calls" if calls else "stop"}]})
                    send({"model": body.get("model"), "choices": [], "usage": usage})
                    send_raw = getattr(self.wfile, "write")
                    try:
                        send_raw(b"data: [DONE]\n\n")
                    except OSError:
                        pass
                    return
                message = {"role": "assistant", "content": content}
                if calls:
                    message["tool_calls"] = calls
                self._json(200, {"model": body.get("model"), "choices": [
                    {"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
                    "usage": usage})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.thread is not None:
            self.server.shutdown()
            self.thread = None
        self.server.server_close()


def make_gateway(fake, key_file, tmp_path, **changes):
    cfg = iconfig.InferenceConfig(base_url=fake.url, model="fake-model", key_file=str(key_file), backoff=0.01,
                                  **changes)
    sleeps = []
    gw = Gateway(cfg, key=iconfig.read_key(cfg), ledger=Ledger(tmp_path / "x.inference.sqlite"), sleep=sleeps.append)
    gw.sleeps = sleeps
    return gw
