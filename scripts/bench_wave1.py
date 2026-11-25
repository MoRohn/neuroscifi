#!/usr/bin/env python3
"""Wave 1 integration evidence (Project Cognitive Genesis): the cognitive benchmark on the integrated tree.

  all_off    every Wave 1 setting read as off by every individual (Wave 0's behaviour), the baseline policy and the
             references: diffed against the recorded docs/quality/evidence/genesis_baseline.json (results only; cost,
             environment and timestamps excluded). The adversarial family's case list changed in Wave 1 (lane M
             renamed one case, lane K added four), so its diff is reported case by case.
  defaults   every setting as shipped, each family answered by the mechanism the shipped settings use: anticipation
             and info_value baseline (learning progress is on by default), counterfactual imagination (what-if and
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

Both splits; the withheld family is never run here (no --final). Usage:
  scripts/bench_wave1.py run [--split development|held_out ...] [--out FILE]
  scripts/bench_wave1.py offline [--regime all_off|defaults|wave0]    (the offline research benchmark, one run)
  scripts/bench_wave1.py acceptance --regime all_off --out FILE       (`nsf acceptance --all` under a regime)
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


def diff(a, b, path="", out=None, limit=200):
    """Paths where two result trees differ (cost and timing keys excluded)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k in SKIP | DERIVED:
                continue
            if k not in a or k not in b:
                out.append({"path": f"{path}.{k}".lstrip("."), "before": a.get(k, "<absent>") if k in a else
                            "<absent>", "after": b.get(k) if k in b else "<absent>"})
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append({"path": path.lstrip("."), "before": a, "after": b})
    return out


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
    return {dotted: cognition._lookup(result, dotted) for dotted, _ in cognition.HEADLINES[family]}


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
            if family == "adversarial":         # the case list itself changed: compared case by case below
                d = diff({k: v for k, v in strip(before).items() if k != "cases"},
                         {k: v for k, v in strip(base["result"]).items() if k != "cases"})
            else:
                d = diff(strip(before), strip(base["result"]))
            entry = {"differences": len(d), "first": d[:20], "headline": headline(family, base["result"]),
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
        if references:
            for name, refs in (("oracle", ("info_value",)), ("random", ("info_value",)),
                               ("autonomy", ("info_value",)), ("open", ("anticipation",))):
                for family in refs:
                    r = one(family, split, name, regime_name="all_off")
                    before = recorded[f"{split}_references"][name]["result"]
                    d = diff(strip(before), strip(r["result"]))
                    section["references"][f"{family}/{name}"] = {"differences": len(d), "first": d[:10]}
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
                section["ablations"][key] = rows
            for key in (("info_value", "knowledge_recheck"), ("retention", "knowledge_evolution"),
                        ("adversarial", "knowledge_evolution")):      # lane K: no components; the mechanism alone
                r = one(key[0], split, key[1])
                section["ablations"][f"{key[0]}/{key[1]}"] = {"lane": "K", "full": headline(key[0], r["result"]),
                                                              "note": "no components: its ablation is the defaults "
                                                                      "run (mechanism off)"}
        out["splits"][split] = section
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
    args = p.parse_args(argv)
    if args.cmd == "acceptance":
        return acceptance(args.regime, args.out)
    value = (run(args.split or ["development", "held_out"], ablations=not args.no_ablations) if args.cmd == "run"
             else offline(args.regime))
    text = json.dumps(value, indent=1, sort_keys=False, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
