"""deploy/memory/server.py, the one process that serves both memory models: its HTTP layer, request validation, key
check and counters against a fake model object (no GPU, no torch), through the real inference gateway where the
shapes matter. What the real models answer is measured, not tested here
(docs/quality/evidence/open_items_memory_server.json)."""
import hmac
import http.client
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import Gateway, InferenceError
from neuroscifi.resources import monitor

ROOT = Path(__file__).resolve().parents[1]
KEY = "nsfm-test-server-key-0123456789"
NAMES = {"embed": "qwen3-embedding-0.6b", "rerank": "qwen3-reranker-0.6b"}


def _load():
    spec = importlib.util.spec_from_file_location("nsf_memory_server", ROOT / "deploy" / "memory" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


class FakeModels:
    """Stands where the two models stand: vectors and scores that depend only on the text's length, a record of
    what it was given, and the token limit the real one enforces."""

    def __init__(self, max_len=2048):
        self.max_len, self.seen, self.fail, self.vector = max_len, [], None, None
        self.gate = threading.Event()
        self.gate.set()

    def _tokens(self, texts):
        counts = [len(t.split()) for t in texts]
        if max(counts) > self.max_len:
            raise server.Refused(400, server.too_long(max(counts), self.max_len), "context_length_exceeded")
        return sum(counts)

    def embed(self, texts):
        assert self.gate.wait(10)
        if self.fail:
            raise self.fail
        self.seen.append(("embed", list(texts)))
        return [list(self.vector) if self.vector else [float(len(t)), 1.0, 0.0] for t in texts], self._tokens(texts)

    def rerank(self, query, documents):
        assert self.gate.wait(10)
        self.seen.append(("rerank", query, list(documents)))
        return [1.0 / (1 + len(d)) for d in documents], self._tokens([query + d for d in documents])


@pytest.fixture
def running():
    """The server on two free loopback ports -> (models, service, {"embed": port, "rerank": port}, log lines)."""
    models, lines = FakeModels(), []
    service = server.Service(models, KEY, NAMES, max_len=models.max_len, log=lines.append)
    stop, ready, box = threading.Event(), threading.Event(), {}

    def serve():
        listeners = [server.Listener(("127.0.0.1", 0), service, "embed"),
                     server.Listener(("127.0.0.1", 0), service, "rerank")]
        box["ports"] = {"embed": listeners[0].server_address[1], "rerank": listeners[1].server_address[1]}
        threads = [threading.Thread(target=one.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                   for one in listeners]
        for thread in threads:
            thread.start()
        ready.set()
        stop.wait()
        for one in listeners:
            one.shutdown()
            one.server_close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        yield models, service, box["ports"], lines
    finally:
        models.gate.set()
        stop.set()
        thread.join(10)
        service.close()


def call(port, method, path, body=None, key=KEY, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    send = dict(headers or {})
    if key:
        send["Authorization"] = f"Bearer {key}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        send.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=data, headers=send)
        resp = conn.getresponse()
        text = resp.read()
        try:
            return resp.status, json.loads(text)
        except ValueError:
            return resp.status, text.decode()
    finally:
        conn.close()


def gateway(port, provider, tmp_path):
    key_file = tmp_path / "api_key"
    key_file.write_text(KEY + "\n")
    key_file.chmod(0o600)
    cfg = iconfig.InferenceConfig(base_url=f"http://127.0.0.1:{port}/v1", key_file=str(key_file), max_retries=0)
    gw = Gateway(cfg, key=iconfig.read_key(cfg, provider))
    gw.provider = provider
    return gw


# ---- the shapes the individual speaks ------------------------------------------------------------------------------

def test_the_gateway_embeds_and_reranks_through_the_server_as_it_does_through_vllm(running, tmp_path):
    models, _, ports, _ = running
    embedder, reranker = gateway(ports["embed"], "embed", tmp_path), gateway(ports["rerank"], "rerank", tmp_path)
    assert [m["id"] for m in embedder.models()] == [NAMES["embed"]]        # each port lists its own model first:
    assert [m["id"] for m in reranker.models()] == [NAMES["rerank"]]       # `nsf setup` takes the first one served
    assert embedder.embed(["abc", "a"], purpose="t") == [[3.0, 1.0, 0.0], [1.0, 1.0, 0.0]]
    assert reranker.rerank("q", ["dddd", "d", "dd"], purpose="t") == [0.2, 0.5, pytest.approx(1 / 3)]
    assert models.seen == [("embed", ["abc", "a"]), ("rerank", "q", ["dddd", "d", "dd"])]


def test_results_keep_the_callers_index_whatever_order_they_come_back_in(running):
    _, _, ports, _ = running
    status, body = call(ports["rerank"], "POST", "/v1/rerank", {"query": "q", "documents": ["dddd", "d", "dd"]})
    assert status == 200 and [r["index"] for r in body["results"]] == [1, 2, 0]         # best first, as vLLM
    assert body["usage"]["prompt_tokens"] == 3 and body["model"] == NAMES["rerank"]
    status, body = call(ports["rerank"], "POST", "/v1/rerank", {"query": "q", "documents": ["dddd", "d", "dd"],
                                                                "top_n": 1})
    assert [r["index"] for r in body["results"]] == [1]
    status, body = call(ports["embed"], "POST", "/v1/embeddings", {"input": "one text", "model": NAMES["embed"]})
    assert status == 200 and [d["index"] for d in body["data"]] == [0] and body["usage"]["prompt_tokens"] == 2
    status, body = call(ports["rerank"], "POST", "/v1/rerank", {"query": "q", "documents": [{"text": "d"}]})
    assert status == 200 and len(body["results"]) == 1


def test_each_port_serves_its_own_endpoint_only(running):
    models, _, ports, _ = running
    assert call(ports["embed"], "POST", "/v1/rerank", {"query": "q", "documents": ["d"]})[0] == 404
    assert call(ports["rerank"], "POST", "/v1/embeddings", {"input": ["a"]})[0] == 404
    assert call(ports["embed"], "GET", "/v1/embeddings")[0] == 404
    assert call(ports["embed"], "POST", "/v1/models", {})[0] == 404
    assert call(ports["embed"], "GET", "/nothing")[0] == 404 and models.seen == []


# ---- the key --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", [None, "", "wrong-key-of-the-same-length-00000", KEY[:-1], KEY + "x", KEY.upper()])
def test_everything_but_health_and_metrics_needs_the_key(running, key):
    models, _, ports, _ = running
    for port, method, path, body in ((ports["embed"], "GET", "/v1/models", None),
                                     (ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]}),
                                     (ports["rerank"], "POST", "/v1/rerank", {"query": "q", "documents": ["d"]}),
                                     (ports["rerank"], "GET", "/no/such/path", None)):
        status, answer = call(port, method, path, body, key=key)
        assert status == 401 and answer["error"]["code"] == "unauthorised"
    assert models.seen == []                                           # refused before the body was even read
    assert call(ports["embed"], "GET", "/health", key=None) == (200, {"status": "ok"})
    assert call(ports["rerank"], "GET", "/metrics", key=None)[0] == 200


def test_the_key_is_compared_in_constant_time_and_other_schemes_are_refused(running, monkeypatch):
    _, service, ports, _ = running
    seen = []
    real = hmac.compare_digest
    monkeypatch.setattr(server.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    assert call(ports["embed"], "GET", "/v1/models")[0] == 200
    assert seen and seen[-1] == (KEY.encode(), KEY.encode())
    assert call(ports["embed"], "GET", "/v1/models", key=None, headers={"Authorization": f"Basic {KEY}"})[0] == 401
    assert call(ports["embed"], "GET", "/v1/models", key=None, headers={"Authorization": KEY})[0] == 401
    assert service.authorised(f"bearer {KEY}") and not service.authorised(None) and not service.authorised(5)
    with pytest.raises(ValueError):
        server.Service(FakeModels(), "short", NAMES, max_len=16)


def test_the_key_file_must_hold_one_key_and_is_never_echoed(tmp_path, capsys):
    good = tmp_path / "key"
    good.write_text(KEY + "\n")
    assert server.read_key(good) == KEY
    for text in ("", "short\n", f"{KEY} {KEY}\n", f"{KEY}\nsecond-line-0123456789\n"):
        bad = tmp_path / "bad"
        bad.write_text(text)
        with pytest.raises(SystemExit) as err:
            server.read_key(bad)
        assert KEY not in str(err.value)
    with pytest.raises(SystemExit):
        server.read_key(tmp_path / "missing")


# ---- what is refused ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body,code", [
    ({}, "invalid_request"), ({"input": []}, "invalid_request"), ({"input": [""]}, "invalid_request"),
    ({"input": ["a", 5]}, "invalid_request"), ({"input": {"text": "a"}}, "invalid_request"),
    ({"input": ["a"] * 257}, "invalid_request"), ({"input": ["a" * 64_001]}, "invalid_request"),
    ({"input": ["a"], "encoding_format": "base64"}, "invalid_request"),
    ({"input": ["a"], "dimensions": 256}, "invalid_request"),
    ({"input": ["a"], "truncate_prompt_tokens": 8}, "invalid_request"),
    ({"input": ["a"], "model": "another-model"}, "model_not_found"),
    ({"input": ["word " * 3000]}, "context_length_exceeded")])
