"""Executable, simulated versions of "What the first release must demonstrate" (Architecture.md p.19) and of
the first physical test (brief p.34).

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
from .bench import host_info, percentiles, teaching_stream

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


def _owner_confirms(life, scope, event):
    """The simulated owner's truthful answer to "did <scope> change?" after event index `event`."""
    return bool(life["changed"]) and scope == _scope(life["factor"]) and event >= life["onset"]


def _run_detecting(db, life, index, *, manual):
    """Feed one lifetime to a detecting arm. Returns final answers, the detection log and the owner's answers.

    After every event beliefs.detect_change runs. On a non-ambiguous suspicion the executive's recorded decision
    is taken: an ask is answered by the simulated owner. Product flow (manual=False): the owner answers
    executive.resolve(..., confirm=True) when the asked scope truly changed (and already had), else
    confirm=False, and the lifetime stays in one epoch. Manual new epoch (manual=True, the previous policy):
    the owner closes the question and a new epoch is started, so every factor is relearned from nothing.
    """
    base = T0 + index * 1000.0
    k = 0
    epoch = f"L{index}-d0"
    evidence.start_epoch(db, epoch, base)
    detections, ambiguous, addressed, owner = [], [], [], {"answers": 0, "confirmed": 0, "rejected": 0}
    deferred = 0
    for j, event in enumerate(life["events"]):
        now = base + j + 1
        evidence.observe(db, _obs(f"L{index}-{j}", epoch, event["channel"], event["root"], event["action"],
                                  event["bit"]), now)
        change = beliefs.detect_change(db, epoch)
        if not change["suspected"]:
            continue
        if change["ambiguous"]:
            decision = executive.decide(db, epoch, now)   # read-only: what would the executive do?
            ambiguous.append({"event": j, "scope": change["scope"], "decision": decision["decision"]})
            continue
        decision = executive.decide(db, epoch, now, record=True)
        # The executive must address the suspected scope: ask about it, or defer on it (ask budget exhausted,
        # or the owner already answered about it and too few new events of that scope arrived since).
        addressed.append(decision["scope"] == change["scope"] and decision["decision"] in ("ask", "defer"))
        detections.append({"event": j, "scope": change["scope"], "llr": change["llr"]})
        asked = decision["decision"] == "ask" and decision["pending_id"] is not None
        if not asked:
            deferred += 1
        if manual:
            if asked:
                executive.resolve(db, decision["pending_id"], "owner confirmed and recalibrated", now)
                owner["answers"] += 1
            k += 1
            epoch = f"L{index}-d{k}"
            evidence.start_epoch(db, epoch, now, note=f"recalibrated after a suspected {change['scope']} change")
        elif asked:
            confirm = _owner_confirms(life, change["scope"], j)
            executive.resolve(db, decision["pending_id"], "yes, it changed" if confirm else "no, nothing changed",
                              now, confirm=confirm)
            owner["answers"] += 1
            owner["confirmed" if confirm else "rejected"] += 1
    return {"answers": _answers(beliefs.compute(db, epoch)), "detections": detections, "ambiguous": ambiguous,
            "addressed": addressed, "deferred": deferred, "owner": owner, "epochs": k + 1}


def _run_pooled(db, life, index):
    """The prototype: one epoch for the whole lifetime, no change detection."""
    epoch = f"L{index}-pooled"
    base = T0 + index * 1000.0
    evidence.start_epoch(db, epoch, base)
    for j, event in enumerate(life["events"]):
        evidence.observe(db, _obs(f"L{index}-{j}", epoch, event["channel"], event["root"], event["action"],
                                  event["bit"]), base + j + 1)
    return _answers(beliefs.compute(db, epoch))


GROUNDING_ARMS = ("pooled", "detecting", "manual_new_epoch")


def _policy_scores(records, factor_of, policies=GROUNDING_ARMS):
    """Confident-wrong count, accuracy, coverage and Brier over (record, factor) answers for each policy."""
    out = {}
    for policy in policies:
        answers = [(r[policy][f], r["truth"][f]) for r in records for f in factor_of(r)]
        out[policy] = {
            "answers": len(answers),
            "confident_wrong": int(sum(_confident_wrong(p, t) for p, t in answers)),
            "accuracy": _mean(_correct(p, t) for p, t in answers),
            "coverage": _mean(_confident(p) for p, t in answers),
            "brier": _mean(_brier(p, t) for p, t in answers),
        }
    return out


def _detection(changed, arm):
    """Classify each changed lifetime by the arm's first confident detection at or after the change's onset."""
    outcomes, delays, by_factor = [], [], {}
    for r in changed:
        log = r["logs"][arm]
        after = [d for d in log["detections"] if d["event"] >= r["onset"]]
        asked = [a for a in log["ambiguous"] if a["event"] >= r["onset"]]
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
    n = len(changed)
    rate = (lambda name: outcomes.count(name) / n) if n else (lambda name: None)
    return {
        "identified_rate": rate("identified"), "asked_rate": rate("asked"),
        "identified_or_asked_rate": (outcomes.count("identified") + outcomes.count("asked")) / n if n else None,
        "wrong_scope_rate": rate("wrong_scope"), "missed_rate": rate("missed"),
        "by_factor": {f: {o: v.count(o) for o in ("identified", "asked", "wrong_scope", "missed")}
                      for f, v in sorted(by_factor.items())},
        "delay_events_mean": _mean(delays), "delay_events_median": float(np.median(delays)) if delays else None,
        "delay_events_max": max(delays) if delays else None,
        "false_alarms_before_onset_on_changed": sum(any(d["event"] < r["onset"] for d in r["logs"][arm]["detections"])
                                                    for r in changed),
    }


