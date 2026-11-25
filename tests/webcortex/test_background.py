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
SRC = ROOT / "src" / "neuroscifi"
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
ENTRY_CALLS = {("autonomy", "idle"), ("scheduler", "tick"), ("scheduler", "work"), (None, "check_due"),
               (None, "claim_next")}


def _origin_is_background(node) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in BACKGROUND_ORIGIN or node.value.startswith(BACKGROUND_PREFIX)
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value).startswith(BACKGROUND_PREFIX)
    return False


def launch_sites(tree, module):
    """{"module:qualname"} of every call in `tree` that creates a task of a background origin or calls a background
    entry point (autonomy.idle, scheduler.tick / work, check_due, claim_next), and the activities passed to
    allowed() there."""
    sites, checks = set(), set()

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inner = scope          # a nested function belongs to the function that defines it
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
                if any(kw.arg == "origin" and _origin_is_background(kw.value) for kw in child.keywords):
                    sites.add(f"{module}:{scope}")
                if (owner, name) in ENTRY_CALLS or (None, name) in ENTRY_CALLS or \
                        (module.endswith(".autonomy") and owner is None and name == "idle"):
                    sites.add(f"{module}:{scope}")
                if name == "allowed" and len(child.args) >= 2 and isinstance(child.args[1], ast.Constant):
                    checks.add(child.args[1].value)
            visit(child, inner)
    visit(tree, "")
    return sites, checks


def _scan():
    sites, checks = set(), set()
    for path in sorted(SRC.rglob("*.py")):
        module = "neuroscifi." + ".".join(path.relative_to(SRC).with_suffix("").parts)
        found, asked = launch_sites(ast.parse(path.read_text()), module.removesuffix(".__init__"))
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


def test_every_launch_site_in_the_source_is_registered_and_every_registered_site_exists():
    sites, checks = _scan()
    registered = {s for spec in background.ACTIVITIES.values() for s in spec["sites"]} | set(background.RUNNERS)
    assert sites - registered == set(), "unregistered background launch sites: register them in background.py"
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
