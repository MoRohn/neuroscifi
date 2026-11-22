"""Signed node identities over the real in-process Pocket bridge (ADR-042 draft; RESEARCH_POLICY.md section 6):
the Spark key pinned at pairing signs every research reply; attested enrolment; signed state-changing requests from
nodes, and each refusal (unsigned from a signed node, replay, stale, tampered body, another family, revoked,
enrolled before signing)."""
import json
import stat
import time
from pathlib import Path

import pytest

from neuroscifi.pocket import server as bridge
from neuroscifi.webcortex import nodes, signing
from nodekeys import NodeKey, enrol
from test_pocket_research import QUESTION, Spark, submit


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path)
    yield s
    s.close()


@pytest.fixture
def devices(spark):
    """Phone A paired by invitation (with the Spark pin), tablet B linked from it, C of another family."""
    status, paired = spark.req("pair", {"code": "invite"})
    assert status == 201
    a = paired["key"]
    b = spark.req("pair", {"code": spark.req("link", {}, a)[1]["code"]})[1]["key"]
    spark.pin = paired["spark"]
    return a, b, spark.server.store.pair()


def verifies(spark, pin=None):
    """Whether the last reply's X-NSF-Spark-Signature verifies with the pinned key (as Pocket checks it)."""
    status, path, nonce, body, headers = spark.last
    pin = pin or spark.pin
    sig = headers.get("X-NSF-Spark-Signature")
    return sig is not None and signing.verify(pin["alg"], signing.unb64(pin["key"]), signing.unb64(sig),
                                              signing.canonical_response(status, path, nonce, body))


def test_pairing_pins_the_spark_key_and_every_research_reply_is_signed(spark, devices, tmp_path):
    a, b, _ = devices
    key_file = Path(str(spark.instance) + ".spark-key")
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    own = signing.SparkKey.load_or_create(key_file)
    assert spark.pin == own.pin() and spark.pin["alg"] == "Ed25519"
    linked = spark.req("pair", {"code": spark.req("link", {}, a)[1]["code"]})[1]
    assert linked["spark"] == spark.pin                                   # a linked device pins the same Spark
    task_id = submit(spark, a, QUESTION)[1]["id"]
    assert verifies(spark)
    for route, data in (("research_list", {}), ("research_get", {"id": task_id}), ("research_evidence", {"id": task_id}),
                        ("research_session", {"id": task_id, "action": "status"}), ("node_enrol", {"action": "status"}),
                        ("research_get", {"id": "6a3c0e8e-2d6c-4f7e-9d47-1f0d2c9b8e11"})):     # a 404 is signed too
        spark.req(route, data, b)
        assert verifies(spark), route
    status, path, nonce, body, headers = spark.last
    # tampered body, another request's nonce, another path or status, or a key that is not the pinned one: refused
    for forged in ((status, path, nonce, body.replace(b"{", b"{ ", 1)), (status, path, "other-nonce-12345678", body),
                   (status, "/api/research_list", nonce, body), (200, path, nonce, body)):
        assert not signing.verify("Ed25519", signing.unb64(spark.pin["key"]),
                                  signing.unb64(headers["X-NSF-Spark-Signature"]), signing.canonical_response(*forged))
    impostor = signing.SparkKey.load_or_create(tmp_path / "impostor.spark-key")
    assert not verifies(spark, impostor.pin())                            # pinning mismatch
    # field routes carry no research result and are not signed
    spark.req("state", {}, a)
    assert "X-NSF-Spark-Signature" not in spark.last[4]


@pytest.mark.parametrize("alg", [signing.ED25519, signing.P256])
def test_attested_enrolment_over_http(spark, devices, alg):
    a, _, c = devices
    assert spark.req("node_enrol", {"action": "status"}, a)[1]["state"] == "none"
    status, challenge = spark.req("node_enrol", {"action": "challenge"}, a)
    assert status == 200 and set(challenge) == {"challenge", "family", "node", "expires"}
    assert challenge["expires"] - time.time() <= nodes.CHALLENGE_SECONDS
    key = NodeKey(alg)
    body = key.attestation(challenge)
    assert len(json.dumps(body)) < bridge.BODY_LIMIT                  # P-256's 65-byte key fits the 512-byte bodies
    assert spark.req("node_enrol", body, c) == (409, {"error": "no_challenge"})   # another family's device
    status, out = spark.req("node_enrol", body, a)
    assert status == 200 and out["state"] == "none" and out["pending"] == "attested"
    assert out["pending_alg"] == alg and out["pending_fingerprint"] == key.fingerprint
    assert spark.req("node_enrol", body, a) == (409, {"error": "no_challenge"})   # used once
    with spark.owner() as db:
        listed = nodes.pending(db, time.time())
        assert [(p["alg"], p["fingerprint"], p["pocket_label"]) for p in listed] == \
            [(alg, key.fingerprint, spark.server.store.label(a))]
        nodes.approve(db, key.fingerprint, "phone", time.time(), by="owner:console")
    status, out = spark.req("node_enrol", {"action": "status"}, a)
    assert out == {"node": challenge["node"], "family": challenge["family"], "state": "enrolled", "label": "phone",
                   "alg": alg, "fingerprint": key.fingerprint}
    for bad in ({"action": "attest"}, {"action": "status", "x": 1}, {"action": "fly"}, {}):
        assert spark.req("node_enrol", bad, a)[0] == 400, bad