def grounding(seed=DEFAULT_SEED, lifetimes=DEFAULT_LIFETIMES):
    """ACC-02 / INS-11 with the brief's p.19 thresholds (ACCEPTANCE_CRITERIA G4).

    Arms on the same streams: pooled (the prototype), detecting (the product flow: the owner confirms or rejects
    the executive's change question and the lifetime stays in one epoch) and manual_new_epoch (the earlier
    policy: every detection starts a new epoch). The checks judge the detecting arm.
    """
    if isinstance(lifetimes, bool) or not isinstance(lifetimes, int) or lifetimes < 2:
        raise ValueError("lifetimes must be an integer >= 2")
    records = []
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        dbs = {arm: store.create(Path(directory) / f"{arm}.sqlite", T0, note=f"acceptance grounding: {arm} arm")
               for arm in GROUNDING_ARMS}
        try:
            for index in range(lifetimes):
                life = _grounding_lifetime(seed, index)
                product = _run_detecting(dbs["detecting"], life, index, manual=False)
                manual = _run_detecting(dbs["manual_new_epoch"], life, index, manual=True)
                records.append({"pooled": _run_pooled(dbs["pooled"], life, index),
                                "detecting": product["answers"], "manual_new_epoch": manual["answers"],
                                "logs": {"detecting": product, "manual_new_epoch": manual},
                                **{key: life[key] for key in ("truth", "changed", "factor", "onset", "change_step",
                                                              "reference_root_faults")}})
        finally:
            for db in dbs.values():
                db.close()
    changed = [r for r in records if r["changed"]]
    unchanged = [r for r in records if not r["changed"]]
    n_changed = len(changed)
    detection = {arm: _detection(changed, arm) for arm in ("detecting", "manual_new_epoch")}
    for arm in detection:
        detection[arm].update(
            unchanged_false_alarm_rate=_mean(bool(r["logs"][arm]["detections"]) for r in unchanged),
            unchanged_ambiguity_ask_rate=_mean(bool(r["logs"][arm]["ambiguous"]) for r in unchanged),
            mean_epochs_per_lifetime=_mean(r["logs"][arm]["epochs"] for r in records))
    identified_or_asked = detection["detecting"]["identified_or_asked_rate"]

    changed_scores = _policy_scores(changed, lambda r: [r["factor"]])
    unchanged_scores = _policy_scores(unchanged, lambda r: FACTORS)
    all_factor_changed = _policy_scores(changed, lambda r: FACTORS)
    cw = {arm: changed_scores[arm]["confident_wrong"] for arm in GROUNDING_ARMS}
    reduction = {arm: (1 - cw[arm] / cw["pooled"] if cw["pooled"] else None)
                 for arm in ("detecting", "manual_new_epoch")}
    task_loss = {arm: (100 * (unchanged_scores["pooled"]["accuracy"] - unchanged_scores[arm]["accuracy"])
                       if unchanged else None) for arm in ("detecting", "manual_new_epoch")}
    coverage = unchanged_scores["detecting"]["coverage"]
    camera_changed = [r for r in changed if r["factor"] == "camera"]
    partner_kept = {arm: _mean(_correct(r[arm]["partner"], r["truth"]["partner"]) == 1 and _confident(r[arm]["partner"])
                               for r in camera_changed) for arm in GROUNDING_ARMS}

    def questions(arm, subset):
        counts = [r["logs"][arm]["owner"]["answers"] for r in subset]
        return {"mean": _mean(counts), "max": max(counts) if counts else None, "total": int(sum(counts))}

    owner = {arm: {"all": questions(arm, records), "changed": questions(arm, changed),
                   "unchanged": questions(arm, unchanged)} for arm in ("detecting", "manual_new_epoch")}
    owner["detecting"]["confirmed_total"] = sum(r["logs"]["detecting"]["owner"]["confirmed"] for r in records)
    owner["detecting"]["rejected_total"] = sum(r["logs"]["detecting"]["owner"]["rejected"] for r in records)
    owner["detecting"]["deferred_suspicions_total"] = sum(r["logs"]["detecting"]["deferred"] for r in records)
    t = GROUNDING_THRESHOLDS
    checks = {
        "confident_wrong_reduction_at_least_50_percent": reduction["detecting"] is not None
        and reduction["detecting"] >= t["confident_wrong_relative_reduction_min"],
        "unchanged_coverage_at_least_80_percent": coverage is not None and coverage >= t["unchanged_coverage_min"],
        "unchanged_task_loss_at_most_2_points": task_loss["detecting"] is not None
        and task_loss["detecting"] <= t["unchanged_task_loss_points_max"],
        "changed_scope_identified_or_asked": identified_or_asked is not None
        and identified_or_asked >= t["identified_or_asked_rate_min"],
        "coverage_and_brier_reported": all(s[p]["brier"] is not None and s[p]["coverage"] is not None
                                           for s in (changed_scores, unchanged_scores) for p in GROUNDING_ARMS),
        "executive_addressed_each_detected_scope": all(ok for r in records for arm in ("detecting", "manual_new_epoch")
                                                       for ok in r["logs"][arm]["addressed"]),
    }
    metrics = {
        "lifetimes": len(records), "changed_lifetimes": n_changed, "unchanged_lifetimes": len(unchanged),
        "arms": {"pooled": "fixed wiring: one epoch, no change detection (the prototype)",
                 "detecting": "product flow: the owner confirms or rejects the executive's change question; "
                              "one epoch per lifetime",
                 "manual_new_epoch": "earlier policy: every detection is answered by starting a new epoch"},
        "changed_factor_counts": {f: sum(r["factor"] == f for r in changed) for f in FACTORS},
        "detection": detection["detecting"],
        "detection_manual_new_epoch": detection["manual_new_epoch"],
        "changed_factor_final_answers": changed_scores,
        "changed_lifetimes_all_factors": all_factor_changed,
        "unchanged_all_factors": unchanged_scores,
        "confident_wrong": {"pooled": cw["pooled"], "detecting": cw["detecting"],
                            "manual_new_epoch": cw["manual_new_epoch"], "relative_reduction": reduction["detecting"],
                            "relative_reduction_manual_new_epoch": reduction["manual_new_epoch"]},
        "unchanged_coverage_detecting": coverage,
        "unchanged_coverage_pooled": unchanged_scores["pooled"]["coverage"],
        "unchanged_coverage_manual_new_epoch": unchanged_scores["manual_new_epoch"]["coverage"],
        "unchanged_task_loss_points": task_loss["detecting"],
        "unchanged_task_loss_points_manual_new_epoch": task_loss["manual_new_epoch"],
        "partner_meaning_preserved_after_camera_change": {"lifetimes": len(camera_changed), **partner_kept},
        "owner_questions_per_lifetime": owner,
        "brier": {"changed_factor": {p: changed_scores[p]["brier"] for p in GROUNDING_ARMS},
                  "unchanged_all_factors": {p: unchanged_scores[p]["brier"] for p in GROUNDING_ARMS}},
        "reference_root_faults_total": sum(r["reference_root_faults"] for r in records),
    }
    notes = [
        f"Design fixed before running: {json.dumps(GROUNDING_DESIGN, sort_keys=True)}.",
        "Each lifetime draws mechanism, camera alignment and partner convention uniformly. Camera and word readings "
        "carry 10% reading noise; each reference root is secretly inverted with probability 0.15 (the instance's "
        "prior), and each root gives 2 readings.",
        "Changed lifetimes flip one factor silently at a step drawn uniformly from 20..29 of 40; the changed factor "
        "cycles camera, camera, partner, mechanism. Unchanged lifetimes never change.",
        "Pooled arm: one epoch for the whole lifetime (the prototype behaviour). Detecting arm (the product flow, "
        "judged by the checks): after every event, beliefs.detect_change; on a non-ambiguous suspicion the "
        "executive's decision is recorded and its question answered by a truthful simulated owner with "
        "executive.resolve(confirm=True) when the asked scope really changed (at or before that event), else "
        "confirm=False; the lifetime stays in one epoch, so a confirmed camera change relearns only the camera. "
        "Manual new epoch arm (the earlier policy, kept for comparison): the question is closed and a new epoch "
        "started, so every factor is relearned. On an ambiguous suspicion both detecting arms log the executive's "
        "read-only decision (test_reference when references exist) and keep pooling.",
        "The executive check: on every non-ambiguous suspicion the executive asks about that scope or defers on it "
        "(ask budget of 3 per epoch exhausted, or the owner already answered about it with fewer than 3 new events "
        "of that scope since).",
        "Final answers are read from beliefs.compute of each arm's last epoch. Confidently wrong means P>0.95 on "
        "the wrong value. Coverage is the fraction of answers with P>0.95. Ties count 0.5 toward accuracy. Brier is "
        "(P(bit=1) - truth)^2 averaged over answers. Partner meaning preserved: in camera-change lifetimes, the "
        "partner convention's final answer is right with P>0.95. Owner questions: executive questions the owner "
        "answered per lifetime.",
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


# A separate process that repeatedly learns a graph and writes a checkpoint of the same instance file until the
# stop file appears (or 300 s pass). It signals readiness after its setup write, so the loaded loop starts only
# once consolidation is running. Each operation is logged as [name, wall start, wall end, ok].
_CONSOLIDATE = """
import json, pathlib, sqlite3, sys, time
from neuroscifi.instance import checkpoints, datasets, graphs, store
from neuroscifi.instance.errors import WritesPaused
path, out_dir, ready, stop = sys.argv[1:5]
now, seed = float(sys.argv[5]), int(sys.argv[6])
db = store.open_existing(path)
ids = datasets.simulate(db, "consolidation", now, seed=seed, path=path)
pathlib.Path(ready).touch()
ops, errors = [], {"writes_paused": 0, "busy": 0}
deadline = time.monotonic() + 300
i = 0
while not pathlib.Path(stop).exists() and time.monotonic() < deadline:
    i += 1
    for name in ("learn", "checkpoint"):
        start, ok = time.time(), True
        try:
            if name == "learn":
                graphs.learn(db, "consolidation", now + 2 * i, train=ids["train"], validation=ids["validation"],
                             holder="consolidation")
            else:
                checkpoints.create(db, path, out_dir, now + 2 * i + 1)
        except WritesPaused:
            errors["writes_paused"] += 1
            ok = False
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) and "busy" not in str(error):
                raise
            errors["busy"] += 1
            ok = False
        ops.append([name, start, time.time(), ok])
db.close()
print(json.dumps({"ops": ops, "errors": errors, "cpu_seconds": time.process_time()}))
"""


def _host():
    """Platform facts and the GPU name from nvidia-smi (None when unavailable), plus a label naming the host."""
    info = host_info()
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                              text=True, timeout=20)
        gpu = (proc.stdout.strip() or None) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        gpu = None
    info["gpu"] = gpu
    if info["machine"] == "aarch64" and gpu and "GB10" in gpu:
        label = "host = this DGX Spark (aarch64, GB10)"
    else:
        label = f"host-local measurement on {info['machine']} (GPU: {gpu or 'none'}), not a DGX Spark"
    return label, info


