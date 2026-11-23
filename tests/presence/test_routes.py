"""Presence endpoints over real HTTP: the console long-poll (token, Host/Origin checks kept), Pocket's family-scoped
conditional poll (no other family's activity), Tier C proposals through the inference gateway (fake local server)."""
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from neuroscifi.console import server as console_server
from neuroscifi.instance import store
from neuroscifi.pocket import server as bridge
from neuroscifi.presence import scene_spec
from neuroscifi.webcortex import orchestrator, state

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "inference"))
from fake_openai import FakeOpenAI  # noqa: E402

TOKEN = "presence-token-" + "p" * 30


def call(port, method, route, body=None, *, token=TOKEN, headers=None, timeout=30):
    c = HTTPConnection("127.0.0.1", port, timeout=timeout)
    sent = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    c.request(method, route, json.dumps(body).encode() if body is not None else None, sent)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data) if data else None


@pytest.fixture
def console(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, time.time())) as db:
        state.set_config(db, {"enabled": True}, time.time())
    srv = console_server.ConsoleServer(path, 0, token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.path = path
    yield srv
    srv.shutdown()
    srv.server_close()


def test_console_presence_requires_token_and_trusted_origin(console):
    port = console.server_port
    assert call(port, "GET", "/api/presence", token=None)[0] == 403
    assert call(port, "GET", "/api/presence", token="wrong-" + "x" * 30)[0] == 403
    assert call(port, "GET", "/api/presence", headers={"Origin": "http://evil.example"})[0] == 403
    assert call(port, "GET", "/api/presence", headers={"Host": "attacker.example"})[0] == 403
    assert call(port, "GET", "/api/presence?wait=99")[0] == 400
    assert call(port, "GET", "/api/presence?since=<script>")[0] == 400
    assert call(port, "GET", "/api/presence?bogus=1")[0] == 400
    status, body = call(port, "GET", "/api/presence")
    assert status == 200 and body["schema"] == "nsf.presence.state/1" and body["version"]


def test_console_long_poll_answers_unchanged_then_on_change(console):
    port = console.server_port
    _, first = call(port, "GET", "/api/presence")
    t0 = time.monotonic()
    status, body = call(port, "GET", f"/api/presence?since={first['version']}&wait=1")
    assert status == 200 and body == {"unchanged": True, "version": first["version"], "ts": body["ts"]}
    assert 0.9 <= time.monotonic() - t0 < 3
    result = {}

    def poll():
        result["t"] = time.monotonic()
        result["reply"] = call(port, "GET", f"/api/presence?since={first['version']}&wait=15")
        result["dt"] = time.monotonic() - result["t"]

    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.6)
    with closing(store.connect(console.path)) as db:
        orchestrator.submit(db, "Was the Zephyr compiler version 4.2.0 released?", time.time(), origin="console")
    th.join(20)
    status, body = result["reply"]
    assert status == 200 and body["version"] != first["version"] and body["primary"]["activity"] == "waiting"
    assert result["dt"] < 3   # answered by the change, not by the 15 s timeout


def test_static_presence_assets_carry_the_console_csp(console):
    for route in ("/presence/presence.mjs", "/presence/theme.css", "/presence/theme-boot.js", "/presence/gallery"):
        c = HTTPConnection("127.0.0.1", console.server_port, timeout=10)
        c.request("GET", route)
        r = c.getresponse()
        r.read()
        c.close()
        assert r.status == 200, route
        assert r.headers["Content-Security-Policy"] == console_server.HEADERS["Content-Security-Policy"]


def test_scene_proposal_not_configured_falls_back(console, monkeypatch):
    # the proposal cache is process-wide: an earlier test (e.g. the e2e Tier C run) may have cached this concept
    monkeypatch.setattr(scene_spec, "_results", {})
    status, body = call(console.server_port, "POST", "/api/presence/scene", {"concept": "untangling a timeline"})
    assert status == 200 and body["status"] == "not_configured" and body["fallback"] in scene_spec.CANONICAL
    assert call(console.server_port, "POST", "/api/presence/scene", {"concept": "<svg onload=x>"})[0] == 400
    assert call(console.server_port, "POST", "/api/presence/scene", {"concept": "a", "extra": 1})[0] == 400


GOOD = {"schema": "nsf.presence.scene/1", "concept": "whatever the model says", "body": "present", "eyes": "open",
        "motion": "drift", "elements": [{"primitive": "spiral", "x": 70, "y": 40, "size": 24, "rotation": 0,
                                         "tone": "ink"},
                                        {"primitive": "dot", "x": 86, "y": 70, "size": 6, "rotation": 0,
                                         "tone": "mint"}]}


@pytest.fixture
def fake():
    f = FakeOpenAI(key=None).start()
    yield f
    f.stop()


def gateway_for(fake, tmp_path):
    from neuroscifi.inference import config as iconfig
    from neuroscifi.inference.gateway import Gateway
    return Gateway(iconfig.InferenceConfig(base_url=fake.url))


