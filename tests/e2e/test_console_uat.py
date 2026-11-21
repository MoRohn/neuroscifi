"""Owner console UAT fixes (lane L3) in a real Chromium under the real CSP: phone tables, plain research reports,
privacy refusal with owner override, worker/server/409/507 states, navigation, knowledge review shortcuts and the
accessibility sweep. Each test names the UAT defect ids it guards."""
import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import checkpoints, datasets, evidence, executive, governance, graphs, store
from neuroscifi.webcortex import knowledge, orchestrator, scheduler, services
from neuroscifi.webcortex import state as wstate
from test_console_research import QUESTION, Research, enable_web, finish

TOKEN = "e2e-uat-token-" + "u" * 30
VIEWS = {"overview": "Overview", "teach": "Teach & beliefs", "evidence": "Evidence", "questions": "Questions",
         "research": "Research", "knowledge": "Knowledge", "graphs": "Graphs", "checkpoints": "Checkpoints",
         "memory": "Memory log"}
DAY = 86400


class Uat:
    """A console without a research worker (tasks stay queued) over an individual with web access on."""

    def __init__(self, folder, *, web=True):
        self.path = Path(folder) / "owner.sqlite"
        db = store.create(self.path, time.time())
        wstate.set_config(db, {"enabled": web, "min_domain_interval": 0.0}, time.time())
        db.close()
        self.server = ConsoleServer(self.path, 0, token=TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = self.server.url
        self.open = True

    def db(self):
        return store.open_existing(self.path)

    def close(self):
        if self.open:
            self.open = False
            self.server.shutdown()
            self.server.server_close()


@pytest.fixture
def uat(tmp_path):
    u = Uat(tmp_path)
    yield u
    u.close()


def go(page, view):
    page.locator("#nav").get_by_role("link", name=re.compile("^" + re.escape(VIEWS[view]))).click()
    expect(page.locator(f"#{view}")).to_be_visible()
    settle(page, view)


def settle(page, view):
    expect(page.locator(f"html[data-view='{view}'][data-inflight='0']")).to_have_count(1)
    expect(page.locator(f"#{view} [aria-busy='true']")).to_have_count(0)


def task(u, objective, *, status=None, result=None, error=None, created=None, mode="fact_verification"):
    """A research task in the instance; with `status` it is moved there directly (a finished report)."""
    db = u.db()
    try:
        now = time.time()
        t = orchestrator.submit(db, objective, created or now, mode=mode, origin="console")
        if status:
            with store.write(db):
                wstate.update_task(db, t["id"], now, status=status, stage=None, result=result, error=error,
                                   finished_at=now)
        return t["id"]
    finally:
        db.close()


def river_report(now, *, sources=True):
    """A contested fact check as the backend wrote it before the L1a fix: verdict 'insufficient' with
    '0 contradict' next to a listed contradiction, cached-age text, coded limitations and an api·api source."""
    newest = now - 5 * DAY
    side = lambda n, km, domain: {"id": f"c{n}", "text": f"The Varnel River is {km} kilometres long.", "type": "quote",  # noqa: E731
                                  "citation": n, "domain": domain, "published_at": newest, "event_at": None,
                                  "source_type": "secondary", "kind": "fact"}
    return {
        "question": "Is it true that the Varnel River is 389 km long?", "mode": "fact_verification", "as_of": now,
        "live": False,
        "answer": [{"text": "2 sources read; 1 statement cluster had usable evidence; the top statement is contested "
                            "(1 agreeing independent source group of 2). Newest cited source: 5 days ago.",
                    "type": "template", "citations": [1, 2], "confidence": None, "status": "summary"},
                   {"text": "The Varnel River is 389 kilometres long.", "type": "quote", "citations": [1],
                    "confidence": 0.5, "status": "contested", "copies": 3, "independent_sources": 1}],
        "findings": [{"statement": "The Varnel River is 389 kilometres long.", "citations": [1], "related": [2],
                      "status": "contested", "copies": 3, "independent_sources": 1}],
        "contradictions": [{"a": side(1, 389, "encyclo.example"), "b": side(2, 412, "records.example"),
                            "reason": "different values: 389 km vs 412 km",
                            "description": "One source gives 389 km for the river's length, another 412 km.",
                            "assessment": "[1] published; [2] published. Not independently confirmed."}],
        "uncertainties": [],
        "limitations": ["wikipedia: rate_limited", "budget_exhausted: budget exhausted: 3 planned queries not sent"],
        "freshness": {"class": "stable", "window_seconds": None, "ttl_seconds": None, "required_seconds": None,
                      "satisfied": True, "newest_source_at": newest},
        "sources": [{"n": 1, "url": "https://encyclo.example/varnel", "title": "Varnel River", "domain": "encyclo.example",
                     "published_at": newest, "retrieved_at": now - 3600, "retrieval_mode": "live", "kind": "page",
                     "flags": {}, "cited": True},
                    {"n": 2, "url": "https://records.example/api/varnel", "title": "Varnel record",
                     "domain": "records.example", "published_at": newest, "retrieved_at": now - 3600,
                     "retrieval_mode": "api", "kind": "api", "flags": {}, "cited": True}] if sources else [],
        "verdict": {"label": "insufficient", "confidence": 0.81, "statement": "The Varnel River is 389 km long",
                    "citations": [1, 2],
                    "reasons": ["4 independent source group(s) agree with the statement; 0 contradict it.",
                                "Posterior mean 0.81 from a Beta(1,1) prior and weighted independent groups."]},
        "comparison": None, "methodology": None, "usage": {}, "budget": None,
    }


def limit_rows(path, n):
    """Set the regulation's evidence row limit behind the console's back (0 pauses every write)."""
    db = sqlite3.connect(path)
    cfg = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
    db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(cfg | {"max_evidence_rows": n}),))
    db.commit()
    db.close()


