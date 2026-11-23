"""`nsf web bench`: the Web Cortex benchmark (offline corpus) and the bounded live operational validation.

    run(quick=False, repeat=3)            -> offline benchmark result (dict, see offline.py)
    run(live=True)                        -> live validation result (dict, see live.py)
    run(read_ahead=N)                     -> offline benchmark with config.read_ahead N in every bench individual
    run(latency_ms=300, windows=(1, 4))   -> task wall time with delayed fixture responses per read-ahead window
    main(argv)                            -> CLI: --out PATH, --repeat N, --quick, --live, --live-port PORT,
                                             --read-ahead N, --latency-ms MS [--windows 1,4]

The offline benchmark is deterministic by design: it runs `repeat` times and reports whether every quality metric
and per-item outcome was identical across runs (wall-clock latency, RSS and file sizes are excluded from that
comparison and reported separately). Results are measurements of this corpus, not of the open web.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys

DEFAULT_DIR = Path("docs/quality/evidence")
OFFLINE_FILE = "webcortex_bench.json"
LIVE_FILE = "webcortex_live.json"


def _version(name):
    from importlib import metadata
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def environment() -> dict:
    repo = Path(__file__).resolve().parents[4]
    commit = dirty = None
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True,
                                timeout=5).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
                                    timeout=5).stdout.strip()) if commit else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"host_digest": hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12],
            "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
            "packages": {n: _version(n) for n in ("neuroscifi", "scrapling", "trafilatura", "lxml", "pypdf",
                                                    "markdownify", "playwright")},
            "git_commit": commit, "git_dirty": dirty}


def run(*, quick: bool = False, repeat: int = 3, live: bool = False, live_port: int = 3291,
        read_ahead: int | None = None, latency_ms: float | None = None, windows=(1, 4)) -> dict:
    """Run the offline benchmark (`repeat` times; read_ahead: config.read_ahead of the bench individuals, None for
    the default), with latency_ms the read-ahead latency measurement (offline.latency) instead, or with live=True
    the live operational validation."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if live:
        from . import live as live_mod
        return {"benchmark": "webcortex-live", "version": 1, "generated_at": stamp, "environment": environment(),
                **live_mod.run(port=live_port)}
    from . import offline
    if latency_ms is not None:
        return {"benchmark": "webcortex-latency", "version": 1, "quick": quick, "generated_at": stamp,
                "environment": environment(),
                **offline.latency(delay=float(latency_ms) / 1000.0, windows=tuple(windows), quick=quick)}
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    runs = [offline.run_once(quick=quick, read_ahead=read_ahead) for _ in range(repeat)]
    first = runs[0]
    differences = sorted({path for r in runs[1:] for path in offline.diff(first["deterministic"], r["deterministic"])})
    return {"benchmark": "webcortex-offline", "version": 1, "label": offline.LABEL, "quick": quick,
            **({} if read_ahead is None else {"read_ahead": read_ahead}),
            "generated_at": stamp, "environment": environment(), "corpus": offline.corpus_summary(),
            "metrics": first["deterministic"], "timing": [r["timing"] for r in runs],
            "resources": [r["resources"] for r in runs],
            "regression": {"runs": repeat, "identical": not differences, "differences": differences[:50],
                           "compared": "every key under 'metrics' (quality metrics and per-item outcomes); timing "
                                       "and resources are reported per run and not compared"},
            "safety": safety_summary(first["deterministic"])}


def safety_summary(m: dict) -> dict:
    """The invariants that must hold whatever the accuracy numbers are."""
    probes = m["probes"]
    checks = m["checks"]
    out = {
        "ssrf_all_blocked": all(p["contained"] for p in probes["probes"] if p["group"] == "ssrf"),
        "robots_respected": all(p["contained"] for p in probes["probes"] if p["group"] == "robots"),
        "limits_enforced": all(p["contained"] for p in probes["probes"] if p["group"] == "limits"),
        "backoff_kept_off_network": probes["backoff_request_reached_server"] == 0,
        "injection_contained": m["injection"]["contained"],
        "budgets_respected": m["budgets"]["ok"] and all(t["within"] for t in checks["tight_budgets"]),
        "live_flag_honest": m["liveness"]["ok"],
        "cancel_queued": checks["cancel_queued"]["ok"],
        "cancel_running": checks["cancel_running"]["ok"],
        "crash_resume": checks["crash_resume"]["ok"],
    }
    out["all_hold"] = all(out.values())
    return out


