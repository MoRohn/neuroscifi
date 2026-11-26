"""The integration loop measured as one system (Wave 2 lane L, directive §8 and §10; ADR-056). Worlds are frozen in
integrated_splits.py (committed before this runner and before any link of the lane).

One staged Web Cortex individual per world and seed (knowledge._World's staging: items with their "still holds"
predictions, arrivals, crowding contested subjects, hypotheses resting on items). Each step is one pass of the real
background loop:

  1. webcortex.scheduler.tick(queue=True, max_tasks=budget): scenario records (settle, then imagine), the knowledge
     re-check, the due predictions' refreshes and their settlement, the timer's curiosity admitted by
     autonomy.admission, in the tick's own order;
  2. webcortex.autonomy.idle (the worker's idle investigation), `idle` times;
  3. every task those queued is answered by a research outcome (reliability `reliability`: the world's current truth,
     else the version already held; a noise subject gets two sources that disagree at random) fed through the real
     consolidation under that task, then the orchestrator's "still holds" prediction for each new version and
     autonomy.after_task (the finding's delivery, through selective broadcast).

Nothing here re-implements a mechanism or decides for the system: the configurations below only set the owner's
settings on the staged individual, as an owner would (webcortex.state.set_config, instance.store.set_config). The host's
background gate (resources.policy.background_gate: memory level, the audio beacon, predictive regulation) is read as
"allowed" so the result does not depend on this host's memory or a voice session at the moment of the run; it is the
same for every configuration.

Configurations (CONFIGS): `baseline` (every Wave 1 and Wave 2 setting off), `defaults` (as shipped), each mechanism
alone, `integrated` (every link on) and `integrated` minus one link at a time (`-<link>`). Links (LINKS) are named by
what they connect; each maps to the settings it needs.

Scores per world and seed (from the world's truth and the stored records; nothing the system imagined or predicted is
used as ground truth):
  correct_mean / correct_final   share of item subjects whose live knowledge states the world's current truth
  caught_per_investigation       stale facts corrected per investigation
  noise_share                    share of investigations spent on noise subjects (contested subjects that never settle)
  wasted_share                   share of investigations of a subject another task of the same pass already covers
  conflicts                      timer passes in which two tasks were queued for one subject (arbitration failures)
  belief_writes                  belief_revisions rows written (the core belief model; must stay 0)
  scenario_brier / log_loss      the scenario records' three-way Brier and log loss once settled (scenarios.errors),
                                 beside the stated reference of three equal futures (Brier 2/3, log loss ln 3); None
                                 while scenario records are off
  self_probes                    self-description probes on the loop against the world's own bookkeeping: PLUMBING
                                 checks (they compare a stored value with the surface that shows it), never a claim
  cost                           wall seconds, tasks, scenario records written
  activity (lane Y, M1)          what each link DID (_activity: admissions with two or more candidates, salience
                                 orders and how often they changed the first candidate, the timer's largest curiosity
                                 pool, learning-progress factors other than 1, re-check marks and premise rows) and,
                                 against the baseline's run of the same world and seed, the steps at which what was
                                 investigated (changed_launch_steps) or what is held (changed_held_steps) differ: a
                                 link that acted but changed neither is "not exercised by this world"
The draw (lane Y, H3; knowledge._World): decoupled by default, so every configuration meets the same world trajectory
(truth changes keyed by world, seed and step; research outcomes by world, seed, step and subject). Lane L's first
evidence was made with the one-stream draw (decoupled=False); the worlds file is the same for both.
What it cannot show: that real subjects change like these, that real sources are this reliable, the open web, the
answer model, or the real host's resources (the gate is read as allowed).
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import json
import math
import tempfile
import time
from pathlib import Path
from unittest import mock

from ...instance import imagined, store
from ...webcortex.config import WebConfig
from . import integrated_splits as splits
from . import knowledge, scoring

WEB_DEFAULTS = WebConfig()
T0 = knowledge.T0
DAY = knowledge.DAY

# What each link needs switched on, on top of the baseline (every Wave 1 setting off). Web settings, instance settings.
LINKS = {
    "recheck": ({"idle_recheck": True, "premise_recheck": True}, {}),
    "marks": ({"premise_recheck": True}, {}),
    "learning_progress": ({"curiosity_learning_progress": True}, {}),
    "scenarios": ({}, {"imagine_scenarios": True}),
    "salience_attention": ({"salience_attention": True, "salience_record": True}, {}),
    "arbitration": ({"arbitration_record": True}, {}),
}
BASE_WEB = {"curiosity_learning_progress": False, "salience_record": False, "hypothesis_fields": False,
            "hypothesis_abandon": False, "premise_recheck": False, "idle_recheck": False, "reflection": False,
            "strategy_selection": "off", "answer_monitoring": False, "selective_answering": False,
            "verification_escalation": False, "adaptive_depth": False, "salience_attention": False,
            "arbitration_record": False}
BASE_INSTANCE = {"imagine_scenarios": False, "broadcast": "off", "predictive_regulation": False,
                 "executive_rule": "rules-v1"}
RECORDS_WEB = {"salience_record": True, "hypothesis_fields": True}     # records only, on in every integrated run


def config(links, *, defaults=False) -> tuple[dict, dict]:
    """(web settings, instance settings) of a configuration: the baseline plus the named links."""
    if defaults:
        return {}, {}
    web, inst = dict(BASE_WEB), dict(BASE_INSTANCE)
    if links:
        web |= RECORDS_WEB
    for name in links:
        w, i = LINKS[name]
        web |= w
        inst |= i
    return web, inst


def configurations(links=tuple(LINKS)) -> dict:
    """name -> (links, defaults?): baseline, defaults, each link alone, integrated, integrated minus each link."""
    out = {"baseline": ((), False), "defaults": ((), True)}
    for name in links:
        out[f"alone:{name}"] = ((name,), False)
    out["integrated"] = (tuple(links), False)
    for name in links:
        out[f"-{name}"] = (tuple(n for n in links if n != name), False)
    return out


@contextmanager
def _host_gate_allowed():
    """The host's background gate read as allowed (see the module doc): the same for every configuration."""
    from ...resources import policy
    allowed = {"allowed": True, "reason": None, "level": "normal"}
    with mock.patch.object(policy, "background_gate", lambda *a, **k: dict(allowed)):
        yield


