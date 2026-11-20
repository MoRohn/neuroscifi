"""Owner console in a real Chromium under its real CSP (axe injection alone uses bypass_csp, as for Pocket)."""
import re
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import checkpoints, datasets, evidence, executive, governance, graphs, store

TOKEN = "e2e-console-token-" + "k" * 30
VIEWS = ["overview", "teach", "evidence", "questions", "graphs", "checkpoints", "memory"]
NAV = {"overview": "Overview", "teach": "Teach & beliefs", "evidence": "Evidence", "questions": "Questions",
       "graphs": "Graphs", "checkpoints": "Checkpoints", "memory": "Memory log"}


class Console:
    def __init__(self, folder):
        self.path = Path(folder) / "owner.sqlite"
        store.create(self.path, time.time()).close()
        self.server = ConsoleServer(self.path, 0, token=TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/"
        self.url = self.server.url

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def console(tmp_path):
    c = Console(tmp_path)
    yield c
    c.close()


@pytest.fixture
def seeded(console):
    """An individual with taught beliefs, a suspected change, an open question, a promoted graph, a checkpoint and a
    second epoch that looks like the first."""
    db = store.open_existing(console.path)
    try:
        now = time.time()
        evidence.start_epoch(db, "bench", now)
        n = 0

        def obs(channel, action, bit, root):
            nonlocal n
            n += 1
            evidence.observe(db, {"id": f"ev-{n:02d}", "epoch": "bench", "channel": channel, "action": action,
                                  "bit": bit, "root": root}, now)
        for _ in range(6):
            obs("camera", 0, 0, "cam1")
            obs("word", 0, 0, "alice")
        for k in range(3):
            obs("reference", 0, 0, f"ref{k}")
        obs("reference", 1, 1, "ref9")
        governance.forget_evidence(db, "ev-16", now)
        governance.promote(db, "ev-01", now)
        executive.decide(db, "bench", now, record=True)
        for _ in range(6):
            obs("camera", 0, 1, "cam1")
            obs("word", 0, 0, "alice")
        executive.decide(db, "bench", now, record=True)
        ids = datasets.simulate(db, "world-a", now, seed=1)
        revision = graphs.learn(db, "world-a", now, train=ids["train"], validation=ids["validation"], holder="test")
        graphs.evaluate(db, revision, now)
        graphs.promote(db, revision, now)
        graphs.learn(db, "world-a", now, train=ids["train"], validation=ids["validation"], holder="test")
        checkpoints.create(db, console.path, checkpoints.default_dir(console.path), now, policy="fast")
        # A later epoch whose readings come from the original mapping looks like "bench" (the match panel).
        evidence.start_epoch(db, "second", now)
        for k in range(4):
            evidence.observe(db, {"id": f"sec-w{k}", "epoch": "second", "channel": "word", "action": 0, "bit": 0,
                                  "root": "alice"}, now)
        for k in range(3):
            evidence.observe(db, {"id": f"sec-r{k}", "epoch": "second", "channel": "reference", "action": 0,
                                  "bit": 0, "root": f"sec-ref{k}"}, now)
    finally:
        db.close()
    return console


def go(page, view):
    page.locator("#nav").get_by_role("link", name=re.compile("^" + re.escape(NAV[view]))).click()
    expect(page.locator(f"#{view}")).to_be_visible()
    expect(page.locator(f"html[data-view='{view}'][data-inflight='0']")).to_have_count(1)
    expect(page.locator(f"#{view} [aria-busy='true']")).to_have_count(0)


def record(page, channel, action, bit, root):
    page.locator("#obsChannel").select_option(channel)
    page.locator(f'#actionGroup input[value="{action}"]').check()
    page.locator(f'#bitGroup input[value="{bit}"]').check()
    page.locator("#obsRoot").fill(root)
    event_id = page.locator("#obsId").input_value()
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", event_id)
    page.locator("#observeBtn").click()
    expect(page.locator("#observeStatus")).to_contain_text(f"Recorded {event_id} as evidence #")
    return event_id


def open_graph(page, revision=1):
    page.get_by_role("button", name=f"View revision {revision}").click()
    expect(page.locator("#graphDetail")).to_contain_text(f"Revision #{revision}")


def test_owner_journey(console, new_page):
    SHOTS.mkdir(exist_ok=True)
    page = new_page(1280, 900)
    page.goto(console.url)
    # (a) the token is taken from the fragment and removed from the location bar
    expect(page.locator("#h-overview")).to_be_visible()
    assert "token" not in page.url and TOKEN not in page.evaluate("location.href")
    expect(page.locator("#writes")).to_have_text("allowed")
    expect(page.locator("#birth")).to_contain_text("commanded direction A")
    expect(page.locator("#inventory")).to_contain_text("epochs")

    # (b) teach: epoch, 6 camera + 6 word observations and 3 references
    go(page, "teach")
    expect(page.locator("#beliefs")).to_contain_text("No epoch yet")
    expect(page.locator("#actionGroup")).to_contain_text("0 · commanded direction A")
    expect(page.locator("#bitGroup")).to_contain_text("1 · observed as commanded direction B")
    page.locator("#epochName").fill("bench")
    page.locator("#epochStart").click()
    expect(page.locator("#epochStatus")).to_have_text("Started epoch bench.")
    expect(page.locator("#epochSelect")).to_have_value("bench")
    for _ in range(6):
        record(page, "camera", 0, 0, "cam1")
        record(page, "word", 0, 0, "alice")
    mechanism = page.locator('#probs [data-scope="mechanism"]')
    expect(mechanism.locator(".badge")).to_have_text("Unidentifiable")
    expect(page.locator("#unidentifiable")).to_contain_text("joint flip symmetry")
    expect(page.locator("#decisionValue")).to_have_text("test reference")
    references = [record(page, "reference", 0, 0, f"ref{k}") for k in range(3)]
    expect(mechanism.locator(".badge")).to_have_text("Identifiable")
    expect(mechanism).to_contain_text("P(inverted) = 0.0")
    expect(page.locator('#probs [data-scope="camera:cam1"] .badge')).to_have_text("Identifiable")
    expect(page.locator('#probs [data-scope="partner:alice"]')).to_be_visible()
    supporting = page.locator('#explain [data-scope="mechanism"] ul.supporting li')
    expect(supporting).to_have_count(3)
    for event_id in references:
        expect(page.locator(f'#explain [data-scope="mechanism"] ul.supporting li[data-id="{event_id}"]')).to_contain_text("reference")
    expect(page.locator('#explain [data-scope="mechanism"]')).to_contain_text("Remaining uncertainty: 3 reference reading(s)")
    expect(page.locator("#revisions")).to_contain_text(f"new evidence {references[1]}")
    expect(page.locator("#decisionValue")).to_have_text("act")
    page.locator("#recordDecision").click()
    expect(page.locator("#decisionStatus")).to_contain_text("Recorded decision “act” for bench.")

    # (c) the camera silently flips; the partner keeps its convention
    flipped = []
    for _ in range(6):
        flipped.append(record(page, "camera", 0, 1, "cam1"))
        record(page, "word", 0, 0, "alice")
    expect(page.locator("#changeScope")).to_have_text("camera:cam1")
    expect(page.locator("#change")).to_contain_text("Change suspected")
    badge = page.locator('#probs [data-scope="camera:cam1"] .suspect')
    expect(badge).to_contain_text("change suspected (llr ")
    expect(page.locator('#probs [data-scope="partner:alice"] .suspect')).to_have_count(0)
    partner_before = page.locator('#probs [data-scope="partner:alice"] .probnum').inner_text()
    expect(page.locator("#changeEvents li")).to_have_count(6)
    for event_id in flipped:
        expect(page.locator(f'#changeEvents li[data-id="{event_id}"]')).to_contain_text("camera · cam1 · action 0 → reading 1")
    expect(page.locator('#explain [data-scope="camera:cam1"] .split')).to_contain_text("6 read the declared mapping, 6 the flipped one")
    expect(page.locator("#decisionValue")).to_have_text("ask")
    expect(page.locator("#decision")).to_contain_text("change suspected in camera:cam1")
    page.locator("#recordDecision").click()
    expect(page.locator("#decisionStatus")).to_contain_text("question #1 is open under Questions")
    expect(page.locator("#openCount")).to_have_text("1 open")
    go(page, "questions")
    expect(page.locator("#pendingList")).to_contain_text("Did camera:cam1 change?")
    expect(page.get_by_role("button", name="No, nothing changed")).to_be_visible()
    page.get_by_role("button", name="Yes, it changed — relearn only that").click()
    expect(page.locator("#questionStatus")).to_contain_text("Question 1 resolved")
    expect(page.locator("#questionStatus")).to_contain_text("Only that factor is relearned")
    expect(page.locator("#pendingList")).to_contain_text("No open questions")
    expect(page.locator("#openCount")).to_have_text("")
    go(page, "teach")
    expect(page.locator("#confirmedChanges")).to_contain_text(re.compile(r"cam1 since #\d+"))
    expect(page.locator('#probs [data-scope="camera:cam1"] .suspect')).to_have_count(0)
    expect(page.locator('#probs [data-scope="partner:alice"] .probnum')).to_have_text(partner_before)
    expect(page.locator("#change")).to_contain_text("No change suspected")
    expect(page.locator("#decisionValue")).to_have_text("act")
    page.locator("#recordDecision").click()
    expect(page.locator("#decisionStatus")).to_contain_text("Recorded decision “act” for bench.")
    expect(page.locator('#probs [data-scope="partner:alice"] .probnum')).to_have_text(partner_before)

    # (d) forget one reference row through the confirmation dialog; beliefs follow
    go(page, "evidence")
    expect(page.locator("#evidenceTable tbody tr")).to_have_count(27)
    page.locator("#evChannel").select_option("reference")
    page.locator("#evApply").click()
    expect(page.locator("#evidenceTable tbody tr")).to_have_count(3)
    page.get_by_role("button", name=f"Forget evidence {references[0]}").click()
    dialog = page.locator("#confirmDialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("payload-free tombstone")
    page.locator("#confirmCancel").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("#evidenceTable tbody tr")).to_have_count(3)
    page.get_by_role("button", name=f"Forget evidence {references[0]}").click()
    page.locator("#confirmOk").click()
    expect(page.locator("#evidenceStatus")).to_contain_text(f"Forgot {references[0]}: 1 evidence row(s) deleted")
    expect(page.locator("#evidenceTable tbody tr")).to_have_count(2)
    go(page, "teach")
    expect(page.locator("#beliefs")).to_contain_text("26 stored")
    expect(page.locator('#explain [data-scope="mechanism"] ul.supporting li')).to_have_count(2)
    expect(page.locator("#revisions")).to_contain_text("evidence that has since been forgotten")
    assert references[0] not in page.locator("#teach").inner_text(), "the forgotten id must not appear on the Teach page"
    assert references[0] not in page.locator("#revisions").inner_html()
    assert "forgot " + references[0] not in page.locator("body").inner_text()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(SHOTS / "console-desktop.png"), full_page=True)

    # (d2) a new epoch fed with readings from the original mapping looks like the first one
    page.locator("#epochName").fill("second")
    page.locator("#epochStart").click()
    expect(page.locator("#epochStatus")).to_have_text("Started epoch second.")
    expect(page.locator("#matchCard")).to_be_hidden()
    for _ in range(4):
        record(page, "word", 0, 0, "alice")
    for k in range(3):
        record(page, "reference", 0, 0, f"ref-b{k}")
    expect(page.locator("#matchCard")).to_be_visible()
    expect(page.get_by_role("heading", name="Looks like an earlier setup")).to_be_visible()
    expect(page.locator("#matchBest")).to_contain_text("bench")
    expect(page.locator("#matchPanel table tbody tr")).to_have_count(1)
    expect(page.locator("#decisionValue")).to_have_text("ask")
    page.locator("#recordDecision").click()
    expect(page.locator("#decisionStatus")).to_contain_text("question #2 is open under Questions")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(SHOTS / "console-match-desktop.png"), full_page=True)
    go(page, "questions")
    expect(page.locator("#pendingList")).to_contain_text("This looks like bench")
    expect(page.locator("#pendingList")).to_contain_text("continue with that calibration?")
    expect(page.get_by_role("button", name="No, this is new")).to_be_visible()
    page.get_by_role("button", name="Continue there").click()
    expect(page.locator("#questionStatus")).to_contain_text("Question 2 resolved")
    expect(page.locator("#questionStatus")).to_contain_text("second now continues the calibration of bench")
    go(page, "teach")
    expect(page.locator("#epochSelect")).to_have_value("second")
    expect(page.locator("#linkedFrom")).to_contain_text("Continues an earlier setup")
    expect(page.locator("#linkedFrom")).to_contain_text("bench (")
    expect(page.locator("#probs [data-scope='camera:cam1']")).to_be_visible()   # bench's camera, continued
    expect(page.locator("#decisionValue")).to_have_text("act")

    # (e) graphs: simulate, learn, evaluate, promote, heatmap, recall
    go(page, "graphs")
    expect(page.locator("#datasetTable")).to_contain_text("No datasets yet")
    expect(page.locator("#learnBtn")).to_be_disabled()
    page.locator("#simBtn").click()
    expect(page.locator("#simStatus")).to_have_text("Simulated context world-a: train #1, validation #2, held-out #3.")
    expect(page.locator("#datasetTable tbody tr")).to_have_count(3)
    page.locator("#learnBtn").click()
    expect(page.locator("#learnStatus")).to_contain_text("Learned candidate revision #1")
    expect(page.locator("#graphDetail")).to_contain_text("Revision #1")
    expect(page.locator('#revisionGroups [data-context="world-a"]')).to_contain_text("candidate")
    page.locator("#gEvaluate").click()
    expect(page.locator("#graphStatus")).to_contain_text("held-out MSE")
    expect(page.locator("#graphEvaluation")).to_contain_text("#3")
    page.locator("#gPromote").click()
    expect(page.locator("#graphStatus")).to_contain_text("Gate passed: revision #1 is now the active graph of world-a.")
    expect(page.locator("#gateSummary")).to_contain_text("All 5 checks passed under gate-v1")
    checks = page.locator("#gateChecklist li")
    expect(checks).to_have_count(5)
    for name in ("independent_heldout", "heldout_gain", "retention", "byte_budget", "lineage_intact"):
        expect(page.locator(f'#gateChecklist li[data-check="{name}"]')).to_contain_text("Passed")
    expect(page.locator('#gateChecklist li[data-check="byte_budget"]')).to_contain_text("of 4096 bytes")
    expect(page.locator("#gPromote")).to_be_disabled()
    cells = page.locator("#heatmap rect.cell")
    expect(cells).to_have_count(24 * 24)
    colored = page.evaluate("[...document.querySelectorAll('#heatmap rect.cell')].filter(r => r.getAttribute('fill') !== '#ffffff').length")
    assert colored == 72
    signs = page.evaluate("""[...document.querySelectorAll('#heatmap rect.cell')].filter(r => +r.dataset.w !== 0)
        .every(r => { const [red, , blue] = r.getAttribute('fill').match(/\\d+/g).map(Number);
                      return (+r.dataset.w > 0) === (blue > red); })""")
    assert signs, "positive weights must be blue, negative rust"
    expect(page.locator("#heatSummary")).to_contain_text("72 non-zero edges")
    expect(page.locator("#revisionGroups")).to_contain_text("active")
    page.locator("#recallBtn").click()
    expect(page.locator("#recallVerdict")).to_have_text("known context: world-a")
    expect(page.locator("#recallResult")).to_contain_text("Selected-graph MSE")
    page.locator("#fmapBtn").click()
    expect(page.locator("#fmapStatus")).to_contain_text("proposed route(s). Coactivation is not connection.")
    expect(page.locator("#fmapResult table tbody tr")).to_have_count(20)
    expect(page.locator("#fmapCaveat")).to_contain_text("common stimulus")
    page.locator("#fmapLags").fill("1, x")
    page.locator("#fmapBtn").click()
    expect(page.locator("#fmapStatus")).to_have_text("Lags are whole numbers of at least 1, separated by commas.")
    page.locator("#fmapLags").fill("1,2")
    page.locator("#fmapTop").fill("3")
    page.locator("#fmapBtn").click()
    expect(page.locator("#fmapResult table tbody tr")).to_have_count(3)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(SHOTS / "console-graphs-desktop.png"), full_page=True)

    # (f) checkpoint create and verify
    go(page, "checkpoints")
    expect(page.locator("#ckTable")).to_contain_text("No checkpoints yet")
    page.locator("#ckPolicy").select_option("fast")
    page.locator("#ckCreate").click()
    expect(page.locator("#ckStatus")).to_contain_text("Checkpoint 1 created")
    expect(page.locator("#ckTable tbody tr")).to_have_count(1)
    expect(page.locator("#ckTable tbody tr")).to_contain_text("valid")
    page.get_by_role("button", name="Verify checkpoint 1").click()
    expect(page.locator("#ckStatus")).to_contain_text("Checkpoint 1 verified")
    expect(page.locator("#ckTable tbody tr")).not_to_contain_text("never")
    page.locator("#ckCreate").click()
    expect(page.locator("#ckStatus")).to_contain_text("Checkpoint 2 created")
    assert (console.path.parent / "owner.checkpoints").is_dir()
    page.locator("#ckKeep").fill("1")
    expect(page.locator("#ckPrune")).to_have_text("Prune (keep 1)")
    page.locator("#ckKeep").fill("0")
    page.locator("#ckPrune").click()
    expect(page.locator("#ckKeepStatus")).to_have_text("Keep must be a whole number of at least 1.")
    page.locator("#ckKeep").fill("1")
    page.locator("#ckPrune").click()
    page.locator("#confirmCancel").click()
    expect(page.locator("#ckTable tbody tr")).to_have_count(2)
    page.locator("#ckPrune").click()
    expect(page.locator("#confirmDialog")).to_contain_text("newest 1")
    page.locator("#confirmOk").click()
    expect(page.locator("#ckKeepStatus")).to_have_text("Kept 1 checkpoint(s); deleted 1.")
    expect(page.locator("#ckTable tbody tr").nth(1)).to_contain_text("deleted")
    page.locator("#ckRebuild").click()
    page.locator("#confirmOk").click()
    expect(page.locator("#ckKeepStatus")).to_have_text("Checkpoint 3 written; 0 stale archive(s) deleted; 1 pruned.")

    go(page, "memory")
    expect(page.locator("#deletionLog")).to_contain_text("evidence")
    expect(page.locator("#decisionLog tbody tr")).to_have_count(4)
    expect(page.locator("#decisionLog")).to_contain_text("camera:cam1")
    go(page, "overview")
    expect(page.locator("#development")).to_contain_text("teacher interventions")
    # (i) nothing was logged to the console, including CSP violations
    assert not page.problems, page.problems
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_locked_states_and_api_errors(console, new_page):
    page = new_page(1280, 900)
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/api/" in r.url else None)
    page.goto(console.base)
    expect(page.locator("#locked")).to_be_visible()
    expect(page.locator("#lockedText")).to_contain_text("Open the URL printed by nsf console")
    expect(page.locator("#nav")).to_be_hidden()
    assert requests == []
    page.goto(console.base + "#token=" + "w" * 40)
    expect(page.locator("#lockedText")).to_contain_text("session token was rejected")
    assert "token" not in page.url
    page.goto(console.url)
    go(page, "teach")
    page.locator("#epochName").fill("e1")
    page.locator("#epochStart").click()
    expect(page.locator("#epochStatus")).to_have_text("Started epoch e1.")
    page.locator("#obsRoot").fill("cam1")
    page.locator("#obsId").fill("fixed-id")
    page.locator("#observeBtn").click()
    expect(page.locator("#observeStatus")).to_contain_text("Recorded fixed-id")
    page.locator("#obsId").fill("fixed-id")
    page.locator("#observeBtn").click()
    expect(page.locator("#observeStatus")).to_contain_text("Duplicate of evidence #1")
    page.locator('#bitGroup input[value="1"]').check()
    page.locator("#observeBtn").click()
    expect(page.locator("#observeStatus")).to_have_text(
        "Evidence id 'fixed-id' is already bound to a different observation (HTTP 409)")
    expect(page.locator("#observeStatus")).to_have_class(re.compile("error"))
    page.locator("#obsRoot").fill("")
    page.locator("#observeBtn").click()
    expect(page.locator("#observeStatus")).to_have_text("Enter the root that produced this reading.")
    unexpected = [p for p in page.problems if "409" not in p and "403" not in p]
    assert not unexpected, unexpected


