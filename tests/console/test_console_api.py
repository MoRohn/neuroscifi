"""Owner console HTTP API (API_CONTRACTS.md §6): security controls and the owner journeys over real HTTP."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import threading
import time

import pytest

from neuroscifi.cli import build_parser
from neuroscifi.console import server
from neuroscifi.instance import checkpoints, graphs, store

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
                       "memory_only": 0, "promoted": 0, "forgotten": 0, "context": None, "streams": 0}]
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


def test_writes_sweep_expired_evidence_first_and_reads_sweep_once_a_minute(console):
    c = console
    c.post("/api/epochs", {"name": "e1"})
    observe(c, "old", "camera", 0, 0, "cam1")
    assert len(c.ok("GET", "/api/epochs/e1/evidence")) == 1
    c.clock[0] += 8 * 86400
    assert observe(c, "old", "camera", 0, 0, "cam1")[0] == 410  # a POST sweeps first
    assert c.ok("GET", "/api/epochs/e1/evidence") == []
    observe(c, "second", "camera", 0, 0, "cam1")
    c.clock[0] += 8 * 86400
    assert len(c.ok("GET", "/api/epochs/e1/evidence")) == 1  # a GET within a minute of the last sweep does not sweep
    c.srv.last_sweep = float("-inf")
    assert c.ok("GET", "/api/epochs/e1/evidence") == []  # a due GET sweeps


def test_expired_learning_events_stay_as_consolidated_readings_over_http(console):
    """ADR-044: after the lifetime the journal is empty, the beliefs are the same, and the API says what was kept."""
    c = console
    teach_baseline(c)
    before = c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]
    c.clock[0] += 8 * 86400
    c.srv.last_sweep = float("-inf")
    assert c.ok("GET", "/api/epochs/e1/evidence") == []
    after = c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]
    for key in ("mechanism", "sensors", "partners", "reference_roots", "unidentifiable"):
        assert after[key] == before[key]
    assert after["evidence"] == {"total": 15, "learning": 15, "memory_only": 0, "duplicates_ignored": 0,
                                 "consolidated": 15}
    epoch, = c.ok("GET", "/api/epochs")
    assert epoch["evidence"] == 0 and epoch["consolidated"] == 15 and epoch["forgotten"] == 15
    inventory = c.ok("GET", "/api/inventory")
    assert inventory["memory"]["consolidated_readings"] == 15 and inventory["regulation"]["consolidated_readings"] == 15
    kept = c.ok("GET", "/api/memory?kind=consolidated&epoch=e1&channel=word")["items"]
    assert len(kept) == 6 and {i["scope"]["root"] for i in kept} == {"alice"}
    assert set(kept[0]["payload"]) == {"seq", "channel", "reading", "sample_id"}
    gone = c.ok("POST", "/api/forget", {"kind": "evidence", "target": "w0"})     # by its id, though it expired
    assert gone["status"] == "forgotten" and gone["effects"]["consolidated_deleted"] == 1
    assert c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]["partners"]["alice"]["n"] == 5


def _hold_write_lock(path):
    other = sqlite3.connect(path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    return other


def test_get_does_not_wait_for_a_writer_and_post_gets_503(console):
    c = console
    c.srv.post_busy_ms = 300
    holder = _hold_write_lock(c.path)
    try:
        started = time.monotonic()
        assert c.get("/api/inventory")[0] == 200
        assert time.monotonic() - started < 1
        started = time.monotonic()
        status, headers, data = c.call("POST", "/api/epochs", {"name": "busy"})
        assert (status, data, headers["Retry-After"]) == (503, {"error": "instance busy; retry"}, "1")
        assert 0.25 < time.monotonic() - started < 2.5
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert c.post("/api/epochs", {"name": "busy"})[0] == 200


def test_negative_length_and_huge_ids(console):
    c = console
    assert c.call("POST", "/api/epochs", raw=b"", headers={"Content-Type": "application/json",
                                                           "Content-Length": "-5"})[0] == 400
    for route in ("/api/graphs/9223372036854775808", "/api/graphs/99999999999999999999999"):
        assert c.get(route)[0] == 404
    assert c.post("/api/pending/9223372036854775808/resolve", {"resolution": "x"})[0] == 404
    assert c.post("/api/checkpoints/18446744073709551616/verify")[0] == 404
    assert c.get("/api/graphs/9223372036854775807")[0] == 404  # in range: an ordinary missing revision


def test_graph_learn_uses_a_lease_holder_per_request(console):
    c = console
    ids = c.ok("POST", "/api/datasets/simulate", {"context": "world-a", "seed": 1})
    db = store.open_existing(c.path)
    with graphs.lease(db, "world-a", "console", c.clock[0]):
        status, data = c.post("/api/graphs/learn", {"context": "world-a", "train": ids["train"],
                                                   "validation": ids["validation"]})
    db.close()
    assert status == 423 and "leased by console" in data["error"]  # a fixed holder would renew and proceed


def test_connection_errors_print_a_fixed_line_and_a_closed_tab_prints_nothing(console):
    """A handler error prints one fixed line (no address, no traceback). R6: a client that went away (a tab closed or
    reloaded while its presence long-poll waited: BrokenPipeError at the reply; a reset mid-request) printed
    "nsf-console: connection error" five times in normal navigation; it now prints nothing."""
    err = io.StringIO()
    # A valid Host (with the port) so the server blocks reading the body when the RST arrives.
    with contextlib.redirect_stderr(err):
        sock = socket.create_connection(("127.0.0.1", console.port))
        sock.sendall(f"POST /api/epochs HTTP/1.1\r\nHost: 127.0.0.1:{console.port}\r\n".encode() +
                     b"Content-Type: application/json\r\n"
                     b"Authorization: Bearer " + TOKEN.encode() + b"\r\nContent-Length: 50\r\n\r\n{")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        time.sleep(0.5)
        for gone in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(104, "reset")):
            try:
                raise gone
            except OSError:
                console.srv.handle_error(None, ("127.0.0.1", 1))
        assert err.getvalue() == ""
        try:
            raise ValueError("a real handler bug")
        except ValueError:
            console.srv.handle_error(None, ("127.0.0.1", 1))
    text = err.getvalue()
    assert text == "nsf-console: connection error\n"
    assert "127.0.0.1" not in text and "Traceback" not in text


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
    assert set(promoted["decision"]["checks"]) == {"independent_heldout", "heldout_gain", "retention", "byte_budget",
                                                   "lineage_intact"}
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
    assert path.parent == checkpoints.default_dir(console.path) == console.path.parent / "individual.checkpoints"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and created["status"] == "valid"
    verified = c.ok("POST", f"/api/checkpoints/{created['id']}/verify")
    assert verified["ok"] is True and verified["verified_at"] == c.clock[0]
    assert [k["id"] for k in c.ok("GET", "/api/checkpoints")] == [created["id"]]
    c.post("/api/forget", {"kind": "evidence", "target": "c0"})
    assert c.ok("GET", "/api/checkpoints")[0]["status"] == "stale"


def test_checkpoint_prune_and_rebuild(console):
    c = console
    teach_baseline(c)
    assert c.post("/api/checkpoints/prune", {})[0] == 400
    for bad in (0, -1, "2", True, 1.5, None):
        assert c.post("/api/checkpoints/prune", {"keep": bad})[0] == 400, bad
    assert c.post("/api/checkpoints/prune", {"keep": 1, "extra": 1})[0] == 400
    assert c.get("/api/checkpoints/prune")[0] == 405
    first = c.ok("POST", "/api/checkpoints", {"policy": "fast"})
    c.clock[0] += 5
    second = c.ok("POST", "/api/checkpoints", {"policy": "fast"})
    pruned = c.ok("POST", "/api/checkpoints/prune", {"keep": 1})
    assert pruned["kept"] == [second["id"]] and pruned["deleted"] == [first["id"]]
    assert not Path(first["path"]).exists() and Path(second["path"]).exists()
    assert {k["id"]: k["status"] for k in c.ok("GET", "/api/checkpoints")} == {first["id"]: "deleted", second["id"]: "valid"}
    # Forgetting makes the survivor stale; rebuild writes a fresh one, deletes the stale file and prunes.
    c.post("/api/forget", {"kind": "evidence", "target": "c0"})
    for bad in ({"keep": 0}, {"keep": "7"}, {"policy": "bogus"}, {"nope": 1}):
        assert c.post("/api/checkpoints/rebuild", bad)[0] == 400, bad
    c.clock[0] += 5
    rebuilt = c.ok("POST", "/api/checkpoints/rebuild", {"keep": 2, "policy": "fast"})
    assert rebuilt["new_checkpoint"]["status"] == "valid" and rebuilt["deleted"] == [second["id"]]
    assert Path(rebuilt["new_checkpoint"]["path"]).parent == checkpoints.default_dir(c.path)
    assert not Path(second["path"]).exists()
    assert c.ok("POST", "/api/checkpoints/rebuild", {})["new_checkpoint"]["policy"] == "cold"


def flip_camera(c, epoch="e1"):
    for k in range(6):
        observe(c, f"c-flip{k}", "camera", 0, 1, "cam1", epoch=epoch)
        observe(c, f"w-more{k}", "word", 1, 1, "alice", epoch=epoch)


def test_resolve_confirm_relearns_only_that_factor(console):
    c = console
    teach_baseline(c)
    flip_camera(c)
    question = c.ok("POST", "/api/epochs/e1/decide")["pending_id"]
    route = f"/api/pending/{question}/resolve"
    for bad in ("yes", 1, 0, "true", None, []):
        status, data = c.post(route, {"resolution": "x", "confirm": bad})
        assert status == 400 and "confirm must be true or false" in data["error"], bad
    assert c.post(route, {"resolution": "x", "confirmed": True})[0] == 400
    assert c.ok("GET", "/api/pending")[0]["id"] == question
    partner_before = c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]["partners"]["alice"]["p_inverted"]
    resolved = c.ok("POST", route, {"resolution": "Confirmed: camera:cam1 changed", "confirm": True})
    assert resolved["changepoint"]["confirmed"] is True and resolved["changepoint"]["scope"] == "camera:cam1"
    beliefs = c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]
    assert [x["scope"] for x in beliefs["confirmed_changes"]] == ["camera:cam1"]
    assert beliefs["confirmed_changes"][0]["since_seq"] == resolved["changepoint"]["since_seq"]
    assert beliefs["partners"]["alice"]["p_inverted"] == pytest.approx(partner_before, abs=0.05)
    assert not beliefs["sensors"]["cam1"].get("suspected_change")
    assert c.ok("POST", "/api/epochs/e1/decide")["decision"] == "act"
    assert c.ok("POST", route, {"resolution": "Confirmed: camera:cam1 changed", "confirm": True})["changepoint"]["confirmed"]
    assert c.post(route, {"resolution": "different", "confirm": True})[0] == 409


def test_resolve_confirm_false_rejects_the_change(console):
    c = console
    teach_baseline(c)
    flip_camera(c)
    question = c.ok("POST", "/api/epochs/e1/decide")["pending_id"]
    resolved = c.ok("POST", f"/api/pending/{question}/resolve", {"resolution": "Rejected", "confirm": False})
    assert resolved["changepoint"]["confirmed"] is False
    assert c.ok("GET", "/api/epochs/e1/beliefs")["beliefs"]["confirmed_changes"] == []


def test_confirm_on_a_question_that_is_not_about_a_change_is_400(console):
    c = console
    c.ok("POST", "/api/epochs", {"name": "e1"})
    for k in range(6):
        observe(c, f"c{k}", "camera", 0, 0, "cam1")
    decision = c.ok("POST", "/api/epochs/e1/decide")
    assert decision["pending_id"] and decision["decision"] == "test_reference"
    status, data = c.post(f"/api/pending/{decision['pending_id']}/resolve", {"resolution": "x", "confirm": True})
    assert status == 400 and "not about a change" in data["error"]
    assert c.post("/api/pending/99/resolve", {"resolution": "x", "confirm": True})[0] == 404


def original_setup(c, epoch):
    """Readings from e1's mapping in a new epoch: 4 camera, 4 word and 3 reference readings (unseen roots)."""
    for k in range(4):
        observe(c, f"{epoch}-c{k}", "camera", 0, 0, "cam1", epoch=epoch)
        observe(c, f"{epoch}-w{k}", "word", 1, 1, "alice", epoch=epoch)
    for k in range(3):
        observe(c, f"{epoch}-r{k}", "reference", 0, 0, f"{epoch}-ref{k}", epoch=epoch)