def test_an_embeddings_request_outside_the_limits_is_refused_with_a_reason(running, body, code):
    models, _, ports, _ = running
    status, answer = call(ports["embed"], "POST", "/v1/embeddings", body)
    assert status == (404 if code == "model_not_found" else 400) and answer["error"]["code"] == code
    assert not any(kind == "embed" for kind, *_ in models.seen) or code == "context_length_exceeded"


@pytest.mark.parametrize("body", [
    {"documents": ["d"]}, {"query": "", "documents": ["d"]}, {"query": 5, "documents": ["d"]},
    {"query": "q"}, {"query": "q", "documents": []}, {"query": "q", "documents": "d"},
    {"query": "q", "documents": ["d", ""]}, {"query": "q", "documents": ["d"] * 257},
    {"query": "q" * 64_001, "documents": ["d"]}, {"query": "q", "documents": ["d"], "top_n": -1},
    {"query": "q", "documents": ["d"], "top_n": True}, {"query": "q", "documents": ["d"], "top_n": 1.5},
    {"query": "q", "documents": [{"text": "d", "more": 1}]}])
def test_a_rerank_request_outside_the_limits_is_refused(running, body):
    models, _, ports, _ = running
    status, answer = call(ports["rerank"], "POST", "/v1/rerank", body)
    assert status == 400 and answer["error"]["code"] == "invalid_request" and models.seen == []


def test_a_body_that_is_not_a_json_object_or_has_no_length_is_refused(running):
    models, _, ports, _ = running
    for raw in (b"{not json", b"[1, 2]", b'"text"', b"\xff\xfe", b"[" * 100_000):
        status, answer = call(ports["embed"], "POST", "/v1/embeddings", raw=raw)
        assert status == 400 and answer["error"]["code"] == "invalid_request"
    conn = http.client.HTTPConnection("127.0.0.1", ports["embed"], timeout=10)
    conn.putrequest("POST", "/v1/embeddings")
    conn.putheader("Authorization", f"Bearer {KEY}")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    assert conn.getresponse().status == 411
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", ports["embed"], timeout=10)
    conn.putrequest("POST", "/v1/embeddings")
    conn.putheader("Authorization", f"Bearer {KEY}")
    conn.putheader("Content-Length", str(server.MAX_BODY + 1))            # announced, never sent
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()
    assert models.seen == []


def test_a_model_failure_is_a_plain_500_and_the_log_never_holds_request_text(running):
    models, _, ports, lines = running
    secret = "the owner's private statement about Orbix"
    models.fail = RuntimeError(f"CUDA error while embedding {secret}")
    status, answer = call(ports["embed"], "POST", "/v1/embeddings", {"input": [secret]})
    assert status == 500 and answer == {"error": {"message": "the model call failed", "type": "server_error",
                                                  "code": "internal_error"}}
    models.fail = None
    assert call(ports["embed"], "POST", "/v1/embeddings", {"input": [secret]})[0] == 200       # and it goes on
    assert call(ports["rerank"], "POST", f"/v1/rerank?note={secret.replace(' ', '+')}",
                {"query": secret, "documents": [secret]})[0] == 200
    for _ in range(200):                  # a request is logged just after its answer is sent: wait for the lines
        if sum(line.startswith(("embed POST /v1/embeddings 200", "rerank POST /v1/rerank 200"))
               for line in list(lines)) >= 2:
            break
        threading.Event().wait(0.02)
    text = "\n".join(lines)
    assert "embed error RuntimeError" in text and "POST /v1/embeddings 200 items=1" in text
    assert secret not in text and "Orbix" not in text and KEY not in text and "?" not in text


