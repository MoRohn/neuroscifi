"""Signed node identities (ADR-042 draft; webcortex/signing.py, webcortex/nodes.py): canonical forms, both node
algorithms, the Spark key file, attested enrolment, and every refusal of a signed request."""
import os
import stat
import sys
from pathlib import Path

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import nodes, signing
from neuroscifi.webcortex.signing import SignatureError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nodekeys import NodeKey  # noqa: E402

T0 = 1_790_000_000.0
BOTH = [signing.ED25519, signing.P256]


@pytest.fixture
def db(tmp_path):
    con = store.create(tmp_path / "i.sqlite", T0)
    yield con
    con.close()


def test_canonical_forms_are_exact_bytes():
    body = b'{"id":"x"}'
    assert signing.canonical_request("f-1", "n-2", "post", "/api/research_cancel", body, 1700000000, "n" * 16) == (
        b"NSF-REQ-2\nf-1\nn-2\nPOST\n/api/research_cancel\n" + signing.sha256_hex(body).encode() + b"\n1700000000\n"
        + b"n" * 16)
    assert signing.canonical_challenge("c", "f-1", "n-2", 1700000300.9) == b"NSF-ENROL-1\nc\nf-1\nn-2\n1700000300"
    assert signing.canonical_response(200, "/api/research_get", "nonce", b"{}") == (
        b"NSF-RES-1\n200\n/api/research_get\nnonce\n"
        b"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")
    # a field cannot smuggle a line break that would make two different requests sign the same bytes
    with pytest.raises(ValueError):
        signing.canonical_request("f", "n", "POST", "/api/x\n200", body, 1, "n" * 16)
    # the three forms never coincide, whatever their fields
    assert len({signing.canonical_request("f", "n", "POST", "p", b"", 1, "n")[:9], signing.canonical_challenge("c", "f", "n", 1)[:9],
                signing.canonical_response(200, "p", "n", b"")[:9]}) == 3


def test_base64url_is_strict_and_canonical():
    assert signing.unb64(signing.b64(b"\x00\xff" * 5)) == b"\x00\xff" * 5
    for bad in ("", "a+b/", "abc=", "AB", 5, "x" * 200):
        with pytest.raises(ValueError):
            signing.unb64(bad)


@pytest.mark.parametrize("alg", BOTH)
def test_both_node_algorithms_verify_and_reject_tampering(alg):
    key, other = NodeKey(alg), NodeKey(alg)
    msg = signing.canonical_request("f", "n", "POST", "/api/research_submit", b'{"objective":"q"}', 1, "n" * 16)
    sig = key.sign(msg)
    assert len(sig) == 64 and signing.verify(alg, key.raw, sig, msg)
    tampered = signing.canonical_request("f", "n", "POST", "/api/research_submit", b'{"objective":"r"}', 1, "n" * 16)
    assert tampered != msg and not signing.verify(alg, key.raw, sig, tampered)  # tampered body
    assert not signing.verify(alg, other.raw, sig, msg)                          # another key
    assert not signing.verify(alg, key.raw, sig[:-1] + bytes([sig[-1] ^ 1]), msg)
    assert not signing.verify(alg, key.raw, sig + b"\0", msg)
    wrong = signing.P256 if alg == signing.ED25519 else signing.ED25519           # the algorithm is never guessed
    assert not signing.verify(wrong, key.raw, sig, msg)
    assert not signing.verify("RS256", key.raw, sig, msg)
    fp = signing.fingerprint(alg, key.raw)
    assert fp == key.fingerprint and len(fp) == 39 and fp.count("-") == 7 and fp != signing.fingerprint(wrong, key.raw)


def test_the_spark_key_is_created_once_with_mode_0600_and_refused_when_loose(tmp_path):
    path = signing.key_path(tmp_path / "individual.sqlite")
    assert path.name == "individual.sqlite.spark-key"
    first = signing.SparkKey.load_or_create(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and not list(tmp_path.glob(".*.tmp"))
    again = signing.SparkKey.load_or_create(path)
    assert again.fingerprint == first.fingerprint and again.pin() == first.pin()
    msg = signing.canonical_response(200, "/api/research_get", "n", b"{}")
    assert signing.verify(signing.ED25519, first.public, again.sign(msg), msg)
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError):
        signing.SparkKey.load_or_create(path)
    os.chmod(path, 0o600)
    link = tmp_path / "link.spark-key"
    link.symlink_to(path)
    with pytest.raises(OSError):
        signing.SparkKey.load_or_create(link)


