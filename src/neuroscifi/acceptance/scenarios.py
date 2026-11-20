"""Executable, simulated versions of "What the first release must demonstrate" (Architecture.md p.19).

Each scenario creates its own instances in a temporary directory, drives the real instance modules through
their public functions, and returns
    {"name", "simulated": True, "pass", "checks", "metrics", "thresholds", "notes"}.
`pass` is the conjunction of `checks`; every check is a named, documented assertion. Results are
deterministic for a given seed, except wall-clock latencies. All evidence is written with
provenance='simulated' and all datasets with provenance 'simulated'. Nothing here is a physical validation.

The design constants below (stream lengths, noise, change positions, thresholds) were fixed before the
scenarios were first run and are reported in each result.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

import numpy as np

from ..lab import graph_bench
from ..instance import beliefs, checkpoints, datasets, evidence, executive, graphs, learner, recall, rules, store
from ..instance.errors import Gone, WritesPaused
from .bench import HOST_LABEL, percentiles, teaching_stream

T0 = 1_800_000_000.0
DAY = 86_400.0
DEFAULT_SEED = 20260519
DEFAULT_LIFETIMES = 200
DEFAULT_EVENTS = 200
CONFIDENT = 0.95
SIMULATED = "simulated"
FACTORS = ("mechanism", "camera", "partner")
CAMERA_ROOT, PARTNER_ROOT = "camera-1", "partner-1"

# Grounding design, fixed up front.
GROUNDING_DESIGN = {
    "steps_per_lifetime": 40,
    "events_per_step": "camera + partner word every step; one reference reading every 4th step",
    "reading_noise": 0.10,
    "reference_readings_per_root": 2,
    "reference_root_flip_probability": 0.15,
    "change_step_range": [20, 30],
    "changed_lifetimes": "every odd-numbered lifetime (half)",
    "changed_factor_cycle": ["camera", "camera", "partner", "mechanism"],
    "confidence": CONFIDENT,
}
GROUNDING_THRESHOLDS = {
    "confident_wrong_relative_reduction_min": 0.50,   # brief p.19
    "unchanged_coverage_min": 0.80,                   # brief p.19 (coverage on normal inputs)
    "unchanged_task_loss_points_max": 2.0,            # brief p.19 (nominal task loss)
    "identified_or_asked_rate_min": 0.80,             # predeclared here for ACC-02 / INS-11
}
DEVELOPMENT_RULE = dict(store.GATE_V1, version="acceptance-degree-6",
                        pack_candidates={"degrees": [6], "bits": [8]})
DENSE_BASELINE_FACTOR = 1.10   # predeclared: promoted heldout MSE within 10% of a dense least-squares graph


def _env():
    """Environment for child processes: make sure they import this source tree."""
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _subprocess_json(code, *args):
    """Run `python -c code args...` in a NEW process and parse its JSON stdout."""
    proc = subprocess.run([sys.executable, "-c", code, *map(str, args)], capture_output=True, text=True,
                          env=_env(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"child process failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}")
    return json.loads(proc.stdout)


def _plain(value):
    """JSON round trip so in-process values compare equal to values parsed from a child process."""
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _result(name, checks, metrics, thresholds, notes):
    checks = {key: bool(value) for key, value in checks.items()}
    return _plain({"name": name, "simulated": True, "pass": all(checks.values()), "checks": checks,
                   "metrics": metrics, "thresholds": thresholds, "notes": notes})


def _mean(values):
    values = list(values)
    return float(np.mean(values)) if values else None


def _obs(event_id, epoch, channel, root, action, bit):
    return {"id": event_id, "epoch": epoch, "channel": channel, "root": root, "action": int(action),
            "bit": int(bit), "provenance": SIMULATED, "actor": "simulator"}


# ---- scoring helpers for binary answers --------------------------------------------------------------

def _answers(result):
    """P(bit = 1) for each hidden factor of the single-camera, single-partner setup (0.5 when unseen)."""
    return {"mechanism": result["mechanism"]["p_inverted"],
            "camera": result["sensors"].get(CAMERA_ROOT, {}).get("p_inverted", .5),
            "partner": result["partners"].get(PARTNER_ROOT, {}).get("p_inverted", .5)}


def _correct(p, truth):
    """1 for a right MAP answer, 0 for a wrong one, 0.5 for an exact tie (a coin flip)."""
    if abs(p - .5) < 1e-9:
        return .5
    return float(int(p > .5) == truth)


def _confident(p):
    return max(p, 1 - p) > CONFIDENT


def _confident_wrong(p, truth):
    return (p > CONFIDENT and truth == 0) or (p < 1 - CONFIDENT and truth == 1)


def _brier(p, truth):
    return (p - truth) ** 2


# ---- 1. birth ----------------------------------------------------------------------------------------

def birth(seed=DEFAULT_SEED):
    """ACC-01 / INS-03: publish the birth record and measure baseline abilities with zero personal episodes."""
    world, _ = graph_bench.world(np.random.default_rng(seed))
    rng = np.random.default_rng([seed, 1])
    train = graph_bench.stream(world, graph_bench.STEPS, rng)
    validation = graph_bench.stream(world, datasets.VALIDATION_STEPS, rng)
    probe_x, probe_y = graph_bench.stream(world, datasets.HELDOUT_STEPS, np.random.default_rng([seed, 2]))
    fitted = learner.fit(*train, seed=seed)
    packed = learner.pack(fitted["dense"], validation, store.GATE_V1)
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        db = store.create(Path(directory) / "born.sqlite", T0, founding_graph=packed["blob"],
                          note="acceptance birth (simulated founding graph)")
        try:
            inventory = store.inventory(db)
            record = inventory["birth"]
            evidence.start_epoch(db, "first-day", T0 + 1)
            state = beliefs.compute(db, "first-day")
            decision = executive.decide(db, "first-day", T0 + 2)
            active = [r for r in graphs.listing(db) if r["status"] == "active"]
            founding = graphs.show(db, record["initial_graph"]["revision_id"]) if record["initial_graph"] else None
        finally:
            db.close()
        bare = store.create(Path(directory) / "bare.sqlite", T0, note="acceptance birth without a founding graph")
        try:
            bare_inventory = store.inventory(bare)
        finally:
            bare.close()
    founding_mse = learner.mse(np.array(founding["matrix"]), probe_x, probe_y) if founding else None
    zero_mse = float(np.mean(probe_y ** 2))
    encoders = record["encoders"]
    checks = {
        "priors_published": record["priors"] == store.PRIORS and bool(record["priors"]),
        "action_semantics_published": set(record["action_semantics"]) == {"0", "1"},
        "encoder_provenance_published": bool(encoders) and all(
            {"name", "provenance", "externally_trained"} <= set(e) for e in encoders),
        "initial_graph_registered": record["initial_graph"] is not None and len(active) == 1
        and active[0]["context"] == "founding" and active[0]["origin"] == "founding",
        "zero_personal_episodes": inventory["memory"]["evidence"] == 0
        and inventory["memory"]["belief_revisions"] == 0,
        "baseline_beliefs_at_half": state["mechanism"]["p_inverted"] == .5 and not state["mechanism"]["identifiable"]
        and not state["sensors"] and not state["partners"] and state["evidence"]["total"] == 0,
        "baseline_decision_observe": decision["decision"] == "observe",
        "founding_graph_predicts_better_than_silence": founding_mse is not None and founding_mse < zero_mse,
        "bare_birth_has_no_initial_graph": bare_inventory["birth"]["initial_graph"] is None
        and bare_inventory["memory"]["graph_revisions"] == 0 and bare_inventory["memory"]["evidence"] == 0,
    }
    metrics = {
        "birth_record_keys": sorted(record),
        "priors": record["priors"], "action_semantics": record["action_semantics"], "encoders": encoders,
        "initial_graph": record["initial_graph"],
        "founding_graph": {"bits": packed["bits"], "degree": packed["degree"], "byte_size": packed["byte_size"]},
        "memory_inventory": inventory["memory"], "development": inventory["development"],
        "personal_episodes": inventory["memory"]["evidence"],
        "baseline": {"mechanism_p_inverted": state["mechanism"]["p_inverted"], "decision": decision["decision"],
                     "decision_reason": decision["reason"],
                     "founding_graph_probe_mse": founding_mse, "zero_predictor_probe_mse": zero_mse},
    }
    notes = [
        "Founding graph: a dense graph fitted with learner.fit on a simulated graph_bench world, packed with "
        "learner.pack under gate-v1; probe MSE is measured on a fresh 512-step simulated stream of that world.",
        "Baseline ability with zero personal episodes: beliefs at 0.5 (mechanism not identifiable) and the "
        "executive's decision 'observe'.",
    ]
    return _result("birth", checks, metrics, {"baseline_mechanism_p_inverted": .5, "baseline_decision": "observe"},
                   notes)


# ---- 2. grounding ------------------------------------------------------------------------------------

def _scope(factor):
    return {"mechanism": "mechanism", "camera": f"camera:{CAMERA_ROOT}", "partner": f"partner:{PARTNER_ROOT}"}[factor]


def _grounding_lifetime(seed, index):
    """One simulated lifetime: hidden bits, an optional silent single-factor change, and its event stream."""
    design = GROUNDING_DESIGN
    rng = np.random.default_rng([seed, 2, index])
    truth = {factor: int(rng.integers(2)) for factor in FACTORS}
    changed = index % 2 == 1
    cycle = design["changed_factor_cycle"]
    factor = cycle[(index // 2) % len(cycle)] if changed else None
    low, high = design["change_step_range"]
    change_step = int(rng.integers(low, high)) if changed else None
    every = 4
    per_root = design["reference_readings_per_root"]
    roots = design["steps_per_lifetime"] // (every * per_root)
    flips = (rng.random(roots) < design["reference_root_flip_probability"]).astype(int)
    events, onset = [], None
    for step in range(design["steps_per_lifetime"]):
        current = dict(truth)
        if changed and step >= change_step:
            current[factor] ^= 1
        action = int(rng.integers(2))
        specs = [("camera", CAMERA_ROOT, current["mechanism"] ^ current["camera"], action,
                  factor in ("camera", "mechanism")),
                 ("word", PARTNER_ROOT, current["mechanism"] ^ current["partner"], action,
                  factor in ("partner", "mechanism"))]
        if step % every == every - 1:
            k = step // (every * per_root)
            specs.append(("reference", f"reference-{k}", current["mechanism"] ^ int(flips[k]),
                          int(rng.integers(2)), factor == "mechanism"))
        for channel, root, relation, act, affected in specs:
            reading = relation ^ int(rng.random() < design["reading_noise"])
            if onset is None and changed and step >= change_step and affected:
                onset = len(events)
            events.append({"channel": channel, "root": root, "action": act, "bit": reading ^ act})
    final = dict(truth)
    if changed:
        final[factor] ^= 1
    return {"truth": final, "changed": changed, "factor": factor, "change_step": change_step, "onset": onset,
            "events": events, "reference_root_faults": int(flips.sum())}


def _run_lifetime(pooled_db, detect_db, life, index):
    """Feed one lifetime to both policies. Returns final answers and the detecting policy's detection log."""
    base = T0 + index * 1000.0
    pooled_epoch = f"L{index}-pooled"
    evidence.start_epoch(pooled_db, pooled_epoch, base)
    k = 0
    epoch = f"L{index}-d0"
    evidence.start_epoch(detect_db, epoch, base)
    detections, ambiguous, calibrate_asks = [], [], []
    for j, event in enumerate(life["events"]):
        now = base + j + 1
        args = (event["channel"], event["root"], event["action"], event["bit"])
        evidence.observe(pooled_db, _obs(f"L{index}-{j}", pooled_epoch, *args), now)
        evidence.observe(detect_db, _obs(f"L{index}-{j}", epoch, *args), now)
        change = beliefs.detect_change(detect_db, epoch)
        if not change["suspected"]:
            continue
        if change["ambiguous"]:
            decision = executive.decide(detect_db, epoch, now)   # read-only: what would the executive do?
            ambiguous.append({"event": j, "scope": change["scope"], "decision": decision["decision"]})
            continue
        # The executive asks the owner to calibrate; the owner confirms and starts a new epoch.
        decision = executive.decide(detect_db, epoch, now, record=True)
        calibrate_asks.append(decision["decision"] == "ask" and decision["scope"] == change["scope"])
        if decision["pending_id"] is not None:
            executive.resolve(detect_db, decision["pending_id"], "owner confirmed and recalibrated", now)
        detections.append({"event": j, "scope": change["scope"], "llr": change["llr"]})
        k += 1
        epoch = f"L{index}-d{k}"
        evidence.start_epoch(detect_db, epoch, now, note=f"recalibrated after a suspected {change['scope']} change")
    return {"pooled": _answers(beliefs.compute(pooled_db, pooled_epoch)),
            "detecting": _answers(beliefs.compute(detect_db, epoch)),
            "detections": detections, "ambiguous": ambiguous, "calibrate_asks": calibrate_asks, "epochs": k + 1}


