// Pocket's side of ADR-042 (draft) against vectors the Spark's Python code produced (tests/js/spark_vectors.json, kept
// in sync by tests/webcortex/test_signing.py): the same canonical bytes and fingerprints, Spark-signed replies accepted
// only when untampered and under the pinned key, with WebCrypto Ed25519 and with the fallback verifier used where a
// browser lacks it, and a P-256 node signature in WebCrypto's r||s form.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as k from "../../src/neuroscifi/pocket/web/node-key.mjs";

const v = JSON.parse(readFileSync(new URL("./spark_vectors.json", import.meta.url), "utf8"));
const enc = new TextEncoder(), dec = new TextDecoder();
const r = v.reply, body = enc.encode(r.body);

// canonicalisation: byte for byte what signing.py builds
assert.equal(dec.decode(await k.canonicalRequest("post", v.request.path, enc.encode(v.request.body), v.request.ts, v.request.nonce)), v.request.canonical);
assert.equal(dec.decode(k.canonicalChallenge(v.challenge)), v.challenge.canonical);
assert.equal(await k.fingerprint(k.ED25519, k.unb64(v.ed25519_fingerprint.key)), v.ed25519_fingerprint.fingerprint);
assert.equal(await k.fingerprint(k.P256, k.unb64(v.request.p256.key)), v.request.p256.fingerprint);
assert.deepEqual(k.unb64(k.b64(Uint8Array.of(0, 255, 7))), Uint8Array.of(0, 255, 7));
assert.throws(() => k.unb64("a+b/"));

// Spark-signed replies, WebCrypto Ed25519 path
const genuine = (pin = v.spark, over = {}) => {
  const x = {...r, ...over};
  return k.verifySpark(pin, x.status, x.path, x.nonce, over.bytes ?? body, x.signature);
};
assert.equal(await genuine(), true);
const tampered = enc.encode(r.body.replace("completed", "cancelled"));
assert.equal(await genuine(v.spark, {bytes: tampered}), false, "tampered body");
assert.equal(await genuine(v.spark, {nonce: "another-request-nonce-0"}), false, "reply to another request");
assert.equal(await genuine(v.spark, {path: "/api/research_list"}), false, "another route");
assert.equal(await genuine(v.spark, {status: 409}), false, "another status");
assert.equal(await genuine(v.impostor), false, "pinning mismatch: a valid signature by another key");
assert.equal(await genuine(null), false, "no pin");
assert.equal(await genuine({...v.spark, alg: "ECDSA-P256"}), false, "the pin's algorithm is Ed25519 only");
assert.equal(await genuine(v.spark, {signature: undefined}), false, "unsigned reply");
assert.equal(await genuine(v.spark, {signature: "AAAA"}), false, "short signature");

// The fallback verifier alone, and verifySpark when WebCrypto refuses Ed25519 (older browsers)
const msg = await k.canonicalResponse(r.status, r.path, r.nonce, body);
const pub = k.unb64(v.spark.key), sig = k.unb64(r.signature);
assert.equal(await k.ed25519Verify(pub, sig, msg), true);
assert.equal(await k.ed25519Verify(pub, sig, await k.canonicalResponse(r.status, r.path, r.nonce, tampered)), false);
assert.equal(await k.ed25519Verify(k.unb64(v.impostor.key), sig, msg), false);
const flipped = sig.slice(); flipped[40] ^= 1;
assert.equal(await k.ed25519Verify(pub, flipped, msg), false);
const big = sig.slice(); big.fill(0xff, 32);                           // S >= L is refused (malleability)
assert.equal(await k.ed25519Verify(pub, big, msg), false);
const subtle = crypto.subtle, importKey = subtle.importKey.bind(subtle);
subtle.importKey = (format, key, alg, ...rest) => (alg?.name ?? alg) === "Ed25519"
  ? Promise.reject(new DOMException("Ed25519 unsupported", "NotSupportedError")) : importKey(format, key, alg, ...rest);
try {
  assert.equal(await genuine(), true, "fallback accepts the genuine reply");
  assert.equal(await genuine(v.spark, {bytes: tampered}), false, "fallback rejects a tampered reply");
  assert.equal(await genuine(v.impostor), false, "fallback rejects another key");
} finally { subtle.importKey = importKey; }

// A P-256 node signature from the Python side verifies in WebCrypto: the r||s form and the canonical bytes agree.
const p256 = await subtle.importKey("raw", k.unb64(v.request.p256.key), {name: "ECDSA", namedCurve: "P-256"}, false, ["verify"]);
assert.equal(await subtle.verify({name: "ECDSA", hash: "SHA-256"}, p256, k.unb64(v.request.p256.signature), enc.encode(v.request.canonical)), true);

// Run-time algorithm detection: Ed25519 here; P-256 when generateKey refuses Ed25519.
assert.equal(await k.algorithm(), k.ED25519);
const generateKey = subtle.generateKey.bind(subtle);
subtle.generateKey = (alg, ...rest) => alg?.name === "Ed25519"
  ? Promise.reject(new DOMException("Ed25519 unsupported", "NotSupportedError")) : generateKey(alg, ...rest);
try { assert.equal(await k.algorithm(), k.P256); } finally { subtle.generateKey = generateKey; }

console.log("Passed Pocket signing: canonical bytes and fingerprints match the Spark; Spark-signed replies accepted only untampered, for their own request, route and status, under the pinned key (WebCrypto Ed25519 and the fallback verifier); P-256 r||s interop; algorithm detection.");
