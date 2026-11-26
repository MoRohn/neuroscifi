#!/usr/bin/env python3
"""ADR-066 (Wave 4 lane S): the cognitive benchmark's retention and adversarial families, development and held-out,
with WebConfig.source_derivation ON wherever an individual's saved settings do not name it (a case that sets it keeps
its own value), against the same run at the shipped defaults. Offline; never the withheld family.

How the setting is forced: WebConfig.from_json, for this process only, reads a missing source_derivation as true. The
run reads the setting back from a new individual first and refuses to continue if it is not on.

Usage: scripts/families_with_source_derivation.py [--out FILE]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FAMILIES = ("retention", "adversarial")


def _summary(out: dict) -> dict:
    fam = out["families"]
    adv = fam["adversarial"]["result"]
    ret = fam["retention"]["result"]
    return {"adversarial": {"passed": adv["passed"], "scored": adv["scored"], "failed": adv["failed"],
                            "by_origin": {k: {"passed": v["passed"], "scored": v["scored"], "failed": v["failed"]}
                                          for k, v in adv["by_origin"].items()}},
            "retention": {"all_scenarios_pass": ret["all_scenarios_pass"], "all_retained": ret["all_retained"],
                          "failed_scenarios": sorted(k for k, v in ret["scenarios"].items() if not v["pass"]),
                          "not_retained": sorted(k for k, v in ret["offline_metrics"].items()
                                                 if v["retained"] is False)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    from dataclasses import replace
    from neuroscifi import acceptance  # noqa: F401
    from neuroscifi.acceptance import cognition
    from neuroscifi.webcortex import config as wconfig
    original = wconfig.WebConfig.from_json.__func__

    def forced(cls, text):
        cfg = original(cls, text)
        raw = json.loads(text) if text else {}
        return cfg if "source_derivation" in raw else replace(cfg, source_derivation=True)
    result = {"compared": "shipped defaults vs source_derivation forced on (unless a case sets it); retention against "
                          "the recorded baseline (cognition.BASELINE)"}
    recorded = json.loads(cognition.BASELINE.read_text(encoding="utf-8"))
    for split in ("development", "held_out"):
        result[split] = {"shipped": _summary(cognition.run(FAMILIES, split=split, baseline_output=recorded))}
        with mock.patch.object(wconfig.WebConfig, "from_json", classmethod(forced)):
            if wconfig.WebConfig.from_json(None).source_derivation is not True:
                raise SystemExit("the setting is not on")
            result[split]["source_derivation"] = _summary(cognition.run(FAMILIES, split=split,
                                                                        baseline_output=recorded))
    text = json.dumps(result, indent=1, default=str) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