def _policy_scores(records, factor_of):
    """Confident-wrong count, accuracy, coverage and Brier over (record, factor) answers for both policies."""
    out = {}
    for policy in ("pooled", "detecting"):
        answers = [(r[policy][f], r["truth"][f]) for r in records for f in factor_of(r)]
        out[policy] = {
            "answers": len(answers),
            "confident_wrong": int(sum(_confident_wrong(p, t) for p, t in answers)),
            "accuracy": _mean(_correct(p, t) for p, t in answers),
            "coverage": _mean(_confident(p) for p, t in answers),
            "brier": _mean(_brier(p, t) for p, t in answers),
        }
    return out


def grounding(seed=DEFAULT_SEED, lifetimes=DEFAULT_LIFETIMES):
    """ACC-02 / INS-11 with the brief's p.19 thresholds (ACCEPTANCE_CRITERIA G4)."""
    if isinstance(lifetimes, bool) or not isinstance(lifetimes, int) or lifetimes < 2:
        raise ValueError("lifetimes must be an integer >= 2")
    records = []
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        pooled_db = store.create(Path(directory) / "pooled.sqlite", T0, note="acceptance grounding: pooled policy")
        detect_db = store.create(Path(directory) / "detecting.sqlite", T0,
                                 note="acceptance grounding: detecting policy")
        try:
            for index in range(lifetimes):
                life = _grounding_lifetime(seed, index)
                run = _run_lifetime(pooled_db, detect_db, life, index)
                records.append({**run, **{key: life[key] for key in ("truth", "changed", "factor", "onset",
                                                                    "change_step", "reference_root_faults")}})
        finally:
            pooled_db.close()
            detect_db.close()
    changed = [r for r in records if r["changed"]]
    unchanged = [r for r in records if not r["changed"]]

    # Detection on changed lifetimes: the first confident detection at or after the onset of the change.
    outcomes, delays = [], []
    by_factor = {}
    for r in changed:
        after = [d for d in r["detections"] if d["event"] >= r["onset"]]
        asked = [a for a in r["ambiguous"] if a["event"] >= r["onset"]]
        target = _scope(r["factor"])
        if after and after[0]["scope"] == target:
            outcome = "identified"
            delays.append(after[0]["event"] - r["onset"])
        elif asked and (not after or asked[0]["event"] < after[0]["event"]):
            outcome = "asked"
        elif after:
            outcome = "wrong_scope"
        else:
            outcome = "missed"
        outcomes.append(outcome)
        by_factor.setdefault(r["factor"], []).append(outcome)
    n_changed = len(changed)
    rate = (lambda name: outcomes.count(name) / n_changed) if n_changed else (lambda name: None)
    identified_or_asked = (outcomes.count("identified") + outcomes.count("asked")) / n_changed if n_changed else None

    changed_scores = _policy_scores(changed, lambda r: [r["factor"]])
    unchanged_scores = _policy_scores(unchanged, lambda r: FACTORS)
    all_factor_changed = _policy_scores(changed, lambda r: FACTORS)
    cw_pooled = changed_scores["pooled"]["confident_wrong"]
    cw_detect = changed_scores["detecting"]["confident_wrong"]
    reduction = 1 - cw_detect / cw_pooled if cw_pooled else None
    task_loss = (100 * (unchanged_scores["pooled"]["accuracy"] - unchanged_scores["detecting"]["accuracy"])
                 if unchanged else None)
    coverage = unchanged_scores["detecting"]["coverage"]
    false_alarm = _mean(bool(r["detections"]) for r in unchanged)
    t = GROUNDING_THRESHOLDS
    checks = {
        "confident_wrong_reduction_at_least_50_percent": reduction is not None
        and reduction >= t["confident_wrong_relative_reduction_min"],
        "unchanged_coverage_at_least_80_percent": coverage is not None and coverage >= t["unchanged_coverage_min"],
        "unchanged_task_loss_at_most_2_points": task_loss is not None
        and task_loss <= t["unchanged_task_loss_points_max"],
        "changed_scope_identified_or_asked": identified_or_asked is not None
        and identified_or_asked >= t["identified_or_asked_rate_min"],
        "coverage_and_brier_reported": all(s[p]["brier"] is not None and s[p]["coverage"] is not None
                                           for s in (changed_scores, unchanged_scores)
                                           for p in ("pooled", "detecting")),
        "executive_asked_to_calibrate_each_detected_scope": all(ok for r in records for ok in r["calibrate_asks"]),
    }
    metrics = {
        "lifetimes": len(records), "changed_lifetimes": n_changed, "unchanged_lifetimes": len(unchanged),
        "changed_factor_counts": {f: sum(r["factor"] == f for r in changed) for f in FACTORS},
        "detection": {
            "identified_rate": rate("identified"), "asked_rate": rate("asked"),
            "identified_or_asked_rate": identified_or_asked, "wrong_scope_rate": rate("wrong_scope"),
            "missed_rate": rate("missed"),
            "by_factor": {f: {o: v.count(o) for o in ("identified", "asked", "wrong_scope", "missed")}
                          for f, v in sorted(by_factor.items())},
            "delay_events_mean": _mean(delays), "delay_events_median": float(np.median(delays)) if delays else None,
            "delay_events_max": max(delays) if delays else None,
            "false_alarms_before_onset_on_changed": sum(any(d["event"] < r["onset"] for d in r["detections"])
                                                        for r in changed),
            "unchanged_false_alarm_rate": false_alarm,
            "unchanged_ambiguity_ask_rate": _mean(bool(r["ambiguous"]) for r in unchanged),
            "mean_epochs_per_lifetime": _mean(r["epochs"] for r in records),
        },
        "changed_factor_final_answers": changed_scores,
        "changed_lifetimes_all_factors": all_factor_changed,
        "unchanged_all_factors": unchanged_scores,
        "confident_wrong": {"pooled": cw_pooled, "detecting": cw_detect, "relative_reduction": reduction},
        "unchanged_coverage_detecting": coverage,
        "unchanged_coverage_pooled": unchanged_scores["pooled"]["coverage"],
        "unchanged_task_loss_points": task_loss,
        "brier": {"changed_factor": {p: changed_scores[p]["brier"] for p in ("pooled", "detecting")},
                  "unchanged_all_factors": {p: unchanged_scores[p]["brier"] for p in ("pooled", "detecting")}},
        "reference_root_faults_total": sum(r["reference_root_faults"] for r in records),
    }
    notes = [
        f"Design fixed before running: {json.dumps(GROUNDING_DESIGN, sort_keys=True)}.",
        "Each lifetime draws mechanism, camera alignment and partner convention uniformly. Camera and word readings "
        "carry 10% reading noise; each reference root is secretly inverted with probability 0.15 (the instance's "
        "prior), and each root gives 2 readings.",
        "Changed lifetimes flip one factor silently at a step drawn uniformly from 20..29 of 40; the changed factor "
        "cycles camera, camera, partner, mechanism. Unchanged lifetimes never change.",
        "Pooled policy: one epoch for the whole lifetime (the prototype behaviour). Detecting policy: after every "
        "event, beliefs.detect_change; on a non-ambiguous suspicion the executive's calibrate question is recorded "
        "and resolved and a new epoch is started (the owner's recalibration); on an ambiguous one the executive's "
        "read-only decision is logged (test_reference when references exist) and evidence keeps pooling.",
        "Final answers are read from beliefs.compute of each policy's last epoch. Confidently wrong means P>0.95 on "
        "the wrong value. Coverage is the fraction of answers with P>0.95. Ties count 0.5 toward accuracy. Brier is "
        "(P(bit=1) - truth)^2 averaged over answers.",
        "Thresholds come from the brief (p.19); identified_or_asked_rate_min=0.80 was declared here before running.",
        "Simulated binary readings stand in for the brief's rendered pixels and tones; this does not exercise "
        "sensory decoding.",
    ]
    return _result("grounding", checks, metrics, dict(GROUNDING_THRESHOLDS, confidence=CONFIDENT), notes)


