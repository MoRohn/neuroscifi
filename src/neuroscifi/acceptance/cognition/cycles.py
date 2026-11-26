"""The learning-cycles experiment (Wave 2 lane F; directive §7 "recursive knowledge evolution", deliverable F;
scripts/bench_learning_cycles.py). Worlds are frozen in cycles_splits.py, committed before this harness. The withheld
worlds (cycles_withheld.py, Wave 3 step 0) are loaded and scored only by final(), one recorded look per run.

One run: a real Web Cortex individual (a temporary file) lives through the world's cycles. Before cycle 1 it holds
what the owner researched (the world's cycle-0 subjects). Each cycle (one simulated day), in this order:
  1. the owner forgets what the world says the owner forgets that cycle (governance.forget_source: a domain);
  2. the individual's own outputs assert versions no source states (the world's `self` list): a record in its
     imagined store (instance.imagined.record, kind 'inference') and, for any task about that subject this cycle, the
     model-written answer stored with the task (result.reasoning, generated_by 'model:...'), as the orchestrator
     stores a model's answer;
  3. the owner researches the cycle's new subjects (an owner task each); a simulated owner accepts the world's share
     of the new items (knowledge.review; labelled simulated, as the offline benchmark labels its acceptance);
  4. the timer runs: scheduler.tick(queue=True) - the reflection (when on), the knowledge re-check (when on), the
     due-order refreshes of "still holds" predictions and the timer's curiosity, all admitted by the real rules and
     bounded by the daily autonomous allowance, which is the world's `budget` (WebConfig.daily_autonomous.tasks);
  5. every queued task is executed against the world: the pages the world wrote for (its subject, this cycle) are
     read through the real extraction (claims.extract_claims), origin stamping (claims.stamp_origins), clustering
     and scoring (claims.cluster, claims.score) and consolidation (consolidate.run), then the orchestrator's
     "still holds" predictions for new items (orchestrator._predict), the mode outcome, the autonomy ledger and
     after-run hooks; predictions settle at the next timer pass (predictions.check_due), as in the product;
  6. the cycle is scored against the world's truth for that cycle.
What is substituted: search and fetching (the world hands each task its pages; no SafeFetcher, no search engine, no
answer synthesis, no model: the stored model answer is a fixed sentence), the host's resource gate (held open:
resources.policy.background_gate), and the owner (accepts a seeded share of new items; answers nothing else).
Not run by default: the worker's idle investigation (autonomy.idle), which in the product shares the daily allowance
with the timer; "the loop" of every configuration but those of idle_configurations() is the timer's pass only (lane Y,
M4). Wave 3 lane V (ADR-062) runs it in those (Run.idle_investigations: how the allowance is split is stated there).
References (lane Y, H4): REFERENCES / references(), from the world generator alone, beside the measured runs.

Measured per cycle against the world's truth (never against anything the individual produced):
  accuracy        share of held items (live knowledge rows about a world subject) that state the version true now
  coverage        share of the subjects so far with at least one held item
  correct         share of the subjects so far whose newest held item states the version true now
  caught          changes of the truth that the newest held item reached by the end, and the cycles it took
  persistence     error episodes (the newest held item states a version that is not true now): cycles each lasted
  retention       subjects held correctly at the end of RETENTION_CYCLE whose truth never changed after it: share
                  still held correctly at the end (lost: no held item; regressed: held but wrong; forgotten by the
                  owner: every held item rested only on a forgotten domain), the forgetting question
  calibration     Brier of each held item's stated confidence against "true now", pooled over items and cycles,
                  with the constant reference (the pooled true rate) beside it
  self_admitted   knowledge rows ever written stating a version only the individual's own outputs asserted (9.x)
  wire_admitted   knowledge rows ever written stating the syndicated wrong story's version (7.x)
  one_origin_rises  investigations whose pages carried one independent origin only (one outlet and its copies, or
                  the wire story and its copies) after which a held item's confidence rose above both its previous
                  value and what that one origin earns alone (claims.score of the origin page): must be 0
  cost            tasks by origin, pages read, wall seconds, file size and row counts at the end (growth per cycle)
"""
from __future__ import annotations

from contextlib import closing, contextmanager, ExitStack
import json
import re
import tempfile
import time
from pathlib import Path
from unittest import mock

from ...instance import store
from . import cycles_splits as worlds
from . import scoring

T0 = 1_800_000_000.0
DAY = 86_400.0
RETENTION_CYCLE = 3
MECHANISM = "learning-cycles-bench"

# Every Wave 1 web setting off (scripts/bench_wave1.py WAVE1_OFF_WEB): the baseline. Lane K's six mechanisms are
# switched on from here, one at a time, all together, and all together with one off.
WAVE1_OFF_WEB = {"curiosity_learning_progress": False, "salience_record": False, "hypothesis_fields": False,
                 "hypothesis_abandon": False, "premise_recheck": False, "idle_recheck": False, "reflection": False,
                 "reflection_model_summary": False, "strategy_selection": "off", "answer_monitoring": False,
                 "selective_answering": False, "verification_escalation": False, "adaptive_depth": False,
                 "pocket_recent": False}
MECHANISMS = ("hypothesis_fields", "premise_recheck", "idle_recheck", "hypothesis_abandon", "reflection",
              "strategy_selection")
ON = {"hypothesis_fields": {"hypothesis_fields": True},
      "premise_recheck": {"premise_recheck": True},
      "idle_recheck": {"idle_recheck": True},
      "hypothesis_abandon": {"hypothesis_abandon": True},
      # the shortest interval the owner may set (1 day): one reflection per cycle; its model summary by a stub that
      # asserts a version no source states (a self-generated output; nothing here calls a model)
      "reflection": {"reflection": True, "reflection_interval_days": 1.0, "reflection_model_summary": True},
      # "gate" plus a 'retain' row written into the throwaway individual's ledger: in force WITHOUT passing the real
      # gate (labelled in every result)
      "strategy_selection": {"strategy_selection": "gate"}}


NO_LOOP = "-autonomy"     # the trivial reference: autonomous research off (no refresh, no curiosity, no re-check)
# Wave 3 step 0: every setting as a new individual has it; nothing is forced but what the run itself needs (enabled,
# autonomous, the daily allowance). Read back inside each run (Run.config).
SHIPPED = "=shipped-defaults"
EXTRA = {"shipped": frozenset({SHIPPED})}      # configurations outside the default set of `run`
WITHHELD = "withheld"     # the split of cycles_withheld.py: scored only by final()
# Settings read back from the individual inside each run, to show which mechanisms were on (review: read the
# configuration back inside a run).
READ_BACK = tuple(WAVE1_OFF_WEB) + ("salience_attention", "arbitration_record", "calibrated_forecasts",
                                    "tick_admission", "enabled", "autonomous", "recheck_order", "recheck_per_tick",
                                    "recheck_daily")

# Wave 3 lane R (ADR-060): the re-check's order (recheck_order; the re-check itself, idle_recheck, on) at the shipped
# share of the allowance (recheck_per_tick 1, recheck_daily 4) and with the whole allowance ("/all": both set to 100,
# the owner's own bounds; the daily autonomous allowance still caps every pass). "recheck:staleness" is lane K's
# order (= only:idle_recheck). Ablations: the combination without each ingredient.
ORDER_RULES = ("rotation", "rate", "rate+seen", "rate+seen+rotation", "seen+rotation", "rate+rotation")
for _rule in ORDER_RULES:
    ON[f"order:{_rule}"] = {"idle_recheck": True, "recheck_order": _rule}
ON["share:all"] = {"recheck_per_tick": 100, "recheck_daily": 100}


def order_configurations() -> dict:
    """{name: mechanisms}: the no-loop reference, baseline, lane K's re-check order and each lane R order, at the
    shipped share and with the whole allowance."""
    out = {"reference:no-loop": frozenset({NO_LOOP}), "baseline": frozenset(),
           "recheck:staleness": frozenset({"idle_recheck"}),
           "recheck:staleness/all": frozenset({"idle_recheck", "share:all"})}
    for rule in ORDER_RULES:
        out[f"recheck:{rule}"] = frozenset({f"order:{rule}"})
        out[f"recheck:{rule}/all"] = frozenset({f"order:{rule}", "share:all"})
    return out


EXTRA |= {k: v for k, v in order_configurations().items() if k not in ("reference:no-loop", "baseline")}

# Wave 3 lane S (ADR-061), for the main session's one withheld look (cycles_s.py measures the rest): the shipped
# defaults with lane S's two fixes off (what Step 0 ran), and with supersession on at the K chosen on development.
LANE_S_FIXES_OFF, LANE_S_SUPERSEDE = "-lane-s-fixes", "+lane-s-supersession-K2"
ON.update({LANE_S_FIXES_OFF: {"attribution_independence": False, "echo_recognition": False},
           LANE_S_SUPERSEDE: {"supersession": "independent", "supersession_min_sources": 2}})
EXTRA.update({"lane-s:fixes-off": frozenset({SHIPPED, LANE_S_FIXES_OFF}),
              "lane-s:supersede-K2": frozenset({SHIPPED, LANE_S_SUPERSEDE})})
READ_BACK = READ_BACK + ("attribution_independence", "echo_recognition", "supersession", "supersession_min_sources")

# Wave 3 fix lane (review3 Q7-2, Q7-8): each lane S fix is decided on its own, so each has its configuration on top of
# the shipped defaults (in which both are OFF since the fix lane, ADR-061); `baseline` is Step 0's behaviour made
# explicit. It sets only WAVE1_OFF_WEB (the fourteen Wave 1 web settings, off; idle_recheck among them, so
# recheck_per_tick and recheck_daily, left at their defaults, do nothing) and STEP0_BASELINE (Wave 3: both lane S fixes
# off, supersession "ask", lane K's re-check order "staleness"); the idle investigation is not run in it (no IDLE
# marker). Every other setting stays at its default, as at Step 0 (review3b L-12), among them three that are ON:
# `calibrated_forecasts` (ADR-046), `tick_admission` "unified" (ADR-050) and Wave 2's `arbitration_record` (ADR-056, a
# record only); Wave 2's `salience_attention` is off by its default. On the integrated tree before the fix lane,
# `baseline` left the lane S switches at their default, which was ON.
LANE_S_ECHO, LANE_S_ATTRIBUTION = "+lane-s-echo", "+lane-s-attribution"
ON.update({LANE_S_ECHO: {"echo_recognition": True}, LANE_S_ATTRIBUTION: {"attribution_independence": True}})
EXTRA.update({"lane-s:echo-only": frozenset({SHIPPED, LANE_S_FIXES_OFF, LANE_S_ECHO}),
              "lane-s:attribution-only": frozenset({SHIPPED, LANE_S_FIXES_OFF, LANE_S_ATTRIBUTION}),
              "lane-s:both": frozenset({SHIPPED, LANE_S_FIXES_OFF, LANE_S_ECHO, LANE_S_ATTRIBUTION})})
STEP0_BASELINE = {"attribution_independence": False, "echo_recognition": False, "supersession": "ask",
                  "recheck_order": "staleness"}
# The one command of the main session's Wave 3 look (scripts/bench_learning_cycles.py final --look wave3): every
# configuration below on the same worlds and seeds. Reference pairings (stated so the report cannot mix them up):
# the lane S configurations against `lane-s:fixes-off` (each fix, then both); `lane-s:supersede-K2` against
# `shipped`; `shipped+idle` against `shipped`; `recheck:*` and `idle-first` against `baseline`.
LOOKS = {"wave3": ("reference:no-loop", "baseline", "shipped", "lane-s:fixes-off", "lane-s:echo-only",
                   "lane-s:attribution-only", "lane-s:both", "lane-s:supersede-K2",
                   "recheck:rate+seen+rotation/all", "recheck:rotation/all", "shipped+idle", "idle-first")}
LOOK_PAIRS = {"lane-s:echo-only": "lane-s:fixes-off", "lane-s:attribution-only": "lane-s:fixes-off",
              "lane-s:both": "lane-s:fixes-off", "shipped": "lane-s:fixes-off", "lane-s:supersede-K2": "shipped",
              "shipped+idle": "shipped", "recheck:rate+seen+rotation/all": "baseline",
              "recheck:rotation/all": "baseline", "idle-first": "baseline", "lane-s:fixes-off": "baseline"}
# What every look reports per world and seed (run_split's per_seed rows), and what the admitted-content counters see
LOOK_FIELDS = ("mean.correct", "mean.coverage", "self_admitted", "wire_admitted", "one_origin_rises",
               "retention.lost", "retention.regressed", "admitted.wrong", "admitted.own_only", "admitted.wire_only",
               "replacements.true", "replacements.false", "replacements.by_rule_false",
               "replacements.false_persistence_max", "replacements.false_censored")
