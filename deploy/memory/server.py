#!/usr/bin/env python3
"""One process for both memory models: an embedding model and a reranker behind the two endpoints the individual
already speaks (webcortex/semantic.py through inference/gateway.py).

  embedding port   POST /v1/embeddings   {"model", "input": [text, ...]}            the OpenAI shape
  reranker port    POST /v1/rerank       {"model", "query", "documents": [text]}    the Jina/Cohere shape
  both ports       GET /v1/models, GET /health (no key), GET /metrics (no key)

It replaces two vLLM servers (deploy/memory/run.sh, NSF_MEMORY_SERVER=vllm) that each bring a whole serving engine
for a 0.6B model. Here one Python process holds both models, with the libraries the pinned image already contains
(torch, transformers) and the standard library's HTTP server: no other dependency, nothing downloaded.

What the models are given is what vLLM's pooling runner gives them, so the scores are the same scores:
- Embedding (Qwen3-Embedding): the text as sent, tokenised with the tokenizer's own end-of-text token, the hidden
  state of the last token, L2-normalised (the model card's `last_token_pool`; padding on the left so that the last
  position is every row's last token). No instruction is added here: the caller sends what it wants embedded.
- Reranker (Qwen3-Reranker): query and document joined as they are sent (the caller writes the model's prompt
  around them, semantic._pair), and the score is the probability of "yes" against "no" at the last position:
  sigmoid(logit_yes - logit_no), the model card's log_softmax over the two tokens. Only those two rows of the
  output layer are computed.
A text longer than the model's limit is refused with 400, as vLLM refuses it: a silently cut reranker prompt would
lose its closing template and score nonsense.

Security: started by run.sh inside a container whose ports Docker publishes on the host's loopback address only.
Every request but /health and /metrics needs the key (`Authorization: Bearer`, compared in constant time; the key
is read from a file mounted read-only, never from the environment or a command line). Outside a container the
server binds a loopback address only; any other address needs --container, which run.sh passes because there the
publish rule is what keeps it local. Bodies, batches and text lengths are bounded; one model call runs at a time
(one owner); no request text is ever logged. /metrics tells only how many requests ran (the idle policy reads it
to see whether the models are in use: resources/idle.py reads the same three names from a vLLM server).
"""
from __future__ import annotations

import argparse
import hmac
import http.server
import ipaddress
import json
import math
import signal
import sys
import threading
import time

MAX_BODY = 4 << 20             # bytes of one request body
MAX_INPUTS = 256               # texts of one embeddings request
MAX_DOCUMENTS = 256            # documents of one rerank request
MAX_CHARS = 64_000             # characters of one text (the token limit is checked after this cheap one)
MAX_CONNECTIONS = 32           # requests in the building at once; more are told 503
SOCKET_TIMEOUT = 30.0          # seconds a connection may take to send its request
BATCH_SEQUENCES = 16           # sequences of one forward pass
BATCH_TOKENS = 16_384          # padded tokens of one forward pass
CACHE_SLACK = 256 << 20        # bytes of unused device cache kept after a call; more is given back
MIN_KEY = 16


class Refused(Exception):
    """A request that is answered with an error status; `message` never holds request text."""

    def __init__(self, status, message, code):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# ---- the models --------------------------------------------------------------------------------------------------