def new_verdict_report(now):
    """The L1a report shape: verdict with supporting/contradicting claim lists."""
    r = river_report(now)
    r["verdict"] = dict(r["verdict"], label="contested", shape="X has N <unit>", supporting=["c1"], contradicting=["c2"],
                        reasons=["1 independent source group(s) agree with the statement; 1 contradict it."])
    return r


def open_report(page, objective):
    row = page.locator("#taskList tbody tr", has_text=objective)
    row.get_by_role("button", name=re.compile("^Open research task")).click()
    expect(page.locator("#report .question")).to_have_text(objective)
    settle(page, "research")


def visible_text(page, selector):
    return page.locator(selector).inner_text()   # innerText: closed <details> content is not rendered


# ---- U-01 phone tables ----------------------------------------------------------------------------------

def test_phone_tables_stack_and_keep_actions_on_screen(uat, new_page):
    """U-01: Research history and Monitors put row actions off-screen at 390 px."""
    SHOTS.mkdir(exist_ok=True)
    for k in range(3):
        task(uat, f"How long is river number {k} in the Varnel basin of the northern province?")
    task(uat, "Why did the Varnel survey stop early?", status="cancelled", error="cancelled by request")
    db = uat.db()
    scheduler.monitor_add(db, "Varnel River length re-measurements and survey reports", time.time(), interval=86400,
                          authorized_by="owner")
    db.close()
    page = new_page(390, 844)
    page.goto(uat.url)
    go(page, "research")
    for slot in ("#taskList", "#monitorList"):
        expect(page.locator(f"{slot} td[data-label]").first).to_be_visible()
        assert page.locator(f"{slot} thead").bounding_box()["height"] <= 1, "header row is visually hidden"
        assert page.evaluate(f"(() => {{ const w = document.querySelector('{slot} .tablewrap'); "
                             "return w.scrollWidth <= w.clientWidth; })()"), slot
        buttons = page.locator(f"{slot} tbody button")
        assert buttons.count() >= 2
        for b in buttons.all():
            box = b.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["height"] >= 43.5, (slot, box)
    expect(page.locator("#taskList td[data-label='Actions']").first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("#taskList").screenshot(path=str(SHOTS / "uat-phone-history-390.png"))
    page.locator("#monitorList").screenshot(path=str(SHOTS / "uat-phone-monitors-390.png"))
    desktop = new_page(1280, 900)
    desktop.goto(uat.url)
    go(desktop, "research")
    assert desktop.locator("#taskList thead").bounding_box()["height"] > 20, "a real table from 640 px"
    assert not page.problems and not desktop.problems, (page.problems, desktop.problems)


# ---- U-02, U-14, U-15, U-16, C-14 the report ---------------------------------------------------------------

def test_report_is_plain_and_consistent(uat, new_page):
    now = time.time()
    river = "Is it true that the Varnel River is 389 km long?"
    task(uat, river, status="completed", result=river_report(now))
    empty_run = "Is it true that the Varnel River freezes every winter?"
    task(uat, empty_run, status="cancelled", result=river_report(now, sources=False), error="cancelled by request")
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "research")
    open_report(page, river)
    report = page.locator("#report")
    # U-02: a plain verdict sentence, the claim that was checked, never "0 contradict" beside a contradiction
    expect(page.locator("#verdictText")).to_contain_text("not enough evidence to decide this claim")
    expect(page.locator("#verdictText .badge")).to_have_text("Not enough evidence")
    expect(report.locator(".answers .badge", has_text="Contested")).to_be_visible()
    # L1a fields: copies vs independent sources, related sources (not citations), plain contradiction sentence
    expect(report.locator(".answers .copies")).to_have_text("3 copies, 1 independent source")
    expect(report.locator(".answers a.related")).to_have_text("source 2")
    expect(report.locator(".answers li").nth(1).locator("a.cite")).to_have_count(1)
    expect(report.locator(".contradiction .reason")).to_have_text("One source gives 389 km for the river's length, another 412 km.")
    expect(page.locator("#verdictText")).to_contain_text("disagree with each other")
    expect(page.locator("#claimChecked")).to_have_text("Claim checked: The Varnel River is 389 km long")
    assert "0 contradict" not in report.text_content()
    expect(report.locator("h4", has_text="Contradictions")).to_be_visible()
    # U-14: numbers and model words only under Technical detail; limitation codes as sentences, no doubled prefix
    shown = visible_text(page, "#report")
    for jargon in ("Posterior", "Beta(1,1)", "rate_limited", "budget_exhausted", "budget exhausted: budget exhausted"):
        assert jargon not in shown, jargon
    expect(page.locator("#limitations")).to_contain_text("wikipedia: The service asked us to slow down (rate limited)")
    expect(page.locator("#limitations")).to_contain_text("used up its request, time or query budget before reading everything (3 planned queries not sent)")
    report.locator("details.tech summary").first.click()
    expect(report.locator("details.tech").first).to_contain_text("Support estimate 81%")
    # U-15: absolute date plus an age computed now
    day = time.strftime("%Y-%m-%d", time.gmtime(now - 5 * DAY))
    expect(report.locator(".answers li").first).to_contain_text(f"Newest cited source: published {day} (5 days ago).")
    # U-16 / C-14: the badge follows the sources read; source lines without "api · api"
    expect(report.locator(".badges .badge", has_text="Live")).to_be_visible()
    expect(report.locator(".badges .badge", has_text="From cache")).to_have_count(0)
    expect(report.locator("#src-2")).to_contain_text("Structured record from an API")
    assert "api · api" not in report.text_content()
    report.screenshot(path=str(SHOTS / "uat-report-verdict-1280.png"))
    # the L1a verdict: its own supporting/contradicting lists give the counts
    newer = "Is it true that the Varnel River is 389 km long today?"
    task(uat, newer, status="completed", result=new_verdict_report(now))
    page.locator("#refreshResearch").click()
    settle(page, "research")
    open_report(page, newer)
    expect(page.locator("#verdictText")).to_have_text(
        "Contested The sources disagree about this claim. 1 source sentence supports it; 1 contradicts it.")
    # U-16: a cancelled run that read nothing shows neither a cache badge nor a verdict
    open_report(page, empty_run)
    expect(report.locator(".badges .badge", has_text=re.compile("Live|From cache"))).to_have_count(0)
    expect(report.locator("h4", has_text="Verdict")).to_have_count(0)
    expect(report).to_contain_text("This research ended: cancelled by request.")
    assert not page.problems, page.problems