COUNTERS = {
    "self_admitted": "knowledge rows ever written whose version has major 9 (the worlds' convention for the "
                     "individual's own outputs): blind to an own output stated as a 1.x value",
    "wire_admitted": "knowledge rows ever written whose version has major 7 (the implausible wire story): blind to a "
                     "plausible syndicated error (1.<minor+1>.0)",
    "admitted.wrong": "knowledge rows written stating a version that was not the truth in the cycle they were "
                      "written (any major; the truth as `correct` reads it): no version convention",
    "admitted.own_only": "knowledge rows written whose every source page was an echo of the individual's own output "
                         "(the world's page kind 'echo')",
    "admitted.wire_only": "knowledge rows written whose every source page was the wire story or a copy of it (page "
                          "kinds 'wire', 'wire_copy'), whatever version they state",
    "replacements": "every supersession consolidation reported, classified against the truth of its cycle: true (the "
                    "new value is true, the old was not), false (the old was true, the new is not), other; "
                    "by_rule_* when the supersession rule made it; per false one: the cycles until the subject was "
                    "held right again (wrong and nothing-held cycles counted apart) and whether the run ended first "
                    "(censored)"}

# Wave 3 lane V (ADR-062): the worker's idle investigation (autonomy.idle) as the product runs it, inside the SAME daily
# allowance as the timer (Run.idle_investigations). Not settings: what the harness's simulated worker does each cycle.
#   IDLE        after the timer's pass and its tasks: the idle decisions take what the pass left of the allowance
#   IDLE_FIRST  before the timer's pass: the idle decisions may take the whole allowance, the pass gets what is left
IDLE = "+idle"
IDLE_FIRST = "+idle-first"
WORKER = frozenset({NO_LOOP, SHIPPED, IDLE, IDLE_FIRST})      # markers that are not settings


def idle_configurations() -> dict:
    """{name: mechanisms}: the no-loop reference, baseline (the timer's pass only), the idle investigation after and
    before the pass, and the settings as a new individual has them without and with it ("the loop as shipped with
    autonomy on" is `shipped+idle`: the owner's enable starts both the timer's autonomous part and the worker's idle
    investigation)."""
    return {"reference:no-loop": frozenset({NO_LOOP}), "baseline": frozenset(), "idle": frozenset({IDLE}),
            "idle-first": frozenset({IDLE_FIRST}), "shipped": frozenset({SHIPPED}),
            "shipped+idle": frozenset({SHIPPED, IDLE})}


EXTRA |= {k: v for k, v in idle_configurations().items() if k not in ("reference:no-loop", "baseline")}

# Wave 4 lane A (ADR-064): the division of the day's allowance between refreshing and reaching (WebConfig
# allowance_division, reach_share), each on top of `shipped+idle` (the product as shipped with autonomy on: the
# timer's pass, then the worker's idle investigation, one allowance), which is every division's reference. Candidates
# assessed on development: a fixed reserve at three shares (0.5 is the fair interleave: equal halves of the day, each
# path's half released to the other when it has nothing admitted), and the adaptive share from each path's observed
# yield. Ablations: NO_RELEASE keeps the reserve held when nothing reaching is admitted (a harness patch of
# autonomy._reach_open, not a setting); "/idle-first" runs the idle investigation before the pass (the order the
# division should make irrelevant). References beside them: the timer's pass alone (`shipped`), and the idle
# investigation taking the allowance first (`shipped+idle-first`).
DIVISION_RULES = {"reserve-0.25": {"allowance_division": "reserve", "reach_share": 0.25},
                  "reserve-0.5": {"allowance_division": "reserve", "reach_share": 0.5},
                  "reserve-0.75": {"allowance_division": "reserve", "reach_share": 0.75},
                  "yield": {"allowance_division": "yield"}}
for _rule, _values in DIVISION_RULES.items():
    ON[f"division:{_rule}"] = _values
NO_RELEASE = "+no-release"
WORKER = WORKER | {NO_RELEASE}
DIVISION_REFERENCE = "shipped+idle"
READ_BACK = READ_BACK + ("allowance_division", "reach_share")


def division_configurations() -> dict:
    """{name: mechanisms}: the references and every division candidate (see DIVISION_RULES)."""
    out = {"reference:no-loop": frozenset({NO_LOOP}), "baseline": frozenset(), "shipped": frozenset({SHIPPED}),
           "shipped+idle": frozenset({SHIPPED, IDLE}), "shipped+idle-first": frozenset({SHIPPED, IDLE_FIRST})}
    for rule in DIVISION_RULES:
        out[f"division:{rule}"] = frozenset({SHIPPED, IDLE, f"division:{rule}"})
    out["division:reserve-0.5/no-release"] = frozenset({SHIPPED, IDLE, "division:reserve-0.5", NO_RELEASE})
    out["division:reserve-0.5/idle-first"] = frozenset({SHIPPED, IDLE_FIRST, "division:reserve-0.5"})
    return out


EXTRA |= {k: v for k, v in division_configurations().items() if k not in EXTRA and k not in ("reference:no-loop",
                                                                                              "baseline")}


def configurations() -> dict:
    """{name: set of lane K mechanisms on}: the no-loop reference, baseline, each alone, all, all without one."""
    out = {"reference:no-loop": frozenset({NO_LOOP}), "baseline": frozenset()}
    for m in MECHANISMS:
        out[f"only:{m}"] = frozenset({m})
    out["all"] = frozenset(MECHANISMS)
    for m in MECHANISMS:
        out[f"all-without:{m}"] = frozenset(MECHANISMS) - {m}
    return out


def _allowance(budget) -> dict:
    return {"tasks": int(budget), "requests": 400, "bytes": 80_000_000}


def budget_at(spec, cycle) -> int:
    """The daily autonomous allowance (tasks) in `cycle`: the world's budget_schedule where it has one, else budget."""
    schedule = spec.get("budget_schedule")
    return int(schedule[cycle]) if schedule else int(spec["budget"])


def _settings(mechanisms, budget) -> dict:
    base = {} if SHIPPED in mechanisms else dict(WAVE1_OFF_WEB) | STEP0_BASELINE
    cfg = base | {"enabled": True, "autonomous": NO_LOOP not in mechanisms, "daily_autonomous": _allowance(budget)}
    for m in sorted(set(mechanisms) - WORKER, key=lambda m: (not m.startswith("-"), m)):   # "-..." (off) first
        cfg |= ON[m]
    return cfg


def _major_minor(values) -> tuple[int, int] | None:
    for v in values or ():
        m = re.fullmatch(r"v?(\d+)\.(\d+)\.0", str(v))
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def investigate(db, task, docs, now, *, answer=None) -> dict:
    """One research task read from the given documents through the real modules: sources saved, claims extracted
    (claims.extract_claims, the task's objective as the question), origins stamped, clusters scored with the task's
    freshness class, consolidation (consolidate.run), the "still holds" predictions of new items
    (orchestrator._predict), the task completed with its report (the stored model answer is `answer`, else the first
    cluster's statement), the mode outcome, the autonomy ledger and the after-run hooks. Returns the consolidation."""
    from ...webcortex import autonomy, claims, consolidate, orchestrator, state
    tid = task["id"]
    freshness = consolidate._freshness(db, state.task(db, tid))
    found, source_ids = [], {}
    with store.write(db):
        for d in docs:
            source_ids[d.canonical_url] = state.save_source(db, tid, d, now)
    for d in docs:
        found += claims.extract_claims(d, task["objective"])
    cfg = state.get_config(db)
    origins = claims.stamp_origins(found, docs, attribution=cfg.attribution_independence, grouping=cfg.origin_grouping)
    clusters = [claims.score(c, now=now, freshness_class=freshness, origins=origins)
                for c in claims.cluster(found)] if found else []
    with store.write(db):
        state.save_clusters(db, tid, clusters, now, source_ids=source_ids)
    out = consolidate.run(db, tid, now)
    orchestrator._predict(db, out["provisional"], now)
    text = answer or (clusters[0].statement if clusters else "No source answered the question.")
    report = {"consolidation": {k: out[k] for k in ("provisional", "strengthened", "unchanged", "superseded",
                                                    "contested", "held", "pending")},
              "reasoning": {"text": text, "generated_by": "model:stub"}, "answer": []}
    usage = {"pages": len(docs), "requests": len(docs), "bytes": sum(len(d.text) for d in docs), "queries": 1}
    checkpoint = state.task(db, tid).get("checkpoint") or {}
    checkpoint["predictions_settled"] = list(out.get("predictions_settled") or [])[:50]
    with store.write(db):
        db.execute("UPDATE web_tasks SET status='completed', finished_at=?, updated_at=?, result=?, usage=?, "
                   "checkpoint=? WHERE id=?", (now, now, state.dumps(report), state.dumps(usage),
                                               state.dumps(checkpoint), tid))
        state.record_mode_outcome(db, task["mode"], "completed", usage, now)
        autonomy.settle(db, tid, usage, checkpoint, now)
        store.bump_state_version(db)
    orchestrator._after_run(db, tid, now)
    return out


def one_origin_confidence(db, task, doc, now) -> float | None:
    """What one origin page earns alone: the highest confidence claims.score gives a cluster of its claims."""
    from ...webcortex import claims, consolidate, state
    freshness = consolidate._freshness(db, state.task(db, task["id"]))
    found = claims.extract_claims(doc, task["objective"])
    return max((claims.score(c, now=now, freshness_class=freshness).confidence for c in claims.cluster(found)),
               default=None)


class _StubGateway:
    """A local 'model' for the reflection summary: always asserts a version no source states (major 9)."""

    def __init__(self, run):
        self.run = run

    def guard(self):
        return "local"

    def chat(self, messages, **kw):
        name = self.run.self_subject()
        text = f"{name} 9.9.0 is the latest {name} release." if name else "Nothing to add."
        return type("Reply", (), {"text": text, "model": "stub"})()


