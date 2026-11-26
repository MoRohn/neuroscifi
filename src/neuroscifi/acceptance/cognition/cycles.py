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
Not run: the worker's idle investigation (autonomy.idle), which in the product shares the daily allowance with the
timer; "the loop" here is the timer's pass only (lane Y, M4).
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
    base = {} if SHIPPED in mechanisms else dict(WAVE1_OFF_WEB)
    cfg = base | {"enabled": True, "autonomous": NO_LOOP not in mechanisms, "daily_autonomous": _allowance(budget)}
    for m in set(mechanisms) - {NO_LOOP, SHIPPED}:
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
    origins = claims.stamp_origins(found, docs, attribution=state.get_config(db).attribution_independence)
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
        self.tasks = {"owner": 0, "refresh": 0, "recheck": 0, "curiosity": 0, "other": 0}
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
                else "recheck" if origin.startswith("refresh:") else "curiosity" if origin == "curiosity"
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
        if name and kind != "owner":      # Wave 3 lane R: what each autonomous look met and did (the diagnosis)
            self.looks.append({"cycle": self.cycle, "kind": kind, "subject": name, "before": prior,
                               "truth": self.truth(name), "read": _look_majority(self.world, name, self.cycle),
                               "look": look["kind"], "after": self._newest_version(name),
                               "outcome": {k: len(out.get(k) or []) for k in ("provisional", "strengthened",
                                                                             "unchanged", "superseded", "contested",
                                                                             "held")}})
        return out

    def _order_picks(self) -> dict:
        """Re-check picks recorded with a recheck_order (ADR-060): all, those taken by the rotation floor (overdue),
        those taken because a newer version was seen or the item was marked."""
        rows = self.db.execute("SELECT json_extract(detail,'$.autonomy.recheck.order.overdue'), json_extract(detail,"
                               "'$.autonomy.recheck.order.newer_seen') FROM web_events WHERE kind='decision' AND "
                               "json_extract(detail,'$.autonomy.recheck.order') IS NOT NULL").fetchall()
        return {"recheck_order_picks": len(rows), "recheck_overdue_picks": sum(1 for o, _ in rows if o),
                "recheck_seen_picks": sum(1 for _, n in rows if n)}

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
        report = scheduler.tick(self.db, self.now, None, queue=True)
        self.run_queued()
        self.score(report)

    def start(self):
        from ...webcortex import autonomy, state, strategies
        state.set_config(self.db, _settings(self.mechanisms, self.spec["budget"]), T0 - 30 * DAY)
        if NO_LOOP not in self.mechanisms:          # the owner's recorded enable (it also sets `autonomous` on)
            autonomy.enable(self.db, T0 - 30 * DAY)
        cfg = state.get_config(self.db)
        self.config = {k: getattr(cfg, k) for k in READ_BACK}
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
                         **self._order_picks()},
            "launches": list(self.launches), "history": {n: list(h) for n, h in self.history.items()},
            "config": dict(self.config), "allowance": list(self.allowance), "looks": list(self.looks),
        }


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
            with _environment(ref):
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
           "order_changed_launch_cycles", "order_changed_held_cycles")


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
SPLITS = ("development", "held_out", RATE + "development", RATE + "held_out")


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
        for i, r in enumerate(rows):
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
                "seeds": list(seeds), "per_seed": [{k: _get(r, k) for k in SCALARS} | {"tasks": r["tasks"]}
                                                   for r in rows],
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
    if not note:
        raise cognition.Refused("final: a note is required (it is the ledger line's reason)")
    known = configurations() | EXTRA
    configs = configs or {k: known[k] for k in FINAL_CONFIGS}
    env = cognition.environment()
    if env["git_dirty"] or not env["git_commit"]:
        raise cognition.Refused("final: the tree must be clean and committed, so the ledger line names the code it "
                                "measured")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"at": at, "commit": env["git_commit"], "dirty": env["git_dirty"], "mechanism": mechanism, "ablate": [],
             "families": [FINAL_FAMILY], "configurations": sorted(configs), "note": note}
    lines = cognition._record_final(cognition.LEDGER, entry)
    spec, generate = _withheld()
    out = {"generated_at": at, "environment": env, "ledger_line": len(lines), "ledger_entry": entry}
    out |= _run_split(WITHHELD, spec, generate, configs=configs)
    out["references"] = _run_references(WITHHELD, spec, generate) | {
        "code": "src/neuroscifi/acceptance/cognition/cycles.py: references, _run_references (from the withheld "
                "generator alone; no product code)"}
    out["world_digests"] = {w["name"]: {str(seed): world_digest(generate(w, seed)) for seed in spec["seeds"]}
                            for w in spec["worlds"]}
    return out


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
