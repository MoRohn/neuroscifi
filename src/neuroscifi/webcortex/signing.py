"""Signed node identities, signed requests and Spark-signed results (ADR-040 draft; RESEARCH_POLICY.md section 6).

Three canonical byte strings, UTF-8, fields joined by "\\n", each prefixed with its own tag so a signature over one
can never be replayed as another:

  enrolment challenge  NSF-ENROL-1 / challenge / family tag / node id / expires (integer seconds)
  node request         NSF-REQ-1 / method / path / SHA-256 hex of the body bytes / timestamp (integer s) / nonce
  Spark response       NSF-RES-1 / status / path / the request's nonce / SHA-256 hex of the body bytes

Node keys are Ed25519 (raw 32-byte public key, 64-byte signature) or, where the browser lacks Ed25519, ECDSA P-256
with SHA-256 (uncompressed 65-byte public point, 64-byte r||s signature as WebCrypto produces it). The algorithm is
recorded at enrolment and never taken from a request. The Spark's own key is Ed25519, generated once and kept in a
0600 file outside the repository (next to the instance: `<instance>.spark-key`). Needs the `crypto` extra
(cryptography).
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
import stat

ED25519 = "Ed25519"
P256 = "ECDSA-P256"
ALGORITHMS = (ED25519, P256)
FRESH_SECONDS = 120          # a signed request's timestamp must be within this many seconds of the Spark's clock
NONCE = re.compile(r"[A-Za-z0-9_-]{16,64}")
_KEY_SIZE = {ED25519: 32, P256: 65}


class SignatureError(Exception):
    """A signed request or attestation was refused; `code` is the short error the client gets."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text, limit=128) -> bytes:
    """Strict unpadded base64url (at most `limit` characters); ValueError otherwise."""
    if not isinstance(text, str) or not 0 < len(text) <= limit or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("not base64url")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64(data) != text:
        raise ValueError("not canonical base64url")
    return data


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _join(*fields) -> bytes:
    parts = [str(f) for f in fields]
    if any("\n" in p for p in parts):
        raise ValueError("a canonical field cannot contain a newline")
    return "\n".join(parts).encode("utf-8")


def canonical_challenge(challenge: str, family: str, node: str, expires: int) -> bytes:
    return _join("NSF-ENROL-1", challenge, family, node, int(expires))


def canonical_request(method: str, path: str, body: bytes, timestamp: int, nonce: str) -> bytes:
    return _join("NSF-REQ-1", method.upper(), path, sha256_hex(body), int(timestamp), nonce)


def canonical_response(status: int, path: str, nonce: str, body: bytes) -> bytes:
    return _join("NSF-RES-1", int(status), path, nonce or "", sha256_hex(body))


def fingerprint(alg: str, public_key: bytes) -> str:
    """What the owner compares between console and device: 128 bits of SHA-256 in eight groups of four hex digits."""
    digest = hashlib.sha256(b"NSF-KEY-1\n" + alg.encode("ascii") + b"\n" + public_key).hexdigest().upper()[:32]
    return "-".join(digest[i:i + 4] for i in range(0, 32, 4))


def public_key(alg: str, raw: bytes):
    """A cryptography public key from the raw bytes a browser exports; ValueError for anything else."""
    if alg not in ALGORITHMS or len(raw) != _KEY_SIZE[alg]:
        raise ValueError("unsupported algorithm or key size")
    if alg == ED25519:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        return Ed25519PublicKey.from_public_bytes(raw)
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def verify(alg: str, raw_public_key: bytes, signature: bytes, message: bytes) -> bool:
    """True only for a valid signature by that key under that algorithm (never raises for bad input)."""
    from cryptography.exceptions import InvalidSignature
    try:
        key = public_key(alg, raw_public_key)
        if len(signature) != 64:
            return False
        if alg == ED25519:
            key.verify(signature, message)
        else:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
            r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            key.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---- the Spark's own key -------------------------------------------------------------------------------------------

def key_path(instance_path) -> Path:
    """Where the Spark key of this instance lives unless the operator names another file (--spark-key)."""
    p = Path(instance_path)
    return p.with_name(p.name + ".spark-key")


class SparkKey:
    """The Spark's Ed25519 signing key: generated once, a 0600 file owned by this user, never in git."""

    def __init__(self, private_key):
        from cryptography.hazmat.primitives import serialization
        self._key = private_key
        self.public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.alg = ED25519
        self.fingerprint = fingerprint(ED25519, self.public)

    @classmethod
    def load_or_create(cls, path) -> "SparkKey":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        path = Path(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            # Written in full to a private temporary file, then linked into place: a concurrent reader never sees a
            # partial key, and an existing key (or symlink) is never replaced.
            temp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, pem)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(temp, path)
            except FileExistsError:      # another process created it meanwhile: use that one
                return cls.load_or_create(path)
            finally:
                os.unlink(temp)
            return cls(key)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise PermissionError(f"{path} must be a regular 0600 file owned by this user (the Spark signing key)")
            data = os.read(fd, 4096)
        finally:
            os.close(fd)
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 private key")
        return cls(key)

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)

    def pin(self) -> dict:
        """What a device pins at pairing."""
        return {"alg": self.alg, "key": b64(self.public), "fingerprint": self.fingerprint}
