#!/usr/bin/env python3
"""Knowledge benchmarks: the lexical index of knowledge.query, and contradictions stated in other words.

  python scripts/bench_knowledge.py index --sizes 1000 20000 100000 --out index.json
  python scripts/bench_knowledge.py contradictions --label lexical --out lexical.json
  python scripts/bench_knowledge.py contradictions --embed-url http://127.0.0.1:18441/v1 \
      --rerank-url http://127.0.0.1:18442/v1 --key-file ~/.config/neuroscifi/memory/api_key \
      --label semantic --out semantic.json [--sweep]

`index`: a temporary individual is given N synthetic live statements (inserted directly: the write path is not what
is measured) and one question is asked through knowledge.query: with the index built for the call (`cached=False`,
what every call cost before the index was kept), on the first call of a process, on a repeated call, and on the
first call after one more statement was written. The memory the kept index holds is measured with tracemalloc.

`contradictions`: the 0.7.0 write path finds knowledge about "the same subject" by its key words
(consolidate._existing), so a statement of the same fact in other words is stored as a second, unrelated item, and
one with another value is not seen as a contradiction. The benchmark stores the fictional facts of
webcortex/bench/contradiction_paraphrase.json as knowledge and offers each probe statement to the same code the
consolidation uses (a claim cluster built from the probe, consolidate.propose + consolidate._existing +
consolidate._compare). Each probe has a class and a gold outcome:

  restate      the fact in other words, same value            -> same      (paired, values agree)
  contradict   the fact in other words, another value          -> conflict  (paired, values differ)
  units        the fact in other words, value in other units   -> same or conflict, as the probe says
  property     same subject, another property, same kind of value -> none (must not be paired)
  subject      another subject, similar wording                -> none (must not be paired)
  unrelated    nothing in memory is about it                    -> none

Reported per class and per half (development and held-out subjects): how many probes got their gold outcome, and
for the three negative classes how many were paired at all (false pairs) and what the value comparison then said.
`--sweep` (with the models) records the embedding similarity and the reranker score of every probe against its
best stored candidate, so thresholds can be chosen on the development half only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.instance import store                                   # noqa: E402
from neuroscifi.webcortex import knowledge, semantic, state             # noqa: E402

T0 = 1_795_200_000.0


# ---- index ----------------------------------------------------------------------------------------------------------

_NAMES = ("Orbix", "Tessaly", "Varnel", "Lumen", "Quillstream", "Tolvane", "Halvorsen", "Ostra", "Solvang", "Zephyr",
          "Nimbus", "Kestrel", "Marlow", "Dunmere", "Avalis", "Corvane", "Ibrel", "Sennet", "Thalric", "Wyndmoor")
_NOUNS = ("server", "river", "bridge", "satellite", "library", "lighthouse", "census", "harbour", "medal", "compiler",
          "reactor", "archive", "railway", "observatory", "protocol", "glacier", "festival", "ministry", "ledger")
_VERBS = ("listens on", "is measured at", "spans", "was launched after", "reached", "stands above", "counted",
          "opened beside", "was awarded for", "targets", "replaced", "stores", "connects", "reported")
_TAILS = ("for client connections", "according to the survey", "after a week of delays", "in the northern district",
          "under the public licence", "during the autumn session", "with the default settings", "across the basin")


def _statements(n, seed=7):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        out.append(f"The {rng.choice(_NAMES)}{i} {rng.choice(_NOUNS)} {rng.choice(_VERBS)} {rng.randint(2, 99999)} "
                   f"{rng.choice(_NOUNS)}s {rng.choice(_TAILS)}.")
    return out


def _fill(db, statements):
    from neuroscifi.webcortex import claims, text
    rows = []
    for i, s in enumerate(statements):
        key = claims.claim_key(s) or f"k{i}"
        rows.append((f"{key} #{i}", 1, s, "accepted", 0.8, 0.2, "stable", T0, T0, T0, json.dumps(text.values(s)),
                     T0, T0))
    with store.write(db):
        db.executemany(
            "INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
            "first_verified_at,last_verified_at,valid_from,claim_values,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        store.bump_state_version(db)


def _timed(fn, repeats=1):
    best = None
    for _ in range(repeats):
        start = time.perf_counter()
        out = fn()
        took = time.perf_counter() - start
        best = took if best is None or took < best else best
    return round(best * 1000, 2), out


def bench_index(sizes, repeats):
    question = "Which Varnel river spans the northern district"
    results = []
    for n in sizes:
        with tempfile.TemporaryDirectory(prefix="nsf-bench-knowledge-") as folder:
            db = store.create(Path(folder) / "i.sqlite", T0)
            state.set_config(db, {"enabled": True}, T0)
            _fill(db, _statements(n))
            knowledge.clear_index()
            before_ms, reference = _timed(lambda: knowledge.query(db, question, T0 + 1, cached=False), repeats)
            knowledge.clear_index()
            tracemalloc.start()                      # memory in a build of its own: tracing slows the build fivefold
            knowledge.query(db, question, T0 + 1)
            held_bytes = tracemalloc.get_traced_memory()[0]
            tracemalloc.stop()
            knowledge.clear_index()
            first_ms, first = _timed(lambda: knowledge.query(db, question, T0 + 1))
            repeat_ms, again = _timed(lambda: knowledge.query(db, question, T0 + 1), max(3, repeats))
            other_ms, _ = _timed(lambda: knowledge.query(db, "Tolvane lighthouse harbour ledger", T0 + 1), 3)
            knowledge.assert_fact(db, "The Wyndmoor glacier retreated 41 metres during the autumn session.", T0 + 2)
            after_write_ms, after = _timed(lambda: knowledge.query(db, question, T0 + 3))
            identical = first == reference and again == reference and \
                after == knowledge.query(db, question, T0 + 3, cached=False)
            read_ms, _ = _timed(lambda: db.execute(
                "SELECT id, statement, key FROM web_knowledge WHERE status IN ('provisional','accepted','stale') "
                "ORDER BY id").fetchall(), 3)
            results.append({"statements": n, "matched": reference["matched"],
                            "per_call_before_ms": before_ms, "first_call_ms": first_ms,
                            "repeated_call_ms": repeat_ms, "another_question_ms": other_ms,
                            "first_call_after_one_write_ms": after_write_ms,
                            "of_which_reading_the_live_statements_ms": read_ms,
                            "index_bytes_held": held_bytes, "bytes_per_statement": round(held_bytes / n),
                            "results_identical_to_uncached": identical})
            db.close()
            knowledge.clear_index()
    return {"benchmark": "knowledge.query lexical index", "question": question, "repeats": repeats,
            "note": "per_call_before_ms is cached=False: tokenise every live statement and build the index for the "
                    "call, what every call did before the index was kept. Times are the best of the repeats.",
            "results": results}


# ---- contradictions -----------------------------------------------------------------------------------------------------

DATA = Path(__file__).resolve().parents[1] / "src" / "neuroscifi" / "webcortex" / "bench" / "contradiction_paraphrase.json"
NEGATIVE = ("property", "subject", "unnamed", "unrelated")


def _cluster(statement, n):
    """A claim cluster as the evaluating stage would hand it to consolidation: two independent sources stating
    `statement`, key, values and polarity derived by the real functions."""
    from neuroscifi.webcortex import claims, text
    from neuroscifi.webcortex.types import Claim, ClaimCluster
    key = claims.claim_key(statement) or statement.casefold()[:200]
    members = []
    for i, host in enumerate(("a.example", "b.example")):
        url = f"http://{host}/probe{n}"
        members.append(Claim(id=hashlib.sha256(f"{url}\n{statement}".encode()).hexdigest()[:24], text=statement,
                             source_url=url, domain=host, title="t", retrieved_at=T0, key=key, kind="statement",
                             polarity=-1 if claims.polarity(statement) < 0 else 1, values=text.values(statement),
                             published_at=T0 - 86400.0, relevance=0.8, flags={"source_type": "secondary"}))
    return ClaimCluster(key=key, claims=members, independent_sources=2, supporting=[c.id for c in members],
                        contradicting=[], confidence=0.85, uncertainty=0.1, status="corroborated",
                        statement=statement)


def _outcome(db, cluster, proposals):
    """(outcome, paired id, how) for one probe, by the code consolidation runs: 'same' | 'conflict' | 'series' |
    'none'."""
    from neuroscifi.webcortex import consolidate
    item = consolidate._existing(db, cluster) or consolidate._paired(db, cluster, proposals)
    if item is None:
        return "none", None, None
    values, polarity = consolidate._values(cluster)
    relation, _ = consolidate._compare(values, polarity, item)
    return relation, item["id"], item.get("paired_by", "words")


def bench_contradictions(args):
    from neuroscifi.inference import config as iconfig
    from neuroscifi.webcortex import consolidate
    data = json.loads(DATA.read_text())
    dev = set(data["development_subjects"])
    with tempfile.TemporaryDirectory(prefix="nsf-bench-knowledge-") as folder:
        db = store.create(Path(folder) / "i.sqlite", T0)
        state.set_config(db, {"enabled": True}, T0)
        ids = {}
        for fact in data["facts"]:
            done = knowledge.assert_fact(db, fact["statement"], T0)
            ids[fact["id"]] = done["id"]
        merged = len(set(ids.values())) != len(ids)
        semantic_on = bool(args.embed_url)
        if semantic_on:
            key_file = os.path.expanduser(args.key_file) if args.key_file else None
            for provider, url, model in (("embed", args.embed_url, args.embed_model),
                                         ("rerank", args.rerank_url, args.rerank_model)):
                if url:
                    iconfig.set_config(db, {"base_url": url, "model": model} |
                                       ({"key_file": key_file} if key_file else {}), provider=provider)
            if args.llm_url:
                iconfig.set_config(db, {"base_url": args.llm_url, "model": args.llm_model} | (
                    {"key_file": os.path.expanduser(args.llm_key_file)} if args.llm_key_file else {}))
            changes = {"enabled": True, "contradictions": True, "pair_check": bool(args.llm_url)}
            if args.config:
                changes.update(json.loads(args.config))
            semantic.set_config(db, changes)
        subject_of = {f["id"]: f["subject"] for f in data["facts"]}
        rows, latencies = [], []
        for n, probe in enumerate(data["probes"]):
            cluster = _cluster(probe["statement"], n)
            start = time.perf_counter()
            proposals = consolidate.propose(db, [cluster], sweep=args.sweep) if semantic_on else {}
            latencies.append((time.perf_counter() - start) * 1000)
            outcome, paired, how = _outcome(db, cluster, proposals)
            gold_id = ids.get(probe.get("fact"))
            ok = outcome == probe["gold"] and (probe["gold"] == "none" or paired == gold_id)
            subject = probe.get("subject") or subject_of.get(probe.get("fact"))
            row = {"class": probe["class"], "statement": probe["statement"], "gold": probe["gold"],
                   "outcome": outcome, "propose_ms": round(latencies[-1], 1), "paired_with": next((k for k, v in ids.items() if v == paired), None),
                   "paired_by": how, "right": ok, "half": "development" if subject in dev else "held_out"}
            trace = (proposals.get("trace") or {}).get(cluster.key) if semantic_on else None
            if trace:
                row["trace"] = trace
            rows.append(row)
        info = semantic.status(db) if semantic_on else None
        db.close()
    semantic.clear()

    def summary(part):
        out = {}
        for cls in ("restate", "contradict", "units", "alias", "property", "subject", "unnamed", "unrelated"):
            got = [r for r in part if r["class"] == cls]
            if not got:
                continue
            entry = {"probes": len(got), "right": sum(r["right"] for r in got)}
            if cls in NEGATIVE:
                entry["paired"] = sum(r["outcome"] != "none" for r in got)
                entry["false_same"] = sum(r["outcome"] == "same" for r in got)
                entry["false_conflict"] = sum(r["outcome"] in ("conflict", "series") for r in got)
            else:
                entry["missed"] = sum(r["outcome"] == "none" for r in got)
                entry["wrong_relation_or_item"] = sum(r["outcome"] != "none" and not r["right"] for r in got)
            out[cls] = entry
        positives = [r for r in part if r["class"] not in NEGATIVE]
        negatives = [r for r in part if r["class"] in NEGATIVE]
        paired_right = sum(r["right"] for r in positives)
        paired_all = sum(r["outcome"] != "none" for r in part)
        out["all"] = {"positives": len(positives), "positives_right": paired_right,
                      "recall": round(paired_right / len(positives), 3) if positives else None,
                      "negatives": len(negatives), "negatives_paired": sum(r["outcome"] != "none" for r in negatives),
                      "pairs_made": paired_all,
                      "pair_precision": round(sum(r["right"] for r in part if r["outcome"] != "none") / paired_all, 3)
                      if paired_all else None}
        return out
    by_outcome = {}
    for r in rows:
        by_outcome.setdefault("paired" if r["outcome"] != "none" else "not_paired", []).append(r["propose_ms"])
    latencies.sort()
    return {"benchmark": "contradictions in other words", "label": args.label,
            "facts": len(data["facts"]), "facts_merged_by_the_write_path": merged, "probes": len(rows),
            "semantic": semantic_on, "semantic_status": info,
            "propose_ms": {"p50": round(latencies[len(latencies) // 2], 2),
                           "p95": round(latencies[int(len(latencies) * .95)], 2),
                           "max": round(latencies[-1], 2),
                           "median_when": {k: sorted(v)[len(v) // 2] for k, v in by_outcome.items()}}
            if semantic_on else None,
            "summary": summary(rows),
            "by_half": {half: summary([r for r in rows if r["half"] == half])
                        for half in ("development", "held_out")},
            "probes_detail": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("index")
    p.add_argument("--sizes", type=int, nargs="+", default=[1000, 20000, 100000])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--out")
    p = sub.add_parser("contradictions")
    p.add_argument("--embed-url")
    p.add_argument("--embed-model", default="qwen3-embedding-0.6b")
    p.add_argument("--rerank-url")
    p.add_argument("--rerank-model", default="qwen3-reranker-0.6b")
    p.add_argument("--key-file")
    p.add_argument("--llm-url", help="the answer model that confirms each pair (pair_check); without it the "
                                     "memory models' proposals are taken as they are")
    p.add_argument("--llm-model", default="qwen3-30b-a3b-instruct-2507-fp8")
    p.add_argument("--llm-key-file")
    p.add_argument("--config", help="JSON of further semantic settings, for example '{\"pair_floor\": 0.5}'")
    p.add_argument("--sweep", action="store_true", help="record every probe's scores (no threshold applied)")
    p.add_argument("--label", default="run")
    p.add_argument("--out")
    args = ap.parse_args(argv)
    result = bench_index(args.sizes, args.repeats) if args.mode == "index" else bench_contradictions(args)
    text = json.dumps(result, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n")
        brief = {k: v for k, v in result.items() if k not in ("probes_detail",)}
        print(json.dumps(brief, indent=1, sort_keys=True))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
