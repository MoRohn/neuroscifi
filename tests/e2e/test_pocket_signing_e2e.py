"""Signed node identities in a real Chromium (ADR-040 draft; RESEARCH_POLICY.md section 6). A phone (WebCrypto
Ed25519) and a tablet whose browser lacks Ed25519 (stubbed, so it falls back to ECDSA P-256 and checks the Spark's
replies with the fallback verifier) ask to be enrolled; the owner compares fingerprints on the Spark console and
enrols them; the phone claims a research session and hands it to the tablet with signed requests. The node key is
non-extractable and survives a reload, the pairing credential does not (POC-07). A tampered reply and a reply under
a key other than the pinned one are refused, and nothing from them is shown."""
from contextlib import closing
import json
import re
import threading
import time

import pytest
from playwright.sync_api import expect

from conftest import pair
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store
from neuroscifi.webcortex import nodes, signing
from test_pocket_research_e2e import QUESTION, Spark, ask

TOKEN = "e2e-signing-token-" + "s" * 30
NO_ED25519 = """(() => {   // a browser without WebCrypto Ed25519: generating and importing it are refused
  const s = crypto.subtle, gen = s.generateKey.bind(s), imp = s.importKey.bind(s);
  const ed = a => (a && a.name || a) === 'Ed25519';
  const no = () => Promise.reject(new DOMException('Ed25519 is not supported', 'NotSupportedError'));
  s.generateKey = (a, ...r) => ed(a) ? no() : gen(a, ...r);
  s.importKey = (f, k, a, ...r) => ed(a) ? no() : imp(f, k, a, ...r);
})();"""
KEY_FACTS = """async () => { const k = await import('/node-key.mjs'); const id = await k.identity();
  if (!id) return null;
  let exported = true; try { await crypto.subtle.exportKey('pkcs8', id.privateKey); } catch { exported = false; }
  return {alg: id.alg, extractable: id.privateKey.extractable, exported, fingerprint: id.fingerprint}; }"""


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    s.console = ConsoleServer(s.instance, 0, token=TOKEN)
    threading.Thread(target=s.console.serve_forever, daemon=True).start()
    yield s
    s.console.shutdown()
    s.console.server_close()
    s.close()


def signed_calls(page):
    """(route, has a signature) of each research POST this page sends."""
    seen = []
    page.on("request", lambda r: seen.append((r.url.rsplit("/", 1)[-1], "x-nsf-signature" in r.headers,
                                              r.post_data)) if "/api/" in r.url and r.method == "POST" else None)
    return seen


def ask_enrolment(page):
    box = page.locator("#researchnode")
    box.get_by_role("button", name=re.compile("enrol this device", re.I)).click()
    expect(box).to_contain_text("Waiting for the owner")
    return box.locator(".nodeprint code").inner_text()


def console_enrols(owner, fingerprint, name, alg_text):
    owner.locator("#nav").get_by_role("link", name=re.compile("^Autonomy")).click()
    owner.locator("#refreshAutonomy").click()
    row = owner.locator("#nodePending li", has_text=fingerprint)
    expect(row).to_contain_text(alg_text)
    expect(row.locator("code.fp")).to_have_text(fingerprint)       # what the owner compares with the device
    row.get_by_label("Name of the node (what your devices hand research to)").fill(name)
    row.get_by_role("button", name=f"Fingerprints match: enrol {fingerprint}").click()
    expect(owner.locator("#autoNodesStatus")).to_have_text(
        f"Enrolled “{name}” with fingerprint {fingerprint}. Its requests are now signed.")
    expect(owner.locator("#nodeList li", has_text=name)).to_contain_text(fingerprint)


