"""Regression tests for the RC1 Pocket UAT findings (lane L4): P-01..P-06, Pocket UX 1-12, U-09..U-27, S-02/04/08 in
the page. Each test names the finding it guards. Chromium with fake media against the in-process bridge."""
import json
import re

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, pair

PASS = "correct horse battery"


def posts(page):
    seen = []
    page.on("request", lambda r: seen.append((r.url.rsplit("/", 1)[-1], r.post_data)) if r.method == "POST" else None)
    return seen


def dialogs(page):
    seen = []
    page.on("dialog", lambda d: seen.append(d.message))
    return seen


def predict(page, goal="work"):
    page.locator("#goal").select_option(goal)
    page.locator("#predict").click()
    expect(page.locator("#feedback")).to_be_visible()


def unlock(page, passphrase=PASS, opened=True):
    if not page.locator("#pass").is_visible():
        page.locator("summary", has_text="Keep episodes on this device").click()
    page.locator("#pass").fill(passphrase)
    page.locator("#unlock").click()
    if opened:
        expect(page.locator("#vaultstatus")).to_have_text("Vault unlocked")


def page_y(page, selector):
    return page.evaluate("s => document.querySelector(s).getBoundingClientRect().top + scrollY", selector)


def share_tag(page, tag="walk", learn=True):
    page.locator(f'[data-tag="{tag}"]').click()
    page.locator("#learn").set_checked(learn)
    page.locator("#save").click()
    expect(page.locator("#message")).to_have_text("Shared.")


def erase_on_spark(bridge):
    for label, *_ in bridge.server.store.pairings():
        bridge.server.store.erase(label)


def active_id(page):
    return page.evaluate("document.activeElement === document.body ? 'BODY' : document.activeElement.id")