def test_epoch_match_and_the_continue_there_question(console):
    c = console
    teach_baseline(c)
    c.ok("POST", "/api/epochs", {"name": "e2"})
    empty = c.ok("GET", "/api/epochs/e2/match")
    assert empty["best"] is None and empty["events"] == 0 and [r["epoch"] for r in empty["ranking"]] == ["e1"]
    original_setup(c, "e2")
    match = c.ok("GET", "/api/epochs/e2/match?last=12")
    assert match["best"] == "e1" and match["ranking"][0]["events"] == 11 and match["best_posterior"] >= .75
    assert match["ranking"][0]["segments"] == {"mechanism": "mechanism@0", "camera:cam1": "cam1@0",
                                               "partner:alice": "alice@0"}
    assert c.ok("GET", "/api/epochs/e2/match") == match
    assert c.get("/api/epochs/nope/match")[0] == 404
    for bad in ("last=0", "last=1001", "last=abc", "last=1.5", "other=1"):
        assert c.get(f"/api/epochs/e2/match?{bad}")[0] == 400, bad
    assert c.post("/api/epochs/e2/match", {})[0] == 405
    decision = c.ok("POST", "/api/epochs/e2/decide")
    assert decision["decision"] == "ask" and decision["scope"] == "epoch:e1"
    question = c.ok("GET", "/api/pending?epoch=e2")[0]
    assert question["question"].startswith("This looks like e1; continue with that calibration?")
    before = c.ok("GET", "/api/epochs/e2/beliefs")["beliefs"]
    assert before["linked_from"] is None and before["sensors"]["cam1"]["n"] == 4
    route = f"/api/pending/{question['id']}/resolve"
    answered = c.ok("POST", route, {"resolution": "Continue with the calibration of epoch e1", "confirm": True})
    assert answered["changepoint"] is None and answered["link"]["source_epoch"] == "e1"
    after = c.ok("GET", "/api/epochs/e2/beliefs")["beliefs"]
    assert after["linked_from"]["epoch"] == "e1" and after["linked_from"]["learning"] == 15
    assert after["sensors"]["cam1"]["n"] == 6 + 4 and after["evidence"]["learning"] == 11
    assert c.ok("POST", route, {"resolution": "Continue with the calibration of epoch e1", "confirm": True})["link"]
    assert c.post(route, {"resolution": "Continue with the calibration of epoch e1", "confirm": False})[0] == 409
    assert c.ok("POST", "/api/epochs/e2/decide")["decision"] == "act"
    assert c.ok("GET", "/api/epochs/e1/match")["ranking"][0]["epoch"] == "e2"
    c.ok("POST", "/api/epochs", {"name": "e3"})
    for k in range(6):   # one camera root alone gains at most a factor 2 over a new setup: never 3:1
        observe(c, f"m-c{k}", "camera", 0, 0, "cam1", epoch="e3")
    single = c.ok("GET", "/api/epochs/e3/match?last=6")
    assert single["best"] is None and all(r["posterior"] < .67 for r in single["ranking"])


