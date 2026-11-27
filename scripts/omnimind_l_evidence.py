"""OMNIMIND lane L's evidence (ADR-078): docs/quality/evidence/omnimind_l.json.

1. The learn family on the DEVELOPMENT split only (SIMULATED people: adaptation to simulated preferences, not benefit to
   a person): every reference, the product's planner alone, the shipped rule (lane L's per-key majority through the
   frozen planner), its evidence minimum at 1 and 3, and the learners tried and NOT shipped (a learner of the planner's
   weights through the `adjustments` seam; a per-key majority backed off to the person's whole tally), with the
   planner-constrained oracle as the bound any learner through the planner meets. Per person: late and overall kept
   share, and the cost of being wrong early (choices the person rejected in the first half of the cycles).
2. Lane C's property tests run under random learned policies (tests/artifacts/test_learn_properties.py): the counts.
3. The cost of the outcome table at 100,000 rows.
Never the held-out or withheld split; no held-out person is read.

    PYTHONPATH=$PWD/src python scripts/omnimind_l_evidence.py --out docs/quality/evidence/omnimind_l.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import tempfile
import time

from neuroscifi.acceptance.omni import learn as bench, learn_people, subject as S
from neuroscifi.artifacts import forms as F, learn

SPLIT = "development"
CRITERIA = [c for c in F.CRITERIA if c != "feasibility"]


# ---- learners tried and not shipped (measured to decide; they live here, not in the product) -------------------------

class WeightsLearner(S.ProductLearner):
    """NOT SHIPPED. Learns the planner's criterion weights per key through the `adjustments` seam (clamped to the
    planner's MAX_ADJUST): a step toward the criteria the wanted form scores higher on than the shown one (a
    replacement), or toward / away from the shown form's scores against the other admitted forms (kept / dismissed)."""

    STEP = 0.05

    def __init__(self):
        super().__init__("apply")
        self.w = defaultdict(lambda: dict.fromkeys(CRITERIA, 0.0))
        self.plans = {}

    def choose(self, case):
        context, shape = S._learn_context(self.P, case), S._learn_shape(case)
        first = self.P.plan(self.P.Request(), (shape,), context)
        key = learn.key_of(first, (shape,), context)
        result = self.P.plan(self.P.Request(), (shape,), context, adjustments={key: dict(self.w[key])})
        self.plans[case["index"]] = (key, result)
        return "+".join(S.FORMS_TO_BENCH[f] for f in result.chosen)

    def feedback(self, case, form, outcome):
        key, result = self.plans.pop(case["index"])
        shown = result.candidate(result.chosen) if result.chosen else None
        if shown is None:
            return
        w = self.w[key]
        if outcome["outcome"] == "replaced" and S.FORMS_FROM_BENCH.get(outcome["by"]):
            target = result.candidate(S.FORMS_FROM_BENCH[outcome["by"]])
            if target is not None:
                for c in CRITERIA:
                    w[c] = max(-F.MAX_ADJUST, min(F.MAX_ADJUST, w[c] + self.STEP * (target.scores[c] -
                                                                                  shown.scores[c])))
                return
        others = [c for c in result.candidates if c.admitted and len(c.forms) == 1 and c is not shown]
        if not others:
            return
        sign = 1 if outcome["outcome"] == "kept" else -1
        for c in CRITERIA:
            mean = statistics.fmean(o.scores[c] for o in others)
            w[c] = max(-F.MAX_ADJUST, min(F.MAX_ADJUST, w[c] + sign * self.STEP * (shown.scores[c] - mean)))