class World(knowledge._World):
    """knowledge._World's staging, with the loop run by the real timer pass and idle investigator.

    Lane Y (after the benchmark-integrity review): the configuration's settings are applied BEFORE the staging (L1;
    before, the staging ran at the shipped defaults and wrote premise and hypothesis-detail rows in every
    configuration), and the draw is decoupled by default (H3; knowledge._World's class doc): the world's truth
    changes do not depend on what a configuration investigated, so configurations are compared on the same world.
    `decoupled=False` is the draw of lane L's first evidence (kept under `before_review` in its evidence file)."""

    def __init__(self, spec, seed, db, web, inst, *, decoupled=True):
        # the re-check's share as shipped (knowledge._World widens it for lane K's own worlds)
        defaults = WEB_DEFAULTS
        super().__init__(spec, seed, db, "integrated", web={"recheck_per_tick": defaults.recheck_per_tick,
                                                            "recheck_daily": defaults.recheck_daily} | web,
                         inst=inst, decoupled=decoupled)
        self.executed = []          # (step, task id, origin, subject or None)

    def subject_of_task(self, task) -> str | None:
        origin = str(task["origin"] or "")
        if origin.startswith("refresh:"):
            return self.subject_of(int(origin.split(":", 1)[1]))
        for (detail,) in self.db.execute("SELECT detail FROM web_events WHERE task_id=? AND kind='decision' "
                                         "ORDER BY id", (task["id"],)):
            picked = (json.loads(detail or "{}") or {}).get("autonomy")
            if isinstance(picked, dict):
                if picked.get("knowledge_id") is not None:
                    return self.subject_of(picked["knowledge_id"])
                key = picked.get("key")
                if isinstance(key, str) and key.endswith(" latest version"):
                    return key.removesuffix(" latest version")
        return None

    def execute(self, step, tid):
        """Answer one queued task with the world's research outcome through the real consolidation."""
        from ...webcortex import autonomy, consolidate, orchestrator, state
        task = state.task(self.db, tid)
        sid = self.subject_of_task(task)
        caught = False
        if sid is not None and sid.startswith("noise"):
            clusters = [self._noise_cluster(sid, self.now)]
        elif sid is not None and sid in self.truth:
            before = self.stored_version(sid)
            reported = self.truth[sid] if self.outcome_rng(sid).random() < self.spec["reliability"] else before
            clusters = [self._agree(sid, reported if reported is not None else self.truth[sid], self.now)]
        else:
            clusters = []
        out = {}
        if clusters:
            with store.write(self.db):
                state.save_clusters(self.db, tid, clusters, self.now)
            out = consolidate.run(self.db, tid, self.now)
        with store.write(self.db):
            self.db.execute("UPDATE web_tasks SET status='completed', finished_at=?, updated_at=?, result=? WHERE id=?",
                            (self.now, self.now, json.dumps({"consolidation": out}, default=str), tid))
            store.bump_state_version(self.db)
        if out.get("provisional"):
            orchestrator._predict(self.db, out["provisional"], self.now)
        if sid is not None and sid in self.truth:
            caught = before != self.truth[sid] and self.stored_version(sid) == self.truth[sid]
        autonomy.after_task(self.db, tid, self.now)
        self.executed.append((step, tid, str(task["origin"]), sid))
        return caught

    def _noise_cluster(self, sid, at):
        rng = self.outcome_rng(sid)
        a_val, b_val = rng.randint(10, 49), rng.randint(50, 99)
        h = self._hosts(2)
        name = knowledge._name(sid)["name"]
        a = self._claim(h[0], sid, f"The {name} index stands at {a_val} points today.", None, at)
        b = self._claim(h[1], sid, f"The {name} index stands at {b_val} points today.", None, at)
        return self._cluster([a, b], status="contested", confidence=0.5,
                             contradicting=[{"a": a.id, "b": b.id, "reason": "different values"}])


