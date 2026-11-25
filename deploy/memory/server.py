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
A text longer than the model's limit is refused with 400 and vLLM's own wording ("maximum context length"), which
the gateway reads as context_overflow: a silently cut reranker prompt would lose its closing template and score
nonsense.

Who waits for whom: the device is taken in turns of one forward pass (at most BATCH_SEQUENCES texts and
BATCH_TOKENS padded tokens), handed out in the order asked, whichever model asks. A call waits at most for the one
forward pass running when it arrives, never for the rest of a large call. Measured on the reference host: one
question's embedding waited 8.0 s behind the largest accepted batch of either model with one lock per request, and
up to 1.5 s with the two models running side by side on the one GPU. Tokenising happens before the turn, a few
texts at a time. A request holds at most MAX_REQUEST_TOKENS.

Security: started by run.sh inside a container without a network (`--network none`: no route to anywhere, so
nothing in it can send a byte out), listening on two Unix sockets (--unix-dir) that deploy/memory/forward.py, in a
container of its own with neither the models nor the key, publishes on the host's loopback address only.
NSF_MEMORY_EGRESS=bridge keeps the earlier way: this container on its own bridge, its ports published directly.
Every request but /health and /metrics needs the key (`Authorization: Bearer`, compared in constant time; the key
is read from a file mounted read-only, never from the environment or a command line). Outside a container the
server binds a loopback address only; any other address needs --container, which run.sh passes because there the
publish rule is what keeps it local. A connection gets no thread until its request head is whole: until then one
door thread reads it (non-blocking, at most HEAD_TIMEOUT, at most MAX_PENDING such connections; when more arrive,
the oldest waiting one is closed, which only a client that sends nothing can be). A whole head is then served by a
thread of its kind: MAX_CONNECTIONS - KEYLESS_SLOTS for requests that carry the valid key, KEYLESS_SLOTS for the
rest (/health, /metrics, refusals), so clients without the key can never take the slots of calls that have it
(review L3b: 32 keep-alive /health pollers made every keyed call 503). An answer to a request without the key closes
its connection. A request beyond the slots of its kind is answered 503: the server shuts its side for writing, and
one shared drainer reads and discards what the client still sends (at most DRAIN_BYTES, DRAIN_SECONDS) before
closing, so a client in the middle of a large body still reads the 503 instead of a reset. A slot and its thread
last one request: a connection kept alive goes back to the door after its answer, with what its client already sent
of the next request, so kept connections can never hold the slots (ENGINEERING_PLAN §6.7: 32 keyed keep-alive
connections made every other keyed call 503 for the head deadline of their next request). A body must arrive whole
within BODY_TIMEOUT, the next request's head on a kept connection within HEAD_TIMEOUT (the door closes it
otherwise). The head is parsed strictly: a request line of at most MAX_REQUEST_LINE bytes, at most MAX_HEADERS headers of MAX_HEADER_BYTES
together, exactly one Content-Length of plain digits on a POST and none (or 0) on a GET, one Host and one
Authorization at most, no Transfer-Encoding. Bodies, batches and text lengths are bounded; no request text is ever
logged, and a path is logged only if it is one of the routes (otherwise "<other>"). Weights load from safetensors
only. /metrics tells only how many requests ran (the idle policy reads it
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
import socket
import sys
import threading
import time

MAX_BODY = 4 << 20             # bytes of one request body
MAX_INPUTS = 256               # texts of one embeddings request
MAX_DOCUMENTS = 256            # documents of one rerank request
MAX_CHARS = 64_000             # characters of one text (the token limit is checked after this cheap one)
MAX_CONNECTIONS = 32           # requests served at once (one thread each), both ports together
KEYLESS_SLOTS = 4              # of these, for requests without the valid key; the rest only for requests with it
MAX_PENDING = 256              # connections whose request head is still arriving (no thread); more: the oldest goes
MAX_REQUEST_LINE = 2048        # bytes of the request line
MAX_HEADERS = 32               # headers of one request
MAX_HEADER_BYTES = 16_384      # bytes of all header names and values together
MAX_REQUEST_TOKENS = 131_072   # tokens of one request: 64 texts of the longest kind, the gateway's largest batch
HEAD_TIMEOUT = 10.0            # seconds a request head (line and headers) may take to arrive, whole
BODY_TIMEOUT = 30.0            # seconds a request body may take to arrive, whole
SEND_TIMEOUT = 30.0            # seconds a response may take to be taken
SOCKET_TIMEOUT = 30.0          # seconds of one blocking read or write
BATCH_SEQUENCES = 16           # sequences of one forward pass
BATCH_TOKENS = 4096            # padded tokens of one forward pass: the longest any other call waits for the device
TOKENIZE_CHUNK = 16            # texts tokenised at once
DRAIN_SECONDS = 2.0            # a refused connection is read and discarded this long at most before it is closed
DRAIN_BYTES = MAX_BODY + MAX_HEADER_BYTES + MAX_REQUEST_LINE + 1024   # and this much: the largest request accepted
DRAIN_MAX = 256                # refused connections being drained at once; more are closed at once
HEAD_BYTES = MAX_REQUEST_LINE + MAX_HEADER_BYTES + 4096   # a head not whole by then is served as it is (and refused)
ROUTES_KNOWN = ("/health", "/metrics", "/v1/models", "/v1/embeddings", "/v1/rerank")
_BUSY_BODY = b'{"error":{"message":"too many connections at once","type":"server_error","code":"overloaded"}}'
BUSY = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\n"
        b"Content-Length: " + str(len(_BUSY_BODY)).encode() + b"\r\n\r\n" + _BUSY_BODY)
