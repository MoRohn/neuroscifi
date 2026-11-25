"""The owner's one switch for everything done unasked (`quiet`, background.py) and the one admission rule for
autonomous investigations (autonomy.admission), ADR-050.

Every registered cognition entry point is driven with quiet on and must hold its work after asking
background.allowed; the source and the systemd units are scanned for launch sites nobody registered."""
import ast
import json
from pathlib import Path
import threading
import time

import pytest

from neuroscifi import background
from neuroscifi.instance import checkpoints, store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import autonomy, curiosity, orchestrator, predictions, scheduler, state
from test_orchestrator import QUESTION, T0, _other, build, corpus, open_individual, one_page_at_a_time, page_hits, \
    serve, HookFetcher

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(background.__file__).resolve().parent      # the package under test (a mutated copy too), not the checkout
HOUR = 3600.0
COGNITION = sorted(k for k, v in background.ACTIVITIES.items() if v["kind"] == "cognition")


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


@pytest.fixture
def site(web):
    return serve(web)


@pytest.fixture
def asked(monkeypatch):
    """The activities background.allowed was asked about."""
    names, real = [], background.allowed

    def spy(db, activity):
        names.append(activity)
        return real(db, activity)
    monkeypatch.setattr(background, "allowed", spy)
    return names


def quiet(db, on=True, now=T0):
    return store.set_config(db, {"quiet": on}, now)


def ranked(*rows):
    """Stand-in for curiosity.rank: knowledge items (id, key, statement, information gain)."""
    out = [{"excluded": False, "kind": "stale", "mode": "fact_verification", "reason": "stale", "info_gain": gain,
            "target": {"knowledge_id": kid, "key": key, "statement": statement}}
           for kid, key, statement, gain in rows]
    return lambda db, now, limit=10: out


GOOD = (3, "ostra basin area", "The Ostra basin covers 9,400 square kilometres of lowland.", 0.5)


def queued(db, origin=None):
    sql = "SELECT id, origin FROM web_tasks WHERE status='queued'"
    return [r for r in db.execute(sql).fetchall() if origin is None or r[1] == origin]


# ---- 1. every registered cognition entry point, driven with quiet on --------------------------------------------

def drive_tick_monitors(db, path, site, monkeypatch):
    mon = scheduler.monitor_add(db, "Zephyr compiler latest version", T0, interval=2 * HOUR, authorized_by="owner")
    quiet(db)
    report = scheduler.tick(db, T0, None, queue=True)
    assert report["queued"] == [] and {"what": "monitors", "reason": background.QUIET_REASON} in report["skipped"]
    assert scheduler.monitor_list(db)[0]["next_run_at"] == T0 == mon["next_run_at"]      # stays due
    quiet(db, False)
    assert [q["origin"] for q in scheduler.tick(db, T0, None, queue=True)["queued"]] == [f"monitor:{mon['id']}"]


def drive_tick_prediction_refresh(db, path, site, monkeypatch):
    calls = []
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: calls.append(1) or [])
    autonomy.enable(db, T0)
    quiet(db)
    report = scheduler.tick(db, T0, None, queue=True)
    assert calls == [] and {"what": "prediction refreshes", "reason": background.QUIET_REASON} in report["skipped"]
    quiet(db, False)
    scheduler.tick(db, T0, None, queue=True)
    assert calls == [1]


def drive_tick_curiosity(db, path, site, monkeypatch):
    monkeypatch.setattr(curiosity, "rank", ranked(GOOD))
    autonomy.enable(db, T0)
    quiet(db)
    report = scheduler.tick(db, T0, None, queue=True)
    assert report["queued"] == [] and {"what": "curiosity", "reason": background.QUIET_REASON} in report["skipped"]
    quiet(db, False)
    assert len(scheduler.tick(db, T0, None, queue=True)["queued"]) == 1


@pytest.mark.parametrize("refreshed", [3, 2, None])
def test_the_tick_never_launches_curiosity_on_a_subject_its_refresh_just_queued(individual, monkeypatch, refreshed):
    """review0 note: under the unified rule the tick's curiosity could launch the subject a due prediction's refresh
    (check_due) had just queued in the same tick: two tasks on one subject. Matched by knowledge id or by key (another
    version of the item)."""
    db, _ = individual
    with store.write(db):
        db.execute("INSERT INTO web_knowledge(id,key,version,statement,status,confidence,uncertainty,freshness_class,"
                   "first_verified_at,last_verified_at,created_at,updated_at) VALUES(2,?,1,?,'superseded',0.6,0.4,"
                   "'stable',1,1,1,1)", (GOOD[1], GOOD[2]))
    monkeypatch.setattr(curiosity, "rank", ranked(GOOD))
    refresh = [] if refreshed is None else [{"prediction_id": 1, "action": "refresh_submitted",
                                             "task_id": "refresh-task", "knowledge_id": refreshed}]
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: refresh)
    autonomy.enable(db, T0)
    report = scheduler.tick(db, T0, None, queue=True)
    launched = [q for q in report["queued"] if q["origin"] == "curiosity"]
    if refreshed is None:
        assert len(launched) == 1                                     # the control: the subject is launched
        return
    assert launched == [] and report["queued"] == [{"task_id": "refresh-task", "origin": "refresh"}]
    assert any(s.get("reason") == scheduler.REFRESHED_THIS_TICK for s in report["skipped"])