# ---- 3. lifelong correction --------------------------------------------------------------------------

_DAY_LATER = """
import json, sys
from neuroscifi.instance import beliefs, executive, governance, memory, store
db = store.open_existing(sys.argv[1])
now = float(sys.argv[2])
swept = governance.sweep(db, now)
exact = beliefs.compute(db, "home")
items = memory.retrieve(db, "belief", epoch="home", limit=1)["items"]
summary = items[0]["payload"]["summary"] if items else None
decision = executive.decide(db, "home", now)
db.close()
print(json.dumps({"swept": swept, "exact": exact, "semantic": summary,
                  "semantic_revision": items[0]["id"] if items else None, "decision": decision["decision"]}))
"""


def lifelong_correction(seed=DEFAULT_SEED):
    """ACC-03 / INS-09 / INS-13: a second partner with the opposite convention; the first partner a day later."""
    rng = np.random.default_rng([seed, 3])
    w, s = int(rng.integers(2)), int(rng.integers(2))
    convention = {"partner-A": 0, "partner-B": 1}
    clock = [T0]
    log = []

    def tick():
        clock[0] += 1
        return clock[0]

    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        path = Path(directory) / "home.sqlite"
        db = store.create(path, T0, note="acceptance lifelong correction")
        counter = [0]

        def observe(channel, root, relation, noisy=True):
            counter[0] += 1
            action = int(rng.integers(2))
            reading = relation ^ (int(rng.random() < .10) if noisy else 0)
            now = tick()
            evidence.observe(db, _obs(f"home-{counter[0]}", "home", channel, root, action, reading ^ action), now)
            decision = executive.decide(db, "home", now, record=True)
            state = beliefs.compute(db, "home")
            kind = None
            if decision["pending_id"] is not None:
                kind = next(p["kind"] for p in executive.pending(db, epoch="home", include_resolved=True)
                            if p["id"] == decision["pending_id"])
            log.append({"event": counter[0], "root": root, "decision": decision["decision"],
                        "scope": decision["scope"], "kind": kind, "pending_id": decision["pending_id"],
                        "partners": {r: v for r, v in state["partners"].items()}})
            return decision

        try:
            evidence.start_epoch(db, "home", tick())
            # Phase 1: partner A teaches with camera feedback; references from three healthy roots.
            for i in range(12):
                observe("camera", CAMERA_ROOT, w ^ s)
                observe("word", "partner-A", w ^ convention["partner-A"])
                if i % 2 == 1:
                    observe("reference", f"reference-{i // 4}", w)
            # Phase 2: partner B arrives. Their first four words contradict each other (B is still settling on
            # labels), which leaves B's meaning genuinely ambiguous; then B speaks consistently with 10% noise.
            for i in range(4):
                observe("word", "partner-B", w ^ convention["partner-B"] ^ (i % 2), noisy=False)
            for row in executive.pending(db, epoch="home"):
                if row["kind"] == "ask" and row["scope"] == "partner:partner-B":
                    executive.resolve(db, row["id"], "B uses the opposite word mapping", tick())
            for i in range(14):
                observe("camera", CAMERA_ROOT, w ^ s)
                observe("word", "partner-B", w ^ convention["partner-B"])
            # Phase 3: both partners speak in the same session.
            for i in range(6):
                observe("word", "partner-A", w ^ convention["partner-A"])
                observe("word", "partner-B", w ^ convention["partner-B"])
            final = beliefs.compute(db, "home")
            pending_open = executive.pending(db, epoch="home")
            asks_total = sum(1 for p in executive.pending(db, epoch="home", include_resolved=True)
                             if p["kind"] in ("ask", "calibrate"))
        finally:
            db.close()
        later = clock[0] + DAY
        child = _subprocess_json(_DAY_LATER, path, later)

    def map_bit(p):
        return None if abs(p - .5) < 1e-9 else int(p > .5)

    def score(state):
        out = {}
        mech = map_bit(state["mechanism"]["p_inverted"])
        for root, t in convention.items():
            p = state["partners"].get(root, {}).get("p_inverted", .5)
            meaning = map_bit(p)
            # Action for a heard word: action = word XOR convention XOR mechanism, so it is right exactly when
            # MAP(convention) XOR MAP(mechanism) equals the true convention XOR mechanism.
            action_ok = meaning is not None and mech is not None and (meaning ^ mech) == (t ^ w)
            out[root] = {"p_inverted": p, "meaning_correct": meaning == t, "action_correct": bool(action_ok)}
        return out

    in_session = score(final)
    exact = score(child["exact"])
    semantic = score(child["semantic"]) if child["semantic"] else {r: {"meaning_correct": False,
                                                                       "action_correct": False}
                                                                   for r in convention}
    meaning_asks = [e for e in log if e["decision"] == "ask" and e["kind"] == "ask"]
    other_asks = [e for e in log if e["decision"] == "ask" and e["kind"] != "ask"]

    def ambiguous_when_asked(entry):
        root = entry["scope"].split(":", 1)[1] if entry["scope"] and entry["scope"].startswith("partner:") else None
        v = entry["partners"].get(root)
        return v is not None and v["n"] >= executive.MIN_PARTNER_WORDS \
            and abs(v["p_inverted"] - .5) < beliefs.IDENTIFIABLE_MARGIN

    asks_by_partner = {}
    for entry in meaning_asks:
        asks_by_partner[entry["scope"]] = asks_by_partner.get(entry["scope"], 0) + 1
    checks = {
        "both_meanings_resolved_in_session": all(v["meaning_correct"] for v in in_session.values()),
        "both_actions_correct_in_session": all(v["action_correct"] for v in in_session.values()),
        "asks_only_while_partner_ambiguous": all(ambiguous_when_asked(e) for e in meaning_asks),
        "no_ask_without_ambiguity_or_change": not other_asks,
        "ambiguous_partner_was_asked_about": asks_by_partner.get("partner:partner-B", 0) >= 1,
        "nothing_expired_after_a_day": child["swept"]["expired"] == 0,
        "first_partner_correct_from_exact_evidence": exact["partner-A"]["meaning_correct"]
        and exact["partner-A"]["action_correct"],
        "first_partner_correct_without_exact_lookup": semantic["partner-A"]["meaning_correct"]
        and semantic["partner-A"]["action_correct"],
        "second_partner_correct_after_a_day": exact["partner-B"]["meaning_correct"]
        and semantic["partner-B"]["meaning_correct"],
    }
    metrics = {
        "true_bits": {"mechanism": w, "camera": s, "partner-A": 0, "partner-B": 1},
        "in_session": in_session, "day_later_exact": exact, "day_later_semantic": semantic,
        "day_later_semantic_revision": child["semantic_revision"], "day_later_decision": child["decision"],
        "day_later_clock_advance_seconds": DAY,
        "executive": {"decisions": len(log), "meaning_asks": len(meaning_asks), "asks_by_scope": asks_by_partner,
                      "other_asks": len(other_asks), "ask_rows_in_epoch": asks_total,
                      "decision_counts": {d: sum(e["decision"] == d for e in log)
                                          for d in sorted({e["decision"] for e in log})},
                      "open_questions_at_end": len(pending_open)},
    }
    notes = [
        "Simulated home epoch: 12 steps of camera feedback and partner A words (10% noise) with six healthy "
        "reference readings from three roots; then partner B with four deliberately contradictory words (to make "
        "B's meaning ambiguous so the ask rule is exercised), the owner's answer, 14 consistent steps of B (10% "
        "noise), and 6 steps where A and B alternate.",
        "executive.decide(record=True) runs after every event; each meaning question (pending kind 'ask') is "
        "checked against beliefs.compute at that moment: >= 4 words and |p - 0.5| < 0.37 for that partner.",
        "A day later: the connection is closed, the clock advances 86,400 s, and a NEW process opens the file, "
        "sweeps expiry at the later time, scores A and B from beliefs.compute (exact evidence) and from the "
        "latest stored belief-revision summary only (semantic memory, no evidence lookup).",
        "References are healthy here; reference-root faults are exercised in the grounding scenario.",
    ]
    return _result("lifelong_correction", checks, metrics, {"ambiguity_margin": beliefs.IDENTIFIABLE_MARGIN,
                                                            "min_partner_words": executive.MIN_PARTNER_WORDS}, notes)