def _queued(db, since_rowid) -> list[str]:
    return [tid for (tid,) in db.execute("SELECT id FROM web_tasks WHERE status='queued' AND rowid>? ORDER BY rowid",
                                         (since_rowid,))]


def _max_rowid(db) -> int:
    return db.execute("SELECT coalesce(max(rowid), 0) FROM web_tasks").fetchone()[0]


def self_probes(db, world, now) -> list[dict]:
    """Self-description probes on the loop: what the record says against the world's own bookkeeping. PLUMBING:
    each compares a stored value with the surface that shows it. A probe the record cannot answer scores None."""
    from ... import self_description
    record = self_description.build(db, None, now)
    cycle = self_description.value(record, "doing.cycle") or {}
    last = next((e for e in reversed(world.executed) if e[2] == "curiosity" or e[2].startswith("refresh:")), None)
    marks = db.execute("SELECT count(*) FROM web_rechecks WHERE status IN ('marked','queued')").fetchone()[0] \
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_rechecks'").fetchone() else 0
    settled = db.execute("SELECT count(*) FROM web_predictions WHERE outcome IS NOT NULL").fetchone()[0]
    errors = db.execute("SELECT count(*) FROM web_predictions WHERE outcome='error'").fetchone()[0]
    probes = [
        ("last_background_task", (cycle.get("last_investigation") or {}).get("task_id"), last[1] if last else None),
        ("open_recheck_marks", (self_description.value(record, "uncertain.to_recheck") or {}).get("open"), marks),
        ("predictions_settled", (cycle.get("predictions") or {}).get("settled"), settled),
        ("prediction_errors", (cycle.get("predictions") or {}).get("errors"), errors),
    ]
    return [{"probe": name, "answer": got, "truth": want, "correct": got == want, "plumbing_check": True}
            for name, got, want in probes]


