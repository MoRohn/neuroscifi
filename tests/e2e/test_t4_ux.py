"""Regression tests for the T4 UX/a11y findings on RC1 (T4-01 .. T4-08): Pocket reflow at 320 px, honest copy,
Pocket refusal focus and submit hints, forced-colours budget bars, touch targets, Knowledge empty state, mic consent
dialog, voice indicator, Autonomy candidate dedupe and signal chip descriptions."""
import re

import pytest
from playwright.sync_api import expect

from conftest import pair
from test_console_autonomy_e2e import AutoConsole, go
from test_pocket_research_e2e import Spark


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


@pytest.fixture
def owner(tmp_path):
    c = AutoConsole(tmp_path)
    yield c
    c.close()


@pytest.fixture
def decided(tmp_path):
    c = AutoConsole(tmp_path, decided=True)
    yield c
    c.close()


def test_pocket_320_has_no_horizontal_scroll_t4_01(spark, new_page):
    page = new_page(320, 640)
    page.goto(spark.url)
    pair(page)
    expect(page.locator("#researchform")).to_be_visible()
    widths = page.evaluate("() => ({doc: document.documentElement.scrollWidth, view: innerWidth, "
                           "select: document.getElementById('researchmode').getBoundingClientRect().width})")
    assert widths["doc"] <= widths["view"], widths
    assert widths["select"] <= 320 - 2 * 18 + 1, widths


def test_pocket_submit_hint_and_private_refusal_t4_04_t4_05(spark, new_page):
    page = new_page(390, 844)
    page.goto(spark.url)
    pair(page)
    expect(page.locator("#researchsubmit")).to_be_disabled()
    expect(page.locator("#researchsubmithint")).to_have_text("Type a question to enable Submit.")
    expect(page.locator("#researchsubmit")).to_have_attribute("aria-describedby", "researchsubmithint")
    page.locator("#objective").fill("Is jane.doe@example.org a maintainer of Zephyr?")
    expect(page.locator("#researchsubmithint")).to_have_text("")
    sent = []
    page.on("request", lambda r: sent.append(r.url) if "/api/research_submit" in r.url else None)
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Remove personal details")
    # R2-05 (R4): an e-mail address is refused on the phone before anything is sent; no request leaves the page
    expect(page.locator("#researchmsg")).to_contain_text("Nothing was sent. The question contains an e-mail address.")
    page.wait_for_timeout(300)
    assert sent == [], sent
    expect(page.locator("#objective")).to_be_focused()
    expect(page.locator("#objective")).to_have_attribute("aria-invalid", "true")
    assert "researchmsg" in page.locator("#objective").get_attribute("aria-describedby")
    page.locator("#objective").fill("Is the Zephyr maintainer list public?")
    expect(page.locator("#objective")).not_to_have_attribute("aria-invalid", "true")
    # a kind only the Spark can judge (a phone number needs its context check) is still POSTed and refused there,
    # and the copy says so rather than "Nothing was sent"
    page.locator("#objective").fill("Who answers the Zephyr support phone +44 7700 900123?")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Refused by your Spark")
    expect(page.locator("#researchmsg")).to_contain_text("Nothing was stored or searched.")
    expect(page.locator("#researchmsg")).not_to_contain_text("Nothing was sent")
    assert len(sent) == 1, sent
    expect(page.locator("#objective")).to_be_focused()
    expect(page.locator("#objective")).to_have_attribute("aria-invalid", "true")


def test_pocket_queued_message_cleared_and_basis_wording_t4_04(spark, new_page):
    page = new_page(390, 844)
    page.goto(spark.url)
    pair(page)
    page.locator("#objective").fill("What is the latest version of the Zephyr compiler?")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Queued on your Spark")
    spark.work()
    expect(page.locator("#researchstage")).to_contain_text("Completed", timeout=15_000)
    expect(page.locator("#researchmsg")).not_to_contain_text("Queued on your Spark")
    # No on-device voice in this Chromium: nothing to press, so no "Read aloud".
    expect(page.locator("#readaloud")).to_be_hidden()


def test_pocket_insufficient_basis_says_why_t4_04(spark, new_page):
    page = new_page(390, 844)
    page.goto(spark.url)
    pair(page)
    page.route("**/api/research_evidence*", lambda r: r.fulfill(
        json={"subjects": 4, "evidence": [
            {"state": "insufficient", "statement": "A claim", "basis": None, "sources": 1, "domain": "one.example"},
            {"state": "insufficient", "statement": "Another claim", "basis": None, "sources": 2},
            {"state": "insufficient", "statement": "", "basis": None, "sources": 0},
            {"state": "insufficient", "statement": "Legacy reply", "basis": None}]}))
    page.locator("#objective").fill("What is the latest version of the Zephyr compiler?")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Queued on your Spark")
    spark.work()
    expect(page.locator("#researchstage")).to_contain_text("Completed", timeout=15_000)
    page.get_by_role("button", name="Review evidence").click()
    expect(page.locator(".revbasis").first).not_to_contain_text("not stated", timeout=10_000)
    # R2-06: a statement quoted from a listed source is not "no supporting evidence": say what the basis lacks
    basis = page.locator(".revbasis")
    expect(basis.nth(0)).to_contain_text("stated by one source only, not confirmed by an independent source")
    expect(basis.nth(1)).to_contain_text("stated by 2 sources, not enough independent agreement")
    expect(basis.nth(2)).to_contain_text("none: no source states it")
    expect(basis.nth(3)).to_contain_text("stated by one source only")
    for i in range(4):
        expect(basis.nth(i)).not_to_contain_text("no supporting evidence was found")