def test_citations_are_44px_on_touch_screens(uat, new_page):
    """U-11: citation links were 28 px on coarse pointers."""
    task(uat, "Is it true that the Varnel River is 389 km long?", status="completed", result=river_report(time.time()))
    page = new_page(390, 844, has_touch=True, is_mobile=True)
    page.goto(uat.url)
    go(page, "research")
    open_report(page, "Is it true that the Varnel River is 389 km long?")
    assert page.evaluate("matchMedia('(pointer: coarse)').matches")
    for cite in page.locator("#report a.cite").all():
        box = cite.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, box
    page.locator("#report").screenshot(path=str(SHOTS / "uat-report-touch-390.png"))


# ---- U-17 failed report --------------------------------------------------------------------------------------

def test_failed_report_is_plain_and_resumable(uat, new_page):
    objective = "What changed in the Varnel survey of 2025?"
    tid = task(uat, objective, status="failed", error="AttributeError: 'NoneType' object has no attribute 'claims'")
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "research")
    open_report(page, objective)
    expect(page.locator("#report .failure .msg")).to_contain_text("stopped because of an error inside the research worker")
    assert "AttributeError" not in visible_text(page, "#report")
    expect(page.locator("#report details.tech pre")).to_have_text(re.compile("^AttributeError"))
    page.locator("#reportResume").click()
    expect(page.locator("#reportStatus")).to_contain_text("again; it continues from its last checkpoint")
    db = uat.db()
    assert db.execute("SELECT status FROM web_tasks WHERE id=?", (tid,)).fetchone()[0] == "queued"
    db.close()
    assert not page.problems, page.problems


# ---- U-03 / C-06 privacy ----------------------------------------------------------------------------------------