def drive_worker_idle_investigation(db, path, site, monkeypatch):
    monkeypatch.setattr(curiosity, "rank", ranked(GOOD))
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)
    quiet(db)
    events = db.execute("SELECT count(*) FROM web_events").fetchone()[0]
    assert autonomy.idle(db, T0 + 1) == {"action": "quiet", "reason": background.QUIET_REASON}
    assert db.execute("SELECT count(*) FROM web_events").fetchone()[0] == events and queued(db) == []
    assert scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 2)["processed"] == []
    assert queued(db) == []
    quiet(db, False)
    assert autonomy.idle(db, T0 + 3)["action"] == "queued"


def drive_worker_claim_background(db, path, site, monkeypatch):
    def submit(text, origin, now):
        return orchestrator.submit(db, text, now, mode="quick_lookup", origin=origin)["id"]
    background_ids = [submit(f"Zephyr board {n}", origin, T0 + n)
                      for n, origin in enumerate(("curiosity", "refresh:7", "monitor:3"))]
    confirmed = submit("Zephyr board confirmed", "curiosity", T0 + 3)
    with store.write(db):      # what knowledge.apply_review records when the owner says yes to a proposal
        state.log_event(db, confirmed, "decision", {"decision": background.OWNER_CONFIRMED}, T0 + 3)
    owner = submit("Zephyr board asked", "cli", T0 + 4)
    interrupted = submit("Zephyr board interrupted", "curiosity", T0 + 5)
    with store.write(db):      # a background task whose worker died while it read
        db.execute("UPDATE web_tasks SET status='reading', lease_holder='dead', lease_expires_at=? WHERE id=?",
                   (T0 - 1, interrupted))
    quiet(db)
    claims = []
    while (row := scheduler.claim_next(db, "w", T0 + 10)) is not None:
        claims.append(row[0])
    assert sorted(claims) == sorted([confirmed, owner])
    assert all(t not in claims for t in [*background_ids, interrupted])
    quiet(db, False)
    assert scheduler.claim_next(db, "w", T0 + 11) is not None


def drive_task_background_run(db, path, site, monkeypatch):
    one_page_at_a_time(db)
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", origin="curiosity")

    def on(n):
        if n == 1:
            _other(path, lambda o: quiet(o))

    services = build(db, path, site)
    services.fetcher = HookFetcher(services.fetcher, on)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    assert done["status"] == "partial" and services.fetcher.pages == 1 and len(page_hits(site)) == 1
    assert background.STOPPED in done["result"]["limitations"] and "consolidation" not in done["result"]
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    stop = [e["detail"] for e in state.events(db, task["id"]) if e["detail"].get("stopped") == "quiet"]
    assert stop and stop[0]["reason"] == background.QUIET_REASON


@pytest.mark.parametrize("when", ["never", "synthesizing", "model_call"])
def test_quiet_turned_on_after_the_reading_stops_before_the_model_and_before_consolidation(individual, site,
                                                                                         monkeypatch, when):
    """review0 L2: quiet turned on while a background task synthesized or reasoned used to let it call the answer
    model and consolidate; both now ask quiet again first, and a refusal ends the run as any quiet stop does."""
    db, path = individual
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", origin="curiosity")
    reasoned, real_synthesize, real_reason = [], orchestrator._Run.synthesize, orchestrator._reason

    def synthesize(run):
        out = real_synthesize(run)
        if when == "synthesizing":
            _other(path, lambda o: quiet(o))
        return out

    def reason(db, run, report):
        reasoned.append(run.id)
        if when == "model_call":
            _other(path, lambda o: quiet(o))
        return real_reason(db, run, report)
    monkeypatch.setattr(orchestrator._Run, "synthesize", synthesize)
    monkeypatch.setattr(orchestrator, "_reason", reason)
    services = build(db, path, site)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    knowledge = db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0]
    if when == "never":                    # the control: the same run consolidates what it read
        assert done["status"] == "completed" and "consolidation" in done["result"] and knowledge > 0
        return
    assert done["status"] == "partial" and background.STOPPED in done["result"]["limitations"]
    assert reasoned == ([] if when == "synthesizing" else [task["id"]])
    assert "consolidation" not in done["result"] and knowledge == 0
    assert [e for e in state.events(db, task["id"]) if e["detail"].get("stopped") == "quiet"]


