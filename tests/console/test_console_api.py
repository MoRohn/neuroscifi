"""Owner console HTTP API (API_CONTRACTS.md §6): security controls and the owner journeys over real HTTP."""
import http.client
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

from neuroscifi.cli import build_parser
from neuroscifi.console import server
from neuroscifi.instance import store

T0 = 1_800_000_000.0
TOKEN = "test-token-" + "x" * 32
ROOT = Path(__file__).resolve().parents[2]


class Client:
    def __init__(self, srv, path):
        self.srv, self.path, self.port = srv, path, srv.server_port

    def call(self, method, route, body=None, *, token=TOKEN, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = {} if token is None else {"Authorization": f"Bearer {token}"}
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        try:
            conn.request(method, route, body=payload, headers=sent)
            response = conn.getresponse()
            data = response.read()
        finally:
            conn.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = data
        return response.status, response.headers, parsed

    def get(self, route, **kw):
        status, _, data = self.call("GET", route, **kw)
        return status, data

    def post(self, route, body=None, **kw):
        status, _, data = self.call("POST", route, {} if body is None else body, **kw)
        return status, data

    def ok(self, method, route, body=None):
        status, _, data = self.call(method, route, body if body is not None or method == "GET" else {})
        assert status == 200, (route, status, data)
        return data


@pytest.fixture
def console(tmp_path):
    path = tmp_path / "individual.sqlite"
    store.create(path, T0).close()
    clock = [T0 + 10]
    srv = server.ConsoleServer(path, 0, token=TOKEN, clock=lambda: clock[0])
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    client = Client(srv, path)
    client.clock = clock
    yield client
    srv.shutdown()
    srv.server_close()


def observe(c, event_id, channel, action, bit, root, epoch="e1", **extra):
    return c.post("/api/observe", {"id": event_id, "epoch": epoch, "channel": channel, "action": action, "bit": bit,
                                   "root": root, **extra})


def teach_baseline(c):
    assert c.post("/api/epochs", {"name": "e1", "note": "bench"}) == (200, {"epoch": "e1", "created": True})
    for k in range(6):
        assert observe(c, f"c{k}", "camera", 0, 0, "cam1")[0] == 200
        assert observe(c, f"w{k}", "word", 1, 1, "alice")[0] == 200
    for k in range(3):
        assert observe(c, f"r{k}", "reference", 0, 0, f"ref{k}")[0] == 200


# ---- security controls --------------------------------------------------------------------------------

def test_binds_loopback_only_and_has_no_bind_option(console):
    assert console.srv.server_address[0] == "127.0.0.1"
    assert server.BIND == "127.0.0.1"
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["console", "--db", str(console.path), "--bind", "0.0.0.0"])
    args = parser.parse_args(["console", "--db", str(console.path)])
    assert not hasattr(args, "bind") and args.port == 8766


def test_token_is_required_and_compared(console):
    assert console.get("/api/inventory", token=None)[0] == 403
    assert console.get("/api/inventory", token="wrong")[0] == 403
    assert console.get("/api/inventory", token=TOKEN + "x")[0] == 403
    status, _, body = console.call("GET", "/api/inventory", token=None, headers={"Authorization": TOKEN})
    assert status == 403 and "token" in body["error"]
    assert console.post("/api/epochs", {"name": "e1"}, token=None)[0] == 403
    assert console.get("/api/epochs")[0] == 200


def test_host_and_origin_checks(console):
    port = console.port
    for headers in ({"Host": f"evil.example:{port}"}, {"Host": "127.0.0.1"}, {"Host": f"127.0.0.1:{port + 1}"}):
        assert console.call("GET", "/api/inventory", headers=headers)[0] == 403
        assert console.call("GET", "/", token=None, headers=headers)[0] == 403
    assert console.call("GET", "/api/inventory", headers={"Host": f"localhost:{port}"})[0] == 200
    for origin in ("http://evil.example", f"https://127.0.0.1:{port}", f"http://localhost:{port}"):
        assert console.call("POST", "/api/epochs", {"name": "e1"}, headers={"Origin": origin})[0] == 403
    assert console.call("POST", "/api/epochs", {"name": "e1"}, headers={"Origin": f"http://127.0.0.1:{port}"})[0] == 200


def test_body_rules(console):
    big = {"name": "e1", "note": "n" * 5000}
    status, data = console.post("/api/epochs", big)
    assert status == 413 and "4096" in data["error"]
    status, _, data = console.call("POST", "/api/epochs", raw=b'{"name":"e1"}', headers={"Content-Type": "text/plain"})
    assert status == 400 and "application/json" in data["error"]
    assert console.call("POST", "/api/epochs", raw=b"[1,2]", headers={"Content-Type": "application/json"})[0] == 400
    assert console.call("POST", "/api/epochs", raw=b"{nope", headers={"Content-Type": "application/json"})[0] == 400
    assert console.call("POST", "/api/epochs", raw=b"", headers={"Content-Type": "application/json"})[0] == 400
    assert console.call("GET", "/api/epochs", raw=b"{}", headers={"Content-Type": "application/json"})[0] == 400
    status, data = console.post("/api/epochs", {"name": "e1", "colour": "blue"})
    assert status == 400 and "colour" in data["error"]