# ---- 4. graph development ----------------------------------------------------------------------------

def _least_squares(states, targets, support):
    """Per-receiver least squares restricted to a boolean support mask (row j = inputs of receiver j)."""
    n = states.shape[1]
    matrix = np.zeros((n, n))
    for j in range(n):
        cols = np.where(support[j])[0]
        if len(cols):
            matrix[j, cols] = np.linalg.lstsq(states[:, cols], targets[:, j], rcond=None)[0]
    return matrix


def _fork(path, world_seed, now):
    """Learn and promote one fork's world under the owner's degree-6 rule; return what the scenario scores."""
    db = store.open_existing(path)
    try:
        with store.write(db):
            rules.register(db, DEVELOPMENT_RULE, now, created_by="owner")
        ids = datasets.simulate(db, "world", now + 1, seed=world_seed)
        revision = graphs.learn(db, "world", now + 2, train=ids["train"], validation=ids["validation"],
                                holder="acceptance")
        promotion = graphs.promote(db, revision, now + 3)
        evaluation = graphs.evaluate(db, revision, now + 4)
        lesion = graphs.lesion(db, revision, ids["heldout"], now + 5)
        shown = graphs.show(db, revision)
        train = datasets.load(db, ids["train"])
        heldout = datasets.load(db, ids["heldout"])
        provenance = {d["provenance"] for d in datasets.listing(db)}
    finally:
        db.close()
    return {"revision": revision, "promoted": promotion["promoted"], "evaluation": evaluation, "lesion": lesion,
            "matrix": np.array(shown["matrix"]), "degree": shown["degree"], "bits": shown["bits"],
            "byte_size": shown["byte_size"], "train": train, "heldout": heldout, "provenance": provenance}