CACHE_SLACK = 256 << 20        # bytes of unused device cache kept after a call; more is given back
MIN_KEY = 16


def too_long(tokens, max_len) -> str:
    """vLLM's wording, so that the gateway reads the refusal as context_overflow."""
    return (f"This model's maximum context length is {max_len} tokens; a text of this request has {tokens} "
            "tokens")


class _Turns:
    """A lock handed out in the order it was asked for (threading.Lock is not: a thread that releases and asks
    again usually gets it back first, so a large call would keep it from a small one)."""

    def __init__(self):
        self._cond = threading.Condition()
        self._next = self._now = 0

    def __enter__(self):
        with self._cond:
            ticket, self._next = self._next, self._next + 1
            while ticket != self._now:
                self._cond.wait()

    def __exit__(self, *exc):
        with self._cond:
            self._now += 1
            self._cond.notify_all()


class Refused(Exception):
    """A request that is answered with an error status; `message` never holds request text."""

    def __init__(self, status, message, code):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# ---- the models --------------------------------------------------------------------------------------------------

class Models:
    """Both models on one device, taken in turns of one forward pass (see the module's doc)."""

    def __init__(self, embed_dir, rerank_dir, *, max_len, device="cuda", log=print):
        import torch
        from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

        self.torch, self.max_len, self.device = torch, int(max_len), device
        self.scheduling()
        dtype = torch.bfloat16 if device != "cpu" else torch.float32     # the checkpoints' own precision on a GPU
        started = time.monotonic()
        self.embed_tok = AutoTokenizer.from_pretrained(embed_dir, local_files_only=True, padding_side="left")
        self.embed_model = self._on_device(AutoModel.from_pretrained(embed_dir, local_files_only=True, dtype=dtype,
                                                                     use_safetensors=True))
        self.rerank_tok = AutoTokenizer.from_pretrained(rerank_dir, local_files_only=True, padding_side="left")
        lm = self._on_device(AutoModelForCausalLM.from_pretrained(rerank_dir, local_files_only=True, dtype=dtype,
                                                                  use_safetensors=True))
        yes, no = self.rerank_tok.convert_tokens_to_ids("yes"), self.rerank_tok.convert_tokens_to_ids("no")
        if not isinstance(yes, int) or not isinstance(no, int) or yes == no or min(yes, no) < 0:
            raise RuntimeError("the reranker's tokenizer has no single tokens for 'yes' and 'no'")
        # The two rows of the output layer that the score needs, kept in single precision; the layer itself (a
        # vocabulary of 150k rows, tied to the input embedding) is not run.
        self.yes_no = lm.get_output_embeddings().weight[[no, yes]].detach().float().clone()
        self.rerank_model = lm.get_decoder() if hasattr(lm, "get_decoder") else lm.model
        self.pad_ids = {"embed": self.embed_tok.pad_token_id, "rerank": self.rerank_tok.pad_token_id}
        if any(not isinstance(v, int) for v in self.pad_ids.values()):
            raise RuntimeError("a tokenizer has no padding token")
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
        if self.device == "cpu":
            return
        cuda = self.torch.cuda
        if cuda.memory_reserved() - cuda.memory_allocated() > CACHE_SLACK:
            cuda.empty_cache()

    def scheduling(self):
        """The turns: one for the device, taken per forward pass by either model (two models side by side on one
        GPU slowed a waiting lookup to 1.5 s); one lock per tokenizer (a fast tokenizer must not be used by two
        threads at once), held for TOKENIZE_CHUNK texts at a time."""
        device = _Turns()
        self.turns = {"embed": device, "rerank": device}
        self.tok_locks = {"embed": threading.Lock(), "rerank": threading.Lock()}

    def _prepare(self, kind, tok, texts):
        """Token ids per text and the groups of one forward pass each, refusing a text over the model's limit and a
        request over MAX_REQUEST_TOKENS before any forward pass. Similar lengths go together (less padding)."""
        ids, total = [], 0
        for start in range(0, len(texts), TOKENIZE_CHUNK):      # a few at a time: another call never waits long
            with self.tok_locks[kind]:
                part = tok(texts[start:start + TOKENIZE_CHUNK], add_special_tokens=True,
                           return_attention_mask=False)["input_ids"]
            longest = max(map(len, part))
            if longest > self.max_len:
                raise Refused(400, too_long(longest, self.max_len), "context_length_exceeded")
            total += sum(map(len, part))
            if total > MAX_REQUEST_TOKENS:
                raise Refused(400, f"the request holds more than {MAX_REQUEST_TOKENS} tokens, the most one request "
                              "may hold", "request_too_large")
            ids += part
        groups, group = [], []
        for i in sorted(range(len(ids)), key=lambda i: len(ids[i])):
            # ascending order: this text is the widest of its group, so the group pads to its length
            if group and (len(group) >= BATCH_SEQUENCES or len(ids[i]) * (len(group) + 1) > BATCH_TOKENS):
                groups.append(group)
                group = []
            group.append(i)
        if group:
            groups.append(group)
        return ids, groups

    def _run(self, kind, tok, texts, compute) -> tuple[list, int]:
        """compute(ids, group) for every group, each in the model's turn -> (values in the texts' order, tokens)."""
        ids, groups = self._prepare(kind, tok, texts)       # before the turn: tokenising never holds the device
        out, tokens = [None] * len(texts), 0
        for group in groups:
            with self.turns[kind], self.torch.inference_mode():
                values = compute(ids, group)
            for i, value in zip(group, values):
                out[i] = value
                tokens += len(ids[i])
        self._release()
        return out, tokens

    def _hidden(self, kind, model, ids, group):
        """The last token's hidden state of each text of the group, in single precision. Padding on the left, as
        the model cards do it (the tokenizer's own pad, without calling the tokenizer from this thread)."""
        torch = self.torch
        rows = [ids[i] for i in group]
        width = max(map(len, rows))
        pad = self.pad_ids[kind]
        input_ids = torch.tensor([[pad] * (width - len(r)) + list(r) for r in rows], device=self.device)
        mask = torch.tensor([[0] * (width - len(r)) + [1] * len(r) for r in rows], device=self.device)
        out = model(input_ids=input_ids, attention_mask=mask)
        return out.last_hidden_state[:, -1].float() if hasattr(out, "last_hidden_state") else out[0][:, -1].float()

    def embed(self, texts) -> tuple[list[list[float]], int]:
        normalize = self.torch.nn.functional.normalize

        def compute(ids, group):
            return normalize(self._hidden("embed", self.embed_model, ids, group), p=2, dim=1).cpu().tolist()
        return self._run("embed", self.embed_tok, texts, compute)

    def rerank(self, query, documents) -> tuple[list[float], int]:
        def compute(ids, group):
            logits = self._hidden("rerank", self.rerank_model, ids, group) @ self.yes_no.T
            return self.torch.sigmoid(logits[:, 1] - logits[:, 0]).cpu().tolist()
        return self._run("rerank", self.rerank_tok, [query + d for d in documents], compute)