def _busy(error):
    return "locked" in str(error) or "busy" in str(error)


def _timed_loop(db, path, stream, epoch):
    """observe then decide(record=True) per event, timed. A WritesPaused or busy error is a failed observation."""
    latencies, decisions, failures, final = [], {}, {"writes_paused": 0, "busy": 0}, None
    window = [time.time(), None]
    for i, obs in enumerate(stream):
        now = T0 + 1 + i
        start = time.perf_counter()
        try:
            evidence.observe(db, {**obs, "id": f"dev-{i}", "epoch": epoch}, now, path=path)
            decision = executive.decide(db, epoch, now, record=True, path=path)
        except WritesPaused:
            failures["writes_paused"] += 1
            continue
        except sqlite3.OperationalError as error:
            if not _busy(error):
                raise
            failures["busy"] += 1
            continue
        latencies.append(time.perf_counter() - start)
        decisions[decision["decision"]] = decisions.get(decision["decision"], 0) + 1
        final = decision["decision"]
    window[1] = time.time()
    return {"latencies": latencies, "decisions": decisions, "failures": failures, "final": final,
            "window": window}


def _loaded_loop(directory, stream, seed):
    """The same loop on a fresh instance while a separate process learns graphs and writes checkpoints of it."""
    path = directory / "loaded.sqlite"
    db = store.create(path, T0, note="acceptance device ownership under consolidation")
    ready, stop = directory / "consolidation.ready", directory / "consolidation.stop"
    try:
        evidence.start_epoch(db, "bench", T0)
        child = subprocess.Popen([sys.executable, "-c", _CONSOLIDATE, str(path), str(directory / "loaded-checkpoints"),
                                  str(ready), str(stop), str(T0 + 10_000), str(seed + 505)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())
        try:
            deadline = time.monotonic() + 120
            while not ready.exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"consolidation process did not start: {child.stderr.read()[-2000:]}")
                time.sleep(.01)
            loop = _timed_loop(db, path, stream, "bench")
        finally:
            stop.touch()
            out, err = child.communicate(timeout=600)
        if child.returncode != 0:
            raise RuntimeError(f"consolidation process failed ({child.returncode}): {err.strip()[-2000:]}")
    finally:
        db.close()
    background = json.loads(out)
    start, end = loop["window"]
    overlapping = [op for op in background["ops"] if op[1] < end and op[2] > start]
    loop["background"] = {
        "operations": {name: sum(op[0] == name and op[3] for op in background["ops"]) for name in ("learn", "checkpoint")},
        "operations_overlapping_loop": len(overlapping),
        "errors": background["errors"], "cpu_seconds": background["cpu_seconds"],
        "operation_ms": {name: percentiles([op[2] - op[1] for op in background["ops"] if op[0] == name])
                              for name in ("learn", "checkpoint")},
    }
    return loop


