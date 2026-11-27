#!/usr/bin/env python3
"""ADR-066 (Wave 4 lane S): with WebConfig.source_derivation at its default (off), the claims module groups, stamps and
scores exactly as at the start of the lane (BEFORE), on every corpus. Read-only; no individual is kept.

BEFORE's whole `webcortex/claims.py` is read from git and loaded as a second module beside this tree's (every module it
imports is shared). Each input goes through both: extract_claims, stamp_origins (the claims' JSON, flags included, must
be equal after it), independence_groups (the output LISTS: group order and id order) over all claims of an input, each
value's side, and both again reversed, and cluster + score (the scored clusters' JSON: evaluate's groups, counts,
posterior, status). Every other setting is tried at each of its values (attribution_independence off/on,
origin_grouping transitive/one_way), with source_derivation off.

  worlds     the source-independence family (Wave 4 step 0: sources_splits_dev, sources_splits_heldout; never the
             withheld worlds), lane F (cycles_splits), lane S (cycles_splits_s), lane S's variant worlds
             (cycles_splits_s_variants, every variant) and lane R (cycles_rate_splits), development and held-out:
             every look
  scenarios  the reviewers' literal pages (tests/webcortex/test_origin_grouping.py SCENARIOS: review3c's probes, the
             second fix lane's adversarial pages), every order of each (at most 120)
  offline    one run of the offline research benchmark (scripts/bench_wave1.py offline, regimes defaults and all_off)
             with claims.stamp_origins, independence_groups and evaluate wrapped: every call the product makes is also
             answered by BEFORE's code on a copy of its input and compared; the run's digest is the run's own

Usage: scripts/compare_source_derivation.py [worlds] [scenarios] [offline] [--out FILE]   (JSON summary; exit 1 on a
difference)
"""
from __future__ import annotations

import argparse
import collections
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BEFORE = "42fd136"


def old_claims():
    """BEFORE's claims module, loaded beside this tree's (same package, so its relative imports resolve here)."""
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{BEFORE}:src/neuroscifi/webcortex/claims.py"],
                         capture_output=True, text=True, check=True).stdout
    module = types.ModuleType("neuroscifi.webcortex._claims_before")
    module.__package__ = "neuroscifi.webcortex"
    module.__file__ = f"{BEFORE}:claims.py"
    sys.modules[module.__name__] = module
    exec(compile(src, module.__file__, "exec"), module.__dict__)      # noqa: S102
    return module


def _json(items):
    return [x.to_json() for x in items]


class Tally:
    def __init__(self, old):
        self.old, self.n, self.differ, self.examples = old, collections.Counter(), collections.Counter(), []

    def same(self, where, what, new, old):
        self.n[f"{where} / {what}"] += 1
        if new != old:
            self.differ[f"{where} / {what}"] += 1
            if len(self.examples) < 20:
                self.examples.append({"where": where, "what": what, "new": new, BEFORE: old})

    def pipeline(self, where, docs, question):
        from neuroscifi.webcortex import claims as new
        old = self.old
        for attribution, grouping in itertools.product((False, True), ("transitive", "one_way")):
            w = f"{where} attribution={'on' if attribution else 'off'} grouping={grouping}"
            fn = [c for d in docs for c in new.extract_claims(d, question)]
            fo = [c for d in docs for c in old.extract_claims(d, question)]
            on = new.stamp_origins(fn, docs, attribution=attribution, grouping=grouping)
            oo = old.stamp_origins(fo, docs, attribution=attribution, grouping=grouping)
            self.same(w, "stamped claims", _json(fn), _json(fo))
            self.same(w, "origins", on, oo)
            sides_n, sides_o = collections.defaultdict(list), collections.defaultdict(list)
            for x in fn:
                sides_n[tuple(sorted(x.values or ()))].append(x)
            for x in fo:
                sides_o[tuple(sorted(x.values or ()))].append(x)
            for part_n, part_o in [(fn, fo), *zip(sides_n.values(), sides_o.values())]:
                if part_n:
                    self.same(w, "groups", new.independence_groups(part_n, origins=on),
                              old.independence_groups(part_o, origins=oo))
                    self.same(w, "groups", new.independence_groups(part_n[::-1], origins=on),
                              old.independence_groups(part_o[::-1], origins=oo))
            if fn:
                now = max(d.retrieved_at for d in docs)
                self.same(w, "scored clusters",
                          _json(new.score(c, now=now, freshness_class="release", origins=on) for c in new.cluster(fn)),
                          _json(old.score(c, now=now, freshness_class="release", origins=oo) for c in old.cluster(fo)))