class BackoffLearner(S.ProductLearner):
    """NOT SHIPPED. The shipped per-key majority, and while a key has not enough evidence, the same rule over the
    person's reactions on every key of the same device class (screen or speaker): a learner that generalises across
    keys ("a person who wants tables on one kind wants them on others")."""

    def __init__(self):
        super().__init__("apply")
        self.person = {"screen": learn.Tally(), "voice": learn.Tally()}

    def _lookup(self, case):
        def lookup(key):
            own = self.tally.policy(key)
            if own.enough:
                return learn.Learned.of(own, "apply")
            wide = self.person["voice" if case["device"] == "voice" else "screen"].policy(("records", "look_up",
                                                                                         "console"))
            return learn.Learned.of(wide, "apply") if wide.enough else learn.Learned.of(own, "apply")
        return lookup

    def choose(self, case):
        result, learned = learn.plan_with(self._lookup(case), self.P.Request(), (S._learn_shape(case),),
                                          S._learn_context(self.P, case))
        key = learn.key_of(result, (S._learn_shape(case),), S._learn_context(self.P, case))
        self._shown[case["index"]] = (key, result.chosen)
        return "+".join(S.FORMS_TO_BENCH[f] for f in result.chosen)

    def feedback(self, case, form, outcome):
        key, chosen = self._shown[case["index"]]
        super().feedback(case, form, outcome)
        if chosen:
            by = S.FORMS_FROM_BENCH.get(outcome.get("by")) if outcome["outcome"] == "replaced" else None
            reaction = outcome["outcome"] if outcome["outcome"] != "replaced" or by else "dismissed"
            self.person["voice" if case["device"] == "voice" else "screen"].add(
                "records", "look_up", "console", "+".join(chosen), reaction, by)


def with_min_evidence(n):
    def make():
        learner = S.make_learner()
        learner.tally = learn.Tally(min_evidence=n)
        return learner
    return make


def _planner_constrained_oracle(person):
    """A BOUND, not a chooser: the person's hidden wanted form handed to the planner as a stated preference. What no
    learner through the frozen planner can exceed (it still keeps the planner's hard constraints and proportion rules)."""
    class Bound(S.ProductLearner):
        def choose(self, case):
            want = bench.preference(person, case["kind"], case["device"])[0]
            name = S.FORMS_FROM_BENCH.get(want)
            context = S._learn_context(self.P, case)
            if name:
                context = F.Context(device=context.device, generators=context.generators, preferences={name: 1.0})
            result = self.P.plan(self.P.Request(), (S._learn_shape(case),), context)
            return "+".join(S.FORMS_TO_BENCH[f] for f in result.chosen)

        def feedback(self, case, form, outcome):
            pass
    return Bound


# ---- running ---------------------------------------------------------------------------------------------------------

def _run(make_for):
    """Per (person, seed) the kept flags, for a factory `make_for(person) -> factory` (None: the oracle)."""
    out = {}
    for person in learn_people.DEVELOPMENT:
        for seed in learn_people.SEEDS[SPLIT]:
            out[(person["id"], seed)] = bench.run_person(make_for(person), person, seed)
    return out


def _summary(flags):
    cycles, per = learn_people.CYCLES, learn_people.PER_CYCLE
    half = cycles // 2 * per
    seeds = learn_people.SEEDS[SPLIT]
    late = [statistics.fmean([k for (p, s), v in flags.items() if s == seed for k in v[half:]]) for seed in seeds]
    whole = [statistics.fmean([k for (p, s), v in flags.items() if s == seed for k in v]) for seed in seeds]
    people = sorted({p for p, _ in flags})
    by_person = {}
    for pid in people:
        runs = [v for (p, _), v in flags.items() if p == pid]
        by_person[pid] = {"late_kept": round(statistics.fmean(k for v in runs for k in v[half:]), 4),
                          "all_kept": round(statistics.fmean(k for v in runs for k in v), 4),
                          "rejected_early": sum(not k for v in runs for k in v[:half]),
                          "early_choices": len(runs) * half}
    return {"late_kept": {"mean": round(statistics.fmean(late), 4), "sd_over_seeds": round(statistics.stdev(late), 4),
                          "per_seed": [round(x, 4) for x in late]},
            "all_kept": {"mean": round(statistics.fmean(whole), 4), "sd_over_seeds": round(statistics.stdev(whole), 4),
                         "per_seed": [round(x, 4) for x in whole]},
            "rejected_early": sum(p["rejected_early"] for p in by_person.values()),
            "early_choices": sum(p["early_choices"] for p in by_person.values()),
            "by_person": by_person}