def _without_state_version(state):
    """Beliefs minus state_version. checkpoints.create and verify legitimately bump the live instance's
    state_version (the archive registry is instance state), so a restored copy and the live file differ in that
    counter only; every other key must be equal."""
    return {key: value for key, value in state.items() if key != "state_version"}


def device_ownership(seed=DEFAULT_SEED, events=DEFAULT_EVENTS):
    """ACC-06 / INS-02 / INS-17 / OPS-06: loop latency idle and under consolidation, restart, checkpoint, pressure."""
    truth, stream = teaching_stream(np.random.default_rng([seed, 7]), events)
    label, host = _host()
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        directory = Path(directory)
        path = directory / "device.sqlite"
        db = store.create(path, T0, note="acceptance device ownership")
        try:
            evidence.start_epoch(db, "bench", T0)
            idle = _timed_loop(db, path, stream, "bench")
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

        loaded = _loaded_loop(directory, stream, seed)
        pressure = _storage_pressure(directory / "pressured.sqlite", seed)

    mech = before["beliefs"]["mechanism"]["p_inverted"]
    failed = {"idle": events - len(idle["latencies"]), "under_consolidation": events - len(loaded["latencies"])}
    checks = {
        "loop_reaches_act": idle["final"] == "act",
        "beliefs_correct_after_loop": int(mech > .5) == truth["mechanism"] and before["beliefs"]["mechanism"]["identifiable"],
        "restart_new_process_identical_beliefs": after["beliefs"] == before["beliefs"],
        "restart_new_process_identical_pending": after["pending"] == before["pending"],
        "restart_new_process_identical_inventory": after["inventory"] == before["inventory"],
        "checkpoint_verified": verified["ok"],
        "checkpoint_restore_identical_beliefs_except_state_version":
            _without_state_version(restored_beliefs) == _without_state_version(live_beliefs),
        "zero_failed_observations_idle_and_under_consolidation": failed["idle"] == 0
        and failed["under_consolidation"] == 0,
        "consolidation_ran_during_loaded_loop": loaded["background"]["operations_overlapping_loop"] > 0,
        **{f"storage_pressure_{k}": v for k, v in pressure["checks"].items()},
    }
    metrics = {
        "loop": {"events": events, "label": label, "host": host, "latency_ms": percentiles(idle["latencies"]),
                 "decision_counts": idle["decisions"], "final_decision": idle["final"],
                 "failures": idle["failures"]},
        "loop_under_consolidation": {
            "events": events, "label": label, "latency_ms": percentiles(loaded["latencies"]),
            "decision_counts": loaded["decisions"], "final_decision": loaded["final"],
            "failed_observations": failed["under_consolidation"], "failures": loaded["failures"],
            "loop_seconds": loaded["window"][1] - loaded["window"][0], "background": loaded["background"]},
        "restart": {"state_version": before["inventory"]["state_version"],
                    "evidence": before["inventory"]["memory"]["evidence"], "pending_rows": len(before["pending"])},
        "checkpoint": {"policy": checkpoint["policy"], "bytes": checkpoint["bytes"],
                       "tombstones_applied": restored["tombstones_applied"],
                       "state_version": {"live": live_beliefs["state_version"],
                                         "restored": restored_beliefs["state_version"]}},
        "storage_pressure": pressure["metrics"],
    }
    notes = [
        f"Capture-to-action loop: {events} simulated events (camera, word, healthy reference in turn, 10% noise), "
        "each timed from evidence.observe through executive.decide(record=True). Latency is reported, not judged "
        f"(no device latency budget is declared); {label}.",
        "Simultaneous inference and consolidation (brief p.19): the same stream is run on a fresh instance while a "
        "separate OS process repeatedly runs graphs.learn and checkpoints.create on that same file (SQLite WAL, "
        "busy timeout 10 s). A WritesPaused or SQLite busy/locked error in the loop counts as a failed observation; "
        "the pass requires none in either loop. The background process's own operation counts, errors and CPU "
        "seconds are reported; at least one of its operations must overlap the loaded loop for the measurement to "
        "count.",
        "Restart: the connection is closed and a NEW process opens the file and returns beliefs, pending questions "
        "and the inventory, compared with the values read before closing.",
        "Checkpoint restore: beliefs of the restored copy equal the live file's in every key except state_version, "
        "which checkpoint create/verify legitimately advance on the live instance.",
        "Not representable in simulation: physical capture devices and reversible switchover of device ownership.",
    ]
    return _result("device_ownership", checks, metrics, {"latency_budget": None, "failed_observations_max": 0},
                   notes)


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


# ---- 7. first physical test (simulated) ---------------------------------------------------------------

FIRST_TEST_LIFETIMES = 100
PANEL, RETURN = "panel-A", "return"
FIRST_TEST_DESIGN = {
    "teach_steps": 24,
    "remounted_steps": 16,
    "return_steps": 4,
    "panel_events_per_step": "camera + partner word every step; one reference reading every 4th step",
    "return_events_per_step": "camera + partner word (original camera mapping), no reference",
    "reading_noise": 0.10,
    "reference_readings_per_root": 2,
    "reference_root_flip_probability": 0.15,
    "remount": "the camera alignment bit flips silently from teach step 24 on (owner not told)",
    "final_answers_at": "end of panel-A, before the restart (camera truth = remounted alignment)",
    "episodic_k": 5,
    "confidence": CONFIDENT,
}
# Declared before the scenario was first run.
FIRST_TEST_THRESHOLDS = {
    "attribution_min": 0.80,
    "earlier_setup_recognition_min": 0.80,
    "retention_min": 0.90,
    "confident_wrong_final_answers": "strictly fewer than fixed wiring",
}
# The conventional recurrent learner with replay (baseline iii), fixed before running.
RECURRENT_DESIGN = {
    "model": "logistic regression P(camera bit = 1) = sigmoid(w . x)",
    "features": "A = 2*action - 1; r_c, r_w, r_r = last camera / word / reference relation (bit XOR action) as "
                "+-1, 0 before the first; x = [1, A, A*r_c, A*r_w, A*r_r]",
    "learning_rate": 0.2,
    "replay_capacity": 256,
    "replay_batch": 4,
    "replay": "after each online step, 4 past (x, y) pairs drawn uniformly from a FIFO buffer get one SGD step each",
}