# ---- one call at a time, and what /metrics says --------------------------------------------------------------------

def test_metrics_report_what_runs_waits_and_was_served_in_the_names_the_idle_policy_reads(running):
    models, service, ports, _ = running
    url = f"http://127.0.0.1:{ports['embed']}/metrics"

    def samples(port):
        status, text = call(port, "GET", "/metrics", key=None)
        assert status == 200
        return {name: sum(v for _, v in rows) for name, rows in monitor.prometheus(text).items()}

    assert samples(ports["embed"]) == {"vllm:num_requests_running": 0.0, "vllm:num_requests_waiting": 0.0,
                                       "vllm:request_success_total": 0.0}
    from neuroscifi.resources import idle
    assert idle._metrics(url) is not None and idle._metrics(url) == {"in_flight": 0.0, "served": 0.0}
    models.gate.clear()                                               # the model is busy
    results = []
    threads = [threading.Thread(target=lambda: results.append(call(ports["embed"], "POST", "/v1/embeddings",
                                                                   {"input": ["a"]})[0])),
               threading.Thread(target=lambda: results.append(call(ports["rerank"], "POST", "/v1/rerank",
                                                                   {"query": "q", "documents": ["d"]})[0]))]
    for thread in threads:
        thread.start()
    for _ in range(200):                  # both run at once: the two models do not wait for each other (F8)
        seen = samples(ports["rerank"])
        if seen["vllm:num_requests_running"] == 2.0:
            break
        threading.Event().wait(0.02)
    assert (seen["vllm:num_requests_running"], seen["vllm:num_requests_waiting"]) == (2.0, 0.0)
    assert idle._metrics(url)["in_flight"] == 2.0                     # either port: the policy sees both in use
    models.gate.set()
    for thread in threads:
        thread.join(10)
    assert results == [200, 200] and len(models.seen) == 2
    after = samples(ports["embed"])
    assert after == samples(ports["rerank"]) == {"vllm:num_requests_running": 0.0, "vllm:num_requests_waiting": 0.0,
                                                 "vllm:request_success_total": 2.0}
    assert call(ports["embed"], "POST", "/v1/embeddings", {"input": [""]})[0] == 400
    assert samples(ports["embed"])["vllm:request_success_total"] == 2.0      # a refused request is not a use


def test_more_requests_at_once_than_the_server_holds_are_told_to_come_back(running):
    """Beyond the cap of its kind a request is answered 503 and gets no thread. Requests with the key and requests
    without it have separate slots (L3b), so a full keyless side never stops a keyed call, and the other way round;
    with the keyless slots full, health and metrics are 503 too (the idle policy then reads /metrics as
    unreadable, which never unloads anything)."""
    models, service, ports, _ = running
    for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
        assert service.keyed.acquire(timeout=5)          # a finishing request frees its slot just after
    try:
        status, answer = call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})
        assert status == 503 and answer["error"]["code"] == "overloaded" and models.seen == []
        assert call(ports["embed"], "GET", "/health", key=None)[0] == 200          # the keyless side is free
        for _ in range(server.KEYLESS_SLOTS):            # (its thread frees the slot just after its answer)
            assert service.keyless.acquire(timeout=5)
        try:
            assert call(ports["embed"], "GET", "/health", key=None)[0] == 503
            assert call(ports["rerank"], "GET", "/metrics", key=None)[0] == 503
        finally:
            for _ in range(server.KEYLESS_SLOTS):
                service.keyless.release()
    finally:
        for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
            service.keyed.release()
    assert call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] == 200


def test_a_client_still_sending_a_large_body_gets_the_503_not_a_broken_pipe(running, tmp_path):
    """The refusal used to close the connection while the client's body was still arriving: the client then got a
    broken pipe or a reset instead of the 503, and the gateway reported a connection failure, not "overloaded".
    The server now shuts its side for writing, reads and discards what still arrives (bounded), then closes."""
    models, service, ports, _ = running
    for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
        assert service.keyed.acquire(timeout=5)          # a finishing request frees its slot just after
    try:
        for size in (10, 200_000, server.MAX_BODY - 64):
            body = b'{"input":["' + b"a" * size + b'"]}'
            status, answer = call(ports["embed"], "POST", "/v1/embeddings", raw=body)
            assert status == 503 and answer["error"]["code"] == "overloaded", size
        embedder = gateway(ports["embed"], "embed", tmp_path)
        with pytest.raises(InferenceError) as err:
            embedder.embed(["word " * 50_000], purpose="t")
        assert err.value.code == "unavailable" and err.value.retryable and err.value.status == 503
        assert "too many connections at once" in str(err.value)
    finally:
        for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
            service.keyed.release()
    assert models.seen == []
    for _ in range(100):                                  # every refused connection is closed in the end
        if not service.draining:
            break
        threading.Event().wait(0.05)
    assert not service.draining


def test_the_gateway_reports_a_refusal_as_an_error_and_never_as_an_answer(running, tmp_path):
    _, _, ports, _ = running
    embedder = gateway(ports["embed"], "embed", tmp_path)
    with pytest.raises(InferenceError):
        embedder.embed(["word " * 3000], purpose="t")
    wrong = gateway(ports["embed"], "embed", tmp_path)
    wrong.key = "not-the-key-0123456789abcdef"
    with pytest.raises(InferenceError) as err:
        wrong.embed(["a"], purpose="t")
    assert err.value.code == "unauthorised"


# ---- where it may listen ---------------------------------------------------------------------------------------------

def test_outside_a_container_only_a_loopback_address_is_accepted():
    assert server.check_bind("127.0.0.1", False) == "127.0.0.1" and server.check_bind("::1", False) == "::1"
    assert server.check_bind("localhost", False) == "localhost"
    for host in ("0.0.0.0", "::", "192.168.1.5", "example.org", ""):
        with pytest.raises(SystemExit) as err:
            server.check_bind(host, False)
        assert "only a loopback address outside a container" in str(err.value)
    assert server.check_bind("0.0.0.0", True) == "0.0.0.0"            # run.sh, where Docker publishes on loopback