def test_model_spec_is_validated_and_never_trusted(tmp_path, fake):
    scene_spec._results.clear()
    path = tmp_path / "i.sqlite"
    with closing(store.create(path, time.time())) as db:
        fake.replies.append({"content": json.dumps(GOOD)})
        out = scene_spec.propose(db, path, "untangling a timeline", gateway=gateway_for(fake, tmp_path))
        assert out["status"] == "ok" and out["spec"]["concept"] == "untangling a timeline"   # requester's words
        assert fake.requests[-1]["response_format"]["type"] == "json_schema"
        # markup, out-of-palette colour, out-of-bounds geometry: schema/geometry rejection (after one repair)
        bad = dict(GOOD, elements=[dict(GOOD["elements"][0], tone="#ff0000")])
        fake.replies += [{"content": json.dumps(bad)}, {"content": "<svg><script>alert(1)</script></svg>"}]
        out = scene_spec.propose(db, path, "another idea", gateway=gateway_for(fake, tmp_path))
        assert out["status"] == "unavailable" and out["reason"] == "schema_invalid" and out["fallback"] == "ideation"
        crowded = dict(GOOD, elements=[dict(GOOD["elements"][0]), dict(GOOD["elements"][0], size=26)])
        fake.replies.append({"content": json.dumps(crowded)})
        out = scene_spec.propose(db, path, "stacked shapes", gateway=gateway_for(fake, tmp_path))
        assert out["status"] == "invalid" and "stacked" in out["errors"][0]


def test_validate_spec_rules():
    assert scene_spec.validate_spec(GOOD) == []
    many = dict(GOOD, elements=[dict(GOOD["elements"][1], tone=t, x=44 + 9 * i) for i, t in
                                enumerate(("mint", "pink", "blue", "purple"))])
    errors = scene_spec.validate_spec(many)
    assert any("accent" in e for e in errors)
    assert scene_spec.validate_spec(dict(GOOD, schema="nsf.presence.scene/2"))
    assert scene_spec.nearest("reading the manual") == "reading" and scene_spec.nearest("zzz qqq") == "present"


def test_validate_spec_rejects_non_finite_numbers():
    for bad in (float("nan"), float("inf"), float("-inf")):
        el = dict(GOOD["elements"][0], x=bad)
        assert scene_spec.validate_spec(dict(GOOD, elements=[el, GOOD["elements"][1]])), bad
        el = dict(GOOD["elements"][0], rotation=bad)
        assert scene_spec.validate_spec(dict(GOOD, elements=[el, GOOD["elements"][1]])), bad


def test_model_spec_with_nan_is_not_accepted(tmp_path, fake):
    scene_spec._results.clear()
    path = tmp_path / "i.sqlite"
    with closing(store.create(path, time.time())) as db:
        nan = json.dumps(GOOD).replace('"x": 70', '"x": NaN')
        assert "NaN" in nan
        fake.replies += [{"content": nan}, {"content": nan}]
        out = scene_spec.propose(db, path, "a nan idea", gateway=gateway_for(fake, tmp_path))
        assert out["status"] != "ok"


# ---- Pocket ---------------------------------------------------------------------------------------------------

class Spark:
    def __init__(self, root, research=True):
        self.instance = root / "individual.sqlite"
        with closing(store.create(self.instance, time.time())) as db:
            state.set_config(db, {"enabled": True, "pocket_research": research}, time.time())
        self.server = bridge.PocketServer(("127.0.0.1", 0), bridge.Handler)
        self.server.store = bridge.Store(root / "mobile.sqlite")
        self.server.pair_lock = threading.Lock()
        self.server.invitation, self.server.invitation_deadline = "invite", time.monotonic() + 60
        self.server.instance_db = str(self.instance)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def req(self, route, data=None, token=""):
        return call(self.server.server_port, "POST", "/api/" + route, {} if data is None else data, token=token)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path)
    yield s
    s.close()


def test_pocket_presence_is_family_scoped(spark):
    _, a = spark.req("pair", {"code": "invite"})
    c = spark.server.store.pair()
    assert spark.req("presence", {}, token="")[0] == 403
    assert spark.req("presence", {}, token="not-a-pairing")[0] == 403
    assert spark.req("research_submit", {"objective": "What is the Zephyr compiler?"}, a["key"])[0] == 201
    status, mine = spark.req("presence", {}, a["key"])
    assert status == 200 and mine["scope"] == "family" and mine["primary"]["activity"] == "waiting"
    assert "Zephyr" in mine["primary"]["label"]
    status, other = spark.req("presence", {}, c)
    assert status == 200 and other["activities"] == [] and "Zephyr" not in json.dumps(other)
    assert spark.req("presence", {"since": mine["version"]}, a["key"]) == (200, {"unchanged": True,
                                                                               "version": mine["version"]})
    assert spark.req("presence", {"since": "x" * 600}, a["key"])[0] == 400       # body over 512 bytes / invalid
    assert spark.req("presence", {"other": 1}, a["key"])[0] == 400


def test_pocket_presence_without_shared_research_is_idle(tmp_path):
    s = Spark(tmp_path, research=False)
    try:
        _, a = s.req("pair", {"code": "invite"})
        status, body = s.req("presence", {}, a["key"])
        assert status == 200 and body["primary"]["activity"] == "idle" and body["sources"]["webcortex"] == "off"
    finally:
        s.close()


def test_pocket_serves_presence_modules_and_not_the_gallery(spark):
    for route, want in (("/presence/presence.mjs", 200), ("/presence/pocket-boot.mjs", 200),
                        ("/presence/gallery", 404), ("/presence/console-boot.mjs", 404)):
        c = HTTPConnection("127.0.0.1", spark.server.server_port, timeout=10)
        c.request("GET", route)
        r = c.getresponse()
        r.read()
        c.close()
        assert r.status == want, route
