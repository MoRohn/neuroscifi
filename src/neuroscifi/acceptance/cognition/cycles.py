"""The learning-cycles experiment (Wave 2 lane F; directive §7 "recursive knowledge evolution", deliverable F;
scripts/bench_learning_cycles.py). Worlds are frozen in cycles_splits.py, committed before this harness.

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


def configurations() -> dict:
    """{name: set of lane K mechanisms on}: the no-loop reference, baseline, each alone, all, all without one."""
    out = {"reference:no-loop": frozenset({NO_LOOP}), "baseline": frozenset()}
    for m in MECHANISMS:
        out[f"only:{m}"] = frozenset({m})
    out["all"] = frozenset(MECHANISMS)
    for m in MECHANISMS:
        out[f"all-without:{m}"] = frozenset(MECHANISMS) - {m}
    return out


def _settings(mechanisms, budget) -> dict:
    cfg = dict(WAVE1_OFF_WEB) | {"enabled": True, "autonomous": NO_LOOP not in mechanisms,
                                 "daily_autonomous": {"tasks": int(budget), "requests": 400, "bytes": 80_000_000}}
    for m in set(mechanisms) - {NO_LOOP}:
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
    origins = claims.stamp_origins(found, docs)
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
    def __init__(self, spec, seed, mechanisms, db):
        self.spec, self.seed, self.mechanisms, self.db = spec, seed, frozenset(mechanisms), db
        self.world = worlds.generate(spec, seed)
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
        look, docs = self._documents(name, now - 600.0) if name else ({"kind": "none", "pages": []}, [])
        self.pages += len(docs)
        before = {r[0]: r[2] for rows in self.held().values() for r in rows}
        single = None
        if look["kind"] in ("one_origin", "wire_only") and docs:
            self.one_origin_looks += 1
            single = one_origin_confidence(db, task, docs[0], now)
        stated = [s["value"] for s in self.self_now if s["subject"] == name]
        out = investigate(db, task, docs, now,
                          answer=f"{name} {stated[0]} is the latest {name} release." if stated else None)
        if single is not None:
            for rows in self.held().values():
                for kid, _mm, conf, _v in rows:
                    if kid in before and conf > max(before[kid], single) + 1e-9:
                        self.rises += 1
        return out

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
        from ...webcortex import scheduler
        self.cycle += 1
        self.now = T0 + self.cycle * self.spec["cycle_hours"] * 3600.0
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
                   "web_premises", "web_reflections", "imagined", "pending")}
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
        }


@contextmanager
def _environment(run_ref):
    """The host's resource gate held open (the experiment measures knowledge, not this host's memory pressure);
    the reflection's model summary answered by the stub (no model is called)."""
    from ...resources import policy as rpolicy
    from ...inference import gateway
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(rpolicy, "background_gate", lambda *a, **k: {"allowed": True,
                                                                                           "reason": None}))
        stack.enter_context(mock.patch.object(gateway.Gateway, "from_db",
                                              classmethod(lambda cls, db: _StubGateway(run_ref[0]))))
        yield


def run_world(spec, seed, mechanisms=frozenset(), *, cycles=None) -> dict:
    """One run of one world and seed with the given lane K mechanisms on; `cycles` shortens the run (tests)."""
    started = time.perf_counter()
    spec = dict(spec) | ({"cycles": int(cycles)} if cycles is not None else {})
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-f-") as directory:
        db = store.create(Path(directory) / "f.sqlite", T0 - 31 * DAY, note="lane F: learning cycles")
        with closing(db):
            ref = [None]
            with _environment(ref):
                run = Run(spec, seed, mechanisms, db)
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
           "one_origin_looks")


def _get(d, dotted):
    for part in dotted.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


def run_split(split, *, configs=None, cycles=None, seeds=None) -> dict:
    spec = worlds.CYCLES[split]
    configs = configs or configurations()
    seeds = tuple(seeds or spec["seeds"])
    out = {}
    for name, mechanisms in configs.items():
        per_world = {}
        for world in spec["worlds"]:
            rows = [run_world(world, seed, mechanisms, cycles=cycles) for seed in seeds]
            per_world[world["name"]] = {
                "seeds": list(seeds), "per_seed": [{k: _get(r, k) for k in SCALARS} | {"tasks": r["tasks"]}
                                                   for r in rows],
                "spread": {k: scoring.spread([_get(r, k) for r in rows]) for k in SCALARS},
                "series_mean": [{k: scoring.spread([r["series"][i][k] for r in rows])["mean"]
                                 for k in ("accuracy", "coverage", "correct", "held_items", "open_marks")}
                                | {"cycle": rows[0]["series"][i]["cycle"]} for i in range(len(rows[0]["series"]))],
                "tasks": {k: scoring.spread([r["tasks"][k] for r in rows])["mean"] for k in rows[0]["tasks"]}}
        out[name] = {"mechanisms": sorted(mechanisms), "worlds": per_world}
    return {"split": split, "seeds": list(seeds), "configurations": out,
            "strategy_selection_note": "in force without passing the real gate (a 'retain' row written into each "
                                       "throwaway individual's ledger)"}


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
