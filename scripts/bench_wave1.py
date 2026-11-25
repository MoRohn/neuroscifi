#!/usr/bin/env python3
"""Wave 1 integration evidence (Project Cognitive Genesis): the cognitive benchmark on the integrated tree.

  all_off    every Wave 1 setting read as off by every individual (Wave 0's behaviour), the baseline policy and the
             references: diffed against the recorded docs/quality/evidence/genesis_baseline.json (results only; cost,
             environment and timestamps excluded). The adversarial family's case list changed in Wave 1 (lane M
             renamed one case, lane K added four), so its diff is reported case by case.
  defaults   every setting as shipped, each family answered by the mechanism the shipped settings use: anticipation
             and info_value baseline (learning progress is off by default since the review1 rework, so the timer
             and idle paths read exactly as all_off), counterfactual imagination (what-if and
             forecast on), self_model self_description, selective monitoring (the record is on), retention and
             adversarial baseline (the code's defaults).
  all_on     every Wave 1 setting that ships off switched on as well (WAVE1_ON), and the lanes' behaviour-changing
             pieces on in the process (instinct.switched_on: predictive regulation, information-v1, learning
             progress): anticipation instinct, info_value wave1_all_on (information-v1 core; web: the knowledge
             re-check's pick first, else curiosity with learning progress), counterfactual imagination, self_model
             self_description, selective monitoring, retention and adversarial instinct (under the same settings).
             Not switched on: strategy selection stays behind its gate (no promotion passed it), and
             reflection_model_summary (it needs the answer model, which this benchmark never calls).
  ablations  each registered component of a lane's mechanism switched off, one run each, the other settings as
             shipped.

  idle_path  the idle investigator's curiosity path (info_value "autonomy") in each regime, always reported apart
             from the timer's path (the info_value rows), never mixed into one number (Wave 1 review B2).
  wide       SUPPLEMENTARY: 12 further seeds (cognition.WIDE_SEEDS) per paired comparison of WIDE_PLAN, each
             mechanism against its reference seed by seed; the split's own seeds stay the official numbers.
  rescore    the baseline re-run under the scoring the Wave 1 review asked for, beside the recorded numbers.

  final      THE ONE LOOK at the withheld family (main session only, once, on the final tree; refused on a dirty
             tree): the baseline re-run under today's scoring (every Wave 1 setting off; the recorded baseline's
             withheld numbers predate the corrected scoring), then the Wave 1 mechanisms (WAVE1_FINAL). Each run
             appends its line to docs/quality/evidence/genesis_final_uses.jsonl before it scores anything.

Both splits; the withheld family is run only by `final`. Usage:
  scripts/bench_wave1.py run [--split development|held_out ...] [--out FILE]
  scripts/bench_wave1.py wide [--split ...] [--out FILE]
  scripts/bench_wave1.py rescore [--split ...] [--out FILE]
  scripts/bench_wave1.py offline [--regime all_off|defaults|wave0]    (the offline research benchmark, one run)
  scripts/bench_wave1.py acceptance --regime all_off --out FILE       (`nsf acceptance --all` under a regime)
  scripts/bench_wave1.py final --note TEXT --out FILE                 (the withheld family; see above)
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get("NSF_BENCH_SRC") or str(ROOT / "src"))   # another tree's src for `wave0` runs

# Settings Wave 1 added, as every individual reads them in each regime (InstanceConfig / WebConfig).
WAVE1_OFF_INSTANCE = {"internal_state_history": False, "predictive_regulation": False, "executive_rule": "rules-v1",
                      "decision_information": False, "imagine_what_if": False, "imagine_forecast": False,
                      "imagine_retrospect": False, "imagine_scenarios": False, "broadcast": "off"}
WAVE1_OFF_WEB = {"curiosity_learning_progress": False, "salience_record": False, "hypothesis_fields": False,
                 "hypothesis_abandon": False, "premise_recheck": False, "idle_recheck": False, "reflection": False,
                 "reflection_model_summary": False, "strategy_selection": "off", "answer_monitoring": False,
                 "selective_answering": False, "verification_escalation": False, "adaptive_depth": False,
                 "pocket_recent": False}
WAVE1_ON_INSTANCE = {"internal_state_history": True, "predictive_regulation": True, "executive_rule": "information-v1",
                     "decision_information": True, "imagine_what_if": True, "imagine_forecast": True,
                     "imagine_retrospect": True, "imagine_scenarios": True, "broadcast": "on"}
WAVE1_ON_WEB = {"curiosity_learning_progress": True, "salience_record": True, "hypothesis_fields": True,
                "hypothesis_abandon": True, "premise_recheck": True, "idle_recheck": True, "reflection": True,
                "reflection_model_summary": False, "strategy_selection": "gate", "answer_monitoring": True,
                "selective_answering": True, "verification_escalation": True, "adaptive_depth": True,
                "pocket_recent": True}
FAMILIES = ("anticipation", "info_value", "counterfactual", "self_model", "selective", "retention", "adversarial")
DEFAULTS = {"anticipation": "baseline", "info_value": "baseline", "counterfactual": "imagination",
            "self_model": "self_description", "selective": "monitoring", "retention": "baseline",
            "adversarial": "baseline"}
ALL_ON = {"anticipation": "instinct", "info_value": "wave1_all_on", "counterfactual": "imagination",
          "self_model": "self_description", "selective": "monitoring", "retention": "instinct",
          "adversarial": "instinct"}
# (family, mechanism): the lane that registered it
LANE_MECHANISMS = {("anticipation", "instinct"): "I", ("info_value", "instinct"): "I",
                   ("info_value", "instinct-idle"): "I", ("retention", "instinct"): "I",
                   ("adversarial", "instinct"): "I", ("counterfactual", "imagination"): "M",
                   ("info_value", "knowledge_recheck"): "K", ("retention", "knowledge_evolution"): "K",
                   ("adversarial", "knowledge_evolution"): "K", ("selective", "monitoring"): "C",
                   ("self_model", "self_description"): "W"}
SKIP = {"cost", "environment", "generated_at", "wall_seconds", "seconds", "elapsed_seconds", "build_ms"}
# what a retention run compares with the recorded baseline (absent from the recorded baseline's own run)
DERIVED = {"baseline", "retained", "all_retained"}


@contextmanager
def regime(instance: dict, web: dict):
    """Every individual reads these settings, whatever it stored (the dataclasses' from_json, patched)."""
    from neuroscifi.instance import store
    from neuroscifi.webcortex import config as wconfig
    real_i, real_w = store.InstanceConfig.from_json.__func__, wconfig.WebConfig.from_json.__func__

    def instance_json(cls, text):
        return dataclasses.replace(real_i(cls, text), **instance)

    def web_json(cls, text):
        cfg = real_w(cls, text)
        for name, value in web.items():
            setattr(cfg, name, value)
        return cfg
    with mock.patch.object(store.InstanceConfig, "from_json", classmethod(instance_json)), \
            mock.patch.object(wconfig.WebConfig, "from_json", classmethod(web_json)):
        yield


REGIMES = {"all_off": lambda: regime(WAVE1_OFF_INSTANCE, WAVE1_OFF_WEB), "defaults": nullcontext,
           "all_on": lambda: regime(WAVE1_ON_INSTANCE, WAVE1_ON_WEB)}


def _register_all_on():
    from neuroscifi.acceptance import cognition
    from neuroscifi.acceptance.cognition import instinct, knowledge

    class AllOnChooser:
        """info_value with every Wave 1 chooser on: information-v1 in the core world; in the web world the staged
        individual's learning progress on (lane I) and the knowledge re-check's pick first (lane K), else curiosity."""

        def __init__(self):
            self.instinct = instinct.InstinctChooser()
            self.recheck = knowledge.RecheckChooser()

        def choose(self, view):
            if view["world"] != "web":
                return self.instinct.choose(view)
            self.instinct._stage(view)
            return self.recheck.choose(view)

        def activate(self):
            return instinct.switched_on()

    cognition.register("info_value", "wave1_all_on", lambda ablate: AllOnChooser,
                       description="integration: lane I's information-v1 and learning progress with lane K's re-check")


def strip(tree):
    if isinstance(tree, dict):
        return {k: strip(v) for k, v in tree.items() if k not in SKIP | DERIVED}
    if isinstance(tree, list):
        return [strip(v) for v in tree]
    return tree


def diff(a, b, path="", out=None, limit=200, added=None):
    """Paths where two result trees differ (cost and timing keys excluded). A key only `b` has is a metric added
    since `a` was written (the Wave 1 review's new scores): listed in `added` when given, never as a difference."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k in SKIP | DERIVED:
                continue
            if k not in a and added is not None:
                added.append(f"{path}.{k}".lstrip("."))
            elif k not in a or k not in b:
                out.append({"path": f"{path}.{k}".lstrip("."), "before": a.get(k, "<absent>") if k in a else
                            "<absent>", "after": b.get(k) if k in b else "<absent>"})
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit, added)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit, added)
    elif a != b:
        out.append({"path": path.lstrip("."), "before": a, "after": b})
    return out


# The counterfactual family's metrics renamed by the Wave 1 review (B3), read under the new names in a result written
# before it; "unanswerable" then counted the front-door questions too.
LEGACY_NAMES = {"coverage": "answered_share", "abstention_correct": "declined_confounded_share",
                "scm:not_identifiable": "scm:confounded"}


def legacy(family, before, after):
    """(before, after) made comparable on the metrics `before` has: the recorded tree's renamed keys under their new
    names, and the new tree's "unanswerable" counted as before (with the front-door questions)."""
    if family != "counterfactual":
        return before, after

    def rename(tree):
        if isinstance(tree, dict):
            return {LEGACY_NAMES.get(k, k): rename(v) for k, v in tree.items()}
        return [rename(v) for v in tree] if isinstance(tree, list) else tree

    def fold(tree):
        if isinstance(tree, dict):
            out = {k: fold(v) for k, v in tree.items()}
            if isinstance(out.get("unanswerable"), int) and isinstance(out.get("front_door"), int):
                out["unanswerable"] += out["front_door"]
            return out
        return [fold(v) for v in tree] if isinstance(tree, list) else tree
    return rename(before), fold(after)


def one(family, split, mechanism, ablate=(), regime_name="defaults"):
    from neuroscifi.acceptance import cognition
    started = time.perf_counter()
    recorded = json.loads(cognition.BASELINE.read_text())     # retention compares with it, as the CLI's default does
    with REGIMES[regime_name]():
        out = cognition.run([family], split=split, mechanism=mechanism, ablate=ablate, baseline_output=recorded)
    fam = out["families"][family]
    return {"family": family, "split": split, "mechanism": mechanism, "ablate": sorted(ablate),
            "regime": regime_name, "used": fam["mechanism"], "result": fam["result"], "cost": fam["cost"],
            "run_seconds": round(time.perf_counter() - started, 2)}


def headline(family, result):
    from neuroscifi.acceptance import cognition
    return {dotted: cognition.headline_value(result, dotted) for dotted, _ in cognition.HEADLINES[family]}


def adversarial_cases(result):
    return {c["id"]: c["pass"] for c in result.get("cases") or []}


def run(splits, *, ablations=True, references=True) -> dict:
    from neuroscifi.acceptance import cognition
    cognition.load_plugins()
    _register_all_on()
    recorded = json.loads((ROOT / "docs/quality/evidence/genesis_baseline.json").read_text())
    out = {"benchmark": "cognition, Wave 1 integration", "environment": cognition.environment(),
           "settings": {"all_off": {"instance": WAVE1_OFF_INSTANCE, "web": WAVE1_OFF_WEB},
                        "all_on": {"instance": WAVE1_ON_INSTANCE, "web": WAVE1_ON_WEB},
                        "defaults_mechanisms": DEFAULTS, "all_on_mechanisms": ALL_ON},
           "splits": {}}
    for split in splits:
        rec = recorded[split]
        section = {"baseline_reproduced": {}, "defaults": {}, "all_on": {}, "ablations": {}, "references": {}}
        for family in FAMILIES:
            base = one(family, split, "baseline", regime_name="all_off")
            before = rec["families"][family]["result"]
            added: list = []
            if family == "adversarial":         # the case list itself changed: compared case by case below
                d = diff({k: v for k, v in strip(before).items() if k != "cases"},
                         {k: v for k, v in strip(base["result"]).items() if k != "cases"}, added=added)
            else:
                d = diff(*legacy(family, strip(before), strip(base["result"])), added=added)
            entry = {"differences": len(d), "first": d[:20],
                     "added_metrics": sorted({a.split("[")[0] for a in added})[:40],
                     "headline": headline(family, base["result"]),
                     "recorded_headline": headline(family, before), "run_seconds": base["run_seconds"]}
            if family == "adversarial":
                entry["cases_recorded"] = adversarial_cases(before)
                entry["cases_now"] = adversarial_cases(base["result"])
            section["baseline_reproduced"][family] = entry
            for name, table in (("defaults", DEFAULTS), ("all_on", ALL_ON)):
                r = one(family, split, table[family], regime_name=name)
                section[name][family] = {"mechanism": table[family], "used": r["used"],
                                         "headline": headline(family, r["result"]), "cost": r["cost"],
                                         "run_seconds": r["run_seconds"]}
                if family == "adversarial":
                    section[name][family]["cases"] = adversarial_cases(r["result"])
                    section[name][family]["failed"] = r["result"].get("failed")
                if family == "retention":
                    section[name][family]["failed_checks"] = _retention_failures(r["result"])
        # the idle investigator's path (autonomy.candidates), always beside the timer's (curiosity.rank; the
        # info_value rows above): the two paths are never mixed in one number (Wave 1 review B2)
        section["idle_path"] = {}
        for name in ("all_off", "defaults", "all_on"):
            r = one("info_value", split, "autonomy", regime_name=name)
            section["idle_path"][name] = {"mechanism": "autonomy", "headline": headline("info_value", r["result"]),
                                          "run_seconds": r["run_seconds"]}
        if references:
            for name, refs in (("oracle", ("info_value",)), ("random", ("info_value",)),
                               ("autonomy", ("info_value",)), ("open", ("anticipation",))):
                for family in refs:
                    r = one(family, split, name, regime_name="all_off")
                    before = recorded[f"{split}_references"][name]["result"]
                    d = diff(strip(before), strip(r["result"]), added=[])
                    section["references"][f"{family}/{name}"] = {"differences": len(d), "first": d[:10]}
            # the Wave 1 review's two-line rule on the verdict word (no recorded run: added by the review)
            r = one("selective", split, "verdict_word", regime_name="all_off")
            section["references"]["selective/verdict_word"] = {"headline": headline("selective", r["result"])}
            r = one("counterfactual", split, "zero", regime_name="all_off")
            section["references"]["counterfactual/zero"] = {"headline": headline("counterfactual", r["result"])}
        if ablations:
            for mech in cognition.mechanisms():
                if (mech.family, mech.name) not in LANE_MECHANISMS or not mech.components:
                    continue
                key = f"{mech.family}/{mech.name}"
                full = one(mech.family, split, mech.name)
                rows = {"lane": LANE_MECHANISMS[(mech.family, mech.name)],
                        "full": headline(mech.family, full["result"])}
                for component in mech.components:
                    r = one(mech.family, split, mech.name, ablate=(component,))
                    rows[f"-{component}"] = headline(mech.family, r["result"])
                if mech.family == "adversarial":
                    rows["full_failed"] = full["result"].get("failed")
                if mech.family == "retention":
                    rows["full_failed_checks"] = _retention_failures(full["result"])
                if key == "selective/monitoring":
                    rows["untested_components"] = _untested_monitoring_components()
                if key == "anticipation/instinct":
                    rows["trend_share_of_avoided"] = _trend_share(section, rows)
                section["ablations"][key] = rows
            for key in (("info_value", "knowledge_recheck"), ("retention", "knowledge_evolution"),
                        ("adversarial", "knowledge_evolution")):      # lane K: no components; the mechanism alone
                r = one(key[0], split, key[1])
                section["ablations"][f"{key[0]}/{key[1]}"] = {"lane": "K", "full": headline(key[0], r["result"]),
                                                              "note": "no components: its ablation is the defaults "
                                                                      "run (mechanism off)"}
        out["splits"][split] = section
    return out


def _untested_monitoring_components():
    """The monitoring score's terms that are 0 on every item of both splits: their ablation cannot change anything,
    because the benchmark never exercises them (Wave 1 review B5)."""
    from neuroscifi.acceptance.cognition import monitoring as P
    from neuroscifi.webcortex import monitoring as M
    seen = {c: 0 for c in M.COMPONENTS}
    for split in ("development", "held_out"):
        for r in P.rows(split):
            sig = dict(r["record"].get("signals") or {}, statement=r["record"].get("statement"))
            for c, v in M.components(sig).items():
                seen[c] += v != 0
    return {"components": sorted(c for c, n in seen.items() if not n),
            "items_with_a_nonzero_term": seen,
            "why": "0 on every item of the fictional corpus (no dependent sources, no answer model): untested here"}


def _trend_share(section, rows):
    """How much of the memory breaches avoided the forecaster's trend accounts for: (full - without trend) /
    (full - baseline); the rest comes from reserving memory per background task (Wave 1 review B6)."""
    key = "summary.memory.avoided_share.mean"
    base = section["baseline_reproduced"]["anticipation"]["headline"].get(key)
    full, minus = rows["full"].get(key), rows.get("-trend", {}).get(key)
    if None in (base, full, minus) or full == base:
        return None
    return {"baseline": base, "full": full, "without_trend": minus, "share": round((full - minus) / (full - base), 4)}


# (label, family, mechanism, its regime, reference, the reference's regime): the paired comparisons of the wide-seeds
# run. The timer's and the idle investigator's curiosity paths are separate rows (Wave 1 review B2).
WIDE_PLAN = (
    ("anticipation: predictive regulation (lane I) vs the reactive gate", "anticipation", "instinct", "defaults",
     "baseline", "all_off"),
    ("info_value, timer path: as shipped (learning progress off) vs every Wave 1 setting off", "info_value",
     "baseline", "defaults", "baseline", "all_off"),
    ("info_value, idle path: as shipped vs every Wave 1 setting off", "info_value", "autonomy", "defaults",
     "autonomy", "all_off"),
    ("info_value, timer path: lane I instinct (information-v1 core, learning progress on, reworked) vs all off",
     "info_value", "instinct", "defaults", "baseline", "all_off"),
    ("info_value, idle path: lane I instinct-idle (learning progress on, reworked) vs all off", "info_value",
     "instinct-idle", "defaults", "autonomy", "all_off"),
    ("info_value: every Wave 1 chooser on (re-check first) vs all off", "info_value", "wave1_all_on", "all_on",
     "baseline", "all_off"),
    ("counterfactual: imagination (lane M) vs today's answerer", "counterfactual", "imagination", "defaults",
     "baseline", "all_off"),
    ("self_model: one self-description record (lane W) vs today's surfaces", "self_model", "self_description",
     "defaults", "baseline", "all_off"),
)


def wide(splits) -> dict:
    """The supplementary wide-seeds run (cognition.wide_seeds, seeds cognition.WIDE_SEEDS) for every row of
    WIDE_PLAN on each split. The split's own seeds stay the official numbers."""
    from neuroscifi.acceptance import cognition
    cognition.load_plugins()
    _register_all_on()
    out = {"benchmark": "cognition, Wave 1: supplementary wide-seeds run", "environment": cognition.environment(),
           "seeds": list(cognition.WIDE_SEEDS),
           "note": "supplementary: reported beside the frozen split's own 3 seeds, which stay the official numbers; "
                   "the worlds are the split's, only the seeds differ; the withheld family is never run",
           "splits": {}}
    for split in splits:
        rows = {}
        for label, family, mech, mech_regime, ref, ref_regime in WIDE_PLAN:
            started = time.perf_counter()
            rows[label] = cognition.wide_seeds(family, split, mech, reference=ref,
                                               contexts=(REGIMES[mech_regime], REGIMES[ref_regime]))
            rows[label]["regimes"] = {"mechanism": mech_regime, "reference": ref_regime}
            rows[label]["run_seconds"] = round(time.perf_counter() - started, 2)
        out["splits"][split] = rows
    return out


def rescore(splits) -> dict:
    """The BASELINE (every Wave 1 setting off) re-run under the scoring the Wave 1 review asked for, beside the
    recorded numbers of docs/quality/evidence/genesis_baseline.json (which stays as it was, untouched)."""
    from neuroscifi.acceptance import cognition
    cognition.load_plugins()
    recorded = json.loads((ROOT / "docs/quality/evidence/genesis_baseline.json").read_text())
    out = {"benchmark": "cognition baseline, re-scored (Wave 1 review)", "environment": cognition.environment(),
           "what": "the unchanged system's behaviour (every Wave 1 setting off, the baseline mechanisms) scored with "
                   "the scoring added after the Wave 1 review: counterfactual answered_within_tolerance, the interval "
                   "score, three groups and the front-door line (B3); self-description under both answer keys and "
                   "without the two plumbing probes (B4); selective with the decision scoring and the verdict-word "
                   "reference (B5); adversarial cases by origin. 'recorded' is the original baseline file's headline "
                   "under the old scoring (renamed metrics read under their old names); that file is unchanged.",
           "splits": {}}
    for split in splits:
        rows = {}
        for family in ("counterfactual", "self_model", "selective", "adversarial"):
            r = one(family, split, "baseline", regime_name="all_off")
            before = recorded[split]["families"][family]["result"]
            rows[family] = {"recorded_old_scoring": headline(family, before),
                            "rescored": headline(family, r["result"]), "result": r["result"]}
        r = one("selective", split, "verdict_word", regime_name="all_off")
        rows["selective_reference_verdict_word"] = {"rescored": headline("selective", r["result"])}
        out["splits"][split] = rows
    return out


# The mechanism each interface of the withheld family is answered by in Wave 1's one look, and whether a new individual
# has it on. Where it ships off, the shipped system behaves as the baseline run beside it.
WAVE1_FINAL = {"anticipation": ("instinct", "off (predictive_regulation)"),
               "info_value": ("instinct", "off (executive_rule information-v1 is opt-in; curiosity_learning_progress)"),
               "counterfactual": ("imagination", "on (what-if, forecast and retrospect commands)"),
               "self_model": ("self_description", "on (display)")}


def register_wave1():
    """One name, `wave1`, for the four lanes' mechanisms, so a single run of the withheld family uses all of them
    (cognition.run takes one mechanism name and falls back to the baseline where a family has none of that name)."""
    from neuroscifi.acceptance import cognition
    cognition.load_plugins()
    for family, (name, ships) in WAVE1_FINAL.items():
        mech = cognition._REGISTRY[(family, name)]
        cognition.register(family, "wave1", mech.factory, components=mech.components,
                           description=f"Wave 1: {family}/{name}; a new individual has it {ships}")


def final(note) -> dict:
    from neuroscifi.acceptance import cognition
    register_wave1()
    env = cognition.environment()
    if env["git_dirty"] or not env["git_commit"]:
        raise SystemExit("final: the tree must be clean and committed, so the ledger line names the code it measured")
    if not note:
        raise SystemExit("final: --note is required")
    families = ("anticipation", "info_value", "counterfactual", "self_model")
    out = {"benchmark": "cognition, the withheld family: Wave 1's one look", "environment": env,
           "what": "Two runs of the withheld family, each recorded in the ledger before it scored anything. (1) "
                   "'baseline': the baseline mechanisms with every Wave 1 setting off, re-run because the recorded "
                   "baseline's withheld numbers (genesis_baseline.json) were scored before the Wave 1 reviews "
                   "corrected the scoring; the behaviour is the unchanged system's. (2) 'wave1': each interface "
                   "answered by its lane's mechanism (mechanisms). Where a mechanism ships off, the shipped "
                   "system's number is the baseline's. Reported whatever it shows; nothing was tuned afterwards.",
           "mechanisms": {f: {"mechanism": n, "a_new_individual_has_it": s} for f, (n, s) in WAVE1_FINAL.items()},
           "runs": {}}
    for label, mechanism, regime_name, text in (
            ("baseline", "baseline", "all_off", "baseline re-scored under the corrected scoring; behaviour unchanged"),
            ("wave1", "wave1", "defaults", "Wave 1 mechanisms, the one look")):
        started = time.perf_counter()
        with REGIMES[regime_name]():
            run_ = cognition.run(["withheld"], final=True, mechanism=mechanism, note=f"{note}: {text}")
        fam = run_["families"]["withheld"]
        out["runs"][label] = {"regime": regime_name, "used": fam["mechanisms"], "cost": fam["cost"],
                              "headline": {f: headline(f, fam["result"][f]) for f in families},
                              "result": fam["result"], "run_seconds": round(time.perf_counter() - started, 2)}
        out["final_runs"] = run_["final_runs"]
    return out


def _retention_failures(result):
    failed = []
    for name, scenario in (result.get("scenarios") or {}).items():
        failed += [f"{name}.{check}" for check in scenario.get("failed_checks") or []]
    for metric, row in (result.get("offline_metrics") or {}).items():
        if row.get("retained") is False:
            failed.append(f"offline.{metric} {row.get('value')} < {row.get('baseline')}")
    return failed


def offline(regime_name) -> dict:
    """One run of the offline research benchmark (the `nsf web bench` corpus, loopback, no model), in process; the
    sha256 of its deterministic metrics. docs/quality/evidence/webcortex_bench.json is neither read nor written."""
    import tempfile
    from neuroscifi.webcortex.bench import corpus as C
    from neuroscifi.webcortex.bench import offline as O
    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    started = time.perf_counter()
    context = nullcontext() if regime_name == "wave0" else REGIMES[regime_name]()
    try:
        with context, tempfile.TemporaryDirectory(prefix="nsf-wave1-offline-") as directory:
            bench = O._Bench(Path(directory), web, False)
            try:
                result = bench.execute()
            finally:
                bench.close()
    finally:
        web.stop()
    det = result["deterministic"]
    return {"regime": regime_name, "sha256": hashlib.sha256(json.dumps(det, sort_keys=True, default=str).encode())
            .hexdigest(), "deterministic": det, "wall_seconds": round(time.perf_counter() - started, 2)}


def acceptance(regime_name, out_path) -> int:
    from neuroscifi.cli import main
    context = nullcontext() if regime_name == "wave0" else REGIMES[regime_name]()
    with context:
        return main(["acceptance", "--all", "--out", out_path])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", action="append", choices=("development", "held_out"))
    r.add_argument("--no-ablations", action="store_true")
    r.add_argument("--out")
    o = sub.add_parser("offline")
    o.add_argument("--regime", default="all_off", choices=("all_off", "defaults", "all_on", "wave0"))
    o.add_argument("--out")
    a = sub.add_parser("acceptance")
    a.add_argument("--regime", default="all_off", choices=("all_off", "defaults", "all_on", "wave0"))
    a.add_argument("--out", required=True)
    for name in ("wide", "rescore"):
        w = sub.add_parser(name)
        w.add_argument("--split", action="append", choices=("development", "held_out"))
        w.add_argument("--out")
    f = sub.add_parser("final")
    f.add_argument("--note", required=True)
    f.add_argument("--out", required=True)
    args = p.parse_args(argv)
    if args.cmd == "acceptance":
        return acceptance(args.regime, args.out)
    splits = getattr(args, "split", None) or ["development", "held_out"]
    if args.cmd == "run":
        value = run(splits, ablations=not args.no_ablations)
    elif args.cmd == "wide":
        value = wide(splits)
    elif args.cmd == "rescore":
        value = rescore(splits)
    elif args.cmd == "final":
        value = final(args.note)
    else:
        value = offline(args.regime)
    text = json.dumps(value, indent=1, sort_keys=False, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