class Run:
    def __init__(self, spec, seed, mechanisms, db, generate=None):
        self.spec, self.seed, self.mechanisms, self.db = spec, seed, frozenset(mechanisms), db
        self.world = (generate or worlds.generate)(spec, seed)
        self.names = {s["name"]: s for s in self.world["subjects"]}
        self.pattern = re.compile(r"\b(" + "|".join(sorted(self.names, key=len, reverse=True)) + r")\b")
        self.cycle = 0
        self.now = T0
        self.tasks = {"owner": 0, "refresh": 0, "recheck": 0, "curiosity": 0, "idle": 0, "other": 0}
        self.idle = {}                    # Wave 3 lane V: autonomy.idle's actions ('queued', 'nothing', ...) and stops
        self.pages = 0
        self.rises = 0
        self.one_origin_looks = 0
        self.forgotten = set()            # subjects whose held items an owner forget removed
        self.series = []
        self.history = {}                 # subject -> list of (cycle, newest held version or None)
        self.calibration = []             # (confidence, true now)
        self.self_now = []
        self.launches = []                # (cycle, kind, subject) of every non-owner task executed (lane Y, M1)
        self.strategy = {"calls": 0, "changed": 0}    # strategies.rank calls / those that changed the order
        self.config = {}                  # READ_BACK as the individual holds them after start()
        self.allowance = []               # the daily autonomous tasks the individual held in each cycle 1..N
        self.looks = []                   # Wave 3 lane R: every autonomous look, what it met and what it changed
        # Wave 3 fix lane (review3 Q7): admitted content by the world's truth and page kinds (COUNTERS), and every
        # supersession classified against the truth, with each false one's (subject, cycle)
        self.admitted = {"rows": 0, "wrong": 0, "own_only": 0, "wire_only": 0}
        self.replaced = {"true": 0, "false": 0, "other": 0, "by_rule_true": 0, "by_rule_false": 0, "by_rule_other": 0}
        self.false_replaced = []
        self.divisions = []               # Wave 4 lane A: the division the timer's pass ran under, per cycle (or None)

    # ---- the world ----------------------------------------------------------------------------------------------
    def subject_in(self, text):
        m = self.pattern.search(text or "")
        return m.group(1) if m else None

    def self_subject(self):
        return self.self_now[0]["subject"] if self.self_now else None

    def truth(self, name, cycle=None):
        return self.names[name]["truth"].get(self.cycle if cycle is None else cycle)

    def born(self, cycle=None):
        c = self.cycle if cycle is None else cycle
        return [n for n, s in self.names.items() if s["born"] <= c]

    def held(self):
        """{subject: [(id, (major, minor) or None, confidence, version)]} of live knowledge rows naming a subject."""
        from ...webcortex import state
        out = {}
        for kid, statement, values, confidence, version in self.db.execute(
                f"SELECT id, statement, claim_values, confidence, version FROM web_knowledge WHERE status IN "
                f"({','.join('?' * len(state.LIVE_KNOWLEDGE))}) ORDER BY id", state.LIVE_KNOWLEDGE):
            name = self.subject_in(statement)
            if name is None:
                continue
            out.setdefault(name, []).append((kid, _major_minor(json.loads(values or "[]")), confidence, version))
        return out

    def newest(self, rows):
        return max(rows, key=lambda r: (r[3], r[0])) if rows else None

    # ---- one task -------------------------------------------------------------------------------------------------
    def _documents(self, name, published_floor):
        from ...webcortex.types import Document
        look = self.world["looks"].get((name, self.cycle)) or {"kind": "none", "pages": []}
        docs = []
        for page in look["pages"]:
            url = f"http://{page['host']}{page['path']}"
            published = min(T0 + page["published_cycle"] * self.spec["cycle_hours"] * 3600.0 - 3600.0,
                            published_floor)
            docs.append(Document(url=url, final_url=url, canonical_url=url, domain=page["host"], title=name,
                                 text=page["text"], retrieved_at=self.now, retrieval_mode="live",
                                 published_at=published))
        return look, docs

    def execute(self, task):
        """Run one queued task against the world's pages for its subject this cycle (see the module docstring)."""
        db, now = self.db, self.now
        tid = task["id"]
        origin = task["origin"] or ""
        kind = ("refresh" if origin.startswith("refresh:") and db.execute(
                    "SELECT 1 FROM web_events WHERE task_id=? AND kind='decision' AND "
                    "json_extract(detail,'$.autonomy.recheck') IS NOT NULL", (tid,)).fetchone() is None
                else "recheck" if origin.startswith("refresh:")
                else ("idle" if self._idle_pick(tid) else "curiosity") if origin == "curiosity"
                else "owner" if origin in ("console", "cli") else "other")
        self.tasks[kind] += 1
        name = self.subject_in(task["objective"])
        if kind != "owner":
            self.launches.append((self.cycle, kind, name))
        look, docs = self._documents(name, now - 600.0) if name else ({"kind": "none", "pages": []}, [])
        self.pages += len(docs)
        before = {r[0]: r[2] for rows in self.held().values() for r in rows}
        single = None
        if look["kind"] in ("one_origin", "wire_only", "echo_only") and docs:
            self.one_origin_looks += 1
            single = one_origin_confidence(db, task, docs[0], now)
        stated = [s["value"] for s in self.self_now if s["subject"] == name]
        prior = self._newest_version(name) if name and kind != "owner" else None
        out = investigate(db, task, docs, now,
                          answer=f"{name} {stated[0]} is the latest {name} release." if stated else None)
        if single is not None:
            for rows in self.held().values():
                for kid, _mm, conf, _v in rows:
                    if kid in before and conf > max(before[kid], single) + 1e-9:
                        self.rises += 1
        if name:
            self._count_admitted(name, look, out)
        if name and kind != "owner":      # Wave 3 lane R: what each autonomous look met and did (the diagnosis)
            self.looks.append({"cycle": self.cycle, "kind": kind, "subject": name, "before": prior,
                               "truth": self.truth(name), "read": _look_majority(self.world, name, self.cycle),
                               "look": look["kind"], "after": self._newest_version(name),
                               "outcome": {k: len(out.get(k) or []) for k in ("provisional", "strengthened",
                                                                             "unchanged", "superseded", "contested",
                                                                             "held")}}
                              | ({"candidate": self._idle_pick(tid)} if kind == "idle" else {}))
        return out

    def _version_of(self, kid):
        row = self.db.execute("SELECT claim_values, sources FROM web_knowledge WHERE id=?", (kid,)).fetchone()
        return (_major_minor(json.loads(row[0] or "[]")), json.loads(row[1] or "[]")) if row else (None, [])

    def _count_admitted(self, name, look, out):
        """Wave 3 fix lane: the rows this consolidation wrote and the supersessions it made, against the world's truth
        and its page kinds (COUNTERS); read-only."""
        truth = (1, self.truth(name))
        kinds = {f"http://{p['host']}{p['path']}": p.get("kind") for p in look["pages"]}
        for kid in out.get("provisional") or ():
            value, sources = self._version_of(kid)
            pages = [kinds.get(s.get("url")) for s in sources if isinstance(s, dict)]
            self.admitted["rows"] += 1
            self.admitted["wrong"] += value != truth
            self.admitted["own_only"] += bool(pages) and all(k == "echo" for k in pages)
            self.admitted["wire_only"] += bool(pages) and all(k in ("wire", "wire_copy") for k in pages)
        for entry in out.get("superseded") or ():
            old, new = self._version_of(entry["old"])[0], self._version_of(entry["new"])[0]
            kind = ("true" if new == truth and old != truth else "false" if old == truth and new != truth else
                    "other")
            self.replaced[kind] += 1
            if entry.get("by") == "rule":
                self.replaced["by_rule_" + kind] += 1
            if kind == "false":
                self.false_replaced.append((name, self.cycle))

    def _idle_pick(self, tid):
        """The candidate kind autonomy.idle queued this task as (its decision event, admission path 'idle'), else
        None: a curiosity task of the timer's pass (path 'tick')."""
        row = self.db.execute("SELECT json_extract(detail,'$.autonomy.kind') FROM web_events WHERE task_id=? AND "
                              "kind='decision' AND json_extract(detail,'$.autonomy.admission.path')='idle' LIMIT 1",
                              (tid,)).fetchone()
        return (row[0] or "unknown") if row else None

    def idle_investigations(self):
        """Wave 3 lane V (ADR-062): the worker's idle investigation as the product runs it. scheduler.work asks
        autonomy.idle for one investigation whenever nothing is queued; here each decision is made as by its own
        worker (idle_interval is per worker) at this cycle's instant (the harness compresses a simulated day to one
        instant, as it does for the owner and the timer), and the task it queues is executed at once, as a worker
        claims it. The one admission rule, the owner's quiet, the recorded enable, min_gain, the cooldown and the
        lineage are autonomy.idle's own; nothing here chooses a subject.
        The allowance: the day's autonomous tasks, WebConfig.daily_autonomous.tasks (the world's budget), counted as
        the timer counts them (scheduler.autonomous_usage: curiosity and refresh tasks of the last 24 h, the idle
        investigation's included). The harness stops asking when the count reaches the allowance, so the timer and
        the idle investigation together never use more looks than the budget the references are given. (Measured at
        0ee88fe, when autonomy.idle did not read that count itself and was bounded only by the D4 budgets and the
        request/byte allowance; since ADR-062 it refuses at the count too, autonomy._tasks_spent.)"""
        from ...webcortex import autonomy, scheduler, state
        cap = int(state.get_config(self.db).daily_autonomous.get("tasks", 0))
        for k in range(cap + 1):          # each queued decision adds one task to the count: at most `cap` rounds
            if scheduler.autonomous_usage(self.db, self.now)["tasks"] >= cap:
                self.idle["stopped_by_allowance"] = self.idle.get("stopped_by_allowance", 0) + 1
                return
            out = autonomy.idle(self.db, self.now, worker=f"cycles-idle-{self.cycle}-{k}")
            self.idle[out["action"]] = self.idle.get(out["action"], 0) + 1
            if out["action"] != "queued":
                return
            self.run_queued()
        raise RuntimeError("an idle investigation was queued that the day's task count did not see")

    def _order_picks(self) -> dict:
        """Re-check picks recorded with a recheck_order (ADR-060): all, those taken by the rotation floor (overdue),
        those taken because a newer version was seen or the item was marked."""
        rows = self.db.execute("SELECT json_extract(detail,'$.autonomy.recheck.order.overdue'), json_extract(detail,"
                               "'$.autonomy.recheck.order.newer_seen') FROM web_events WHERE kind='decision' AND "
                               "json_extract(detail,'$.autonomy.recheck.order') IS NOT NULL").fetchall()
        return {"recheck_order_picks": len(rows), "recheck_overdue_picks": sum(1 for o, _ in rows if o),
                "recheck_seen_picks": sum(1 for _, n in rows if n)}

    def _idle_activity(self) -> dict:
        """What the idle investigation did (Wave 3 lane V): its decisions by action, its tasks by candidate kind, its
        looks at subjects that held nothing (uncovered) and those after which the subject held an item."""
        idle = [lk for lk in self.looks if lk["kind"] == "idle"]
        kinds = {}
        for lk in idle:
            kinds[lk["candidate"]] = kinds.get(lk["candidate"], 0) + 1
        return {"idle_tasks": self.tasks["idle"], "idle_decisions": sum(v for k, v in self.idle.items()
                                                                        if k != "stopped_by_allowance"),
                "idle_actions": dict(self.idle), "idle_by_candidate": kinds,
                "idle_uncovered_looks": sum(1 for lk in idle if lk["before"] is None),
                "idle_covered": sum(1 for lk in idle if lk["before"] is None and lk["after"] is not None),
                "idle_covered_true": sum(1 for lk in idle if lk["before"] is None and lk["after"] == lk["truth"]),
                "idle_stopped_by_allowance": self.idle.get("stopped_by_allowance", 0)}

    def _division_activity(self) -> dict:
        """Wave 4 lane A: what the allowance division did: timer passes that held places for reaching and how many,
        its mean share, and the idle investigations it recorded as reaching or refreshing (their decision events)."""
        held = [d for d in self.divisions if d]
        paths = dict(self.db.execute("SELECT json_extract(detail,'$.autonomy.division.path'), count(*) FROM web_events "
                                     "WHERE kind='decision' AND json_extract(detail,'$.autonomy.division.path') IS NOT "
                                     "NULL GROUP BY 1").fetchall())
        return {"division_held_cycles": sum(1 for d in held if d.get("held")),
                "division_held_places": sum(d.get("held") or 0 for d in held),
                "division_mean_share": (sum(d.get("share") or 0 for d in held) / len(held)) if held else None,
                "division_reach_tasks": paths.get("reach", 0), "division_refresh_idle_tasks": paths.get("refresh", 0)}

    def diagnosis(self) -> dict:
        """Wave 4 lane A, step 2: per cycle, the looks the allowance bought for REFRESHING (a subject that held an
        item) and for REACHING (a subject that held nothing), and what each bought, read against the world's truth:
        made (the subject's newest held item became the true version), lost (it was true and is not), kept (true
        before and after); `last`: what the last look of each path in the cycle bought (+1 made, -1 lost, 0 else; None
        when the path had no look). A record of the looks made, not a counterfactual."""
        per = []
        for c in range(1, self.cycle + 1):
            row = {"cycle": c, "allowance": self.allowance[c - 1] if c - 1 < len(self.allowance) else None,
                   "timer": 0, "idle": 0}
            for path in ("refresh", "reach"):
                row |= {f"{path}_{k}": 0 for k in ("looks", "made", "lost", "kept")} | {f"{path}_last": None}
            for lk in self.looks:
                if lk["cycle"] != c:
                    continue
                row["idle" if lk["kind"] == "idle" else "timer"] += 1
                path = "reach" if lk["before"] is None else "refresh"
                made = lk["before"] != lk["truth"] and lk["after"] == lk["truth"]
                lost = lk["before"] == lk["truth"] and lk["after"] != lk["truth"]
                row[f"{path}_looks"] += 1
                row[f"{path}_made"] += made
                row[f"{path}_lost"] += lost
                row[f"{path}_kept"] += lk["before"] == lk["truth"] == lk["after"]
                row[f"{path}_last"] = 1 if made else -1 if lost else 0
            per.append(row)
        total = {}
        for path in ("refresh", "reach"):
            looks = sum(r[f"{path}_looks"] for r in per)
            net = sum(r[f"{path}_made"] - r[f"{path}_lost"] for r in per)
            last = [r[f"{path}_last"] for r in per if r[f"{path}_last"] is not None]
            total[path] = {"looks": looks, "made": sum(r[f"{path}_made"] for r in per),
                           "lost": sum(r[f"{path}_lost"] for r in per), "kept": sum(r[f"{path}_kept"] for r in per),
                           "net_per_look": net / looks if looks else None,
                           "last_unit_mean": sum(last) / len(last) if last else None}
        spent = sum(1 for r in per if r["allowance"] is not None and r["timer"] + r["idle"] >= r["allowance"])
        return {"per_cycle": per, "total": total, "cycles_allowance_spent": spent,
                "timer_spent_allowance_cycles": sum(1 for r in per if r["allowance"] is not None
                                                    and r["timer"] >= r["allowance"])}

    def _newest_version(self, name):
        """The newest held item's minor version (None: nothing held; -1: a version that is not 1.x), as score()."""
        top = self.newest(self.held().get(name) or [])
        return top[1][1] if top and top[1] and top[1][0] == 1 else (None if top is None else -1)

    def owner_research(self, name, at):
        from ...webcortex import knowledge, orchestrator
        task = orchestrator.submit(self.db, f"What is the latest {name} release?", at, mode="fact_verification",
                                   origin="console")
        saved, self.now = self.now, at
        try:
            out = self.execute(task)
            if self.world["accepted"][name]:
                for kid in out["provisional"]:
                    knowledge.review(self.db, kid, True, at)
        finally:
            self.now = saved

    def run_queued(self):
        from ...webcortex import state
        for (tid,) in self.db.execute("SELECT id FROM web_tasks WHERE status='queued' AND coalesce(stage,'')!="
                                      "'proposed' ORDER BY created_at, rowid").fetchall():
            self.execute(state.task(self.db, tid))

    # ---- one cycle --------------------------------------------------------------------------------------------------
    def plant_self(self):
        from ...instance import imagined
        self.self_now = list(self.world["self"].get(self.cycle) or [])
        for item in self.self_now:
            name = item["subject"]
            imagined.record(self.db, "inference",
                            payload={"text": f"{name} {item['value']} is the latest {name} release."},
                            assumptions=["the individual's own earlier output"],
                            uncertainty="asserted by the individual's own output; no source states it",
                            mechanism=MECHANISM, mechanism_version="1",
                            question=f"What is the latest {name} release?", now=self.now)

    def forget(self):
        from ...instance import governance
        for domain in self.world["forgets"].get(self.cycle) or []:
            before = self.held()
            governance.forget_source(self.db, domain, self.now)
            after = self.held()
            for name, rows in before.items():
                if rows and not after.get(name):
                    self.forgotten.add(name)

    def step(self):
        from ...webcortex import scheduler, state
        self.cycle += 1
        self.now = T0 + self.cycle * self.spec["cycle_hours"] * 3600.0
        if self.spec.get("budget_schedule"):       # a world whose allowance changes from cycle to cycle
            state.set_config(self.db, {"daily_autonomous": _allowance(budget_at(self.spec, self.cycle))}, self.now)
        self.allowance.append(state.get_config(self.db).daily_autonomous.get("tasks"))
        self.forget()
        self.plant_self()
        for name in sorted(n for n, s in self.names.items() if s["born"] == self.cycle):
            self.owner_research(name, self.now)
        if IDLE_FIRST in self.mechanisms:
            self.idle_investigations()
        report = scheduler.tick(self.db, self.now, None, queue=True)
        self.divisions.append(report.get("division"))
        self.run_queued()
        if IDLE in self.mechanisms:
            self.idle_investigations()
        self.score(report)

    def start(self):
        from ...webcortex import autonomy, state, strategies
        state.set_config(self.db, _settings(self.mechanisms, self.spec["budget"]), T0 - 30 * DAY)
        if NO_LOOP not in self.mechanisms:          # the owner's recorded enable (it also sets `autonomous` on)
            autonomy.enable(self.db, T0 - 30 * DAY)
        cfg = state.get_config(self.db)
        self.config = {k: getattr(cfg, k) for k in READ_BACK}
        # Wave 3 lane V: what the idle investigation runs under (the recorded enable and the autonomy policy)
        pol = autonomy.policy(self.db)
        self.config |= {"autonomy_enabled": autonomy.enabled(self.db),
                        "idle_investigation": "after the pass" if IDLE in self.mechanisms else
                        "before the pass" if IDLE_FIRST in self.mechanisms else "not run"} | {
            f"autonomy.{k}": pol[k] for k in ("min_gain", "cooldown", "idle_interval")}
        if "strategy_selection" in self.mechanisms:
            strategies.record(self.db, capability=strategies.CAPABILITY, level=3,
                              change="learning-cycles experiment: in force without the real gate",
                              evidence={}, generalisation={}, regressions={}, cost={}, reproducibility={},
                              decision="retain", decided_by="gate", now=T0 - 30 * DAY)
        for name in sorted(n for n, s in self.names.items() if s["born"] == 0):
            self.owner_research(name, T0 - self.names[name]["age_days"] * DAY)
        self.score(None)

    # ---- scoring ---------------------------------------------------------------------------------------------------
    def score(self, report):
        held = self.held()
        born = self.born()
        items = [(name, r) for name, rows in held.items() for r in rows]
        true_items = sum(1 for name, r in items if r[1] == (1, self.truth(name)))
        for name, r in items:
            self.calibration.append((float(r[2] or 0.0), r[1] == (1, self.truth(name))))
        correct = 0
        for name in born:
            top = self.newest(held.get(name) or [])
            version = top[1][1] if top and top[1] and top[1][0] == 1 else (None if top is None else -1)
            self.history.setdefault(name, []).append((self.cycle, version))
            correct += version == self.truth(name)
        k = self.db
        self.series.append({
            "cycle": self.cycle,
            "accuracy": true_items / len(items) if items else None,
            "coverage": sum(1 for n in born if held.get(n)) / len(born) if born else None,
            "correct": correct / len(born) if born else None,
            "held_items": len(items),
            "open_marks": k.execute("SELECT count(*) FROM web_rechecks WHERE status IN ('marked','queued')"
                                    ).fetchone()[0],
            "open_hypotheses": k.execute("SELECT count(*) FROM web_hypotheses WHERE status='open'").fetchone()[0],
            "open_questions": k.execute("SELECT count(*) FROM pending WHERE scope LIKE 'web:%' AND resolved_at IS "
                                        "NULL").fetchone()[0],
            "file_bytes": k.execute("PRAGMA page_count").fetchone()[0] * k.execute("PRAGMA page_size").fetchone()[0],
            "tick_skipped": len((report or {}).get("skipped") or []),
        })

    def summary(self) -> dict:
        db = self.db
        last = self.cycle
        # caught: each change of the truth after cycle 0 and whether / when the newest held item reached it
        changes, lags = 0, []
        for name, s in self.names.items():
            hist = dict(self.history.get(name, []))
            for c in range(max(1, s["born"] + 1), last + 1):
                if s["truth"][c] != s["truth"][c - 1]:
                    changes += 1
                    reached = [d for d in range(c, last + 1) if hist.get(d) is not None and hist[d] >= s["truth"][c]]
                    if reached:
                        lags.append(reached[0] - c)
        # persistence: runs of cycles in which the newest held item states a version not true then
        episodes, open_at_end = [], 0
        for name, hist in self.history.items():
            run = 0
            for c, v in hist:
                wrong = v is not None and v != self.truth(name, c)
                if wrong:
                    run += 1
                elif run:
                    episodes.append(run)
                    run = 0
            if run:
                episodes.append(run)
                open_at_end += 1
        # retention of what was right at RETENTION_CYCLE and never changed after it
        base = [n for n, hist in self.history.items() if dict(hist).get(RETENTION_CYCLE) == self.truth(n, RETENTION_CYCLE)
                and all(self.truth(n, c) == self.truth(n, RETENTION_CYCLE) for c in range(RETENTION_CYCLE, last + 1))]
        end = {n: dict(self.history[n]).get(last) for n in base}
        lost = sorted(n for n in base if end[n] is None and n not in self.forgotten)
        regressed = sorted(n for n in base if end[n] is not None and end[n] != self.truth(n, last))
        by_owner = sorted(n for n in base if end[n] is None and n in self.forgotten)
        kept = [n for n in base if end[n] == self.truth(n, last)]
        rows = db.execute("SELECT claim_values FROM web_knowledge").fetchall()
        majors = [(_major_minor(json.loads(v or "[]")) or (None, None))[0] for (v,) in rows]
        false_list = [{"subject": name, "cycle": c} | false_persistence(self, name, c)
                      for name, c in self.false_replaced]
        n = len(self.calibration)
        rate = sum(1 for _, t in self.calibration if t) / n if n else None
        counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                  ("web_knowledge", "web_claims", "web_hypotheses", "web_predictions", "web_events", "web_rechecks",
                   "web_premises", "web_reflections", "imagined", "pending", "web_hypothesis_details")}
        return {
            "mean": {k: scoring.spread([s[k] for s in self.series[1:]])["mean"]
                     for k in ("accuracy", "coverage", "correct")},
            "final": {k: self.series[-1][k] for k in ("accuracy", "coverage", "correct")},
            "series": [{k: s[k] for k in ("cycle", "accuracy", "coverage", "correct", "held_items", "open_marks",
                                          "open_hypotheses", "file_bytes")} for s in self.series],
            "changes": changes, "caught": len(lags), "caught_share": len(lags) / changes if changes else None,
            "catch_lag_cycles": scoring.spread(lags)["mean"],
            "error_episodes": len(episodes), "error_persistence_cycles": scoring.spread(episodes)["mean"],
            "error_persistence_max": max(episodes) if episodes else 0, "errors_open_at_end": open_at_end,
            "retention": {"base": len(base), "kept": len(kept), "share": len(kept) / len(base) if base else None,
                          "lost": len(lost), "regressed": len(regressed), "forgotten_by_owner": len(by_owner)},
            "calibration": {"n": n, "brier": sum((c - t) ** 2 for c, t in self.calibration) / n if n else None,
                            "constant_reference": rate * (1 - rate) if rate is not None else None,
                            "true_rate": rate},
            "self_admitted": sum(1 for m in majors if m == 9), "wire_admitted": sum(1 for m in majors if m == 7),
            "admitted": dict(self.admitted),
            "replacements": dict(self.replaced) | {
                "false_list": false_list,
                "false_persistence_max": max((f["cycles"] for f in false_list), default=0),
                "false_censored": sum(1 for f in false_list if f["censored"])},
            "one_origin_looks": self.one_origin_looks, "one_origin_rises": self.rises,
            "tasks": dict(self.tasks), "pages": self.pages,
            "open_marks_max": max(s["open_marks"] for s in self.series),
            "open_marks_end": self.series[-1]["open_marks"],
            "rows_end": counts, "file_bytes_end": self.series[-1]["file_bytes"],
            "file_bytes_growth_per_cycle": (self.series[-1]["file_bytes"] - self.series[0]["file_bytes"]) / max(1, last),
            "abandoned": db.execute("SELECT count(*) FROM web_hypotheses WHERE status='abandoned'").fetchone()[0],
            "reflections": counts["web_reflections"],
            # what each mechanism DID (lane Y, M1): its own records, and strategy selection's rankings
            "activity": {"hypothesis_detail_rows": counts["web_hypothesis_details"],
                         "premise_rows": counts["web_premises"], "recheck_rows": counts["web_rechecks"],
                         "recheck_tasks": self.tasks["recheck"], "strategy_rank_calls": self.strategy["calls"],
                         "strategy_rank_changed": self.strategy["changed"],
                         # Wave 3 lane R: re-check picks made under another order, and why (their decision events)
                         **self._order_picks(),
                         # Wave 3 lane V: the idle investigation's decisions, tasks, and the subjects it reached
                         **self._idle_activity(),
                         # Wave 4 lane A: the allowance division's holds and the paths of the idle investigations
                         **self._division_activity()},
            "launches": list(self.launches), "history": {n: list(h) for n, h in self.history.items()},
            "config": dict(self.config), "allowance": list(self.allowance), "looks": list(self.looks),
            "diagnosis": self.diagnosis(),
        }