def test_run_sh_starts_the_server_with_the_key_as_a_read_only_file_and_both_ports_on_loopback(tmp_path):
    import os
    import subprocess
    models = tmp_path / "models"
    for name in NAMES.values():
        (models / name).mkdir(parents=True)
        (models / name / "config.json").write_text("{}")
    folder = tmp_path / ".config" / "neuroscifi" / "memory"
    folder.mkdir(parents=True)
    (folder / "memory.env").write_text(
        f"NSF_MEMORY_SERVER=single\nNSF_MEMORY_EMBED_MODEL_DIR={models}/{NAMES['embed']}\n"
        f"NSF_MEMORY_RERANK_MODEL_DIR={models}/{NAMES['rerank']}\n")
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "print", "all"], capture_output=True,
                          text=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, timeout=60)
    assert done.returncode == 0, done.stderr
    line, ports = done.stdout.splitlines()       # the model server; the forwarder that publishes its ports (§6.7)
    assert " --network none " in line and " -p " not in line and "--unix-dir /run/nsf-memory " in line
    assert "/sockets:/run/nsf-memory:rw " in line and "--unix-socket\\ /run/nsf-memory/embed.sock" in line
    assert "/api_key:/run/neuroscifi/api_key:ro" in line and "--key-file /run/neuroscifi/api_key" in line
    assert "--env-file" not in line and "API_KEY=" not in line        # the key is in no environment, no inspect
    assert "server.py:/opt/neuroscifi/server.py:ro" in line and "--entrypoint python3" in line
    for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user ", "--pids-limit 256",
                 "--memory 8g", ":ro -v", "@sha256:", "--device nvidia.com/gpu=all"):
        assert flag in line, flag
    assert f"{NAMES['embed']}:ro" in line and f"{NAMES['rerank']}:ro" in line
    assert " -p 127.0.0.1:18441:8000 -p 127.0.0.1:18442:8001 " in ports and "0.0.0.0:18" not in ports
    assert "--name neuroscifi-memory-ports --network neuroscifi-memory " in ports
    assert "/sockets:/run/nsf-memory:ro " in ports and "forward.py:/opt/neuroscifi/forward.py:ro" in ports
    for absent in ("api_key", "--device", "/models/", "server.py", "--env-file"):     # no key file, model or device
        assert absent not in ports, absent
    # keyed calls cross it in plain text, so it runs the standard library only (no site-packages, no .pth hook)
    assert "--entrypoint python3 " in ports and " -I -S /opt/neuroscifi/forward.py " in ports
    for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user ", "--memory 256m",
                 "--container --host 0.0.0.0", "--listen 8000=/run/nsf-memory/embed.sock",
                 "--listen 8001=/run/nsf-memory/rerank.sock"):
        assert flag in ports, flag
    assert not (folder / "api_key").exists() and not (tmp_path / ".cache").exists()    # `print` creates nothing
    with open(folder / "memory.env", "a") as fh:
        fh.write("NSF_MEMORY_EGRESS=bridge\n")                       # the way back: one container, as before
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "print", "all"], capture_output=True,
                          text=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, timeout=60)
    assert done.returncode == 0 and len(done.stdout.splitlines()) == 1
    assert " -p 127.0.0.1:18441:8000 -p 127.0.0.1:18442:8001 " in done.stdout and "--network none" not in done.stdout
    assert "--container --host 0.0.0.0 --embed-port 8000 --rerank-port 8001" in done.stdout
    with open(folder / "memory.env", "a") as fh:
        fh.write("NSF_MEMORY_EGRESS=open\n")
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "print", "all"], capture_output=True,
                          text=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, timeout=60)
    assert done.returncode == 1 and "NSF_MEMORY_EGRESS must be none or bridge" in done.stderr


# ---- review round: strict requests, bounded connections, turns per model (open-items review F8, F11, L3) ------------

def raw_exchange(port, data, *, wait=5.0):
    """Send raw bytes, read until the server closes or `wait` passes -> (bytes received, closed by the server)."""
    import socket
    sock = socket.create_connection(("127.0.0.1", port), timeout=wait)
    try:
        sock.sendall(data)
        got = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                return got, False
            if not chunk:
                return got, True
            got += chunk
    finally:
        sock.close()


def status_of(answer: bytes) -> int:
    return int(answer.split(b" ", 2)[1]) if answer.startswith(b"HTTP/1.") else 0


AUTH = f"Authorization: Bearer {KEY}\r\n".encode()


BODY = b'{"input":["abcde"]}'                              # 19 bytes


@pytest.mark.parametrize("lengths", [["19", "19"], ["+19"], ["1_9"], ["0x13"], ["-19"], ["19 19"], ["١٩"], ["019"]])
def test_a_content_length_that_is_not_exactly_one_plain_number_is_refused(running, lengths):
    """L3: duplicate Content-Length headers (the first won), `+19` and `1_9` (int() accepts both) were taken."""
    models, _, ports, _ = running
    body = BODY
    head = b"POST /v1/embeddings HTTP/1.1\r\nHost: x\r\n" + AUTH + b"Content-Type: application/json\r\n"
    head += b"".join(f"Content-Length: {v}\r\n".encode() for v in lengths)
    answer, _ = raw_exchange(ports["embed"], head + b"\r\n" + body)
    assert status_of(answer) == 400 and models.seen == [], answer[:120]


def test_duplicate_headers_that_decide_anything_and_a_get_with_a_body_are_refused(running):
    _, _, ports, _ = running
    for head in (b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + AUTH + b"\r\n",
                 b"GET /v1/models HTTP/1.1\r\nHost: x\r\nHost: y\r\n" + AUTH + b"\r\n",
                 b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + b"Content-Length: 5\r\n\r\nGET /"):
        answer, closed = raw_exchange(ports["embed"], head)
        assert status_of(answer) == 400 and closed, answer[:120]
    answer, _ = raw_exchange(ports["embed"], b"POST /v1/embeddings HTTP/1.1\r\nHost: x\r\n" + AUTH +
                             b"Transfer-Encoding: chunked\r\nContent-Length: 5\r\n\r\n")
    assert status_of(answer) == 411