def worlds(tally):
    from neuroscifi.acceptance.cognition import cycles, cycles_rate_splits as R, cycles_splits as F
    from neuroscifi.acceptance.cognition import cycles_splits_s as S, cycles_splits_s_variants as SV
    from neuroscifi.acceptance.cognition import sources
    from neuroscifi.webcortex.types import Document
    for split in sources.SPLITS:
        spec, generate = sources._public(split)
        for world in spec["worlds"]:
            for seed in spec["seeds"]:
                g = generate(world, seed)
                for (name, c), look in sorted(g["looks"].items()):
                    now = sources.T0 + c * world["cycle_hours"] * 3600.0
                    docs = [sources._document(p, now, world, name) for p in look["pages"]]
                    if docs:
                        tally.pipeline(f"sources {split}", docs, f"What is the latest {name} release?")
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
                        tally.pipeline(f"{family} {split}", docs, f"What is the latest {name} release?")


def scenarios(tally):
    from neuroscifi.webcortex.types import Document
    spec = importlib.util.spec_from_file_location("origin_grouping_scenarios",
                                                  ROOT / "tests" / "webcortex" / "test_origin_grouping.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, (_attribution, pages) in module.SCENARIOS.items():
        for order in itertools.islice(itertools.permutations(pages), 120):
            docs = []
            for host, text, path, canonical in order:
                url = f"http://{host}{path or '/' + module.N.lower()}"
                docs.append(Document(url=url, final_url=url, canonical_url=canonical or url, domain=host,
                                     title=module.N, text=text, retrieved_at=module.T0, retrieval_mode="live",
                                     published_at=module.T0 - 86_400.0))
            tally.pipeline(f"scenario {name}", docs, module.SQ)


def offline(tally):
    from unittest import mock
    from neuroscifi.webcortex import claims
    spec = importlib.util.spec_from_file_location("bench_wave1_derivation", ROOT / "scripts" / "bench_wave1.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    old = tally.old
    digests = {}
    for regime in ("defaults", "all_off"):
        where = f"offline research benchmark ({regime})"
        real = {n: getattr(claims, n) for n in ("stamp_origins", "independence_groups", "evaluate")}

        def stamp(found, documents, *a, _w=where, **kw):
            found = list(found)
            twin = copy.deepcopy(found)
            out = real["stamp_origins"](found, documents, *a, **kw)
            kw.pop("derivation", None)
            kw.pop("echo", None)
            tally.same(_w, "stamp_origins", (out, _json(found)), (old.stamp_origins(twin, documents, *a, **kw),
                                                                  _json(twin)))
            return out

        def groups(found, *, origins=None, _w=where):
            found = list(found)
            out = real["independence_groups"](found, origins=origins)
            tally.same(_w, "independence_groups", out, old.independence_groups(copy.deepcopy(found), origins=origins))
            return out

        def evaluate(found, **kw):
            found = list(found)
            out = real["evaluate"](found, **kw)
            twin = old.evaluate(copy.deepcopy(found), **kw)
            strip = (lambda e: {k: ([x.id for x in v] if k == "order" else v.id if k == "representative" and v
                                    else v) for k, v in e.items()})
            tally.same(where, "evaluate", strip(out), strip(twin))
            return out
        with mock.patch.object(claims, "stamp_origins", stamp), \
                mock.patch.object(claims, "independence_groups", groups), \
                mock.patch.object(claims, "evaluate", evaluate):
            run = bench.offline(regime)
        digests[regime] = {"sha256": run["sha256"], "portable_sha256": run["portable_sha256"]}
    return digests


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("parts", nargs="*", choices=("worlds", "scenarios", "offline"), help="all when none is named")
    p.add_argument("--out")
    a = p.parse_args(argv)
    a.parts = a.parts or ["worlds", "scenarios", "offline"]
    from neuroscifi.webcortex.config import WebConfig
    if WebConfig().source_derivation is not False:
        raise SystemExit("source_derivation is not off by default")
    tally = Tally(old_claims())
    out = {"compared_with": BEFORE}
    if "worlds" in a.parts:
        worlds(tally)
    if "scenarios" in a.parts:
        scenarios(tally)
    if "offline" in a.parts:
        out["offline_digests"] = offline(tally)
    out |= {"inputs_total": sum(tally.n.values()), "different_total": sum(tally.differ.values()),
            "inputs": dict(tally.n), "different": dict(tally.differ), "examples": tally.examples}
    text = json.dumps(out, indent=1, default=str) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    sys.stdout.write(json.dumps({k: out[k] for k in ("compared_with", "inputs_total", "different_total")}
                                | {"offline_digests": out.get("offline_digests")}) + "\n")
    return 1 if out["different_total"] else 0


if __name__ == "__main__":
    sys.exit(main())
