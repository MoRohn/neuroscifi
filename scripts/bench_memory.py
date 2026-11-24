#!/usr/bin/env python3
"""Memory retrieval benchmark: does knowledge.query return the right fact, and nothing when nothing is known?

  python scripts/bench_memory.py --label baseline --out memory.json
  python scripts/bench_memory.py --embed-url http://127.0.0.1:18441/v1 --rerank-url http://127.0.0.1:18442/v1 \
      --key-file ~/.config/neuroscifi/memory/api_key --label semantic --out memory_semantic.json

A temporary individual is given the 48 fictional facts of webcortex/bench/memory_paraphrase.json as owner
assertions (the real write path). Each query then goes through knowledge.query (the real read path) and is scored
by class:

  near    the fact's own vocabulary, inflected or reordered
  para    the same question in other words (the subject is named, the predicate is not)
  noname  a description that does not name the subject
  none    nothing in memory answers it: the right result is no item

Reported per class: recall@1, recall@3 (|gold in top 3| / min(3, |gold|)), mean reciprocal rank, and for `none`
the share of queries that still returned something. Query latency is the wall time of knowledge.query. With
`--embed-url` (and optionally `--rerank-url`) the run turns semantic retrieval on for its temporary individual;
without it the run measures the lexical ranking alone. Results are also given per half: any threshold is chosen on
the development subjects and the held-out subjects show whether it carries over.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
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
        if args.embed_url:
            from neuroscifi.inference import config as iconfig
            from neuroscifi.webcortex import semantic
            key = {"key_file": os.path.expanduser(args.key_file)} if args.key_file else {}
            iconfig.set_config(db, {"base_url": args.embed_url, "model": args.embed_model} | key, T0, provider="embed")
            if args.rerank_url:
                iconfig.set_config(db, {"base_url": args.rerank_url, "model": args.rerank_model} | key, T0,
                                   provider="rerank")
            semantic.clear()
            configured = semantic.set_config(db, {"enabled": True} | (json.loads(args.config) if args.config else {}),
                                             T0).to_json() | {"embed_model": args.embed_model,
                                                              "rerank_model": args.rerank_model if args.rerank_url else None}
        seeded = seed(db, data["facts"])
        started = time.perf_counter()
        knowledge.query(db, "warm up", T0 + 1000)                       # first call embeds every statement
        first_query_ms = round((time.perf_counter() - started) * 1000, 1)
        development = set(data.get("development_subjects") or [])
        rows, now = [], T0 + 2000
        for q in data["queries"]:
            started = time.perf_counter()
            found = knowledge.query(db, q["query"], now, limit=args.limit)
            elapsed = (time.perf_counter() - started) * 1000
            ranked = [seeded["ids"].get(i["id"]) for i in found["items"]]
            gold = set(q["gold"])
            first = next((k + 1 for k, f in enumerate(ranked) if f in gold), None)
            half = ("development" if q["gold"][0].split(".")[0] in development else "held_out") if q["gold"] \
                else ("development" if len(rows) % 2 == 0 else "held_out")
            rows.append({"class": q["class"], "half": half, "query": q["query"], "gold": q["gold"], "ranked": ranked[:5], "all": ranked,
                         "returned": len(ranked), "first_relevant_rank": first, "ms": round(elapsed, 3),
                         "top_score": found["items"][0].get("score") if found["items"] else None,
                         "retrieval": found.get("retrieval")})
        db.close()
    return {"benchmark": "memory-retrieval", "version": 1, "label": args.label,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "environment": environment(),
            "facts": len(data["facts"]), "facts_merged_by_the_write_path": seeded["merged"],
            "semantic_config": configured, "limit": args.limit, "first_query_ms": first_query_ms,
            "summary": summarise(rows),
            "by_half": {half: summarise([r for r in rows if r["half"] == half]) for half in ("development", "held_out")},
            "queries": rows}


def gate(args) -> dict:
    """The memory-answer decision on every near and para query against the four facts of its subject: the lexical
    test (intent.covers, what 0.7.0 decides with) and the answer model's check (memory_gate.confirm). A right
    decision accepts the fact that answers the question and refuses the subject's three other facts."""
    from neuroscifi.inference import config as iconfig
    from neuroscifi.inference.gateway import Gateway
    from neuroscifi.webcortex import intent, memory_gate
    data = json.loads(DATA.read_text())
    cfg = iconfig.InferenceConfig(base_url=args.llm_url, model=args.llm_model,
                                  key_file=os.path.expanduser(args.llm_key_file or ""))
    gateway = Gateway(cfg, key=iconfig.read_key(cfg))
    ids = list(data["facts"])
    counts = {name: {"right_fact_accepted": 0, "right_facts": 0, "other_property_accepted": 0, "other_properties": 0}
              for name in ("lexical_covers", "model_check")}
    latency, skipped, mistakes = [], 0, []
    for q in data["queries"]:
        if q["class"] not in ("near", "para"):
            continue
        subject = q["gold"][0].split(".")[0]
        items = [{"id": n, "fact": f, "statement": data["facts"][f]} for n, f in enumerate(ids)
                 if f.startswith(subject + ".")]
        started = time.perf_counter()
        verdict = memory_gate.confirm(None, None, q["query"], items, gateway=gateway)
        latency.append((time.perf_counter() - started) * 1000)
        if verdict["status"] != "checked":
            skipped += 1
            continue
        for item in items:
            right = item["fact"] in q["gold"]
            for name, accepted in (("lexical_covers", intent.covers(item, q["query"])),
                                   ("model_check", item["id"] in verdict["confirmed"])):
                c = counts[name]
                c["right_facts" if right else "other_properties"] += 1
                c["right_fact_accepted" if right else "other_property_accepted"] += accepted
                if name == "model_check" and accepted != right:
                    mistakes.append({"query": q["query"], "fact": item["fact"], "accepted": accepted})
    return {"benchmark": "memory-answer-check", "version": 1, "label": args.label, "model": args.llm_model,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "environment": environment(),
            "queries": len(latency), "facts_per_query": 4, "skipped": skipped, "decisions": counts,
            "model_check_ms": {"p50": percentile(latency, 0.5), "p95": percentile(latency, 0.95)},
            "model_check_mistakes": mistakes,
            "note": "The model check is applied only to statements the lexical test chose, so in use a statement "
                    "is accepted when both accept it; the two are shown separately here."}


