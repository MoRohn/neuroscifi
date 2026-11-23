#!/usr/bin/env python3
"""Memory retrieval benchmark: does knowledge.query return the right fact, and nothing when nothing is known?

  python scripts/bench_memory.py --label baseline --out memory.json
  python scripts/bench_memory.py --config '{"base_url": "http://127.0.0.1:8813/v1", "model": "..."}' ...

A temporary individual is given the 48 fictional facts of webcortex/bench/memory_paraphrase.json as owner
assertions (the real write path). Each query then goes through knowledge.query (the real read path) and is scored
by class:

  near    the fact's own vocabulary, inflected or reordered
  para    the same question in other words (the subject is named, the predicate is not)
  noname  a description that does not name the subject
  none    nothing in memory answers it: the right result is no item

Reported per class: recall@1, recall@3 (|gold in top 3| / min(3, |gold|)), mean reciprocal rank, and for `none`
the share of queries that still returned something. Query latency is the wall time of knowledge.query. `--config`
is passed to the semantic-retrieval settings when the installation has them; without it the run measures the
lexical ranking alone.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.instance import store                                   # noqa: E402
from neuroscifi.webcortex import knowledge, state                       # noqa: E402
from neuroscifi.webcortex.bench import environment                      # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "src/neuroscifi/webcortex/bench/memory_paraphrase.json"
T0 = 1_795_000_000.0


def percentile(values, q):
    data = sorted(values)
    if not data:
        return None
    k = (len(data) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(data) - 1)
    return round(data[lo] + (data[hi] - data[lo]) * (k - lo), 3)


def seed(db, facts: dict) -> dict:
    """Assert every fact; return knowledge id -> fact id. A fact the write path merges into another is reported."""
    ids, merged = {}, []
    for n, (fact_id, statement) in enumerate(facts.items()):
        out = knowledge.assert_fact(db, statement, T0 + n)
        if out["status"] != "accepted" or out.get("superseded"):
            merged.append({"fact": fact_id, "result": out["status"], "superseded": out.get("superseded")})
        ids[out["id"]] = fact_id
    return {"ids": ids, "merged": merged}


def run(args) -> dict:
    data = json.loads(DATA.read_text())
    with tempfile.TemporaryDirectory(prefix="nsf-memorybench-") as directory:
        db = store.create(Path(directory) / "memory.sqlite", T0, note="scripts/bench_memory.py (fictional facts)")
        state.set_config(db, {"enabled": True}, T0)
        configured = None
        if args.config:
            from neuroscifi.webcortex import semantic                   # absent before semantic retrieval exists
            configured = semantic.set_config(db, json.loads(args.config), T0).to_json()
        seeded = seed(db, data["facts"])
        knowledge.query(db, "warm up", T0 + 1000)                       # first call pays any index build
        rows, now = [], T0 + 2000
        for q in data["queries"]:
            started = time.perf_counter()
            found = knowledge.query(db, q["query"], now, limit=args.limit)
            elapsed = (time.perf_counter() - started) * 1000
            ranked = [seeded["ids"].get(i["id"]) for i in found["items"]]
            gold = set(q["gold"])
            first = next((k + 1 for k, f in enumerate(ranked) if f in gold), None)
            rows.append({"class": q["class"], "query": q["query"], "gold": q["gold"], "ranked": ranked[:5],
                         "returned": len(ranked), "first_relevant_rank": first, "ms": round(elapsed, 3),
                         "top_score": found["items"][0].get("score") if found["items"] else None,
                         "retrieval": found.get("retrieval")})
        db.close()
    return {"benchmark": "memory-retrieval", "version": 1, "label": args.label,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "environment": environment(),
            "facts": len(data["facts"]), "facts_merged_by_the_write_path": seeded["merged"],
            "semantic_config": configured, "limit": args.limit, "summary": summarise(rows), "queries": rows}


def summarise(rows) -> dict:
    by_class = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    out = {}
    for name, group in by_class.items():
        if name == "none":
            out[name] = {"queries": len(group),
                         "returned_something": sum(1 for r in group if r["returned"]),
                         "false_positive_rate": round(sum(1 for r in group if r["returned"]) / len(group), 3)}
            continue
        recall1 = sum(1 for r in group if r["first_relevant_rank"] == 1) / len(group)
        recall3 = sum(len(set(r["gold"]) & set(r["ranked"][:3])) / min(3, len(r["gold"])) for r in group) / len(group)
        mrr = sum(1.0 / r["first_relevant_rank"] for r in group if r["first_relevant_rank"]) / len(group)
        out[name] = {"queries": len(group), "recall_at_1": round(recall1, 3), "recall_at_3": round(recall3, 3),
                     "mrr": round(mrr, 3), "not_found": sum(1 for r in group if not r["first_relevant_rank"])}
    answerable = [r for r in rows if r["class"] != "none"]
    out["all_answerable"] = {
        "queries": len(answerable),
        "recall_at_1": round(sum(1 for r in answerable if r["first_relevant_rank"] == 1) / len(answerable), 3),
        "recall_at_3": round(sum(len(set(r["gold"]) & set(r["ranked"][:3])) / min(3, len(r["gold"]))
                                 for r in answerable) / len(answerable), 3),
        "mrr": round(sum(1.0 / r["first_relevant_rank"] for r in answerable if r["first_relevant_rank"])
                     / len(answerable), 3)}
    out["query_ms"] = {"p50": percentile([r["ms"] for r in rows], 0.5), "p95": percentile([r["ms"] for r in rows], 0.95),
                       "max": max(r["ms"] for r in rows)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", default="run")
    ap.add_argument("--limit", type=int, default=10, help="knowledge.query limit (default 10, its own default)")
    ap.add_argument("--config", help="JSON for the semantic-retrieval settings (omit to measure lexical ranking)")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = run(args)
    text = json.dumps(result, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    print(json.dumps({k: v for k, v in result["summary"].items()}, indent=1), file=sys.stderr)
    if result["facts_merged_by_the_write_path"]:
        print(f"note: {len(result['facts_merged_by_the_write_path'])} fact(s) were merged by assert_fact", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
