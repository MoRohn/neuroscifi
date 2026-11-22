"""Test-side research node: the key a Pocket device holds (Ed25519 or ECDSA P-256 as WebCrypto produces them), the
attested enrolment it performs, and the headers of a signed request (webcortex/signing.py, ADR-042 draft)."""
import secrets
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from neuroscifi.webcortex import nodes, signing


class NodeKey:
    def __init__(self, alg=signing.ED25519):
        self.alg = alg
        if alg == signing.ED25519:
            self.private = Ed25519PrivateKey.generate()
            self.raw = self.private.public_key().public_bytes(serialization.Encoding.Raw,
                                                              serialization.PublicFormat.Raw)
        else:
            self.private = ec.generate_private_key(ec.SECP256R1())
            self.raw = self.private.public_key().public_bytes(serialization.Encoding.X962,
                                                              serialization.PublicFormat.UncompressedPoint)
        self.fingerprint = signing.fingerprint(alg, self.raw)
        self.family = self.node = None     # the family tag and node id of the last challenge it signed (attestation)

    def sign(self, message: bytes) -> bytes:
        if self.alg == signing.ED25519:
            return self.private.sign(message)
        r, s = decode_dss_signature(self.private.sign(message, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")    # IEEE P1363, as WebCrypto returns it

    def headers(self, route, body: bytes, *, timestamp=None, nonce=None, family=None, node=None):
        """A signed request for the device this key last attested from (node_enrol status names the same two)."""
        ts = int(time.time()) if timestamp is None else int(timestamp)
        nonce = nonce or secrets.token_urlsafe(18)
        sig = self.sign(signing.canonical_request(family or self.family, node or self.node, "POST", "/api/" + route,
                                                  body, ts, nonce))
        return {"X-NSF-Timestamp": str(ts), "X-NSF-Nonce": nonce, "X-NSF-Signature": signing.b64(sig)}

    def attestation(self, challenge: dict) -> dict:
        self.family, self.node = challenge["family"], challenge["node"]
        message = signing.canonical_challenge(challenge["challenge"], challenge["family"], challenge["node"],
                                              challenge["expires"])
        return {"action": "attest", "challenge": challenge["challenge"], "alg": self.alg,
                "public_key": signing.b64(self.raw), "signature": signing.b64(self.sign(message))}


def enrol(spark, token, label, alg=signing.ED25519, key=None):
    """The full attested enrolment over HTTP, then the owner's approval of the fingerprint the device shows. The
    bridge's `req` signs this device's state-changing requests from then on. `key`: an existing key (a re-paired
    browser keeps its key)."""
    key = key or NodeKey(alg)
    status, challenge = spark.req("node_enrol", {"action": "challenge"}, token)
    assert status == 200, challenge
    status, out = spark.req("node_enrol", key.attestation(challenge), token)
    assert status == 200 and out["pending"] == "attested" and out["pending_fingerprint"] == key.fingerprint, out
    with spark.owner() as db:
        nodes.approve(db, key.fingerprint, label, time.time())
    spark.signers[token] = key
    return key
