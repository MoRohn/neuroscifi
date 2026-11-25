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
CLI, Pocket, voice, MCP, research nodes, a curiosity proposal the owner confirmed) is never held. A background task
already running when quiet is turned on stops at its next stop check (before every network step, as the owner's
cancel does), ends 'partial', and nothing it read is consolidated (no model call after the stop). Queued background
tasks stay queued and run after quiet is turned off.
"""
from __future__ import annotations

SETTING = "quiet"
QUIET_REASON = ("quiet is on: the owner stopped background work, nothing runs unless the owner asks "
                "(`nsf quiet off` resumes it)")
STOPPED = "stopped by quiet (the owner stopped background work): nothing was consolidated into knowledge"
# A proposal the owner confirmed (knowledge.apply_review) is owner-asked work although its origin is 'curiosity'.
OWNER_CONFIRMED = "proposal authorised by the owner"
# Task origins of background work (orchestrator.is_autonomous plus monitors' scheduled runs).
ORIGIN_SQL = "(origin='curiosity' OR origin LIKE 'refresh:%' OR origin LIKE 'monitor:%')"
HELD_SQL = (f"({ORIGIN_SQL} AND id NOT IN (SELECT task_id FROM web_events WHERE kind='decision' AND task_id IS NOT "
            f"NULL AND json_extract(detail,'$.decision')='{OWNER_CONFIRMED}'))")

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
        "kind": "maintenance", "what": "the worker's liveness file every 10 s (no task, no text)",
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
}

# systemd units that start something on their own (deploy/systemd), with the activities they run.
UNITS = {
    "neuroscifi-webcortex-tick.timer": ("tick.monitors", "tick.prediction_refresh", "tick.curiosity",
                                        "tick.cache_retention"),
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
    """Whether quiet holds this task: a background origin and not a proposal the owner confirmed."""
    if not held_origin(origin):
        return False
    return db.execute("SELECT 1 FROM web_events WHERE task_id=? AND kind='decision' AND "
                      "json_extract(detail,'$.decision')=? LIMIT 1", (task_id, OWNER_CONFIRMED)).fetchone() is None


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
