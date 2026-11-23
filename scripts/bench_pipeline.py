#!/usr/bin/env python3
"""End-to-end research benchmark with the live model: the offline corpus through the real orchestrator.

Retrieval, extraction, claims and verification run on the fictional offline corpus of `nsf web bench` (loopback
fixture, no internet). The reasoning step calls the configured model endpoint for real, so a task's wall time splits
into orchestration and model time, and the answer quality the guard lets through is counted:

  python scripts/bench_pipeline.py --url http://127.0.0.1:8811/v1 --model qwen3-30b-a3b-instruct-2507-fp8 \
      --key-file ~/.config/neuroscifi/inference/api_key --label baseline --out pipeline.json

Per task: status, wall seconds, every model call from the inference ledger (purpose, latency, first token, tokens),
the reasoning status (model answer or the extractive template, and why), sentences kept and rejected by the guard,
and what the self-critique did. The individual is temporary and removed afterwards. The corpus is fictional, so
`--texts` may keep the answer sentences for a side-by-side comparison of two runs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.inference import config as iconfig                     # noqa: E402
from neuroscifi.inference.ledger import Ledger, ledger_path           # noqa: E402
from neuroscifi.webcortex.bench import corpus as C, environment       # noqa: E402
from neuroscifi.webcortex.bench.offline import _Bench                 # noqa: E402


def percentile(values, q):
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    k = (len(data) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(data) - 1)
    return round(data[lo] + (data[hi] - data[lo]) * (k - lo), 3)


def summary(values):
    data = [v for v in values if v is not None]
    return {"n": len(data), "p50": percentile(data, 0.5), "p95": percentile(data, 0.95),
            "p99": percentile(data, 0.99), "mean": round(sum(data) / len(data), 3) if data else None}


def run(args) -> dict:
    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    tasks = []
    try:
        with tempfile.TemporaryDirectory(prefix="nsf-pipelinebench-") as directory:
            bench = _Bench(Path(directory), web, quick=args.quick)
            try:
                changes = {"base_url": args.url, "model": args.model}
                if args.key_file:
                    changes["key_file"] = os.path.expanduser(args.key_file)
                changes.update(json.loads(args.config) if args.config else {})
                iconfig.set_config(bench.db, changes, C.T0)
                ledger = Ledger(ledger_path(bench.path))
                chosen = [q for q in C.QUESTIONS if not args.quick or q["id"] in C.QUICK_QUESTIONS]
                for n, q in enumerate(chosen[:args.limit] if args.limit else chosen, 1):
                    seen = {r["id"] for r in ledger.recent(10000)}
                    rec = bench.run_task(q["id"], q["question"])
                    calls = [r for r in reversed(ledger.recent(10000)) if r["id"] not in seen]
                    tasks.append(task_row(q, rec, calls, args.texts))
                    print(f"{n:3d} {q['id']} {rec['status']:9s} {rec['wall_seconds']:6.2f}s "
                          f"{tasks[-1]['reasoning_status']}", file=sys.stderr, flush=True)
            finally:
                bench.close()
    finally:
        web.stop()
    return {"benchmark": "pipeline-live-model", "version": 1, "label": args.label, "url": args.url,
            "model": args.model, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "environment": environment(), "corpus": "nsf web bench offline corpus (fictional; loopback fixture)",
            "summary": summarise(tasks), "tasks": tasks}


def task_row(q, rec, calls, texts):
    reasoning = (rec["report"] or {}).get("reasoning") or {}
    model_ms = sum(c["latency_ms"] or 0.0 for c in calls)
    row = {"id": q["id"], "category": q.get("category"), "mode": rec["mode"], "status": rec["status"],
           "wall_s": round(rec["wall_seconds"], 3), "model_s": round(model_ms / 1000, 3),
           "orchestration_s": round(rec["wall_seconds"] - model_ms / 1000, 3),
           "reasoning_status": reasoning.get("status") or "none", "reason": reasoning.get("reason"),
           "kept": len(reasoning.get("answer") or []), "rejected": len(reasoning.get("rejected") or []),
           "critique": reasoning.get("critique"),
           "flagged": sum(1 for s in reasoning.get("answer") or [] if s.get("flag")),
           "calls": [{k: c[k] for k in ("purpose", "status", "latency_ms", "first_token_ms", "prompt_tokens",
                                         "completion_tokens", "tokens_per_second", "attempts")} for c in calls]}
    if texts:
        row["answer"] = [{"text": s["text"], "claims": s["claims"], "flag": s.get("flag")}
                         for s in reasoning.get("answer") or []]
        row["rejected_sentences"] = reasoning.get("rejected") or []
    return row


def summarise(tasks):
    def calls(purpose, field):
        return [c[field] for t in tasks for c in t["calls"] if c["purpose"] == purpose and c["status"] == "ok"]
    with_model = [t for t in tasks if t["calls"]]
    answered = [t for t in tasks if t["reasoning_status"] == "model"]
    critiques = [t["critique"] for t in tasks if t["critique"]]
    wall = sum(t["wall_s"] for t in with_model) or 1.0
    return {
        "tasks": len(tasks), "task_status": dict(Counter(t["status"] for t in tasks)),
        "reasoning_status": dict(Counter(t["reasoning_status"] for t in tasks)),
        "model_answers": len(answered),
        "kept_sentences_per_model_answer": summary([t["kept"] for t in answered]),
        "sentences": {"kept": sum(t["kept"] for t in tasks), "rejected": sum(t["rejected"] for t in tasks)},
        "wall_s_tasks_with_model_calls": summary([t["wall_s"] for t in with_model]),
        "orchestration_s": summary([t["orchestration_s"] for t in with_model]),
        "share_of_wall": {
            "answer_call": round(sum(calls("research_answer", "latency_ms")) / 1000 / wall, 3),
            "critique_call": round(sum(calls("research_critique", "latency_ms")) / 1000 / wall, 3),
            "orchestration": round(sum(t["orchestration_s"] for t in with_model) / wall, 3)},
        "answer_call": {"latency_ms": summary(calls("research_answer", "latency_ms")),
                        "first_token_ms": summary(calls("research_answer", "first_token_ms")),
                        "prompt_tokens": summary(calls("research_answer", "prompt_tokens")),
                        "completion_tokens": summary(calls("research_answer", "completion_tokens")),
                        "tokens_per_second": summary(calls("research_answer", "tokens_per_second"))},
        "critique_call": {"latency_ms": summary(calls("research_critique", "latency_ms")),
                          "prompt_tokens": summary(calls("research_critique", "prompt_tokens")),
                          "completion_tokens": summary(calls("research_critique", "completion_tokens"))},
        "critique_outcomes": {"ran": sum(1 for c in critiques if c.get("status") == "ok"),
                              "status": dict(Counter(c.get("status") for c in critiques)),
                              "sentences_removed": sum(c.get("removed") or 0 for c in critiques),
                              "sentences_flagged": sum(c.get("flagged") or 0 for c in critiques)},
        "model_call_errors": dict(Counter(c["status"] for t in tasks for c in t["calls"] if c["status"] != "ok")),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--key-file")
    ap.add_argument("--config", help="JSON of further inference settings, for example '{\"self_critique\": false}'")
    ap.add_argument("--label", default="run")
    ap.add_argument("--quick", action="store_true", help="the 7-question subset")
    ap.add_argument("--limit", type=int, default=0, help="at most this many questions")
    ap.add_argument("--texts", action="store_true", help="keep answer sentences (the corpus is fictional)")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = run(args)
    text = json.dumps(result, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    print(json.dumps(result["summary"], indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