def test_a_background_task_the_owner_resumes_or_requeues_is_never_held(individual, site, monkeypatch):
    """review0 L6: `nsf web resume <curiosity task>` and `nsf web requeue <failed curiosity task>` by the owner were
    held by quiet (and the resumed run stopped by it), against ADR-050: owner-asked work is never held."""
    from types import SimpleNamespace
    from neuroscifi.webcortex import cli as wcli
    db, path = individual

    def failed_curiosity(text):
        tid = orchestrator.submit(db, text, T0, mode="quick_lookup", origin="curiosity")["id"]
        with store.write(db):
            state.update_task(db, tid, T0, status="failed", finished_at=T0, error="no pages")
        return tid
    requeued, resumed, other = failed_curiosity("Zephyr board 0"), failed_curiosity(QUESTION), \
        failed_curiosity("Zephyr board 2")
    with store.write(db):
        state.update_task(db, other, T0, status="partial")
    untouched = orchestrator.submit(db, "Zephyr board 3", T0, mode="quick_lookup", origin="curiosity")["id"]
    quiet(db)
    runs = []                     # resumed first: no other lease is held, so only quiet could stop it
    monkeypatch.setattr(wcli, "_run_task", lambda db, a, task_id, resume=False: runs.append(task_id) or
                        orchestrator.resume(db, task_id, build(db, path, site)))
    done = wcli._resume(db, SimpleNamespace(task=resumed), T0 + 1)
    assert runs == [resumed] and done["status"] == "completed"
    assert background.STOPPED not in done["result"]["limitations"]
    assert orchestrator.requeue(db, requeued, T0 + 2, origin="cli")["requeued"] == "same"
    child = orchestrator.requeue(db, other, T0 + 2, origin="curiosity")      # as Pocket passes the task's own origin
    assert child["requeued"] == "child" and child["origin"] == "curiosity"
    claimed = []
    while (row := scheduler.claim_next(db, "w", T0 + 3)) is not None:
        claimed.append(row[0])
    assert sorted(claimed) == sorted([requeued, child["id"]]) and untouched not in claimed   # nobody asked: held
    decisions = [e["detail"].get("decision") for t in (requeued, child["id"], resumed) for e in state.events(db, t)]
    assert decisions.count(background.OWNER_ASKED) == 3


@pytest.mark.parametrize("decision", [background.OWNER_ASKED, background.OWNER_CONFIRMED, None])
def test_a_background_task_the_owner_asked_for_is_labelled_so_in_its_answer(individual, site, monkeypatch, decision):
    """review0b N12: a background task the owner resumed or queued again kept the "autonomous" label in the answer
    note (no note at all), like one nobody saw; it now reads as owner-confirmed, as a proposal the owner said yes to."""
    from neuroscifi.webcortex import reasoning
    db, path = individual
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", origin="curiosity")
    if decision is not None:
        with store.write(db):
            state.log_event(db, task["id"], "decision", {"decision": decision}, T0)
    labels, real_apply, real_degraded = [], reasoning.apply, reasoning._degraded
    monkeypatch.setattr(reasoning, "apply", lambda *a, **k: labels.append(k["asked"]) or real_apply(*a, **k))
    monkeypatch.setattr(reasoning, "_degraded", lambda *a, **k: labels.append(k["asked"]) or real_degraded(*a, **k))
    services = build(db, path, site)
    try:
        orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    assert labels and set(labels) == {"autonomous" if decision is None else "confirmed"}


@pytest.mark.parametrize("stopped", ["limitation", "event", None])
def test_a_refresh_stopped_by_quiet_leaves_its_prediction_due(individual, stopped):
    """review0b N5: a refresh quiet stopped ends 'partial' with nothing consolidated; check_due settled its prediction
    'inconclusive' ("the refresh found no evidence") and calibration counted it. The prediction now stays due and
    the next check queues a new refresh."""
    db, _ = individual
    state.set_config(db, {"enabled": True}, T0)
    with store.write(db):
        db.execute("INSERT INTO web_knowledge(id,key,version,statement,status,confidence,uncertainty,freshness_class,"
                   "first_verified_at,last_verified_at,created_at,updated_at) VALUES(1,?,1,?,'provisional',0.7,0.3,"
                   "'stable',1,1,1,1)", (GOOD[1], GOOD[2]))
    pid = predictions.add(db, 1, "it will still hold", T0 + 10, T0)
    task = orchestrator.submit(db, "refresh the Ostra basin", T0 + 11, mode="quick_lookup", origin="refresh:1")
    with store.write(db):
        db.execute("UPDATE web_predictions SET task_id=? WHERE id=?", (task["id"], pid))
        state.update_task(db, task["id"], T0 + 12, status="partial", finished_at=T0 + 12,
                          result={"limitations": [background.STOPPED] if stopped == "limitation" else [],
                                  "answer": []})
        if stopped == "event":
            state.log_event(db, task["id"], "decision", {"stopped": "quiet", "reason": background.QUIET_REASON},
                            T0 + 12)
    actions = predictions.check_due(db, T0 + 20)
    row = db.execute("SELECT outcome, task_id FROM web_predictions WHERE id=?", (pid,)).fetchone()
    if stopped is None:                       # the control: a refresh that ran and found nothing settles it
        assert actions[0]["action"] == "checked" and row[0] == "inconclusive"
        return
    assert actions == [{"prediction_id": pid, "action": "skipped",
                        "reason": "its refresh was stopped by quiet; the prediction stays due"}]
    assert row == (None, None)
    again = predictions.check_due(db, T0 + 21)
    assert again[0]["action"] == "refresh_submitted" and again[0]["task_id"] != task["id"]