def _first_test_lifetime(seed, index):
    """Hidden bits, panel-A events (teach, then a silent camera remount) and the return events (original mapping)."""
    design = FIRST_TEST_DESIGN
    rng = np.random.default_rng([seed, 9, index])
    truth = {factor: int(rng.integers(2)) for factor in FACTORS}
    teach, steps = design["teach_steps"], design["teach_steps"] + design["remounted_steps"]
    every, per_root = 4, design["reference_readings_per_root"]
    flips = (rng.random(steps // (every * per_root)) < design["reference_root_flip_probability"]).astype(int)
    noise = design["reading_noise"]

    def event(channel, root, relation, action):
        reading = relation ^ int(rng.random() < noise)
        return {"channel": channel, "root": root, "action": action, "bit": reading ^ action}

    events, onset = [], None
    for step in range(steps):
        camera = truth["camera"] ^ int(step >= teach)
        action = int(rng.integers(2))
        if step >= teach and onset is None:
            onset = len(events)
        events.append(event("camera", CAMERA_ROOT, truth["mechanism"] ^ camera, action))
        events.append(event("word", PARTNER_ROOT, truth["mechanism"] ^ truth["partner"], action))
        if step % every == every - 1:
            k = step // (every * per_root)
            events.append(event("reference", f"reference-{k}", truth["mechanism"] ^ int(flips[k]),
                                int(rng.integers(2))))
    back = []
    for _ in range(design["return_steps"]):
        action = int(rng.integers(2))
        back.append(event("camera", CAMERA_ROOT, truth["mechanism"] ^ truth["camera"], action))
        back.append(event("word", PARTNER_ROOT, truth["mechanism"] ^ truth["partner"], action))
    return {"truth": truth, "remounted": truth["camera"] ^ 1, "events": events, "onset": onset, "return": back,
            "references": sum(e["channel"] == "reference" for e in events)}


def _camera_bit_p(state, action):
    """P(next camera bit = 1 | action) from beliefs: P(relation = 1) = p_w (1 - p_s) + (1 - p_w) p_s.

    The marginals are combined as if independent (beliefs.compute exposes marginals only); at the joint flip
    symmetry (no reference yet) this gives 0.5.
    """
    p_w = state["mechanism"]["p_inverted"]
    p_s = state["sensors"].get(CAMERA_ROOT, {}).get("p_inverted", .5)
    relation = p_w * (1 - p_s) + (1 - p_w) * p_s
    return relation if action == 0 else 1 - relation


def _owner_answer(db, epoch, decision, now, *, changed_scope=None, changed=False, setup=None):
    """The simulated owner answers the executive's open question truthfully. Returns what was asked and answered.

    "This looks like X; continue there?" -> yes only when X is the setup truly in use (`setup`). A change question
    -> confirm=True only when that scope truly changed (`changed` and `changed_scope`), else confirm=False. A
    partner-meaning question -> a plain answer (confirm=None).
    """
    row = next(p for p in executive.pending(db, epoch=epoch) if p["id"] == decision["pending_id"])
    scope = row["scope"]
    if scope.startswith("epoch:"):
        kind, confirm = "earlier_setup", scope == f"epoch:{setup}"
        text = "yes, this is that setup" if confirm else "no, this is a different setup"
    elif row["question"].startswith("What does"):
        kind, confirm, text = "meaning", None, "the partner's words mean what they meant before"
    else:
        kind, confirm = "change", bool(changed and scope == changed_scope)
        text = "yes, it changed" if confirm else "no, nothing changed"
    executive.resolve(db, row["id"], text, now, confirm=confirm)
    return {"kind": kind, "scope": scope, "confirm": confirm}


def _first_test_panel(path, life):
    """Product, before the restart: teach in panel-A, the silent remount, executive questions answered."""
    start = time.process_time()
    db = store.create(path, T0, note="acceptance first physical test (simulated)")
    camera_scope = f"camera:{CAMERA_ROOT}"
    questions, predictions = [], []
    try:
        evidence.start_epoch(db, PANEL, T0)
        for j, e in enumerate(life["events"]):
            now = T0 + 1 + j
            if e["channel"] == "camera":
                predictions.append([_camera_bit_p(beliefs.compute(db, PANEL), e["action"]), e["bit"], j])
            evidence.observe(db, _obs(f"A-{j}", PANEL, e["channel"], e["root"], e["action"], e["bit"]), now)
            decision = executive.decide(db, PANEL, now, record=True)
            if decision["decision"] == "ask" and decision["pending_id"] is not None:
                answer = _owner_answer(db, PANEL, decision, now, changed_scope=camera_scope,
                                       changed=j >= life["onset"])
                questions.append({"event": j, **answer})
        final = _plain(beliefs.compute(db, PANEL))
    finally:
        db.close()
    return {"final": final, "questions": questions, "predictions": predictions,
            "cpu_seconds": time.process_time() - start}


def _first_test_return(jobs):
    """Product, after the restart (runs in a NEW process): reopen each file, check panel-A survived, then return
    to the original setup in a new epoch "return". Also reads panel-A's beliefs and its last revision summary."""
    from ..instance import memory
    out = []
    for job in jobs:
        start = time.process_time()
        db = store.open_existing(job["path"])
        try:
            intact = _plain(beliefs.compute(db, PANEL)) == job["panel"]
            base = T0 + 100_000
            evidence.start_epoch(db, RETURN, base)
            questions, predictions = [], []
            for j, e in enumerate(job["events"]):
                now = base + 1 + j
                if e["channel"] == "camera":
                    predictions.append([_camera_bit_p(beliefs.compute(db, RETURN), e["action"]), e["bit"], j])
                evidence.observe(db, _obs(f"R-{j}", RETURN, e["channel"], e["root"], e["action"], e["bit"]), now)
                decision = executive.decide(db, RETURN, now, record=True)
                if decision["decision"] == "ask" and decision["pending_id"] is not None:
                    questions.append({"event": j, **_owner_answer(db, RETURN, decision, now, setup=PANEL)})
            match = beliefs.match_epochs(db, RETURN)
            after = _plain(beliefs.compute(db, PANEL))
            items = memory.retrieve(db, "belief", epoch=PANEL, limit=1)["items"]
            summary = items[0]["payload"]["summary"] if items else None
        finally:
            db.close()
        out.append({"intact": intact, "questions": questions, "predictions": predictions, "match_best": match["best"],
                    "match_ranking": match["ranking"], "panel_after": after, "summary": summary,
                    "cpu_seconds": time.process_time() - start})
    return out


_RETURN_AFTER_RESTART = """
import json, sys
from neuroscifi.acceptance import scenarios
with open(sys.argv[1]) as handle:
    jobs = json.load(handle)
print(json.dumps(scenarios._first_test_return(jobs)))
"""


def _fixed_wiring(db, life, index):
    """Baseline (i), the prototype: one pooled epoch for panel-A and the return, no detection, no questions."""
    start = time.process_time()
    epoch = f"L{index}"
    evidence.start_epoch(db, epoch, T0)
    predictions, final = [], None
    stream = [(e, "A") for e in life["events"]] + [(e, "R") for e in life["return"]]
    for j, (e, phase) in enumerate(stream):
        if phase == "R" and final is None:
            final = beliefs.compute(db, epoch)
        if e["channel"] == "camera":
            predictions.append([_camera_bit_p(beliefs.compute(db, epoch), e["action"]), e["bit"], phase])
        evidence.observe(db, _obs(f"L{index}-{j}", epoch, e["channel"], e["root"], e["action"], e["bit"]), T0 + 1 + j)
    return {"answers": _answers(final), "predictions": predictions, "cpu_seconds": time.process_time() - start}


def _vote(relations):
    """Fraction of 1s among the last k relations of one root (0.5 with none)."""
    recent = relations[-FIRST_TEST_DESIGN["episodic_k"]:]
    return sum(recent) / len(recent) if recent else .5


def _episodic(life):
    """Baseline (ii), direct episodic lookup: per root, the majority of its last k=5 readings (bit XOR action).

    Mechanism = majority of the last 5 reference readings (pooled over roots); camera and partner = the majority
    relation of that root combined with the mechanism answer. P is the vote fraction, combined like _camera_bit_p.
    """
    start = time.process_time()
    memory = {"camera": [], "word": [], "reference": []}
    predictions, final = [], None

    def answers():
        p_w = _vote(memory["reference"])
        combine = (lambda p_rel: p_w * (1 - p_rel) + (1 - p_w) * p_rel)
        return {"mechanism": p_w, "camera": combine(_vote(memory["camera"])), "partner": combine(_vote(memory["word"]))}

    for phase, events in (("A", life["events"]), ("R", life["return"])):
        if phase == "R":
            final = answers()
        for e in events:
            if e["channel"] == "camera":
                p_rel = _vote(memory["camera"])
                predictions.append([p_rel if e["action"] == 0 else 1 - p_rel, e["bit"], phase])
            memory[e["channel"]].append(e["bit"] ^ e["action"])
    return {"answers": final, "predictions": predictions, "cpu_seconds": time.process_time() - start}


class _RecurrentReplay:
    """Baseline (iii): a conventional recurrent learner with replay, kept tiny (see RECURRENT_DESIGN).

    Its recurrent state is the last relation seen on each channel; a logistic regression maps (action, state) to
    P(next camera bit = 1). It learns online (one SGD step on the log loss per camera event, after predicting it)
    and replays past events from a FIFO buffer. It predicts readings; it has no notion of factors or setups.
    """

    def __init__(self, rng):
        self.rng = rng
        self.w = np.zeros(5)
        self.buffer = []
        self.last = {"camera": 0.0, "word": 0.0, "reference": 0.0}

    def _x(self, action):
        a = 2.0 * action - 1.0
        return np.array([1.0, a, a * self.last["camera"], a * self.last["word"], a * self.last["reference"]])

    def _sgd(self, x, y):
        p = 1.0 / (1.0 + np.exp(-float(self.w @ x)))
        self.w += RECURRENT_DESIGN["learning_rate"] * (y - p) * x

    def step(self, e):
        """Returns P(bit = 1) predicted before a camera event (None for other channels), then learns from it."""
        p = None
        if e["channel"] == "camera":
            x = self._x(e["action"])
            p = float(1.0 / (1.0 + np.exp(-float(self.w @ x))))
            self._sgd(x, e["bit"])
            if self.buffer:
                for i in self.rng.integers(len(self.buffer), size=RECURRENT_DESIGN["replay_batch"]):
                    self._sgd(*self.buffer[int(i)])
            self.buffer.append((x, e["bit"]))
            del self.buffer[:-RECURRENT_DESIGN["replay_capacity"]]
        self.last[e["channel"]] = 2.0 * (e["bit"] ^ e["action"]) - 1.0
        return p


def _recurrent(life, seed, index):
    start = time.process_time()
    model = _RecurrentReplay(np.random.default_rng([seed, 10, index]))
    predictions = []
    for phase, events in (("A", life["events"]), ("R", life["return"])):
        for e in events:
            p = model.step(e)
            if p is not None:
                predictions.append([p, e["bit"], phase])
    return {"predictions": predictions, "cpu_seconds": time.process_time() - start}


def _prediction_scores(pairs):
    """Accuracy (ties count 0.5) and Brier of P(bit = 1) predictions."""
    return {"n": len(pairs), "accuracy": _mean(_correct(p, bit) for p, bit in pairs),
            "brier": _mean(_brier(p, bit) for p, bit in pairs)}


def _segment_recognition(panel, events):
    """Diagnostic, not a check: would the return be recognised if panel-A's EARLIER camera segment were scored the
    way match_epochs scores an epoch's current beliefs (reading error .10, unseen or tied -> .5, beat unknown by
    log(19))? Returns None when panel-A has no confirmed camera segment."""
    rows = panel["segments"].get(f"camera:{CAMERA_ROOT}")
    if not rows:
        return None

    def bit(p):
        return None if abs(p - .5) < 1e-9 else int(p > .5)

    mechanism = bit(panel["mechanism"]["p_inverted"])
    partner = bit(panel["partners"].get(PARTNER_ROOT, {}).get("p_inverted", .5))
    camera = bit(rows[0]["p_inverted"])
    ll = 0.0
    for e in events:
        factor = camera if e["channel"] == "camera" else partner
        predicted = None if mechanism is None or factor is None else mechanism ^ factor
        ll += np.log(.5) if predicted is None else np.log(.9 if (e["bit"] ^ e["action"]) == predicted else .1)
    return bool(ll - len(events) * np.log(.5) >= beliefs.CHANGE_THRESHOLD)


def first_physical_test(seed=DEFAULT_SEED, lifetimes=FIRST_TEST_LIFETIMES):
    """Brief p.34, simulated: teach one action and one word, switch one sensor mapping, return after a restart."""
    if isinstance(lifetimes, bool) or not isinstance(lifetimes, int) or lifetimes < 1:
        raise ValueError("lifetimes must be a positive integer")
    camera_scope = f"camera:{CAMERA_ROOT}"
    lives = [_first_test_lifetime(seed, index) for index in range(lifetimes)]
    with tempfile.TemporaryDirectory(prefix="nsf-acceptance-") as directory:
        directory = Path(directory)
        panels = [_first_test_panel(directory / f"life-{index}.sqlite", life) for index, life in enumerate(lives)]
        jobs = [{"path": str(directory / f"life-{index}.sqlite"), "events": life["return"], "panel": panel["final"]}
                for index, (life, panel) in enumerate(zip(lives, panels))]
        (directory / "jobs.json").write_text(json.dumps(jobs))
        returns = _subprocess_json(_RETURN_AFTER_RESTART, directory / "jobs.json")
        fixed_db = store.create(directory / "fixed.sqlite", T0, note="acceptance first physical test: fixed wiring")
        try:
            fixed = [_fixed_wiring(fixed_db, life, index) for index, life in enumerate(lives)]
        finally:
            fixed_db.close()
    episodic = [_episodic(life) for life in lives]
    recurrent = [_recurrent(life, seed, index) for index, life in enumerate(lives)]

    def at_end(life):
        """Truth at the end of panel-A: the camera is remounted."""
        return dict(life["truth"], camera=life["remounted"])

    # Attribution: the first change question at or after the remount names the camera.
    attribution = []
    for life, panel in zip(lives, panels):
        changes = [q for q in panel["questions"] if q["kind"] == "change" and q["event"] >= life["onset"]]
        attribution.append(bool(changes) and changes[0]["scope"] == camera_scope)
    recognized = [any(q["kind"] == "earlier_setup" and q["scope"] == f"epoch:{PANEL}" for q in r["questions"])
                  for r in returns]

    def map_answers(state):
        return _answers(state) if state else {f: .5 for f in FACTORS}

    product_final = [_answers(panel["final"]) for panel in panels]
    retained = [_correct(_answers(r["panel_after"])[f], life["truth"][f]) for life, r in zip(lives, returns)
                for f in ("mechanism", "partner")]
    camera_segments = []
    for life, r in zip(lives, returns):
        rows = r["panel_after"]["segments"].get(camera_scope)
        camera_segments.append({"original": _correct(rows[0]["p_inverted"], life["truth"]["camera"]) if rows else None,
                                "current": _correct(r["panel_after"]["sensors"][CAMERA_ROOT]["p_inverted"],
                                                    life["remounted"])})
    retrieval = [_correct(map_answers(r["summary"])[f], at_end(life)[f]) for life, r in zip(lives, returns)
                 for f in FACTORS]

    def final_scores(answer_sets):
        pairs = [(answers[f], at_end(life)[f]) for life, answers in zip(lives, answer_sets) for f in FACTORS]
        return {"answers": len(pairs), "confident_wrong": int(sum(_confident_wrong(p, t) for p, t in pairs)),
                "accuracy": _mean(_correct(p, t) for p, t in pairs), "coverage": _mean(_confident(p) for p, _ in pairs),
                "brier": _mean(_brier(p, t) for p, t in pairs)}

    finals = {"product": final_scores(product_final), "fixed_wiring": final_scores([f["answers"] for f in fixed]),
              "episodic_lookup": final_scores([e["answers"] for e in episodic]), "recurrent_replay": None}
    product_pred = [[p, bit, "A"] for panel in panels for p, bit, _ in panel["predictions"]] + \
                   [[p, bit, "R"] for r in returns for p, bit, _ in r["predictions"]]
    learners = {"product": product_pred,
                "fixed_wiring": [x for f in fixed for x in f["predictions"]],
                "episodic_lookup": [x for e in episodic for x in e["predictions"]],
                "recurrent_replay": [x for r in recurrent for x in r["predictions"]]}
    prediction = {name: {"panel_a": _prediction_scores([(p, b) for p, b, phase in rows if phase == "A"]),
                         "return_to_earlier_setup": _prediction_scores([(p, b) for p, b, phase in rows if phase == "R"])}
                  for name, rows in learners.items()}
    panel_answers = [sum(1 for _ in panel["questions"]) for panel in panels]
    return_answers = [len(r["questions"]) for r in returns]
    owner = [a + b for a, b in zip(panel_answers, return_answers)]
    cpu = {"product": [p["cpu_seconds"] + r["cpu_seconds"] for p, r in zip(panels, returns)],
           "fixed_wiring": [f["cpu_seconds"] for f in fixed], "episodic_lookup": [e["cpu_seconds"] for e in episodic],
           "recurrent_replay": [r["cpu_seconds"] for r in recurrent]}
    question_kinds = {}
    for q in [q for panel in panels for q in panel["questions"]] + [q for r in returns for q in r["questions"]]:
        key = f"{q['kind']}:{'yes' if q['confirm'] else 'no' if q['confirm'] is False else 'answered'}"
        question_kinds[key] = question_kinds.get(key, 0) + 1
    segment_diag = [_segment_recognition(panel["final"], life["return"]) for life, panel in zip(lives, panels)]
    with_segment = [d for d in segment_diag if d is not None]
    rates = {"attribution": _mean(attribution), "recognition": _mean(recognized), "retention": _mean(retained)}
    t = FIRST_TEST_THRESHOLDS
    checks = {
        "attribution_at_least_80_percent": rates["attribution"] >= t["attribution_min"],
        "earlier_setup_recognised_at_least_80_percent": rates["recognition"] >= t["earlier_setup_recognition_min"],
        "older_skill_retention_at_least_90_percent": rates["retention"] >= t["retention_min"],
        "fewer_confident_wrong_final_answers_than_fixed_wiring":
            finals["product"]["confident_wrong"] < finals["fixed_wiring"]["confident_wrong"],
    }
    metrics = {
        "lifetimes": lifetimes,
        "attribution_rate": rates["attribution"],
        "earlier_setup_recognition_rate": rates["recognition"],
        "older_skill_retention": rates["retention"],
        "retention_detail": {
            "mechanism_and_partner_correct_after_return": rates["retention"],
            "camera_original_segment_correct": _mean(c["original"] for c in camera_segments
                                                     if c["original"] is not None),
            "camera_current_segment_correct": _mean(c["current"] for c in camera_segments),
            "lifetimes_with_confirmed_camera_segment": sum(c["original"] is not None for c in camera_segments)},
        "restart_panel_beliefs_identical": _mean(r["intact"] for r in returns),
        "retrieval_archive_lookup_disabled": {
            "product_last_revision_summary_accuracy": _mean(retrieval),
            "episodic_lookup": "no answer without its archive (every answer is a coin flip, accuracy 0.5)",
            "fixed_wiring": "beliefs are recomputed from evidence on read; none without it",
            "recurrent_replay": "predicts readings only; no factor answers to retrieve"},
        "return_match": {"best_counts": {str(k): sum(r["match_best"] == k for r in returns)
                                         for k in sorted({r["match_best"] for r in returns}, key=str)},
                         "diagnostic_recognition_if_earlier_segment_were_scored":
                             _mean(with_segment) if with_segment else None,
                         "diagnostic_lifetimes": len(with_segment)},
        "final_answers_end_of_panel_a": finals,
        "next_camera_reading_prediction": prediction,
        "interventions": {
            "owner_answers_per_lifetime": {"mean": _mean(owner), "max": max(owner), "total": int(sum(owner))},
            "owner_answers_before_restart_mean": _mean(panel_answers),
            "owner_answers_after_restart_mean": _mean(return_answers),
            "question_kinds": dict(sorted(question_kinds.items())),
            "reference_readings_per_lifetime": _mean(life["references"] for life in lives),
            "interventions_per_lifetime_mean": _mean(o + life["references"] for o, life in zip(owner, lives)),
            "baselines_owner_answers": 0},
        "compute_cpu_seconds_per_lifetime": {name: {"mean": _mean(v), "max": max(v)} for name, v in cpu.items()},
        "teacher_cost": {"product_owner_answers_mean": _mean(owner), "fixed_wiring": 0, "episodic_lookup": 0,
                         "recurrent_replay": 0},
    }
    notes = [
        f"Design fixed before running: {json.dumps(FIRST_TEST_DESIGN, sort_keys=True)}; recurrent baseline: "
        f"{json.dumps(RECURRENT_DESIGN, sort_keys=True)}.",
        "Each lifetime is its own instance file. Panel-A: 24 teaching steps, then the camera is remounted silently "
        "(its alignment bit flips) for 16 more steps. executive.decide(record=True) runs after every event and a "
        "truthful simulated owner answers each question: confirm=True only for a change question about the camera "
        "asked after the remount, confirm=False otherwise; 'continue there?' is answered yes only for panel-A.",
        "Restart: all files are closed and ONE new process opens each file, checks panel-A's beliefs equal those "
        "read before closing, starts epoch 'return' and feeds 4 steps of camera + word readings from the ORIGINAL "
        "camera mapping, with the executive deciding after each event. Recognised = the executive asked 'This "
        "looks like panel-A; continue there?'.",
        "Attribution: the first change question asked at or after the remount names camera:camera-1 (the owner "
        "gives no hint before that question). Retention: panel-A's mechanism (the taught action) and partner "
        "convention (the taught word) are MAP-correct after the return. Retrieval with archive lookup disabled: "
        "panel-A's latest belief-revision summary (memory.retrieve kind 'belief') scored against the end-of-panel "
        "truth, with no evidence read.",
        "Baselines on the same streams. Fixed wiring: the prototype, one pooled epoch for panel-A and the return, "
        "no detection, no questions (run in-process; its state is the same file). Direct episodic lookup: majority "
        "of the last 5 readings per channel/root. Conventional recurrent learner with replay: see RECURRENT_DESIGN; "
        "it is scored on next camera reading prediction only. Every learner predicts every camera reading before "
        "seeing it (prequential), in panel-A and in the return; product predictions come from the epoch it is in "
        "(panel-A, then 'return') and combine marginals as described in _camera_bit_p.",
        "Teacher cost = owner answers; interventions = owner answers + reference readings used. Compute cost = "
        "time.process_time per lifetime (product: panel-A plus its share of the restart process).",
        "Known structural limit: match_epochs scores an epoch by its CURRENT beliefs. After the owner confirms the "
        "remount inside panel-A, panel-A's current camera segment is the remounted one, so readings from the "
        "original mapping contradict it. return_match.diagnostic_* reports, for information only, how often the "
        "return would be recognised if panel-A's earlier camera segment were scored the same way.",
        "Simulated binary readings stand in for the brief's physical sensors; this is not a physical test.",
    ]
    return _result("first_physical_test", checks, metrics, dict(FIRST_TEST_THRESHOLDS, confidence=CONFIDENT), notes)


# ---- runner ------------------------------------------------------------------------------------------

SCENARIOS = {
    "birth": birth,
    "grounding": grounding,
    "lifelong_correction": lifelong_correction,
    "graph_development": graph_development,
    "growth_retention": growth_retention,
    "device_ownership": device_ownership,
    "first_physical_test": first_physical_test,
}


def run(names=None, *, seed=DEFAULT_SEED, lifetimes=DEFAULT_LIFETIMES, events=DEFAULT_EVENTS,
        test_lifetimes=FIRST_TEST_LIFETIMES):
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
        if name == "first_physical_test":
            kwargs["lifetimes"] = test_lifetimes
        start = time.perf_counter()
        results[name] = SCENARIOS[name](**kwargs)
        results[name]["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    return {"simulated": True, "pass": all(r["pass"] for r in results.values()), "seed": seed,
            "parameters": {"lifetimes": lifetimes, "events": events, "test_lifetimes": test_lifetimes},
            "scenarios": results,
            "label": "simulated acceptance scenarios; not a physical validation"}