def test_phone_layout(seeded, new_page):
    SHOTS.mkdir(exist_ok=True)
    page = new_page(390, 844)
    page.goto(seeded.url)
    for view in VIEWS:
        go(page, view)
        if view == "graphs":
            open_graph(page)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), view
        controls = page.locator(f"#{view} :is(button, select, input:not([type=radio]):not([type=checkbox])), #nav a")
        for box in (b.bounding_box() for b in controls.all() if b.is_visible()):
            assert box["height"] >= 43.5, (view, box)
    go(page, "teach")
    beliefs, observe = page.locator("#h-beliefs").bounding_box(), page.locator("#h-observe").bounding_box()
    assert beliefs["y"] > observe["y"] and abs(beliefs["x"] - observe["x"]) < 2, "single column on a phone"
    expect(page.locator("#matchCard")).to_be_visible()
    page.screenshot(path=str(SHOTS / "console-phone.png"), full_page=True)
    page.locator("#epochSelect").select_option("bench")
    expect(page.locator("#probs [data-scope='camera:cam1'] .suspect")).to_be_visible()
    page.screenshot(path=str(SHOTS / "console-phone-change.png"), full_page=True)
    desktop = new_page(1280, 900)
    desktop.goto(seeded.url)
    go(desktop, "teach")
    epoch, observe = desktop.locator("#h-epoch").bounding_box(), desktop.locator("#h-observe").bounding_box()
    assert abs(epoch["y"] - observe["y"]) < 2 and observe["x"] > epoch["x"] + 200, "two columns from 1000px"
    nav, main = desktop.locator("#nav").bounding_box(), desktop.locator("#main").bounding_box()
    assert nav["x"] + nav["width"] <= main["x"], "navigation sits beside the content"
    assert not page.problems and not desktop.problems, (page.problems, desktop.problems)


