"""Owner console Autonomy view (D4, docs/autonomy/RESEARCH_POLICY.md) in a real Chromium under the real CSP: the
opt-in switch asks for confirmation and records the owner, limits are validated and persist, both daily budgets show
exactly what GET /api/web/autonomy reports, budget changes reach a live region, and axe finds nothing serious or
critical in light and dark at 390 and 1280 px."""
from contextlib import closing
import json
import re
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, evolution, state

TOKEN = "e2e-autonomy-token-" + "a" * 30


def anchor(t, history=1200, run=3600):
    """NOW for this module: the wall clock, unless the fixtures' history (NOW - history) and the next `run` seconds
    would straddle a UTC midnight. Daily budgets are per UTC day, so a ledger row and a decision on opposite sides
    of midnight never meet (CI collected at 23:54 and built `decided` at 00:01). Then NOW moves back, never forward,
    to `run` seconds before that midnight, and the console runs on the same shifted clock."""
    midnight = (t + run) - (t + run) % 86400
    return t if t - history >= midnight else midnight - run


NOW = anchor(time.time())
SKEW = NOW - time.time()


def clock():
    return time.time() + SKEW


CHOSEN = "The Zephyr compiler version 4.2.0 was released on 17 November 2026"
CERTAIN = "The Quartz editor licence changed to MIT in 2025"
AUTO_USED = {"searches": 6, "pages": 8, "seconds": 16.9, "spend_usd": 0.0, "searxng_requests": 2, "engine_requests": 14}
USER_USED = {"searches": 3, "pages": 5, "seconds": 41.0, "spend_usd": 0.0, "searxng_requests": 1, "engine_requests": 7}


def hypothesize(db, statement, p):
    """The owner's hypothesis with a testable prediction (as `nsf knowledge hypothesize`)."""
    from neuroscifi.webcortex import claims, text
    with store.write(db):
        hid, _ = evolution.open_hypothesis(
            db, key=claims.claim_key(statement), statement=statement, origin="owner",
            verification={"policy": "ADR-035", "state": "not_evaluated", "basis": "owner_hypothesis",
                          "confidence": p}, claims=[], values=text.values(statement),
            polarity=claims.polarity(statement), now=NOW, with_prediction=False)
        evolution.predict(db, hid, NOW, deadline=NOW + 20 * 86400, probability=p)
        store.bump_state_version(db)


def charge(db, task_id, scope, used):
    """A settled ledger row for today, as autonomy.settle leaves it after a task ran."""
    with store.write(db):
        db.execute("INSERT INTO autonomy_ledger(task_id,scope,day,state,allowed,used,opened_at,settled_at) "
                   "VALUES(?,?,?,'settled','{}',?,?,?)", (task_id, scope, autonomy.day_of(NOW),
                                                          json.dumps(used), NOW, NOW))


