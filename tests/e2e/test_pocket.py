"""Pocket in a real Chromium with fake microphone and camera. Needs `npm ci` for axe-core (see scripts/e2e.sh)."""
import json
import re

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, pair


def posts(page):
    seen = []
    page.on("request", lambda r: seen.append((r.url.rsplit("/", 1)[-1], r.post_data)) if r.method == "POST" else None)
    return seen


def hide_page(page):
    page.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:true});
        document.dispatchEvent(new Event('visibilitychange')); }""")


def test_prototype_journey(bridge, new_page):
    page = new_page()
    sent = posts(page)
    page.goto(bridge.url)
    expect(page.locator("#save")).to_be_disabled()
    page.locator("#sound").click()
    expect(page.locator("#draft")).to_contain_text("sound ·", timeout=12000)
    expect(page.locator("#waiting")).to_contain_text("sound ·")
    expect(page.locator("#stop")).to_be_disabled()
    expect(page.locator("#lastactive")).not_to_have_text("none since this page opened")
    page.locator("#light").click()
    expect(page.locator("#draft")).to_contain_text("light ·")
    assert not sent, "Local sensor acquisition contacted the bridge"
    pair(page)
    page.locator("#save").click()
    expect(page.locator("#history")).to_contain_text("memory only")
    page.locator('[data-tag="walk"]').click()
    page.locator("#learn").check()
    page.locator("#save").click()
    expect(page.locator("#forecast")).to_contain_text("walk (40%)")
    page.locator("#history li").filter(has_text="walk").get_by_role("button").click()
    expect(page.locator("#forecast")).to_contain_text("No learning")
    # Fake microphone and a virtual clock: automatic writes are opt-in and bounded to the session.
    page.clock.install()
    page.locator("#session summary").click()
    page.locator("#auto").check()
    page.locator("#listen").click()
    expect(page.locator("#status")).to_contain_text("Listening")
    page.clock.run_for(65000)
    expect(page.locator("#history")).to_contain_text("sound")
    assert page.locator("#auto").is_disabled()
    page.locator("#stop").click()
    expect(page.locator("#auto")).not_to_be_checked()
    count = sum(1 for name, _ in sent if name == "save")
    page.clock.run_for(65000)
    assert sum(1 for name, _ in sent if name == "save") == count
    page.locator("#listen").click()
    expect(page.locator("#status")).to_contain_text("Listening")
    hide_page(page)
    expect(page.locator("#stop")).to_be_disabled()
    expect(page.locator("#draft")).to_contain_text("No observation")
    saves = [json.loads(raw) for route, raw in sent if route == "save"]
    assert len(saves) == 3 and all(set(r) == {"id", "channel", "value", "learn"} for r in saves)
    assert saves[-1]["channel"] == "sound" and saves[-1]["learn"] is True
    assert not page.problems, page.problems  # includes any CSP violation
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_field_session_and_vault(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    page.locator("#goal").select_option("work")
    page.locator("#sound").click()
    expect(page.locator("#draft")).to_contain_text("sound ·", timeout=12000)
    page.locator("#useSound").click()
    expect(page.locator("#featurelist")).to_contain_text("Sound ·")
    page.locator("#predict").click()
    expect(page.locator("#guess")).to_contain_text("Insufficient evidence for a prediction")
    expect(page.locator("#feedback")).to_be_visible()
    page.locator("#locallearn").check()
    page.get_by_role("button", name="Continued").click()
    expect(page.locator("#fieldmsg")).to_have_text("Outcome recorded on this device: Continued.")
    expect(page.locator("#historyLocal")).to_contain_text("work → continued")
    # Vault: reject short passphrase, create, hold a pending prediction, lock, unlock and restore it.
    page.get_by_text("Keep episodes on this device").click()
    page.locator("#lock").click()
    page.locator("#pass").fill("short")
    page.locator("#unlock").click()
    expect(page.locator("#fieldmsg")).to_contain_text("at least 12 characters")
    page.locator("#pass").fill("correct horse battery")
    page.locator("#unlock").click()
    expect(page.locator("#vaultstatus")).to_have_text("Vault unlocked")
    page.locator("#goal").select_option("read")
    page.locator("#predict").click()
    expect(page.locator("#guess")).to_contain_text("Insufficient evidence")
    page.locator("#lock").click()
    expect(page.locator("#vaultstatus")).to_have_text("Session memory only")
    expect(page.locator("#guess")).to_have_text("")
    page.locator("#pass").fill("wrong passphrase!!")
    page.locator("#unlock").click()
    expect(page.locator("#fieldmsg")).to_have_text(re.compile("^That passphrase does not open the vault on this device"))
    page.locator("#pass").fill("correct horse battery")
    page.locator("#unlock").click()
    expect(page.locator("#fieldmsg")).to_have_text("Prediction restored. Record what happened next.")
    expect(page.locator("#guess")).to_contain_text("Insufficient evidence")
    page.get_by_role("button", name="Paused").click()
    expect(page.locator("#historyLocal")).to_contain_text("read → paused")
    page.locator("#wipe").click()
    expect(page.locator("#fieldmsg")).to_have_text("Local vault erased.")
    assert not page.problems, page.problems


def test_predict_with_linked_devices_requires_pairing(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    page.locator("#goal").select_option("work")
    page.locator("#withSpark").check()
    page.locator("#predict").click()
    expect(page.locator("#fieldmsg")).to_contain_text("Pair with your Spark first")
    expect(page.locator("#guess")).to_have_text("")


def share(page, tag, learn):
    page.locator(f'[data-tag="{tag}"]').click()
    page.locator("#learn").set_checked(learn)
    page.locator("#save").click()
    expect(page.locator("#message")).to_have_text("Shared.")
    expect(page.locator("#draft")).to_have_text("No observation waiting.")


def test_evening_review(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    expect(page.locator("#evenpair")).to_be_visible()
    expect(page.locator("#evenbridge")).to_be_hidden()
    pair(page)
    share(page, "walk", True)
    page.locator("#sound").click()
    expect(page.locator("#draft")).to_contain_text("sound ·", timeout=12000)
    page.locator("#learn").check()
    page.locator("#save").click()
    expect(page.locator("#message")).to_have_text("Shared.")
    expect(page.locator("#forecast")).to_contain_text("activity")
    expect(page.locator("#forecast")).to_contain_text("sound")
    expect(page.locator("#changed li")).to_have_count(2)
    # Mute: forecast drops the channel, the stored record stays but no longer learns.
    page.get_by_role("button", name="Stop learning this pattern: sound").click()
    expect(page.locator("#message")).to_have_text("Learning paused for sound. 1 observation no longer changes the forecast.")
    expect(page.locator("#forecast")).not_to_contain_text("sound")
    expect(page.locator("#forecast")).to_contain_text("activity")
    expect(page.locator("#history")).to_contain_text("sound")
    expect(page.locator("#changed li")).to_have_count(1)
    # A learning save on the muted channel is refused and explained.
    page.locator("#sound").click()
    expect(page.locator("#draft")).to_contain_text("sound ·", timeout=12000)
    page.locator("#learn").check()
    page.locator("#save").click()
    expect(page.locator("#message")).to_contain_text("Learning is paused for sound")
    expect(page.locator("#save")).to_be_enabled()
    page.locator("#learn").uncheck()
    page.locator("#save").click()
    expect(page.locator("#message")).to_have_text("Shared.")
    # Unmute restores future learning only.
    page.get_by_role("button", name="Resume learning: sound").click()
    expect(page.locator("#message")).to_contain_text("Learning resumed for sound")
    expect(page.locator("#forecast")).not_to_contain_text("sound")
    page.locator("#sound").click()
    expect(page.locator("#draft")).to_contain_text("sound ·", timeout=12000)
    page.locator("#learn").check()
    page.locator("#save").click()
    expect(page.locator("#forecast")).to_contain_text("sound")
    # Forget one item, then the rest of this session with a count.
    page.get_by_role("button", name=re.compile("^Forget this: activity")).click()
    expect(page.locator("#changed li")).to_have_count(1)
    page.locator("#forgetsession").click()
    expect(page.locator("#sessionprogress")).to_have_text(re.compile(r"^Forgot all \d+ items shared from this tab\.$"))
    expect(page.locator("#history li")).to_have_count(0)
    expect(page.locator("#forecast")).to_have_text("No learning observations retained.")
    assert [p for p in page.problems if "423" not in p] == []  # the one expected console line is the refused save


def test_forget_session_reports_partial_failure(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    pair(page)
    share(page, "walk", False)
    share(page, "rest", False)
    share(page, "focus", False)
    calls = []

    def flaky(route):
        calls.append(1)
        route.fulfill(status=500, body="{}") if len(calls) == 2 else route.continue_()

    page.route("**/api/forget", flaky)
    page.locator("#forgetsession").click()
    expect(page.locator("#sessionprogress")).to_have_text("Forgot 2 of 3. 1 could not be confirmed. Retry to finish.")
    expect(page.locator("#history li")).to_have_count(1)
    page.locator("#forgetsession").click()
    expect(page.locator("#sessionprogress")).to_have_text("Forgot all 1 items shared from this tab.")
    expect(page.locator("#history li")).to_have_count(0)


def test_offline_cold_open(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    expect(page.locator("#offlinestate")).to_have_text("Offline shell ready")
    page.context.set_offline(True)
    page.reload()
    expect(page.get_by_role("heading", name="Pocket", level=1)).to_be_visible()
    expect(page.locator("#goal")).to_be_visible()
    page.locator("#goal").select_option("read")
    expect(page.locator("#featurelist")).to_contain_text("Goal · read")
    manifest = page.evaluate("fetch('/manifest.webmanifest').then(r=>r.json())")
    assert [s["url"] for s in manifest["shortcuts"]] == ["/#workspace", "/#mark", "/#sensor"]
    page.context.set_offline(False)


def test_manifest_is_linked_and_shortcut_targets_exist(bridge, new_page):
    page = new_page()
    page.goto(bridge.url)
    assert page.locator('link[rel="manifest"]').get_attribute("href") == "/manifest.webmanifest"
    # the browser chrome matches the page in the resolved theme (ui.css --bg; docs/ui/DESIGN_SYSTEM.md)
    assert page.locator('meta[name="theme-color"]').get_attribute("content") == "#F6F4F3"
    assert page.locator('meta[name="color-scheme"]').get_attribute("content") == "light"
    manifest = page.evaluate("fetch('/manifest.webmanifest').then(r=>r.json())")
    for shortcut in manifest["shortcuts"]:
        assert page.locator("#" + shortcut["url"].split("#")[1]).count() == 1
    assert page.evaluate("fetch('/icon.svg').then(r=>r.headers.get('content-type'))") == "image/svg+xml"
    assert not page.problems, page.problems


def test_layouts(bridge, new_page):
    SHOTS.mkdir(exist_ok=True)
    tablet = new_page(1024, 768)
    tablet.goto(bridge.url)
    left, right = tablet.locator(".fieldcol").bounding_box(), tablet.locator(".sensorcol").bounding_box()
    assert left["x"] + left["width"] <= right["x"] + 1, (left, right)
    assert abs(left["y"] - right["y"]) < 4, "columns start together"
    assert tablet.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    tablet.screenshot(path=str(SHOTS / "pocket-tablet.png"), full_page=True)
    phone = new_page(390, 844)
    phone.goto(bridge.url)
    top, bottom = phone.locator(".fieldcol").bounding_box(), phone.locator(".sensorcol").bounding_box()
    assert bottom["y"] >= top["y"] + top["height"] - 1 and abs(top["x"] - bottom["x"]) < 1, (top, bottom)
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    phone.screenshot(path=str(SHOTS / "pocket-phone.png"), full_page=True)
    for sel in ("button", "select", "input[type=password]"):
        for box in (b.bounding_box() for b in phone.locator(sel).all() if b.is_visible()):
            assert box["height"] >= 43.5, (sel, box)


@pytest.mark.parametrize("size", [(1024, 768), (390, 844)], ids=["tablet", "phone"])
def test_accessibility_axe(bridge, new_page, size):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; app CSP is asserted elsewhere
    page.goto(bridge.url)
    page.add_script_tag(path=str(AXE))

    def violations():
        found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
            r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")
        return [v for v in found if v["impact"] in ("serious", "critical")]

    assert violations() == []
    pair(page)
    share(page, "walk", True)
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    assert violations() == []


def focus_report(page):
    return page.evaluate("""() => {
        const all = [...document.querySelectorAll('button, select, input, summary, a[href]')].filter(e =>
          !e.disabled && e.checkVisibility() && getComputedStyle(e).visibility === 'visible');
        all.forEach((e, i) => e.dataset.k = i);
        const buttons = new Set(all.filter(e => e.tagName === 'BUTTON').map(e => e.dataset.k));
        return {buttons: [...buttons], total: all.length};
    }""")


@pytest.mark.parametrize("paired", [False, True], ids=["unpaired", "paired"])
def test_keyboard_reaches_every_enabled_button(bridge, new_page, paired):
    page = new_page()
    page.goto(bridge.url)
    if paired:
        pair(page)
        share(page, "walk", True)
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    report = focus_report(page)
    reached, bad = set(), []
    page.evaluate("document.activeElement && document.activeElement.blur()")
    for _ in range(report["total"] + 5):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
            const e = document.activeElement, s = getComputedStyle(e);
            return {k: e.dataset.k ?? null, tag: e.tagName, id: e.id, outline: s.outlineStyle, width: parseFloat(s.outlineWidth),
                    focusVisible: e.matches(':focus-visible')};
        }""")
        if info["k"] is None:
            continue
        reached.add(info["k"])
        if not info["focusVisible"] or info["outline"] == "none" or info["width"] < 2:
            bad.append(info)
    assert not bad, bad
    missing = set(report["buttons"]) - reached
    assert not missing, page.evaluate("ks => ks.map(k => document.querySelector(`[data-k='${k}']`).outerHTML.slice(0,80))", sorted(missing))