# ---- P-01: a double tap on an outcome never lands on a destructive control -----------------------------------------
@pytest.mark.parametrize("outcome", ["continued", "switched", "paused"])
def test_double_tap_outcome_keeps_the_episode(bridge, new_page, outcome):
    page = new_page(390, 844)
    asked = dialogs(page)
    page.goto(bridge.url)
    predict(page)
    button = page.locator(f'[data-outcome="{outcome}"]')
    box = button.bounding_box()
    button.scroll_into_view_if_needed()
    box = button.bounding_box()
    memory_top = page_y(page, "#localmem")
    page.evaluate("""() => { window.clicked = []; document.addEventListener('click',
        e => window.clicked.push((e.target.textContent || '').trim()), true); }""")
    button.dblclick()
    page.wait_for_timeout(400)
    expect(page.locator("#historyLocal li")).to_have_count(1)
    expect(page.locator("#fieldmsg")).to_contain_text("Outcome recorded on this device")
    assert asked == [], "no confirmation dialog was opened by the second tap"
    assert "Forget here" not in page.evaluate("window.clicked"), page.evaluate("window.clicked")
    under = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.textContent?.trim() || ''",
                          [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
    assert under not in ("Forget here", "Share episode"), under
    assert abs(page_y(page, "#localmem") - memory_top) < 1, "layout below the outcome stays put"
    assert not page.problems, page.problems


def test_forget_here_needs_confirmation(bridge, new_page):
    page = new_page(390, 844)
    page.goto(bridge.url)
    predict(page)
    page.get_by_role("button", name="Paused").click()
    expect(page.locator("#historyLocal li")).to_have_count(1)
    page.wait_for_timeout(800)
    page.evaluate("window.confirm = (m) => { window.lastConfirm = m; return window.nextConfirm; }")
    page.evaluate("window.nextConfirm = false")
    page.get_by_role("button", name=re.compile("^Forget here")).click()
    expect(page.locator("#historyLocal li")).to_have_count(1)
    assert "cannot be undone" in page.evaluate("window.lastConfirm")
    page.evaluate("window.nextConfirm = true")
    page.get_by_role("button", name=re.compile("^Forget here")).click()
    expect(page.locator("#historyLocal li")).to_have_count(0)
    expect(page.locator("#fieldmsg")).to_have_text("Episode forgotten on this device.")


# ---- P-02: forgetting the pairing removes the vault copy; a restored credential is checked first -------------------
def test_forget_pairing_also_removes_the_vault_copy(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    unlock(page)
    expect(page.locator("#vaultstatus")).to_have_text("Vault unlocked")
    page.locator("#rememberpair").click()
    expect(page.locator("#fieldmsg")).to_have_text("Pairing protected by this vault for 24 hours.")
    assert "auth" in json.loads(page.evaluate("localStorage.getItem('neuroscifi.field.v1')"))
    page.locator("#unpair").click()
    page.wait_for_load_state()
    expect(page.locator("#linkstate")).to_have_text("Phone only")
    assert "auth" not in json.loads(page.evaluate("localStorage.getItem('neuroscifi.field.v1')"))
    unlock(page)
    expect(page.locator("#vaultstatus")).to_have_text("Vault unlocked")
    expect(page.locator("#linkstate")).to_have_text("Phone only")
    expect(page.locator("#pairform")).to_be_visible()


def test_restored_credential_is_validated_before_paired(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    unlock(page)
    page.locator("#rememberpair").click()
    expect(page.locator("#fieldmsg")).to_have_text("Pairing protected by this vault for 24 hours.")
    page.locator("#lock").click()
    expect(page.locator("#linkstate")).to_have_text("Phone only")
    # Valid: checked with the Spark, then "Paired with Spark".
    gate = []
    page.route("**/api/state", lambda route: gate.append(route))
    unlock(page)
    expect(page.locator("#linkstate")).to_have_text("Checking your Spark…")
    assert gate, "the check is in flight while the header says so"
    for route in gate:
        route.fallback()
    page.unroute("**/api/state")
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")
    # Unreachable: the credential is kept but not called paired.
    page.locator("#lock").click()
    page.route("**/api/state", lambda route: route.abort())
    unlock(page)
    expect(page.locator("#linkstate")).to_have_text("Paired · Spark unreachable")
    expect(page.locator("#pairhelp")).to_contain_text("not confirmed yet")
    page.unroute("**/api/state")
    # Revoked on the Spark: refused, explained, and dropped from the vault.
    page.locator("#lock").click()
    erase_on_spark(bridge)
    unlock(page)
    expect(page.locator("#linkstate")).to_have_text("Not paired · pair again")
    expect(page.locator("#message")).to_have_text("Your Spark no longer accepts this pairing. Pair again.")
    expect(page.locator("#pairform")).to_be_visible()
    page.wait_for_function("() => !('auth' in JSON.parse(localStorage.getItem('neuroscifi.field.v1')))")


# ---- P-03: a pairing erased on the Spark is explained wherever it shows up -----------------------------------------
@pytest.mark.parametrize("action", ["share", "refresh", "link"])
def test_revoked_pairing_is_explained(bridge, new_page, action):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    erase_on_spark(bridge)
    if action == "share":
        page.locator('[data-tag="rest"]').click()
        page.locator("#save").click()
    elif action == "refresh":
        page.locator("#refresh").click()
    else:
        page.locator("#linkdevice").click()
    expect(page.locator("#message")).to_have_text("Your Spark no longer accepts this pairing. Pair again.")
    expect(page.locator("#linkstate")).to_have_text("Not paired · pair again")
    expect(page.locator("#pairform")).to_be_visible()
    expect(page.locator("#paired")).to_be_hidden()
    if action == "share":
        expect(page.locator("#draft")).to_have_text("activity · rest")  # the draft waits for a new pairing
    bridge.invite("second-code")
    pair(page, "second-code")


# ---- P-04: keyboard focus follows the action ----------------------------------------------------------------------
def test_focus_after_keyboard_actions(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    page.locator('[data-tag="walk"]').click()
    page.locator("#save").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#message")).to_have_text("Shared.")
    assert active_id(page) == "draft"
    page.locator('[data-tag="rest"]').click()
    page.locator("#drop").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#draft")).to_have_text("No observation waiting.")
    assert active_id(page) == "draft"
    predict(page)
    page.locator('[data-outcome="switched"]').focus()
    page.keyboard.press("Enter")
    expect(page.locator("#fieldmsg")).to_have_text("Outcome recorded on this device: Switched tasks.")
    assert active_id(page) == "fieldmsg"
    page.locator("#sound").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#stop")).to_be_enabled()
    assert active_id(page) == "stop"
    expect(page.locator("#draft")).to_contain_text("sound ·", timeout=12000)
    assert active_id(page) == "draft"
    page.locator("#light").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#draft")).to_contain_text("light ·")
    expect(page.locator("#stop")).to_be_disabled()
    assert active_id(page) == "draft"


# ---- P-05: a double click on mute sends one request and reports the right count ----------------------------------
def test_mute_double_click(bridge, new_page):
    page = new_page()
    sent = posts(page)
    page.goto(bridge.url)
    pair(page)
    share_tag(page, "walk", True)
    page.get_by_role("button", name="Stop learning this pattern: activity").dblclick()
    expect(page.locator("#message")).to_have_text("Learning paused for activity. 1 observation no longer changes the forecast.")
    page.wait_for_timeout(300)
    assert [r for r, _ in sent].count("mute") == 1
    expect(page.get_by_role("button", name="Resume learning: activity")).to_be_focused()


# ---- P-06: the goal select reverts when a pending prediction refuses the change -----------------------------------
def test_goal_select_reverts_while_prediction_pending(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    predict(page, "work")
    page.locator("#goal").select_option("read")
    expect(page.locator("#fieldmsg")).to_have_text("Finish or reset the current prediction first.")
    expect(page.locator("#goal")).to_have_value("work")
    page.locator("#goal").select_option("")
    expect(page.locator("#goal")).to_have_value("work")
    expect(page.locator("#featurelist")).to_contain_text("Goal · work")


# ---- Pocket UX 1, U-20: pairing failures say what went wrong -------------------------------------------------------
def test_pairing_errors_are_distinct(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    page.locator("#code").fill("not-the-code")
    page.locator("#pairform button").click()
    expect(page.locator("#message")).to_have_text(
        "That code was not accepted. Codes work once and expire after 5 minutes. Check it, or get a new code from "
        "your Spark or a linked device.")
    expect(page.locator("#code")).to_be_focused()
    page.route("**/api/pair", lambda route: route.abort())
    page.locator("#pairform button").click()
    expect(page.locator("#message")).to_contain_text("Could not reach your Spark.")
    page.unroute("**/api/pair")
    bridge.invite("used-code")
    pair(page, "used-code")
    other = new_page()
    other.goto(bridge.url)
    other.locator("#code").fill("used-code")
    other.locator("#pairform button").click()
    expect(other.locator("#message")).to_contain_text("That code was not accepted.")


# ---- Pocket UX 2, U-19: the header shows when the Spark cannot be reached -----------------------------------------
def test_reachability_in_the_header(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    page.route("**/api/state", lambda route: route.abort())
    page.locator("#refresh").click()
    expect(page.locator("#linkstate")).to_have_text("Paired · Spark unreachable")
    expect(page.locator("#message")).to_contain_text("Your Spark is unreachable.")
    page.unroute("**/api/state")
    page.locator("#refresh").click()
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")
    page.context.set_offline(True)
    expect(page.locator("#linkstate")).to_have_text("Paired · this device is offline")
    expect(page.locator("#offlinestate")).to_contain_text("no network now")
    page.context.set_offline(False)
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")


# ---- Pocket UX 3: abstention explained -----------------------------------------------------------------------------
def test_insufficient_evidence_is_explained(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    predict(page)
    expect(page.locator("#guess")).to_have_text(
        "Insufficient evidence for a prediction: no similar learning episodes yet. Pocket guesses only when about "
        "three closely matching episodes mostly (55% or more) ended the same way.")
    page.locator("#why summary").click()
    expect(page.locator("#reason")).to_contain_text("too uncertain to name one: continued 33%")


# ---- Pocket UX 6: the specific image problem -----------------------------------------------------------------------
def test_image_errors_are_specific(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    page.locator("summary", has_text="Screen and device signals").click()
    page.locator("#screenfile").set_input_files(files=[{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"x"}])
    expect(page.locator("#fieldmsg")).to_have_text(
        "That file is text/plain. Choose a PNG, JPEG or WebP screenshot. Other evidence can still be used.")
    page.locator("#screenfile").set_input_files(files=[{"name": "s.png", "mimeType": "image/png", "buffer": b"broken"}])
    expect(page.locator("#fieldmsg")).to_contain_text("could not read that image")


# ---- Pocket UX 8, 9: vault hints name the right control; nothing to erase ------------------------------------------
def test_vault_hints(bridge, new_page):
    page = new_page()
    asked = dialogs(page)
    page.goto(bridge.url)
    page.locator("summary", has_text="Keep episodes on this device").click()
    page.locator("#wipe").click()
    expect(page.locator("#fieldmsg")).to_have_text("No local vault to erase.")
    assert asked == []
    predict(page)
    page.get_by_role("button", name="Continued").click()
    unlock(page, opened=False)
    expect(page.locator("#fieldmsg")).to_contain_text("“Lock and clear view”")
    expect(page.locator("#lock")).to_have_text("Lock and clear view")


# ---- Pocket UX 10: double taps on Share episode and Link -----------------------------------------------------------
def test_double_tap_share_episode_and_link(bridge, new_page):
    page = new_page()
    sent = posts(page)
    asked = dialogs(page)
    page.goto(bridge.url)
    pair(page)
    predict(page)
    page.get_by_role("button", name="Continued").click()
    page.get_by_role("button", name=re.compile("^Share episode")).dblclick()
    expect(page.locator("#fieldmsg")).to_contain_text("Spark received episode")
    page.locator("#linkdevice").dblclick()
    expect(page.locator("#handoff")).to_contain_text("Enter on your other device")
    page.wait_for_timeout(700)
    routes = [r for r, _ in sent]
    assert routes.count("episode") == 1 and routes.count("link") == 1, routes
    assert len(asked) == 2, asked


# ---- Pocket UX 11, 12, U-10, U-11, U-24: target sizes and layout ---------------------------------------------------
@pytest.mark.parametrize("size", [(390, 844), (768, 1024), (1024, 768), (1280, 800)])
def test_targets_and_labels(bridge, new_page, size):
    page = new_page(*size)
    page.goto(bridge.url)
    pair(page)
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    for box in (c.bounding_box() for c in page.locator("input[type=checkbox]").all() if c.is_visible()):
        assert box["width"] >= 24 and box["height"] >= 24, box
    for box in (a.bounding_box() for a in page.locator("nav a").all() if a.is_visible()):
        assert box["height"] >= 44, box
    label, field = page.locator('label[for="pass"]').bounding_box(), page.locator("#pass").bounding_box()
    assert label["y"] + label["height"] <= field["y"] + 1, "passphrase label sits above its field"
    refresh, link = page.locator("#refresh").bounding_box(), page.locator("#linkdevice").bounding_box()
    gap = link["x"] - (refresh["x"] + refresh["width"]) if abs(link["y"] - refresh["y"]) < 2 else link["y"] - (refresh["y"] + refresh["height"])
    assert gap >= 8, (refresh, link)
    cite = page.evaluate("""() => { const s = document.createElement('span'); s.className = 'cites';
        const a = document.createElement('a'); a.href = '#x'; a.textContent = '[1]'; s.append(a);
        document.getElementById('spark').append(s); const r = a.getBoundingClientRect(); s.remove();
        return [r.width, r.height]; }""")
    assert min(cite) >= 24, cite
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_citation_targets_on_touch(bridge, new_page):
    page = new_page(390, 844, has_touch=True, is_mobile=True)
    page.goto(bridge.url)
    cite = page.evaluate("""() => { const s = document.createElement('span'); s.className = 'cites';
        const a = document.createElement('a'); a.href = '#x'; a.textContent = '[1]'; s.append(a);
        document.body.append(s); const r = a.getBoundingClientRect(); return [r.width, r.height]; }""")
    assert min(cite) >= 44, cite


# ---- U-09: the status line clears itself and can be dismissed ------------------------------------------------------
def test_message_dismiss_and_auto_clear(bridge, new_page):
    page = new_page()
    page.clock.install()
    page.goto(bridge.url)
    pair(page)
    expect(page.locator("#messagebar")).to_be_visible()
    page.locator("#dismiss").click()
    expect(page.locator("#message")).to_have_text("")
    expect(page.locator("#messagebar")).to_be_hidden()
    assert active_id(page) == "main"
    share_tag(page)
    page.clock.run_for(8100)
    expect(page.locator("#message")).to_have_text("")
    expect(page.locator("#messagebar")).to_be_hidden()


# ---- U-26: landmarks, skip link and section headings ---------------------------------------------------------------
def test_landmarks_skip_link_and_headings(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    assert page.evaluate("document.querySelector('header').closest('main') === null")
    assert page.evaluate("document.querySelector('footer').closest('main') === null")
    assert page.evaluate("document.querySelector('header').compareDocumentPosition(document.querySelector('main')) & 4")
    page.keyboard.press("Tab")
    expect(page.locator(".skip")).to_be_focused()
    expect(page.locator(".skip")).to_be_visible()
    page.keyboard.press("Enter")
    assert active_id(page) == "main"
    expect(page.locator("#sensor h2")).to_have_text("Sense")
    expect(page.locator("#review h2")).to_have_text("Review")
    assert page.locator("#sensor").get_attribute("aria-labelledby") == "sensetitle"


# ---- S-02 in the page: forget the whole family; a full family is explained -----------------------------------------
def test_forget_all_linked_devices(bridge, new_page):
    phone, tablet = new_page(), new_page()
    phone.goto(bridge.url)
    pair(phone)
    phone.locator("#linkdevice").click()
    expect(phone.locator("#handoff")).to_contain_text("Enter on your other device")
    code = phone.locator("#handoff").inner_text().rsplit(": ", 1)[1].strip()
    tablet.goto(bridge.url)
    pair(tablet, code)
    tablet.locator("#unpairall").click()
    tablet.wait_for_load_state()
    expect(tablet.locator("#linkstate")).to_have_text("Phone only")
    phone.locator("#refresh").click()
    expect(phone.locator("#message")).to_have_text("Your Spark no longer accepts this pairing. Pair again.")
    assert bridge.server.store.pairings() == []


def test_full_family_is_explained(bridge, new_page, monkeypatch):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    monkeypatch.setattr(bridge.server.store, "family_full", lambda token: True)
    page.locator("#linkdevice").click()
    expect(page.locator("#message")).to_have_text("This pairing already has 6 devices. Forget one before linking another.")


# ---- axe: no violations of any impact at four widths, in the busiest states ----------------------------------------
def axe(page):
    page.add_script_tag(path=str(AXE))
    return page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")


@pytest.mark.parametrize("size", [(390, 844), (768, 1024), (1024, 768), (1280, 800)])
def test_axe_zero_violations(bridge, new_page, size):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; app CSP is asserted elsewhere
    page.goto(bridge.url)
    assert axe(page) == []
    pair(page)
    share_tag(page)
    predict(page)
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    assert axe(page) == []
    page.get_by_role("button", name="Continued").click()
    expect(page.locator("#historyLocal li")).to_have_count(1)
    assert axe(page) == []
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f"pocket-uat-{size[0]}.png"), full_page=True)


# ---- Research panel: U-27 one live region, S-04 writes paused, S-08 bytes ------------------------------------------
@pytest.fixture
def research_spark(tmp_path):
    from test_pocket_research_e2e import Spark
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


def test_research_stage_is_one_persistent_live_region(research_spark, new_page):
    page = new_page()
    page.goto(research_spark.url)
    pair(page)
    page.locator("#objective").fill("What is the latest version of the Zephyr compiler?")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchstage")).to_contain_text("Queued")
    page.evaluate("document.getElementById('researchstage').dataset.mark = 'same node'")
    research_spark.work()
    expect(page.locator("#researchstage")).to_have_text("Completed", timeout=15_000)
    assert page.evaluate("document.getElementById('researchstage').dataset.mark") == "same node"
    assert page.locator("#researchview [role=status]").count() == 1
    expect(page.locator("#researchbody .rsources li")).to_have_count(3)


def test_research_writes_paused_is_explained(research_spark, new_page):
    page = new_page()
    page.goto(research_spark.url)
    pair(page)
    page.route("**/api/research_submit", lambda route: route.fulfill(
        status=507, content_type="application/json", body='{"error":"writes_paused"}'))
    page.locator("#objective").fill("Zephyr compiler release notes")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_have_text(
        "Your Spark has paused new research because its storage needs attention. Nothing was queued. Try again "
        "after the owner frees space.")
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")


def test_research_counter_counts_bytes(research_spark, new_page):
    page = new_page()
    page.goto(research_spark.url)
    pair(page)
    question = "量子" * 150
    page.locator("#objective").fill(question)
    size = len(json.dumps({"objective": question}, ensure_ascii=False, separators=(",", ":")).encode())
    expect(page.locator("#objectivecount")).to_have_text(f"300 / 300 characters · {size} / 1024 bytes")
    expect(page.locator("#researchsubmit")).to_be_enabled()
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Queued on your Spark")
    page.evaluate("document.getElementById('objective').value = 'a' + '\\u0001'.repeat(299)")  # 6 bytes each in JSON
    page.locator("#objective").dispatch_event("input")
    expect(page.locator("#objectivecount")).to_contain_text("too long to send")
    expect(page.locator("#researchsubmit")).to_be_disabled()


# ---- RC2 retest (lane L7): RT-B-01, RT-B-02, RT-B-08 ---------------------------------------------------------------
def test_share_episode_on_a_different_row_is_never_dropped(bridge, new_page):
    """RT-B-01: only the same episode is guarded, and only while its request is in flight."""
    page = new_page()
    sent = posts(page)
    asked = dialogs(page)
    page.goto(bridge.url)
    pair(page)
    for outcome in ("Continued", "Paused"):
        predict(page)
        page.get_by_role("button", name=outcome).click()
        page.wait_for_timeout(800)
    shares = page.get_by_role("button", name=re.compile("^Share episode"))
    expect(shares).to_have_count(2)
    shares.nth(0).click()
    shares.nth(1).click()
    expect(page.locator("#fieldmsg")).to_contain_text("Spark received episode")
    page.wait_for_timeout(500)
    routes = [r for r, _ in sent]
    assert routes.count("episode") == 2, routes
    assert len(asked) == 2, asked
    assert not page.problems, page.problems


def test_pairing_refusal_explanation_stays_until_dismissed(bridge, new_page):
    """RT-B-02: errors and stops are not blanket-cleared after 8 seconds; confirmations still are."""
    page = new_page()
    page.clock.install()
    page.goto(bridge.url)
    pair(page)
    page.clock.run_for(8100)
    expect(page.locator("#message")).to_have_text("")  # "Paired." is transient
    erase_on_spark(bridge)
    page.locator("#refresh").click()
    expect(page.locator("#message")).to_have_text("Your Spark no longer accepts this pairing. Pair again.")
    page.clock.run_for(60_000)
    expect(page.locator("#message")).to_have_text("Your Spark no longer accepts this pairing. Pair again.")
    page.locator("#dismiss").click()
    expect(page.locator("#message")).to_have_text("")


def test_revoked_pairing_clears_the_research_message(research_spark, new_page):
    """RT-B-08: nothing from the old pairing is left in the research panel."""
    page = new_page()
    page.goto(research_spark.url)
    pair(page)
    page.locator("#objective").fill("Zephyr compiler release notes")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Queued on your Spark")
    erase_on_spark(research_spark.bridge)
    page.locator("#unpair").evaluate("b => b.ownerDocument.defaultView.pocketBridge('state', {}).catch(() => {})")  # next poll
    expect(page.locator("#linkstate")).to_contain_text("Not paired")
    expect(page.locator("#researchmsg")).to_have_text("")


def test_expired_link_code_is_removed(bridge, new_page):
    """RT-B-08: the code shown on the sponsoring device disappears when it stops working (five minutes)."""
    page = new_page()
    page.clock.install()
    page.goto(bridge.url)
    pair(page)
    page.locator("#linkdevice").click()
    expect(page.locator("#handoff")).to_contain_text("Enter on your other device")
    page.clock.run_for(290_000)
    expect(page.locator("#handoff")).to_contain_text("Enter on your other device")
    page.clock.run_for(11_000)
    expect(page.locator("#handoff")).to_have_text("")


def test_pocket_shows_copies_and_independent_sources(research_spark, new_page):
    """Parity with the console (U-18): "N copies, 1 independent source" when the report carries the fields."""
    page = new_page()

    def patched(route):
        body = route.fetch().json()
        for item in (body.get("report") or {}).get("answer", []):
            if item.get("status") not in (None, "summary"):
                item["copies"], item["independent_sources"] = 3, 1
        route.fulfill(json=body)

    page.route("**/api/research_get", patched)
    page.goto(research_spark.url)
    pair(page)
    page.locator("#objective").fill("What is the latest version of the Zephyr compiler?")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchstage")).to_contain_text("Queued")
    research_spark.work()
    expect(page.locator("#researchstage")).to_have_text("Completed", timeout=15_000)
    expect(page.locator("#researchbody .rcopies").first).to_have_text("3 copies, 1 independent source")


def test_revocation_during_the_post_submit_refresh_leaves_no_stale_refusal(research_spark, new_page):
    """Integrator, final regression run: the post-submit refresh() was in flight when the pairing was revoked, then
    openTask() started under the new generation, call() raised a local "Not paired" error disguised as HTTP 403, and
    fail() showed "Your Spark no longer accepts this pairing" in the research panel after it had been cleared.
    Deterministic here: the research_list reply is held until the pairing has been erased and dropped."""
    import threading
    page = new_page()
    page.goto(research_spark.url)
    pair(page)
    release = threading.Event()

    def hold_list(route):
        release.wait(10)
        route.continue_()

    page.route("**/api/research_list", hold_list)
    page.locator("#objective").fill("Zephyr compiler release notes")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Queued on your Spark")
    erase_on_spark(research_spark.bridge)
    page.locator("#unpair").evaluate("b => b.ownerDocument.defaultView.pocketBridge('state', {}).catch(() => {})")
    expect(page.locator("#linkstate")).to_contain_text("Not paired")
    release.set()
    page.wait_for_timeout(1500)  # let the held refresh and any follow-up settle
    expect(page.locator("#researchmsg")).to_have_text("")


def test_back_online_rechecks_the_spark_instead_of_keeping_a_stale_unreachable(bridge, new_page):
    """Integrator, final regression run: a request that failed while the device was offline left reach="down"; going
    back online only cleared the offline flag, so the header kept "Spark unreachable" although the Spark answered.
    Deterministic here: a state request is forced to fail while offline."""
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    page.context.set_offline(True)
    page.evaluate("() => window.pocketBridge('state', {}).catch(() => {})")   # fails: no network
    expect(page.locator("#linkstate")).to_have_text("Paired · this device is offline")
    page.context.set_offline(False)
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")


def test_restart_right_after_pairing_recovers_without_a_reload(research_spark, new_page):
    """R2-01: the bridge restarted within a second of pairing, so the first history and voice requests failed; the tab
    stayed "Spark unreachable" for over a minute with Research and Voice hidden although the credential was valid,
    and only a reload (which forgets a tab-memory pairing, POC-07) changed anything. The tab now re-checks with
    backoff and restores the header and both sections as soon as the Spark answers."""
    page = new_page()
    page.goto(research_spark.url)
    failed = []

    def fail_first(route):          # each route's first request dies with the restarting bridge
        name = route.request.url.rsplit("/", 1)[-1]
        if name in failed:
            route.fallback()
        else:
            failed.append(name)
            route.abort()
    page.route("**/api/episodes", fail_first)
    page.route("**/api/voice_status", fail_first)
    page.locator("#code").fill("test-only")
    page.locator("#pairform button").click()
    expect(page.locator("#linkstate")).to_have_text("Paired · Spark unreachable")
    expect(page.locator("#navresearch")).to_be_hidden()
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark", timeout=10_000)
    expect(page.locator("#navresearch")).to_be_visible()
    expect(page.locator("#researchform")).to_be_visible()
    expect(page.locator("#navvoice")).to_be_visible()
    assert page.evaluate("() => window.pocketToken()"), "still paired: no reload, no new code"
    assert failed == ["episodes", "voice_status"], failed
