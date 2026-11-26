#!/usr/bin/env python3
"""The front door's withheld look, condition (b) of the rule written before it (review3, "Wording I would accept"):
compare the run with the front door (ON) and the run with `--ablate front_door` (OFF), leaf by leaf.

  python scripts/compare_front_door_look.py ON.json OFF.json

ON and OFF are outputs of `scripts/bench_cognition.py run --mechanism imagination [--ablate front_door]`, either of the
withheld family (`--family withheld --final`, the look) or of the counterfactual family on a public split (`--family
counterfactual --split held_out`, the check that the comparison is mechanical and does not fail on noise).

Read back first (otherwise the look is VOID, exit 2): ON's `ablate` is [] and OFF's ["front_door"]; the counterfactual
part ran `imagination` without falling back to the baseline in both; for a withheld pair, both runs name the same
commit and a clean tree. Then:
  (a) reported: in ON, summary_by_group.front_door.max_relative_error.max <= 0.1 (or None: none answered), and per seed
      front_door, front_door_declined and front_door_within_tolerance; "not exercised by these worlds" when every
      front-door question was declined.
  (b) every leaf of the result (families.withheld.result, or families.counterfactual.result) is equal in the two runs
      except, in the counterfactual result: everything under summary_by_group.front_door and
      by_query_kind["scm:confounded"], and the fields EXEMPT under summary and under each per_seed[i]. Run metadata
      (generated_at, environment, final_runs, ablate, note, cost) is not compared (it is outside the result).
Prints every differing leaf, marked exempt or not. Exit 0 when (a) and (b) hold, 1 when either fails, 2 when void.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

TOLERANCE = 0.1
EXEMPT = ("answered_within_tolerance", "answered_within_strict_tolerance", "max_relative_error",
          "front_door_declined", "front_door_within_tolerance", "declined_confounded_share")


def _part(run):
    """(the compared result, the counterfactual result inside it, the path from one to the other, the read-back
    record of the counterfactual mechanism)."""
    families = run.get("families") or {}
    if "withheld" in families:
        whole = families["withheld"]
        return whole["result"], whole["result"]["counterfactual"], ("counterfactual",), \
            (whole.get("mechanisms") or {}).get("counterfactual") or {}
    whole = families["counterfactual"]
    return whole["result"], whole["result"], (), whole.get("mechanism") or {}


def leaves(value, path=()):
    """{path tuple: leaf value} of a JSON value (a list's items keyed by their index)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out |= leaves(v, path + (str(k),))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out |= leaves(v, path + (str(i),))
        return out
    return {path: value}


def exempt(path, prefix) -> bool:
    """Whether a differing leaf is one the front door may move (see the module doc)."""
    if path[:len(prefix)] != prefix:
        return False
    p = path[len(prefix):]
    return (p[:2] == ("summary_by_group", "front_door") or p[:2] == ("by_query_kind", "scm:confounded") or
            (len(p) >= 2 and p[0] == "summary" and p[1] in EXEMPT) or
            (len(p) >= 3 and p[0] == "per_seed" and p[2] in EXEMPT))


def compare(on, off) -> dict:
    void = []
    if on.get("ablate") != []:
        void.append(f"ON's ablate is {on.get('ablate')!r}, not []")
    if off.get("ablate") != ["front_door"]:
        void.append(f"OFF's ablate is {off.get('ablate')!r}, not ['front_door']")
    result_on, cf_on, prefix, mech_on = _part(on)
    result_off, _cf_off, prefix_off, mech_off = _part(off)
    if prefix != prefix_off:
        void.append("the two runs are not of the same family")
    for label, mech in (("ON", mech_on), ("OFF", mech_off)):
        if mech.get("used") != "imagination" or mech.get("fell_back_to_baseline") is not False:
            void.append(f"{label}'s counterfactual part ran {mech.get('used')!r} "
                        f"(fell back to the baseline: {mech.get('fell_back_to_baseline')!r})")
    if prefix:          # the withheld look itself: one commit, clean trees
        envs = [r.get("environment") or {} for r in (on, off)]
        if envs[0].get("git_commit") != envs[1].get("git_commit") or not envs[0].get("git_commit"):
            void.append("the two runs do not name the same commit")
        if any(e.get("git_dirty") is not False for e in envs):
            void.append("a run was not taken on a clean tree")
    group = (cf_on.get("summary_by_group") or {}).get("front_door") or {}
    worst = (group.get("max_relative_error") or {}).get("max") if isinstance(group.get("max_relative_error"),
                                                                              dict) else None
    per_seed = [{k: row.get(k) for k in ("front_door", "front_door_declined", "front_door_within_tolerance")}
                for row in cf_on.get("per_seed") or ()]
    asked = sum(r["front_door"] or 0 for r in per_seed)
    declined = sum(r["front_door_declined"] or 0 for r in per_seed)
    a = worst is None or worst <= TOLERANCE + 1e-12
    a_on, a_off = leaves(result_on), leaves(result_off)
    differ = []
    for path in sorted(set(a_on) | set(a_off)):
        left, right = a_on.get(path, "<missing>"), a_off.get(path, "<missing>")
        if left != right:
            differ.append({"leaf": ".".join(path), "on": left, "off": right, "exempt": exempt(path, prefix)})
    outside = [d for d in differ if not d["exempt"]]
    return {"void": void, "a_holds": a, "front_door_max_relative_error": worst,
            "front_door_group_answered_within_tolerance": group.get("answered_within_tolerance"),
            "per_seed": per_seed,
            "exercised": "not exercised by these worlds (every front-door question was declined)"
            if asked and declined == asked else ("no front-door question in these worlds" if not asked else
                                                 f"{asked - declined} of {asked} front-door questions answered"),
            "b_holds": not outside, "differing_leaves": len(differ), "outside_the_exemptions": len(outside),
            "differ": differ}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    on, off = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args)
    out = compare(on, off)
    for d in out["differ"]:
        print(f"{'exempt ' if d['exempt'] else 'OUTSIDE'}  {d['leaf']}: on {d['on']!r}  off {d['off']!r}")
    summary = {k: v for k, v in out.items() if k != "differ"}
    print(json.dumps(summary, indent=1))
    if out["void"]:
        print("VOID: " + "; ".join(out["void"]), file=sys.stderr)
        return 2
    return 0 if out["a_holds"] and out["b_holds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
