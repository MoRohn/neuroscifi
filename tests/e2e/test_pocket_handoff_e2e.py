"""Pocket research-session ownership in a real Chromium (RESEARCH_POLICY.md section 6): a phone takes a session, offers
it to the linked tablet of its own family, the tablet accepts and then releases it; a hand-off to another family's
node is refused and that family cannot even read the session. The evidence review and the session controls pass axe
in light and dark at 390 and 1280 px."""
from contextlib import closing
import re
import time

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, pair
from neuroscifi.instance import store
from neuroscifi.webcortex import nodes
from test_pocket_research_e2e import QUESTION, Spark, ask


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


def enrol(spark, page, label):
    """Attested enrolment (ADR-040 draft): the device asks, signing the Spark's challenge with its own key; the owner
    compares the fingerprint the device shows and enrols it under `label` (console, or `nsf web node enrol`)."""
    box = page.locator("#researchnode")
    box.get_by_role("button", name=re.compile("enrol this device", re.I)).click()
    expect(box).to_contain_text("Waiting for the owner")
    fingerprint = box.locator(".nodeprint code").inner_text()
    with closing(store.connect(spark.instance)) as db:
        assert [p["fingerprint"] for p in nodes.pending(db, time.time())].count(fingerprint) == 1
        nodes.approve(db, fingerprint, label, time.time(), by="owner:console")
    box.get_by_role("button", name="Check again").click()
    expect(box).to_contain_text(f"This device is the research node “{label}”")
    expect(box.locator(".nodeprint code")).to_have_text(fingerprint)
    return fingerprint


# research_session status, accept and take from a page, signed with that browser's node key when `sign` is true
SESSION_CALLS = """async ([id, sign]) => { const k = await import('/node-key.mjs');
    return Promise.all(['status', 'accept', 'take'].map(async action => {
        const body = JSON.stringify({id, action}), nonce = k.nonce();
        const headers = {'Content-Type': 'application/json', Authorization: 'Bearer ' + window.pocketToken(),
                         'X-NSF-Nonce': nonce};
        if (sign && action !== 'status')
            Object.assign(headers, await k.signedHeaders('/api/research_session', new TextEncoder().encode(body), nonce));
        return (await fetch('/api/research_session', {method: 'POST', headers, body})).status; })); }"""


def session(page):
    return page.locator("#researchview .rsession")


def reopen(page):
    page.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Open: " + QUESTION).first.click()
    expect(session(page).locator(".rsessionstate")).not_to_contain_text("Checking")


def test_handoff_between_two_devices_of_one_family_and_refused_across_families(spark, new_page):
    phone, tablet, stranger = new_page(390, 844), new_page(1024, 768), new_page(390, 844)
    phone.goto(spark.url)
    pair(phone)
    phone.locator("#linkdevice").click()
    expect(phone.locator("#handoff")).to_contain_text("Enter on your other device")
    code = phone.locator("#handoff").inner_text().rsplit(": ", 1)[1].strip()
    tablet.goto(spark.url)
    pair(tablet, code)
    spark.bridge.invite("other-family")
    stranger.goto(spark.url)
    pair(stranger, "other-family")
    for page, label in ((phone, "phone"), (tablet, "tablet"), (stranger, "stranger")):
        enrol(spark, page, label)

    ask(phone)
    box = session(phone)
    expect(box.locator("h4")).to_have_text("Who drives this research")
    expect(box.locator(".rowner")).to_have_text("○Nobody")
    expect(box).to_contain_text("Any of your linked devices can cancel or continue it")
    box.get_by_role("button", name="Take ownership").click()
    expect(phone.locator("#researchmsg")).to_have_text(
        "This device now drives this research. Your other devices can read it but not cancel or continue it.")
    expect(box.locator(".rowner")).to_have_text("●This device")
    expect(box.locator(".rsessionstate")).to_be_focused()

    # Another family's node is not a hand-off target, and that family cannot read the session at all.
    box.get_by_label("Hand off to (the name of another of your devices)").fill("stranger")
    box.get_by_role("button", name="Offer hand-off").click()
    expect(phone.locator("#researchmsg")).to_have_text(
        "No other enrolled device of your family is called “stranger”. Use the name your owner gave it.")
    expect(box.locator(".rowner")).to_have_text("●This device")
    expect(stranger.locator("#researchlist")).to_contain_text("No questions from your devices yet.")
    task_id = phone.evaluate("""() => fetch('/api/research_list', {method: 'POST', headers: {'Content-Type':
        'application/json', Authorization: 'Bearer ' + window.pocketToken()}, body: '{}'}).then(r => r.json())
        .then(b => b.tasks[0].id)""")
    # signed with the stranger's own node key, as Pocket signs: still nothing of another family (404)
    refused = stranger.evaluate(SESSION_CALLS, [task_id, True])
    assert refused == [404, 404, 404]
    # unsigned, the stranger's node is refused before anything else (ADR-040 draft)
    assert stranger.evaluate(SESSION_CALLS, [task_id, False]) == [404, 401, 401]

    # Offer to the tablet of the same family; the tablet accepts.
    box.get_by_label("Hand off to (the name of another of your devices)").fill("tablet")
    box.get_by_role("button", name="Offer hand-off").click()
    expect(phone.locator("#researchmsg")).to_have_text("Offered to “tablet”. It moves when that device accepts, within 5 minutes.")
    expect(box.locator(".rsessionstate")).to_contain_text("Offered to “tablet”: waiting for it to accept")
    tablet.locator("#refresh").click()
    tablet.get_by_role("button", name="Open: " + QUESTION).click()
    tbox = session(tablet)
    expect(tbox.locator(".rowner")).to_have_text("◐Offered to this device")
    expect(tbox).to_contain_text("“phone” offers this research to this device.")
    tbox.get_by_role("button", name="Accept hand-off").click()
    expect(tablet.locator("#researchmsg")).to_have_text("Hand-off accepted: this device now drives this research.")
    expect(tbox.locator(".rowner")).to_have_text("●This device")

    # The phone now sees the tablet as the driver; its cancel is refused and explained.
    reopen(phone)
    expect(box.locator(".rowner")).to_have_text("◑“tablet”")
    expect(box).to_contain_text("Only it can cancel or continue it")
    expect(box.get_by_role("button")).to_have_count(0)
    phone.get_by_role("button", name="Cancel research").click()
    expect(phone.locator("#researchmsg")).to_have_text("Another of your devices is driving this research right now. Ask it to hand it over.")
    expect(phone.locator(".rstage")).to_contain_text("Queued")

    # The tablet releases it; the phone may drive it again and cancels.
    tbox.get_by_role("button", name="Release").click()
    expect(tablet.locator("#researchmsg")).to_have_text("Released. Any of your linked devices can drive this research again.")
    expect(tbox.locator(".rowner")).to_have_text("○Nobody")
    reopen(phone)
    expect(box.locator(".rowner")).to_have_text("○Nobody")
    phone.get_by_role("button", name="Cancel research").click()
    expect(phone.locator(".rstage")).to_have_text("Cancelled")
    with closing(store.connect(spark.instance)) as db:
        assert db.execute("SELECT count(*) FROM autonomy_sessions").fetchone()[0] == 0
    unexpected = [p for page in (phone, tablet, stranger) for p in page.problems
                  if not any(f"status of {code}" in p for code in (401, 404, 409))]   # the refusals themselves
    assert unexpected == [], unexpected


