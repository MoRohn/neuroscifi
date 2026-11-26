"""One owner switch for everything the individual does unasked (ADR-050).

`quiet` is an instance setting (InstanceConfig.quiet; `nsf quiet on|off|status`, or `nsf config --quiet` /
`--no-quiet`). Off by default: behaviour is what it was before the switch existed. On: nothing that does research or
investigation unasked runs. The setting is changed only by the owner (the CLI; no console, Pocket, voice or MCP
route sets it), every change is a row in the deletion log like every other owner setting, and a restore carries it
into the copy (checkpoints.CARRIED_SETTINGS).

Every background entry point asks allowed(db, activity) for an activity named in ACTIVITIES; a name that is not
registered raises KeyError, so a new mechanism cannot start without being registered and classified.
tests/test_background.py drives each registered cognition entry point with quiet on, and scans the source and the
systemd units for launch sites nobody registered. Kinds:

  cognition    research or investigation nobody asked for at that moment: held while quiet is on
  maintenance  upkeep of data and resources the owner configured (evidence expiry and its consolidated readings,
               cache retention, unloading idle model services, the worker's heartbeat): keeps running. It starts no
               research, sends no search query and calls no model
  delivery     telling the owner about work that already finished (a finding question, a Pocket push): keeps running
  owner        started by the owner (a curiosity proposal waits for the owner's yes): never held

The switch sits above the others. It never changes the stored values of the `autonomous` switch, the autonomy
enable record, monitors or timers: turning quiet off leaves exactly what they say. Work the owner asks for (console,
CLI, Pocket, voice, MCP, research nodes, a curiosity proposal the owner confirmed, a background task the owner
resumed or queued again) is never held. A background task already running when quiet is turned on stops at its next
stop check (before every network step, as the owner's cancel does, and again before the model is called and before
consolidation), ends 'partial', and nothing it read is consolidated (no model call after the stop). Queued background
tasks stay queued and run after quiet is turned off.
"""
from __future__ import annotations

SETTING = "quiet"
QUIET_REASON = ("quiet is on: the owner stopped background work, nothing runs unless the owner asks "
                "(`nsf quiet off` resumes it)")
STOPPED = "stopped by quiet (the owner stopped background work): nothing was consolidated into knowledge"
# A proposal the owner confirmed (knowledge.apply_review) is owner-asked work although its origin is 'curiosity'.
OWNER_CONFIRMED = "proposal authorised by the owner"
# So is a background task the owner resumed or queued again (`nsf web resume|requeue`, the console, Pocket, voice):
# asked_by_owner records it with the task.
OWNER_ASKED = "resumed or requeued by the owner"
# Task origins of background work (orchestrator.is_autonomous plus monitors' scheduled runs). GLOB, not LIKE: matched
# with case, exactly as held_origin and orchestrator.is_autonomous match them.
ORIGIN_SQL = "(origin='curiosity' OR origin GLOB 'refresh:*' OR origin GLOB 'monitor:*')"
HELD_SQL = (f"({ORIGIN_SQL} AND id NOT IN (SELECT task_id FROM web_events WHERE kind='decision' AND task_id IS NOT "
            f"NULL AND json_extract(detail,'$.decision') IN ('{OWNER_CONFIRMED}','{OWNER_ASKED}')))")

KINDS = ("cognition", "maintenance", "delivery", "owner")