def test_privacy_refusal_is_plain_and_has_an_owner_override(uat, new_page):
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "research")
    question = "What did jane.doe@example.org publish about okapis?"
    page.locator("#askObjective").fill(question)
    page.locator("#askBtn").click()
    status = page.locator("#askStatus")
    expect(status).to_contain_text("Nothing was sent. Your question contains an e-mail address.")
    assert "authorization" not in status.inner_text() and "HTTP" not in status.inner_text()
    expect(page.locator("#askObjective")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#askObjective")).to_be_focused()
    expect(page.locator("#askObjective")).to_have_value(question)
    box = page.get_by_label("Send these details to search providers anyway")
    expect(box).to_be_visible()
    page.locator("#askForm").screenshot(path=str(SHOTS / "uat-privacy-1280.png"))
    box.check()   # a confirmation names what is sent; cancelling leaves it unsent
    expect(page.locator("#confirmDialog")).to_be_visible()
    expect(page.locator("#confirmText")).to_contain_text("an e-mail address")
    page.locator("#confirmCancel").click()
    expect(box).not_to_be_checked()
    db = uat.db()
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0
    db.close()
    box.check()
    page.locator("#confirmOk").click()
    expect(status).to_contain_text("Research queued")
    expect(page.locator("#askObjective")).not_to_have_attribute("aria-invalid", "true")
    expect(page.locator("#askDiscloseRow")).to_be_hidden()
    db = uat.db()
    detail = db.execute("SELECT detail FROM web_events WHERE kind='submitted'").fetchone()[0]
    db.close()
    assert json.loads(detail)["disclosure_authorized"] is True
    assert not [p for p in page.problems if "400" not in p], page.problems


# ---- U-04 queued without a worker, U-09 duplicates -----------------------------------------------------------

def test_queued_task_without_worker_says_so(uat, new_page):
    objective = "How deep is Lake Orrin in winter?"
    task(uat, objective, created=time.time() - 120)
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "research")
    open_report(page, objective)
    hint = page.locator("#queuedHint")
    expect(hint).to_contain_text("No research worker has picked this task up yet")
    expect(hint.locator("code", has_text="nsf web work")).to_be_visible()
    expect(page.locator("#taskList .stalled")).to_contain_text("no worker yet")
    # U-09/S-14: the same question again is the same task, said plainly
    page.locator("#askObjective").fill(objective)
    page.locator("#askMode").select_option("fact_verification")
    page.locator("#askBtn").click()
    expect(page.locator("#askStatus")).to_contain_text("Already running — opened the existing task")
    db = uat.db()
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 1
    db.close()
    # S-06: a task whose worker process died says so instead of waiting silently
    dead = subprocess.Popen(["true"])
    dead.wait()
    db = uat.db()
    with store.write(db):
        db.execute("UPDATE web_tasks SET status='reading', lease_holder=?, lease_expires_at=?",
                   (f"{services.host_digest()}:{dead.pid}:console", time.time() + 600))
    db.close()
    page.locator("#refreshResearch").click()
    settle(page, "research")
    expect(page.locator("#workerStopped")).to_contain_text("stopped responding")
    expect(page.locator("#queuedHint")).to_have_count(0)
    assert not page.problems, page.problems


# ---- U-05 server stopped ----------------------------------------------------------------------------------------

def test_server_stopped_shows_one_banner_and_disables_changes(uat, new_page):
    db = uat.db()
    datasets.simulate(db, "world-a", time.time(), seed=1)
    db.close()
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "graphs")
    options = page.locator("#learnContext option").all_inner_texts()
    rows = page.locator("#datasetTable tbody tr").count()
    uat.close()
    page.locator("#refreshGraphs").click()
    banner = page.locator("#offline")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Server stopped. Restart nsf console and open the new URL it prints.")
    expect(page.locator("#graphs .msg.error")).to_have_count(0)
    expect(page.locator("#datasetTable tbody tr")).to_have_count(rows)
    assert page.locator("#learnContext option").all_inner_texts() == options, "selects are not rewritten"
    for control in ("#simBtn", "#learnBtn", "#datasetTable button.danger"):
        expect(page.locator(control).first).to_be_disabled()
    expect(page.locator("#refreshGraphs")).to_be_enabled()
    go_nav = page.locator("#nav a[data-view='research']")
    go_nav.click()
    expect(page.locator("#research")).to_be_visible()
    expect(page.locator("#askBtn")).to_be_disabled()
    expect(page.locator("#research .msg.error")).to_have_count(0)
    page.screenshot(path=str(SHOTS / "uat-server-stopped-1280.png"))


# ---- U-06 409, U-07 507, U-28 backticks, U-30 reasons --------------------------------------------------------

def test_web_turned_off_elsewhere_and_writes_paused(uat, new_page):
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "research")
    expect(page.locator("#askBtn")).to_be_enabled()
    db = uat.db()
    wstate.set_config(db, {"enabled": False}, time.time())
    db.close()
    page.locator("#askObjective").fill("How deep is Lake Orrin in winter?")
    page.locator("#askBtn").click()
    expect(page.locator("#askStatus")).to_have_text(re.compile("^Web access is off for this individual, so nothing was sent"))
    assert "`" not in page.locator("#askStatus").inner_text() and "HTTP" not in page.locator("#askStatus").inner_text()
    expect(page.locator("#webOff")).to_be_visible()
    expect(page.locator("#webEnable")).to_be_focused()
    # U-30: disabled buttons say why, and Add monitor is off with web access
    expect(page.locator("#askBtn")).to_be_disabled()
    expect(page.locator("#monitorBtn")).to_be_disabled()
    for button in ("#askBtn", "#monitorBtn"):
        why = page.locator(button).get_attribute("aria-describedby").split()[-1]
        expect(page.locator(f"#{why}")).to_have_text("Turn on web access first.")
    page.locator("#webEnable").click()
    expect(page.locator("#askBtn")).to_be_enabled()
    expect(page.locator("#monitorBtn")).to_be_enabled()
    # U-07: 507 refreshes the header and says what to do
    limit_rows(uat.path, 0)   # writes paused: an empty journal is already full
    expect(page.locator("#writes")).to_have_text("allowed")   # limit 0: even an empty journal is full
    page.locator("#askBtn").click()
    expect(page.locator("#askStatus")).to_contain_text("Writes are paused by regulation (")
    expect(page.locator("#askStatus")).to_contain_text("Free disk space, forget evidence or raise the limit")
    expect(page.locator("#writes")).to_have_text("paused")
    expect(page.locator("#writesWhy")).not_to_be_empty()
    page.locator("header.top").screenshot(path=str(SHOTS / "uat-header-paused-1280.png"))
    assert not [p for p in page.problems if "409" not in p and "507" not in p], page.problems