@pytest.mark.parametrize("kind", ["stale", "hypothesis_prediction", "prediction"])
def test_the_unified_tick_launches_a_pooled_subject_only_in_the_kind_the_timer_ranked(individual, monkeypatch,
                                                                                     kind):
    """review0b (tick dedupe, partial): candidates() keeps one item per subject, so a due prediction or a hypothesis
    on a subject in the timer's pool could take it, and the tick launched it in a shape it never launched before
    (a 'prediction' or a fact-verified hypothesis)."""
    db, _ = individual
    monkeypatch.setattr(curiosity, "rank", ranked(GOOD))
    item = {"kind": kind, "key": GOOD[1], "statement": GOOD[2], "objective": "Ostra basin area",
            "mode": "fact_verification", "info_gain": 0.9, "cost": 1.0, "priority": 1.0, "signals": {},
            "knowledge_id": GOOD[0], "owner": False, "depth": 0, "reason": "test"}
    monkeypatch.setattr(autonomy, "candidates", lambda db, now, limit=10: {"chosen": [item], "refused": [],
                                                                          "min_gain": 0.0})
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: [])
    autonomy.enable(db, T0)
    report = scheduler.tick(db, T0, None, queue=True)
    launched = [q for q in report["queued"] if q["origin"] == "curiosity"]
    if kind == "stale":                       # the control: the timer's own kind is launched
        assert len(launched) == 1
        return
    assert launched == [] and any(s.get("target", {}).get("key") == GOOD[1] for s in report["skipped"])


def test_background_origins_are_matched_with_case_everywhere(individual):
    """review0 note: ORIGIN_SQL matched with LIKE (no case) where held_origin and is_autonomous match with case."""
    db, _ = individual
    for origin in ("curiosity", "refresh:7", "monitor:3", "Refresh:7", "MONITOR:3", "Curiosity", "cli"):
        tid = orchestrator.submit(db, f"Zephyr {origin}", T0, mode="quick_lookup", origin=origin)["id"]
        held_sql = db.execute(f"SELECT 1 FROM web_tasks WHERE id=? AND {background.HELD_SQL}", (tid,)).fetchone()
        assert (held_sql is not None) == background.held_origin(origin) == background.held_task(db, tid, origin), \
            origin


DRIVERS = {"tick.monitors": drive_tick_monitors, "tick.prediction_refresh": drive_tick_prediction_refresh,
           "tick.curiosity": drive_tick_curiosity, "worker.idle_investigation": drive_worker_idle_investigation,
           "worker.claim_background": drive_worker_claim_background,
           "task.background_run": drive_task_background_run}


def test_every_registered_cognition_activity_has_a_driver():
    """A new cognition activity cannot be registered without a test that drives it with quiet on."""
    assert sorted(DRIVERS) == COGNITION


@pytest.mark.parametrize("activity", COGNITION)
def test_quiet_holds_each_background_entry_point_after_asking_the_one_check(activity, individual, site, monkeypatch,
                                                                          asked):
    db, path = individual
    DRIVERS[activity](db, path, site, monkeypatch)
    assert activity in asked, f"{activity}: its entry point never asked background.allowed"


def test_maintenance_and_delivery_keep_running_and_an_unregistered_activity_is_refused(individual):
    db, _ = individual
    quiet(db)
    for name, spec in background.ACTIVITIES.items():
        assert background.allowed(db, name) == ((False, background.QUIET_REASON) if spec["kind"] == "cognition"
                                                else (True, None)), name
    with pytest.raises(KeyError):
        background.allowed(db, "dreaming.new_mechanism")
    quiet(db, False)
    assert all(background.allowed(db, name) == (True, None) for name in background.ACTIVITIES)


# ---- 2. the source and the units: no launch site that nobody registered -------------------------------------------

BACKGROUND_ORIGIN = ("curiosity",)
BACKGROUND_PREFIX = ("refresh:", "monitor:")
ENTRY_MODULES = {"autonomy": {"idle"}, "scheduler": {"tick", "work"}}
ENTRY_ANYWHERE = {"check_due", "claim_next"}


def _origin_is_background(node) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in BACKGROUND_ORIGIN or node.value.startswith(BACKGROUND_PREFIX)
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value).startswith(BACKGROUND_PREFIX)
    return False