def false_persistence(run, name, cycle) -> dict:
    """How long a false replacement (of `name`, in `cycle`) stayed wrong (review3 S-5): the cycles from it until the
    subject's newest held item states the truth again, split into cycles held wrong and cycles with nothing held, and
    whether the run ended first (censored: the true persistence is at least `cycles`)."""
    hist = dict(run.history.get(name, []))
    wrong = none = 0
    for d in range(cycle, run.cycle + 1):
        v = hist.get(d)
        if v is not None and v == run.truth(name, d):
            return {"cycles": wrong + none, "wrong": wrong, "none": none, "censored": False}
        none += v is None
        wrong += v is not None
    return {"cycles": wrong + none, "wrong": wrong, "none": none, "censored": True}


@contextmanager
def _environment(run_ref):
    """The host's resource gate held open (the experiment measures knowledge, not this host's memory pressure);
    the reflection's model summary answered by the stub (no model is called); strategy selection's rankings counted
    (a pass-through wrapper: the call and its result are unchanged; lane Y, M1)."""
    from ...resources import policy as rpolicy
    from ...inference import gateway
    from ...webcortex import strategies
    real_rank = strategies.rank

    def rank(db, now, chosen, **kw):
        ordered, record = real_rank(db, now, chosen, **kw)
        if run_ref[0] is not None:
            run_ref[0].strategy["calls"] += 1
            run_ref[0].strategy["changed"] += [c.get("key") for c in ordered] != [c.get("key") for c in chosen]
        return ordered, record
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(rpolicy, "background_gate", lambda *a, **k: {"allowed": True,
                                                                                           "reason": None}))
        stack.enter_context(mock.patch.object(gateway.Gateway, "from_db",
                                              classmethod(lambda cls, db: _StubGateway(run_ref[0]))))
        stack.enter_context(mock.patch.object(strategies, "rank", rank))
        yield