# ---- U-08, U-12, U-29, U-22, U-23, U-25 layout ------------------------------------------------------------------

def test_phone_header_navigation_and_counts(uat, new_page):
    SHOTS.mkdir(exist_ok=True)
    limit_rows(uat.path, 0)   # writes paused: an empty journal is already full
    page = new_page(390, 844)
    page.goto(uat.url)
    expect(page.locator("#h-overview")).to_be_visible()
    expect(page.locator("#writes")).to_have_text("paused")
    dt = page.locator(".meta dt", has_text="Writes").bounding_box()
    assert dt["height"] < 30, "U-08: 'Writes' stays on one line"
    tops = {round(b["y"]) for b in (li.bounding_box() for li in page.locator("#nav li").all())}
    assert len(tops) == 1, "U-12: one scrollable row of navigation"
    assert page.locator("#h-overview").bounding_box()["y"] < 260, "U-12: the heading is near the top"
    page.screenshot(path=str(SHOTS / "uat-phone-nav-390.png"))
    page.locator("#nav a[data-view='memory']").scroll_into_view_if_needed()
    page.locator("#nav a[data-view='memory']").click()
    settle(page, "memory")
    current = page.locator("#nav a[aria-current='page']").bounding_box()
    assert current["x"] >= 0 and current["x"] + current["width"] <= 390, current
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert "DM Sans" not in page.evaluate("getComputedStyle(document.documentElement).fontFamily")   # U-25
    # U-29: the Research count follows new tasks on any view
    expect(page.locator("#activeCount")).to_have_text("")
    limit_rows(uat.path, 100_000)
    task(uat, "How deep is Lake Orrin in winter?")
    page.locator("#nav a[data-view='questions']").click()
    settle(page, "questions")
    expect(page.locator("#activeCount")).to_have_text("1 active")


def test_heatmap_labels_and_card_spacing(uat, new_page):
    db = uat.db()
    now = time.time()
    ids = datasets.simulate(db, "world-a", now, seed=1)
    revision = graphs.learn(db, "world-a", now, train=ids["train"], validation=ids["validation"], holder="test")
    graphs.promote(db, revision, now)   # promoted without a separate evaluation (U6)
    db.close()
    page = new_page(390, 844)
    page.goto(uat.url)
    go(page, "graphs")
    row = page.locator("#revisionGroups tbody tr").first
    expect(row.locator("td[data-label='Held-out MSE']")).to_have_text(re.compile(r"\d"))   # U6: not "—"
    page.get_by_role("button", name="View revision 1").click()
    expect(page.locator("#heatmap")).to_be_visible()
    expect(page.locator("#graphDetail")).to_contain_text("measured by the promotion gate")
    heights = page.evaluate("[...document.querySelectorAll('#heatmap text')].map(t => t.getBoundingClientRect().height)")
    assert min(heights) >= 10, heights   # U-22: was 7 px
    page.locator("#graphMatrix").screenshot(path=str(SHOTS / "uat-heatmap-390.png"))


# ---- U-13, U-21, U-23 knowledge and questions ---------------------------------------------------------------------

@pytest.fixture
def research(tmp_path):
    r = Research(tmp_path)
    yield r
    r.close()