def summarise(rows) -> dict:
    by_class = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    out = {}
    for name, group in by_class.items():
        if name == "topic":      # the subject's name alone: all of its facts, and nothing else
            whole = sum(1 for r in group if set(r["gold"]) <= set(r["ranked"][:len(r["gold"])]))
            out[name] = {"queries": len(group), "all_facts_on_top": round(whole / len(group), 3),
                         "recall_of_facts": round(sum(len(set(r["gold"]) & set(r["ranked"])) / len(r["gold"])
                                                      for r in group) / len(group), 3),
                         "other_subjects_returned_mean": round(sum(r["returned"] - len(set(r["gold"]) & set(
                             r["all"])) for r in group) / len(group), 2)}
            continue
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
    answerable = [r for r in rows if r["class"] not in ("none", "topic")]
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
    ap.add_argument("--embed-url", help="embedding endpoint: turns semantic retrieval on for the run")
    ap.add_argument("--embed-model", default="qwen3-embedding-0.6b")
    ap.add_argument("--rerank-url", help="reranker endpoint (optional)")
    ap.add_argument("--rerank-model", default="qwen3-reranker-0.6b")
    ap.add_argument("--key-file", help="API key file of the memory endpoints")
    ap.add_argument("--config", help="JSON of semantic-retrieval settings, e.g. '{\"floor\": 0.05}'")
    ap.add_argument("--gate", action="store_true", help="measure the memory-answer check instead (needs --llm-url)")
    ap.add_argument("--llm-url", help="--gate: the answer model's OpenAI-compatible base")
    ap.add_argument("--llm-model", default="qwen3-30b-a3b-instruct-2507-fp8")
    ap.add_argument("--llm-key-file")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.gate:
        if not args.llm_url:
            ap.error("--gate needs --llm-url")
        result = gate(args)
        text = json.dumps(result, indent=1, default=str)
        if args.out:
            Path(args.out).write_text(text + "\n")
        print(json.dumps({k: result[k] for k in ("decisions", "model_check_ms", "skipped", "model_check_mistakes")},
                         indent=1), file=sys.stderr)
        return 0
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
