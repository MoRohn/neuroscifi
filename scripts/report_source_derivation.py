#!/usr/bin/env python3
"""ADR-066 (Wave 4 lane S): the evidence summary docs/quality/evidence/genesis_wave4_s.json, from the outputs of the
runs named below (each produced by its own script, given here as files) and two read-only computations made here.

Inputs (JSON files): --dev and --held (scripts/bench_sources.py run, every configuration), --cost
(scripts/cost_source_derivation.py), --cycles-f (scripts/bench_learning_cycles.py run --split held_out --config shipped
--config wave4-s:derivation), --cycles-s (scripts/bench_wave3_s.py run --family lane-s --config baseline --config
wave4-s:derivation), --families (scripts/families_with_source_derivation.py), --compare (scripts/
compare_source_derivation.py), --assess-held (scripts/assess_derivation_evidence.py --split held_out).
Computed here: the verdict (sources.verdict_derivation, committed before the held-out run) on both splits; the
development -> held-out drop as rates per look and per eligible look; and the name reader's census by page form (which
derived pages of the development and held-out worlds name a READ page they derive from in capitalised words equal to
its site label: what the rule can and cannot see, and why).

The summary keeps, per configuration and world: totals over seeds, by shape summed over seeds, mean_correct per seed
and its spread, the read-back, and looks changed against shipped; never per-look histories.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _bench_summary(out):
    keep = {}
    for name, conf in out["configurations"].items():
        keep[name] = {"settings": conf["settings"], "worlds": {
            w: {"totals_sum": e["totals_sum"], "by_shape_sum": e["by_shape_sum"], "mean_correct": e["mean_correct"],
                "mean_correct_per_seed": [s["mean_correct"] for s in e["per_seed"]],
                "config_read_back": e.get("config_read_back"), "config_same_every_seed": e.get("config_same_every_seed"),
                "looks_changed_vs_shipped": e.get("looks_changed_vs_shipped"), "cost_mean": e.get("cost_mean")}
            for w, e in conf["worlds"].items()}}
    refs = {w: {r: {"totals_sum": e["totals_sum"], "by_shape_sum": e["by_shape_sum"], "mean_correct": e["mean_correct"]}
                for r, e in v.items()} for w, v in out["references"].items()}
    return {"commit": out.get("commit"), "split": out["split"], "seeds": out["seeds"],
            "world_digests": out["world_digests"], "configurations": keep, "references": refs}


def _rates(v, looks, eligible):
    t = v["totals"]
    return {k: {c: round(t[k][c] / (eligible if k == "suppressed" else looks), 4) for c in t[k]}
            for k in ("wrongly_admitted", "wrongly_admitted_false", "own_admitted", "suppressed")}


def _drop(dev_v, held_v):
    out = {}
    for name, v in (("development", dev_v), ("held_out", held_v)):
        looks = v["totals"]["looks"]["shipped"]
        eligible = v["totals"]["eligible"]["shipped"]
        r = _rates(v, looks, eligible)
        out[name] = {"per_look_or_eligible": r,
                     "rule_minus_shipped": {k: round(r[k]["source_derivation"] - r[k]["shipped"], 4) for k in r},
                     "precision_recall": v["precision_recall"],
                     "paired_correct": {w: round(p["mean"], 4) for w, p in v["paired_correct"].items()}}
    return out


def name_census():
    """Per derived page form (development and held-out worlds): of the (page, parent) pairs, the parent read and named
    by the page in capitalised words equal to its site label (seen), read and not so named (missed), or not read (the
    rule has no read page to attach it to)."""
    from neuroscifi.acceptance.cognition import sources
    from neuroscifi.webcortex import claims
    out = collections.defaultdict(collections.Counter)
    for split in sources.SPLITS:
        spec, generate = sources._public(split)
        for world in spec["worlds"]:
            for seed in spec["seeds"]:
                g = generate(world, seed)
                for look in g["looks"].values():
                    read = {p["id"]: p for p in look["pages"]}
                    for p in look["pages"]:
                        if p["basis"] != "derived":
                            continue
                        keys = claims._capitalised_keys(p["text"])
                        for parent in p["derives_from"]:
                            q = read.get(parent)
                            label = claims.re.sub(r"[^a-z0-9]", "", claims.site_label(q["host"])) if q else None
                            kind = "parent not read" if q is None else "seen" if (
                                len(label) >= claims.DERIVATION_MIN_KEY and label in keys) else "missed"
                            out[f"{p['shape']} / {p['form']}"][kind] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("dev", "held", "cost", "cycles-f", "cycles-s", "families", "compare", "assess-held"):
        ap.add_argument(f"--{name}")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    from neuroscifi.acceptance.cognition import sources
    dev, held = _load(a.dev), _load(a.held)
    v_dev, v_held = sources.verdict_derivation(dev), sources.verdict_derivation(held)
    result = {
        "what": "Wave 4 lane S (ADR-066): source_derivation, one source by evidence of derivation; simulated worlds of "
                "fictional software releases read through the real modules, offline, no model",
        "verdict_rule": "sources.verdict_derivation (committed in 8e46b98, before the held-out run)",
        "verdict": {"development": v_dev, "held_out": v_held},
        "development_to_held_out": _drop(v_dev, v_held),
        "name_census": name_census(),
        "bench": {"development": _bench_summary(dev), "held_out": _bench_summary(held)},
    }
    if a.assess_held:
        result["evidence_assessment_held_out"] = _load(a.assess_held)
    if a.cost:
        result["cost"] = _load(a.cost)
    cf = _load(a.cycles_f)
    if cf:
        result["cycles_lane_f_held_out"] = {"commit": cf.get("commit"), "worlds": {
            w: {c: {"mean_correct_per_seed": [s["mean.correct"] for s in cf["configurations"][c]["worlds"][w][
                "per_seed"]], "sd": cf["configurations"][c]["worlds"][w]["spread"]["mean.correct"]["sd"],
                    "counters": {k: [s.get(k) for s in cf["configurations"][c]["worlds"][w]["per_seed"]]
                                 for k in ("self_admitted", "wire_admitted", "one_origin_rises", "retention.lost",
                                           "retention.regressed", "admitted.wrong", "changed_held_cycles")},
                    "read_back_source_derivation": cf["configurations"][c]["worlds"][w]["config_read_back"].get(
                        "source_derivation")}
                for c in ("shipped", "wave4-s:derivation")} for w in cf["configurations"]["shipped"]["worlds"]}}
    cs = _load(a.cycles_s)
    if cs:
        keys = ("mean.correct", "s.own_admitted", "s.syndicated_admitted", "s.wrong_admitted", "s.honest_merged",
                "s.copies_missed", "retention.lost", "retention.regressed")
        result["cycles_wave3_lane_s"] = {"commit": cs.get("commit"), "results": {
            name: {w: {c: {k: [s.get(k) for s in r["configurations"][c]["worlds"][w]["per_seed"]] for k in keys}
                       | {"sd_mean_correct": r["configurations"][c]["worlds"][w]["spread"]["mean.correct"]["sd"],
                          "read_back_source_derivation": r["configurations"][c]["worlds"][w]["config_read_back"].get(
                              "source_derivation")}
                       for c in ("baseline", "wave4-s:derivation")}
                   for w in r["configurations"]["baseline"]["worlds"]}
            for name, r in cs["results"].items()}}
    if a.families:
        result["families_with_the_rule_on"] = _load(a.families)
    if a.compare:
        c = _load(a.compare)
        result["default_path_comparison"] = {k: c[k] for k in ("compared_with", "inputs_total", "different_total",
                                                               "offline_digests") if k in c}
    text = json.dumps(result, indent=1, default=str) + "\n"
    Path(a.out).write_text(text, encoding="utf-8")
    print(f"wrote {a.out} ({len(text.encode()) // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