def test_pocket_volume_slider_and_checkbox_targets_t4_07(spark, new_page):
    page = new_page(390, 844, has_touch=True, is_mobile=True)
    page.goto(spark.url)
    pair(page)
    page.locator("#navvoice").evaluate("e => e.hidden = false")
    page.evaluate("() => { const v = document.getElementById('voice'); v.hidden = false; }")
    if page.locator("#nsfvVolume").count():
        box = page.locator("#nsfvVolume").bounding_box()
        assert box["height"] >= 24, box
    for sel in ("#withSpark",):
        box = page.locator(sel).bounding_box()
        assert box["width"] >= 24 and box["height"] >= 24, (sel, box)


def test_console_voice_panel_consent_dialog_indicator_and_targets_t4_07_t4_08(decided, new_page):
    page = new_page(1280, 900)
    page.goto(decided.url)
    page.locator("#nav").get_by_role("link", name="Voice").click()
    expect(page.locator("#voice .nsfv")).to_be_visible()
    for sel in (".nsfv-check", ".nsfv-mode"):
        for box in [e.bounding_box() for e in page.locator(sel).all()]:
            assert box["height"] >= 24, (sel, box)
    for sel in (".nsfv-check input", ".nsfv-mode input"):
        for box in [e.bounding_box() for e in page.locator(sel).all()]:
            assert box["height"] >= 24 and box["width"] >= 24, (sel, box)
    # The consent path: the client asks for consent when the microphone is switched on for the first time.
    page.evaluate("""() => {
        const api = window.__nsfVoice;
        api.client.micOnNow = async () => { throw Object.assign(new Error('consent'), {code: 'consent'}); };
        api.ui.mic.disabled = false;
    }""")
    page.locator(".nsfv-mic, .nsfv-bar button[aria-pressed]").first.click()
    dialog = page.get_by_role("dialog", name="Microphone consent")
    expect(dialog).to_be_visible()
    expect(dialog).to_be_focused()
    expect(dialog).to_have_attribute("aria-modal", "true")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page.locator(".nsfv-bar button[aria-pressed]").first).to_be_focused()
    # Mic state and speaking state are reported together, not one instead of the other.
    page.evaluate("""() => {
        const c = window.__nsfVoice.client;
        c.speaking = true; c.dispatchEvent(new Event('state'));
    }""")
    expect(page.locator(".nsfv-indicator")).to_have_text("Microphone off · NeuroSciFi speaking")


def test_autonomy_dedupes_candidates_and_explains_chips_t4_08(owner, new_page):
    page = new_page(1280, 900)

    def twice(route):
        response = route.fetch()
        body = response.json()
        chosen = (body.get("candidates") or {}).get("chosen") or []
        if chosen:
            dup = dict(chosen[0], key=chosen[0]["key"] + "-dup", kind="hypothesis")
            body["candidates"]["chosen"] = chosen + [dup]
        route.fulfill(response=response, json=body)

    page.route("**/api/web/autonomy", twice)
    page.goto(owner.url)
    go(page)
    items = page.locator("#autoChosen > li")
    statements = [t.strip() for t in items.locator(".kstatement").all_inner_texts()]
    assert len(statements) == len(set(statements)), statements
    expect(page.locator("#autoCandidates h3").first).to_have_text(f"Would investigate ({len(statements)})")
    expect(items.first.locator(".candmeta").first).to_contain_text("also:")
    chip = page.locator(".signals li[data-signal]").first
    assert re.search(r"weight \d\.\d\d on a 0 to 1 scale", chip.get_attribute("title"))
    assert chip.get_attribute("aria-label") == chip.get_attribute("title")


def test_budget_bars_survive_forced_colours_t4_06(decided, new_page):
    page = new_page(1280, 900, forced_colors="active")
    page.goto(decided.url)
    go(page)
    fills = page.locator("#autoBudgets .mtrack .used")
    fills.first.wait_for(state="attached")
    colours = page.evaluate("""() => [...document.querySelectorAll('#autoBudgets .mtrack .used')].filter(e => e.offsetWidth > 0)
        .map(e => [getComputedStyle(e).backgroundColor, getComputedStyle(e.parentElement).backgroundColor])""")
    assert colours, "at least one budget bar has a fill"
    for fill, track in colours:
        assert fill != track and fill != "rgba(0, 0, 0, 0)", (fill, track)


