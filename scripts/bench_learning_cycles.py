#!/usr/bin/env python3
"""The learning-cycles experiment (Wave 2 lane F; directive §7, deliverable F): does the recursive knowledge loop
improve knowledge over repeated cycles, and what does each of lane K's mechanisms add?

  python scripts/bench_learning_cycles.py run --split development                  # choose and debug here
  python scripts/bench_learning_cycles.py run --split held_out --out held.json      # report here, once
  python scripts/bench_learning_cycles.py verdict held.json                         # the pre-registered rule
  python scripts/bench_learning_cycles.py run --split development --cycles 3 --config baseline   # a quick look
  python scripts/bench_learning_cycles.py final --note TEXT --out /outside/the/tree.json          # THE look (below)

Wave 3 lane R (ADR-060), the re-check orders on lane R's worlds (cycles_rate_splits.py) and on lane F's:
  python scripts/bench_learning_cycles.py run --split rate:held_out --orders --out rate_held.json
  python scripts/bench_learning_cycles.py verdict --orders rate_held.json

Wave 3 lane V (ADR-062), the worker's idle investigation in the same daily allowance as the timer:
  python scripts/bench_learning_cycles.py run --split rate:held_out --idle --out idle_rate_held.json
  python scripts/bench_learning_cycles.py verdict --idle idle_rate_held.json
  python scripts/bench_learning_cycles.py idle --out idle.json        # every public split, references and verdicts

Offline and deterministic per seed (apart from wall seconds); no model is called and no connection is made. The worlds
are frozen in src/neuroscifi/acceptance/cognition/cycles_splits.py (committed before this harness); what is measured,
and the verdict rule, are in src/neuroscifi/acceptance/cognition/cycles.py. The cognitive benchmark's withheld family
is never run here.

`final` scores the WITHHELD learning-cycles worlds (cycles_withheld.py), and nothing else does: one look per run, for
the main session at the end of a wave (and Wave 3 step 0's look at the unchanged system). It refuses a dirty or
uncommitted tree, another tree's source, an --out inside this checkout and one it cannot write, and appends its line to
docs/quality/evidence/genesis_final_uses.jsonl before it scores anything. Default configurations: the no-loop
reference, `baseline` (every Wave 1 setting off) and `shipped` (every setting as a new individual has it).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance.cognition import cycles, scoring        # noqa: E402


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write(value, out):
    text = json.dumps(scoring.rounded(value), indent=1)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", choices=cycles.SPLITS, required=True,
                   help="rate:<split>: Wave 3 lane R's worlds (cycles_rate_splits.py)")
    r.add_argument("--orders", action="store_true",
                   help="lane R's configurations (cycles.order_configurations) instead of lane K's")
    r.add_argument("--idle", action="store_true",
                   help="lane V's configurations (cycles.idle_configurations): the worker's idle investigation")
    r.add_argument("--division", action="store_true",
                   help="Wave 4 lane A's configurations (cycles.division_configurations): the allowance division")
    r.add_argument("--config", action="append", help="repeatable; default every configuration")
    r.add_argument("--look", choices=sorted(cycles.LOOKS),
                   help="the configurations of a withheld look (cycles.LOOKS), on a public split: a dry run of it")
    r.add_argument("--cycles", type=int, help="shorten every world to this many cycles")
    r.add_argument("--out")
    v = sub.add_parser("verdict")
    v.add_argument("result")
    v.add_argument("--out")
    v.add_argument("--orders", action="store_true", help="lane R's rule (cycles.order_verdicts)")
    v.add_argument("--idle", action="store_true", help="lane V's rule (cycles.idle_verdicts)")
    v.add_argument("--division", action="store_true",
                   help="Wave 4 lane A's rule (cycles.division_verdicts) and the development choice")
    f = sub.add_parser("final")
    f.add_argument("--note", required=True)
    f.add_argument("--out", required=True, help="a path OUTSIDE this checkout; copy it in afterwards")
    f.add_argument("--config", action="append", help=f"repeatable; default {', '.join(cycles.FINAL_CONFIGS)}")
    f.add_argument("--look", choices=sorted(cycles.LOOKS),
                   help="every configuration of a named look (cycles.LOOKS), instead of --config")
    f.add_argument("--mechanism", default="baseline", help="the name the ledger line records")
    i = sub.add_parser("idle", help="lane V's measurement in one command: every public split with "
                                    "cycles.idle_configurations(), its references and cycles.idle_verdicts")
    i.add_argument("--out", required=True)
    i.add_argument("--split", action="append", choices=cycles.SPLITS, help="repeatable; default every public split")
    d = sub.add_parser("division", help="Wave 4 lane A's measurement in one command: a split of "
                                        "cycles_reach_splits.py with cycles.division_configurations(), its references, "
                                        "the verdicts and the development choice, written as a summary")
    d.add_argument("--split", required=True, choices=[s for s in cycles.SPLITS if s.startswith(cycles.REACH)])
    d.add_argument("--out", required=True)
    d.add_argument("--full", help="also write the whole run_split result here (not evidence: per-cycle histories)")
    c = sub.add_parser("division-cost", help="Wave 4 lane A: the allowance division's cost on a large individual")
    c.add_argument("--out")
    c.add_argument("--tasks", type=int, default=10_000)
    c.add_argument("--events", type=int, default=500_000)
    a = ap.parse_args(argv)
    if a.command == "division-cost":
        return division_cost(a)
    if a.command == "division":
        started = time.perf_counter()
        run = cycles.run_split(a.split, configs=cycles.division_configurations())
        run["references"] = cycles.run_references(a.split)
        if a.full:
            _write(run, a.full)
        out = {"label": "Wave 4 lane A: the division of the day's allowance between refreshing and reaching; simulated "
                        "worlds (cycles_reach_splits.py) through the real modules; offline; no model",
               "commit": _commit(), "environment": {"python": platform.python_version(),
                                                    "machine": platform.machine()}}
        out |= cycles.division_summary(run)
        out["wall_seconds"] = time.perf_counter() - started
        _write(out, a.out)
        return 0
    if a.command == "idle":
        return idle(a)
    if a.command == "verdict":
        if a.division:
            verdicts = cycles.division_verdicts(json.loads(Path(a.result).read_text(encoding="utf-8")))
            _write({"verdicts": verdicts, "choice": cycles.division_choice(verdicts)}, a.out)
            return 0
        rule = cycles.order_verdicts if a.orders else cycles.idle_verdicts if a.idle else cycles.verdicts
        _write(rule(json.loads(Path(a.result).read_text(encoding="utf-8"))), a.out)
        return 0
    known = cycles.configurations() | cycles.EXTRA
    configs = ((cycles.order_configurations() if a.orders else cycles.idle_configurations() if a.idle else
                cycles.division_configurations() if a.division else cycles.configurations())
               if a.command == "run" else {k: known[k] for k in cycles.FINAL_CONFIGS})
    if a.config and getattr(a, "look", None):
        ap.error("--look names the configurations itself; give it without --config")
    if a.config:
        unknown = sorted(set(a.config) - set(known))
        if unknown:
            ap.error(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(known)}")
        configs = {k: known[k] for k in a.config}
    if getattr(a, "look", None):
        configs = {k: known[k] for k in cycles.LOOKS[a.look]}
    if a.command == "final":
        return final(a, configs)
    started = time.perf_counter()
    out = {"label": "lane F learning cycles: a simulated world of fictional software releases driven through the "
                    "real modules (extraction, clustering, consolidation, predictions, the timer); offline; no "
                    "model; search, fetching and the owner substituted",
           "commit": _commit(), "environment": {"python": platform.python_version(), "machine": platform.machine()}}
    out |= cycles.run_split(a.split, configs=configs, cycles=a.cycles)
    if a.cycles is None:        # lane Y (H4): the references, from the world generator alone (no product code)
        out["references"] = cycles.run_references(a.split)
    if a.look:                  # the look's report fields, as final() writes them
        out |= cycles.look_view(out) | {"look_name": a.look}
    out["wall_seconds"] = time.perf_counter() - started
    _write(out, a.out)
    return 0


def idle(a) -> int:
    """Wave 3 lane V (ADR-062): the idle investigation on every public split (development first), each with its
    references and the verdict rule; the safety counts of every run summed per split."""
    started = time.perf_counter()
    out = {"label": "Wave 3 lane V: the worker's idle investigation in the learning cycles, inside the timer's daily "
                    "allowance; simulated worlds through the real modules; offline; no model",
           "commit": _commit(), "environment": {"python": platform.python_version(), "machine": platform.machine()},
           "runs": {}, "verdicts": {}, "safety_all_runs": {}}
    for split in a.split or ("development", cycles.RATE + "development", "held_out", cycles.RATE + "held_out"):
        run = cycles.run_split(split, configs=cycles.idle_configurations())
        run["references"] = cycles.run_references(split)
        run["commit"] = out["commit"]       # review3 LOW 6: every split names the commit it was measured on
        out["runs"][split] = run
        out["verdicts"][split] = cycles.idle_verdicts(run)
        rows = [r for c in run["configurations"].values() for w in c["worlds"].values() for r in w["per_seed"]]
        out["safety_all_runs"][split] = {"runs": len(rows)} | {k: sum(r[k] or 0 for r in rows)
                                                               for k in cycles.SAFETY + cycles.LOSSES}
    out["wall_seconds"] = time.perf_counter() - started
    _write(out, a.out)
    return 0


def division_cost(a) -> int:
    """Wave 4 lane A (ADR-064): the division's cost on a large staged individual (rows written directly: tasks, events,
    knowledge, open hypotheses with predictions, autonomous tasks of the last week with results and decisions): the
    wall time of each read the division makes, of autonomy.division per rule, and of an idle decision under "race" and
    "reserve" (each on a fresh copy). Every read is outside a write transaction (asserted)."""
    import random
    import shutil
    import tempfile
    from contextlib import closing
    from neuroscifi.instance import store
    from neuroscifi.webcortex import autonomy, state
    from neuroscifi.webcortex.types import MODES
    rng = random.Random(64)
    t0 = 1_800_000_000.0
    now = t0 + 400 * 86400
    n_tasks, n_events, n_knowledge, n_hyp, recent = a.tasks, a.events, 10_000, 3_000, 300
    out = {"rows": {"web_tasks": n_tasks, "web_events": n_events, "web_knowledge": n_knowledge,
                    "open hypotheses with a prediction": n_hyp, "autonomous tasks finished in the last 7 days": recent}}
    with tempfile.TemporaryDirectory(prefix="nsf-division-cost-") as directory:
        path = Path(directory) / "individual.sqlite"
        with closing(store.create(path, t0)) as db:
            state.set_config(db, {"enabled": True, "autonomous": True,
                                  "daily_autonomous": {"tasks": 100, "requests": 4000, "bytes": 800_000_000}}, t0)
            result = json.dumps({"consolidation": {"provisional": [1], "superseded": []}, "pad": "x" * 20_000})
            with store.write(db):
                db.executemany("INSERT INTO web_tasks(id, objective, mode, status, origin, created_at, updated_at, "
                               "finished_at, result) VALUES(?, 'q', ?, 'completed', ?, ?, ?, ?, ?)",
                               [(f"t{i}", MODES[i % len(MODES)],
                                 ("curiosity" if i % 2 else f"refresh:{i}") if i >= n_tasks - recent else "console",
                                 (now - (n_tasks - i) * 1800) if i >= n_tasks - recent else t0 + i * 3000,
                                 t0, (now - (n_tasks - i) * 1800) if i >= n_tasks - recent else t0 + i * 3000,
                                 result) for i in range(n_tasks)])
                db.executemany("INSERT INTO web_events(task_id, kind, detail, created_at) VALUES(?, ?, ?, ?)",
                               [(f"t{rng.randrange(n_tasks)}", "decision" if i % 5 == 0 else "stage",
                                 json.dumps({"autonomy": {"division": {"path": rng.choice(("reach", "refresh"))}}})
                                 if i % 5 == 0 else "{}", t0 + i * 60) for i in range(n_events)])
                db.executemany("INSERT INTO web_knowledge(id, key, version, statement, status, confidence, "
                               "uncertainty, freshness_class, first_verified_at, last_verified_at, task_id, "
                               "created_at, updated_at) VALUES(?, ?, 1, ?, 'accepted', 0.8, 0.2, 'stable', ?, ?, ?, "
                               "?, ?)", [(i + 1, f"k{i}", f"Subject{i} 1.{i % 9}.0 is the latest Subject{i} release.",
                                          now - 86400, now - 86400, f"t{rng.randrange(n_tasks)}", t0, t0)
                                         for i in range(n_knowledge)])
            from neuroscifi.webcortex import claims, evolution, text
            with store.write(db):
                for i in range(n_hyp):
                    s = f"The Project{i} compiler version 4.{i % 9}.0 was released in November"
                    hid, _ = evolution.open_hypothesis(
                        db, key=claims.claim_key(s), statement=s, origin="owner" if i % 10 == 0 else "gap",
                        verification={"policy": "ADR-035", "state": "not_evaluated", "basis": "owner_hypothesis",
                                      "confidence": 0.5}, claims=[], values=text.values(s),
                        polarity=claims.polarity(s), now=now - 3600, with_prediction=False)
                    evolution.predict(db, hid, now - 3600, deadline=now + 20 * 86400, probability=0.5)
            autonomy.enable(db, t0)
        timings = {}

        def timed(label, fn, copy=False, repeat=3):
            runs = []
            for k in range(repeat):
                target = path
                if copy:
                    target = Path(directory) / f"copy-{label}-{k}.sqlite"
                    shutil.copyfile(path, target)
                with closing(store.connect(target)) as db:
                    started = time.perf_counter()
                    value = fn(db)
                    runs.append(time.perf_counter() - started)
                    assert not db.in_transaction, label
            timings[label] = {"median_seconds": sorted(runs)[len(runs) // 2], "runs": runs}
            return value

        timed("_reach_tasks (window count)", lambda db: autonomy._reach_tasks(db, now))
        timed("_yield_share (7 days of finished tasks)", lambda db: autonomy._yield_share(db, now))
        timed("_reach_open (the admission rule's candidates)", lambda db: autonomy._reach_open(db, now))
        timed("candidates (the idle path's own ranking, for comparison)",
              lambda db: autonomy.candidates(db, now, limit=5))
        for rule in ("race", "reserve", "yield"):
            def one(db, rule=rule):
                state.set_config(db, {"allowance_division": rule, "reach_share": 0.5}, now)
                started = time.perf_counter()
                value = autonomy.division(db, now)
                timings.setdefault(f"division[{rule}] alone", {"runs": []})["runs"].append(
                    time.perf_counter() - started)
                return value
            out[f"division[{rule}]"] = timed(f"division[{rule}] with its setting write", one, copy=True)
            runs = timings[f"division[{rule}] alone"]["runs"]
            timings[f"division[{rule}] alone"]["median_seconds"] = sorted(runs)[len(runs) // 2]
        for rule in ("race", "reserve"):
            def decide(db, rule=rule):
                state.set_config(db, {"allowance_division": rule, "reach_share": 0.5}, now)
                return autonomy.idle(db, now, worker=f"cost-{rule}")["action"]
            out[f"idle[{rule}]"] = timed(f"idle decision[{rule}] (with its setting write)", decide, copy=True)
    out["timings"] = timings
    out["commit"], out["environment"] = _commit(), {"python": platform.python_version(),
                                                    "machine": platform.machine()}
    _write(out, a.out)
    return 0


def final(a, configs) -> int:
    """The look at the withheld worlds (cycles.final), refused here before anything is recorded when it would not name
    this tree or would write into it."""
    from neuroscifi.acceptance import cognition
    if cognition.REPO.resolve() != ROOT.resolve():
        raise SystemExit("final: run it on this checkout's own source (not an installed copy), so the ledger line is "
                         "written here and names this tree")
    if Path(a.out).resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("final: write --out outside this checkout and copy it in afterwards: a new file in the tree "
                         "would make the tree dirty")
    out = Path(a.out)           # review3b M-1: a failed write after the ledger line would spend the look for nothing
    if not out.parent.is_dir() or not os.access(out.parent, os.W_OK) or out.is_dir() or (
            out.exists() and not os.access(out, os.W_OK)):
        raise SystemExit(f"final: --out {a.out} cannot be written (its directory must exist and be writable)")
    started = time.perf_counter()
    try:
        value = cycles.final(a.note, configs=configs, mechanism=a.mechanism)
    except cognition.Refused as exc:
        raise SystemExit(str(exc)) from None
    out = {"label": "the withheld learning-cycles worlds (Wave 3 step 0): simulated worlds of fictional software "
                    "releases driven through the real modules; offline; no model; search, fetching and the owner "
                    "substituted", "commit": _commit(),
           "environment_short": {"python": platform.python_version(), "machine": platform.machine()}}
    out |= value
    out["wall_seconds"] = time.perf_counter() - started
    _write(out, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