@contextmanager
def _activity(counts):
    """What each link DID in a run (lane Y, M1), counted from the real calls' own results; the calls pass through
    unchanged (a counting wrapper, nothing decided or altered here):
      admissions / admissions_multi   autonomy.admission calls on the timer and idle paths / those that admitted
                                      two or more candidates (the only ones an order can change)
      tick_pool_max                   the largest timer curiosity pool of the run (subjects it could launch)
      salience_ordered / salience_first_changed   admissions taken in salience order / of those, how many took
                                      first another candidate than the priority order would have
      lp_factors_not_1                curiosity.rank entries whose learning-progress factor was not 1"""
    from ...webcortex import autonomy, curiosity
    real_admission, real_rank = autonomy.admission, curiosity.rank

    def admission(*a, **k):
        out = real_admission(*a, **k)
        if k.get("path") in ("tick", "idle"):
            counts["admissions"] += 1
            counts["admissions_multi"] += len(out.get("chosen") or []) >= 2
            if "pool" in out:
                counts["tick_pool_max"] = max(counts["tick_pool_max"], len(out["pool"]))
            order = out.get("order") or {}
            if order.get("by") == "salience":
                counts["salience_ordered"] += 1
                counts["salience_first_changed"] += order.get("priority_rank_of_first") not in (0, None)
        return out

    def rank(*a, **k):
        out = real_rank(*a, **k)
        for entry in out:
            factor = (entry.get("learning_progress") or {}).get("factor") \
                if isinstance(entry.get("learning_progress"), dict) else None
            counts["lp_factors_not_1"] += isinstance(factor, (int, float)) and abs(factor - 1.0) > 1e-12
        return out
    with mock.patch.object(autonomy, "admission", admission), mock.patch.object(curiosity, "rank", rank):
        yield


ACTIVITY = ("admissions", "admissions_multi", "tick_pool_max", "salience_ordered", "salience_first_changed",
            "lp_factors_not_1")