def test_handoff_between_devices(bridge, new_page):
    a, b = new_page(), new_page()
    a.goto(bridge.url)
    pair(a)
    a.locator("#linkdevice").click()
    expect(a.locator("#handoff")).to_contain_text("Enter on your other device")
    code = a.locator("#handoff").inner_text().rsplit(": ", 1)[1].strip()
    a.locator("#goal").select_option("work")
    a.locator("#predict").click()
    a.locator("#locallearn").check()
    a.get_by_role("button", name="Continued").click()
    expect(a.locator("#fieldmsg")).to_have_text("Outcome recorded on this device: Continued.")
    a.get_by_role("button", name=re.compile("^Share episode")).click()
    expect(a.locator("#fieldmsg")).to_contain_text("Spark received episode")
    expect(a.locator("#history")).to_contain_text("field episode · continued")
    b.goto(bridge.url)
    b.locator("#code").fill(code)
    b.locator("#pairform button").click()
    expect(b.locator("#linkstate")).to_have_text("Paired with Spark")
    expect(b.locator("#history")).to_contain_text("field episode · continued")
    b.locator("#history li").filter(has_text="field episode").get_by_role("button").click()
    expect(b.locator("#history li")).to_have_count(0)
    a.locator("#refresh").click()
    expect(a.locator("#history li")).to_have_count(0)
    assert not a.problems and not b.problems, (a.problems, b.problems)
    reuse = new_page()
    reuse.goto(bridge.url)
    reuse.locator("#code").fill(code)
    reuse.locator("#pairform button").click()
    expect(reuse.locator("#message")).to_contain_text("That code was not accepted. Codes work once and expire after 5 minutes.")
