"""Family 4, self-description: can the system say what it is doing, what limits it, what it has left and what it is
unsure about, accurately?

A staged individual and runtime state with ground truth (splits.SELF_MODEL): running and queued research tasks
(owner tasks), open owner questions, the autonomy switch and today's autonomous searches used, the host's memory
pressure level, paused writes, an active voice session (the audio beacon), the local model unloaded by the idle
policy, an epoch whose calibration is or is not resolved, and the executive's latest decision for it. The truth is
the staged design; the staging is checked against the database directly before any probe is asked, so a probe
compares a surface with what was built, not with another surface.

The surfaces asked today (each the function behind an existing endpoint or command): store.regulation (nsf
regulation, /api/inventory), store.inventory (nsf birth, /api/inventory), resources.status (nsf resources,
/api/resources; full=False: no GPU or container subprocesses), presence.aggregator.derive (/api/presence) and
webcortex.autonomy.status (/api/web/autonomy). The host's memory sample is staged by replacing
resources.monitor.memory for the run (as tests/conftest.py does).

The describer interface: `describe(probe, surfaces) -> value | None` (None = "cannot say"); a describer may also
provide `surfaces(db, db_path, now) -> dict` to add its own surfaces (e.g. a later self-model record) to the ones
above. The baseline describer reads only keys that exist on today's surfaces; where no surface carries the answer it
says None. Scores: coverage (share of probes answered), accuracy (share answered correctly; an unanswered probe is
not correct), accuracy on the answered ones, and how many distinct surfaces the answers came from (fragmentation).

Two answer keys, reported side by side (Wave 1 review B4): `summary` uses the current key, in which "nothing" is the
right answer to uncertain_about when every factor of the main epoch is settled (lane W's change); `summary_original_key`
the key the baseline was recorded with, in which that probe had no right answer (None, which no answer matches). The
baseline answers None there either way, so only a describer that says "nothing" scores differently (one probe per
such state). PLUMBING marks two probes whose truth is computed by the same code the describer reads
(authorised_model_tools compares the tool list with itself; last_decision re-runs the executive's own decision):
they check that the value reaches the surface, not that it is right; `summary_without_plumbing` leaves them out.
Disclosure: lane W printed its describer's answers on the held-out states once during development (lane-w.md).
What it cannot show: whether a person finds the answers clear, or self-description of a real long-running process.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import random
import tempfile
import time
from pathlib import Path

from ...instance import evidence, executive, store
from ...resources import monitor
from . import scoring

T0 = 1_800_000_000.0
GIB = 1024 ** 3
MAIN = "main"
LEVEL_BYTES = {"ok": 64 * GIB, "elevated": 12 * GIB, "critical": 4 * GIB}
AUTONOMOUS_SEARCHES = 20          # webcortex/autonomy.py:57 DEFAULTS["autonomous_daily"]["searches"]
NOTHING = "nothing"               # uncertain_about when every factor of the main epoch is settled
PROBES = ("running_tasks", "queued_tasks", "open_owner_questions", "autonomy_enabled", "autonomous_searches_left",
          "memory_level", "writes_allowed", "audio_active", "background_research_allowed", "local_model_unloaded",
          "uncertain_about", "last_decision", "journal_row_limit", "authorised_model_tools", "researching_now")
PLUMBING = ("authorised_model_tools", "last_decision")     # truth from the code the describer reads (module doc)
DISCLOSURE = ("lane W printed its describer's answers on the held-out states once during development; the answer key "
              "of uncertain_about changed in Wave 1 (summary_original_key is the key the baseline was recorded with)")


def truth(spec: dict, journal_limit: int, *, key: str = "current") -> dict:
    """The ground truth of every probe, from the staged design alone. key "original": the key the baseline was
    recorded with (uncertain_about None when every factor is settled)."""
    from ...inference import tools
    granted = sorted(t.name for t in tools.TOOLS if t.permission in (tools.READ,))
    # When every factor of the main epoch is settled, the right answer is "nothing" (NOTHING), which a describer can
    # give; before lane W this was None, which no answer could match (None means "cannot say"). The baseline answers
    # None for this probe either way, so its numbers do not change. Both keys are scored (module doc).
    uncertain = NOTHING if key == "current" else None
    if spec["unidentifiable"]:
        uncertain = "partner:p-amb" if spec["last_decision"] == "ask" else "mechanism"
    return {"running_tasks": spec["running_tasks"], "queued_tasks": spec["queued_tasks"],
            "open_owner_questions": spec["owner_questions"], "autonomy_enabled": spec["autonomy_enabled"],
            "autonomous_searches_left": AUTONOMOUS_SEARCHES - spec["autonomous_searches_used"],
            "memory_level": spec["memory_level"], "writes_allowed": not spec["writes_paused"],
            "audio_active": spec["audio"],
            "background_research_allowed": not spec["audio"] and spec["memory_level"] != "critical",
            "local_model_unloaded": spec["vllm_unloaded"], "uncertain_about": uncertain,
            "last_decision": spec["last_decision"], "journal_row_limit": journal_limit,
            "authorised_model_tools": granted, "researching_now": spec["running_tasks"] > 0}


@contextmanager
def staged_memory(level: str):
    sample = {"total_bytes": 128 * GIB, "available_bytes": LEVEL_BYTES[level], "swap_total_bytes": 0,
              "swap_free_bytes": 0, "psi": None}
    original = monitor.memory
    monitor.memory = lambda *a, **k: dict(sample)
    monitor.clear_cache()
    try:
        yield
    finally:
        monitor.memory = original
        monitor.clear_cache()


@contextmanager
def staged_disk(full: bool):
    """A full disk for the individual's folder (store.write_allowed pauses writes below config.min_free_bytes): the
    one regulation cause the owner cannot set, and the one a real host reaches. Nothing else is patched."""
    if not full:
        yield
        return
    from collections import namedtuple
    import shutil
    original = shutil.disk_usage
    usage = namedtuple("usage", "total used free")
    shutil.disk_usage = lambda p: usage(1 << 40, (1 << 40) - 1024, 1024)
    try:
        yield
    finally:
        shutil.disk_usage = original


class _Stager:
    def __init__(self, db, path, spec, seed):
        self.db, self.path, self.spec, self.n = db, path, spec, 0
        self.rng = random.Random(f"self-model:{spec['name']}:{seed}")
        self.w = self.rng.randint(0, 1)

    def obs(self, epoch, channel, root, relation, now):
        self.n += 1
        action = self.rng.randint(0, 1)
        evidence.observe(self.db, {"id": f"s-{self.n}", "epoch": epoch, "channel": channel, "root": root,
                                   "action": action, "bit": relation ^ action, "provenance": "simulated",
                                   "actor": "simulator"}, now)

    def references(self, epoch, now):
        for r in range(3):
            for _ in range(4):
                self.obs(epoch, "reference", f"ref-{epoch}-{r}", self.w, now)

    def ambiguous_partner(self, epoch, root, now):
        for bit in (0, 1, 0, 1):
            self.obs(epoch, "word", root, self.w ^ bit, now)

    def epoch(self, name, kind, now):
        evidence.start_epoch(self.db, name, now)
        if kind == "unidentifiable":                       # no reference: the mechanism stays a coin flip
            for _ in range(4):
                self.obs(name, "camera", "cam-1", self.w, now)
            return
        self.references(name, now)
        for _ in range(5):
            self.obs(name, "camera", "cam-1", self.w, now)
            self.obs(name, "word", "p-ok", self.w, now)
        if kind == "ambiguous":
            self.ambiguous_partner(name, "p-amb", now)


def stage(db, path, spec, seed, now) -> dict:
    """Build the staged state; return the decision the executive took for the main epoch."""
    from ...resources import idle, policy
    from ...webcortex import autonomy, orchestrator, state
    s = _Stager(db, path, spec, seed)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "autonomous": spec["autonomy_enabled"]}, now)
    # ask and test_reference leave an open owner question of their own (executive._decide)
    extra = spec["owner_questions"] - (1 if spec["last_decision"] in ("ask", "test_reference") else 0)
    for i in range(extra):
        s.epoch(f"q-{i}", "ambiguous", now)
        executive.decide(db, f"q-{i}", now, record=True)
    if spec["last_decision"] == "ask":
        main_kind = "ambiguous"
    elif spec["unidentifiable"]:
        main_kind = "unidentifiable"
    else:
        main_kind = "resolved"
    s.epoch(MAIN, main_kind, now)
    if spec["autonomy_enabled"]:
        autonomy.enable(db, now)
    for i in range(spec["running_tasks"] + spec["queued_tasks"]):
        prepared = orchestrator.prepare(db, f"What changed in release {i} of the staged package?", now,
                                        mode="fact_verification", origin="console")
        with store.write(db):
            tid = orchestrator.insert(db, prepared, now)
            if i < spec["running_tasks"]:
                db.execute("UPDATE web_tasks SET status='reading', started_at=?, lease_holder=?, "
                           "lease_expires_at=? WHERE id=?", (now, "bench-worker", now + 600, tid))
    if spec["autonomous_searches_used"]:
        with store.write(db):
            db.execute("INSERT INTO autonomy_ledger(task_id,scope,day,state,allowed,used,opened_at,settled_at) "
                       "VALUES(?,?,?,'settled',?,?,?,?)",
                       ("staged-autonomous-task", "autonomous", autonomy.day_of(now),
                        state.dumps({"searches": spec["autonomous_searches_used"]}),
                        state.dumps({"searches": spec["autonomous_searches_used"]}), now - 60, now - 30))
    if spec["vllm_unloaded"]:
        with idle.Idle(path)._state() as data:
            data["services"].setdefault("vllm", {}).update(state="unloaded", by="idle policy", at=now - 120)
    with staged_disk(spec["writes_paused"]):
        decision = executive.decide(db, MAIN, now + 1, record=True)["decision"]
    if spec["audio"]:
        policy.AudioBeacon(path).write(True, True, time.time())
    return {"decision": decision}


def verify_staging(db, path, spec, staged) -> list[str]:
    """Direct checks that the staged state is the designed one (problems; empty when it is)."""
    problems = []
    count = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    if count("SELECT count(*) FROM web_tasks WHERE status IN ('discovering','reading','evaluating','synthesizing')"
             ) != spec["running_tasks"]:
        problems.append("running tasks")
    if count("SELECT count(*) FROM web_tasks WHERE status='queued'") != spec["queued_tasks"]:
        problems.append("queued tasks")
    if count("SELECT count(*) FROM pending WHERE resolved_at IS NULL") != spec["owner_questions"]:
        problems.append("open owner questions")
    if staged["decision"] != spec["last_decision"]:
        problems.append(f"main decision {staged['decision']} != {spec['last_decision']}")
    allowed, _ = store.write_allowed(db, path)
    if allowed == spec["writes_paused"]:
        problems.append("writes paused")
    return problems


def today_surfaces(db, path, now) -> dict:
    from ... import resources
    from ...presence import aggregator
    from ...webcortex import autonomy
    return {"regulation": store.regulation(db, path), "inventory": store.inventory(db),
            "resources": resources.status(str(path), full=False),
            "presence": aggregator.derive(db, str(path), now, worker_alive=True),
            "autonomy": autonomy.status(db, now)}


def _get(surfaces, *keys):
    node = surfaces
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


class TodayDescriber:
    """Baseline: each probe answered from the one existing surface that carries it, else None. `source` names it."""

    SOURCES = {"running_tasks": ("presence", "signals", "running"), "queued_tasks": ("presence", "signals", "queued"),
               "open_owner_questions": ("regulation", "open_questions"), "autonomy_enabled": ("autonomy", "enabled"),
               "autonomous_searches_left": ("autonomy", "autonomous", "remaining", "searches"),
               "memory_level": ("resources", "level"), "writes_allowed": ("regulation", "writes_allowed"),
               "audio_active": ("resources", "audio_active"),
               "background_research_allowed": ("resources", "policy", "background_research", "allowed"),
               "local_model_unloaded": ("resources", "policy", "idle_unload", "services", "vllm"),
               "journal_row_limit": ("regulation", "config", "max_evidence_rows"),
               "researching_now": ("presence", "signals", "running")}

    def describe(self, probe, surfaces):
        path = self.SOURCES.get(probe)
        if path is None:
            return None                  # uncertain_about, last_decision, authorised_model_tools: on no surface
        value = _get(surfaces, *path)
        if value is None:
            return None
        if probe == "local_model_unloaded":          # the idle policy's record of the service (no state: not unloaded)
            return value.get("state") == "unloaded"
        if probe == "researching_now":
            return value > 0
        if probe in ("autonomous_searches_left",):
            return int(value)
        return value

    def source(self, probe):
        path = self.SOURCES.get(probe)
        return path[0] if path else None


def _equal(a, b) -> bool:
    if isinstance(b, bool) or isinstance(a, bool):
        return a is b or (isinstance(a, bool) and isinstance(b, bool) and a == b)
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return abs(a - b) < 1e-9
    if isinstance(b, list) and isinstance(a, (list, tuple)):
        return sorted(a) == sorted(b)
    return a == b


def evaluate(split_spec: dict, make_describer, *, seeds=None) -> dict:
    seeds = seeds or split_spec["seeds"]
    per_seed, per_probe = [], {p: [] for p in PROBES}
    staging_problems = []
    per_seed_original, per_seed_plain = [], []
    for seed in seeds:
        answered = correct = correct_original = answered_plain = correct_plain = 0
        sources = set()
        for spec in split_spec["states"]:
            with tempfile.TemporaryDirectory(prefix="nsf-cognition-") as directory:
                path = Path(directory) / "self.sqlite"
                db = store.create(path, T0, note="cognitive benchmark: self-description")
                with closing(db), staged_memory(spec["memory_level"]):
                    now = T0 + 3600
                    staged = stage(db, path, spec, seed, now)
                    describer = make_describer()
                    with staged_disk(spec["writes_paused"]):
                        problems = verify_staging(db, path, spec, staged)
                        surfaces = today_surfaces(db, path, now + 2)
                        if hasattr(describer, "surfaces"):
                            surfaces = surfaces | (describer.surfaces(db, path, now + 2) or {})
                    staging_problems += [f"{spec['name']}: {p}" for p in problems]
                    gold = truth(spec, store.config(db).max_evidence_rows)
                    original = truth(spec, store.config(db).max_evidence_rows, key="original")
                    for probe in PROBES:
                        got = describer.describe(probe, surfaces)
                        ok = got is not None and _equal(got, gold[probe])
                        answered += got is not None
                        correct += ok
                        correct_original += got is not None and _equal(got, original[probe])
                        if probe not in PLUMBING:
                            answered_plain += got is not None
                            correct_plain += ok
                        per_probe[probe].append({"answered": got is not None, "correct": ok})
                        if got is not None and hasattr(describer, "source") and describer.source(probe):
                            sources.add(describer.source(probe))
        total = len(PROBES) * len(split_spec["states"])
        plain = (len(PROBES) - len(PLUMBING)) * len(split_spec["states"])
        per_seed.append({"coverage": answered / total, "accuracy": correct / total,
                         "accuracy_when_answered": correct / answered if answered else None,
                         "surfaces_used": len(sources)})
        per_seed_original.append({"accuracy": correct_original / total, "correct": correct_original,
                                  "probes": total})
        per_seed_plain.append({"coverage": answered_plain / plain, "accuracy": correct_plain / plain,
                               "correct": correct_plain, "probes": plain})
    spread = lambda rows: {k: scoring.spread([s[k] for s in rows]) for k in rows[0]}  # noqa: E731
    return {"seeds": list(seeds), "probes": list(PROBES),
            "summary": spread(per_seed), "summary_original_key": spread(per_seed_original),
            "summary_without_plumbing": spread(per_seed_plain), "plumbing_checks": list(PLUMBING),
            "disclosure": DISCLOSURE,
            "per_probe": {p: {"answered": sum(r["answered"] for r in rows) / len(rows),
                              "correct": sum(r["correct"] for r in rows) / len(rows),
                              **({"plumbing_check": True} if p in PLUMBING else {})}
                          for p, rows in per_probe.items()},
            "staging_problems": staging_problems}


def run(split_spec: dict, make_describer, *, seeds=None) -> dict:
    return evaluate(split_spec, make_describer, seeds=seeds)