def run_world(spec, seed, links, *, defaults=False, decoupled=True) -> dict:
    from ...webcortex import autonomy, scenarios, scheduler
    from ...webcortex import state as wstate
    started = time.perf_counter()
    web, inst = config(links, defaults=defaults)
    counts = dict.fromkeys(ACTIVITY, 0)
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-l-") as directory, ExitStack() as stack:
        stack.enter_context(_host_gate_allowed())
        db = store.create(Path(directory) / "l.sqlite", T0, note="lane L benchmark: the integration loop")
        stack.callback(db.close)
        world = World(spec, seed, db, web, inst, decoupled=decoupled)
        stack.enter_context(_activity(counts))
        beliefs_before = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]
        series, caught, conflicts, wasted = [], 0, 0, 0
        launches, held = [], []         # per step: what was investigated, what is held (compared across configurations)
        arrivals = 0
        for step in range(spec["steps"]):
            world.step = step
            world.now += spec["step_hours"] * 3600.0
            world.step_world()
            for _ in range(spec["arrivals"]):
                arrivals += 1
                world._new_subject(f"new{arrivals:03d}", world.now - 60)
            # the timer's pass queues its tasks and the worker runs them; the worker's idle investigation runs only
            # once nothing is queued (scheduler.work), so it comes after them, as on a running individual
            mark = _max_rowid(db)
            scheduler.tick(db, world.now, None, max_tasks=spec["budget"], queue=True)
            looked = []
            passes = [_queued(db, mark)]
            for tid in passes[0]:
                looked.append(world.subject_of_task(wstate.task(db, tid)))
                caught += world.execute(step, tid)
            for i in range(spec["idle"]):
                mark = _max_rowid(db)
                autonomy.idle(db, world.now + 60 * (i + 1), worker=f"bench-{i}")
                for tid in _queued(db, mark):
                    looked.append(world.subject_of_task(wstate.task(db, tid)))
                    caught += world.execute(step, tid)
            tick_named = [s for s in (world.subject_of_task(wstate.task(db, t)) for t in passes[0]) if s]
            conflicts += len(tick_named) != len(set(tick_named))
            named = [s for s in looked if s is not None]
            wasted += len(named) - len(set(named))
            series.append(world.score())
            launches.append(tuple(looked))
            held.append(tuple((sid, world.stored_version(sid)) for sid in sorted(world.truth)))
        scenarios.tick(db, world.now + 1)         # settle what the last pass can compare
        errors = scenarios.errors(db)["mechanisms"] if scenarios.enabled(db) else {}
        probes = self_probes(db, world, world.now + 2)
        beliefs_after = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]
        imagined_rows = imagined.counts(db)["total"]          # a count, never a record's content
        arbitrations = db.execute("SELECT count(*) FROM web_events WHERE kind='decision' AND "
                                  "json_extract(detail,'$.arbitration') IS NOT NULL").fetchone()[0]
        marks_rows = db.execute("SELECT count(*) FROM web_rechecks").fetchone()[0]
        premise_rows = db.execute("SELECT count(*) FROM web_premises").fetchone()[0]
    investigations = [e for e in world.executed]
    n = len(investigations)
    noise = sum(1 for e in investigations if e[3] is not None and e[3].startswith("noise"))
    final = series[-1]
    keys = ("all", "weighted", "old", "new")
    err = next(iter(errors.values()), None)
    return {"correct_mean": scoring.spread([s["all"] for s in series])["mean"], "correct_final": final["all"],
            "correct_weighted_mean": scoring.spread([s["weighted"] for s in series])["mean"],
            "correct_old_mean": scoring.spread([s["old"] for s in series])["mean"],
            "caught_per_investigation": caught / n if n else None, "investigations": n,
            "noise_share": noise / n if n else None, "wasted_share": wasted / n if n else None,
            "conflicts": conflicts, "arbitrations": arbitrations,
            "belief_writes": beliefs_after - beliefs_before, "lost": final["lost"],
            "scenario_n": err["n"] if err else 0, "scenario_brier": err["brier_3way"] if err else None,
            "scenario_log_loss": err["log_loss"] if err else None,
            "self_probes_correct": sum(1 for p in probes if p["correct"]), "self_probes": len(probes),
            "probes": probes, "imagined_rows": imagined_rows, "series_keys": keys,
            "marks_rows": marks_rows, "premise_rows": premise_rows, **counts,
            "launches": launches, "held": held, "wall_seconds": time.perf_counter() - started}


METRICS = ("correct_mean", "correct_final", "correct_weighted_mean", "correct_old_mean", "caught_per_investigation",
           "investigations", "noise_share", "wasted_share", "conflicts", "arbitrations", "belief_writes", "scenario_n",
           "scenario_brier", "scenario_log_loss", "self_probes_correct", "imagined_rows", "wall_seconds",
           "marks_rows", "premise_rows", *ACTIVITY, "changed_launch_steps", "changed_held_steps")


def _changed(rows, base, field) -> int:
    """Steps at which a run's `field` (what was investigated, what is held) differs from the baseline's run of the
    same world and seed (the same world trajectory under the decoupled draw)."""
    return sum(1 for a, b in zip(rows[field], base[field]) if a != b)