def learn_table() -> dict:
    subjects = {
        "today": ("reference: always prose", lambda p: bench.Today),
        "never_learns": ("reference: the kind's usual form, never adjusted", lambda p: bench.NeverLearns),
        "person_majority": ("reference learner: one tally per person", lambda p: bench.PersonMajority),
        "key_majority": ("reference learner: per (kind, device) tally, choosing directly", lambda p: bench.KeyMajority),
        "oracle": ("reference bound: knows the hidden preference", lambda p: None),
        "product_planner_only": ("the frozen planner through the driver, nothing learned applied (form_learning "
                                 "record)", lambda p: S.make_planner_only),
        "product_shipped": ("SHIPPED: lane L's per-key majority through the frozen planner (form_learning apply; "
                            "subject:make_learner)", lambda p: S.make_learner),
        "product_min_evidence_1": ("the shipped rule with the evidence minimum at 1 (not shipped)",
                                   lambda p: with_min_evidence(1)),
        "product_min_evidence_3": ("the shipped rule with the evidence minimum at 3 (not shipped)",
                                   lambda p: with_min_evidence(3)),
        "not_shipped_weights": ("NOT SHIPPED: a learner of the planner's weights through the adjustments seam",
                                lambda p: WeightsLearner),
        "not_shipped_backoff": ("NOT SHIPPED: the per-key majority backed off to the person's whole tally",
                                lambda p: BackoffLearner),
        "planner_constrained_oracle": ("BOUND: the hidden preference handed to the planner as a stated preference",
                                       _planner_constrained_oracle),
    }
    rows, started = {}, time.time()
    for name, (what, make_for) in subjects.items():
        rows[name] = {"what": what, **_summary(_run(make_for))}
    for name, row in rows.items():           # the official scorer agrees with this table for every chooser it can run
        if name in bench.REFERENCES and name != "oracle":
            official = bench.score(name, SPLIT)
            assert official["late_kept"]["per_seed"] == row["late_kept"]["per_seed"], name
    official = bench.score(S.make_learner, SPLIT)
    assert official["late_kept"]["per_seed"] == rows["product_shipped"]["late_kept"]["per_seed"]
    visible = {}
    for measure in ("late_kept", "all_kept"):
        sd_never = rows["never_learns"][measure]["sd_over_seeds"]
        sd_major = rows["key_majority"][measure]["sd_over_seeds"]
        visible[measure] = {"over_never_learns": round(2 * math.sqrt(2) * sd_never, 4),
                            "over_key_majority": round(2 * math.sqrt(2) * max(sd_never, sd_major), 4)}
    verdicts = {}
    for name in ("product_shipped", "not_shipped_weights", "not_shipped_backoff", "product_min_evidence_1"):
        verdicts[name] = {}
        for measure in ("late_kept", "all_kept"):
            mine = rows[name][measure]["per_seed"]
            for ref in ("never_learns", "key_majority", "product_shipped"):
                if ref == name:
                    continue
                gain = statistics.fmean(a - b for a, b in zip(mine, rows[ref][measure]["per_seed"]))
                sd = max(rows[name][measure]["sd_over_seeds"], rows[ref][measure]["sd_over_seeds"])
                margin = round(2 * math.sqrt(2) * sd, 4)
                verdicts[name][f"{measure}_over_{ref}"] = {"gain": round(gain, 4), "visible_above": margin,
                                                           "clears": gain > margin}
    return {"label": bench.LABEL, "split": SPLIT, "people": [p["id"] for p in learn_people.DEVELOPMENT],
            "seeds": list(learn_people.SEEDS[SPLIT]), "denominator_per_cycle": 600,
            "early": "cycles 1-4 (the first half): rejected = dismissed or replaced", "subjects": rows,
            "visible_gain_development": visible, "verdicts": verdicts,
            "seconds": round(time.time() - started, 1)}


# ---- property counts ----------------------------------------------------------------------------------------------

def property_counts() -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "artifacts"))
    import test_forms as C
    import test_learn_properties as LP
    real, out = F.plan, {}
    cases = [(t, 0) for t, _ in LP.CASES] + [(C.test_more_data_never_moves_the_choice_toward_words, 1)]
    try:
        for test, monotone in cases:
            wrapper = LP.Learning(79 if monotone else 78, words=not monotone)
            F.plan = wrapper
            test()
            F.plan = real
            out[test.__name__ + (" (learned preferences name no words form)" if monotone else "")] = {
                "plans": wrapper.plans, "learned_applied": wrapper.applied, "choice_changed": wrapper.changed,
                "violations": 0}
    finally:
        F.plan = real
    return out


# ---- cost of the outcome table -------------------------------------------------------------------------------------

