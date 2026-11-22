"""What a screen reader is given, read from Chromium's accessibility tree (CDP Accessibility.*), for the key console and
Pocket flows: every control has a role and a name, research progress and voice state sit in polite live regions that
receive the stage text as it changes, and keyboard focus moves in reading order through named stops only.

These complement the axe scans (rules over the DOM): here the browser's computed tree is checked, which is what NVDA,
Orca, TalkBack and VoiceOver consume. They do not replace a person with a screen reader
(docs/presence/ACCESSIBILITY.md, manual protocol)."""
import re

from playwright.sync_api import expect

from conftest import pair
from test_console_research import QUESTION as CONSOLE_QUESTION, Research, ask as console_ask, enable_web, finish, go
from test_pocket_research_e2e import Spark, ask as pocket_ask

import pytest

CONTROLS = {"button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "switch", "slider",
            "tab", "menuitem", "spinbutton", "DisclosureTriangle"}


class AX:
    """Chromium's accessibility tree over CDP."""

    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.send("Accessibility.enable")

    def tree(self):
        return [n for n in self.cdp.send("Accessibility.getFullAXTree")["nodes"] if not n.get("ignored")]

    def node(self, expression):
        """The accessibility node of the element `expression` evaluates to (None when the element is missing)."""
        found = self.cdp.send("Runtime.evaluate", {"expression": expression})["result"]
        if "objectId" not in found:
            return None
        nodes = self.cdp.send("Accessibility.getPartialAXTree", {"objectId": found["objectId"],
                                                                  "fetchRelatives": False})["nodes"]
        return describe(nodes[0]) if nodes else None


def describe(node):
    props = {p["name"]: p["value"].get("value") for p in node.get("properties", [])}
    return {"role": (node.get("role") or {}).get("value"), "name": (node.get("name") or {}).get("value", ""),
            "ignored": node.get("ignored", False), **props}


def unnamed_controls(ax):
    return [describe(n) for n in ax.tree() if (n.get("role") or {}).get("value") in CONTROLS
            and not ((n.get("name") or {}).get("value") or "").strip()]


def focus_walk(page, ax, steps):
    """Tab up to `steps` times from the top of the page; the role and name of each focused element, until focus
    leaves the page (it returns to <body>: the cycle is complete and nothing trapped it)."""
    page.evaluate("() => { document.activeElement?.blur(); window.scrollTo(0, 0); }")
    page.locator("body").focus()
    walk = []
    for _ in range(steps):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement === document.body"):
            break
        walk.append(ax.node("document.activeElement"))
    return walk


def watch(page, selector):
    """Record every text the live region at `selector` takes from now on (what a screen reader would announce)."""
    page.evaluate("""(sel) => { const el = document.querySelector(sel); window.__said = window.__said || {};
        const said = window.__said[sel] = [];
        new MutationObserver(() => { const t = el.textContent.trim(); if (t && said.at(-1) !== t) said.push(t); })
          .observe(el, {subtree: true, childList: true, characterData: true}); }""", selector)


def said(page, selector):
    return page.evaluate("(sel) => window.__said[sel]", selector)


def silent_live_regions(page, ax):
    """Live regions that text would make visible but that are out of the accessibility tree now (hidden while
    empty, e.g. `:empty{display:none}`): their first message is not announced, because a live region must be in the
    tree before its content changes. A region inside a closed panel stays hidden when filled and is not counted."""
    count = page.evaluate("""() => { const sel = '[aria-live]:not([aria-live="off"]), [role=status], [role=alert], [role=log]';
        const shows = e => { if (e.hidden) return false; if (e.checkVisibility()) return true;
            const before = e.textContent; e.textContent = 'probe'; const shown = e.checkVisibility();
            e.textContent = before; return shown; };
        const els = [...document.querySelectorAll(sel)].filter(shows);
        els.forEach((e, i) => e.dataset.liveProbe = i); return els.length; }""")
    silent = []
    for i in range(count):
        node = ax.node(f"document.querySelector('[data-live-probe=\\'{i}\\']')")
        if node is None or node["ignored"]:
            silent.append(page.evaluate(f"document.querySelector('[data-live-probe=\\'{i}\\']').outerHTML.slice(0, 120)"))
    page.evaluate("() => document.querySelectorAll('[data-live-probe]').forEach(e => delete e.dataset.liveProbe)")
    return silent


def assert_live(node, politeness="polite"):
    assert node and not node["ignored"], node
    assert node.get("live") == politeness, node


# ---- Pocket ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=1.0)
    yield s
    s.close()