class Models:
    """Both models on one device. `embed` and `rerank` are called under the server's lock, one at a time."""

    def __init__(self, embed_dir, rerank_dir, *, max_len, device="cuda", log=print):
        import torch
        from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

        self.torch, self.max_len, self.device = torch, int(max_len), device
        dtype = torch.bfloat16 if device != "cpu" else torch.float32     # the checkpoints' own precision on a GPU
        started = time.monotonic()
        self.embed_tok = AutoTokenizer.from_pretrained(embed_dir, local_files_only=True, padding_side="left")
        self.embed_model = self._on_device(AutoModel.from_pretrained(embed_dir, local_files_only=True, dtype=dtype))
        self.rerank_tok = AutoTokenizer.from_pretrained(rerank_dir, local_files_only=True, padding_side="left")
        lm = self._on_device(AutoModelForCausalLM.from_pretrained(rerank_dir, local_files_only=True, dtype=dtype))
        yes, no = self.rerank_tok.convert_tokens_to_ids("yes"), self.rerank_tok.convert_tokens_to_ids("no")
        if not isinstance(yes, int) or not isinstance(no, int) or yes == no or min(yes, no) < 0:
            raise RuntimeError("the reranker's tokenizer has no single tokens for 'yes' and 'no'")
        # The two rows of the output layer that the score needs, kept in single precision; the layer itself (a
        # vocabulary of 150k rows, tied to the input embedding) is not run.
        self.yes_no = lm.get_output_embeddings().weight[[no, yes]].detach().float().clone()
        self.rerank_model = lm.get_decoder() if hasattr(lm, "get_decoder") else lm.model
        # The first call of each shape pays for the device's start-up (measured: 1.4 s for the first two-text
        # request after a one-text warm-up), so a few shapes are run here, before the ports open.
        for texts in (["warm up"], ["warm up", "a second and somewhat longer text to warm up with"],
                      ["warm up " * 40] * BATCH_SEQUENCES):
            self.embed(texts)
            self.rerank("warm up ", texts)
        log(f"models loaded in {time.monotonic() - started:.1f}s on {device} ({str(dtype).rsplit('.', 1)[-1]})")

    def _on_device(self, model):
        """The model on the device, in evaluation mode. Each tensor is first copied out of the file mapping into
        the process's own memory and moved from there: measured on the reference host (GB10, unified memory),
        moving file-mapped weights straight to the device ran at about 5 MB/s (59 s for the 311 MB embedding table
        alone); copied first, the 1.2 GB of a model move in about 6 s. One tensor at a time, so the copy never
        holds more than the largest tensor."""
        torch = self.torch
        if self.device != "cpu":
            with torch.no_grad():
                for tensor in (*model.parameters(), *model.buffers()):
                    tensor.data = tensor.data.clone().to(self.device)
        return model.to(self.device).eval()

    def _release(self):
        """Give the device's cached blocks back after a batch much larger than the usual one (measured: 64 texts
        at once left 440 MiB cached for good). The usual lookup, one question and a dozen statements, keeps its
        cache and pays nothing."""
        cuda = self.torch.cuda
        if self.device != "cpu" and cuda.memory_reserved() - cuda.memory_allocated() > CACHE_SLACK:
            cuda.empty_cache()

    def _batches(self, tok, texts):
        """Token ids per text (refusing one over the limit), then index groups that fit one forward pass."""
        ids = tok(texts, add_special_tokens=True, return_attention_mask=False)["input_ids"]
        for n in map(len, ids):
            if n > self.max_len:
                raise Refused(400, f"a text has {n} tokens; this model serves at most {self.max_len}",
                              "context_length_exceeded")
        group = []
        for i in sorted(range(len(ids)), key=lambda i: len(ids[i])):      # similar lengths together: less padding
            # ascending order: this text is the widest of its group, so the group pads to its length
            if group and (len(group) >= BATCH_SEQUENCES or len(ids[i]) * (len(group) + 1) > BATCH_TOKENS):
                yield group, ids
                group = []
            group.append(i)
        if group:
            yield group, ids

    def _hidden(self, model, tok, ids, group):
        """The last token's hidden state of each text of the group, in single precision."""
        batch = tok.pad({"input_ids": [ids[i] for i in group]}, padding=True, return_tensors="pt")
        batch = {name: value.to(self.device) for name, value in batch.items()}
        out = model(**batch)
        return out.last_hidden_state[:, -1].float() if hasattr(out, "last_hidden_state") else out[0][:, -1].float()

    def embed(self, texts) -> tuple[list[list[float]], int]:
        torch = self.torch
        vectors, tokens = [None] * len(texts), 0
        with torch.inference_mode():
            for group, ids in self._batches(self.embed_tok, texts):
                hidden = torch.nn.functional.normalize(self._hidden(self.embed_model, self.embed_tok, ids, group),
                                                       p=2, dim=1).cpu().tolist()
                for i, vector in zip(group, hidden):
                    vectors[i] = vector
                    tokens += len(ids[i])
        self._release()
        return vectors, tokens

    def rerank(self, query, documents) -> tuple[list[float], int]:
        torch = self.torch
        scores, tokens = [None] * len(documents), 0
        with torch.inference_mode():
            for group, ids in self._batches(self.rerank_tok, [query + d for d in documents]):
                logits = self._hidden(self.rerank_model, self.rerank_tok, ids, group) @ self.yes_no.T
                for i, score in zip(group, torch.sigmoid(logits[:, 1] - logits[:, 0]).cpu().tolist()):
                    scores[i] = score
                    tokens += len(ids[i])
        self._release()
        return scores, tokens