# ---- the service -------------------------------------------------------------------------------------------------

class Service:
    """What both listeners share: the models, the key, the counters, the connection slots and their deadlines."""

    def __init__(self, models, key, names, *, max_len, log=print):
        if not isinstance(key, str) or len(key) < MIN_KEY:
            raise ValueError(f"the API key must hold at least {MIN_KEY} characters")
        self.models, self.key, self.names, self.max_len, self.log = models, key, names, int(max_len), log
        self.count = threading.Lock()
        self.running = self.served = 0
        self.keyed = threading.BoundedSemaphore(MAX_CONNECTIONS - KEYLESS_SLOTS)
        self.keyless = threading.BoundedSemaphore(KEYLESS_SLOTS)
        self.pending: dict = {}                     # socket -> [listener, address, deadline, head bytes so far]
        self.incoming: list = []                    # sockets accepted, not yet seen by the door
        self.prefixes: dict = {}                    # socket -> the head bytes the door read, for its handler
        self.door_lock = threading.Lock()
        self.door = None
        self.wake_r = self.wake_w = None
        self.deadlines: dict = {}                   # socket -> monotonic deadline of what it is sending (None: none)
        self.watch_lock = threading.Lock()
        self.reaper, self.closed = None, threading.Event()
        self.draining: dict = {}                    # refused socket -> [deadline, bytes it may still send]
        self.drain_lock = threading.Lock()
        self.drainer = None

    def close(self) -> None:
        """Stop the door, the reaper and the drainer (the listeners are closed by their owner)."""
        self.closed.set()
        self._wake()
        for thread in (self.door, self.reaper, self.drainer):
            if thread is not None:
                thread.join(DRAIN_SECONDS + 1)

    # ---- the door: no thread before a request head is whole --------------------------------------------------------
    def admit(self, listener, sock, address, head: bytes = b"") -> None:
        """Hand a connection to the door: one just accepted (called on the accepting thread; never blocks, never
        reads), or one kept alive after its answer, with what its client already sent of the next request (`head`).
        Between two requests a kept connection holds no slot and no thread (open-items §6.7: 28 keyed keep-alive
        connections held every keyed slot for the head deadline of their next request)."""
        import socket
        with self.door_lock:
            if self.closed.is_set():
                sock.close()
                return
            self.incoming.append((listener, sock, address, head))
            if self.door is None:
                self.wake_r, self.wake_w = socket.socketpair()
                self.wake_r.setblocking(False)
                self.wake_w.setblocking(False)
                self.door = threading.Thread(target=self._door, name="nsf-memory-door", daemon=True)
                self.door.start()
        self._wake()

    def _wake(self) -> None:
        try:
            if self.wake_w is not None:
                self.wake_w.send(b"x")
        except OSError:                 # its buffer is full: the door is awake already
            pass

    def _drop(self, sel, sock) -> None:
        self.pending.pop(sock, None)
        try:
            sel.unregister(sock)
        except (KeyError, ValueError, OSError):
            pass

    def _door(self) -> None:
        import selectors
        sel = selectors.DefaultSelector()
        sel.register(self.wake_r, selectors.EVENT_READ)
        try:
            while not self.closed.is_set():
                events = sel.select(timeout=0.2)
                with self.door_lock:
                    arrived, self.incoming = self.incoming, []
                for listener, sock, address, head in arrived:
                    if b"\r\n\r\n" in head or len(head) >= HEAD_BYTES:     # a pipelined request, whole already
                        self._dispatch(listener, sock, address, head)
                        continue
                    try:
                        sock.setblocking(False)
                        sel.register(sock, selectors.EVENT_READ)
                    except (OSError, ValueError):
                        sock.close()
                        continue
                    self.pending[sock] = [listener, address, time.monotonic() + HEAD_TIMEOUT, bytearray(head)]
                    while len(self.pending) > MAX_PENDING:        # the oldest waiting connection makes room
                        oldest = next(iter(self.pending))
                        self._drop(sel, oldest)
                        oldest.close()
                for key, _ in events:
                    sock = key.fileobj
                    if sock is self.wake_r:
                        try:
                            while self.wake_r.recv(4096):
                                pass
                        except OSError:
                            pass
                        continue
                    entry = self.pending.get(sock)
                    if entry is None:
                        continue
                    try:
                        got = sock.recv(4096)
                    except BlockingIOError:
                        continue
                    except OSError:
                        got = b""
                    if not got:                                   # the client went away
                        self._drop(sel, sock)
                        sock.close()
                        continue
                    entry[3] += got
                    if b"\r\n\r\n" in entry[3] or len(entry[3]) >= HEAD_BYTES:
                        self._drop(sel, sock)
                        self._dispatch(entry[0], sock, entry[1], bytes(entry[3]))
                now = time.monotonic()
                for sock in [s for s, e in self.pending.items() if now > e[2]]:   # a head too slow to arrive
                    self._drop(sel, sock)
                    sock.close()
        finally:
            for sock in list(self.pending):
                self._drop(sel, sock)
                sock.close()
            with self.door_lock:
                for _, sock, _, _ in self.incoming:
                    sock.close()
                self.incoming = []
            sel.close()
            self.wake_r.close()
            self.wake_w.close()

    def keyed_head(self, head: bytes) -> bool:
        """Whether a request head carries exactly one Authorization header with the valid key (one line, unfolded).
        Only decides which slots serve it; the handler checks everything again."""
        lines = head.split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]
        found = [line for line in lines if line.split(b":", 1)[0].strip().lower() == b"authorization"]
        if len(found) != 1 or any(line[:1] in (b" ", b"\t") for line in lines):
            return False
        return self.authorised(found[0].split(b":", 1)[1].strip().decode("latin-1"))

    def _dispatch(self, listener, sock, address, head: bytes) -> None:
        slots = self.keyed if self.keyed_head(head) else self.keyless
        if not slots.acquire(blocking=False):
            self.refuse(sock)
            return
        self.prefixes[sock] = head
        try:
            sock.settimeout(SOCKET_TIMEOUT)
            threading.Thread(target=listener.serve_admitted, args=(sock, address, slots),
                             name="nsf-memory-request", daemon=True).start()
        except BaseException:
            self.prefixes.pop(sock, None)
            slots.release()
            sock.close()
            raise

    def refuse(self, sock) -> None:
        """Answer a connection beyond the cap with BUSY without reading it, on the accepting thread, without
        blocking it: the response is written to a fresh socket's empty buffer, the write side is shut (the client
        sees the response end), and the socket goes to the drainer. Closing it while the client's body is still
        arriving would make the kernel reset the connection, and the client would lose the 503 (found: a POST under
        load got BrokenPipeError)."""
        import socket
        try:
            sock.setblocking(False)
            sent = sock.send(BUSY)
            if sent != len(BUSY):
                raise OSError("the 503 did not fit the socket's buffer")
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            sock.close()
            return
        with self.drain_lock:
            if len(self.draining) >= DRAIN_MAX or self.closed.is_set():
                sock.close()
                return
            self.draining[sock] = [time.monotonic() + DRAIN_SECONDS, DRAIN_BYTES]
            if self.drainer is None:
                self.drainer = threading.Thread(target=self._drain, name="nsf-memory-drainer", daemon=True)
                self.drainer.start()

    def _drain(self) -> None:
        """One thread for every refused connection: read and discard until the client closes, the byte budget is
        spent or the deadline passes, then close."""
        import selectors
        while not self.closed.is_set():
            with self.drain_lock:
                socks = list(self.draining)
            if not socks:
                self.closed.wait(0.05)
                continue
            with selectors.DefaultSelector() as sel:
                for sock in socks:
                    try:
                        sel.register(sock, selectors.EVENT_READ)
                    except (OSError, ValueError, KeyError):
                        pass
                readable = {key.fileobj for key, _ in sel.select(timeout=0.05)}
            now = time.monotonic()
            done = []
            for sock in socks:
                entry = self.draining.get(sock)
                if entry is None:
                    continue
                if sock in readable:
                    try:
                        got = sock.recv(65536)
                    except BlockingIOError:
                        got = None
                    except OSError:
                        got = b""
                    if got == b"":
                        done.append(sock)
                        continue
                    if got:
                        entry[1] -= len(got)
                if entry[1] <= 0 or now > entry[0]:
                    done.append(sock)
            if done:
                with self.drain_lock:
                    for sock in done:
                        self.draining.pop(sock, None)
                for sock in done:
                    sock.close()
        with self.drain_lock:
            left, self.draining = list(self.draining), {}
        for sock in left:
            sock.close()

    def watch(self, sock, seconds) -> None:
        """Close `sock` if what it is sending now does not arrive whole within `seconds` (None: no deadline)."""
        with self.watch_lock:
            self.deadlines[sock] = None if seconds is None else time.monotonic() + seconds
            if self.reaper is None and not self.closed.is_set():
                self.reaper = threading.Thread(target=self._reap, name="nsf-memory-reaper", daemon=True)
                self.reaper.start()

    def unwatch(self, sock) -> None:
        with self.watch_lock:
            self.deadlines.pop(sock, None)

    def _reap(self) -> None:
        import socket
        while not self.closed.wait(0.2):
            now = time.monotonic()
            with self.watch_lock:
                late = [sock for sock, deadline in self.deadlines.items() if deadline is not None and now > deadline]
                for sock in late:
                    self.deadlines[sock] = None
            for sock in late:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def authorised(self, header) -> bool:
        given = header[7:] if isinstance(header, str) and header[:7].lower() == "bearer " else ""
        return hmac.compare_digest(given.encode("utf-8", "replace"), self.key.encode("utf-8"))

    def call(self, fn, *args):
        """Run one model call (the model's turns order it with others); counted as running meanwhile."""
        with self.count:
            self.running += 1
        try:
            return fn(*args)
        finally:
            with self.count:
                self.running -= 1

    def answered(self) -> None:
        """A model request answered 200: what /metrics counts as served (a refused or unusable one is not)."""
        with self.count:
            self.served += 1

    def metrics(self) -> str:
        with self.count:
            running, waiting, served = self.running, 0, self.served    # waiting: inside running (turns)
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
    """One request per thread: the door read its head; a connection kept alive goes back to the door afterwards
    (Listener.serve_admitted). Reads are unbuffered, so what the client sent beyond this request is exactly what
    the door's prefix still holds (`leftover`), and nothing is lost in a buffer between two requests."""
    protocol_version = "HTTP/1.1"
    server_version = "neuroscifi-memory"
    sys_version = ""
    timeout = SOCKET_TIMEOUT
    rbufsize = 0

    def handle(self):
        self.close_connection = True
        self.handle_one_request()

    def leftover(self) -> bytes:
        """What the client already sent of its next request (pipelined), for the door."""
        return self.rfile.head.read() if isinstance(self.rfile, _Prefixed) else b""

    def log_message(self, *args):          # the default access log is replaced by _done (never any request text)
        pass

    def setup(self):
        super().setup()
        head = self.server.service.prefixes.pop(self.connection, b"")
        if head:                                # what the door read comes first
            self.rfile = _Prefixed(head, self.rfile)

    def handle_one_request(self):
        self.server.service.watch(self.connection, HEAD_TIMEOUT)     # the next request's head, whole, in time
        super().handle_one_request()

    def parse_request(self):
        if len(self.raw_requestline) > MAX_REQUEST_LINE:
            self.requestline = self.request_version = self.command = ""     # as the base class does for its limit
            self.close_connection = True
            self.send_error(414)
            return False
        return super().parse_request()

    def _send(self, status, payload, content_type="application/json"):
        self.server.service.watch(self.connection, SEND_TIMEOUT)
        body = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400 or self.close_connection:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self, status, message, code):
        self._send(status, {"error": {"message": message, "type": "invalid_request_error" if status < 500 else
                                      "server_error", "code": code}})

    def _done(self, status, started, items=0):
        role = self.server.role
        path = self.path.split("?", 1)[0]
        shown = path if path in ROUTES_KNOWN else "<other>"          # never the bytes a client sent
        method = self.command if self.command in ("GET", "POST") else "<other>"
        self.server.service.log(f"{role} {method} {shown} {status} items={items} "
                                f"{(time.monotonic() - started) * 1000:.0f}ms")

    def _strict(self, method) -> int:
        """The head, checked before anything else -> the body's length (0 for a GET), or Refused."""
        headers = self.headers
        if len(headers) > MAX_HEADERS or sum(len(k) + len(v) for k, v in headers.items()) > MAX_HEADER_BYTES:
            raise Refused(431, "the request's headers are too many or too large", "headers_too_large")
        for name in ("Host", "Authorization", "Content-Length", "Transfer-Encoding"):
            if len(headers.get_all(name) or []) > 1:
                raise Refused(400, f"one {name} header at most", "invalid_request")
        if headers.get("Transfer-Encoding") is not None:
            raise Refused(411, "send a Content-Length; chunked bodies are not accepted", "length_required")
        given = headers.get("Content-Length")
        if given is not None and (not given.isascii() or not given.isdigit() or len(given) > 9
                                  or (len(given) > 1 and given.startswith("0"))):
            raise Refused(400, "Content-Length must be one plain number", "invalid_request")
        length = None if given is None else int(given)
        if method == "GET":
            if length:
                raise Refused(400, "a GET carries no body", "invalid_request")
            return 0
        if length is None:
            raise Refused(411, "send a Content-Length", "length_required")
        if length > MAX_BODY:
            raise Refused(413, f"the body is larger than {MAX_BODY} bytes", "request_too_large")
        return length

    def _body(self, length) -> dict:
        self.server.service.watch(self.connection, BODY_TIMEOUT)
        raw = self.rfile.read(length)
        self.server.service.watch(self.connection, None)             # the model's work has no deadline here
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
        status, items = 500, 0
        try:
            length = self._strict(method)
            if not service.authorised(self.headers.get("Authorization")):
                self.close_connection = True    # one request per connection without the key (review L3b)
            if method == "GET" and path in ("/health", "/metrics"):      # no key: the health check, the idle policy
                status = 200
                if path == "/health":
                    return self._send(200, {"status": "ok"})
                return self._send(200, service.metrics().encode(), "text/plain; version=0.0.4")
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
            payload, items = fn(service, self._body(length))
            status = 200
            service.answered()
            return self._send(200, payload)
        except Refused as err:
            status = err.status
            try:
                self._refuse(err.status, err.message, err.code)
            except OSError:
                self.close_connection = True
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

    def process_request(self, request, client_address):
        """No thread yet: the door reads the request head (Service.admit)."""
        self.service.admit(self, request, client_address)

    def serve_admitted(self, request, client_address, slots):
        """The thread of one request: answered, then the slot freed and the connection either closed or, kept
        alive, handed back to the door with what its client already sent of the next request."""
        again = None
        try:
            self.service.watch(request, HEAD_TIMEOUT)
            handler = self.RequestHandlerClass(request, client_address, self)
            if not handler.close_connection and not self.service.closed.is_set():
                again = handler.leftover()
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.service.unwatch(request)
            self.service.prefixes.pop(request, None)
            slots.release()
            if again is None:
                self.shutdown_request(request)
            else:
                self.service.admit(self, request, client_address, again)