def run(split, configs=None, *, seeds=None, decoupled=True) -> dict:
    """Every configuration on every world of the split under its seeds: per world the scores' spread over seeds, and
    the mean over worlds. With the baseline among the configurations, each run also counts the steps at which what
    it investigated, and what it held, differ from the baseline's run of the same world and seed (lane Y, M1)."""
    spec = splits.INTEGRATED[split]
    seeds = tuple(seeds or spec["seeds"])
    configs = configs or configurations()
    out = {"split": split, "seeds": list(seeds), "reference": {"scenario_brier_equal_futures": 2 / 3,
                                                               "scenario_log_loss_equal_futures": math.log(3)},
           "draw": knowledge.DRAW[decoupled], "configurations": {}}
    raw = {(name, world["name"]): [run_world(world, seed, links, defaults=defaults, decoupled=decoupled)
                                   for seed in seeds]
           for name, (links, defaults) in configs.items() for world in spec["worlds"]}
    for (name, world), rows in raw.items():
        base = raw.get(("baseline", world))
        for i, r in enumerate(rows):
            r["changed_launch_steps"] = _changed(r, base[i], "launches") if base else None
            r["changed_held_steps"] = _changed(r, base[i], "held") if base else None
    for name, (links, defaults) in configs.items():
        worlds = {}
        for world in spec["worlds"]:
            rows = raw[(name, world["name"])]
            worlds[world["name"]] = {k: scoring.spread([r[k] for r in rows]) for k in METRICS}
            worlds[world["name"]]["per_seed"] = {str(s): {k: r[k] for k in METRICS} for s, r in zip(seeds, rows)}
        out["configurations"][name] = {
            "links": list(links), "defaults": defaults, "worlds": worlds,
            "mean": {k: scoring.spread([w[k]["mean"] for w in worlds.values()])["mean"] for k in METRICS}}
    return scoring.rounded(out)


def forced(links) -> tuple[dict, dict]:
    """(web, instance) settings the "integrated" mechanism forces on in the retention and adversarial families: the
    links' own settings and the records they need (RECORDS_WEB), nothing else. Every other setting stays what each
    case's individual stored (lane Y, H2: forcing the whole configuration switched every Wave 1 setting off inside
    the cases, so a case that stages its own setting passed vacuously)."""
    web, inst = (dict(RECORDS_WEB) if links else {}), {}
    for name in links:
        w, i = LINKS[name]
        web |= w
        inst |= i
    return web, inst


@contextmanager
def switched_on(links=tuple(LINKS)):
    """Every individual the family creates reads the links' settings on top of what it stored (forced(); the settings
    dataclasses' from_json, patched for the run; nothing is written), as lane K's KnowledgeEvolution does."""
    import dataclasses
    from ...instance.store import InstanceConfig
    web, inst = forced(links)
    real_i, real_w = InstanceConfig.from_json.__func__, WebConfig.from_json.__func__

    def instance_json(cls, text):
        return dataclasses.replace(real_i(cls, text), **inst)

    def web_json(cls, text):
        cfg = real_w(cls, text)
        for name, value in web.items():
            setattr(cfg, name, value)
        return cfg
    with mock.patch.object(InstanceConfig, "from_json", classmethod(instance_json)), \
            mock.patch.object(WebConfig, "from_json", classmethod(web_json)):
        yield


class Integrated:
    """retention / adversarial: every link of the integrated configuration on for the family (minus the ablated)."""

    def __init__(self, ablate=frozenset()):
        self.links = tuple(n for n in LINKS if n not in ablate)

    def activate(self):
        return switched_on(self.links)


def register():
    from . import register as add
    for family in ("retention", "adversarial"):
        add(family, "integrated", lambda ablate: (lambda: Integrated(ablate)), components=tuple(LINKS),
            description="Wave 2 lane L: every link of the integration loop on (re-check, marks, learning progress, "
                        "scenario records, salience attention, the arbitration record)")


register()

__all__ = ["CONFIGS", "Integrated", "LINKS", "World", "config", "configurations", "forced", "run", "run_world",
           "switched_on"]
CONFIGS = configurations()