def test_no_this_is_new_declines_the_earlier_epoch(console):
    c = console
    teach_baseline(c)
    c.ok("POST", "/api/epochs", {"name": "e2"})
    original_setup(c, "e2")
    decision = c.ok("POST", "/api/epochs/e2/decide")
    assert decision["scope"] == "epoch:e1"
    answered = c.ok("POST", f"/api/pending/{decision['pending_id']}/resolve",
                    {"resolution": "This is a new setup, not epoch e1", "confirm": False})
    assert answered["link"] is None and answered["changepoint"] is None
    assert c.ok("GET", "/api/epochs/e2/match")["best"] == "e1"
    again = c.ok("POST", "/api/epochs/e2/decide")
    assert again["scope"] != "epoch:e1" and again["asks_used"] == 1
    assert c.ok("GET", "/api/epochs/e2/beliefs")["beliefs"]["linked_from"] is None


def test_functional_map(console):
    c = console
    ids = c.ok("POST", "/api/datasets/simulate", {"context": "world-a", "seed": 1})
    assert c.post("/api/graphs/coactivation", {"dataset_id": ids["heldout"]})[0] == 404, "needs an active graph"
    revision = c.ok("POST", "/api/graphs/learn", {"context": "world-a", "train": ids["train"],
                                                 "validation": ids["validation"]})["revision_id"]
    c.ok("POST", f"/api/graphs/{revision}/evaluate")
    c.ok("POST", f"/api/graphs/{revision}/promote")
    mapped = c.ok("POST", "/api/graphs/coactivation", {"dataset_id": ids["heldout"], "lags": [1, 2], "top": 5})
    assert mapped["context"] == "world-a" and mapped["lags"] == [1, 2] and mapped["revision_id"] == revision
    assert len(mapped["routes"]) == 5 and "common stimulus" in mapped["caveat"]
    assert {"src", "dst", "lag", "correlation", "n"} <= set(mapped["routes"][0])
    magnitudes = [abs(r["correlation"]) for r in mapped["routes"]]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert len(c.ok("POST", "/api/graphs/coactivation", {"dataset_id": ids["heldout"]})["routes"]) == 20
    bad_bodies = [{}, {"dataset_id": "1"}, {"dataset_id": True}, {"dataset_id": ids["heldout"], "top": 0},
                  {"dataset_id": ids["heldout"], "top": "5"}, {"dataset_id": ids["heldout"], "lags": []},
                  {"dataset_id": ids["heldout"], "lags": [0]}, {"dataset_id": ids["heldout"], "lags": ["1"]},
                  {"dataset_id": ids["heldout"], "lags": [True]}, {"dataset_id": ids["heldout"], "lags": 1},
                  {"dataset_id": ids["heldout"], "lags": [10_000]}, {"dataset_id": ids["heldout"], "extra": 1}]
    for body in bad_bodies:
        assert c.post("/api/graphs/coactivation", body)[0] == 400, body
    assert c.post("/api/graphs/coactivation", {"dataset_id": 999})[0] == 404
    assert c.get("/api/graphs/coactivation")[0] in (400, 404, 405)