def graph_development(seed=DEFAULT_SEED):
    """ACC-04 / GRA-02 / GRA-11: fork one individual into two worlds, cross-test, lesion, permute, compare."""
    world_seeds = {"fork-1": seed + 101, "fork-2": seed + 202}
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        base = Path(directory) / "base.sqlite"
        db = store.create(base, T0, note="acceptance graph development: parent before forking")
        birth_record = store.birth(db)
        db.close()
        forks = {}
        for i, (name, world_seed) in enumerate(world_seeds.items()):
            path = Path(directory) / f"{name}.sqlite"
            shutil.copyfile(base, path)
            path.chmod(0o600)
            forks[name] = _fork(path, world_seed, T0 + 100 * (i + 1))
            fork_db = store.open_existing(path)
            try:
                forks[name]["same_birth"] = store.birth(fork_db) == birth_record
            finally:
                fork_db.close()
    names = list(forks)
    cross = {f"{m}_graph_on_{h}_heldout": learner.mse(forks[m]["matrix"], *forks[h]["heldout"])
             for m in names for h in names}
    per_fork, checks = {}, {}
    for i, name in enumerate(names):
        other = names[1 - i]
        fork = forks[name]
        truth, _ = graph_bench.world(np.random.default_rng(world_seeds[name]))
        edges = fork["lesion"]["edges"]
        true_effects = [e["delta_mse"] for e in edges if truth[e["dst"], e["src"]] != 0]
        weak_effects = [e["delta_mse"] for e in edges if truth[e["dst"], e["src"]] == 0]
        x, y = fork["train"]
        hx, hy = fork["heldout"]
        n = x.shape[1]
        support_rng = np.random.default_rng([seed, 4, i])
        fixed_support = np.zeros((n, n), bool)
        for j in range(n):
            fixed_support[j, support_rng.choice(np.delete(np.arange(n), j), fork["degree"], replace=False)] = True
        fixed = _least_squares(x, y, fixed_support)
        dense = _least_squares(x, y, ~np.eye(n, dtype=bool))
        own = cross[f"{name}_graph_on_{name}_heldout"]
        foreign = cross[f"{other}_graph_on_{name}_heldout"]
        heldout = {"promoted_graph": own, "fixed_random_sparse_lstsq_baseline": learner.mse(fixed, hx, hy),
                   "dense_lstsq_baseline": learner.mse(dense, hx, hy),
                   "permuted_control": fork["lesion"]["permuted_control_mse"],
                   "other_fork_graph": foreign, "zero_predictor": float(np.mean(hy ** 2))}
        per_fork[name] = {
            "world_seed": world_seeds[name], "revision": fork["revision"], "promoted": fork["promoted"],
            "degree": fork["degree"], "bits": fork["bits"], "byte_size": fork["byte_size"],
            "evaluated_heldout_mse": fork["evaluation"]["heldout_mse"],
            "heldout_mse": heldout, "cross_over_own_ratio": foreign / own,
            "lesion": {"edges": len(edges), "true_edges": len(true_effects), "weak_edges": len(weak_effects),
                       "true_edge_mean_delta_mse": _mean(true_effects), "weak_edge_mean_delta_mse": _mean(weak_effects),
                       "base_mse": fork["lesion"]["base_mse"]},
            "same_birth_record_as_parent": fork["same_birth"],
            "dataset_provenance": sorted(fork["provenance"]),
        }
        checks.update({
            f"{name}_promoted": fork["promoted"],
            f"{name}_forked_from_same_individual": fork["same_birth"],
            f"{name}_better_on_own_world_than_other_fork": own < foreign,
            f"{name}_true_edges_matter_more_than_weak": bool(true_effects) and bool(weak_effects)
            and _mean(true_effects) > _mean(weak_effects),
            f"{name}_permuted_control_worse": heldout["permuted_control"] > own,
            f"{name}_beats_fixed_random_sparse": own < heldout["fixed_random_sparse_lstsq_baseline"],
            f"{name}_within_10_percent_of_dense": own <= DENSE_BASELINE_FACTOR * heldout["dense_lstsq_baseline"],
            f"{name}_datasets_simulated": fork["provenance"] == {SIMULATED},
        })
    notes = [
        "One individual is born, closed and copied into two files (fork-1, fork-2) that share its birth record. "
        "Each fork registers an owner rule allowing degree 6 at 8 bits (3 true + 3 weak inputs per receiver), "
        "simulates a different graph_bench world (seeds seed+101 and seed+202), learns, promotes, evaluates and "
        "lesions on its own held-out set.",
        "Baselines computed here in numpy from each fork's training set and labelled as baselines: a fixed random "
        "sparse support with the same degree fitted by least squares, and a dense least-squares graph (no self "
        "edges). Equal-cost recurrent alternatives from the brief are not implemented in this release.",
        "Lesion classification uses the generator matrix of each simulated world (true edge vs weak edge).",
    ]
    return _result("graph_development", checks, {"cross_test": cross, "forks": per_fork},
                   {"dense_baseline_factor_max": DENSE_BASELINE_FACTOR, "rule": DEVELOPMENT_RULE["version"]}, notes)