def test_report_links_to_knowledge_and_questions_have_shortcuts(research, new_page):
    page = new_page(1280, 900)
    page.goto(research.url)
    go(page, "research")
    enable_web(page)
    expect(page.locator("#askMode option[value='fact_verification']")).to_have_text("Check a claim")   # U-21
    expect(page.locator("label[for='askMode']")).to_have_text("Kind of research")
    labels = page.locator("#askMode option").all_inner_texts()   # the map shared with Pocket
    assert labels[0] == "Let NeuroSciFi choose" and "Technical (packages, releases, advisories)" in labels, labels
    assert {"Compare options", "Continuous investigation", "What changed", "Fill a knowledge gap"} <= set(labels), labels
    assert not {"Fact verification", "Comparison of two things"} & set(labels), labels
    page.locator("#askObjective").fill(QUESTION)
    page.locator("#askMode").select_option("fact_verification")
    page.locator("#askBtn").click()
    finish(page)
    link = page.locator("#reviewLink a")
    expect(link).to_be_visible()
    link.click()   # U-13: one click from the report to the item, focused and filtered
    settle(page, "knowledge")
    expect(page.locator("#kStatus")).to_have_value("provisional")
    focused = page.evaluate("document.activeElement.id")
    assert focused.startswith("k-title-"), focused
    kid = int(focused.split("-")[-1])
    expect(page.locator(f"#k-title-{kid}")).to_be_focused()
    cards = page.locator("#knowledgeList article[data-knowledge]")
    expect(cards.first.locator(".badge").first).to_have_text("Provisional")
    if cards.count() > 1:   # U-23: cards do not touch
        a, b = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
        assert b["y"] - (a["y"] + a["height"]) >= 12 or b["x"] - (a["x"] + a["width"]) >= 12
    assert page.locator("#kSearch").bounding_box()["y"] + page.locator("#kSearch").bounding_box()["height"] + 12 \
        <= cards.first.bounding_box()["y"]
    page.locator("#kStatus").select_option("accepted")
    expect(page.locator("#knowledgeList")).to_contain_text("No accepted knowledge.")
    page.locator("#kStatus").select_option("")
    page.screenshot(path=str(SHOTS / "uat-knowledge-focus-1280.png"), full_page=True)

    # web questions: Accept/Reject for a knowledge item, one button per value for a contested subject
    db = store.open_existing(research.path)
    now = time.time()
    with store.write(db):
        kq = knowledge.open_question(db, f"web:knowledge:{kid}", "Accept this knowledge item?", now)
        cq = knowledge.open_question(db, "web:contested:00000000-0000-4000-8000-000000000001:0",
                                     "Sources disagree about «The Varnel River is 389 kilometres long» (different "
                                     "values: 389 km vs 412 km); nothing was stored as knowledge. Which holds?", now)
    db.close()
    go(page, "questions")
    card = page.locator(f"article[data-pending='{kq}']")
    expect(card).to_contain_text(f"knowledge #{kid}")
    assert "web:knowledge" not in card.inner_text(), "U-14: the raw scope is only under Technical detail"
    contested = page.locator(f"article[data-pending='{cq}']")
    expect(contested).to_contain_text("sources disagree")
    expect(contested.get_by_role("button", name=re.compile("^389 km holds"))).to_be_visible()
    expect(contested.get_by_role("button", name=re.compile("^412 km holds"))).to_be_visible()
    page.screenshot(path=str(SHOTS / "uat-questions-1280.png"), full_page=True)
    contested.get_by_role("button", name=re.compile("^412 km holds")).click()
    expect(page.locator("#questionStatus")).to_contain_text(f"Question {cq} resolved")
    card.get_by_role("button", name=re.compile("^Accept")).click()
    expect(page.locator("#questionStatus")).to_contain_text("The knowledge item is accepted.")
    db = store.open_existing(research.path)
    assert db.execute("SELECT resolution FROM pending WHERE id=?", (cq,)).fetchone()[0] == "412 km"
    assert db.execute("SELECT status FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0] == "accepted"
    db.close()
    assert not page.problems, page.problems


# ---- C-07, C-10, C-11, C-12, C-13, U1, U5, U8 -------------------------------------------------------------------

def seed(path):
    """Taught beliefs with a suspected change, a promoted graph, a checkpoint and a second epoch that looks like the
    first (the match card)."""
    db = store.open_existing(path)
    try:
        now = time.time()
        evidence.start_epoch(db, "bench", now)
        n = 0

        def obs(epoch, channel, action, bit, root):
            nonlocal n
            n += 1
            evidence.observe(db, {"id": f"ev-{n:02d}", "epoch": epoch, "channel": channel, "action": action,
                                  "bit": bit, "root": root}, now)
        for _ in range(6):
            obs("bench", "camera", 0, 0, "cam1")
            obs("bench", "word", 0, 0, "alice")
        for k in range(3):
            obs("bench", "reference", 0, 0, f"ref{k}")
        governance.promote(db, "ev-01", now)
        executive.decide(db, "bench", now, record=True)
        for _ in range(6):
            obs("bench", "camera", 0, 1, "cam1")
            obs("bench", "word", 0, 0, "alice")
        executive.decide(db, "bench", now, record=True)
        ids = datasets.simulate(db, "world-a", now, seed=1)
        revision = graphs.learn(db, "world-a", now, train=ids["train"], validation=ids["validation"], holder="t")
        graphs.evaluate(db, revision, now)
        graphs.promote(db, revision, now)
        checkpoints.create(db, path, checkpoints.default_dir(path), now, policy="fast")
        evidence.start_epoch(db, "second", now)
        for k in range(4):
            obs("second", "word", 0, 0, "alice")
        for k in range(3):
            obs("second", "reference", 0, 0, f"sec-ref{k}")
    finally:
        db.close()


def test_wording_and_dialog_focus(uat, new_page):
    seed(uat.path)
    task(uat, "How deep is Lake Orrin in winter?")
    page = new_page(1280, 900)
    page.goto(uat.url.split("#")[0])
    locked = page.locator("#lockedText")   # U1/U2: what to do, and that the token lasts for the console run
    expect(locked).to_contain_text("valid for as long as this console runs")
    assert "one-time" not in locked.inner_text()
    page.goto(uat.url.split("#")[0] + "#token=" + "w" * 40)
    expect(locked).to_contain_text("session token was rejected")
    expect(locked.locator("code", has_text="nsf console")).to_be_visible()   # U-28 / U3
    assert "`" not in locked.inner_text()
    page.goto(uat.url)
    expect(page.locator("#inventory")).to_contain_text("Web memory")   # C-11
    expect(page.locator("#inventory")).to_contain_text("research tasks")
    go(page, "teach")
    expect(page.locator("#matchRule")).to_contain_text("first 12 learning readings")   # C-10
    expect(page.locator("#matchCard")).not_to_contain_text("posterior")
    go(page, "evidence")
    page.locator("#evidenceTable button.danger").first.click()
    dialog = page.locator("#confirmDialog")
    expect(dialog).to_contain_text("Open questions stay open for now")   # C-07
    expect(dialog).not_to_contain_text("superseded")
    for _ in range(5):   # U8: Tab stays inside the modal dialog
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('confirmDialog').contains(document.activeElement)")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.getElementById('confirmDialog').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    go(page, "research")
    page.locator("#taskStatus").select_option("failed")   # C-12
    expect(page.locator("#taskList")).to_have_text("No failed tasks.")
    page.locator("#taskStatus").select_option("")
    # C-13: over 2000 characters is refused with a reason instead of being cut off; 2000 CJK characters fit
    page.locator("#askObjective").fill("x" * 2001)
    page.locator("#askBtn").click()
    expect(page.locator("#askCount")).to_contain_text("2001 characters: the limit is 2000")
    expect(page.locator("#askObjective")).to_have_value("x" * 2001)
    cjk = ("ヴァルネル川の長さは何キロメートルですか" * 100)[:2000]
    page.locator("#askObjective").fill(cjk)
    expect(page.locator("#askCount")).to_have_text("2000 of 2000 characters")
    page.locator("#askBtn").click()
    expect(page.locator("#askStatus")).to_contain_text("Research queued")
    go(page, "memory")   # U5: every memory kind, and the command-line-only operations
    expect(page.locator("#memKind option")).to_have_count(13)
    expect(page.locator("#memoryBrowse tbody tr")).to_have_count(2)
    page.locator("#memKind").select_option("checkpoint")
    expect(page.locator("#memoryBrowse tbody tr")).to_have_count(1)
    expect(page.locator("#memory")).to_contain_text("nsf graph rollback")
    expect(page.locator("#memory")).to_contain_text("nsf checkpoint restore")
    assert not [p for p in page.problems if "403" not in p], page.problems


# ---- C-15 accessibility: every view, three widths, moderate included -------------------------------------------

def scan(page, found, name):
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    result = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))""")
    bad = [v for v in result if v["impact"] in ("moderate", "serious", "critical")]
    if bad:
        found[name] = bad


@pytest.mark.parametrize("size", [(390, 844), (1024, 1366), (1280, 900)], ids=["390", "1024", "1280"])
def test_axe_every_view(research, new_page, size):
    seed(research.path)
    page = new_page(*size, bypass_csp=True)   # only so axe can be injected
    page.goto(research.url)
    expect(page.locator("#h-overview")).to_be_visible()
    page.add_script_tag(path=str(AXE))
    go(page, "research")
    enable_web(page)
    page.locator("#askObjective").fill(QUESTION)
    page.locator("#askMode").select_option("fact_verification")
    page.locator("#askBtn").click()
    finish(page)
    page.locator("#monTopic").fill("Zephyr compiler releases")
    page.locator("#monitorBtn").click()
    expect(page.locator("#monitorList tbody tr")).to_have_count(1)
    found = {}
    for view in VIEWS:
        go(page, view)
        if view == "graphs":
            page.get_by_role("button", name="View revision 1").click()
            expect(page.locator("#heatmap")).to_be_visible()
        if view == "knowledge":
            expect(page.locator("#knowledgeList article[data-knowledge]").first).to_be_visible()
        expect(page.locator("html[data-inflight='0']")).to_have_count(1)
        scan(page, found, view)
        page.screenshot(path=str(SHOTS / f"uat-{view}-{size[0]}.png"), full_page=True)
    go(page, "evidence")
    page.locator("#evidenceTable button.danger").first.click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    scan(page, found, "dialog")
    page.keyboard.press("Escape")
    assert found == {}, found


# ---- RC2 retest (lane L7): RT-B-04, RT-B-05, RT-B-07, U-14/U-28 leftovers ---------------------------------------------

@pytest.mark.parametrize("width,height", [(768, 1024), (1024, 1366), (1280, 900)])
def test_row_actions_stay_inside_the_table_box_on_tablets(uat, new_page, width, height):
    """RT-B-05: Research history (768 px of columns) and Monitors (805 px) clipped their actions beside the navigation."""
    for k in range(2):
        task(uat, f"How long is river number {k} in the Varnel basin of the northern province?")
    db = uat.db()
    scheduler.monitor_add(db, "Varnel River length re-measurements and survey reports", time.time(), interval=86400,
                          authorized_by="owner")
    db.close()
    page = new_page(width, height)
    page.goto(uat.url)
    go(page, "research")
    for slot in ("#taskList", "#monitorList"):
        wrap = page.locator(f"{slot} .tablewrap")
        expect(wrap).to_be_visible()
        assert page.evaluate(f"(() => {{ const w = document.querySelector('{slot} .tablewrap'); "
                             "return w.scrollWidth <= w.clientWidth; })()"), (slot, width)
        box = wrap.bounding_box()
        for b in page.locator(f"{slot} tbody button").all():
            r = b.bounding_box()
            assert r["x"] >= box["x"] - 1 and r["x"] + r["width"] <= box["x"] + box["width"] + 1, (slot, width, r, box)
    assert not page.problems, page.problems


def test_knowledge_review_keeps_keyboard_focus(research, new_page):
    """RT-B-04: Accept / Reject / Forget re-render the list; focus must not fall to the page body."""
    page = new_page(1280, 900)
    page.goto(research.url)
    go(page, "research")
    enable_web(page)
    page.locator("#askObjective").fill(QUESTION)
    page.locator("#askMode").select_option("fact_verification")
    page.locator("#askBtn").click()
    finish(page)
    page.locator("#reviewLink a").click()
    settle(page, "knowledge")
    page.locator("#kStatus").select_option("")
    first = page.locator("#knowledgeList article[data-knowledge]").first
    kid = first.get_attribute("data-knowledge")
    first.get_by_role("button", name=re.compile("^Accept")).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#knowledgeStatus")).to_contain_text(f"Accepted knowledge #{kid}")
    focus = page.evaluate("[document.activeElement.tagName, document.activeElement.id]")
    assert focus[0] != "BODY" and (focus[1].startswith("k-") or focus[1] == "h-knowledge"), focus
    page.locator(f"#k-forget-{kid}").focus()
    page.keyboard.press("Enter")
    page.locator("#confirmDialog").get_by_role("button", name="Forget").click()
    expect(page.locator("#knowledgeStatus")).to_contain_text(f"Forgot knowledge #{kid}")
    focus = page.evaluate("[document.activeElement.tagName, document.activeElement.id]")
    assert focus[0] != "BODY", focus
    assert not page.problems, page.problems


def ask_web(u, scope, text):
    db = u.db()
    with store.write(db):
        qid = knowledge.open_question(db, scope, text, time.time())
    db.close()
    return qid


@pytest.mark.parametrize("text,values", [
    ("«The Varnel River is 389 kilometres long»: one says 389 km, another says 412 km. Nothing was stored as knowledge. "
     "Which holds?", ["389 km", "412 km"]),
    ("Sources disagree about NumPy: one says 2.3, another says 2.4. Nothing was stored. Which holds?", ["2.3", "2.4"]),
    ("Sources disagree about the river. Which holds?\nvalues: 389 km | 412 km | 400 km", ["389 km", "412 km", "400 km"]),
    ("Sources disagree about the river (different values: 389 km vs 412 km). Which holds?", ["389 km", "412 km"]),
])
def test_contested_question_has_one_button_per_value(uat, new_page, text, values):
    """RT-B-07: the backend wording changed ("one says A, another says B"; a final "values: A | B" line)."""
    qid = ask_web(uat, "web:contested:00000000-0000-4000-8000-000000000002:0", text)
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "questions")
    card = page.locator(f"article[data-pending='{qid}']")
    for v in values:
        expect(card.get_by_role("button", name=re.compile("^" + re.escape(v) + " holds"))).to_be_visible()
    assert card.get_by_role("button", name=re.compile(" holds ")).count() == len(values)
    assert not re.search(r"^values:", card.locator("p.question").inner_text(), re.M), "the machine-readable line is not shown"


def test_question_text_is_plain_with_numbers_and_keys_under_technical_detail(uat, new_page):
    """U-14, U-28 leftovers: no nats or stemmed keys in the sentence; `nsf web evidence` is <code>, not backticks."""
    calibrate = uat.db()
    with store.write(calibrate):
        cid = calibrate.execute(
            "INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(NULL,'calibrate','camera:cam-north',?,?)",
            ("The readings for camera:cam-north since evidence #17 fit a flipped mapping (gain 8.73 nats). Did "
             "camera:cam-north change? Please confirm and recalibrate. (evidence through #28)", time.time())).lastrowid
    calibrate.close()
    mid = ask_web(uat, "web:finding:1", "Monitor #1 «Zephyr compiler releases» found a meaningful change: «compiler "
                  "releas version zephyr» changed from v4.2.0 to v4.3.0. See the console (Research, Monitors) or "
                  "`nsf web evidence` for the task; answer to keep tracking it.")
    page = new_page(1280, 900)
    page.goto(uat.url)
    go(page, "questions")
    first = page.locator(f"article[data-pending='{cid}']")
    sentence = first.locator("p.question").inner_text()
    assert "nats" not in sentence and "gain" not in sentence, sentence
    assert "Did camera:cam-north change?" in sentence
    assert first.locator("details.tech").text_content().count("8.73 nats") == 1
    second = page.locator(f"article[data-pending='{mid}']")
    text = second.locator("p.question").inner_text()
    assert "releas version" not in text and "`" not in text, text
    expect(second.locator("p.question code")).to_have_text("nsf web evidence")
    assert "compiler releas version zephyr" in second.locator("details.tech").text_content()
    assert not page.problems, page.problems