def _device(db, n, family="family-fp"):
    return nodes.device_digest(db, f"device-fp-{n}"), nodes.state.family_digest(db, family)


def _enrolled(db, n=1, alg=signing.ED25519, family="family-fp", label="phone", now=T0):
    device, fam = _device(db, n, family)
    key = NodeKey(alg)
    ch = nodes.challenge(db, device, fam, "abc123", now)
    assert ch["node"] == nodes.node_id(device) and ch["family"] == nodes.family_tag(fam)
    assert nodes.attest(db, device, fam, key.attestation(ch), now + 1)["pending_fingerprint"] == key.fingerprint
    assert nodes.node(db, device) is None                      # nothing is a node before the owner approves it
    nodes.approve(db, key.fingerprint, label, now + 2)
    return device, fam, key


def _signed(db, device, fam, key, body=b'{"id":"x"}', *, route="research_cancel", ts=None, nonce=None, now=T0 + 10,
            sent_body=None):
    h = key.headers(route, body, timestamp=now if ts is None else ts, nonce=nonce)
    return nodes.verify_request(db, device, fam, "POST", "/api/" + route, body if sent_body is None else sent_body,
                                h["X-NSF-Timestamp"], h["X-NSF-Nonce"], h["X-NSF-Signature"], now)


@pytest.mark.parametrize("alg", BOTH)
def test_attested_enrolment_then_signed_requests(db, alg):
    device, fam, key = _enrolled(db, alg=alg)
    found = nodes.node(db, device)
    assert found["alg"] == alg and found["key_fingerprint"] == key.fingerprint and found["signed"] is True
    assert nodes.status(db, device, fam, T0 + 3) == {"node": nodes.node_id(device), "family": nodes.family_tag(fam),
                                                     "state": "enrolled", "label": "phone", "alg": alg,
                                                     "fingerprint": key.fingerprint}
    assert [n["state"] for n in nodes.listing(db)] == ["enrolled"] and nodes.pending(db, T0 + 3) == []
    assert _signed(db, device, fam, key)["label"] == "phone"
    assert nodes.requires_signature(db, device)


def test_replay_stale_tampered_and_wrong_family_are_refused(db):
    device, fam, key = _enrolled(db)
    _signed(db, device, fam, key, nonce="nonce-0123456789abcdef")
    with pytest.raises(SignatureError) as refused:                     # same nonce again
        _signed(db, device, fam, key, nonce="nonce-0123456789abcdef", now=T0 + 11)
    assert refused.value.code == "replayed_request"
    for skew, ok in ((-120, True), (120, True), (-121, False), (121, False)):
        try:
            _signed(db, device, fam, key, ts=T0 + 100 + skew, now=T0 + 100)
            assert ok
        except SignatureError as error:
            assert not ok and error.code == "stale_request"
    with pytest.raises(SignatureError) as refused:
        _signed(db, device, fam, key, sent_body=b'{"id":"y"}')
    assert refused.value.code == "signature_invalid"
    with pytest.raises(SignatureError) as refused:
        _signed(db, device, nodes.state.family_digest(db, "other-family"), key)
    assert refused.value.code == "wrong_family"
    with pytest.raises(SignatureError) as refused:
        nodes.verify_request(db, device, fam, "POST", "/api/research_cancel", b"", "12.5", "n" * 16, "AAAA", T0)
    assert refused.value.code == "malformed"
    # the nonce table forgets after twice the freshness window, when the timestamp alone already refuses a replay
    _signed(db, device, fam, key, nonce="late-nonce-0123456789", now=T0 + 1000)
    assert db.execute("SELECT count(*) FROM node_nonces").fetchone()[0] == 1


def test_another_nodes_key_cannot_sign_for_it(db):
    device, fam, _ = _enrolled(db, 1, label="phone")
    _, _, other = _enrolled(db, 2, label="tablet")
    with pytest.raises(SignatureError) as refused:
        _signed(db, device, fam, other)
    assert refused.value.code == "signature_invalid"