def test_knowledge_empty_state_has_a_next_step_and_provisional_wording_t4_03_t4_08(tmp_path, new_page):
    c = AutoConsole(tmp_path)
    try:
        page = new_page(1280, 900)
        page.goto(c.url)
        page.locator("#nav").get_by_role("link", name=re.compile("^Knowledge")).click()
        expect(page.locator("#knowledgeList .empty")).to_contain_text("Next step")
        item = {"id": 7, "statement": "The Zephyr compiler is at 4.2.0", "status": "provisional", "version": 1,
                "confidence": 0.6, "freshness_class": "volatile", "last_verified_at": 1_700_000_000,
                "first_verified_at": 1_700_000_000, "sources": [], "versions": [], "feedback": []}
        page.route("**/api/web/knowledge*", lambda r: r.fulfill(json={"items": [item]}))
        page.locator("#kTopic").fill("zephyr")
        page.keyboard.press("Enter")
        card = page.locator("[data-knowledge='7']")
        expect(card).to_contain_text("Last checked against sources")
        expect(card).to_contain_text("First seen")
        expect(card).not_to_contain_text("verified")
    finally:
        c.close()


def test_an_owner_assertion_is_labelled_by_its_provenance_not_as_verified_t1_18(tmp_path, new_page):
    """T1-18 (RC2 retest r1, s5_kview): owner-asserted rows (verification not_evaluated) showed "Last verified" and
    "First verified". They say who asserted them and when; a verified conclusion keeps its verification dates."""
    from contextlib import closing
    from datetime import datetime, timezone
    from neuroscifi.instance import store
    from neuroscifi.webcortex import knowledge
    c = AutoConsole(tmp_path)
    try:
        when = 1_795_200_000.0
        with closing(store.open_existing(c.path)) as db:
            kid = knowledge.assert_fact(db, "Rao and Ballard published the predictive coding model of the visual "
                                        "cortex in 1999.", when)["id"]
        page = new_page(1280, 900)
        page.goto(c.url)
        page.locator("#nav").get_by_role("link", name=re.compile("^Knowledge")).click()
        page.locator("#kTopic").fill("predictive coding")
        page.keyboard.press("Enter")
        card = page.locator(f"[data-knowledge='{kid}']")
        day = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%d")
        expect(card).to_contain_text("Asserted by you")
        expect(card).to_contain_text(day)
        expect(card).not_to_contain_text("verified")
        assert not page.problems, page.problems
    finally:
        c.close()


AXE_ALL = "axe.run(document,{resultTypes:['violations']}).then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | ')))"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 390, 1280])
def test_changed_views_axe_clean(spark, owner, new_page, width, theme):
    from conftest import AXE
    page = new_page(width, 900, bypass_csp=True)
    page.add_init_script(f"localStorage.setItem('nsf.theme', '{theme}')")
    page.goto(owner.url)
    page.add_script_tag(path=str(AXE))
    for view in ("Knowledge", "Autonomy", "Voice"):
        page.locator("#nav").get_by_role("link", name=re.compile(f"^{view}")).click()
        page.wait_for_timeout(400)
        if view == "Voice":
            page.evaluate("() => { window.__nsfVoice.ui.consent.hidden = false; }")
            # T4-02: a model answer with its marked sentence, as the transcript shows it
            page.evaluate("""() => window.__nsfVoice.client.emit('segment', {id: 991, speaker: 'neuroscifi',
                status: 'final', authority: 'authoritative', created_at: 1795280000, label: 'NeuroSciFi',
                text: 'Zephyr 4.2.0 shipped. [1]\\nIt adds targets. [2] (only one source or too little evidence; not independently confirmed)',
                detail: {label: 'Model-written summary by m from the collected evidence; each sentence was checked against its cited sources, and a sentence that independent sources do not support is marked',
                         sources: [{n: 1, title: 'Zephyr', url: 'http://news.example/z', domain: 'news.example'}]}})""")
            expect(page.locator("[data-seg='991']")).to_contain_text("not independently confirmed")
        assert page.evaluate(AXE_ALL) == [], view
    page.goto(spark.url)
    pair(page)
    page.add_script_tag(path=str(AXE))
    page.locator("#objective").fill("x@y.org")
    page.locator("#researchsubmit").click()
    expect(page.locator("#objective")).to_have_attribute("aria-invalid", "true")
    # R2-05 (R4): an e-mail address is refused on the phone before sending; the refused state is what axe checks
    expect(page.locator("#researchmsg")).to_contain_text("Nothing was sent. The question contains an e-mail address.")
    assert page.evaluate(AXE_ALL) == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # R2-06: the evidence review with each no-basis wording
    page.route("**/api/research_evidence*", lambda r: r.fulfill(json={"subjects": 3, "evidence": [
        {"state": "insufficient", "statement": "A claim", "basis": None, "sources": 1, "domain": "one.example"},
        {"state": "insufficient", "statement": "Another claim", "basis": None, "sources": 2},
        {"state": "supported", "statement": "Third", "basis": "corroboration", "sources": 3}]}))
    page.locator("#objective").fill("What is the latest version of the Zephyr compiler?")
    page.locator("#researchsubmit").click()
    spark.work()
    expect(page.locator("#researchstage")).to_contain_text("Completed", timeout=15_000)
    page.get_by_role("button", name="Review evidence").click()
    expect(page.locator(".revbasis").first).to_contain_text("stated by one source only")
    assert page.evaluate(AXE_ALL) == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