def run_world(spec, seed, mechanisms=frozenset(), *, cycles=None, generate=None) -> dict:
    """One run of one world and seed with the given lane K mechanisms on; `cycles` shortens the run (tests);
    `generate` is the world's generator (default cycles_splits.generate)."""
    started = time.perf_counter()
    spec = dict(spec) | ({"cycles": int(cycles)} if cycles is not None else {})
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-f-") as directory:
        db = store.create(Path(directory) / "f.sqlite", T0 - 31 * DAY, note="lane F: learning cycles")
        with closing(db):
            ref = [None]
            with _environment(ref), ExitStack() as ablation:
                if NO_RELEASE in mechanisms:     # Wave 4 lane A's ablation: the reserve held even with nothing to reach
                    from ...webcortex import autonomy
                    ablation.enter_context(mock.patch.object(autonomy, "_reach_open", lambda db, now: True))
                run = Run(spec, seed, mechanisms, db, generate)
                ref[0] = run
                run.start()
                for _ in range(spec["cycles"]):
                    run.step()
                out = run.summary()
    out["wall_seconds"] = time.perf_counter() - started
    return out


SCALARS = ("mean.accuracy", "mean.coverage", "mean.correct", "final.correct", "caught_share", "catch_lag_cycles",
           "error_persistence_cycles", "error_persistence_max", "errors_open_at_end", "retention.share",
           "retention.lost", "retention.regressed", "calibration.brier", "calibration.constant_reference",
           "self_admitted", "wire_admitted", "one_origin_rises", "pages", "wall_seconds", "file_bytes_end",
           "file_bytes_growth_per_cycle", "open_marks_max", "open_marks_end", "reflections", "abandoned",
           # the counts behind the shares above
           "changes", "caught", "error_episodes", "retention.base", "retention.forgotten_by_owner", "calibration.n",
           "one_origin_looks",
           # what each mechanism did, and whether it changed what was investigated or held (lane Y, M1)
           "activity.hypothesis_detail_rows", "activity.premise_rows", "activity.recheck_rows",
           "activity.recheck_tasks", "activity.strategy_rank_calls", "activity.strategy_rank_changed",
           "changed_launch_cycles", "changed_held_cycles",
           # Wave 3 lane R (ADR-060): what a re-check order did, and what it changed against lane K's order
           "activity.recheck_order_picks", "activity.recheck_overdue_picks", "activity.recheck_seen_picks",
           "order_changed_launch_cycles", "order_changed_held_cycles",
           # Wave 3 lane V (ADR-062): what the idle investigation did
           "activity.idle_tasks", "activity.idle_decisions", "activity.idle_uncovered_looks", "activity.idle_covered",
           "activity.idle_covered_true", "activity.idle_stopped_by_allowance",
           # Wave 3 fix lane (review3 Q7, S-5): admitted content without a version convention, and replacements
           "admitted.rows", "admitted.wrong", "admitted.own_only", "admitted.wire_only",
           "replacements.true", "replacements.false", "replacements.other", "replacements.by_rule_true",
           "replacements.by_rule_false", "replacements.false_persistence_max", "replacements.false_censored",
           # Wave 4 lane A (ADR-064): what the allowance division did, and what it changed against shipped+idle
           "activity.division_held_cycles", "activity.division_held_places", "activity.division_mean_share",
           "activity.division_reach_tasks", "activity.division_refresh_idle_tasks",
           "division_changed_launch_cycles", "division_changed_held_cycles",
           "diagnosis.total.refresh.looks", "diagnosis.total.refresh.made", "diagnosis.total.refresh.lost",
           "diagnosis.total.reach.looks", "diagnosis.total.reach.made", "diagnosis.total.reach.lost",
           "diagnosis.cycles_allowance_spent")


def _get(d, dotted):
    for part in dotted.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


def _changed(row, base) -> tuple[int, int]:
    """(cycles in which the autonomous tasks investigated other subjects than the baseline's run of the same world and
    seed, cycles in which a subject's newest held version differs from that run's): the world is written whole
    before the run, so both runs met the same pages."""
    def by_cycle(launches):
        out = {}
        for c, kind, name in launches:
            out.setdefault(c, []).append((kind, name))
        return out
    mine, theirs = by_cycle(row["launches"]), by_cycle(base["launches"])
    launch = sum(1 for c in set(mine) | set(theirs) if mine.get(c) != theirs.get(c))
    held = {}
    for history in (row["history"], base["history"]):
        for name, hist in history.items():
            for c, v in hist:
                held.setdefault((name, c), []).append(v)
    held_cycles = {c for (name, c), vs in held.items() if len(vs) != 2 or vs[0] != vs[1]}
    return launch, len(held_cycles)


RATE = "rate:"            # Wave 3 lane R: "rate:development", "rate:held_out" are the splits of cycles_rate_splits.py
REACH = "reach:"          # Wave 4 lane A: "reach:development", "reach:held_out" are the splits of cycles_reach_splits.py
SPLITS = ("development", "held_out", RATE + "development", RATE + "held_out", REACH + "development",
          REACH + "held_out")


def _mean_diagnosis(rows) -> dict:
    """Run.diagnosis() of several seeds: the totals' mean, and per cycle the mean of each count (None-free means of
    `last`, with the number of seeds in which the path had a look)."""
    def mean(values):
        xs = [v for v in values if v is not None]
        return sum(xs) / len(xs) if xs else None
    totals = {p: {k: mean([r["total"][p][k] for r in rows]) for k in rows[0]["total"][p]} for p in ("refresh", "reach")}
    per = []
    for i in range(len(rows[0]["per_cycle"])):
        cells = [r["per_cycle"][i] for r in rows]
        per.append({k: mean([c[k] for c in cells]) for k in cells[0]}
                   | {f"{p}_last_n": sum(1 for c in cells if c[f"{p}_last"] is not None) for p in ("refresh", "reach")})
    return {"total": totals, "per_cycle": per,
            "cycles_allowance_spent": mean([r["cycles_allowance_spent"] for r in rows]),
            "timer_spent_allowance_cycles": mean([r["timer_spent_allowance_cycles"] for r in rows])}


def _public(split) -> tuple[dict, object]:
    """(a split of cycles_splits.py or, named "rate:<split>", of cycles_rate_splits.py, its generator or None for
    cycles_splits.generate). The withheld worlds are reached only through final()."""
    if split == WITHHELD:
        from . import Refused
        raise Refused("the withheld learning-cycles worlds are scored only by cycles.final() (scripts/"
                      "bench_learning_cycles.py final), which records the run in the ledger first. Do not look at "
                      "them while developing.")
    if split.startswith(RATE):
        from . import cycles_rate_splits
        return cycles_rate_splits.CYCLES[split[len(RATE):]], cycles_rate_splits.generate
    if split.startswith(REACH):
        from . import cycles_reach_splits
        return cycles_reach_splits.CYCLES[split[len(REACH):]], cycles_reach_splits.generate
    return worlds.CYCLES[split], None


def run_split(split, *, configs=None, cycles=None, seeds=None) -> dict:
    spec, generate = _public(split)
    return _run_split(split, spec, generate, configs=configs, cycles=cycles, seeds=seeds)


def _run_split(split, spec, generate, *, configs=None, cycles=None, seeds=None) -> dict:
    configs = configs or configurations()
    seeds = tuple(seeds or spec["seeds"])
    raw = {(name, world["name"]): [run_world(world, seed, mechanisms, cycles=cycles, generate=generate)
                                   for seed in seeds]
           for name, mechanisms in configs.items() for world in spec["worlds"]}
    specs = {w["name"]: w for w in spec["worlds"]}
    for (name, world), rows in raw.items():
        base = raw.get(("baseline", world))
        # Wave 3 lane R: what a re-check order changed against lane K's order at the same share
        lane_k = "recheck:staleness" + ("/all" if name.endswith("/all") else "")
        same_share = raw.get((lane_k, world)) if name.startswith("recheck:") and name != lane_k else None
        # Wave 4 lane A: what a division changed against its reference (shipped+idle), same world and seed
        divided = raw.get((DIVISION_REFERENCE, world)) if name.startswith("division:") else None
        for i, r in enumerate(rows):
            r["division_changed_launch_cycles"], r["division_changed_held_cycles"] = \
                _changed(r, divided[i]) if divided else (None, None)
            r["changed_launch_cycles"], r["changed_held_cycles"] = _changed(r, base[i]) if base else (None, None)
            r["order_changed_launch_cycles"], r["order_changed_held_cycles"] = \
                _changed(r, same_share[i]) if same_share else (None, None)
            r["decomposition"] = decompose(r, specs[world] | ({"cycles": int(cycles)} if cycles is not None else {}),
                                           seeds[i], generate)
    out = {}
    for name, mechanisms in configs.items():
        per_world = {}
        for world in spec["worlds"]:
            rows = raw[(name, world["name"])]
            per_world[world["name"]] = {
                "seeds": list(seeds), "per_seed": [{k: _get(r, k) for k in SCALARS} | {
                    "tasks": r["tasks"], "false_replacements": r["replacements"]["false_list"]} for r in rows],
                "spread": {k: scoring.spread([_get(r, k) for r in rows]) for k in SCALARS},
                "series_mean": [{k: scoring.spread([r["series"][i][k] for r in rows])["mean"]
                                 for k in ("accuracy", "coverage", "correct", "held_items", "open_marks")}
                                | {"cycle": rows[0]["series"][i]["cycle"]} for i in range(len(rows[0]["series"]))],
                "tasks": {k: scoring.spread([r["tasks"][k] for r in rows])["mean"] for k in rows[0]["tasks"]},
                # the settings as the individual held them, and its daily allowance per cycle (Wave 3 step 0)
                "config_read_back": rows[0]["config"],
                "config_same_every_seed": all(r["config"] == rows[0]["config"] for r in rows),
                "allowance": rows[0]["allowance"],
                # Wave 3 lane R: the shortfall's decomposition (decompose), mean over seeds, and per seed
                "decomposition": _mean_decomposition([r["decomposition"] for r in rows]),
                "decomposition_per_seed": [{ref: r["decomposition"][ref] for ref in ("round_robin", "oracle")}
                                           for r in rows]}
            if split.startswith(REACH):      # Wave 4 lane A: the world's kind and the diagnosis (mean over seeds)
                per_world[world["name"]] |= {"kind": specs[world["name"]].get("kind"),
                                             "diagnosis": _mean_diagnosis([r["diagnosis"] for r in rows])}
        out[name] = {"mechanisms": sorted(mechanisms), "worlds": per_world}
    return {"split": split, "seeds": list(seeds), "configurations": out,
            "strategy_selection_note": "in force without passing the real gate (a 'retain' row written into each "
                                       "throwaway individual's ledger)"}


# ---- references from the world generator alone (lane Y, H4; after the benchmark-integrity review) ---------------------
#
# What the decay means needs a reference: the same worlds, the same budget where one applies, and no product code.
# Each reference holds one value per subject, starts from what the owner's research would give it at the subject's
# birth, and is scored like `correct` (share of the subjects so far whose held value is the true one now):
#   snapshot      a perfect snapshot at birth (the true version then), never refreshed: the ideal no-loop
#   round_robin   a blind schedule with the run's budget: each cycle the `budget` subjects checked longest ago
#                 (ties by name) are looked at once and the held value becomes what that look's pages state by
#                 majority (honest, lagging and copied pages each one vote; the wire story and its copies, and echo
#                 pages, are not read; a tie or a look with none of them teaches nothing); birth knowledge the same
#   round_robin_perfect   the same schedule with perfect reads (each look learns the truth): the schedule's own limit
#   oracle        knows which subjects changed: each cycle it refreshes, perfectly, up to `budget` subjects whose held
#                 value is wrong (in birth order): the bound for this budget
# These are not product configurations: they read the world's truth or pages directly.
REFERENCES = ("snapshot", "round_robin", "round_robin_perfect", "oracle")


def _look_majority(world, name, cycle):
    votes = {}
    for page in (world["looks"].get((name, cycle)) or {"pages": []})["pages"]:
        if page["kind"] not in ("honest", "lag", "copy"):
            continue
        m = re.search(r"\b1\.(\d+)\.0\b", page["text"])
        if m:
            votes[int(m.group(1))] = votes.get(int(m.group(1)), 0) + 1
    if not votes:
        return None
    top = max(votes.values())
    best = [v for v, n in votes.items() if n == top]
    return best[0] if len(best) == 1 else None