def test_a_device_that_is_not_a_node_is_told_why(spark, new_page):
    page = new_page()
    page.goto(spark.url)
    pair(page)
    ask(page)
    session(page).get_by_role("button", name="Take ownership").click()
    expect(page.locator("#researchmsg")).to_contain_text("This device is not enrolled as a research node")
    expect(session(page).locator(".rowner")).to_have_text("○Nobody")
    expect(session(page).get_by_role("button", name="Take ownership")).to_be_enabled()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
def test_evidence_review_and_session_axe(spark, new_page, size, theme):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; the app CSP is asserted elsewhere
    page.add_init_script(f"localStorage.setItem('nsf.theme', '{theme}')")
    page.goto(spark.url)
    pair(page)
    assert page.evaluate("document.documentElement.dataset.theme") == theme
    enrol(spark, page, "phone")
    ask(page)
    spark.work()
    page.locator("#refresh").click()
    page.get_by_role("button", name="Open: " + QUESTION).first.click()
    expect(page.locator(".rstage")).to_have_text("Completed", timeout=30_000)
    page.get_by_role("button", name="Review evidence").click()
    review = page.locator(".revidence")
    expect(review.locator("h4")).to_have_text("Evidence review")
    expect(review.locator(".revsummary")).to_contain_text("checked:")
    first = review.locator(".revlist li").first
    expect(first.locator(".revstate")).to_contain_text("Supported")
    expect(first.locator(".revbasis")).to_contain_text("basis: ")
    assert first.get_attribute("data-state") == "supported"
    # not colour only: the state is a word with a glyph, and each state has its own border pattern
    styles = review.locator(".revlist li").evaluate_all(
        "els => els.map(e => [e.dataset.state, getComputedStyle(e).borderLeftStyle])")
    patterns = {"supported": "solid", "disputed": "solid", "conflicting": "double", "temporally_unresolved": "dotted",
                "insufficient": "dashed", "not_evaluated": "dashed"}
    assert all(patterns.get(state, "dashed") == style for state, style in styles), styles
    session(page).get_by_role("button", name="Take ownership").click()
    expect(session(page).get_by_role("button", name="Offer hand-off")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for box in (b.bounding_box() for b in page.locator("#researchview :is(button, input)").all() if b.is_visible()):
        assert box["height"] >= 43.5, box
    SHOTS.mkdir(exist_ok=True)
    page.locator("#researchview").screenshot(path=str(SHOTS / f"pocket-session-{theme}-{size[0]}.png"))
    page.add_script_tag(path=str(AXE))
    found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))""")
    assert [v for v in found if v["impact"] in ("serious", "critical")] == []
    assert not page.problems, page.problems