# name -> kind, what it does, and the functions ("module:qualname") that start it or check it. A function that
# starts background work and is not listed here fails tests/test_background.py.
ACTIVITIES = {
    "worker.idle_investigation": {
        "kind": "cognition", "what": "the research worker's idle investigation when nothing is queued "
                                     "(autonomy.idle; also `nsf web autonomy idle`)",
        "sites": ("neuroscifi.webcortex.autonomy:idle", "neuroscifi.webcortex.autonomy:cli",
                  "neuroscifi.webcortex.scheduler:work")},
    "worker.claim_background": {
        "kind": "cognition", "what": "the worker claiming, or resuming after a crash, a queued background task "
                                     "(curiosity, prediction refresh, monitor run)",
        "sites": ("neuroscifi.webcortex.scheduler:claim_next", "neuroscifi.webcortex.scheduler:work")},
    "tick.monitors": {
        "kind": "cognition", "what": "the timer's runs of due monitors (they stay due and run after quiet ends)",
        "sites": ("neuroscifi.webcortex.scheduler:tick",)},
    "tick.prediction_refresh": {
        "kind": "cognition", "what": "the timer's refreshes of knowledge whose prediction is due",
        "sites": ("neuroscifi.webcortex.scheduler:tick", "neuroscifi.webcortex.predictions:check_due")},
    "tick.curiosity": {
        "kind": "cognition", "what": "the timer's curiosity investigations",
        "sites": ("neuroscifi.webcortex.scheduler:tick",)},
    "tick.scenarios": {
        "kind": "cognition", "what": "the timer's scenario records for open predictions and their comparison once "
                                     "settled (imagined store only; off unless imagine_scenarios is on)",
        "sites": ("neuroscifi.webcortex.scenarios:tick",)},
    "tick.knowledge_recheck": {
        "kind": "cognition", "what": "the timer's re-check of the knowledge most at risk of being stale (lane K, "
                                     "setting idle_recheck, off by default): refresh tasks admitted by "
                                     "autonomy.admission",
        "sites": ("neuroscifi.webcortex.scheduler:tick", "neuroscifi.webcortex.recheck:run",
                  "neuroscifi.webcortex.recheck:submit")},
    "tick.reflection": {
        "kind": "cognition", "what": "the timer's bounded reflection over the records (lane K, setting reflection, "
                                     "off by default): a report, at most 5 actions (re-check marks, abandoned "
                                     "hypotheses, one owner question)",
        "sites": ("neuroscifi.webcortex.scheduler:tick", "neuroscifi.webcortex.reflection:scheduled")},
    "task.background_run": {
        "kind": "cognition", "what": "a background task already running: it stops at its next stop check, "
                                     "before any further network step",
        "sites": ("neuroscifi.webcortex.orchestrator:_Run.check", "neuroscifi.webcortex.orchestrator:_Lookout.__call__")},
    "tick.cache_retention": {
        "kind": "maintenance", "what": "public-cache retention by age and size (deletes only)",
        "sites": ("neuroscifi.webcortex.scheduler:_retention",)},
    "instance.sweep": {
        "kind": "maintenance", "what": "evidence expiry and its consolidated readings (every command and the "
                                       "nightly timer); stopping it would let expired evidence keep counting",
        "sites": ("neuroscifi.instance.governance:sweep",)},
    "instance.checkpoint": {
        "kind": "maintenance", "what": "the nightly cold checkpoint and the rewrite of archives a forget made stale",
        "sites": ("neuroscifi.instance.checkpoints:rebuild_stale", "neuroscifi.instance.checkpoints:after_forget")},
    "resources.idle_unload": {
        "kind": "maintenance", "what": "unloading model services idle past the owner's timeout (frees memory; "
                                       "holding it would keep resources the owner chose to release)",
        "sites": ("neuroscifi.resources.idle:Idle.tick",)},
    "resources.wake": {
        "kind": "maintenance", "what": "reloading a model service a request is waiting for",
        "sites": ("neuroscifi.resources.idle:serve_wake_requests",)},
    "worker.heartbeat": {
        "kind": "maintenance", "what": "the worker's liveness file every 10 s, and a sample of the internal-state "
                                       "record every 30 s (internal_state.py, ADR-051; numbers only: no task, "
                                       "no text)",
        "sites": ("neuroscifi.webcortex.scheduler:_beat",)},
    "voice.sweep": {
        "kind": "maintenance", "what": "expiring retained audio and ending idle voice sessions",
        "sites": ("neuroscifi.voice.hub:VoiceHub._sweep",)},
    "pocket.upkeep": {
        "kind": "maintenance", "what": "expiring Pocket pairing records every 60 s",
        "sites": ("neuroscifi.pocket.server:main",)},
    "autonomy.after_task": {
        "kind": "delivery", "what": "a finished autonomous task's finding, as an owner question (starts no work)",
        "sites": ("neuroscifi.webcortex.autonomy:after_task",)},
    "pocket.push": {
        "kind": "delivery", "what": "an empty Web Push when a family's task finished (starts no work)",
        "sites": ("neuroscifi.pocket.push:Push.run",)},
    "curiosity.proposal": {
        "kind": "owner", "what": "`nsf web curiosity --propose`: a proposal waits for the owner's yes; once "
                                 "confirmed it is owner-asked work and quiet does not hold it",
        "sites": ("neuroscifi.webcortex.curiosity:propose",)},
}