def table_cost(rows=100_000, artifacts=500) -> dict:
    from neuroscifi.artifacts import make, store as A
    from neuroscifi.instance import store
    t0 = 1_800_000_000.0
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "cost.sqlite"
        db = store.create(path, t0)
        store.set_config(db, {"form_learning": "apply"}, t0)
        ids = []
        for i in range(artifacts):
            spec = {"spec": "chart.bar", "version": 1, "title": f"Cost {i}", "description": "", "options": {},
                    "data": {"categories": ["a", "b", "c", "d", "e"], "x": {"label": "x"}, "y": {"label": "y"},
                             "series": [{"name": "s", "values": [1, 2, 3, 4, i]}]},
                    "sources": ["request"], "made_by": {"generator": "", "version": 0}}
            made = make.make(spec)
            ids.append(A.add_made(db, made, made.spec, [], now=t0).id)
        size_before = path.stat().st_size + Path(f"{path}-wal").stat().st_size if Path(f"{path}-wal").exists() \
            else path.stat().st_size
        rng = random.Random(78)
        kinds, devices = ("series", "categories", "distribution", "relation", "records"), F.DEVICES
        names = ("prose", "table", "line_chart", "bar_chart", "histogram", "scatter_chart")
        batch = []
        for n in range(rows):
            kind, outcome = rng.choice(kinds), rng.choice(("check_passed", "kept", "dismissed", "replaced"))
            purpose = "change_over_time" if kind == "series" else "compare"
            batch.append((ids[n % artifacts], kind, purpose, rng.choice(devices), rng.choice(names), outcome,
                          rng.choice(names) if outcome == "replaced" else None,
                          "check" if outcome.startswith("check") else "owner", t0 + n))
        start = time.perf_counter()
        with store.write(db):
            db.executemany("INSERT INTO form_outcomes(artifact_id,kind,purpose,device,form,outcome,replaced_by,source,"
                           "at) VALUES(?,?,?,?,?,?,?,?,?)", batch)
        insert_s = time.perf_counter() - start
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size_after = path.stat().st_size

        def timed(fn, n=5):
            values = []
            for _ in range(n):
                start = time.perf_counter()
                fn()
                values.append(time.perf_counter() - start)
            return round(statistics.median(values) * 1000, 2)
        record_ms = timed(lambda: learn.record_outcome(db, ids[0], "kept"), 20)
        key_ms = timed(lambda: learn.adjustments_for(db, "series", "change_over_time", "console"), 20)
        shape = F.ContentShape(("series",), 300, 1, True, 0, False, 0.0, 0)
        plan_ms = timed(lambda: learn.plan(db, F.Request(), (shape,), F.Context()), 10)
        plain_ms = timed(lambda: F.plan(F.Request(), (shape,), F.Context()), 10)
        status = learn.plan(db, F.Request(), (shape,), F.Context())[1].status
        report_ms = timed(lambda: learn.report(db), 3)
        gaps_ms = timed(lambda: learn.gap_report(db), 1)
        start = time.perf_counter()
        A.forget_artifact(db, ids[1], t0 + rows + 1)
        forget_ms = round((time.perf_counter() - start) * 1000, 1)
        held = db.execute("SELECT count(*) FROM form_outcomes").fetchone()[0]
        start = time.perf_counter()
        learn.reset(db, t0 + rows + 2)
        reset_ms = round((time.perf_counter() - start) * 1000, 1)
        db.close()
    return {"rows": rows, "made_artifacts": artifacts, "insert_all_s": round(insert_s, 2),
            "file_bytes_per_row": round((size_after - size_before) / rows, 1),
            "median_ms": {"record_outcome": record_ms, "adjustments_for_one_key": key_ms,
                          "learn.plan (two plans when something applies)": plan_ms, "forms.plan alone": plain_ms,
                          "report_all_keys": report_ms, "gap_report": gaps_ms},
            "forget_one_artifact_ms": forget_ms, "rows_after_forget": held, "reset_ms": reset_ms,
            "status_of_the_timed_plan": status,
            "note": "one run on a loaded shared host; every record_outcome trims the table to its bound (100,000 "
                    "rows), so the forget took the rest of its artifact's rows by trigger"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = {"lane": "omnimind-l", "adr": "ADR-078", "simulated": True,
           "statement": "the learn family measures adaptation to SIMULATED, stable preferences from simulated "
                        "feedback, not benefit to a person; development split only",
           "environment": {"python": platform.python_version(), "machine": platform.machine()},
           "learn_development": learn_table(), "properties_under_random_learned_policies": property_counts(),
           "outcome_table_cost": table_cost()}
    text = json.dumps(out, indent=1, sort_keys=True) + "\n"
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