def _headline(result: dict) -> str:
    if result["benchmark"] == "webcortex-latency":
        lines = [f"Web Cortex read-ahead latency (every fixture response delayed {result['delay_seconds']:g} s)"]
        for window, row in result["windows"].items():
            t = row["task_seconds"]
            lines.append(f"  read_ahead {window}: task p50 {t['p50']} s, p95 {t['p95']} s; {row['requests_used']} "
                         f"requests used, {row['prefetch_unused_requests']} sent ahead and unused")
        lines.append(f"  identical task outcomes: {result['identical_outcomes']}")
        return "\n".join(lines)
    if result["benchmark"] == "webcortex-live":
        lines = ["Web Cortex live validation (real internet):"]
        for name, sc in (result.get("scenarios") or {}).items():
            lines.append(f"  {name}: {sc.get('status')} — {sc.get('summary', '')}")
        lines.append(f"  requests: {result.get('totals', {}).get('requests')}")
        return "\n".join(lines)
    m = result["metrics"]
    auth, time_, ver = m["authoritative"], m["time_sensitive"], m["verification"]["confusion"]
    states = m["verification"].get("states") or {"correct": None, "n": None}
    con = m["contradictions"]
    pcs, uat = m["citations"]["per_cited_source"], m["uat"]
    lines = [f"Web Cortex offline benchmark ({'quick' if result['quick'] else 'full'}; {result['corpus']['pages']} "
             f"pages, {result['corpus']['hosts']} hosts; fictional corpus, no network)",
             f"  retrieval success        {auth['retrieval_success']['n']}/{auth['retrieval_success']['of']}",
             f"  evidence coverage        {auth['evidence_coverage']['found']}/{auth['evidence_coverage']['gold']}",
             f"  answer correct (top)     {auth['answer_correct_top']['n']}/{auth['answer_correct_top']['of']}",
             f"  citation precision       {pcs['supported_citations']}/{pcs['citations']} cited sources contain "
             f"the quote; {pcs['items_every_source_supports']}/{pcs['items']} items fully supported",
             f"  citation support (legacy, any source) {m['citations']['supported']}/{m['citations']['quote_items']}",
             f"  freshness compliance     {time_['freshness_compliance']['n']}/{time_['freshness_compliance']['of']}",
             f"  verdict accuracy         {ver['correct']}/{ver['n']} (legacy labels)",
             f"  verification states      {states['correct']}/{states['n']}",
             f"  contradiction e2e P/R    {con['end_to_end']['all']['precision']}/{con['end_to_end']['all']['recall']}",
             f"  memory recall@3 / MRR    {m['memory']['recall_at_3']} / {m['memory']['mrr']}",
             f"  fact-check discrimination {uat['fact_check_discrimination']['discriminated']}/"
             f"{uat['fact_check_discrimination']['pairs']} true/false pairs",
             f"  latest version correct   {uat['latest_version']['correct_top']}/{uat['latest_version']['questions']}",
             f"  top answer on-subject    {uat['top_on_subject']['on_subject']}/{uat['top_on_subject']['questions']}",
             f"  cross-subject merges     {uat['cross_subject']['answer_items_merged']} answer items, "
             f"{uat['cross_subject']['findings_merged']} findings",
             f"  safety invariants hold   {result['safety']['all_hold']}",
             f"  identical across {result['regression']['runs']} run(s): {result['regression']['identical']}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nsf web bench",
        description="Web Cortex benchmark. Default: the offline corpus benchmark (no network, deterministic). "
                    "--live: the bounded live validation against the real internet (temporary individual).")
    parser.add_argument("--out", type=Path, help=f"JSON output path (default: {DEFAULT_DIR}/{OFFLINE_FILE} or "
                        f"{LIVE_FILE} when that directory exists here; otherwise the JSON is printed)")
    parser.add_argument("--repeat", type=int, default=3, help="offline runs compared for determinism (default 3)")
    parser.add_argument("--quick", action="store_true", help="small offline subset (used by the test suite)")
    parser.add_argument("--live", action="store_true", help="run the live operational validation instead")
    parser.add_argument("--live-port", type=int, default=3291, help="loopback port for the open-webSearch daemon")
    parser.add_argument("--read-ahead", type=int, help="config.read_ahead of the bench individuals (default: the "
                                                       "configuration default)")
    parser.add_argument("--latency-ms", type=float, help="instead: task wall time with every fixture response "
                                                         "delayed this long, per read-ahead window")
    parser.add_argument("--windows", default="1,4", help="--latency-ms: read-ahead windows, comma-separated")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        windows = tuple(int(w) for w in args.windows.split(","))
    except ValueError:
        parser.error("--windows expects comma-separated integers")
    result = run(quick=args.quick, repeat=args.repeat, live=args.live, live_port=args.live_port,
                 read_ahead=args.read_ahead, latency_ms=args.latency_ms, windows=windows)
    out = args.out
    if out is None and DEFAULT_DIR.is_dir():
        out = DEFAULT_DIR / (LIVE_FILE if args.live else OFFLINE_FILE)
    text = json.dumps(result, indent=1, sort_keys=False, default=str) + "\n"
    if out is None:
        sys.stdout.write(text)
        print(_headline(result), file=sys.stderr)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(_headline(result))
        print(f"written: {out}")
    if args.live:
        return 0 if result.get("completed") else 1
    if args.latency_ms is not None:
        return 0 if result["identical_outcomes"] else 1
    return 0 if result["safety"]["all_hold"] else 1


__all__ = ["main", "run", "environment", "safety_summary"]