# Functions that only run the entry points above (each entry point checks quiet itself).
RUNNERS = {
    "neuroscifi.webcortex.cli:_tick": "`nsf web tick` (the 15-minute timer runs it)",
    "neuroscifi.webcortex.cli:_work": "`nsf web work` (the research worker service)",
    "neuroscifi.console.server:ConsoleServer._work": "`nsf console --web-worker`",
    "neuroscifi.webcortex.bench.live:scenario_d": "a live benchmark on its own temporary individual",
    "neuroscifi.webcortex.bench.live:scenario_d_second": "its second tick, on the same temporary individual",
    # The cognitive benchmark (scripts/bench_cognition.py): each stages its own temporary individual and runs the real
    # entry points on it to measure them; no service, timer or product module imports this package.
    "neuroscifi.acceptance.cognition.infovalue:_Web.investigate": "benchmark: curiosity on a staged individual",
    "neuroscifi.acceptance.cognition.adversarial:spent_budget_refuses_autonomy": "benchmark: a spent budget",
    "neuroscifi.acceptance.cognition.adversarial:critical_memory_stops_background_only": "benchmark: critical memory",
    "neuroscifi.acceptance.cognition.adversarial:owner_stop_holds": "benchmark: the owner's stop",
    "neuroscifi.acceptance.cognition.adversarial:owner_first_under_contention": "benchmark: owner work first",
    "neuroscifi.acceptance.cognition.knowledge:_World.investigate": "benchmark (lane K): one re-check outcome fed "
                                                                    "through consolidation on a staged individual",
    "neuroscifi.acceptance.cognition.adversarial:forecast_hold_yields_to_the_owner": "benchmark (Wave 2, lane U): "
                                                                                     "a forecast hold against the "
                                                                                     "owner's task",
    "neuroscifi.acceptance.cognition.adversarial:task_survives_its_worker_disconnecting": "benchmark (Wave 2, lane "
                                                                                          "U): a worker lost "
                                                                                          "mid-task",
    "neuroscifi.acceptance.cognition.cycles:Run.step": "benchmark (lane F): one cycle's timer pass (scheduler.tick, "
                                                       "queue only) on a staged temporary individual",
    "neuroscifi.acceptance.cognition.integrated:run_world": "benchmark (Wave 2 lane L): the timer pass and the idle "
                                                            "investigator on a staged individual, step by step",
    "neuroscifi.acceptance.cognition.shadow_check:staged_run": "benchmark (Wave 3 lane H, ADR-058): two staged "
                                                               "background tasks whose status follows an anticipation "
                                                               "trace while the real gate decides; nothing is run",
    "neuroscifi.acceptance.cognition.adversarial:rule_runs_only_inside_admitted_consolidation":
        "benchmark (Wave 3 lane S): two timer passes (quiet on, then off) on a staged temporary individual",
    "neuroscifi.acceptance.cognition.cycles:Run.idle_investigations": "benchmark (Wave 3 lane V, ADR-062): the "
                                                                      "worker's idle investigation on a staged "
                                                                      "temporary individual, inside the day's "
                                                                      "allowance",
}

# Functions that pass on an origin they did not write out: one an owner surface gave them, or one read from a stored
# task or hypothesis. None chooses a background origin itself. The source scan flags every origin= that is not a
# literal (tests/webcortex/test_background.py), so a new one must be named here (or among the sites above) with what
# it forwards.
FORWARDERS = {
    "neuroscifi.webcortex.orchestrator:submit": "a task's origin, as its caller gave it",
    "neuroscifi.webcortex.orchestrator:requeue": "the owner surface's origin, or the task's own (an owner action)",
    "neuroscifi.webcortex.knowledge:refresh": "its caller's origin (check_due's refresh:<id>, the owner's surface)",
    "neuroscifi.webcortex.mcp:Server._web_research": "orchestrator.MCP_ORIGIN ('mcp'), a constant",
    "neuroscifi.inference.tools:_web_research": "the conversation's origin (the owner surface the model answers on)",
    "neuroscifi.pocket.research_routes:handle": "a requeue from the owner's paired device, with the task's own origin",
    "neuroscifi.voice.bridge:Bridge._submit_locked": "the speaking principal's origin (the owner's voice)",
    "neuroscifi.voice.bridge:Bridge._continue_research": "a requeue by the owner's voice, with the principal's origin",
    "neuroscifi.webcortex.bench.live:_task": "a live benchmark's origin on its own temporary individual",
    "neuroscifi.acceptance.cognition.infovalue:_Web._consolidate": "benchmark: a staged task on a temporary individual",
    "neuroscifi.acceptance.cognition.knowledge:_World._consolidate": "benchmark (lane K): a staged task on a "
                                                                     "temporary individual",
    # not a task's origin at all: a hypothesis's (evolution.HYPOTHESIS_ORIGINS), a claim's sources, a Presence label
    "neuroscifi.webcortex.consolidate:run": "a hypothesis's origin (verdict state), not a task's",
    "neuroscifi.webcortex.consolidate:_research_opportunity": "a hypothesis's origin (verdict state), not a task's",
    "neuroscifi.webcortex.evolution:open_hypothesis": "a hypothesis's origin to its recorded fields (lane K's "
                                                      "_describe), not a task's",
    "neuroscifi.webcortex.claims:stamp_origins": "the list of feeds a claim came from (claim flags), not a task's",
    "neuroscifi.presence.aggregator:derive": "the class of a task's origin, shown on Presence (starts nothing)",
}