def scan(page, found, name):
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    result = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))""")
    bad = [v for v in result if v["impact"] in ("serious", "critical")]
    if bad:
        found[name] = bad


@pytest.mark.parametrize("size", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
def test_accessibility_axe(seeded, new_page, size):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; the CSP is enforced in the other tests
    page.goto(seeded.url)
    expect(page.locator("#h-overview")).to_be_visible()
    page.add_script_tag(path=str(AXE))
    found = {}
    for view in VIEWS:
        go(page, view)
        if view == "graphs":
            open_graph(page)
            expect(page.locator("#heatmap")).to_be_visible()
        if view == "teach":  # the default epoch shows the match panel; "bench" shows the change badge
            expect(page.locator("#matchCard")).to_be_visible()
            scan(page, found, "teach-match")
            page.locator("#epochSelect").select_option("bench")
            expect(page.locator("#probs [data-scope='camera:cam1'] .suspect")).to_be_visible()
            expect(page.locator("html[data-inflight='0']")).to_have_count(1)
        if view == "graphs":
            page.locator("#fmapBtn").click()
            expect(page.locator("#fmapResult table")).to_be_visible()
        scan(page, found, view)
    go(page, "evidence")
    page.locator("#evidenceTable button.danger").first.click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    result = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id))""")
    if result:
        found["dialog"] = result
    page.keyboard.press("Escape")
    expect(page.locator("#confirmDialog")).to_be_hidden()
    assert found == {}