def test_pocket_pairing_and_research_progress_in_the_accessibility_tree(spark, new_page):
    page = new_page()
    page.goto(spark.url)
    ax = AX(page)
    assert unnamed_controls(ax) == [] and silent_live_regions(page, ax) == []
    # Focus starts at the skip link, then follows the reading order of the header, the sections and the pair form.
    walk = focus_walk(page, ax, 80)
    assert 10 < len(walk) < 80, len(walk)          # the cycle ends: focus is not trapped
    assert walk[0]["role"] == "link" and walk[0]["name"] == "Skip to content"
    assert all(step and step["name"].strip() for step in walk), [s for s in walk if not (s and s["name"].strip())]
    roles = [(s["role"], s["name"]) for s in walk]
    code = next(i for i, (role, name) in enumerate(roles) if role == "textbox" and re.search("code", name, re.I))
    pair_button = next(i for i, (role, name) in enumerate(roles) if role == "button" and name == "Pair")
    assert code < pair_button, roles
    assert_live(ax.node("document.getElementById('linkstate')"))
    watch(page, "#linkstate")
    pair(page)
    assert "Paired with Spark" in said(page, "#linkstate")
    assert ax.node("document.getElementById('linkstate')")["name"] in ("", "Paired with Spark")
    # Paired: the research form and its live regions.
    expect(page.locator("#navresearch")).to_be_visible()
    page.locator("#navresearch").click()
    assert unnamed_controls(ax) == []
    objective = ax.node("document.getElementById('objective')")
    assert objective["role"] == "textbox" and objective["name"]
    assert_live(ax.node("document.getElementById('researchmsg')"))
    watch(page, "#researchmsg")
    pocket_ask(page)
    stage = ax.node("document.getElementById('researchstage')")
    assert stage["role"] == "status"
    assert_live(stage)
    watch(page, "#researchstage")
    spark.work(background=True)
    expect(page.locator("#researchstage")).to_have_text("Completed", timeout=45_000)
    stages = said(page, "#researchstage")
    assert stages[-1] == "Completed" and any(s.startswith("Reading") for s in stages), stages
    assert any(s.startswith("Queued on your Spark") for s in said(page, "#researchmsg"))
    assert unnamed_controls(ax) == []     # the report: sources, citations, read-aloud controls
    assert silent_live_regions(page, ax) == []
    citation = ax.node("document.querySelector('#researchview .cites a')")
    assert citation["role"] == "link" and citation["name"]


# ---- console --------------------------------------------------------------------------------------------------------

@pytest.fixture
def research(tmp_path):
    r = Research(tmp_path)
    yield r
    r.close()


def test_console_research_progress_is_announced_and_focus_follows_the_form(research, new_page):
    page = new_page(1280, 900)
    page.goto(research.url)
    ax = AX(page)
    expect(page.locator("#h-overview")).to_be_visible()
    assert unnamed_controls(ax) == []
    walk = focus_walk(page, ax, 12)
    assert walk[0]["role"] == "link" and re.match("Skip", walk[0]["name"]), walk[0]
    assert all(step and step["name"].strip() for step in walk), walk
    go(page, "research", "Research")
    enable_web(page)
    assert unnamed_controls(ax) == []
    # From the question field, Tab goes to the mode and then to the submit button (reading order).
    page.locator("#askObjective").focus()
    order = []
    for _ in range(6):
        order.append(ax.node("document.activeElement"))
        page.keyboard.press("Tab")
    names = [(s["role"], s["name"]) for s in order]
    mode = next(i for i, (role, _) in enumerate(names) if role == "combobox")
    submit = next(i for i, (role, name) in enumerate(names) if role == "button" and re.search("research", name, re.I))
    assert names[0][0] == "textbox" and names[0][1] and 0 < mode < submit, names
    assert_live(ax.node("document.getElementById('researchLive')"))
    status = ax.node("document.getElementById('askStatus')")
    assert status["role"] == "status"
    assert_live(status)
    watch(page, "#researchLive")
    watch(page, "#askStatus")
    console_ask(page, CONSOLE_QUESTION, "fact_verification")
    finish(page)
    assert any("Research queued" in s for s in said(page, "#askStatus"))
    announced = said(page, "#researchLive")
    assert announced and any(re.search("Completed|completed", s) for s in announced), announced
    assert unnamed_controls(ax) == [] and silent_live_regions(page, ax) == []


def test_console_voice_state_is_a_polite_status(research, new_page):
    page = new_page(1280, 900)
    page.goto(research.url)
    ax = AX(page)
    go(page, "voice", "Voice")
    expect(page.locator(".nsfv-indicator")).to_be_attached()
    indicator = ax.node("document.querySelector('.nsfv-indicator')")
    assert indicator["role"] == "status"
    assert_live(indicator)
    text = page.locator(".nsfv-indicator").text_content()
    assert re.search("[Mm]icrophone", text), text
    notice = ax.node("document.querySelector('.nsfv-notice')")
    assert_live(notice)
    assert silent_live_regions(page, ax) == []
    transcript = ax.node("document.querySelector('.nsfv-transcript')")
    assert transcript["name"] == "Transcript"
    assert_live(transcript)
    assert unnamed_controls(ax) == []
    walk = focus_walk(page, ax, 30)
    assert all(step and step["name"].strip() for step in walk), [s for s in walk if not (s and s["name"].strip())]