def test_revocation_deletes_the_key_and_later_signatures_fail(db):
    device, fam, key = _enrolled(db)
    assert nodes.revoke(db, "phone", T0 + 5) == 1
    row = db.execute("SELECT alg, public_key, key_fingerprint FROM autonomy_nodes").fetchone()
    assert row == (None, None, None)
    with pytest.raises(SignatureError) as refused:
        _signed(db, device, fam, key)
    assert refused.value.code == "node_revoked"
    assert nodes.status(db, device, fam, T0 + 6)["state"] == "revoked" and not nodes.requires_signature(db, device)
    rotated = NodeKey()                                    # key rotation is re-enrolment: a new challenge and approval
    nodes.approve(db, nodes.attest(db, device, fam, rotated.attestation(nodes.challenge(db, device, fam, "abc", T0 + 7)),
                                   T0 + 7)["pending_fingerprint"], "phone", T0 + 8)
    assert _signed(db, device, fam, rotated, now=T0 + 9)["key_fingerprint"] == rotated.fingerprint
    with pytest.raises(SignatureError):
        _signed(db, device, fam, key, now=T0 + 9)


def test_a_node_enrolled_before_signing_must_re_enrol(db):
    nodes.enrol(db, "device-fp-1", "family-fp", "phone", T0)          # the 0.7.0 path: no key
    device, fam = _device(db, 1)
    assert nodes.node(db, device) is None and not nodes.requires_signature(db, device)
    assert nodes.status(db, device, fam, T0)["state"] == "reenrol"
    assert [n["state"] for n in nodes.listing(db)] == ["reenrol"]
    with pytest.raises(PermissionError, match="reenrol"):
        nodes.take(db, "task", device, fam, T0)
    with pytest.raises(SignatureError) as refused:
        _signed(db, device, fam, NodeKey())
    assert refused.value.code == "reenrol"


def test_attestation_is_bound_to_the_challenge_family_and_expiry(db):
    device, fam = _device(db, 1)
    key = NodeKey()
    ch = nodes.challenge(db, device, fam, "abc", T0)
    other_family = nodes.state.family_digest(db, "other-family")
    with pytest.raises(SignatureError) as refused:
        nodes.attest(db, device, other_family, key.attestation(ch), T0 + 1)
    assert refused.value.code == "wrong_family"
    other_device = nodes.device_digest(db, "device-fp-9")
    with pytest.raises(SignatureError) as refused:                     # another device cannot use this challenge
        nodes.attest(db, other_device, fam, key.attestation(ch), T0 + 1)
    assert refused.value.code == "no_challenge"
    forged = key.attestation(dict(ch, node=nodes.node_id(other_device)))     # signed over another node id
    with pytest.raises(SignatureError) as refused:
        nodes.attest(db, device, fam, forged, T0 + 1)
    assert refused.value.code == "signature_invalid"
    with pytest.raises(SignatureError) as refused:
        nodes.attest(db, device, fam, key.attestation(ch), T0 + nodes.CHALLENGE_SECONDS + 1)
    assert refused.value.code == "challenge_expired"
    with pytest.raises(SignatureError) as refused:
        nodes.attest(db, device, fam, dict(key.attestation(ch), alg="RS256"), T0 + 1)
    assert refused.value.code == "bad_key"
    nodes.attest(db, device, fam, key.attestation(ch), T0 + 2)
    with pytest.raises(SignatureError) as refused:                     # a challenge is used once
        nodes.attest(db, device, fam, key.attestation(ch), T0 + 3)
    assert refused.value.code == "no_challenge"
    assert [p["fingerprint"] for p in nodes.pending(db, T0 + 3)] == [key.fingerprint]
    with pytest.raises(nodes.NotFound):                                # approval expires with the pending request
        nodes.approve(db, key.fingerprint, "phone", T0 + 2 + nodes.PENDING_SECONDS + 1)
    assert nodes.reject(db, key.fingerprint, T0 + 4) == 1 and nodes.pending(db, T0 + 4) == []


P25519 = 2 ** 255 - 19
SMALL_ORDER = [bytes(32), (1).to_bytes(32, "little"), (P25519 - 1).to_bytes(32, "little"), P25519.to_bytes(32, "little"),
               (P25519 + 1).to_bytes(32, "little"),
               bytes.fromhex("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05"),
               bytes.fromhex("c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a")]