def test_signed_node_requests_and_their_refusals(spark, devices):
    a, b, c = devices
    key = enrol(spark, a, "phone")
    enrol(spark, b, "tablet")
    task_id = submit(spark, a, QUESTION)[1]["id"]                         # signed by the test client, as Pocket does
    assert spark.req("research_submit", {"objective": QUESTION}, a, sign=False) == (401, {"error": "signature_required"})
    assert verifies(spark)                                                # refusals are signed replies too
    take = {"id": task_id, "action": "take"}
    assert spark.req("research_session", take, a, sign=False) == (401, {"error": "signature_required"})
    assert spark.req("research_session", {"id": task_id, "action": "status"}, a, sign=False)[0] == 200   # a read
    assert spark.req("research_get", {"id": task_id}, a, sign=False)[0] == 200
    raw = json.dumps(take).encode()
    fixed = key.headers("research_session", raw)
    assert spark.req("research_session", take, a, headers=fixed)[1]["owner"] == "phone"
    assert spark.req("research_session", take, a, headers=fixed) == (401, {"error": "replayed_request"})
    old = key.headers("research_session", raw, timestamp=time.time() - 121)
    assert spark.req("research_session", take, a, headers=old) == (401, {"error": "stale_request"})
    ahead = key.headers("research_session", raw, timestamp=time.time() + 121)
    assert spark.req("research_session", take, a, headers=ahead) == (401, {"error": "stale_request"})
    other = key.headers("research_session", json.dumps({"id": task_id, "action": "release"}).encode())
    assert spark.req("research_session", take, a, headers=other) == (401, {"error": "signature_invalid"})  # tampered
    cancel = key.headers("research_cancel", json.dumps({"id": task_id}).encode())
    assert spark.req("research_cancel", {"id": task_id}, b, headers=cancel) == (401, {"error": "signature_invalid"})
    assert spark.req("research_cancel", {"id": task_id}, c, headers=cancel) == (401, {"error": "unknown_node"})
    assert spark.req("research_cancel", {"id": task_id}, c, sign=False) == (404, {})   # another family, unsigned
    assert spark.req("research_cancel", {"id": task_id}, a)[1]["status"] == "cancelled"
    with spark.owner() as db:
        assert nodes.revoke(db, "phone", time.time()) == 1
    assert spark.req("research_continue", {"id": task_id}, a) == (401, {"error": "node_revoked"})
    assert spark.req("node_enrol", {"action": "status"}, a)[1]["state"] == "revoked"


def test_a_node_enrolled_before_signing_is_told_to_re_enrol(spark, devices):
    a, _, _ = devices
    with spark.owner() as db:                                             # enrolled by 0.7.0 software: no key
        nodes.enrol(db, bridge.fingerprint(a), spark.server.store.family(a), "phone", time.time())
    task_id = submit(spark, a, QUESTION)[1]["id"]
    assert spark.req("node_enrol", {"action": "status"}, a)[1]["state"] == "reenrol"
    assert spark.req("research_session", {"id": task_id, "action": "take"}, a) == (409, {"error": "reenrol"})
    enrol(spark, a, "phone")                                              # re-enrolment restores it, signed
    assert spark.req("research_session", {"id": task_id, "action": "take"}, a)[1]["you"] is True
    assert spark.req("research_session", {"id": task_id, "action": "release"}, a, sign=False) == \
        (401, {"error": "signature_required"})