def test_a_request_head_beyond_its_limits_is_refused(running):
    _, _, ports, _ = running
    many = b"".join(f"X-Filler-{n}: 1\r\n".encode() for n in range(server.MAX_HEADERS + 1))
    answer, _ = raw_exchange(ports["embed"], b"GET /health HTTP/1.1\r\nHost: x\r\n" + many + b"\r\n")
    assert status_of(answer) == 431
    big = b"X-Big: " + b"a" * server.MAX_HEADER_BYTES + b"\r\n"
    answer, _ = raw_exchange(ports["embed"], b"GET /health HTTP/1.1\r\nHost: x\r\n" + big + b"\r\n")
    assert status_of(answer) == 431
    answer, _ = raw_exchange(ports["embed"], b"GET /" + b"a" * server.MAX_REQUEST_LINE + b" HTTP/1.1\r\n\r\n")
    assert status_of(answer) == 414


def test_connections_that_send_nothing_get_no_thread_and_never_lock_out_a_keyed_call(running, monkeypatch):
    """L3: 300 half-open connections made 300 threads. Now a connection gets a thread only once its request head is
    whole; until then it is a socket the door watches, and when MAX_PENDING are waiting the oldest is closed."""
    import socket
    monkeypatch.setattr(server, "MAX_PENDING", 64)
    _, service, ports, _ = running
    call(ports["embed"], "GET", "/health", key=None)      # the shared threads exist before counting
    before = threading.active_count()
    held = [socket.create_connection(("127.0.0.1", ports["embed" if n % 2 else "rerank"]), timeout=5)
            for n in range(300)]
    try:
        threading.Event().wait(0.3)
        assert threading.active_count() - before <= 1, "a connection that sent nothing got a thread"
        assert len(service.pending) <= server.MAX_PENDING
        for _ in range(5):                                # newer connections push the oldest idle ones out
            assert call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] == 200
            assert call(ports["rerank"], "GET", "/health", key=None)[0] == 200
    finally:
        for sock in held:
            sock.close()


def test_keyless_pollers_on_keep_alive_cannot_hold_the_slots_of_keyed_calls(running):
    """L3b (the reviewer's scenario): 32 connections polled /health without the key and kept every slot, and a
    keyed embedding got 503 in 15 of 15 tries. Now an answer to a request without the key closes its connection,
    and requests without the key have KEYLESS_SLOTS of their own."""
    import http.client
    _, _, ports, _ = running
    stop, polls = threading.Event(), []

    def poller(port):
        conn = None
        while not stop.is_set():
            try:
                if conn is None:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                resp.read()
                polls.append((resp.status, resp.getheader("Connection")))
                if resp.will_close:
                    conn.close()
                    conn = None
            except (OSError, http.client.HTTPException):
                if conn is not None:
                    conn.close()
                conn = None
            stop.wait(0.05)
        if conn is not None:
            conn.close()
    threads = [threading.Thread(target=poller, args=(ports["embed" if n % 2 else "rerank"],), daemon=True)
               for n in range(server.MAX_CONNECTIONS)]
    for thread in threads:
        thread.start()
    try:
        threading.Event().wait(0.5)
        results = [call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] for _ in range(15)]
        assert results == [200] * 15, results
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
    answered = [p for p in polls if p[0] == 200]
    assert answered and all(header == "close" for _, header in answered)    # keyless: one request per connection


def test_an_answer_without_the_key_closes_its_connection_and_a_keyed_one_keeps_it(running):
    _, _, ports, _ = running
    answer, closed = raw_exchange(ports["embed"], b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n", wait=3)
    assert status_of(answer) == 200 and closed and b"Connection: close" in answer
    answer, closed = raw_exchange(ports["embed"], b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + b"\r\n", wait=1)
    assert status_of(answer) == 200 and not closed                     # keep-alive stays for the key's holder


def test_a_connection_that_does_not_finish_its_request_head_in_time_is_closed(running, monkeypatch):
    import socket
    import time
    monkeypatch.setattr(server, "HEAD_TIMEOUT", 0.6)
    _, _, ports, _ = running
    started = time.monotonic()
    answer, closed = raw_exchange(ports["embed"], b"", wait=5)                       # says nothing at all
    assert closed and answer == b"" and time.monotonic() - started < 3
    sock = socket.create_connection(("127.0.0.1", ports["embed"]), timeout=5)      # drips its head byte by byte:
    started, closed = time.monotonic(), None                                         # each byte well inside any
    try:                                                                             # per-read timeout
        for byte in b"GET /health HTTP/1.1\r\nX-Slow: " + b"a" * 200:
            sock.sendall(bytes([byte]))
            threading.Event().wait(0.05)
        sock.settimeout(3)
        closed = sock.recv(100) == b""
    except OSError:
        closed = True
    finally:
        sock.close()
    assert closed and time.monotonic() - started < 4, "a dripped request head held its connection"


def test_the_log_shows_only_known_routes_and_never_raw_request_bytes(running):
    _, _, ports, lines = running
    raw_exchange(ports["embed"], b"GET /\x1b[31mRED\x1b[0m?q=secret HTTP/1.1\r\nHost: x\r\n\r\n")
    raw_exchange(ports["embed"], b"GET /v1/models\x7f HTTP/1.1\r\nHost: x\r\n\r\n")
    call(ports["embed"], "GET", "/health", key=None)
    for _ in range(200):                  # a request is logged just after its answer is sent: wait for the line
        if any(line.startswith("embed GET /health 200") for line in list(lines)):
            break
        threading.Event().wait(0.02)
    text = "\n".join(lines)
    assert "\x1b" not in text and "RED" not in text and "secret" not in text and "\x7f" not in text
    assert "embed GET <other> 401" in text and "embed GET /health 200" in text


def test_the_too_long_message_is_the_one_the_gateway_reads_as_context_overflow(running, tmp_path):
    """F11: the server said "a text has N tokens; this model serves at most M", which the gateway did not
    recognise; it now says what vLLM says."""
    _, _, ports, _ = running
    with pytest.raises(InferenceError) as err:
        gateway(ports["embed"], "embed", tmp_path).embed(["word " * 3000], purpose="t")
    assert err.value.code == "context_overflow"
    with pytest.raises(InferenceError) as err:
        gateway(ports["rerank"], "rerank", tmp_path).rerank("q", ["word " * 3000], purpose="t")
    assert err.value.code == "context_overflow"


def test_an_unusable_answer_is_not_counted_as_served(running):
    models, service, ports, _ = running
    models.vector = [float("nan"), 1.0, 0.0]
    status, _ = call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})
    assert status == 500 and service.served == 0
    models.vector = None
    assert call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] == 200 and service.served == 1


