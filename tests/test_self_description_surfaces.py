"""Where the self-description is served (ADR-053): the console's /api/inventory?self=1 and its read-only imagined view,
the Pocket self_status route (signed like every research reply, family scope, invisible while research is off) and
the read-only MCP self_status tool."""
from contextlib import closing
import http.client
import io
import json
import threading

import pytest

from neuroscifi import self_description as sd
from neuroscifi.console import server as console_server
from neuroscifi.instance import imagined, store
from neuroscifi.webcortex import mcp, orchestrator, signing, state
from test_pocket_research import Spark

T0 = 1_800_000_000.0
TOKEN = "self-test-token-" + "z" * 32


@pytest.fixture
def console(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, T0)) as db:
        state.set_config(db, {"enabled": True}, T0)
    srv = console_server.ConsoleServer(path, 0, token=TOKEN, clock=lambda: T0 + 10)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, path
    srv.shutdown()
    srv.server_close()


def get(srv, route):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=30)
    try:
        conn.request("GET", route, headers={"Authorization": f"Bearer {TOKEN}"})
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_inventory_carries_the_record_only_when_asked(console):
    srv, path = console
    status, plain = get(srv, "/api/inventory")
    assert status == 200 and "self" not in plain          # the header's reload never pays for it
    status, inv = get(srv, "/api/inventory?measure=1&self=1")
    assert status == 200 and inv["self"]["schema"] == sd.SCHEMA and inv["self_short"]
    assert inv["self"]["state_version"] == inv["state_version"]
    assert get(srv, "/api/inventory?selfish=1")[0] == 400
    status, quick = get(srv, "/api/resources?quick=1")     # the overview's Resources card: no subprocess
    assert status == 200 and quick["gpu"] is None and quick["containers"] is None and "policy" in quick


def test_the_record_has_its_own_request_and_never_takes_the_overview_down(console, monkeypatch):
    """review1 A1: the record was bundled into the overview's inventory request, so one damaged part failed the header
    and all six overview panels. GET /api/self serves it alone; a record that cannot be built at all is a message."""
    srv, path = console
    status, own = get(srv, "/api/self")
    assert status == 200 and own["self"]["schema"] == sd.SCHEMA and own["self_short"]
    assert own["self"]["state_version"] == own["state_version"]
    assert get(srv, "/api/self?x=1")[0] == 400
    monkeypatch.setattr(sd, "_build", lambda *a, **k: 1 / 0)
    status, inv = get(srv, "/api/inventory?measure=1")                 # what the overview asks now
    assert status == 200 and "self" not in inv and inv["regulation"]
    status, broken = get(srv, "/api/self")
    assert status == 200 and broken["self"] is None and "ZeroDivisionError" in broken["self_error"]
    status, old = get(srv, "/api/inventory?measure=1&self=1")          # an older page's request still works
    assert status == 200 and old["self"] is None and old["regulation"]


def test_imagined_view_is_read_only_and_labelled(console):
    srv, path = console
    with closing(store.connect(path)) as db:
        rid = imagined.record(db, now=T0, **imagined.example(3))
    status, listing = get(srv, "/api/imagined")
    assert status == 200 and listing["label"] == imagined.NOT_EVIDENCE
    assert [x["id"] for x in listing["items"]] == [rid] and listing["items"][0]["label"] == imagined.NOT_EVIDENCE
    assert get(srv, "/api/imagined?limit=0")[0] == 400 and get(srv, "/api/imagined?kind=dream")[0] == 400
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=30)
    conn.request("POST", "/api/imagined", body=b"{}", headers={"Authorization": f"Bearer {TOKEN}",
                                                                "Content-Type": "application/json"})
    assert conn.getresponse().status == 405                  # nothing writes through this view
    conn.close()


def test_mcp_self_status_is_read_only_and_reports_the_package_version(tmp_path):
    path = tmp_path / "individual.sqlite"
    store.create(path, T0).close()
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "self_status", "arguments": {}}}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "self_status", "arguments": {"short": True}}}),
             json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "self_status", "arguments": {"forget": True}}})]
    out = io.StringIO()
    with closing(store.connect(path)) as db:
        before = store.state_version(db)
    mcp.serve(path, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out, stderr=io.StringIO(), clock=lambda: T0)
    got = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}
    tool = next(t for t in got[1]["result"]["tools"] if t["name"] == "self_status")
    assert tool["annotations"]["readOnlyHint"] is True and tool["annotations"]["destructiveHint"] is False
    record = json.loads(got[2]["result"]["content"][0]["text"].split("\n\n", 1)[1])
    assert record["schema"] == sd.SCHEMA and got[2]["result"]["isError"] is False
    assert json.loads(got[3]["result"]["content"][0]["text"].split("\n\n", 1)[1])["short"]
    assert got[4]["result"]["isError"] is True                # unknown arguments refused
    with closing(store.connect(path)) as db:
        assert store.state_version(db) == before


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path)
    yield s
    s.close()


def verifies(spark, pin):
    status, path, nonce, body, headers = spark.last
    sig = headers.get("X-NSF-Spark-Signature")
    return sig is not None and signing.verify(pin["alg"], signing.unb64(pin["key"]), signing.unb64(sig),
                                              signing.canonical_response(status, path, nonce, body))


def test_pocket_self_status_is_signed_family_scoped_and_hidden_while_research_is_off(spark):
    status, paired = spark.req("pair", {"code": "invite"})
    assert status == 201
    a, pin = paired["key"], paired["spark"]
    c = spark.server.store.pair()
    mine = spark.req("research_submit", {"objective": "Is the Okapi compiler 2.0 out yet?"}, a)[1]["id"]
    with spark.owner() as db:
        owner = orchestrator.submit(db, "Where does the okapi live?", T0, origin="console")
        db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(NULL,'ask','web:x','?',?)",
                   (T0,))
    status, body = spark.req("self_status", {}, a)
    assert status == 200 and verifies(spark, pin)
    record = body["record"]
    assert record["scope"] == "family" and body["short"]
    assert [t["id"] for t in sd.value(record, "doing.tasks")["items"]] == [mine]
    assert "questions" not in record["doing"] and "decisions" not in record and "uncertain" not in record
    assert owner["id"] not in json.dumps(record)
    assert spark.req("self_status", {}, c)[1]["record"]["doing"]["tasks"]["value"]["items"] == []
    spark.configure(pocket_recent=True)
    shared = sd.value(spark.req("self_status", {}, a)[1]["record"], "recent")
    assert [x["task_id"] for x in shared][:1] == [owner["id"]] and shared[0]["openable"] is False
    assert spark.req("research_get", {"id": owner["id"]}, a)[0] == 404      # a reference, never the report
    assert spark.req("self_status", {"x": 1}, a)[0] == 400
    assert spark.req("self_status", {})[0] == 403
    spark.configure(pocket_research=False)
    assert spark.req("self_status", {}, a)[0] == 404