# ---- the service -------------------------------------------------------------------------------------------------

class Service:
    """What both listeners share: the models, their lock, the key and the counters."""

    def __init__(self, models, key, names, *, max_len, log=print):
        if not isinstance(key, str) or len(key) < MIN_KEY:
            raise ValueError(f"the API key must hold at least {MIN_KEY} characters")
        self.models, self.key, self.names, self.max_len, self.log = models, key, names, int(max_len), log
        self.lock = threading.Lock()                # one model call at a time
        self.count = threading.Lock()
        self.running = self.waiting = self.served = 0
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def authorised(self, header) -> bool:
        given = header[7:] if isinstance(header, str) and header[:7].lower() == "bearer " else ""
        return hmac.compare_digest(given.encode("utf-8", "replace"), self.key.encode("utf-8"))

    def call(self, fn, *args):
        """Run one model call, alone; a call that fails is not counted as served."""
        with self.count:
            self.waiting += 1
        self.lock.acquire()
        with self.count:
            self.waiting -= 1
            self.running += 1
        try:
            out = fn(*args)
        finally:
            with self.count:
                self.running -= 1
            self.lock.release()
        with self.count:
            self.served += 1
        return out

    def metrics(self) -> str:
        with self.count:
            running, waiting, served = self.running, self.waiting, self.served
        lines = []
        for name, kind, value in (("vllm:num_requests_running", "gauge", running),
                                  ("vllm:num_requests_waiting", "gauge", waiting),
                                  ("vllm:request_success_total", "counter", served)):
            lines += [f"# TYPE {name} {kind}", f'{name}{{server="neuroscifi-memory"}} {float(value)}']
        return "\n".join(lines) + "\n"


def _texts(value, what, limit, *, single=False):
    """A non-empty list of non-empty strings within the limits, or Refused."""
    if single and isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise Refused(400, f"{what} must be a non-empty list of texts", "invalid_request")
    if len(value) > limit:
        raise Refused(400, f"{what} holds {len(value)} texts; at most {limit} in one request", "invalid_request")
    out = []
    for item in value:
        if isinstance(item, dict) and set(item) == {"text"}:
            item = item["text"]
        if not isinstance(item, str) or not item:
            raise Refused(400, f"{what} must hold non-empty texts", "invalid_request")
        if len(item) > MAX_CHARS:
            raise Refused(400, f"a text of {what} has {len(item)} characters; at most {MAX_CHARS}", "invalid_request")
        out.append(item)
    return out


def _model(body, served):
    name = body.get("model")
    if name is not None and name != served:
        raise Refused(404, "this endpoint does not serve the model asked for", "model_not_found")