def references(spec, seed, generate=None, *, trace=None) -> dict:
    """{reference: [correct at cycle 0..N]} and the world's changes per cycle (see REFERENCES); the budget is the
    run's in each cycle (budget_at). `trace` (a dict, Wave 3 lane R): filled with {reference: {cycle: {subject:
    held value}}, reference + ":looks": [(cycle, subject)]} for the diagnosis; the series are unchanged by it."""
    world = (generate or worlds.generate)(spec, seed)
    subjects, n = world["subjects"], spec["cycles"]
    out = {}
    for ref in REFERENCES:
        held, last, series = {}, {}, []
        if trace is not None:
            trace[ref], trace[ref + ":looks"] = {}, []
        for c in range(n + 1):
            for s in subjects:
                if s["born"] == c:
                    held[s["name"]] = _look_majority(world, s["name"], c) if ref == "round_robin" else s["truth"][c]
                    last[s["name"]] = c
            if c >= 1 and ref != "snapshot":
                budget = budget_at(spec, c)
                older = [s for s in subjects if s["born"] < c]
                if ref == "oracle":
                    pick = [s for s in older if held[s["name"]] != s["truth"][c]][:budget]
                else:
                    pick = sorted(older, key=lambda s: (last[s["name"]], s["name"]))[:budget]
                for s in pick:
                    value = _look_majority(world, s["name"], c) if ref == "round_robin" else s["truth"][c]
                    if value is not None:
                        held[s["name"]] = value
                    last[s["name"]] = c
                    if trace is not None:
                        trace[ref + ":looks"].append((c, s["name"]))
            live = [s for s in subjects if s["born"] <= c]
            series.append(sum(1 for s in live if held[s["name"]] == s["truth"][c]) / len(live))
            if trace is not None:
                trace[ref][c] = dict(held)
        out[ref] = series
    changes = sum(1 for s in subjects for c in range(s["born"] + 1, n + 1) if s["truth"][c] != s["truth"][c - 1])
    return {"series": out, "changes_per_cycle": changes / n}


# ---- the diagnosis of the loop's shortfall (Wave 3 lane R, ADR-060) ------------------------------------------------
#
# decompose() splits the gap between a reference (the blind round-robin, the oracle) and a measured run, in the units
# of PRIMARY (per cycle: subjects / subjects so far; mean over cycles 1..N), into the subject-cycles where the
# reference holds the true version and the run does not, by why the run does not:
#   uncovered          the run holds nothing about the subject (the owner's research gave it no knowledge)
#   wrong_source       the run's newest item is not a 1.x version (the wire story or the individual's own output)
#   found_not_revised  stale 1.x; since it went wrong an autonomous look read the true version (the look's majority,
#                      as the round-robin reads it) and the newest item stayed wrong (contested, held: lane S's part)
#   looked_unreadable  stale; it was looked at since it went wrong, but no look's pages showed the true version
#   not_looked         stale; no autonomous look at it since it went wrong (the order and the allowance)
# minus run_ahead: the subject-cycles where the run holds the true version and the reference does not. The parts sum
# to the gap. Beside it, every autonomous look by what it met (confirmed an item already true, caught the change,
# found it and did not revise, read nothing true, nothing held) and the allowance left unused; and the reference's
# own looks, the share of them spent on subjects whose held value was already true.
GAP_PARTS = ("uncovered", "wrong_source", "found_not_revised", "looked_unreadable", "not_looked", "run_ahead")
LOOK_PARTS = ("confirm", "caught", "found_not_revised", "unreadable", "nothing_held")


def decompose(row, spec, seed, generate=None) -> dict:
    """{reference: {"gap", parts...}, "looks": {...}, "reference_looks": {...}} for one run summary (see above)."""
    world = (generate or worlds.generate)(spec, seed)
    subjects = {s["name"]: s for s in world["subjects"]}
    n = spec["cycles"]
    trace = {}
    references(spec, seed, generate, trace=trace)
    hist = {name: dict(h) for name, h in row["history"].items()}
    looks_at = {}
    for look in row["looks"]:
        looks_at.setdefault(look["subject"], []).append(look)
    out = {}
    for ref in ("round_robin", "oracle"):
        parts = dict.fromkeys(GAP_PARTS, 0.0)
        for c in range(1, n + 1):
            live = [s for s in subjects.values() if s["born"] <= c]
            for s in live:
                name, truth = s["name"], s["truth"][c]
                mine = hist.get(name, {}).get(c)
                ref_right, my_right = trace[ref][c].get(name) == truth, mine == truth
                if my_right and not ref_right:
                    parts["run_ahead"] -= 1 / len(live)
                if ref_right and not my_right:
                    if mine is None:
                        part = "uncovered"
                    elif mine == -1:
                        part = "wrong_source"
                    else:
                        start = c          # the first cycle of the run of cycles in which the held value is wrong
                        while start - 1 >= s["born"] and hist.get(name, {}).get(start - 1) not in (
                                None, s["truth"][start - 1]):
                            start -= 1
                        since = [lk for lk in looks_at.get(name, []) if start <= lk["cycle"] <= c]
                        part = ("not_looked" if not since else
                                "found_not_revised" if any(lk["read"] == lk["truth"] for lk in since) else
                                "looked_unreadable")
                    parts[part] += 1 / len(live)
        parts = {k: v / n for k, v in parts.items()}
        out[ref] = {"gap": sum(parts.values())} | parts
    looks = dict.fromkeys(LOOK_PARTS, 0)
    by_kind = {}
    for lk in row["looks"]:
        part = ("nothing_held" if lk["before"] is None else "confirm" if lk["before"] == lk["truth"] else
                "caught" if lk["after"] == lk["truth"] else
                "found_not_revised" if lk["read"] == lk["truth"] else "unreadable")
        looks[part] += 1
        by_kind.setdefault(lk["kind"], dict.fromkeys(LOOK_PARTS, 0))[part] += 1
    allowance = sum(row["allowance"]) if row.get("allowance") else sum(budget_at(spec, c) for c in range(1, n + 1))
    out["looks"] = looks | {"total": len(row["looks"]), "allowance": allowance,
                            "unused": allowance - len(row["looks"]), "by_kind": by_kind}
    ref_looks = {}
    for ref in ("round_robin", "oracle"):
        confirm = sum(1 for c, name in trace[ref + ":looks"]
                      if trace[ref].get(c - 1, {}).get(name) == subjects[name]["truth"][c])
        ref_looks[ref] = {"total": len(trace[ref + ":looks"]), "confirm": confirm}
    out["reference_looks"] = ref_looks
    return out


def _mean_decomposition(rows) -> dict:
    """decompose() results of several seeds: the mean of every number (looks by kind included)."""
    def mean(values):
        return sum(values) / len(values)
    out = {ref: {k: mean([r[ref][k] for r in rows]) for k in rows[0][ref]} for ref in ("round_robin", "oracle")}
    out["looks"] = {k: mean([r["looks"][k] for r in rows]) for k in rows[0]["looks"] if k != "by_kind"}
    kinds = sorted({k for r in rows for k in r["looks"]["by_kind"]})
    out["looks"]["by_kind"] = {k: {p: mean([r["looks"]["by_kind"].get(k, {}).get(p, 0) for r in rows])
                                   for p in LOOK_PARTS} for k in kinds}
    out["reference_looks"] = {ref: {k: mean([r["reference_looks"][ref][k] for r in rows]) for k in ("total", "confirm")}
                              for ref in ("round_robin", "oracle")}
    return out


def run_references(split, *, seeds=None) -> dict:
    """Per world: budget, changes per cycle, and per reference the mean of `correct` over cycles 1..N (spread over
    seeds, as PRIMARY), at cycle 0 and at cycle N, and the per-cycle mean series."""
    spec, generate = _public(split)
    return _run_references(split, spec, generate, seeds=seeds)


def _run_references(split, spec, generate, *, seeds=None) -> dict:
    seeds = tuple(seeds or spec["seeds"])
    out = {}
    for world in spec["worlds"]:
        rows = [references(world, seed, generate) for seed in seeds]
        entry = {"budget": world["budget"], "cycles": world["cycles"],
                 "budget_schedule": list(world["budget_schedule"]) if world.get("budget_schedule") else None,
                 "changes_per_cycle": scoring.spread([r["changes_per_cycle"] for r in rows])}
        for ref in REFERENCES:
            series = [r["series"][ref] for r in rows]
            entry[ref] = {"mean_correct": scoring.spread([sum(s[1:]) / len(s[1:]) for s in series]),
                          "per_seed_mean_correct": [sum(s[1:]) / len(s[1:]) for s in series],
                          "cycle0": scoring.spread([s[0] for s in series])["mean"],
                          "final": scoring.spread([s[-1] for s in series])["mean"],
                          "series_mean": [scoring.spread([s[i] for s in series])["mean"]
                                          for i in range(len(series[0]))]}
        out[world["name"]] = entry
    return {"split": split, "seeds": list(seeds), "references": list(REFERENCES), "worlds": out,
            "code": "src/neuroscifi/acceptance/cognition/cycles.py: references, run_references (from cycles_splits."
                    "generate alone; no product code)"}


# ---- the withheld worlds (Wave 3 step 0): one look each, recorded before it scores ---------------------------------------
#
# cycles_withheld.py holds worlds no lane may tune on. final() is the only code that loads them: it refuses without a
# note and on a tree that is dirty or not committed (the ledger itself excepted, as cognition.environment() reads it),
# then appends its line to the ledger of final runs (cognition.LEDGER) BEFORE it loads or scores anything, so a run
# that crashes still counts as a look. scripts/bench_learning_cycles.py final also refuses an output path inside the
# checkout and another tree's source. The ledger line names the family "learning_cycles_withheld" (not "withheld",
# the cognitive benchmark's family, which strategies.gate reads).
FINAL_FAMILY = "learning_cycles_withheld"
FINAL_CONFIGS = ("reference:no-loop", "baseline", "shipped")      # the unchanged system's look (step 0)


WITHHELD_FILE = Path(__file__).resolve().parent / "cycles_withheld.py"
# The withheld worlds' SHA-256, pinned when they were committed (26409bc; the pin first in a test, d0b6b02). final()
# compares the file's bytes with it BEFORE loading them (review3 0-2): a changed worlds file is never scored.
WITHHELD_SHA256 = "0a4831aa07e30cd31a2f4e3270e59ef20338ed2ebb229b22c77f1448628ebc0d"


def withheld_digest() -> str:
    """sha256 of the withheld worlds file's bytes (nothing is decoded or loaded)."""
    import hashlib
    return hashlib.sha256(WITHHELD_FILE.read_bytes()).hexdigest()


def _withheld():
    """(the withheld split, its generator): loaded here only, and only by final()."""
    from . import cycles_withheld
    return cycles_withheld.CYCLES[cycles_withheld.SPLIT], cycles_withheld.generate


def world_digest(world) -> str:
    """sha256 of everything a world says (truth, pages, own outputs, forgets, acceptances): two runs that report the
    same digest met the same world."""
    import hashlib
    body = {"subjects": world["subjects"], "self": {str(c): v for c, v in world["self"].items()},
            "looks": sorted([name, c, look] for (name, c), look in world["looks"].items()),
            "forgets": {str(c): v for c, v in world["forgets"].items()}, "accepted": world["accepted"]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def final(note, *, configs=None, mechanism="baseline") -> dict:
    """THE look at the withheld worlds for `configs` ({name: mechanisms}; default FINAL_CONFIGS), recorded in the
    ledger as `mechanism` before anything is loaded or scored. Returns run_split's result for the split, the
    references from the generator alone and each world's digest per seed."""
    from datetime import datetime, timezone
    from .. import cognition
    if not (note or "").strip():         # review3c F5: a note of only spaces is no reason either
        raise cognition.Refused("final: a note is required (it is the ledger line's reason)")
    known = configurations() | EXTRA
    configs = configs or {k: known[k] for k in FINAL_CONFIGS}
    env = cognition.environment()
    if env["git_dirty"] is not False or not env["git_commit"]:      # review3 0-1: unknown is not clean
        raise cognition.Refused("final: the tree must be clean and committed (and git must say so), so the ledger "
                                "line names the code it measured")
    digest = withheld_digest()
    if digest != WITHHELD_SHA256:
        raise cognition.Refused(f"final: the withheld worlds file's SHA-256 is {digest[:12]}..., not the pinned "
                                f"{WITHHELD_SHA256[:12]}...: it was changed and is not scored")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"at": at, "commit": env["git_commit"], "dirty": env["git_dirty"], "mechanism": mechanism, "ablate": [],
             "families": [FINAL_FAMILY], "configurations": sorted(configs), "note": note,
             "withheld_sha256": digest}
    lines = cognition._record_final(cognition.LEDGER, entry)
    spec, generate = _withheld()
    out = {"generated_at": at, "environment": env, "ledger_line": len(lines), "ledger_entry": entry}
    out |= _run_split(WITHHELD, spec, generate, configs=configs)
    out["references"] = _run_references(WITHHELD, spec, generate) | {
        "code": "src/neuroscifi/acceptance/cognition/cycles.py: references, _run_references (from the withheld "
                "generator alone; no product code)"}
    out["world_digests"] = {w["name"]: {str(seed): world_digest(generate(w, seed)) for seed in spec["seeds"]}
                            for w in spec["worlds"]}
    out |= look_view(out)
    return out