# A package that only measures (its functions appear in RUNNERS): no module of the product outside it may import it,
# so no service can start its work; scripts/bench_cognition.py is its one caller. tests/webcortex/test_background.py
# checks it.
MEASUREMENT_ONLY = ("neuroscifi.acceptance.cognition",)

# systemd units that start something on their own (deploy/systemd), with the activities they run.
UNITS = {
    "neuroscifi-webcortex-tick.timer": ("tick.monitors", "tick.prediction_refresh", "tick.curiosity",
                                        "tick.cache_retention", "tick.scenarios", "tick.knowledge_recheck",
                                        "tick.reflection"),
    "neuroscifi-checkpoint.timer": ("instance.sweep", "instance.checkpoint"),
    "neuroscifi-idle.timer": ("resources.idle_unload",),
    "neuroscifi-wake.path": ("resources.wake",),
}


def quiet(db) -> bool:
    """The owner's quiet setting, read now (one meta row). False for a file without instance settings."""
    from .instance import store
    from .instance.errors import NotFound
    try:
        return bool(store.config(db).quiet)
    except NotFound:
        return False


def allowed(db, activity) -> tuple[bool, str | None]:
    """(True, None) when `activity` may run now; (False, reason) while quiet holds it. KeyError for an activity
    that is not registered in ACTIVITIES."""
    kind = ACTIVITIES[activity]["kind"]
    if kind == "cognition" and quiet(db):
        return False, QUIET_REASON
    return True, None


def held_origin(origin) -> bool:
    """Whether a task of this origin is background work quiet holds (before the owner-confirmation check)."""
    origin = str(origin or "")
    return origin == "curiosity" or origin.startswith(("refresh:", "monitor:"))


def held_task(db, task_id, origin) -> bool:
    """Whether quiet holds this task: a background origin, not a proposal the owner confirmed and not a task the owner
    resumed or queued again."""
    if not held_origin(origin):
        return False
    return db.execute("SELECT 1 FROM web_events WHERE task_id=? AND kind='decision' AND "
                      "json_extract(detail,'$.decision') IN (?,?) LIMIT 1",
                      (task_id, OWNER_CONFIRMED, OWNER_ASKED)).fetchone() is None


def asked_by_owner(db, task_id, origin, now):
    """The owner resumed or queued again a background task: record it with the task (a 'decision' event), so quiet
    never holds it (owner-asked work is never held, ADR-050). Nothing for a task quiet would not hold. Caller owns the
    transaction."""
    if held_task(db, task_id, origin):
        from .webcortex import state
        state.log_event(db, task_id, "decision", {"decision": OWNER_ASKED}, now)


def status(db) -> dict:
    """What `nsf quiet status` shows: the switch, its last change, and what it holds and what keeps running."""
    from .instance import store
    on = quiet(db)
    row = db.execute("SELECT requested_at FROM deletion_log WHERE target_kind=? AND target_id=? "
                     "ORDER BY id DESC LIMIT 1", (store.SETTING_KIND, SETTING)).fetchone()
    held = [{"activity": k, "what": v["what"]} for k, v in ACTIVITIES.items() if v["kind"] == "cognition"]
    runs = [{"activity": k, "kind": v["kind"], "what": v["what"]} for k, v in ACTIVITIES.items()
            if v["kind"] != "cognition"]
    out = {"quiet": on, "changed_at": row[0] if row else None,
           "summary": ("Quiet: nothing runs unless you ask. Background research, monitors and refreshes wait."
                       if on else "Background work follows your other settings (autonomy, monitors, timers)."),
           "held_while_quiet": held, "keeps_running": runs}
    from .webcortex import state as wstate
    if wstate.has_tables(db):
        out["background_tasks_waiting"] = db.execute(
            f"SELECT count(*) FROM web_tasks WHERE status='queued' AND {HELD_SQL}").fetchone()[0]
    return out


def cli(db, args, now):
    """`nsf quiet [on|off|status]`: the owner's switch (store.set_config records the change)."""
    from .instance import store
    if args.quiet_action in ("on", "off"):
        store.set_config(db, {SETTING: args.quiet_action == "on"}, now)
    return status(db)