def embeddings(service: Service, body: dict) -> tuple[dict, int]:
    _model(body, service.names["embed"])
    if body.get("encoding_format", "float") != "float":
        raise Refused(400, "only encoding_format 'float' is served", "invalid_request")
    for unsupported in ("dimensions", "truncate_prompt_tokens"):
        if body.get(unsupported) is not None:
            raise Refused(400, f"'{unsupported}' is not supported", "invalid_request")
    texts = _texts(body.get("input"), "input", MAX_INPUTS, single=True)
    vectors, tokens = service.call(service.models.embed, texts)
    if len(vectors) != len(texts) or not all(isinstance(v, list) and v and all(map(math.isfinite, v)) for v in vectors):
        raise Refused(500, "the embedding model returned an unusable vector", "internal_error")
    return {"object": "list", "model": service.names["embed"],
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}, len(texts)


def rerank(service: Service, body: dict) -> tuple[dict, int]:
    _model(body, service.names["rerank"])
    query = body.get("query")
    if not isinstance(query, str) or not query or len(query) > MAX_CHARS:
        raise Refused(400, "query must be a non-empty text within the size limit", "invalid_request")
    documents = _texts(body.get("documents"), "documents", MAX_DOCUMENTS)
    top = body.get("top_n")
    if top is not None and (isinstance(top, bool) or not isinstance(top, int) or top < 0):
        raise Refused(400, "top_n must be a non-negative integer", "invalid_request")
    scores, tokens = service.call(service.models.rerank, query, documents)
    if len(scores) != len(documents) or not all(isinstance(s, float) and math.isfinite(s) for s in scores):
        raise Refused(500, "the reranker returned an unusable score", "internal_error")
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    if top:
        order = order[:top]
    return {"model": service.names["rerank"], "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            "results": [{"index": i, "relevance_score": scores[i]} for i in order]}, len(documents)


ROUTES = {"embed": ("/v1/embeddings", embeddings), "rerank": ("/v1/rerank", rerank)}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "neuroscifi-memory"
    sys_version = ""
    timeout = SOCKET_TIMEOUT

    def log_message(self, *args):          # the default access log is replaced by _done (never any request text)
        pass

    def _send(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self, status, message, code):
        self._send(status, {"error": {"message": message, "type": "invalid_request_error" if status < 500 else
                                      "server_error", "code": code}})

    def _done(self, status, started, items=0):
        role = self.server.role
        self.server.service.log(f"{role} {self.command} {self.path.split('?', 1)[0][:40]} {status} items={items} "
                                f"{(time.monotonic() - started) * 1000:.0f}ms")

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise Refused(411, "send a Content-Length; chunked bodies are not accepted", "length_required")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise Refused(411, "send a Content-Length", "length_required") from None
        if length < 0 or length > MAX_BODY:
            raise Refused(413, f"the body is larger than {MAX_BODY} bytes", "request_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise Refused(400, "the body ended early", "invalid_request")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise Refused(400, "the body is not JSON in UTF-8", "invalid_request") from None
        if not isinstance(body, dict):
            raise Refused(400, "the body must be a JSON object", "invalid_request")
        return body

    def _handle(self, method):
        service, role, started = self.server.service, self.server.role, time.monotonic()
        path = self.path.split("?", 1)[0]
        if method == "GET" and path in ("/health", "/metrics"):      # answered whatever else is going on: the
            try:                                                     # container's health check and the idle policy
                if path == "/health":
                    self._send(200, {"status": "ok"})
                else:
                    self._send(200, service.metrics().encode(), "text/plain; version=0.0.4")
            except OSError:
                self.close_connection = True
            return self._done(200, started)
        if not service.slots.acquire(blocking=False):
            self._refuse(503, "too many requests at once", "overloaded")
            return self._done(503, started)
        status, items = 500, 0
        try:
            if not service.authorised(self.headers.get("Authorization")):
                raise Refused(401, "a valid API key is required", "unauthorised")
            if method == "GET" and path == "/v1/models":
                status = 200
                return self._send(200, {"object": "list", "data": [
                    {"id": service.names[role], "object": "model", "owned_by": "neuroscifi",
                     "max_model_len": service.max_len}]})
            route, fn = ROUTES[role]
            if method != "POST" or path != route:
                raise Refused(404, "no such endpoint on this port", "not_found")
            payload, items = fn(service, self._body())
            status = 200
            return self._send(200, payload)
        except Refused as err:
            status = err.status
            self._refuse(err.status, err.message, err.code)
        except (BrokenPipeError, ConnectionError, TimeoutError):
            status = 499
            self.close_connection = True
        except Exception as exc:               # never a traceback with request text; the kind of error only
            status = 500
            service.log(f"{role} error {type(exc).__name__}")
            try:
                self._refuse(500, "the model call failed", "internal_error")
            except OSError:
                self.close_connection = True
        finally:
            service.slots.release()
            self._done(status, started, items)

    def do_GET(self):          # noqa: N802 - the base class's naming
        self._handle("GET")

    def do_POST(self):         # noqa: N802
        self._handle("POST")


class Listener(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, service, role):
        self.service, self.role = service, role
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):       # a dropped connection is not worth a traceback
        self.service.log(f"{self.role} connection error {sys.exc_info()[0].__name__}")


def check_bind(host, container) -> str:
    """The address to listen on. Outside a container only a loopback address; --container (run.sh) allows any,
    because there Docker's publish rule decides who can reach the port."""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and not container:
        raise SystemExit(f"server.py: refusing to listen on {host}: only a loopback address outside a container")
    return host


def read_key(path) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError as exc:
        raise SystemExit(f"server.py: cannot read the key file: {exc.strerror}") from None
    if len(key) < MIN_KEY or any(ch.isspace() for ch in key):
        raise SystemExit(f"server.py: the key file must hold one key of at least {MIN_KEY} characters")
    return key


def serve(service, host, embed_port, rerank_port, stop: threading.Event):
    listeners = [Listener((host, embed_port), service, "embed"), Listener((host, rerank_port), service, "rerank")]
    threads = [threading.Thread(target=one.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
               for one in listeners]
    for thread in threads:
        thread.start()
    service.log(f"listening: embeddings on {host}:{listeners[0].server_address[1]}, "
                f"reranker on {host}:{listeners[1].server_address[1]}")
    try:
        stop.wait()
    finally:
        for one in listeners:
            one.shutdown()
            one.server_close()
    return listeners


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--embed-model", required=True, help="folder of the embedding model")
    ap.add_argument("--rerank-model", required=True, help="folder of the reranker")
    ap.add_argument("--embed-name", required=True, help="the name the embedding model is served under")
    ap.add_argument("--rerank-name", required=True, help="the name the reranker is served under")
    ap.add_argument("--key-file", required=True, help="file that holds the API key")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--embed-port", type=int, default=8000)
    ap.add_argument("--rerank-port", type=int, default=8001)
    ap.add_argument("--max-len", type=int, default=2048, help="tokens of one text")
    ap.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    ap.add_argument("--container", action="store_true", help="allow a non-loopback address (run.sh, inside Docker)")
    a = ap.parse_args(argv)
    if not 16 <= a.max_len <= 32768:
        raise SystemExit("server.py: --max-len must be between 16 and 32768")
    host = check_bind(a.host, a.container)
    key = read_key(a.key_file)

    def log(message):
        print(time.strftime("%Y-%m-%dT%H:%M:%S"), message, flush=True)

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    models = Models(a.embed_model, a.rerank_model, max_len=a.max_len, device=a.device, log=log)
    if stop.is_set():
        return 0
    service = Service(models, key, {"embed": a.embed_name, "rerank": a.rerank_name}, max_len=a.max_len, log=log)
    serve(service, host, a.embed_port, a.rerank_port, stop)
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