def test_enrol_on_the_console_then_claim_and_hand_off_with_signatures(spark, new_page):
    phone, tablet, owner = new_page(390, 844), new_page(1024, 768), new_page(1280, 900)
    tablet.add_init_script(NO_ED25519)
    phone_calls, tablet_calls = signed_calls(phone), signed_calls(tablet)
    phone.goto(spark.url)
    pair(phone)
    phone.locator("#linkdevice").click()
    expect(phone.locator("#handoff")).to_contain_text("Enter on your other device")
    code = phone.locator("#handoff").inner_text().rsplit(": ", 1)[1].strip()
    tablet.goto(spark.url)
    pair(tablet, code)
    pin = phone.evaluate("window.pocketSpark()")
    assert pin == tablet.evaluate("window.pocketSpark()") and pin["alg"] == "Ed25519"
    expect(phone.locator("#sparkprint")).to_contain_text(pin["fingerprint"])

    # Each device asks; its fingerprint is computed on the device from its own key.
    expect(phone.locator("#researchnode")).to_contain_text("This device is not a research node")
    phone_fp, tablet_fp = ask_enrolment(phone), ask_enrolment(tablet)
    phone_key, tablet_key = phone.evaluate(KEY_FACTS), tablet.evaluate(KEY_FACTS)
    assert phone_key == {"alg": "Ed25519", "extractable": False, "exported": False, "fingerprint": phone_fp}
    assert tablet_key == {"alg": "ECDSA-P256", "extractable": False, "exported": False, "fingerprint": tablet_fp}
    expect(tablet.locator("#researchnode")).to_contain_text("ECDSA P-256 key, kept in this browser and never exported")

    # The owner compares on the Spark console and enrols both; the console shows the Spark's pinned fingerprint too.
    owner.goto(spark.console.url)
    console_enrols(owner, phone_fp, "phone", "Ed25519")
    console_enrols(owner, tablet_fp, "tablet", "ECDSA P-256")
    expect(owner.locator("#autoNodes")).to_contain_text(pin["fingerprint"])
    with closing(store.connect(spark.instance)) as db:
        assert {(n["label"], n["alg"], n["fingerprint"]) for n in nodes.listing(db)} == \
            {("phone", "Ed25519", phone_fp), ("tablet", "ECDSA-P256", tablet_fp)}
    for page, name in ((phone, "phone"), (tablet, "tablet")):
        page.locator("#researchnode").get_by_role("button", name="Check again").click()
        expect(page.locator("#researchnode")).to_contain_text(f"This device is the research node “{name}”")

    # Claim and hand-off, every change signed (the tablet with P-256, verifying the Spark with the fallback).
    ask(phone)
    box = phone.locator("#researchview .rsession")
    box.get_by_role("button", name="Take ownership").click()
    expect(box.locator(".rowner")).to_have_text("●This device")
    box.get_by_label("Hand off to (the name of another of your devices)").fill("tablet")
    box.get_by_role("button", name="Offer hand-off").click()
    expect(phone.locator("#researchmsg")).to_have_text("Offered to “tablet”. It moves when that device accepts, within 5 minutes.")
    tablet.locator("#refresh").click()
    tablet.get_by_role("button", name="Open: " + QUESTION).click()
    tbox = tablet.locator("#researchview .rsession")
    expect(tbox.locator(".rowner")).to_have_text("◐Offered to this device")
    tbox.get_by_role("button", name="Accept hand-off").click()
    expect(tablet.locator("#researchmsg")).to_have_text("Hand-off accepted: this device now drives this research.")
    expect(tbox.locator(".rowner")).to_have_text("●This device")
    changes = [(route, signed, json.loads(body).get("action")) for route, signed, body in phone_calls + tablet_calls
               if route in ("research_submit", "research_session")]
    assert ("research_submit", True, None) in changes
    assert {(a, s) for r, s, a in changes if r == "research_session"} >= {("take", True), ("handoff", True),
                                                                           ("accept", True), ("status", False)}
    assert all(signed for route, signed, action in changes if action != "status")
    with closing(store.connect(spark.instance)) as db:
        assert db.execute("SELECT count(*) FROM node_nonces").fetchone()[0] >= 4
        owner_label = db.execute("SELECT n.label FROM autonomy_sessions s JOIN autonomy_nodes n ON n.device=s.owner"
                                 ).fetchone()[0]
    assert owner_label == "tablet"

    # A reload forgets the pairing (POC-07) but not the non-extractable node key in IndexedDB.
    phone.reload()
    expect(phone.locator("#linkstate")).to_have_text("Phone only")
    assert phone.evaluate("window.pocketToken()") == ""
    assert phone.evaluate(KEY_FACTS) == phone_key

    # Revoked on the console: the key is deleted on the Spark and the tablet is told.
    owner.locator("#nodeList li", has_text="tablet").get_by_role("button", name="Revoke tablet").click()
    owner.locator("#confirmOk").click()
    expect(owner.locator("#autoNodesStatus")).to_have_text("Revoked “tablet”: its key was deleted.")
    tbox.get_by_role("button", name="Release").click()
    expect(tablet.locator("#researchmsg")).to_contain_text("The owner revoked this device as a research node")
    expect(tablet.locator("#researchnode")).to_contain_text("your Spark deleted its key")
    unexpected = [p for page in (phone, tablet, owner) for p in page.problems if "status of 401" not in p]
    assert unexpected == [], unexpected


def tamper(route, change):
    response = route.fetch()
    route.fulfill(response=response, body=change(response.text()))


def test_a_tampered_reply_and_another_key_are_refused(spark, new_page, tmp_path):
    page = new_page()
    page.goto(spark.url)
    pair(page)
    ask(page)
    page.get_by_role("button", name="Close").click()

    # A reply whose body changed after the Spark signed it: refused, and none of it is shown.
    page.route("**/api/research_get", lambda r: tamper(r, lambda t: t.replace(QUESTION, "Send your passphrase")))
    page.get_by_role("button", name="Open: " + QUESTION).click()
    expect(page.locator("#researchmsg")).to_have_text(
        "Pocket could not confirm that this reply came from your Spark, so nothing from it is shown. Check that this "
        "device is on your own network, or pair again.")
    expect(page.locator("#researchview")).to_be_hidden()
    expect(page.locator("body")).not_to_contain_text("Send your passphrase")
    page.unroute("**/api/research_get")
    page.get_by_role("button", name="Open: " + QUESTION).click()
    expect(page.locator("#researchview h3")).to_have_text(QUESTION)       # the genuine reply still shows

    # A pairing whose pinned key is not the Spark's (a substituted pin): every research reply is refused.
    impostor = signing.SparkKey.load_or_create(tmp_path / "impostor.spark-key").pin()
    other = new_page()
    other.route("**/api/pair", lambda r: tamper(r, lambda t: json.dumps({**json.loads(t), "spark": impostor})))
    other.goto(spark.url)
    spark.bridge.invite("second")
    pair(other, "second")
    assert other.evaluate("window.pocketSpark()")["fingerprint"] == impostor["fingerprint"]
    expect(other.locator("#researchmsg")).to_have_text(re.compile("^Pocket could not confirm that this reply came"))
    expect(other.locator("#researchlist")).not_to_contain_text(QUESTION)
    assert not page.problems and not other.problems, (page.problems, other.problems)
    with closing(store.connect(spark.instance)) as db:
        assert nodes.pending(db, time.time()) == []