def look_view(result) -> dict:
    """The look's report fields (LOOK_FIELDS) per configuration, world and seed, the false replacements with their
    persistence and censoring, the reference each configuration is compared with (LOOK_PAIRS) and what each
    admitted-content counter can see (COUNTERS). A view of run_split's result: nothing is recomputed."""
    table = {}
    for name, conf in result["configurations"].items():
        table[name] = {w: [{"seed": s} | {k: row.get(k) for k in LOOK_FIELDS} |
                           {"false_replacements": row.get("false_replacements") or []}
                           for s, row in zip(entry["seeds"], entry["per_seed"])]
                       for w, entry in conf["worlds"].items()}
    return {"look": table, "look_pairs": {k: v for k, v in LOOK_PAIRS.items() if k in result["configurations"]},
            "counters": COUNTERS}


# ---- the verdict, written before the held-out run (roadmap §2 applied to this experiment) -----------------------------
#
# PRIMARY = mean over cycles 1..N of `correct` (the share of the subjects so far whose newest held item states the
# version true now). Per held-out world, a configuration's gain is the mean over seeds of its paired difference with
# the baseline (same world, same seed); the baseline's spread is the sample sd of its PRIMARY over the seeds.
#   (a) alone beats:   for every held-out world, gain of `only:<m>` > the baseline's spread
#   (b) ablation:      `all` beats the baseline by more than the spread in every world, and `all-without:<m>` does not
#                      in at least one world (None, not applicable, when `all` itself does not beat the baseline)
#   (c) retention:     subjects lost or regressed (retention) not above the baseline's, in every world and seed
#   (d) adversarial:   self_admitted, wire_admitted and one_origin_rises stay 0 (the experiment's own adversarial
#                      checks); the cognitive benchmark's adversarial family is reported beside it, not here
# retain = (a) and (b) and (c) and (d); revise = not retain, (c) and (d) hold, and the gain is positive in every world
# with the configuration better on at least 3 paired seeds per world, or (a) holds in some world; else "no evidence"
# (identical_to_baseline marks every paired difference 0).
PRIMARY = "mean.correct"


def _paired(result, config, world, metric=PRIMARY):
    base = [r[metric] for r in result["configurations"]["baseline"]["worlds"][world]["per_seed"]]
    rows = [r[metric] for r in result["configurations"][config]["worlds"][world]["per_seed"]]
    return [r - b for r, b in zip(rows, base)]


def _beats(result, config, world):
    diffs = _paired(result, config, world)
    spread = result["configurations"]["baseline"]["worlds"][world]["spread"][PRIMARY]["sd"] or 0.0
    gain = sum(diffs) / len(diffs)
    return gain > spread, gain, spread, diffs


def _safe(result, config):
    mine = result["configurations"][config]["worlds"]
    base = result["configurations"]["baseline"]["worlds"]
    retention = all((r["retention.lost"] or 0) + (r["retention.regressed"] or 0) <=
                    (b["retention.lost"] or 0) + (b["retention.regressed"] or 0)
                    for w in mine for r, b in zip(mine[w]["per_seed"], base[w]["per_seed"]))
    adversarial = all(r["self_admitted"] == 0 and r["wire_admitted"] == 0 and r["one_origin_rises"] == 0
                      for w in mine for r in mine[w]["per_seed"])
    return retention, adversarial


def verdicts(result) -> dict:
    """{mechanism: {"verdict", "checks", "per_world"}, "all": ...} under the rule above (a run_split result)."""
    names = list(result["configurations"]["baseline"]["worlds"])
    out = {}
    all_beats = {w: _beats(result, "all", w)[0] for w in names}
    for m in MECHANISMS:
        per_world = {}
        for w in names:
            beats, gain, spread, diffs = _beats(result, f"only:{m}", w)
            contrib = [a - b for a, b in zip(_paired(result, "all", w), _paired(result, f"all-without:{m}", w))]
            per_world[w] = {"gain": gain, "baseline_sd": spread, "beats": beats, "paired": diffs,
                            "better_seeds": sum(1 for d in diffs if d > 1e-12),
                            "worse_seeds": sum(1 for d in diffs if d < -1e-12),
                            "all_minus_ablated": sum(contrib) / len(contrib),
                            "ablated_beats": _beats(result, f"all-without:{m}", w)[0]}
        a = all(per_world[w]["beats"] for w in names)
        b = (any(not per_world[w]["ablated_beats"] for w in names)) if all(all_beats.values()) else None
        retention, adversarial = _safe(result, f"only:{m}")
        if a and b and retention and adversarial:
            verdict = "retain"
        elif retention and adversarial and (
                all(per_world[w]["gain"] > 0 and per_world[w]["better_seeds"] >= 3 for w in names)
                or any(per_world[w]["beats"] for w in names)):
            verdict = "revise"
        else:
            verdict = "no evidence"
        out[m] = {"verdict": verdict,
                  "identical_to_baseline": all(abs(d) < 1e-12 for w in names for d in per_world[w]["paired"]),
                  "checks": {"a_alone_beats_spread": a, "b_ablation_removes": b, "c_retention": retention,
                             "d_adversarial": adversarial}, "per_world": per_world}
    retention, adversarial = _safe(result, "all")
    out["all"] = {"per_world": {w: dict(zip(("beats", "gain", "baseline_sd", "paired"), _beats(result, "all", w)))
                                for w in names}, "c_retention": retention, "d_adversarial": adversarial}
    return out


# ---- Wave 3 lane R: the verdict on the re-check orders, written before the held-out run (ADR-060) ---------------------
#
# The same PRIMARY, pairing and spread as above, for every configuration of order_configurations() against `baseline`
# (today's loop), on each held-out world:
#   (a) beats:      the paired gain over the baseline > the baseline's sd, in every world
#   (b) ablation:   for the combination (COMBINATION) only: it beats in every world, and each ablation (ABLATIONS: the
#                   combination without one ingredient) does not beat in at least one world; None (not applicable)
#                   for every other configuration, or when the combination does not beat
#   (c) retention:  subjects lost plus regressed not above the baseline's, in every world and seed
#   (d) safety:     own-output versions admitted, wrong-story versions admitted and one-origin confidence rises each
#                   not above the baseline's, in every world and seed (Wave 3 addendum: a gain that comes with more
#                   wrongly admitted content is not a gain); the counts themselves are reported beside the gain
# retain = (a), (c), (d) and (b) where it applies; revise = not retain, (c) and (d), and the gain positive in every world
# with at least 3 paired seeds better, or (a) in some world; "not exercised" when the order changed no launch against
# lane K's order at the same share in any run (order_changed_launch_cycles all 0); else "no evidence".
# Descriptive, not part of the rule: the paired difference against the product's blind rotation at the same share
# (`recheck:rotation[/all]`: the "at least as good as a blind schedule" question).
COMBINATION = "recheck:rate+seen+rotation/all"
ABLATIONS = {"rate": "recheck:seen+rotation/all", "seen": "recheck:rate+rotation/all",
             "rotation": "recheck:rate+seen/all", "whole allowance": "recheck:rate+seen+rotation"}
SAFETY = ("self_admitted", "wire_admitted", "one_origin_rises")
LOSSES = ("retention.lost", "retention.regressed")


def _not_above(result, config, keys, *, total=False) -> bool:
    """Every world and seed: the configuration's counts not above the baseline's (each, or their total)."""
    mine = result["configurations"][config]["worlds"]
    base = result["configurations"]["baseline"]["worlds"]
    for w in mine:
        for r, b in zip(mine[w]["per_seed"], base[w]["per_seed"]):
            pairs = [(sum(r[k] or 0 for k in keys), sum(b[k] or 0 for k in keys))] if total else \
                [(r[k] or 0, b[k] or 0) for k in keys]
            if any(x > y for x, y in pairs):
                return False
    return True


def order_verdicts(result) -> dict:
    """{configuration: {"verdict", "checks", "per_world"}} under the rule above (a run_split result over
    order_configurations())."""
    confs = result["configurations"]
    names = list(confs["baseline"]["worlds"])
    combination_beats = COMBINATION in confs and all(_beats(result, COMBINATION, w)[0] for w in names)
    out = {}
    for config in confs:
        if config in ("baseline", "reference:no-loop"):
            continue
        blind = "recheck:rotation" + ("/all" if config.endswith("/all") else "")
        per_world = {}
        for w in names:
            beats, gain, spread, diffs = _beats(result, config, w)
            rows = confs[config]["worlds"][w]["per_seed"]
            vs_blind = ([a[PRIMARY] - b[PRIMARY] for a, b in zip(rows, confs[blind]["worlds"][w]["per_seed"])]
                        if blind in confs else [])
            per_world[w] = {"gain": gain, "baseline_sd": spread, "beats": beats, "paired": diffs,
                            "better_seeds": sum(1 for d in diffs if d > 1e-12),
                            "worse_seeds": sum(1 for d in diffs if d < -1e-12),
                            "vs_blind_rotation": sum(vs_blind) / len(vs_blind) if vs_blind else None,
                            "vs_blind_rotation_better_seeds": sum(1 for d in vs_blind if d > 1e-12),
                            "vs_blind_rotation_worse_seeds": sum(1 for d in vs_blind if d < -1e-12),
                            "safety": {k: sum(r[k] or 0 for r in rows) for k in SAFETY},
                            "baseline_safety": {k: sum(r[k] or 0 for r in confs["baseline"]["worlds"][w]["per_seed"])
                                                for k in SAFETY},
                            "retention_lost_or_regressed": sum(r[k] or 0 for r in rows for k in LOSSES),
                            "order_changed_launch_cycles": [r.get("order_changed_launch_cycles") for r in rows]}
        a = all(per_world[w]["beats"] for w in names)
        b = None
        if config == COMBINATION and combination_beats:
            b = all(any(not _beats(result, ablated, w)[0] for w in names)
                    for ablated in ABLATIONS.values() if ablated in confs)
        retention = _not_above(result, config, LOSSES, total=True)
        safety = _not_above(result, config, SAFETY)
        changed = [c for w in names for c in per_world[w]["order_changed_launch_cycles"] if c is not None]
        if a and retention and safety and b is not False:
            verdict = "retain"
        elif retention and safety and (all(per_world[w]["gain"] > 0 and per_world[w]["better_seeds"] >= 3
                                           for w in names) or any(per_world[w]["beats"] for w in names)):
            verdict = "revise"
        elif changed and not any(changed):
            verdict = "not exercised"
        else:
            verdict = "no evidence"
        out[config] = {"verdict": verdict, "checks": {"a_beats_spread": a, "b_ablation_removes": b,
                                                      "c_retention": retention, "d_safety": safety},
                       "per_world": per_world}
    return out


# ---- Wave 3 lane V: the verdict on the idle investigation, written before the held-out run (ADR-062) -----------------
#
# The same PRIMARY and spread, for each configuration of idle_configurations() with the idle investigation against the
# same configuration without it (IDLE_PAIRS: `idle` and `idle-first` against `baseline`, `shipped+idle` against
# `shipped`), paired by world and seed, on each held-out world:
#   (a) beats:      the paired gain > the sd of the configuration without it, in every world
#   (c) retention:  subjects lost plus regressed not above the configuration without it, in every world and seed
#   (d) safety:     own-output versions admitted, wrong-story versions admitted and one-origin confidence rises each
#                   not above the configuration without it, in every world and seed (a gain that comes with more
#                   wrongly admitted content is not a gain); the counts themselves are reported beside the gain
# gain = (a), (c), (d); revise = not gain, (c), (d), and the gain positive in every world with at least 3 paired seeds
# better, or (a) in some world; "not exercised" when the idle investigation queued no task in any run; else "no
# evidence". Descriptive, not part of the rule: the paired change of coverage and of the decomposition's `uncovered`
# part against the blind round-robin, and the idle investigation's activity.
IDLE_PAIRS = {"idle": "baseline", "idle-first": "baseline", "shipped+idle": "shipped"}