class UnixListener(Listener):
    """A listener on a Unix socket (--unix-dir): how run.sh serves from a container without a network
    (`--network none`, so the process that holds the models and the key cannot open a connection to anywhere);
    deploy/memory/forward.py, in a container of its own that holds neither, publishes the two ports. The socket
    file is the owner's only (0600, in a folder run.sh keeps 0700); a stale one from a stopped server is replaced,
    anything else at that path is refused."""
    address_family = socket.AF_UNIX
    allow_reuse_address = False

    def server_bind(self):
        import os
        import stat
        path = self.server_address
        try:
            if not stat.S_ISSOCK(os.lstat(path).st_mode):
                raise SystemExit(f"server.py: {path} exists and is not a socket; not replaced")
            os.unlink(path)
        except FileNotFoundError:
            pass
        old = os.umask(0o177)
        try:
            self.socket.bind(path)
        finally:
            os.umask(old)
        self.bound = path
        os.chmod(path, 0o600)
        self.server_address = path
        self.server_name, self.server_port = "localhost", 0

    def process_request(self, request, client_address):
        self.service.admit(self, request, ("unix", 0))

    def server_close(self):
        """Closed, and the socket file removed: only the one this listener made (a refused path is left as it is)."""
        import os
        import stat
        super().server_close()
        path = getattr(self, "bound", None)
        try:
            if path and stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)
        except OSError:
            pass