class AutoConsole:
    def __init__(self, folder, *, decided=False):
        self.path = Path(folder) / "owner.sqlite"
        with closing(store.create(self.path, NOW)) as db:
            state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, NOW)
            hypothesize(db, CHOSEN, 0.5)
            hypothesize(db, CERTAIN, 0.995)      # nearly certain: below the minimum gain, refused with its reason
            charge(db, "auto-1", "autonomous", AUTO_USED)
            charge(db, "user-1", "user", USER_USED)
            if decided:   # a history: enabled, one chosen investigation, a limit change, a refusal for budget
                autonomy.enable(db, NOW - 1200, by="owner:cli")
                assert autonomy.idle(db, NOW - 1000)["action"] == "queued"
                autonomy.set_policy(db, {"autonomous_daily": {"searches": 6}}, NOW - 800)
                assert autonomy.idle(db, NOW - 500)["action"] == "refused"
        self.server = ConsoleServer(self.path, 0, token=TOKEN, clock=clock)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = self.server.url
        self.base = f"http://127.0.0.1:{self.server.server_port}/"

    def status(self, page):
        response = page.request.get(self.base + "api/web/autonomy", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.ok
        return response.json()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


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


def go(page):
    page.locator("#nav").get_by_role("link", name=re.compile("^Autonomy")).click()
    settle(page)


def settle(page):
    expect(page.locator("#autonomy")).to_be_visible()
    expect(page.locator("html[data-view='autonomy'][data-inflight='0']")).to_have_count(1)
    expect(page.locator("#autonomy [aria-busy='true']")).to_have_count(0)


def shown(page, scope, key):
    return page.locator(f"#autoBudgets .scope[data-scope={scope}] .meter[data-key={key}]")


def expected(value, unit, limit):
    """The page's wording for 'used of limit', computed independently from the API numbers."""
    if unit == "usd":
        return f"${value:.2f} of ${limit:.2f}"
    if unit == "min":
        return f"{round(value / 60, 1):g} of {round(limit / 60, 1):g} min"
    return f"{round(value)} of {round(limit)}"


def assert_budgets_match(page, api):
    for scope in ("autonomous", "user"):
        b = api[scope]
        for key, unit in (("searches", "count"), ("pages", "count"), ("seconds", "min"), ("spend_usd", "usd")):
            expect(shown(page, scope, key).locator(".meterval")).to_have_text(expected(b["used"][key], unit, b["limits"][key]))
            left = b["remaining"][key]
            word = f"${left:.2f} left" if unit == "usd" else f"{round(left / 60, 1):g} min left" if unit == "min" \
                else f"{round(left)} left"
            expect(shown(page, scope, key).locator(".meterfoot")).to_contain_text("None left today" if (
                left < 1 if unit != "usd" else left <= 0) else word)
        for key in ("searxng_requests", "engine_requests"):
            expect(page.locator(f"#autoBudgets .scope[data-scope={scope}] dd[data-key={key}]")).to_have_text(
                str(round(b["used"][key])))
    a = api["autonomous"]
    expect(shown(page, "autonomous", "concurrent").locator(".meterval")).to_have_text(
        f"{a['running']} of {a['limits']['concurrent']}")


def test_opt_in_needs_confirmation_and_is_recorded(owner, new_page):
    page = new_page(1280, 900)
    page.goto(owner.url)
    go(page)
    api = owner.status(page)
    assert api["enabled"] is False and api["switch"] is False
    expect(page.locator("#autoStateBadge")).to_have_text("Off")
    expect(page.locator("#autoSwitch")).not_to_be_checked()
    expect(page.locator("#autoSwitch")).to_have_attribute("role", "switch")
    expect(page.locator("#autoState")).to_contain_text("never enabled")
    # Both budgets show exactly what the API reports (used of limit, what is left, observed SearXNG/engine counts).
    assert_budgets_match(page, api)
    expect(shown(page, "autonomous", "searches").locator(".meterval")).to_have_text("6 of 20")
    expect(shown(page, "autonomous", "seconds").locator(".meterval")).to_have_text("0.3 of 30 min")
    expect(shown(page, "user", "searches").locator(".meterval")).to_have_text("3 of 400")
    # The ranking: the uncertain hypothesis is chosen with its signals; the near-certain one refused with its reason.
    chosen = page.locator("#autoChosen > li")
    expect(chosen).to_have_count(1)
    expect(chosen.first).to_contain_text(CHOSEN)
    expect(chosen.first.locator(".signals li[data-signal=hypothesis]")).to_contain_text("Open hypothesis")
    expect(page.locator("#autoRefused")).to_contain_text(CERTAIN)
    expect(page.locator("#autoRefused")).to_contain_text("Expected information gain 0.012 bits < minimum 0.05")
    expect(page.locator("#autoDecisions")).to_contain_text("No autonomy decision yet")

    # Cancel leaves it off and records nothing.
    page.locator("label.switch").click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    expect(page.locator("#confirmTitle")).to_have_text("Let the individual research on its own?")
    expect(page.locator("#confirmText")).to_contain_text("up to 20 searches, 60 pages, 30 minutes")
    page.locator("#confirmCancel").click()
    expect(page.locator("#confirmDialog")).to_be_hidden()
    expect(page.locator("#autoSwitch")).not_to_be_checked()
    expect(page.locator("#autoSwitch")).to_be_focused()
    expect(page.locator("#autoSwitchStatus")).to_have_text("Autonomous research stays off. Nothing was recorded.")
    assert owner.status(page)["record"] == {}

    # Keyboard: Space on the focused switch, then confirm.
    page.keyboard.press("Space")
    expect(page.locator("#confirmDialog")).to_be_visible()
    page.locator("#confirmOk").click()
    expect(page.locator("#autoSwitchStatus")).to_contain_text("Autonomous research is on, recorded as enabled by you, in this console")
    settle(page)
    expect(page.locator("#autoSwitch")).to_be_checked()
    expect(page.locator("#autoStateBadge")).to_have_text("On")
    api = owner.status(page)
    assert api["enabled"] is True and api["record"]["enabled_by"] == "owner:console"
    expect(page.locator("#autoDecisionList > li").first).to_contain_text("Switched on")

    # Switching off needs no confirmation and is recorded too.
    page.locator("label.switch").click()
    expect(page.locator("#autoSwitchStatus")).to_contain_text("switch-off is recorded")
    settle(page)
    expect(page.locator("#confirmDialog")).to_be_hidden()
    api = owner.status(page)
    assert api["enabled"] is False and api["record"]["disabled_by"] == "owner:console"
    expect(page.locator("#autoDecisionList > li").first).to_contain_text("Switched off")

    # A budget change made elsewhere (a task settled) is announced politely after a refresh.
    with closing(store.connect(owner.path)) as db:
        charge(db, "auto-2", "autonomous", {"searches": 4, "pages": 2, "seconds": 3.0, "spend_usd": 0.0})
    page.locator("#refreshAutonomy").click()
    settle(page)
    expect(page.locator("#autoLive")).to_have_attribute("aria-live", "polite")
    expect(page.locator("#autoLive")).to_contain_text("Autonomous research, searches: 10 of 20 left")
    assert_budgets_match(page, owner.status(page))
    assert not page.problems, page.problems


def test_limits_are_validated_and_persist(owner, new_page):
    page = new_page(390, 844)
    page.goto(owner.url)
    go(page)
    expect(page.locator("#lim-autonomous-searches")).to_have_value("20")
    expect(page.locator("#lim-autonomous-seconds")).to_have_value("30")
    expect(page.locator("#lim-min_gain")).to_have_value("0.05")
    page.locator("#lim-autonomous-searches").fill("-1")
    page.locator("#lim-autonomous-concurrent").fill("17")
    page.locator("#lim-min_gain").fill("1.5")
    page.locator("#lim-user-pages").fill("2.5")
    page.locator("#autoSave").click()
    expect(page.locator("#autoLimitsStatus")).to_have_text("Nothing was saved: correct the 4 marked fields.")
    expect(page.locator("#lim-autonomous-searches")).to_be_focused()
    expect(page.locator("#lim-autonomous-searches")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#lim-autonomous-searches-err")).to_have_text("Must be a number of 0 or more.")
    expect(page.locator("#lim-autonomous-concurrent-err")).to_have_text("At most 16 at the same time.")
    expect(page.locator("#lim-min_gain-err")).to_have_text("At most 1 bit.")
    expect(page.locator("#lim-user-pages-err")).to_have_text("Must be a whole number.")
    assert "lim-min_gain-err" in page.locator("#lim-min_gain").get_attribute("aria-describedby")
    assert owner.status(page)["autonomous"]["limits"]["searches"] == 20, "nothing was sent"

    page.locator("#lim-autonomous-searches").fill("10")
    page.locator("#lim-autonomous-concurrent").fill("1")
    page.locator("#lim-autonomous-seconds").fill("15")
    page.locator("#lim-min_gain").fill("0.1")
    page.locator("#lim-user-pages").fill("600")
    page.locator("#autoSave").click()
    expect(page.locator("#autoLimitsStatus")).to_have_text(
        "Saved: autonomous searches 20 → 10; autonomous active time 30 → 15 min; autonomous at the same time 2 → 1; "
        "your pages read 1200 → 600; minimum expected information gain 0.05 → 0.1.")
    settle(page)
    expect(page.locator("#lim-autonomous-searches")).not_to_have_attribute("aria-invalid", "true")
    api = owner.status(page)
    assert api["autonomous"]["limits"] == {"searches": 10, "pages": 60, "seconds": 900.0, "spend_usd": 1.0, "concurrent": 1}
    assert api["user"]["limits"]["pages"] == 1200 // 2 and api["min_gain"] == 0.1
    assert_budgets_match(page, api)
    expect(page.locator("#autoDecisionList > li").first).to_contain_text("Limits changed")
    expect(page.locator("#autoDecisionList > li").first).to_contain_text("autonomous searches 10")
    # Persisted: a fresh page load shows the stored values.
    page.goto(owner.url)
    go(page)
    expect(page.locator("#lim-autonomous-searches")).to_have_value("10")
    expect(page.locator("#lim-autonomous-seconds")).to_have_value("15")
    expect(page.locator("#lim-autonomous-concurrent")).to_have_value("1")
    expect(page.locator("#lim-user-pages")).to_have_value("600")
    expect(page.locator("#lim-min_gain")).to_have_value("0.1")
    expect(shown(page, "autonomous", "searches").locator(".meterval")).to_have_text("6 of 10")
    # Undo restores the stored values; saving unchanged fields sends nothing.
    page.locator("#lim-autonomous-pages").fill("5")
    page.locator("#autoReset").click()
    expect(page.locator("#lim-autonomous-pages")).to_have_value("60")
    page.locator("#autoSave").click()
    expect(page.locator("#autoLimitsStatus")).to_have_text("Nothing to save: the fields match the stored limits.")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for box in (b.bounding_box() for b in page.locator("#autonomy :is(button, input:not([type=checkbox]))").all() if b.is_visible()):
        assert box["height"] >= 43.5, box
    assert not page.problems, page.problems


def test_decisions_show_chosen_signals_and_refusal_reasons(decided, new_page):
    page = new_page(1280, 900)
    page.goto(decided.url)
    go(page)
    items = page.locator("#autoDecisionList > li")
    expect(items).to_have_count(4)
    expect(items.nth(0)).to_contain_text("Refused")
    expect(items.nth(0)).to_contain_text("Daily autonomous research budget exhausted on")
    expect(items.nth(1)).to_contain_text("Limits changed")
    expect(items.nth(2)).to_contain_text("Chosen")
    expect(items.nth(2).locator(".signals li[data-signal=prediction_uncertainty]")).to_contain_text("1.00")
    expect(items.nth(3)).to_contain_text("Switched on")
    expect(items.nth(3)).to_contain_text("you, on the command line")
    expect(page.locator("#autoBudgets .scope[data-scope=autonomous]")).to_contain_text("Exhausted")
    expect(page.locator("#autoBudgets .scope[data-scope=user]")).to_contain_text("Room left")
    expect(page.locator("#autoRefused")).to_contain_text("Investigated autonomously within the last 24 hours")
    assert_budgets_match(page, decided.status(page))
    # The chosen decision links to its task's report.
    items.nth(2).get_by_role("link", name="Open its task under Research").click()
    expect(page.locator("#research")).to_be_visible()
    expect(page.locator("#report .question")).to_contain_text("Zephyr compiler")
    assert not page.problems, page.problems


def test_a_notification_is_claimed_only_with_a_delivered_question(owner, new_page):
    """R1-01: the RC2 log recorded "notified" with pending_id null and the console said "You were notified" although
    no question existed. Only a decision with a question id says so; the others say "Not delivered"."""
    with closing(store.open_existing(owner.path)) as db, store.write(db):
        state.log_event(db, None, "decision", {"autonomy": "notified", "pending_id": None}, NOW - 30)
        state.log_event(db, None, "decision", {"autonomy": "finding not delivered", "pending_id": None}, NOW - 20)
        state.log_event(db, None, "decision", {"autonomy": "notified", "pending_id": 42}, NOW - 10)
        store.bump_state_version(db)
    page = new_page(1280, 900)
    page.goto(owner.url)
    go(page)
    items = page.locator("#autoDecisionList > li")
    expect(items).to_have_count(3)
    expect(items.nth(0)).to_contain_text("You were notified")
    expect(items.nth(0)).to_contain_text("question #42")
    for n in (1, 2):
        expect(items.nth(n)).to_contain_text("Not delivered")
        expect(items.nth(n)).not_to_contain_text("You were notified")
    assert not page.problems, page.problems


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
def test_autonomy_axe(decided, new_page, size, theme):
    page = new_page(*size, bypass_csp=True)   # only so axe can be injected; the CSP is enforced in the other tests
    page.add_init_script(f"localStorage.setItem('nsf.theme', '{theme}')")
    page.goto(decided.url)
    go(page)
    assert page.evaluate("document.documentElement.dataset.theme") == theme
    page.locator("#autonomy details").evaluate_all("els => els.forEach(e => e.open = true)")
    page.locator("#lim-autonomous-searches").fill("-5")
    page.locator("#autoSave").click()          # field errors are part of the scanned UI
    expect(page.locator("#lim-autonomous-searches-err")).to_be_visible()
    page.add_script_tag(path=str(AXE))
    scan = """axe.run(document.getElementById('autonomy'),{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))"""
    bad = [v for v in page.evaluate(scan) if v["impact"] in ("serious", "critical")]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f"console-autonomy-{theme}-{size[0]}.png"), full_page=True)
    page.locator("#lim-autonomous-searches").fill("6")
    page.locator("#autoReset").click()
    page.locator("label.switch").click()   # on is recorded already (owner:cli); off, then on asks to confirm
    expect(page.locator("#autoSwitchStatus")).to_contain_text("switch-off is recorded")
    settle(page)
    page.locator("label.switch").click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    dialog = page.evaluate("""axe.run(document.getElementById('confirmDialog'),{resultTypes:['violations']}).then(r =>
        r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id))""")
    page.keyboard.press("Escape")
    expect(page.locator("#confirmDialog")).to_be_hidden()
    expect(page.locator("#autoSwitch")).not_to_be_checked()
    assert bad == [] and dialog == [], (bad, dialog)
    assert not page.problems, page.problems