def idle_verdicts(result) -> dict:
    """{configuration: {"verdict", "against", "checks", "per_world"}} under the rule above (a run_split result over
    idle_configurations())."""
    confs = result["configurations"]
    out = {}
    for config, against in IDLE_PAIRS.items():
        if config not in confs or against not in confs:
            continue
        per_world, retention, safety, tasks = {}, True, True, []
        for w, mine in confs[config]["worlds"].items():
            rows, base = mine["per_seed"], confs[against]["worlds"][w]["per_seed"]
            diffs = [r[PRIMARY] - b[PRIMARY] for r, b in zip(rows, base)]
            spread = confs[against]["worlds"][w]["spread"][PRIMARY]["sd"] or 0.0
            gain = sum(diffs) / len(diffs)
            retention &= all(sum(r[k] or 0 for k in LOSSES) <= sum(b[k] or 0 for k in LOSSES)
                             for r, b in zip(rows, base))
            safety &= all((r[k] or 0) <= (b[k] or 0) for r, b in zip(rows, base) for k in SAFETY)
            tasks += [r["activity.idle_tasks"] or 0 for r in rows]
            uncovered = [mine["decomposition"]["round_robin"]["uncovered"],
                         confs[against]["worlds"][w]["decomposition"]["round_robin"]["uncovered"]]
            per_world[w] = {"gain": gain, "against_sd": spread, "beats": gain > spread, "paired": diffs,
                            "better_seeds": sum(1 for d in diffs if d > 1e-12),
                            "worse_seeds": sum(1 for d in diffs if d < -1e-12),
                            "coverage_change": sum(r["mean.coverage"] - b["mean.coverage"]
                                                   for r, b in zip(rows, base)) / len(rows),
                            "uncovered_part_change": uncovered[0] - uncovered[1],
                            "safety": {k: sum(r[k] or 0 for r in rows) for k in SAFETY},
                            "against_safety": {k: sum(b[k] or 0 for b in base) for k in SAFETY},
                            "retention_lost_or_regressed": sum(r[k] or 0 for r in rows for k in LOSSES),
                            "idle_tasks": [r["activity.idle_tasks"] for r in rows]}
        names = list(per_world)
        a = all(per_world[w]["beats"] for w in names)
        if a and retention and safety:
            verdict = "gain"
        elif retention and safety and (all(per_world[w]["gain"] > 0 and per_world[w]["better_seeds"] >= 3
                                           for w in names) or any(per_world[w]["beats"] for w in names)):
            verdict = "revise"
        elif not any(tasks):
            verdict = "not exercised"
        else:
            verdict = "no evidence"
        out[config] = {"verdict": verdict, "against": against,
                       "checks": {"a_beats_spread": a, "c_retention": retention, "d_safety": safety},
                       "per_world": per_world}
    return out


# ---- Wave 4 lane A: the verdict on dividing the allowance, written before any held-out run (ADR-064) ------------------
#
# Each division configuration (division_configurations(), every name starting "division:") against DIVISION_REFERENCE
# (`shipped+idle`, the product as shipped with autonomy on), paired by world and seed, on each world of a split of
# cycles_reach_splits.py. PRIMARY as above; sd = the sample sd of the reference's PRIMARY over the seeds (the smallest
# gain the rule can see); a world's `kind` is the one cycles_reach_splits.py states (written before any division ran):
#   (a) gain:       the paired gain > sd in every world whose kind is not "covered" (where reaching can matter)
#   (b) no harm:    the paired gain >= -sd in every world, "covered" ones included (a division that always favours
#                   reaching must not cost refreshing more than the spread)
#   (c) retention:  subjects lost plus regressed, summed over the seeds of a world, not above the reference's, in
#                   every world
#   (d) safety:     own-output versions admitted, wrong-story versions admitted, one-origin confidence rises and rows
#                   admitted stating a version that was not true (admitted.wrong, no version convention), each summed
#                   over the seeds of a world, not above the reference's, in every world (reaching more subjects meets
#                   more sources: a gain with more wrongly admitted content is not a gain); the counts are reported
#                   beside every gain
# verdict: "unsafe" when (d) fails; else "harm" when (b) or (c) fails; else "gain" when (a) holds; else "revise" when
# the gain is positive in every non-covered world with at least n-1 of n paired seeds better, or (a) holds in some
# non-covered world; else "not exercised" when the division changed no launch against the reference in any run
# (division_changed_launch_cycles all 0); else "no evidence".
# Reported, not part of the rule: the paired sd (paired noise), coverage change, the reach / refresh looks and their
# yield (diagnosis), the division's activity, the ablations (`/no-release` against `division:reserve-0.5`,
# `/idle-first` against `division:reserve-0.5`), the gap to the blind round-robin.
#
# The choice, made on DEVELOPMENT only, before the held-out run (DIVISION_CANDIDATES): the candidate whose development
# verdict is "gain", else "revise", with the highest mean paired gain over the non-covered worlds (ties: the name);
# when none is "gain" or "revise", nothing is chosen: every candidate is still run and reported on held-out, the record
# says "nothing chosen on development", and the withheld look names the candidate with the highest mean paired gain
# over the non-covered development worlds among those not "unsafe" or "harm" (or none). Whatever any verdict says,
# allowance_division ships "race" (off): turning it on is the owner's decision.
DIVISION_CANDIDATES = tuple(f"division:{rule}" for rule in DIVISION_RULES)
DIVISION_SAFETY = SAFETY + ("admitted.wrong",)
# The development choice (docs/quality/evidence/genesis_wave4_a_dev.json, commit 39ba9af): every candidate "unsafe"
# (more wrongly admitted content than shipped+idle in at least one world), so NOTHING is chosen and the rule names no
# division configuration for the withheld look. The look below holds only the references, should the main session
# want the existing split measured on unseen worlds; it cannot promote anything.
LOOKS["wave4-a"] = ("reference:no-loop", "shipped", "shipped+idle", "shipped+idle-first")
LOOK_PAIRS.setdefault("shipped+idle-first", DIVISION_REFERENCE)


def division_verdicts(result) -> dict:
    """{configuration: {"verdict", "against", "checks", "per_world"}} under the rule above (a run_split result over
    division_configurations() on a split of cycles_reach_splits.py)."""
    confs = result["configurations"]
    ref = confs[DIVISION_REFERENCE]["worlds"]
    out = {}
    for config in confs:
        if not config.startswith("division:"):
            continue
        per_world = {}
        for w, mine in confs[config]["worlds"].items():
            rows, base = mine["per_seed"], ref[w]["per_seed"]
            diffs = [r[PRIMARY] - b[PRIMARY] for r, b in zip(rows, base)]
            sd = ref[w]["spread"][PRIMARY]["sd"] or 0.0
            gain = sum(diffs) / len(diffs)
            per_world[w] = {
                "kind": mine.get("kind"), "gain": gain, "against_sd": sd, "beats": gain > sd, "harm": gain < -sd,
                "paired": diffs, "paired_sd": scoring.spread(diffs)["sd"],
                "better_seeds": sum(1 for d in diffs if d > 1e-12), "worse_seeds": sum(1 for d in diffs if d < -1e-12),
                "coverage_change": sum(r["mean.coverage"] - b["mean.coverage"] for r, b in zip(rows, base)) / len(rows),
                "safety": {k: sum(r[k] or 0 for r in rows) for k in DIVISION_SAFETY},
                "against_safety": {k: sum(b[k] or 0 for b in base) for k in DIVISION_SAFETY},
                "retention_lost_or_regressed": sum(r[k] or 0 for r in rows for k in LOSSES),
                "against_retention_lost_or_regressed": sum(b[k] or 0 for b in base for k in LOSSES),
                "changed_launch_cycles": [r.get("division_changed_launch_cycles") for r in rows],
                "reach_tasks": [r.get("activity.division_reach_tasks") for r in rows],
                "held_places": [r.get("activity.division_held_places") for r in rows]}
        open_worlds = [w for w, p in per_world.items() if p["kind"] != "covered"]
        a = bool(open_worlds) and all(per_world[w]["beats"] for w in open_worlds)
        b = not any(p["harm"] for p in per_world.values())
        c = all(p["retention_lost_or_regressed"] <= p["against_retention_lost_or_regressed"]
                for p in per_world.values())
        d = all(p["safety"][k] <= p["against_safety"][k] for p in per_world.values() for k in DIVISION_SAFETY)
        changed = [x for p in per_world.values() for x in p["changed_launch_cycles"] if x is not None]
        if not d:
            verdict = "unsafe"
        elif not (b and c):
            verdict = "harm"
        elif a:
            verdict = "gain"
        elif (open_worlds and all(per_world[w]["gain"] > 0 and per_world[w]["better_seeds"] >=
                                  max(1, len(per_world[w]["paired"]) - 1) for w in open_worlds)) or \
                any(per_world[w]["beats"] for w in open_worlds):
            verdict = "revise"
        elif changed and not any(changed):
            verdict = "not exercised"
        else:
            verdict = "no evidence"
        out[config] = {"verdict": verdict, "against": DIVISION_REFERENCE,
                       "mean_gain_open_worlds": (sum(per_world[w]["gain"] for w in open_worlds) / len(open_worlds)
                                                 if open_worlds else None),
                       "checks": {"a_gain_beyond_sd": a, "b_no_harm": b, "c_retention": c, "d_safety": d},
                       "per_world": per_world}
    return out


DIVISION_FIELDS = ("mean.correct", "mean.coverage", "self_admitted", "wire_admitted", "one_origin_rises",
                   "admitted.rows", "admitted.wrong", "admitted.own_only", "admitted.wire_only", "retention.lost",
                   "retention.regressed", "pages", "wall_seconds", "activity.idle_tasks",
                   "activity.division_held_cycles", "activity.division_held_places", "activity.division_mean_share",
                   "activity.division_reach_tasks", "activity.division_refresh_idle_tasks",
                   "division_changed_launch_cycles", "division_changed_held_cycles",
                   "diagnosis.total.refresh.looks", "diagnosis.total.refresh.made", "diagnosis.total.refresh.lost",
                   "diagnosis.total.reach.looks", "diagnosis.total.reach.made", "diagnosis.total.reach.lost",
                   "diagnosis.cycles_allowance_spent")
DIAGNOSED = ("shipped", DIVISION_REFERENCE, "shipped+idle-first")   # their per-cycle diagnosis is kept in the summary


def division_summary(result) -> dict:
    """A summary of a run_split result over division_configurations() (evidence files hold summaries, Wave 4): per
    configuration and world the DIVISION_FIELDS per seed and their spread, the read-back, the diagnosis totals (and
    per cycle for DIAGNOSED), the gap to the references; then the verdicts and the development choice."""
    refs = (result.get("references") or {}).get("worlds") or {}
    out = {}
    for name, conf in result["configurations"].items():
        worlds = {}
        for w, e in conf["worlds"].items():
            diag = dict(e.get("diagnosis") or {})
            if name not in DIAGNOSED:
                diag.pop("per_cycle", None)
            gaps = {k: refs[w][k]["mean_correct"]["mean"] - e["spread"][PRIMARY]["mean"]
                    for k in ("round_robin", "oracle")} if w in refs else None
            worlds[w] = {"kind": e.get("kind"), "seeds": e["seeds"],
                         "per_seed": [{k: r.get(k) for k in DIVISION_FIELDS} for r in e["per_seed"]],
                         "spread": {k: {s: e["spread"][k][s] for s in ("mean", "sd")} for k in DIVISION_FIELDS
                                    if k in e["spread"]},
                         "config_same_every_seed": e["config_same_every_seed"], "diagnosis": diag,
                         "gap_to": gaps}
        backs = [e["config_read_back"] for e in conf["worlds"].values()]
        out[name] = {"mechanisms": conf["mechanisms"], "config_read_back": backs[0],
                     "config_same_every_world": all(b == backs[0] for b in backs), "worlds": worlds}
    verdicts = division_verdicts(result)
    return {"split": result["split"], "seeds": result["seeds"], "configurations": out,
            "references": {w: {k: v[k]["mean_correct"] for k in REFERENCES} | {"budget": v["budget"]}
                           for w, v in refs.items()},
            "verdicts": verdicts, "choice": division_choice(verdicts)}


def division_choice(verdicts) -> dict:
    """The development choice under the rule above: {"chosen": name or None, "for_the_look": name or None, "why"}."""
    ranked = sorted(((v["mean_gain_open_worlds"] if v["mean_gain_open_worlds"] is not None else float("-inf"), name)
                     for name, v in verdicts.items() if name in DIVISION_CANDIDATES), key=lambda t: (-t[0], t[1]))
    for wanted in ("gain", "revise"):
        hits = [name for _, name in ranked if verdicts[name]["verdict"] == wanted]
        if hits:
            return {"chosen": hits[0], "for_the_look": hits[0],
                    "why": f"the {wanted!r} candidate with the highest mean paired gain over the non-covered worlds"}
    fallback = [name for _, name in ranked if verdicts[name]["verdict"] not in ("unsafe", "harm")]
    return {"chosen": None, "for_the_look": fallback[0] if fallback else None,
            "why": "nothing chosen on development (no candidate is 'gain' or 'revise'); the look names the candidate "
                   "with the highest mean paired gain over the non-covered worlds that is not 'unsafe' or 'harm'"}