class _Prefixed:
    """A request stream that first yields the bytes the door already read, then the socket's own."""

    def __init__(self, head, rest):
        import io
        self.head, self.rest = io.BytesIO(head), rest

    def readline(self, limit=-1):
        line = self.head.readline(limit)
        if line.endswith(b"\n") or (limit is not None and 0 <= limit <= len(line)):
            return line
        return line + self.rest.readline(-1 if limit is None or limit < 0 else limit - len(line))

    def read(self, n=-1):
        """`n` bytes, fewer only at the end of the stream (the socket's own stream is unbuffered: one read of it
        returns what has arrived, so it is read until `n` are there)."""
        got = self.head.read(n)
        if n is None or n < 0:
            return got + self.rest.read()
        parts = [got]
        while len(got) < n:
            more = self.rest.read(n - len(got))
            if not more:
                break
            parts.append(more)
            got = b"".join(parts)
            parts = [got]
        return got

    def close(self):
        self.rest.close()


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


def serve(service, host, embed_port, rerank_port, stop: threading.Event, unix_dir=None):
    if unix_dir:
        import os
        listeners = [UnixListener(os.path.join(unix_dir, f"{role}.sock"), service, role)
                     for role in ("embed", "rerank")]
        where = [one.server_address for one in listeners]
    else:
        listeners = [Listener((host, embed_port), service, "embed"),
                     Listener((host, rerank_port), service, "rerank")]
        where = [f"{host}:{one.server_address[1]}" for one in listeners]
    threads = [threading.Thread(target=one.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
               for one in listeners]
    for thread in threads:
        thread.start()
    service.log(f"listening: embeddings on {where[0]}, reranker on {where[1]}")
    try:
        stop.wait()
    finally:
        for one in listeners:
            one.shutdown()
            one.server_close()
        service.close()
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
    ap.add_argument("--unix-dir", help="listen on embed.sock and rerank.sock in this folder instead of TCP ports "
                    "(run.sh: the container has no network; forward.py publishes the ports)")
    a = ap.parse_args(argv)
    if not 16 <= a.max_len <= 32768:
        raise SystemExit("server.py: --max-len must be between 16 and 32768")
    host = a.host if a.unix_dir else check_bind(a.host, a.container)
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
    serve(service, host, a.embed_port, a.rerank_port, stop, unix_dir=a.unix_dir)
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