# ---- 5. growth and retention -------------------------------------------------------------------------

def _learn_promote(db, context, now, *, seed, world="a"):
    ids = datasets.simulate(db, context, now, seed=seed, world=world)
    revision = graphs.learn(db, context, now + 1, train=ids["train"], validation=ids["validation"],
                            holder="acceptance")
    return revision, ids, graphs.promote(db, revision, now + 2)


def growth_retention(seed=DEFAULT_SEED):
    """ACC-05 / GRA-04..07: the gate rejects a rewired world for retention, a new context keeps both skills."""
    world_seed = seed + 303
    a, b = graph_bench.world(np.random.default_rng(world_seed))
    unrelated, _ = graph_bench.world(np.random.default_rng(world_seed + 1))
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        path = Path(directory) / "growth.sqlite"
        db = store.create(path, T0, note="acceptance growth and retention")
        try:
            first, ids_a, first_promotion = _learn_promote(db, "desk", T0 + 10, seed=world_seed)
            rewired, _, rejected = _learn_promote(db, "desk", T0 + 20, seed=world_seed, world="b")
            remounted, _, accepted = _learn_promote(db, "desk-remounted", T0 + 30, seed=world_seed, world="b")
            active = {r["context"]: r["id"] for r in graphs.listing(db) if r["status"] == "active"}

            rng = np.random.default_rng([seed, 5])
            parts, truth = [], []
            for context, matrix in (("desk-remounted", b), ("desk", a), ("desk-remounted", b), ("desk", a)):
                parts.append(graph_bench.stream(matrix, 128, rng))
                truth += [context] * 128
            states = np.vstack([p[0] for p in parts])
            targets = np.vstack([p[1] for p in parts])
            probe = datasets.add(db, "probe", "heldout", states, targets, SIMULATED, T0 + 40,
                                 note="alternating desk-remounted/desk segments")
            known = recall.run(db, probe, T0 + 41)
            chosen = [c["context"] for c in known["choices"] for _ in range(c["from"], c["to"])]
            accuracy = float(np.mean([c == t for c, t in zip(chosen, truth)]))

            u_states, u_targets = graph_bench.stream(unrelated, 512, np.random.default_rng([seed, 6]))
            stranger = datasets.add(db, "stranger", "heldout", u_states, u_targets, SIMULATED, T0 + 50,
                                    note="unrelated world")
            revisions_before = len(graphs.listing(db))
            belief_revisions_before = store.inventory(db)["memory"]["belief_revisions"]
            unknown = recall.run(db, stranger, T0 + 51)
            revisions_after = len(graphs.listing(db))
            belief_revisions_after = store.inventory(db)["memory"]["belief_revisions"]
            new_context_questions = [p for p in executive.pending(db) if p["kind"] == "new_context"]

            # Capacity to learn the next skill after both are stored.
            third, _, third_promotion = _learn_promote(db, "workshop", T0 + 60, seed=world_seed + 2)
            still_active = {r["context"]: r["id"] for r in graphs.listing(db) if r["status"] == "active"}
            provenance = {d["provenance"] for d in datasets.listing(db)}
        finally:
            db.close()

        # Lookup disabled: a copy in which every dataset except the probe stream is unreadable.
        copy_path = Path(directory) / "lookup-disabled.sqlite"
        shutil.copyfile(path, copy_path)
        copy_path.chmod(0o600)
        raw = sqlite3.connect(copy_path)
        try:
            raw.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id!=?", (T0 + 70, probe))
            raw.commit()
        finally:
            raw.close()
        copy = store.open_existing(copy_path)
        try:
            readable = []
            for d in datasets.listing(copy):
                try:
                    datasets.load(copy, d["id"])
                    readable.append(d["id"])
                except Gone:
                    pass
            blind = recall.run(copy, probe, T0 + 71, contexts=["desk", "desk-remounted"], record=False)
        finally:
            copy.close()

    decision = rejected["decision"]
    retention = decision["checks"]["retention"]
    checks = {
        "first_context_promoted": first_promotion["promoted"],
        "rewired_candidate_rejected_for_retention": not rejected["promoted"] and decision["failed"] == ["retention"],
        "rejection_recommends_new_context": decision.get("recommendation") == graphs.NEW_CONTEXT_ADVICE,
        "new_context_promoted": accepted["promoted"],
        "both_skills_active": active.get("desk") == first and active.get("desk-remounted") == remounted,
        "recall_context_accuracy_at_least_90_percent": accuracy >= .90,
        "recall_selected_beats_latest_only": known["selected_mse"] < known["latest_only_mse"],
        "unrelated_world_is_unknown_context": unknown["verdict"] == "unknown_context",
        "unknown_context_opens_a_question": unknown["pending_id"] is not None
        and any(p["id"] == unknown["pending_id"] for p in new_context_questions),
        "unknown_context_creates_no_graph_or_belief_revision": revisions_after == revisions_before
        and belief_revisions_after == belief_revisions_before,
        "next_skill_learned_without_losing_older_ones": third_promotion["promoted"]
        and still_active.get("desk") == first and still_active.get("desk-remounted") == remounted
        and still_active.get("workshop") == third,
        "lookup_disabled_only_probe_readable": readable == [probe],
        "lookup_disabled_recall_identical": blind["choices"] == known["choices"]
        and blind["selected_mse"] == known["selected_mse"],
        "datasets_simulated": provenance == {SIMULATED},
    }
    metrics = {
        "revisions": {"desk": first, "desk_rewired_candidate": rewired, "desk-remounted": remounted,
                      "workshop": third},
        "rejection": {"failed": decision["failed"], "recommendation": decision.get("recommendation"),
                      "heldout_gain": {k: decision["checks"]["heldout_gain"].get(k)
                                       for k in ("candidate_mse", "incumbent_mse", "passed")},
                      "retention": retention.get("datasets"), "thresholds": decision["thresholds"]},
        "recall": {"steps": known["steps"], "context_accuracy": accuracy, "selected_mse": known["selected_mse"],
                   "latest_only_mse": known["latest_only_mse"], "unknown_fraction": known["unknown_fraction"],
                   "verdict": known["verdict"], "segments": len(known["choices"])},
        "unknown_context": {"verdict": unknown["verdict"], "unknown_fraction": unknown["unknown_fraction"],
                            "pending_id": unknown["pending_id"], "graph_revisions_before": revisions_before,
                            "graph_revisions_after": revisions_after},
        "next_skill": {"promoted": third_promotion["promoted"],
                       "heldout_mse": third_promotion["decision"]["checks"]["heldout_gain"].get("candidate_mse")},
        "lookup_disabled": {"readable_datasets": readable, "selected_mse": blind["selected_mse"],
                            "verdict": blind["verdict"]},
    }
    notes = [
        f"Worlds a and b are the original and rewired pair of graph_bench.world(seed {world_seed}); the unrelated "
        f"world uses seed {world_seed + 1}; the next skill uses seed {world_seed + 2}.",
        "Recall probe: four 128-step segments alternating desk-remounted, desk, desk-remounted, desk; context "
        "labels are used only for scoring.",
        "Lookup disabled: the harness copies the file and blanks every dataset blob except the probe with raw SQL on "
        "the copy. The governed path (governance.forget_dataset) would instead invalidate the graphs learned from "
        "those datasets, which is the intended deletion cascade, so it cannot be used to disable lookup.",
        "The lookup-disabled replay is restricted to the same two contexts as the original replay (the copy also "
        "holds the later 'workshop' skill).",
        "The capacity to learn the next skill is measured as a third world promoted under the same gate while the "
        "two earlier skills stay active.",
    ]
    return _result("growth_retention", checks, metrics, {"context_accuracy_min": .90, **store.GATE_V1}, notes)