@pytest.mark.parametrize("raw", SMALL_ORDER + [k[:31] + bytes([k[31] | 0x80]) for k in SMALL_ORDER])
def test_small_order_ed25519_keys_are_refused_at_attestation(db, raw):
    """Security review A-4: under a small-order key the signature (R = identity, S = 0) verifies for any message, so
    the attestation proved no private key. Every encoding of every small-order point is refused (libsodium's list,
    sign bit ignored, non-canonical y = p and p + 1 included); a real key still attests."""
    device, fam = _device(db, 1)
    ch = nodes.challenge(db, device, fam, "abc", T0)
    identity = (1).to_bytes(32, "little")
    forged = {"action": "attest", "challenge": ch["challenge"], "alg": signing.ED25519, "public_key": signing.b64(raw),
              "signature": signing.b64(identity + bytes(32))}
    with pytest.raises(SignatureError) as refused:
        nodes.attest(db, device, fam, forged, T0 + 1)
    assert refused.value.code == "bad_key" and nodes.pending(db, T0 + 1) == []
    assert signing.small_order(raw)
    assert not signing.verify(signing.ED25519, raw, identity + bytes(32), b"any message")
    real = NodeKey()
    assert not signing.small_order(real.raw)
    assert nodes.attest(db, device, fam, real.attestation(ch), T0 + 1)["pending_fingerprint"] == real.fingerprint


def test_the_same_key_attested_by_a_new_pairing_supersedes_the_old_request(db):
    """A reload forgets the pairing (POC-07) but not the key: the re-paired browser asks again with the same key."""
    key = NodeKey()
    for n in (1, 2):
        device, fam = _device(db, n)
        nodes.attest(db, device, fam, key.attestation(nodes.challenge(db, device, fam, f"pocket{n}", T0)), T0)
    assert [p["pocket_label"] for p in nodes.pending(db, T0)] == ["pocket2"]
    assert nodes.approve(db, key.fingerprint, "phone", T0)["device"] == _device(db, 2)[0]


VECTORS = Path(__file__).resolve().parents[1] / "js" / "spark_vectors.json"


def js_vectors():
    """Deterministic vectors for tests/js/signing_check.mjs: the same canonical bytes, fingerprints and a Spark
    signature that Pocket's node-key.mjs must accept (and reject once tampered or under another pin)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    spark = signing.SparkKey(Ed25519PrivateKey.from_private_bytes(bytes(range(32))))
    impostor = signing.SparkKey(Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33))))
    body = '{"id":"6a3c0e8e-2d6c-4f7e-9d47-1f0d2c9b8e11","objective":"Zephyr 4.2 — “released”?","status":"completed"}'
    nonce = "AbCdEfGhIjKlMnOpQr_-01"
    p256 = ec.derive_private_key(0x1234567890ABCDEF, ec.SECP256R1())
    p256_raw = p256.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    request = signing.canonical_request("f-0123456789abcdef", "n-fedcba9876543210", "POST", "/api/research_session",
                                        b'{"id":"x","action":"take"}', 1790000000, nonce)
    r, s = decode_dss_signature(p256.sign(request, ec.ECDSA(hashes.SHA256())))
    challenge = {"challenge": "c" * 43, "family": "f-0123456789abcdef", "node": "n-fedcba9876543210",
                 "expires": 1790000300}
    return {
        "spark": spark.pin(), "impostor": impostor.pin(),
        "reply": {"status": 200, "path": "/api/research_get", "nonce": nonce, "body": body,
                  "signature": signing.b64(spark.sign(signing.canonical_response(200, "/api/research_get", nonce,
                                                                                  body.encode())))},
        "request": {"body": '{"id":"x","action":"take"}', "path": "/api/research_session", "ts": 1790000000,
                    "nonce": nonce, "family": "f-0123456789abcdef", "node": "n-fedcba9876543210",
                    "canonical": request.decode(),
                    "p256": {"key": signing.b64(p256_raw), "signature": signing.b64(r.to_bytes(32, "big") +
                                                                                    s.to_bytes(32, "big")),
                             "fingerprint": signing.fingerprint(signing.P256, p256_raw)}},
        "challenge": dict(challenge, canonical=signing.canonical_challenge(**challenge).decode()),
        "ed25519_fingerprint": {"key": spark.pin()["key"], "fingerprint": spark.fingerprint},
    }


def test_js_vectors_are_what_the_spark_produces():
    """tests/js/spark_vectors.json was written by js_vectors(); ECDSA signatures are randomised, so that one is
    checked by verification rather than by equality. Regenerate with NSF_WRITE_VECTORS=1."""
    import json
    fresh = js_vectors()
    if os.environ.get("NSF_WRITE_VECTORS") == "1":
        VECTORS.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    stored = json.loads(VECTORS.read_text(encoding="utf-8"))
    p256 = stored["request"].pop("p256")
    fresh["request"].pop("p256")
    assert stored == fresh
    assert signing.verify(signing.P256, signing.unb64(p256["key"]), signing.unb64(p256["signature"]),
                          stored["request"]["canonical"].encode())
    assert p256["fingerprint"] == signing.fingerprint(signing.P256, signing.unb64(p256["key"]))