def _origin_is_fixed(node) -> bool:
    """A literal origin (a string, None, or an f-string with a fixed prefix): what it names can be read here."""
    if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, str)):
        return True
    return isinstance(node, ast.JoinedStr) and bool(node.values) and isinstance(node.values[0], ast.Constant)


def launch_sites(tree, module, direct=None):
    """{"module:qualname"} of every function in `tree` that creates a task of a background origin, passes on an origin
    it did not write out (a variable, an attribute: it may be a background one), or refers to a background entry
    point (autonomy.idle, scheduler.tick / work, check_due, claim_next) - called, handed to a thread or
    functools.partial, or imported under another name - and the activities passed to allowed() there (review0 L8).
    `direct`, a set when given, also collects the functions found for a background origin written out or an entry
    point, not for a forwarded origin alone: a forwarder (background.FORWARDERS) may be nothing else (review0b N7)."""
    sites, checks = set(), set()
    direct = set() if direct is None else direct
    owners = {name: name for name in ENTRY_MODULES}     # local name -> the entry module it is bound to
    entries = set(ENTRY_ANYWHERE) | ({"idle"} if module.endswith(".autonomy") else set())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = (node.module or "").rpartition(".")[2]
            for alias in node.names:
                if alias.name in ENTRY_MODULES:
                    owners[alias.asname or alias.name] = alias.name
                if alias.name in ENTRY_MODULES.get(source, ()) or alias.name in ENTRY_ANYWHERE:
                    entries.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.rpartition(".")[2] in ENTRY_MODULES:
                    owners[alias.asname or alias.name.rpartition(".")[2]] = alias.name.rpartition(".")[2]

    def refers(node):
        if isinstance(node, ast.Attribute):
            owner = node.value.id if isinstance(node.value, ast.Name) else None
            return node.attr in ENTRY_ANYWHERE or node.attr in ENTRY_MODULES.get(owners.get(owner), ())
        return isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in entries

    def visit(node, scope, in_function=False):
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
                if in_function:            # a nested function belongs to the function that defines it
                    inner = scope
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if any(kw.arg == "origin" and (_origin_is_background(kw.value) or not _origin_is_fixed(kw.value))
                       for kw in child.keywords):
                    sites.add(f"{module}:{scope}")
                if any(kw.arg == "origin" and _origin_is_background(kw.value) for kw in child.keywords):
                    direct.add(f"{module}:{scope}")
                if name == "allowed" and len(child.args) >= 2 and isinstance(child.args[1], ast.Constant):
                    checks.add(child.args[1].value)
            if refers(child):
                sites.add(f"{module}:{scope}")
                direct.add(f"{module}:{scope}")
            visit(child, inner, in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
    visit(tree, "")
    return sites, checks


def _scan(direct=None):
    sites, checks = set(), set()
    for path in sorted(SRC.rglob("*.py")):
        module = "neuroscifi." + ".".join(path.relative_to(SRC).with_suffix("").parts)
        found, asked = launch_sites(ast.parse(path.read_text()), module.removesuffix(".__init__"), direct)
        sites |= found
        checks |= asked
    return sites, checks


def test_the_scanner_finds_a_launch_nobody_registered():
    """The scan itself: a new mechanism queueing curiosity research, or calling an entry point, is found."""
    sample = ("def dream(db, now):\n    orchestrator.submit(db, 'x', now, origin='curiosity')\n"
              "class Muse:\n    def wander(self, db):\n        return autonomy.idle(db, 0)\n"
              "def refresh_all(db, k):\n    knowledge.refresh(db, k, 0, origin=f'refresh:{k}')\n"
              "def owner(db):\n    orchestrator.submit(db, 'x', 0, origin='cli')\n")
    sites, _ = launch_sites(ast.parse(sample), "neuroscifi.new")
    assert sites == {"neuroscifi.new:dream", "neuroscifi.new:Muse.wander", "neuroscifi.new:refresh_all"}
    # review0 L8: an entry point handed to a thread or to functools.partial, imported under another name, or an
    # origin that is not written out
    sample = ("import functools, threading\nfrom . import scheduler as s\nfrom .predictions import check_due as due\n"
              "from .autonomy import idle as wander\n"
              "def spawn(path):\n    threading.Thread(target=scheduler.work, args=(path,)).start()\n"
              "def later(db):\n    return functools.partial(s.tick, db)\n"
              "def renamed(db, now):\n    return due(db, now)\n"
              "def drift(db):\n    return wander(db, 0)\n"
              "def forward(db, origin):\n    orchestrator.submit(db, 'x', 0, origin=origin)\n"
              "def stored(db, task):\n    orchestrator.submit(db, 'x', 0, origin=task['origin'])\n"
              "def fixed(db, k):\n    orchestrator.submit(db, 'x', 0, origin=f'user:{k}')\n"
              "def owner(db):\n    orchestrator.submit(db, 'x', 0, origin=None)\n")
    sites, _ = launch_sites(ast.parse(sample), "neuroscifi.new")
    assert sites == {f"neuroscifi.new:{f}" for f in ("spawn", "later", "renamed", "drift", "forward", "stored")}
    # review0b N7: a function that forwards an origin and also launches work itself is not only a forwarder
    sample = ("def forward(db, origin):\n    orchestrator.submit(db, 'x', 0, origin=origin)\n"
              "def forward_and_wander(db, origin):\n    orchestrator.submit(db, 'x', 0, origin=origin)\n"
              "    return autonomy.idle(db, 0)\n"
              "def forward_and_wonder(db, origin):\n    orchestrator.submit(db, 'x', 0, origin=origin)\n"
              "    orchestrator.submit(db, 'y', 0, origin='curiosity')\n")
    direct = set()
    sites, _ = launch_sites(ast.parse(sample), "neuroscifi.new", direct)
    assert sites == {f"neuroscifi.new:{f}" for f in ("forward", "forward_and_wander", "forward_and_wonder")}
    assert direct == {"neuroscifi.new:forward_and_wander", "neuroscifi.new:forward_and_wonder"}


def test_every_launch_site_in_the_source_is_registered_and_every_registered_site_exists():
    direct = set()
    sites, checks = _scan(direct)
    launching = {s for spec in background.ACTIVITIES.values() for s in spec["sites"]} | set(background.RUNNERS)
    registered = launching | set(background.FORWARDERS)
    assert sites - registered == set(), "unregistered background launch sites: register them in background.py"
    # review0b N7: a forwarder is exempt only for what it forwards; a background origin it writes out, or an entry
    # point it calls, must be registered as an activity's site or a runner
    assert direct - launching == set(), "a forwarder also launches background work itself: register that site"
    for site_name in registered:          # the registry names real functions (no stale entry)
        module, _, qualname = site_name.partition(":")
        path = SRC.joinpath(*module.split(".")[1:]).with_suffix(".py")
        tree = ast.parse(path.read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names |= {f"{node.name}.{f.name}" for f in node.body if isinstance(f, ast.FunctionDef)}
            elif isinstance(node, ast.FunctionDef):
                names.add(node.name)
        assert qualname in names, site_name
    # every cognition activity is asked about somewhere in the source, and nothing asks about an unknown one
    assert set(COGNITION) <= checks and checks <= set(background.ACTIVITIES)


def test_a_measurement_only_package_is_imported_by_nothing_else_in_the_product():
    """The cognitive benchmark runs real entry points on staged individuals (RUNNERS). That is safe only while no
    service, timer or other product module can reach it: nothing in src outside the package imports it."""
    for package in background.MEASUREMENT_ONLY:
        assert SRC.joinpath(*package.split(".")[1:]).is_dir(), package
        inside = SRC.joinpath(*package.split(".")[1:])
        parent, _, leaf = package.rpartition(".")
        importers = []
        for path in sorted(SRC.rglob("*.py")):
            if inside in path.parents:
                continue
            module = "neuroscifi." + ".".join(path.relative_to(SRC).with_suffix("").parts)
            module_package = module.removesuffix(".__init__") if path.name == "__init__.py" else module.rpartition(".")[0]
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:      # relative: resolve against this module's package
                        parts = module_package.split(".")
                        base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                    names = [base] + [f"{base}.{a.name}" for a in node.names]
                else:
                    continue
                if any(n == package or n.startswith(package + ".") for n in names):
                    importers.append(module)
        assert importers == [], f"{package} is measurement only; imported by {importers}"


def test_every_systemd_timer_and_path_unit_is_registered():
    units = {p.name for p in (ROOT / "deploy" / "systemd").iterdir() if p.suffix in (".timer", ".path")}
    assert units == set(background.UNITS)
    assert all(a in background.ACTIVITIES for acts in background.UNITS.values() for a in acts)


# ---- 3. the setting: owner only, recorded, carried by restore, above the other switches -----------------------------

def test_quiet_is_off_by_default_set_only_by_the_owners_cli_and_recorded(individual, capsys):
    from neuroscifi.cli import main
    db, path = individual
    assert store.config(db).quiet is False and background.quiet(db) is False
    assert main(["quiet", "on", "--db", str(path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["quiet"] is True and shown["summary"].startswith("Quiet: nothing runs unless you ask")
    assert background.quiet(db)
    assert main(["config", "--no-quiet", "--db", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] == ["quiet"] and not background.quiet(db)
    log = [json.loads(e) for (e,) in db.execute("SELECT effects FROM deletion_log WHERE target_kind='setting' AND "
                                                "target_id='quiet' ORDER BY id")]
    assert [(e["from"], e["to"]) for e in log] == [(False, True), (True, False)]
    assert main(["quiet", "status", "--db", str(path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["quiet"] is False and {a["activity"] for a in status["held_while_quiet"]} == set(COGNITION)
    assert store.regulation(db)["config"]["quiet"] is False           # `nsf regulation` shows it


def test_no_other_surface_can_change_quiet():
    """The console's switches, Pocket's routes and the MCP tools have no way to set it (ADR-050)."""
    from neuroscifi.console import server as console
    from neuroscifi.webcortex import mcp
    assert "quiet" not in console.WEB_SWITCHES
    names = {t["name"] for t in mcp._tools()}
    assert names == {"web_search", "web_research", "web_task", "web_evidence", "web_knowledge"}
    assert not any(word in n for n in names for word in ("config", "forget", "quiet", "autonomy", "monitor"))
    pocket = (SRC / "pocket" / "server.py").read_text() + (SRC / "pocket" / "research_routes.py").read_text()
    assert "set_config" not in pocket and "quiet" not in pocket


def test_a_damaged_quiet_value_holds_background_work(individual):
    db, _ = individual
    with store.write(db):
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0]) | {"quiet": "no"}
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    assert background.quiet(db) is True
    assert any("quiet" in p for p in store.setting_problems(db))


def test_a_restore_carries_the_owners_quiet(individual, tmp_path):
    db, path = individual
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    quiet(db, now=T0 + 2)
    report = checkpoints.restore(db, archive["id"], tmp_path / "restored.sqlite", T0 + 3)
    assert report["settings_carried"]["quiet"] == {"from": False, "to": True}
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert background.quiet(copy) is True
    finally:
        copy.close()


@pytest.mark.parametrize("archived,live", [("legacy", "unified"), ("unified", "legacy")])
def test_a_restore_carries_the_owners_tick_admission_rule(individual, tmp_path, archived, live):
    """review0 note: restoring an archive taken under tick_admission "legacy" brought the looser rule back. The live
    value wins, as for quiet."""
    db, path = individual
    state.set_config(db, {"tick_admission": archived}, T0)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    state.set_config(db, {"tick_admission": live}, T0 + 2)
    report = checkpoints.restore(db, archive["id"], tmp_path / "restored.sqlite", T0 + 3)
    assert report["settings_carried"]["tick_admission"] == {"from": archived, "to": live}
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert state.get_config(copy).tick_admission == live
        assert state.get_config(copy).enabled is True                     # the other web settings are the archive's
    finally:
        copy.close()


def test_quiet_sits_above_the_other_switches_and_never_holds_what_the_owner_asks(individual, site):
    db, path = individual
    autonomy.enable(db, T0)
    mon = scheduler.monitor_add(db, "Zephyr compiler latest version", T0, interval=2 * HOUR, authorized_by="owner")
    before = (state.get_config(db).to_json(), autonomy.policy(db)["record"], scheduler.monitor_list(db))
    quiet(db)
    asked = orchestrator.submit(db, QUESTION, T0 + 1, mode="fact_verification")
    out = scheduler.work(path, once=True, fixture=site, providers=[corpus()], clock=lambda: T0 + 1, idle=False)
    assert [p["task_id"] for p in out["processed"]] == [asked["id"]]
    assert state.task(db, asked["id"])["status"] == "completed"
    quiet(db, False)
    assert (state.get_config(db).to_json(), autonomy.policy(db)["record"], scheduler.monitor_list(db)) == before
    assert autonomy.enabled(db) and mon["status"] == "active"


def test_presence_says_quiet_in_plain_words_in_both_scopes(individual):
    from neuroscifi.presence import aggregator
    db, path = individual
    aggregator._cache.clear()
    calm = aggregator.derive(db, path, T0)
    assert "background" not in calm["sources"]
    quiet(db)
    aggregator._cache.clear()
    for family in (None, "ab" * 8):
        s = aggregator.derive(db, path, T0, family=family)
        assert s["sources"]["background"] == "quiet"
        assert s["primary"]["activity"] == "resting" and s["primary"]["detail"] == "Quiet: nothing runs unless you ask"
    aggregator._cache.clear()


def test_a_running_worker_honours_quiet_within_one_poll(individual, site):
    """Measured: from the commit of `nsf quiet on` (another connection) to the worker's recorded hold, and from
    `nsf quiet off` to the held task being claimed, each within one poll interval (plus scheduling slack)."""
    db, path = individual
    poll = 0.5
    stop = threading.Event()
    worker = threading.Thread(target=scheduler.work, args=(path,), daemon=True,
                              kwargs={"stop": stop, "poll": poll, "sleep": stop.wait, "fixture": site,
                                      "providers": [corpus()], "idle": False})
    worker.start()
    try:
        time.sleep(poll)
        started = time.monotonic()
        _other(path, lambda o: quiet(o))
        while not db.execute("SELECT 1 FROM web_events WHERE json_extract(detail,'$.worker')=?",
                             ("holding background work: quiet is on",)).fetchone():
            assert time.monotonic() - started < poll + 2.0, "the worker did not notice quiet within one poll"
            time.sleep(0.02)
        held = orchestrator.submit(db, "Zephyr compiler boards", T0, mode="quick_lookup", origin="curiosity")
        time.sleep(3 * poll)
        assert state.task(db, held["id"])["status"] == "queued"
        released = time.monotonic()
        _other(path, lambda o: quiet(o, False))
        while state.task(db, held["id"])["status"] == "queued" and \
                state.task(db, held["id"]).get("lease_holder") is None:
            assert time.monotonic() - released < poll + 2.0, "the held task was not claimed within one poll"
            time.sleep(0.02)
    finally:
        stop.set()
        worker.join(30)


# ---- 4. one admission rule for both autonomy paths ----------------------------------------------------------------

def test_both_autonomy_paths_admit_through_the_one_function(individual, monkeypatch):
    db, _ = individual
    paths, real = [], autonomy.admission
    monkeypatch.setattr(autonomy, "admission", lambda *a, **k: paths.append(k["path"]) or real(*a, **k))
    monkeypatch.setattr(curiosity, "rank", ranked(GOOD))
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)
    scheduler.tick(db, T0, None, queue=True)
    autonomy.idle(db, T0 + 1)
    assert paths == ["tick", "idle"]


def _tick_with(db, monkeypatch, rule):
    """The tick's launches over three knowledge items: one below the unified minimum gain (0.03 bits: above the
    tick's old floor of 0.02), one investigated autonomously an hour ago (cooldown), and one that clears both."""
    low = (1, "zephyr compiler version", "The Zephyr compiler 4.2.0 is the current stable release.", 0.03)
    cooled = (2, "varnel river length", "The Varnel River is 412 kilometres long and drains the Ostra basin.", 0.5)
    monkeypatch.setattr(curiosity, "rank", ranked(low, cooled, GOOD))
    state.set_config(db, {"tick_admission": rule}, T0)
    with store.write(db):
        state.log_event(db, None, "decision", {"autonomy": {"key": cooled[1]}}, T0 - HOUR)
    report = scheduler.tick(db, T0, None, queue=True)
    keys = []
    for q in report["queued"]:
        detail = next(e["detail"] for e in state.events(db, q["task_id"]) if e["kind"] == "decision"
                      and "autonomy" in e["detail"])
        keys.append((detail["autonomy"]["key"] if isinstance(detail["autonomy"], dict) else detail["key"],
                     (detail["autonomy"] if isinstance(detail["autonomy"], dict) else detail)["admission"]["rule"]))
    return report, keys


def test_the_unified_rule_refuses_what_the_tick_used_to_launch(individual, monkeypatch):
    db, _ = individual
    autonomy.enable(db, T0)
    report, keys = _tick_with(db, monkeypatch, "unified")
    assert report["admission"] == "unified" and keys == [("ostra basin area", "unified")]
    reasons = {s["target"]["key"]: s["reason"] for s in report["skipped"] if "target" in s}
    assert "minimum 0.05" in reasons["zephyr compiler version"]
    assert "within the last" in reasons["varnel river length"]
    # the admitted subject cools down: the idle investigator does not take it again
    assert "ostra basin area" in autonomy._recent(db, T0, autonomy.policy(db)["cooldown"])


def test_the_legacy_setting_restores_the_old_tick_rule_for_one_release(individual, monkeypatch):
    db, _ = individual
    state.set_config(db, {"autonomous": True}, T0)       # the switch alone, no recorded enable: the old tick ran
    report, keys = _tick_with(db, monkeypatch, "legacy")
    assert report["admission"] == "legacy"
    assert keys == [("zephyr compiler version", "legacy"), ("varnel river length", "legacy"),
                    ("ostra basin area", "legacy")]
    assert autonomy._recent(db, T0, 60.0) == set()      # legacy launches never fed the cooldown, as before


def test_the_unified_tick_needs_the_recorded_enable_and_skips_templates(individual, monkeypatch):
    db, _ = individual
    template = (4, "openalex brain neurons", 'OpenAlex record: "Yes, the human brain has around 86 billion '
                'neurons", dated 2025-02-06.', 0.5)
    monkeypatch.setattr(curiosity, "rank", ranked(template, GOOD))
    state.set_config(db, {"autonomous": True}, T0)
    report = scheduler.tick(db, T0, None, queue=True)
    assert report["queued"] == [] and any(s.get("what") == "curiosity" and "recorded owner switch" in s["reason"]
                                          for s in report["skipped"])
    autonomy.enable(db, T0)
    report = scheduler.tick(db, T0, None, queue=True)
    assert len(report["queued"]) == 1
    assert any(s["target"]["knowledge_id"] == 4 and "template" in s["reason"] for s in report["skipped"]
               if "target" in s)


def test_the_tick_admission_setting_is_validated(individual):
    db, _ = individual
    assert state.get_config(db).tick_admission == "unified"
    with pytest.raises(ValidationError):
        state.set_config(db, {"tick_admission": "loose"}, T0)
    with pytest.raises(ValidationError):
        autonomy.admission(db, T0, path="idle", rule="legacy")