# ---- 6. device ownership -----------------------------------------------------------------------------

_RESTART = """
import json, sys
from neuroscifi.instance import beliefs, executive, store
db = store.open_existing(sys.argv[1])
out = {"beliefs": beliefs.compute(db, "bench"), "pending": executive.pending(db, include_resolved=True),
       "inventory": store.inventory(db)}
db.close()
print(json.dumps(out))
"""


def device_ownership(seed=DEFAULT_SEED, events=DEFAULT_EVENTS):
    """ACC-06 / INS-02 / INS-17 / OPS-06 (host part): loop latency, restart, checkpoint restore, storage pressure."""
    truth, stream = teaching_stream(np.random.default_rng([seed, 7]), events)
    latencies, decisions = [], {}
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        directory = Path(directory)
        path = directory / "device.sqlite"
        db = store.create(path, T0, note="acceptance device ownership")
        try:
            evidence.start_epoch(db, "bench", T0)
            for i, obs in enumerate(stream):
                now = T0 + 1 + i
                start = time.perf_counter()
                evidence.observe(db, {**obs, "id": f"dev-{i}", "epoch": "bench"}, now, path=path)
                decision = executive.decide(db, "bench", now, record=True, path=path)
                latencies.append(time.perf_counter() - start)
                decisions[decision["decision"]] = decisions.get(decision["decision"], 0) + 1
            final_decision = decision["decision"]
            before = _plain({"beliefs": beliefs.compute(db, "bench"),
                             "pending": executive.pending(db, include_resolved=True),
                             "inventory": store.inventory(db)})
        finally:
            db.close()
        after = _subprocess_json(_RESTART, path)

        db = store.open_existing(path)
        try:
            checkpoint = checkpoints.create(db, path, directory / "checkpoints", T0 + events + 10)
            verified = checkpoints.verify(db, checkpoint["id"], T0 + events + 11)
            restored = checkpoints.restore(db, checkpoint["id"], directory / "restored.sqlite", T0 + events + 12)
            live_beliefs = _plain(beliefs.compute(db, "bench"))
        finally:
            db.close()
        copy = store.open_existing(directory / "restored.sqlite")
        try:
            restored_beliefs = _plain(beliefs.compute(copy, "bench"))
        finally:
            copy.close()

        pressure = _storage_pressure(directory / "pressured.sqlite", seed)

    p = percentiles(latencies)
    mech = before["beliefs"]["mechanism"]["p_inverted"]
    checks = {
        "loop_reaches_act": final_decision == "act",
        "beliefs_correct_after_loop": int(mech > .5) == truth["mechanism"] and before["beliefs"]["mechanism"]["identifiable"],
        "restart_new_process_identical_beliefs": after["beliefs"] == before["beliefs"],
        "restart_new_process_identical_pending": after["pending"] == before["pending"],
        "restart_new_process_identical_inventory": after["inventory"] == before["inventory"],
        "checkpoint_verified": verified["ok"],
        "checkpoint_restore_identical_beliefs": restored_beliefs == live_beliefs,
        **{f"storage_pressure_{k}": v for k, v in pressure["checks"].items()},
    }
    metrics = {
        "loop": {"events": events, "label": HOST_LABEL, "latency_ms": p, "decision_counts": decisions,
                 "final_decision": final_decision},
        "restart": {"state_version": before["inventory"]["state_version"],
                    "evidence": before["inventory"]["memory"]["evidence"], "pending_rows": len(before["pending"])},
        "checkpoint": {"policy": checkpoint["policy"], "bytes": checkpoint["bytes"],
                       "tombstones_applied": restored["tombstones_applied"]},
        "storage_pressure": pressure["metrics"],
    }
    notes = [
        f"Capture-to-action loop: {events} simulated events (camera, word, healthy reference in turn, 10% noise), "
        "each timed from evidence.observe through executive.decide(record=True). Latency is "
        f"{HOST_LABEL}; no device latency budget is declared, so it is reported, not judged.",
        "Restart: the connection is closed and a NEW process opens the file and returns beliefs, pending questions "
        "and the inventory, compared with the values read before closing.",
        "Not representable in simulation: physical capture devices, reversible switchover of device ownership, "
        "and latency under simultaneous inference and consolidation on the Spark (OPS-06 stays blocked).",
    ]
    return _result("device_ownership", checks, metrics, {"latency_budget": None}, notes)


