"""Console autonomy controls (D4) over the real HTTP server: status, the recorded owner switch, limits, ranking."""
from contextlib import closing

from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, state
from test_console_web import T0, start_console, stop_console


def test_autonomy_status_switch_and_limits(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True}, T0)
    db.close()
    c = start_console(path, None, worker=False)
    try:
        status = c.ok("GET", "/api/web/autonomy")
        assert status["switch"] is False and status["enabled"] is False
        assert status["autonomous"]["limits"]["searches"] == 20 and status["candidates"]["chosen"] == []
        out = c.ok("POST", "/api/web/autonomy", {"enabled": True, "autonomous_daily": {"searches": 10}})
        assert out["enabled"] is True and out["record"]["enabled_by"] == "owner:console"
        assert out["autonomous"]["limits"]["searches"] == 10 and out["autonomous"]["remaining"]["searches"] == 10
        code, data = c.post("/api/web/autonomy", {"autonomous_daily": {"searches": -1}})
        assert code == 400
        code, data = c.post("/api/web/autonomy", {"enabled": "yes"})
        assert code == 400
        for raw in (b'{"autonomous_daily": {"searches": Infinity}}', b'{"autonomous_daily": {"searches": 1e400}}',
                    b'{"cooldown": Infinity}'):     # T5-04: OverflowError -> 500
            code, _, data = c.call("POST", "/api/web/autonomy", raw=raw, headers={"Content-Type": "application/json"})
            assert code == 400, (raw, code, data)
        assert c.ok("POST", "/api/web/config", {"autonomous": False})["config"]["autonomous"] is False
        assert c.ok("GET", "/api/web/autonomy")["enabled"] is False          # the switch-off is recorded too
        c.ok("POST", "/api/web/config", {"autonomous": True})
        with closing(store.connect(path)) as db:
            assert autonomy.policy(db)["record"]["enabled_by"] == "owner:console"
    finally:
        stop_console(c)


def test_research_nodes_enrol_by_fingerprint_reject_and_revoke(tmp_path):
    """ADR-040 draft: the console lists attested enrolments with their fingerprint, enrols one under a name, rejects
    another, revokes (deleting the key) and shows the Spark's own fingerprint once the bridge created its key."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nodekeys import NodeKey
    from neuroscifi.webcortex import nodes, signing
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    keys = {}
    for n, alg in ((1, signing.ED25519), (2, signing.P256)):
        device, family = nodes.device_digest(db, f"device-{n}"), state.family_digest(db, "family")
        keys[n] = NodeKey(alg)
        nodes.attest(db, device, family, keys[n].attestation(nodes.challenge(db, device, family, f"pocket{n}", T0)), T0)
    db.close()
    c = start_console(path, None, worker=False)
    try:
        out = c.ok("GET", "/api/web/nodes")
        assert out["nodes"] == [] and out["spark"] is None
        assert [(p["pocket_label"], p["alg"], p["fingerprint"]) for p in out["pending"]] == \
            [("pocket1", "Ed25519", keys[1].fingerprint), ("pocket2", "ECDSA-P256", keys[2].fingerprint)]
        assert c.post("/api/web/nodes", {"action": "enrol", "fingerprint": "0000-0000", "label": "phone"})[0] == 404
        assert c.post("/api/web/nodes", {"action": "enrol", "fingerprint": keys[1].fingerprint})[0] == 400
        assert c.post("/api/web/nodes", {"action": "fly"})[0] == 400
        out = c.ok("POST", "/api/web/nodes", {"action": "enrol", "fingerprint": keys[1].fingerprint.lower(),
                                              "label": "phone"})
        assert [(n["label"], n["state"], n["alg"], n["fingerprint"]) for n in out["nodes"]] == \
            [("phone", "enrolled", "Ed25519", keys[1].fingerprint)]
        out = c.ok("POST", "/api/web/nodes", {"action": "reject", "fingerprint": keys[2].fingerprint})
        assert out["pending"] == []
        out = c.ok("POST", "/api/web/nodes", {"action": "revoke", "label": "phone"})
        assert [(n["state"], n["fingerprint"]) for n in out["nodes"]] == [("revoked", None)]
        assert c.post("/api/web/nodes", {"action": "revoke", "label": "phone"})[0] == 404
        spark = signing.SparkKey.load_or_create(signing.key_path(path))
        assert c.ok("GET", "/api/web/nodes")["spark"]["fingerprint"] == spark.fingerprint
    finally:
        stop_console(c)