# ---- the command ----------------------------------------------------------------------------------------------

_CONSOLES = []


@pytest.fixture(autouse=True)
def _no_orphan_consoles():
    """Kill any console subprocess a test leaves running, even when the test fails mid-way."""
    yield
    while _CONSOLES:
        proc = _CONSOLES.pop()
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def _run_console(tmp_path, *extra):
    path = tmp_path / "cli.sqlite"
    if not path.exists():
        store.create(path, T0).close()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen([sys.executable, "-m", "neuroscifi.cli", "console", "--db", str(path), "--port", "0", *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    _CONSOLES.append(proc)
    return proc


def _stop(proc, sig=signal.SIGINT, timeout=20):
    """Stop a console subprocess; escalate to kill so a failing test never leaves an orphan server."""
    proc.send_signal(sig)
    try:
        return proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError(f"console did not stop on {sig!r}: {err}")


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
        out, err = _stop(proc)
    assert proc.returncode == 0, err
    assert token not in out and token not in err
    assert str(url_file) in out
    assert not url_file.exists(), "the URL file is removed at shutdown"


def _wait_for_url(proc, url_file, stale=None):
    deadline = time.monotonic() + 20
    while not (url_file.exists() and url_file.read_text().endswith("\n") and url_file.read_text().strip() != stale):
        assert proc.poll() is None and time.monotonic() < deadline, proc.stderr.read() if proc.poll() else "timeout"
        time.sleep(0.05)
    return url_file.read_text().strip()


def test_console_restarts_after_sigterm_and_after_a_crash(tmp_path):
    url_file = tmp_path / "console.url"
    first = _run_console(tmp_path, "--url-file", str(url_file))
    url = _wait_for_url(first, url_file)
    first.send_signal(signal.SIGTERM)
    out, err = first.communicate(timeout=20)
    assert first.returncode == 0, err
    assert not url_file.exists()
    second = _run_console(tmp_path, "--url-file", str(url_file))
    second_url = _wait_for_url(second, url_file)
    assert second_url != url and stat.S_IMODE(url_file.stat().st_mode) == 0o600
    second.kill()  # a crash leaves a stale URL file behind
    second.communicate(timeout=20)
    assert url_file.exists()
    third = _run_console(tmp_path, "--url-file", str(url_file))
    try:
        third_url = _wait_for_url(third, url_file, stale=second_url)
        assert third_url not in (url, second_url)
        assert stat.S_IMODE(url_file.stat().st_mode) == 0o600
    finally:
        third.send_signal(signal.SIGTERM)
        third.communicate(timeout=20)
    assert not url_file.exists()


@pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGQUIT])
def test_url_file_is_removed_on_hangup_and_quit(tmp_path, sig):
    """A closed terminal (SIGHUP) or a quit key (SIGQUIT) must not leave the token file behind."""
    url_file = tmp_path / "console.url"
    proc = _run_console(tmp_path, "--url-file", str(url_file))
    _wait_for_url(proc, url_file)
    out, err = _stop(proc, sig)
    assert proc.returncode == 0, err
    assert not url_file.exists(), f"the URL file survived {sig!r}"


def test_url_file_that_is_not_a_stale_console_url_is_refused(tmp_path):
    other = tmp_path / "precious"
    other.write_text("keep me")
    victim = tmp_path / "victim"
    victim.write_text("http://127.0.0.1:1/#token=stale")
    link = tmp_path / "link"
    link.symlink_to(victim)
    for target in (other, link, tmp_path):
        proc = _run_console(tmp_path, "--url-file", str(target))
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 2 and "never overwrites" in err, (target, err)
    assert other.read_text() == "keep me" and victim.read_text() == "http://127.0.0.1:1/#token=stale"
    assert link.is_symlink() and other.exists() and tmp_path.is_dir()


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