def _storage_pressure(path, seed):
    """Fill a tiny evidence budget; writes pause while beliefs and an approved graph stay usable."""
    limit = 12
    db = store.create(path, T0, config=store.InstanceConfig(max_evidence_rows=limit),
                      note="acceptance storage pressure")
    try:
        revision, ids, promotion = _learn_promote(db, "desk", T0 + 1, seed=seed + 404)
        _, stream = teaching_stream(np.random.default_rng([seed, 8]), limit + 1)
        evidence.start_epoch(db, "bench", T0 + 5)
        for i, obs in enumerate(stream[:limit]):
            evidence.observe(db, {**obs, "id": f"p-{i}", "epoch": "bench"}, T0 + 10 + i, path=path)
        paused = False
        try:
            evidence.observe(db, {**stream[limit], "id": f"p-{limit}", "epoch": "bench"}, T0 + 50, path=path)
        except WritesPaused:
            paused = True
        decision = executive.decide(db, "bench", T0 + 51, record=True, path=path)
        state = beliefs.compute(db, "bench")
        replay = recall.run(db, ids["heldout"], T0 + 52, record=False)
        regulation = store.regulation(db, path)
        rows = store.inventory(db)["memory"]["evidence"]
    finally:
        db.close()
    checks = {
        "observe_raises_writes_paused": paused,
        "decide_returns_defer": decision["decision"] == "defer",
        "nothing_written_while_paused": rows == limit,
        "beliefs_still_readable": state["evidence"]["total"] == limit,
        "approved_skill_still_usable": promotion["promoted"] and replay["verdict"] == "known:desk",
        "regulation_reports_pause": regulation["writes_allowed"] is False,
    }
    metrics = {"max_evidence_rows": limit, "evidence_rows": rows, "decision": decision["decision"],
               "regulation_reason": regulation["reason"], "recall_verdict": replay["verdict"],
               "recall_selected_mse": replay["selected_mse"], "graph_revision": revision}
    return {"checks": checks, "metrics": metrics}


# ---- runner ------------------------------------------------------------------------------------------

SCENARIOS = {
    "birth": birth,
    "grounding": grounding,
    "lifelong_correction": lifelong_correction,
    "graph_development": graph_development,
    "growth_retention": growth_retention,
    "device_ownership": device_ownership,
}


def run(names=None, *, seed=DEFAULT_SEED, lifetimes=DEFAULT_LIFETIMES, events=DEFAULT_EVENTS):
    """Run the named scenarios (all when None) and return {"simulated", "pass", "seed", "scenarios"}."""
    names = list(SCENARIOS) if not names else list(names)
    unknown = sorted(set(names) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(unknown)}; choose from {', '.join(SCENARIOS)}")
    results = {}
    for name in names:
        kwargs = {"seed": seed}
        if name == "grounding":
            kwargs["lifetimes"] = lifetimes
        if name == "device_ownership":
            kwargs["events"] = events
        start = time.perf_counter()
        results[name] = SCENARIOS[name](**kwargs)
        results[name]["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    return {"simulated": True, "pass": all(r["pass"] for r in results.values()), "seed": seed,
            "parameters": {"lifetimes": lifetimes, "events": events}, "scenarios": results,
            "label": "simulated acceptance scenarios; not a physical validation"}