def test_unknown_route_and_method(console):
    assert console.get("/api/nothing")[0] == 404
    assert console.get("/api/graphs/abc")[0] == 404
    assert console.get("/nothing.js", token=None)[0] == 404
    status, headers, _ = console.call("GET", "/api/observe")
    assert status == 405 and headers["Allow"] == "POST"
    assert console.call("POST", "/api/inventory", {})[0] == 405
    assert console.call("DELETE", "/api/epochs")[0] == 405
    assert console.call("POST", "/", {}, token=None)[0] == 405


def test_static_assets_are_public_and_carry_the_csp(console):
    for route, mime in (("/", "text/html"), ("/console.css", "text/css"), ("/console.js", "text/javascript"),
                        ("/icon.svg", "image/svg+xml")):
        status, headers, _ = console.call("GET", route, token=None)
        assert status == 200 and headers["Content-Type"].startswith(mime), route
        assert headers["Content-Security-Policy"] == server.HEADERS["Content-Security-Policy"]
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
    status, headers, _ = console.call("GET", "/api/inventory")
    assert headers["Content-Security-Policy"] == server.HEADERS["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_no_access_log_and_no_traceback(console, capfd, monkeypatch):
    def broken(db):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(server.store, "inventory", broken)
    status, _, body = console.call("GET", "/api/inventory")
    assert status == 500 and body == {"error": "internal error"}
    console.get("/api/epochs")
    console.get("/api/epochs", token="wrong")
    out, err = capfd.readouterr()
    assert TOKEN not in out + err and "GET" not in err and "secret" not in out + err


# ---- teach journey ---------------------------------------------------------------------------------------

def test_teach_journey_over_http(console):
    c = console
    inventory = c.ok("GET", "/api/inventory")
    assert inventory["birth"]["action_semantics"]["0"] and inventory["regulation"]["writes_allowed"] is True
    teach_baseline(c)
    epochs = c.ok("GET", "/api/epochs")
    assert epochs == [{"name": "e1", "created_at": T0 + 10, "note": "bench", "evidence": 15, "learning": 15,
                       "memory_only": 0, "promoted": 0, "forgotten": 0}]
    explain = c.ok("GET", "/api/epochs/e1/beliefs")
    beliefs = explain["beliefs"]
    assert beliefs["mechanism"]["identifiable"] is True and beliefs["unidentifiable"] == []
    assert set(beliefs["sensors"]) == {"cam1"} and set(beliefs["partners"]) == {"alice"}
    mechanism = explain["factors"][0]
    assert mechanism["scope"] == "mechanism" and mechanism["supporting_total"] == 3
    assert {e["id"] for e in mechanism["supporting"]} == {"r0", "r1", "r2"}
    assert any(r["cause"].startswith("evidence:") for r in explain["revisions"])
    assert c.ok("GET", "/api/epochs/e1/explain") == explain
    preview = c.ok("GET", "/api/epochs/e1/decision")
    assert preview["decision"] == "act"
    recorded = c.ok("POST", "/api/epochs/e1/decide")
    assert recorded["decision"] == "act" and recorded["pending_id"] is None
    # The camera flips silently while the partner keeps its convention.
    for k in range(6):
        observe(c, f"c-flip{k}", "camera", 0, 1, "cam1")
        observe(c, f"w-more{k}", "word", 1, 1, "alice")
    change = c.ok("GET", "/api/epochs/e1/beliefs")["change"]
    assert change["suspected"] and change["scope"] == "camera:cam1" and not change["ambiguous"]
    decision = c.ok("POST", "/api/epochs/e1/decide")
    assert decision["decision"] == "ask" and decision["scope"] == "camera:cam1" and decision["pending_id"]
    pending = c.ok("GET", "/api/pending")
    assert [p["id"] for p in pending] == [decision["pending_id"]] and pending[0]["kind"] == "calibrate"
    assert c.ok("GET", "/api/pending?epoch=e1") == pending
    resolved = c.ok("POST", f"/api/pending/{decision['pending_id']}/resolve", {"resolution": "camera remounted"})
    assert resolved["resolution"] == "camera remounted"
    assert c.ok("GET", "/api/pending") == []
    assert len(c.ok("GET", "/api/pending?all=1")) == 1
    assert c.ok("GET", "/api/inventory")["development"]["teacher_interventions"] == 1
    decisions = c.ok("GET", "/api/memory?kind=decision")["items"]
    assert [d["payload"]["decision"] for d in decisions] == ["ask", "act"]
    rows = c.ok("GET", "/api/epochs/e1/evidence?channel=camera&root=cam1")
    assert len(rows) == 12 and {r["channel"] for r in rows} == {"camera"}
    assert len(c.ok("GET", "/api/epochs/e1/evidence?limit=5")) == 5


def test_duplicates_memory_only_and_promotion(console):
    c = console
    c.post("/api/epochs", {"name": "e1"})
    first = observe(c, "m1", "camera", 0, 0, "cam1", consent="memory_only", sample_id="s1")
    again = observe(c, "m1", "camera", 0, 0, "cam1", consent="memory_only", sample_id="s1")
    assert first[1]["status"] == "created" and again[1]["status"] == "duplicate"
    assert c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]["evidence"]["memory_only"] == 1
    assert c.post("/api/promote", {"id": "m1"})[1]["retention"] == "promoted"
    row = c.ok("GET", "/api/epochs/e1/evidence")[0]
    assert row["retention"] == "promoted" and row["expires_at"] is None
    assert c.ok("GET", "/api/epochs")[0]["promoted"] == 1