def test_a_small_order_key_neither_attests_nor_forges_a_signed_request(spark, devices):
    """Security review A-4 over HTTP: the identity point as public key with (R = identity, S = 0) as signature was
    attested without any private key, and once approved its credential forged "signed" requests (201)."""
    import secrets
    a, _, _ = devices
    identity = (1).to_bytes(32, "little")
    forged = signing.b64(identity + bytes(32))
    challenge = spark.req("node_enrol", {"action": "challenge"}, a)[1]
    attest = {"action": "attest", "challenge": challenge["challenge"], "alg": "Ed25519",
              "public_key": signing.b64(identity), "signature": forged}
    assert spark.req("node_enrol", attest, a) == (409, {"error": "bad_key"})
    with spark.owner() as db:                       # a row approved before the fix still verifies nothing
        nodes.enrol(db, bridge.fingerprint(a), spark.server.store.family(a), "keyless", time.time(),
                    key=(signing.ED25519, identity))
    body = json.dumps({"objective": QUESTION})
    headers = {"X-NSF-Timestamp": str(int(time.time())), "X-NSF-Nonce": secrets.token_urlsafe(18),
               "X-NSF-Signature": forged}
    assert spark.req("research_submit", raw=body, token=a, headers=headers, sign=False, data=None) == \
        (401, {"error": "signature_invalid"})


def test_a_signed_request_is_bound_to_its_device_and_family_and_a_key_lives_on_one_row(spark, devices):
    """Security review A-3: after a reload and re-pair, the same browser key was enrolled on its old and its new
    device row, both live; a captured signed request then also verified with the other row's credential, in another
    family (201). Approval now revokes the other row, and the signature names its family tag and node id (NSF-REQ-2)."""
    old = devices[0]
    new = spark.server.store.pair()                       # the same browser re-paired: another device and family
    key = NodeKey()
    enrol(spark, old, "phone-old", key=key)
    enrol(spark, new, "phone-new", key=key)               # the same key attested again and approved
    with spark.owner() as db:
        states = {n["label"]: (n["state"], n["fingerprint"]) for n in nodes.listing(db)}
        assert states == {"phone-old": ("revoked", None), "phone-new": ("enrolled", key.fingerprint)}
    body = json.dumps({"objective": QUESTION})
    captured = key.headers("research_submit", body.encode())
    assert spark.req("research_submit", raw=body, token=new, headers=captured, sign=False, data=None)[0] == 201
    assert spark.req("research_submit", raw=body, token=old, headers=captured, sign=False, data=None) == \
        (401, {"error": "node_revoked"})
    # even with both rows live on one key (as before the fix), the captured request does not verify on the other row
    with spark.owner() as db, db:
        db.execute("UPDATE autonomy_nodes SET revoked_at=NULL, alg=?, public_key=?, key_fingerprint=? WHERE label=?",
                   (key.alg, signing.b64(key.raw), key.fingerprint, "phone-old"))
    replay = key.headers("research_submit", body.encode())
    assert spark.req("research_submit", raw=body, token=old, headers=replay, sign=False, data=None) == \
        (401, {"error": "signature_invalid"})
    assert spark.req("research_submit", raw=body, token=new, headers=replay, sign=False, data=None)[0] == 201
    # the old canonical form (NSF-REQ-1: no family, no node) is refused
    ts, nonce = int(time.time()), "legacy-nonce-0123456789"
    legacy = key.sign(b"\n".join([b"NSF-REQ-1", b"POST", b"/api/research_submit",
                                  signing.sha256_hex(body.encode()).encode(), str(ts).encode(), nonce.encode()]))
    assert spark.req("research_submit", raw=body, token=new, sign=False, data=None, headers={
        "X-NSF-Timestamp": str(ts), "X-NSF-Nonce": nonce, "X-NSF-Signature": signing.b64(legacy)}) == \
        (401, {"error": "signature_invalid"})


def test_a_node_enrolled_before_signing_neither_keeps_nor_renews_a_session(spark, devices):
    """Security review A-6: a signed=0 node that owned a session at the upgrade kept cancelling and continuing it
    unsigned, each action renewing its lease to +900 s, so the family's other devices stayed locked out (409)."""
    a, b, _ = devices
    task_id = submit(spark, a, QUESTION)[1]["id"]
    now = time.time()
    with spark.owner() as db:   # enrolled before migration 12 and owning the session at the upgrade
        nodes.enrol(db, bridge.fingerprint(a), spark.server.store.family(a), "legacy", now)
        device = nodes.device_digest(db, bridge.fingerprint(a))
        with nodes.store.write(db):
            db.execute("INSERT INTO autonomy_sessions(session,family,owner,acquired_at,expires_at) VALUES(?,?,?,?,?)",
                       (task_id, nodes.state.family_digest(db, spark.server.store.family(a)), device, now, now + 60))
    assert spark.req("research_session", {"id": task_id, "action": "status"}, b)[1]["owner"] is None
    assert spark.req("research_cancel", {"id": task_id}, a)[0] == 200     # an ordinary family device, unsigned
    with spark.owner() as db:
        assert db.execute("SELECT expires_at FROM autonomy_sessions").fetchone()[0] == now + 60   # not renewed
    assert spark.req("research_continue", {"id": task_id}, b)[0] == 201  # the family is not locked out
