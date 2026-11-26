#!/usr/bin/env python3
"""ADR-063 (Wave 3, third fix lane): the default origin grouping ("transitive") against 3ce4ae3's, on every look of
the benchmark worlds and on every grouping the offline research benchmark makes. Read-only; no individual is kept.

3ce4ae3's `independence_groups` and `_groups_with_credits` are read from git (`git show 3ce4ae3:<claims.py>`) and run
over this tree's claims module (every other function they call is unchanged since 3ce4ae3). Each input is grouped by
both, and the two output LISTS (group order, id order) must be equal.

  worlds    lane F (cycles_splits), lane S (cycles_splits_s), lane S's variant worlds (cycles_splits_s_variants,
            every variant) and lane R (cycles_rate_splits), development and held-out (the withheld worlds are never
            read): every look's claims, stamped as the harness stamps them (attribution_independence off and on), all
            of them together and each value's side, in reading order and reversed.
  offline   one run of the offline research benchmark (scripts/bench_wave1.py offline, regime defaults and all_off) with
            claims.independence_groups wrapped: every call the product makes is also answered by 3ce4ae3's code and
            compared; the run returns this tree's answer, so its digest is the run's own.

Usage: scripts/compare_origin_grouping.py [worlds] [offline] [--out FILE]      (prints a JSON summary; exit 1 on a
difference)
"""
from __future__ import annotations

import argparse
import ast
import collections
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BEFORE = "3ce4ae3"


def old_grouping():
    from neuroscifi.webcortex import claims
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BEFORE}:src/neuroscifi/webcortex/claims.py"],
                         capture_output=True, text=True, check=True).stdout
    namespace = dict(vars(claims))
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in ("independence_groups", "_groups_with_credits"):
            exec(compile(ast.Module([node], []), f"{BEFORE}:claims.py", "exec"), namespace)   # noqa: S102
    return namespace["independence_groups"]


class Tally:
    def __init__(self, old):
        self.old, self.n, self.differ, self.examples = old, collections.Counter(), collections.Counter(), []

    def compare(self, where, found, origins=None, *, grouping=None):
        from neuroscifi.webcortex import claims
        new = (grouping or claims.independence_groups)(found, origins=origins)
        old = self.old(found, origins=origins)
        self.n[where] += 1
        if new != old:
            self.differ[where] += 1
            if len(self.examples) < 20:
                self.examples.append({"where": where, "default": new, BEFORE: old})
        return new


def worlds(tally):
    from neuroscifi.acceptance.cognition import cycles, cycles_rate_splits as R, cycles_splits as F
    from neuroscifi.acceptance.cognition import cycles_splits_s as S, cycles_splits_s_variants as SV
    from neuroscifi.webcortex import claims
    from neuroscifi.webcortex.types import Document
    families = [("F", F.CYCLES, F.generate), ("S", S.CYCLES, S.generate), ("R", R.CYCLES, R.generate)]
    families += [(f"S-variant:{v}", SV.CYCLES, SV.generator(v)) for v in SV.VARIANTS]
    for family, spec_of, generate in families:
        for split in ("development", "held_out"):
            spec = spec_of[split]
            for world in spec["worlds"]:
                for seed in spec["seeds"]:
                    for (name, _), look in sorted(generate(world, seed)["looks"].items()):
                        if not look["pages"]:
                            continue
                        docs = []
                        for p in look["pages"]:
                            url = f"http://{p['host']}{p['path']}"
                            docs.append(Document(url=url, final_url=url, canonical_url=url, domain=p["host"],
                                                 title=name, text=p["text"], retrieved_at=cycles.T0,
                                                 retrieval_mode="live",
                                                 published_at=cycles.T0 + p["published_cycle"] * 86_400.0))
                        for attribution in (False, True):
                            found = [x for d in docs for x in claims.extract_claims(
                                d, f"What is the latest {name} release?")]
                            claims.stamp_origins(found, docs, attribution=attribution)
                            sides = collections.defaultdict(list)
                            for x in found:
                                sides[tuple(sorted(x.values or ()))].append(x)
                            where = f"{family} {split} attribution={'on' if attribution else 'off'}"
                            for part in [found, *sides.values()]:
                                if part:
                                    tally.compare(where, part)
                                    tally.compare(where, part[::-1])


def offline(tally):
    from unittest import mock
    from neuroscifi.webcortex import claims
    spec = importlib.util.spec_from_file_location("bench_wave1_grouping", ROOT / "scripts" / "bench_wave1.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    digests = {}
    for regime in ("defaults", "all_off"):
        where = f"offline research benchmark ({regime})"

        def wrapped(found, *, origins=None, _w=where, _real=claims.independence_groups):
            return tally.compare(_w, list(found), origins, grouping=_real)
        with mock.patch.object(claims, "independence_groups", wrapped):
            run = bench.offline(regime)
        digests[regime] = {"sha256": run["sha256"], "portable_sha256": run["portable_sha256"]}
    return digests


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("parts", nargs="*", choices=("worlds", "offline"), help="both when none is named")
    p.add_argument("--out")
    a = p.parse_args(argv)
    a.parts = a.parts or ["worlds", "offline"]
    tally = Tally(old_grouping())
    out = {"compared_with": BEFORE}
    if "worlds" in a.parts:
        worlds(tally)
    if "offline" in a.parts:
        out["offline_digests"] = offline(tally)
    out |= {"inputs": dict(tally.n), "inputs_total": sum(tally.n.values()), "different": dict(tally.differ),
            "different_total": sum(tally.differ.values()), "examples": tally.examples}
    text = json.dumps(out, indent=1, default=str) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    sys.stdout.write(text)
    return 1 if out["different_total"] else 0


if __name__ == "__main__":
    sys.exit(main())
