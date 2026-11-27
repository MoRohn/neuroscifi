"""OMNIMIND Wave O-1 integration evidence (offline, no model, development split only).

1. `omnimind_int2.json`: the planner's time estimates against what making and checking took. For each made form,
   every production of lane P's evidence corpus (scripts/omnimind_p_evidence.py CORPUS: the plan's own choice, and
   each planner form named explicitly where its builder takes the content) is run REPEATS times with no individual;
   the pair "seconds" of its record (`forms.foresee`'s estimate beside the wall clock of generate, render, check and
   repair) gives predicted / observed. Reported per made form: n, the median ratio, its range, the median seconds of
   each. A known error of the frozen planner's cost model, recorded, not corrected (ADR-076).
2. `omnimind_int2_development.json` (with --bench DIR): one table of the development runs written by
   `bench_omni.py run --split development --subject artifacts` and `bench_omni_forms.py run --split development`
   for forms, cross and learn (learner and planner alone), read from DIR. Nothing here runs another split.

usage: omnimind_int2_evidence.py [--repeats N] [--out FILE] [--bench DIR --out-dev FILE]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.artifacts import forms  # noqa: E402
from neuroscifi.artifacts import produce as P  # noqa: E402
from neuroscifi.artifacts import produce_builders as B  # noqa: E402
from neuroscifi.artifacts.types import Limits  # noqa: E402

LABEL = ("OMNIMIND Wave O-1 integration evidence: measured on this host, offline, no model; development split only; "
         "made artifacts are made by the system and are not evidence")


def _corpus() -> dict:
    spec = importlib.util.spec_from_file_location("omnimind_p_evidence", ROOT / "scripts" / "omnimind_p_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CORPUS


def _seconds_pair(record) -> tuple[float, float] | None:
    pair = next((p for p in record.get("foresight", []) if p["what"] == "seconds"), None)
    if pair is None or not pair.get("observed"):
        return None
    return float(pair["predicted"]), float(pair["observed"])


def estimates(repeats: int) -> dict:
    runs: dict[str, list] = {}
    for kind, (name, data, request) in _corpus().items():
        pieces, _ = P.interpret(None, [P.Given(name, data)], Limits())
        named = [None] + [f for f in forms.FORMS if f in B.BUILDERS and f not in P.WORDS]
        for form in named:
            if form is not None:
                try:
                    B.build(form, pieces, request)
                except B.CannotBuild:
                    continue
            for _ in range(repeats):
                result = P.produce(None, request, [P.Given(name, data)], form=form)
                pair = _seconds_pair(result.record) if result.status == "made" else None
                if pair is None:
                    break
                made = result.record["made"]["planner_form"]
                runs.setdefault(made, []).append({"source": kind, "named": form, "predicted": pair[0],
                                                  "observed": pair[1]})
    out = {}
    for form, rows in sorted(runs.items()):
        ratios = [r["predicted"] / r["observed"] for r in rows if r["observed"] > 0]
        out[form] = {"n": len(rows), "sources": sorted({r["source"] for r in rows}),
                     "predicted_s_median": round(statistics.median(r["predicted"] for r in rows), 4),
                     "observed_s_median": round(statistics.median(r["observed"] for r in rows), 5),
                     "ratio_median": round(statistics.median(ratios), 1), "ratio_min": round(min(ratios), 1),
                     "ratio_max": round(max(ratios), 1)}
    every = [r["predicted"] / r["observed"] for rows in runs.values() for r in rows if r["observed"] > 0]
    return {"label": LABEL, "repeats": repeats,
            "rule": "ratio = forms.foresee's seconds / the wall clock of generate, render, check and repair of the "
                    "made form (the production record's foresight pair 'seconds'); one shared, loaded host",
            "overall": {"n": len(every), "ratio_median": round(statistics.median(every), 1),
                        "ratio_min": round(min(every), 1), "ratio_max": round(max(every), 1),
                        "under_estimated": sum(1 for r in every if r < 1)},
            "by_form": out,
            "consequence": "the planner's cost criterion and its budget test see slow forms as slower than they are: "
                           "it can only exclude or lower a form that would have fit (more cautious), never admit one "
                           "over the budget, because the production enforces the budget itself on the clock"}


def development(bench: Path) -> dict:
    art = json.loads((bench / "artifacts_dev.json").read_text())
    families = {fam: {k: row.get(k) for k in ("n", "right", "abstained", "wrong", "failed")}
                for fam, row in art["splits"]["development"]["families"].items()}
    forms_r = json.loads((bench / "forms_dev.json").read_text())["result"]
    cross_r = json.loads((bench / "cross_dev.json").read_text())["result"]
    learn = {s: json.loads((bench / f"learn_{s}_dev.json").read_text())["result"]
             for s in ("make_learner", "make_planner_only")}
    return {"label": LABEL, "split": "development", "commit": art.get("commit"),
            "artifacts_subject": families,
            "artifacts_total": {k: sum(r[k] for r in families.values()) for k in ("n", "right", "abstained",
                                                                                     "wrong", "failed")},
            "forms_planner": {k: forms_r[k] for k in ("n_cases", "good", "good_or_fine", "bad",
                                                      "hard_constraints_violated")},
            "cross_relate": {k: cross_r[k] for k in ("n_cases", "correct", "answered", "correct_of_answered",
                                                     "both_twins_correct")} |
            {"by_kind": {k: {kk: vv for kk, vv in v.items() if not isinstance(vv, (dict, list))}
                         for k, v in cross_r.get("by_kind", {}).items()}},
            "learn": {s: {k: {kk: vv for kk, vv in r[k].items() if kk != "per_seed"} for k in ("late_kept", "all_kept")}
                      for s, r in learn.items()},
            "commands": ["scripts/bench_omni.py run --split development --subject artifacts",
                         "scripts/bench_omni_forms.py run --family forms --split development --subject "
                         "neuroscifi.acceptance.omni.subject:choose_form",
                         "scripts/bench_omni_forms.py run --family cross --split development --subject "
                         "neuroscifi.acceptance.omni.subject:relate",
                         "scripts/bench_omni_forms.py run --family learn --split development --subject "
                         "neuroscifi.acceptance.omni.subject:make_learner (and :make_planner_only)"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "docs" / "quality" / "evidence" / "omnimind_int2.json"))
    ap.add_argument("--bench")
    ap.add_argument("--out-dev", default=str(ROOT / "docs" / "quality" / "evidence" /
                                             "omnimind_int2_development.json"))
    ap.add_argument("--skip-estimates", action="store_true")
    args = ap.parse_args(argv)
    if not args.skip_estimates:
        text = json.dumps(estimates(args.repeats), indent=1, sort_keys=True) + "\n"
        Path(args.out).write_text(text)
        print(f"wrote {args.out} ({len(text.encode())} bytes)")
    if args.bench:
        text = json.dumps(development(Path(args.bench)), indent=1, sort_keys=True) + "\n"
        Path(args.out_dev).write_text(text)
        print(f"wrote {args.out_dev} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