def test_error_statuses(console):
    c = console
    c.post("/api/epochs", {"name": "e1"})
    assert observe(c, "x1", "camera", 0, 0, "cam1")[0] == 200
    status, data = observe(c, "x1", "camera", 1, 0, "cam1")
    assert status == 409 and "different observation" in data["error"]
    status, data = c.post("/api/forget", {"kind": "evidence", "target": "x1"})
    assert status == 200 and data["status"] == "forgotten" and data["effects"]["deleted"] == 1
    status, data = observe(c, "x1", "camera", 0, 0, "cam1")
    assert status == 410 and "forgotten" in data["error"]
    assert c.post("/api/promote", {"id": "x1"})[0] == 410
    assert c.get("/api/epochs/nope/beliefs")[0] == 404
    assert c.get("/api/epochs/nope/decision")[0] == 404
    assert c.post("/api/epochs/nope/decide")[0] == 404
    assert c.get("/api/graphs/99")[0] == 404
    assert c.post("/api/pending/99/resolve", {"resolution": "x"})[0] == 404
    assert c.post("/api/checkpoints/99/verify")[0] == 404
    assert observe(c, "x2", "smell", 0, 0, "cam1")[0] == 400
    assert c.post("/api/forget", {"kind": "planet", "target": "x"})[0] == 400
    assert c.post("/api/forget", {"kind": "dataset", "target": "1"})[0] == 400
    assert c.get("/api/memory")[0] == 400
    assert c.get("/api/memory?kind=nothing")[0] == 400
    assert c.get("/api/pending?colour=1")[0] == 400


def test_forget_epoch_and_partner(console):
    c = console
    teach_baseline(c)
    status, data = c.post("/api/forget", {"kind": "partner", "target": "alice"})
    assert status == 200 and data["effects"]["deleted"] == 6
    assert c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]["partners"] == {}
    status, data = c.post("/api/forget", {"kind": "epoch", "target": "e1"})
    assert status == 200 and data["effects"]["deleted"] == 9
    assert c.ok("GET", "/api/epochs")[0]["forgotten"] == 15
    deletions = c.ok("GET", "/api/memory?kind=deletion")["items"]
    assert [d["payload"]["target_kind"] for d in deletions] == ["epoch", "partner"]


def test_every_request_sweeps_expired_evidence_first(console):
    c = console
    c.post("/api/epochs", {"name": "e1"})
    observe(c, "old", "camera", 0, 0, "cam1")
    assert len(c.ok("GET", "/api/epochs/e1/evidence")) == 1
    c.clock[0] += 8 * 86400
    assert c.ok("GET", "/api/epochs/e1/evidence") == []
    assert observe(c, "old", "camera", 0, 0, "cam1")[0] == 410


# ---- graphs and checkpoints ---------------------------------------------------------------------------------