def focus_report(page, view):
    return page.evaluate("""(view) => {
        const scope = [...document.querySelectorAll('header, nav, #' + view)];
        const all = scope.flatMap(s => [...s.querySelectorAll('button, select, input, summary, a[href]')]).filter(e =>
          !e.disabled && e.checkVisibility() && getComputedStyle(e).visibility === 'visible');
        all.forEach((e, i) => e.dataset.k = i);
        return {buttons: all.filter(e => e.tagName === 'BUTTON').map(e => e.dataset.k), total: all.length};
    }""", view)


@pytest.mark.parametrize("view", VIEWS)
def test_keyboard_reaches_every_enabled_button(seeded, new_page, view):
    page = new_page(1280, 900)
    page.goto(seeded.url)
    go(page, view)
    if view == "graphs":
        open_graph(page, 2)
        expect(page.locator("#gPromote")).to_be_enabled()
    page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
    report = focus_report(page, view)
    assert report["buttons"], view
    page.evaluate("document.activeElement && document.activeElement.blur()")
    reached, bad = set(), []
    for _ in range(report["total"] + 40):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
            const e = document.activeElement, s = getComputedStyle(e);
            return {k: e.dataset.k ?? null, tag: e.tagName, id: e.id, outline: s.outlineStyle,
                    width: parseFloat(s.outlineWidth), focusVisible: e.matches(':focus-visible')};
        }""")
        if info["k"] is None:
            continue
        reached.add(info["k"])
        if not info["focusVisible"] or info["outline"] == "none" or info["width"] < 2:
            bad.append(info)
    assert not bad, bad
    missing = set(report["buttons"]) - reached
    assert not missing, page.evaluate(
        "ks => ks.map(k => document.querySelector(`[data-k='${k}']`).outerHTML.slice(0, 90))", sorted(missing))
    assert not page.problems, page.problems
