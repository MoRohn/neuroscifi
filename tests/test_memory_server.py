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
        self.max_len, self.seen, self.fail = max_len, [], None
        self.gate = threading.Event()
        self.gate.set()

    def _tokens(self, texts):
        counts = [len(t.split()) for t in texts]
        if max(counts) > self.max_len:
            raise server.Refused(400, f"a text has {max(counts)} tokens; this model serves at most {self.max_len}",
                                 "context_length_exceeded")
        return sum(counts)

    def embed(self, texts):
        assert self.gate.wait(10)
        if self.fail:
            raise self.fail
        self.seen.append(("embed", list(texts)))
        return [[float(len(t)), 1.0, 0.0] for t in texts], self._tokens(texts)

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
    for _ in range(200):                                              # one runs, the other waits for it
        seen = samples(ports["rerank"])
        if seen["vllm:num_requests_running"] == 1.0 and seen["vllm:num_requests_waiting"] == 1.0:
            break
        threading.Event().wait(0.02)
    assert (seen["vllm:num_requests_running"], seen["vllm:num_requests_waiting"]) == (1.0, 1.0)
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
    models, service, ports, _ = running
    for _ in range(server.MAX_CONNECTIONS):
        assert service.slots.acquire(blocking=False)
    try:
        status, answer = call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})
        assert status == 503 and answer["error"]["code"] == "overloaded" and models.seen == []
        assert call(ports["embed"], "GET", "/health", key=None)[0] == 200      # health and metrics always answer
        assert call(ports["rerank"], "GET", "/metrics", key=None)[0] == 200
    finally:
        for _ in range(server.MAX_CONNECTIONS):
            service.slots.release()
    assert call(ports["embed"], "POST", "/v1/embeddings", {"input": ["a"]})[0] == 200


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
    line = done.stdout
    assert " -p 127.0.0.1:18441:8000 -p 127.0.0.1:18442:8001 " in line and "0.0.0.0:18" not in line
    assert "/api_key:/run/neuroscifi/api_key:ro" in line and "--key-file /run/neuroscifi/api_key" in line
    assert "--env-file" not in line and "API_KEY=" not in line        # the key is in no environment, no inspect
    assert "server.py:/opt/neuroscifi/server.py:ro" in line and "--entrypoint python3" in line
    for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user ", "--pids-limit 256",
                 "--memory 8g", ":ro -v", "@sha256:", "--container --host 0.0.0.0"):
        assert flag in line, flag
    assert f"{NAMES['embed']}:ro" in line and f"{NAMES['rerank']}:ro" in line
    assert not (folder / "api_key").exists()                          # `print` creates nothing