def test_graph_journey_over_http(console):
    c = console
    ids = c.ok("POST", "/api/datasets/simulate", {"context": "world-a", "seed": 1})
    assert set(ids) == {"train", "validation", "heldout"}
    listing = c.ok("GET", "/api/datasets")
    assert [d["split"] for d in listing] == ["train", "validation", "heldout"]
    assert c.ok("GET", "/api/datasets?context=other") == []
    assert c.post("/api/datasets/simulate", {"context": "w", "seed": "1"})[0] == 400
    revision = c.ok("POST", "/api/graphs/learn", {"context": "world-a", "train": ids["train"],
                                                 "validation": ids["validation"]})["revision_id"]
    status, data = c.post("/api/graphs/learn", {"context": "world-a", "train": ids["validation"],
                                                "validation": ids["validation"]})
    assert status == 400 and "expected train" in data["error"]
    evaluation = c.ok("POST", f"/api/graphs/{revision}/evaluate")
    assert evaluation["heldout_mse"] > 0 and [h["dataset_id"] for h in evaluation["heldout"]] == [ids["heldout"]]
    promoted = c.ok("POST", f"/api/graphs/{revision}/promote")
    assert promoted["promoted"] is True and promoted["decision"]["failed"] == []
    assert set(promoted["decision"]["checks"]) == {"independent_heldout", "heldout_gain", "retention", "byte_budget"}
    assert c.post(f"/api/graphs/{revision}/promote")[0] == 409
    shown = c.ok("GET", f"/api/graphs/{revision}")
    assert shown["status"] == "active" and len(shown["matrix"]) == 24 and shown["edges"] == 72
    assert sum(1 for row in shown["matrix"] for w in row if w != 0) == 72
    assert [g["id"] for g in c.ok("GET", "/api/graphs?context=world-a")] == [revision]
    recall = c.ok("POST", "/api/recall", {"dataset_id": ids["heldout"]})
    assert recall["verdict"] == "known:world-a" and recall["choices"][0]["context"] == "world-a"
    assert c.ok("POST", "/api/recall", {"dataset_id": ids["heldout"], "contexts": ["world-a"]})["verdict"] == "known:world-a"
    assert c.post("/api/recall", {"dataset_id": ids["heldout"], "contexts": ["missing"]})[0] == 404
    # A second candidate rejected by the owner, then a dataset forgotten with its cascade.
    second = c.ok("POST", "/api/graphs/learn", {"context": "world-a", "train": ids["train"],
                                               "validation": ids["validation"]})["revision_id"]
    assert c.post(f"/api/graphs/{second}/reject", {})[0] == 400
    rejected = c.ok("POST", f"/api/graphs/{second}/reject", {"reason": "duplicate of the active graph"})
    assert rejected["status"] == "rejected"
    forgotten = c.ok("POST", "/api/forget", {"kind": "dataset", "target": ids["train"]})
    assert revision in forgotten["effects"]["invalidated"]
    assert c.ok("GET", f"/api/graphs/{revision}")["status"] == "invalidated"


def test_checkpoint_create_and_verify(console):
    c = console
    teach_baseline(c)
    assert c.post("/api/checkpoints", {"policy": "bogus"})[0] == 400
    created = c.ok("POST", "/api/checkpoints", {"policy": "fast"})
    path = Path(created["path"])
    assert path.parent == console.path.parent / "checkpoints" and path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and created["status"] == "valid"
    verified = c.ok("POST", f"/api/checkpoints/{created['id']}/verify")
    assert verified["ok"] is True and verified["verified_at"] == c.clock[0]
    assert [k["id"] for k in c.ok("GET", "/api/checkpoints")] == [created["id"]]
    c.post("/api/forget", {"kind": "evidence", "target": "c0"})
    assert c.ok("GET", "/api/checkpoints")[0]["status"] == "stale"


# ---- the command ----------------------------------------------------------------------------------------------

def _run_console(tmp_path, *extra):
    path = tmp_path / "cli.sqlite"
    if not path.exists():
        store.create(path, T0).close()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.Popen([sys.executable, "-m", "neuroscifi.cli", "console", "--db", str(path), "--port", "0", *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def test_url_file_is_private_and_token_stays_off_stdout(tmp_path):
    url_file = tmp_path / "console.url"
    proc = _run_console(tmp_path, "--url-file", str(url_file))
    try:
        deadline = time.monotonic() + 20
        while not (url_file.exists() and url_file.read_text().endswith("\n")):
            assert proc.poll() is None and time.monotonic() < deadline, proc.stderr.read() if proc.poll() else "timeout"
            time.sleep(0.05)
        url = url_file.read_text().strip()
        assert stat.S_IMODE(url_file.stat().st_mode) == 0o600
        assert url.startswith("http://127.0.0.1:") and "/#token=" in url
        token = url.split("#token=", 1)[1]
        assert len(token) >= 43
        port = int(url.split(":")[2].split("/")[0])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/inventory", headers={"Authorization": f"Bearer {token}"})
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err
    assert token not in out and token not in err
    assert str(url_file) in out
    again = _run_console(tmp_path, "--url-file", str(url_file))
    out, err = again.communicate(timeout=20)
    assert again.returncode == 2 and "already exists" in err and token not in out + err


def test_stdout_url_and_missing_instance(tmp_path):
    proc = _run_console(tmp_path)
    try:
        line = proc.stdout.readline()
    finally:
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=20)
    assert "http://127.0.0.1:" in line and "/#token=" in line
    missing = subprocess.run([sys.executable, "-m", "neuroscifi.cli", "console", "--db", str(tmp_path / "none.sqlite")],
                             capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert missing.returncode == 2 and "No instance" in missing.stderr