def test_weights_are_loaded_from_safetensors_only():
    source = (ROOT / "deploy" / "memory" / "server.py").read_text()
    assert source.count("use_safetensors=True") == 2 and "trust_remote_code" not in source


# ---- turns: how long one call can make another wait (F8) -----------------------------------------------------------

class _Tok:
    """A tokenizer stand-in: one token per word, and a record of when it was used."""

    def __init__(self, log):
        self.log = log

    def __call__(self, texts, **kwargs):
        self.log.append(("tokenize", len(texts)))
        return {"input_ids": [[1] * len(t.split()) for t in texts]}


def _models(log):
    from types import SimpleNamespace
    from contextlib import nullcontext
    m = server.Models.__new__(server.Models)
    m.torch, m.device, m.max_len = SimpleNamespace(inference_mode=nullcontext), "cpu", 2048
    m.scheduling()
    return m, _Tok(log)


def test_a_small_call_waits_at_most_one_forward_pass_of_a_large_call_of_the_same_model():
    log, gates = [], {}
    models, tok = _models(log)

    def compute(tag):
        def run(ids, group):
            log.append((tag, len(group)))
            gate = gates.get((tag, len([e for e in log if e[0] == tag])))
            if gate is not None:
                assert gate.wait(10)
            return [0.0] * len(group)
        return run
    gates[("large", 1)] = threading.Event()
    large = threading.Thread(target=models._run, args=("embed", tok, ["w"] * (3 * server.BATCH_SEQUENCES),
                                                        compute("large")))
    large.start()
    for _ in range(200):
        if ("large", server.BATCH_SEQUENCES) in log:
            break
        threading.Event().wait(0.01)
    small = threading.Thread(target=models._run, args=("embed", tok, ["w"], compute("small")))
    small.start()
    for _ in range(100):                                  # the small call has tokenised and taken its turn
        if log.count(("tokenize", 1)) == 1:
            break
        threading.Event().wait(0.01)
    threading.Event().wait(0.1)
    gates[("large", 1)].set()
    large.join(10)
    small.join(10)
    passes = [e for e in log if e[0] != "tokenize"]
    assert passes == [("large", 16), ("small", 1), ("large", 16), ("large", 16)]
    assert log.index(("tokenize", 1)) < log.index(("small", 1))     # tokenising never waited for the device


def test_a_call_of_the_other_model_waits_at_most_one_forward_pass_too():
    """Two models side by side on one GPU slowed a waiting lookup to 1.5 s (measured), so both share one turn per
    forward pass: a rerank call arriving during a large embedding runs after the pass in progress, before the
    rest of the large call."""
    log, gates = [], {}
    models, tok = _models(log)

    def compute(tag):
        def run(ids, group):
            log.append((tag, len(group)))
            gate = gates.get((tag, len([e for e in log if e[0] == tag])))
            if gate is not None:
                assert gate.wait(10)
            return [0.0] * len(group)
        return run
    gates[("large", 1)] = threading.Event()
    large = threading.Thread(target=models._run, args=("embed", tok, ["w"] * (2 * server.BATCH_SEQUENCES),
                                                        compute("large")))
    large.start()
    for _ in range(200):
        if ("large", server.BATCH_SEQUENCES) in log:
            break
        threading.Event().wait(0.01)
    other = threading.Thread(target=models._run, args=("rerank", tok, ["w", "w w"], compute("rerank")))
    other.start()
    threading.Event().wait(0.2)
    assert ("rerank", 2) not in log                       # it waits for the pass in progress
    gates[("large", 1)].set()
    large.join(10)
    other.join(10)
    assert [e for e in log if e[0] != "tokenize"] == [("large", 16), ("rerank", 2), ("large", 16)]


def test_tokenising_a_large_call_never_holds_the_tokenizer_for_long():
    log = []
    models, tok = _models(log)
    models._run("embed", tok, ["w"] * 40, lambda ids, group: [0.0] * len(group))
    assert [e for e in log if e[0] == "tokenize"] == [("tokenize", 16), ("tokenize", 16), ("tokenize", 8)]


def test_a_request_is_refused_before_any_forward_pass_when_too_large():
    log = []
    models, tok = _models(log)
    with pytest.raises(server.Refused) as err:
        models._run("embed", tok, ["w " * 2049], lambda ids, group: [0.0] * len(group))
    assert err.value.code == "context_length_exceeded" and "maximum context length is 2048 tokens" in err.value.message
    with pytest.raises(server.Refused) as err:
        models._run("embed", tok, ["w " * 2000] * (server.MAX_REQUEST_TOKENS // 2000 + 1),
                    lambda ids, group: [0.0] * len(group))
    assert err.value.code == "request_too_large"
    assert [e for e in log if e[0] != "tokenize"] == []


# ---- remaining-items round: kept connections hold no slot; no network for the server (ENGINEERING_PLAN §6.7) -------

def _request_threads():
    return [t for t in threading.enumerate() if t.name == "nsf-memory-request" and t.is_alive()]


def _read_one_answer(sock) -> bytes:
    """One whole response from a raw socket (head and the body its Content-Length announces)."""
    got = b""
    while b"\r\n\r\n" not in got:
        got += sock.recv(65536)
    head, _, body = got.partition(b"\r\n\r\n")
    length = int([line.split(b":", 1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length")][0])
    while len(body) < length:
        body += sock.recv(65536)
    return head + b"\r\n\r\n" + body


def test_kept_alive_keyed_connections_hold_no_slot_between_their_requests(running):
    """§6.7: a client that kept 32 connections alive with the key held every keyed slot, each until the deadline of
    its next request's head (10 s), and every other keyed call was answered 503. A kept connection now goes back to
    the door after each answer: no slot, no thread, until the head of its next request is whole."""
    _, service, ports, _ = running
    kept = []
    try:
        for n in range(server.MAX_CONNECTIONS):
            conn = http.client.HTTPConnection("127.0.0.1", ports["embed" if n % 2 else "rerank"], timeout=10)
            conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {KEY}"})
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 200 and not resp.will_close
            kept.append(conn)
        for _ in range(100):
            if not _request_threads():
                break
            threading.Event().wait(0.02)
        assert _request_threads() == []                                      # no thread between two requests
        results = [call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] for _ in range(15)]
        assert results == [200] * 15, results
        for conn in kept:                                                   # and every kept one still serves
            conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {KEY}"})
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 200
    finally:
        for conn in kept:
            conn.close()


def test_a_kept_connection_with_half_of_its_next_head_holds_no_slot_and_is_closed_at_the_deadline(running,
                                                                                                 monkeypatch):
    import socket
    _, _, ports, _ = running
    monkeypatch.setattr(server, "HEAD_TIMEOUT", 0.6)
    held = []
    try:
        for _ in range(server.MAX_CONNECTIONS):
            sock = socket.create_connection(("127.0.0.1", ports["embed"]), timeout=5)
            sock.sendall(b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + b"\r\n")
            assert status_of(_read_one_answer(sock)) == 200
            sock.sendall(b"GET /v1/models HTTP/1.1\r\nHo")                 # half of the next head, never the rest
            held.append(sock)
        assert call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] == 200
        threading.Event().wait(1.5)
        for sock in held:                                                    # closed by the door's deadline
            sock.settimeout(2)
            assert sock.recv(65536) == b""
    finally:
        for sock in held:
            sock.close()


def test_pipelined_requests_on_one_connection_are_all_answered_in_order(running):
    """What a client sent beyond one request is handed to the door with the connection, not lost in a buffer."""
    models, _, ports, _ = running
    body = b'{"input":["abc"]}'
    post = (b"POST /v1/embeddings HTTP/1.1\r\nHost: x\r\n" + AUTH + b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    get = b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + b"\r\n"
    last = b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + b"Connection: close\r\n\r\n"
    answer, closed = raw_exchange(ports["embed"], post + get + post + last, wait=5)
    statuses = [int(part.split(b" ", 1)[0]) for part in answer.split(b"HTTP/1.1 ")[1:]]
    assert statuses == [200, 200, 200, 200] and closed
    assert answer.count(b'"embedding":[') == 2 and answer.count(b'"max_model_len"') == 2
    assert models.seen == [("embed", ["abc"]), ("embed", ["abc"])]


def _unix_request(path, data: bytes, wait=5.0) -> bytes:
    import socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(wait)
    try:
        sock.connect(str(path))
        sock.sendall(data)
        got = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                return got
            if not chunk:
                return got
            got += chunk
    finally:
        sock.close()


def _load_forward():
    spec = importlib.util.spec_from_file_location("nsf_memory_forward", ROOT / "deploy" / "memory" / "forward.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forward = _load_forward()


@pytest.fixture
def unix_running(tmp_path):
    """The server on two Unix sockets (as run.sh starts it in a container without a network), and forward.py
    joining two free loopback ports to them -> (models, service, {"embed": port, "rerank": port}, folder,
    forwarder)."""
    import asyncio
    folder = tmp_path / "sockets"
    folder.mkdir(mode=0o700)
    models, lines = FakeModels(), []
    service = server.Service(models, KEY, NAMES, max_len=models.max_len, log=lines.append)
    stop, ready, box = threading.Event(), threading.Event(), {}

    serving = threading.Thread(target=server.serve, args=(service, None, 0, 0, stop),
                               kwargs={"unix_dir": str(folder)}, daemon=True)
    serving.start()
    for _ in range(500):
        if (folder / "embed.sock").exists() and (folder / "rerank.sock").exists():
            break
        threading.Event().wait(0.01)
    fwd = forward.Forwarder({0: str(folder / "embed.sock")}, "127.0.0.1")
    fwd2 = forward.Forwarder({0: str(folder / "rerank.sock")}, "127.0.0.1")
    loop = asyncio.new_event_loop()

    def run_loop():
        asyncio.set_event_loop(loop)
        box["ports"] = {"embed": loop.run_until_complete(fwd.start())[0],
                        "rerank": loop.run_until_complete(fwd2.start())[0]}
        ready.set()
        loop.run_forever()

    looping = threading.Thread(target=run_loop, daemon=True)
    looping.start()
    assert ready.wait(10)
    try:
        yield models, service, box["ports"], folder, fwd
    finally:
        models.gate.set()
        for one in (fwd, fwd2):
            asyncio.run_coroutine_threadsafe(one.close(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        looping.join(10)
        loop.close()
        stop.set()
        serving.join(10)


def test_the_server_on_unix_sockets_answers_and_keeps_them_the_owners_only(unix_running):
    import stat
    _, _, _, folder, _ = unix_running
    for role in ("embed", "rerank"):
        mode = (folder / f"{role}.sock").lstat().st_mode
        assert stat.S_ISSOCK(mode) and stat.S_IMODE(mode) == 0o600
    answer = _unix_request(folder / "embed.sock", b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH +
                           b"Connection: close\r\n\r\n")
    assert status_of(answer) == 200 and b"qwen3-embedding-0.6b" in answer
    assert status_of(_unix_request(folder / "rerank.sock", b"GET /v1/models HTTP/1.1\r\nHost: x\r\n\r\n")) == 401


def test_a_stale_socket_is_replaced_and_anything_else_at_its_path_is_refused(tmp_path):
    import socket
    service = server.Service(FakeModels(), KEY, NAMES, max_len=16, log=lambda m: None)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(tmp_path / "embed.sock"))
    stale.close()                                         # left behind, as by a server that was killed
    one = server.UnixListener(str(tmp_path / "embed.sock"), service, "embed")
    one.server_close()
    assert not (tmp_path / "embed.sock").exists()         # removed when the server stops
    (tmp_path / "embed.sock").write_text("not a socket")
    with pytest.raises(SystemExit):
        server.UnixListener(str(tmp_path / "embed.sock"), service, "embed")
    assert (tmp_path / "embed.sock").read_text() == "not a socket"
    (tmp_path / "target").write_text("x")
    (tmp_path / "rerank.sock").symlink_to(tmp_path / "target")
    with pytest.raises(SystemExit):
        server.UnixListener(str(tmp_path / "rerank.sock"), service, "rerank")
    assert (tmp_path / "target").read_text() == "x" and (tmp_path / "rerank.sock").is_symlink()
    service.close()


def test_the_gateway_works_through_the_forwarder_as_it_does_on_the_ports(unix_running, tmp_path):
    _, _, ports, _, fwd = unix_running
    embedder = gateway(ports["embed"], "embed", tmp_path)
    vectors = embedder.embed(["one", "three"], purpose="t")
    assert vectors == [[3.0, 1.0, 0.0], [5.0, 1.0, 0.0]]
    reranker = gateway(ports["rerank"], "rerank", tmp_path)
    assert len(reranker.rerank("q", ["a", "bb"], purpose="t")) == 2
    assert call(ports["embed"], "GET", "/health", key=None)[0] == 200
    assert call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]}, key="nsfm-wrong-key-0123456789")[0] == 401
    for _ in range(100):                                  # every joined connection is let go
        if fwd.open == 0:
            break
        threading.Event().wait(0.02)
    assert fwd.open == 0


def test_through_the_forwarder_a_refused_client_reads_its_503_and_a_kept_one_keeps_its_connection(unix_running):
    """The server's half close (503, then reading the rest of a large body) and keep-alive pass through."""
    _, service, ports, _, fwd = unix_running
    for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
        assert service.keyed.acquire(timeout=5)
    try:
        for size in (10, 200_000, server.MAX_BODY - 64):
            body = b'{"input":["' + b"a" * size + b'"]}'
            status, answer = call(ports["embed"], "POST", "/v1/embeddings", raw=body)
            assert status == 503 and answer["error"]["code"] == "overloaded", size
    finally:
        for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
            service.keyed.release()
    conn = http.client.HTTPConnection("127.0.0.1", ports["embed"], timeout=10)
    try:
        for _ in range(3):
            conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {KEY}"})
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 200 and not resp.will_close
    finally:
        conn.close()
    for _ in range(200):
        if fwd.open == 0:
            break
        threading.Event().wait(0.05)
    assert fwd.open == 0


def test_the_forwarder_ends_a_connection_when_the_server_is_down_and_beyond_its_cap(tmp_path):
    import asyncio
    import socket

    async def nobody_behind():
        fwd = forward.Forwarder({0: str(tmp_path / "missing.sock")}, "127.0.0.1")
        port = (await fwd.start())[0]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        try:
            got = await asyncio.wait_for(reader.read(), 5)         # the connection just ends (or is reset:
        except ConnectionResetError:                               # what was sent is never read)
            got = b""
        writer.close()
        await fwd.close()
        return got, fwd.open

    assert asyncio.run(nobody_behind()) == (b"", 0)

    async def capped():
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(tmp_path / "slow.sock"))
        listener.listen(8)
        fwd = forward.Forwarder({0: str(tmp_path / "slow.sock")}, "127.0.0.1", max_connections=1)
        port = (await fwd.start())[0]
        first = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        second_r, second_w = await asyncio.open_connection("127.0.0.1", port)
        try:
            ended = await asyncio.wait_for(second_r.read(), 5)
        except ConnectionResetError:
            ended = b""
        open_then = fwd.open
        first[1].close()
        second_w.close()
        listener.close()                       # its waiting connection ends: the forwarder lets the first one go
        await fwd.close()
        return ended, open_then

    assert asyncio.run(capped()) == (b"", 1)


def test_the_forwarder_listens_on_loopback_only_outside_a_container_and_checks_its_routes():
    assert forward.check_bind("127.0.0.1", False) == "127.0.0.1"
    with pytest.raises(SystemExit):
        forward.check_bind("0.0.0.0", False)
    assert forward.check_bind("0.0.0.0", True) == "0.0.0.0"
    for bad in (["8000"], ["8000=relative.sock"], ["0=/x.sock"], ["70000=/x.sock"], []):
        with pytest.raises(SystemExit):
            forward.parse_routes(bad)
    assert forward.parse_routes(["8000=/run/a.sock", "8001=/run/b.sock"]) == {8000: "/run/a.sock",
                                                                               8001: "/run/b.sock"}


def test_clients_the_server_is_done_with_never_hold_the_forwarders_room(unix_running):
    """A client that asks without the key, is answered and closed by the server, and then neither reads nor closes
    held a joined connection for seconds; enough of them filled the forwarder, and it closed every new connection,
    keyed calls included (the server reached directly closes them and holds nothing). Such a connection is now only
    drained, and a newcomer closes the oldest one drained when the forwarder is full."""
    import asyncio
    _, _, _, folder, _ = unix_running

    async def scenario():
        fwd = forward.Forwarder({0: str(folder / "embed.sock")}, "127.0.0.1", max_connections=8)
        port = (await fwd.start())[0]
        idle = []
        for _ in range(8):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")      # and never reads, never closes
            idle.append(writer)
        for _ in range(250):
            if fwd.draining == 8:
                break
            await asyncio.sleep(0.02)
        draining, full = fwd.draining, fwd.open
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /v1/models HTTP/1.1\r\nHost: x\r\n" + AUTH + b"Connection: close\r\n\r\n")
        try:
            answer = await asyncio.wait_for(reader.read(), 5)
        except ConnectionResetError:
            answer = b""
        writer.close()
        for one in idle:
            one.close()
        await fwd.close()
        return draining, full, answer

    draining, full, answer = asyncio.run(scenario())
    assert draining == 8 and full == 8
    assert status_of(answer) == 200 and b"qwen3-embedding-0.6b" in answer


def test_a_refused_clients_rest_is_drained_by_the_forwarder_within_its_bounds(unix_running, monkeypatch):
    """After the server's 503 the forwarder no longer joins the connection: it reads and discards what the client
    still sends for at most DRAIN_SECONDS, then closes, whatever the client does."""
    import asyncio
    _, service, _, folder, _ = unix_running
    monkeypatch.setattr(forward, "DRAIN_SECONDS", 0.5)
    for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
        assert service.keyed.acquire(timeout=5)

    async def scenario():
        fwd = forward.Forwarder({0: str(folder / "embed.sock")}, "127.0.0.1")
        port = (await fwd.start())[0]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"POST /v1/embeddings HTTP/1.1\r\nHost: x\r\n" + AUTH + b"Content-Length: 1000000\r\n\r\n")
        answer = await asyncio.wait_for(reader.readuntil(b"}}"), 5)       # the 503, while the body never comes
        started = asyncio.get_running_loop().time()
        ended = await asyncio.wait_for(reader.read(), 5)                    # then the forwarder closes
        took = asyncio.get_running_loop().time() - started
        writer.close()
        await fwd.close()
        return answer, ended, took, fwd.open

    try:
        answer, ended, took, still = asyncio.run(scenario())
    finally:
        for _ in range(server.MAX_CONNECTIONS - server.KEYLESS_SLOTS):
            service.keyed.release()
    assert status_of(answer) == 503 and ended == b"" and took < 3 and still == 0
