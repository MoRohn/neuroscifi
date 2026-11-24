#!/usr/bin/env python3
"""Differential check of the answer path: compose() and the guard of this tree against a recorded reference.

The guard (webcortex/reasoning.py: compose, attribution, check_sentence, guard and what they call) was attack-tested
over eight review rounds. A change next to it must not move a single verdict. This script records what a git ref
does with a fixed set of inputs, and tests/inference/test_guard_differential.py replays the same inputs through the
working tree and compares every result: the composed sentences, each accepted item (text, citations, claims,
states, hedged, attributed, insufficiency) and each rejected sentence with its reasons.

Inputs, three kinds:
  harvested   every (arguments) the reference's own guard tests pass to reasoning.compose and reasoning.guard
              (tests/inference/test_inference_reasoning.py, tests/webcortex/test_shared_verdict.py at the ref): the
              adversarial sentences of the attack rounds, with their bundles
  review      tests/inference/fixtures/guard_corpus_review.json: the security review's corpora for this branch
  benchmark   tests/inference/fixtures/guard_corpus_benchmark.json: the kept and rejected sentences of the live
              pipeline benchmark runs (fictional corpus), kept ones with the code's attribution taken off again

  python scripts/guard_differential.py record --ref 307b907          # rewrite the recorded reference
  python scripts/guard_differential.py compare                       # this tree against the recorded reference
  python scripts/guard_differential.py benchmark-corpus RUN.json ... # rebuild the benchmark corpus from bench runs

`record` exports the ref with `git archive` into a temporary directory and runs it there, so the reference is that
commit's source, not a copy kept in this tree. Nothing is fetched; no model is called.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "inference" / "fixtures"
REFERENCE = FIXTURES / "guard_reference.json"
REVIEW = FIXTURES / "guard_corpus_review.json"
BENCHMARK = FIXTURES / "guard_corpus_benchmark.json"
GUARD_TESTS = ("tests/inference/test_inference_reasoning.py", "tests/webcortex/test_shared_verdict.py")

# Written into the exported ref and loaded as a pytest plugin: records what the ref's tests pass to the guard.
HARVEST_PLUGIN = '''
import copy, json, os

_seen, _out = set(), None


def _record(kind, payload, bundle):
    try:
        line = json.dumps({"kind": kind, "input": payload, "bundle": bundle}, sort_keys=True, ensure_ascii=True)
        json.loads(line)
    except (TypeError, ValueError):
        return
    if line not in _seen:
        _seen.add(line)
        _out.write(line + "\\n")


def pytest_configure(config):
    global _out
    from neuroscifi.webcortex import reasoning
    _out = open(os.environ["NSF_GUARD_HARVEST"], "w")
    compose, guard = reasoning.compose, reasoning.guard

    def compose_recorded(value, b):
        _record("compose", copy.deepcopy(value), copy.deepcopy(b))
        return compose(value, b)

    def guard_recorded(sentences, b):
        _record("guard", copy.deepcopy(sentences), copy.deepcopy(b))
        return guard(sentences, b)
    reasoning.compose, reasoning.guard = compose_recorded, guard_recorded


def pytest_unconfigure(config):
    if _out is not None:
        _out.close()
'''


def verdict(reasoning, kind: str, payload, bundle: dict) -> dict:
    """What one tree does with one input. kind 'compose': the composed sentences. 'guard': accepted and rejected.
    'answer': a model answer through compose() and then the guard, as apply() does. An exception is a result too."""
    import copy
    b, payload = copy.deepcopy(bundle), copy.deepcopy(payload)
    try:
        if kind == "compose":
            return {"composed": reasoning.compose(payload, b)}
        if kind == "guard":
            accepted, rejected = reasoning.guard(payload, b)
            return {"accepted": accepted, "rejected": rejected}
        composed = reasoning.compose(payload, b)
        accepted, rejected = reasoning.guard(copy.deepcopy(composed), b)
        return {"composed": composed, "accepted": accepted, "rejected": rejected}
    except Exception as exc:            # recorded, so that both trees must fail alike
        return {"error": f"{type(exc).__name__}: {exc}"}


def comparable(result: dict) -> dict:
    """A result with the parts of each rejection reason in a fixed order. Some reasons list words taken from a set
    ("changes who does what in its cited claims: fixed, reported"), and their order varies from one Python process
    to the next (hash randomisation), at the reference commit too. The parts themselves are all compared."""
    out = dict(result)
    if "rejected" in out:
        out["rejected"] = [dict(r, reasons=[sorted(re.split(r", |; |: ", reason)) for reason in r["reasons"]])
                           for r in out["rejected"]]
    return out


def bundle_of(claims, question: str) -> dict:
    """A bundle from [state, text, sources?] rows (the review corpora's shape)."""
    rows, sources = [], set()
    for i, c in enumerate(claims, 1):
        own = list(c[2]) if len(c) > 2 else [i]
        sources |= set(own)
        rows.append({"id": f"C{i}", "state": c[0], "sources": own, "published": None, "event": None, "text": c[1]})
    return {"question": question, "verdict": None, "claims": rows,
            "sources": [{"id": n, "title": f"Source {n}", "published": "undated"} for n in sorted(sources)]}


def corpus_cases() -> list[dict]:
    """The committed corpora as cases {origin, kind, input, bundle}."""
    cases = []
    for k, case in enumerate(json.loads(REVIEW.read_text())):
        b = bundle_of(case["claims"], case.get("q", "Is numpy 2.5.5 secure?"))
        value = {"sentences": [{"claims": c, "text": t} for t, c in case["s"]], "gaps": []}
        cases.append({"origin": f"review:{case.get('from', '')}:{k}", "kind": "answer", "input": value, "bundle": b})
        for t, c in case["s"]:          # and each sentence on its own: one sentence can change another's verdict
            cases.append({"origin": f"review:{case.get('from', '')}:{k}", "kind": "answer",
                          "input": {"sentences": [{"claims": c, "text": t}], "gaps": []}, "bundle": b})
    for k, case in enumerate(json.loads(BENCHMARK.read_text())):
        b = bundle_of(case["claims"], case["question"])
        cases.append({"origin": f"benchmark:{case['task']}:{k}", "kind": "answer",
                      "input": {"sentences": [{"claims": case["cites"], "text": case["text"]}], "gaps": []},
                      "bundle": b})
    return cases


def run_in(src: Path, code: str, *args: str, env: dict | None = None, cwd: Path | None = None) -> str:
    full = dict(os.environ, PYTHONPATH=f"{src}{os.pathsep}{REPO / 'scripts'}", **(env or {}))
    done = subprocess.run([sys.executable, "-c", code, *args], env=full, cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"reference run failed:\n{done.stdout[-2000:]}\n{done.stderr[-4000:]}")
    return done.stdout


def record(ref: str) -> dict:
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", ref], capture_output=True, text=True,
                         check=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="nsf-guard-ref-") as tmp:
        tmp = Path(tmp)
        archive = subprocess.run(["git", "-C", str(REPO), "archive", sha, "src", "tests"], capture_output=True,
                                 check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive, check=True)
        (tmp / "nsf_guard_harvest.py").write_text(HARVEST_PLUGIN)
        harvest = tmp / "harvest.jsonl"
        tests = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "nsf_guard_harvest", *GUARD_TESTS],
            cwd=tmp, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=f"{tmp / 'src'}{os.pathsep}{tmp}", NSF_GUARD_HARVEST=str(harvest)))
        if tests.returncode:
            raise SystemExit(f"the reference's own guard tests failed:\n{tests.stdout[-3000:]}")
        summary = re.sub(r" in [\d.]+s.*$", "", tests.stdout.strip().splitlines()[-1])     # "827 passed"
        cases = [dict(json.loads(line), origin="harvested") for line in harvest.read_text().splitlines()]
        cases += corpus_cases()
        (tmp / "cases.json").write_text(json.dumps(cases))
        out = run_in(tmp / "src", "import json, sys\n"
                                  "from neuroscifi.webcortex import reasoning\n"
                                  "from guard_differential import verdict\n"
                                  "cases = json.load(open(sys.argv[1]))\n"
                                  "print(json.dumps([verdict(reasoning, c['kind'], c['input'], c['bundle']) "
                                  "for c in cases]))", str(tmp / "cases.json"), cwd=tmp)
        results = json.loads(out)
    bundles, index, packed = [], {}, []
    for case, result in zip(cases, results):
        key = json.dumps(case["bundle"], sort_keys=True)
        if key not in index:
            index[key] = len(bundles)
            bundles.append(case["bundle"])
        packed.append({"origin": case["origin"], "kind": case["kind"], "bundle": index[key], "input": case["input"],
                       "reference": result})
    return {"what": "what compose() and the guard of the reference commit do with each input; see "
                    "scripts/guard_differential.py",
            "ref": ref, "commit": sha, "reference_guard_tests": summary,
            "cases_by_origin": {o: sum(1 for c in packed if c["origin"].split(":")[0] == o)
                                for o in ("harvested", "review", "benchmark")},
            "bundles": bundles, "cases": packed}


def only_stricter(reference: dict, now: dict, mark: str) -> bool:
    """Whether `now` differs from `reference` in one way only: sentences the reference accepted are refused with the
    reason that starts with `mark` (reasoning.CUT_WORDS), and nothing else moved. That is: the composed sentences
    are the same (a refused one carries "cut"), the accepted items are the reference's in the same order with some
    missing and every remaining one identical, and the rejections are the reference's plus one `mark` rejection per
    missing item."""
    if "error" in reference or "error" in now or set(reference) != set(now):
        return False
    if "composed" in now:
        if [{k: v for k, v in item.items() if k != "cut"} for item in now["composed"]] != reference["composed"]:
            return False
    if "accepted" not in now:
        return True                                 # a compose case: the mark is the only difference
    theirs, missing = iter(reference["accepted"]), 0
    for item in now["accepted"]:
        for candidate in theirs:
            if candidate == item:
                break
            missing += 1
        else:
            return False                            # an accepted item the reference did not accept, or reordered
    missing += sum(1 for _ in theirs)
    cut = [r for r in now["rejected"] if len(r["reasons"]) == 1 and r["reasons"][0].startswith(mark)]
    kept = [r for r in now["rejected"] if r not in cut]
    return missing == len(cut) and comparable({"rejected": kept}) == comparable({"rejected": reference["rejected"]})


def differences(reasoning, reference: dict | None = None) -> dict:
    """Every recorded input through this tree. {"compared", "identical", "stricter": [...], "different": [...]}:
    `stricter` holds the cases whose only difference is a sentence refused under reasoning.CUT_WORDS (only_stricter),
    `different` every other difference, each as {case, origin, kind, input, reference, now}."""
    reference = reference or json.loads(REFERENCE.read_text())
    mark = getattr(reasoning, "CUT_WORDS", "\0").split("'")[0]
    out = {"compared": len(reference["cases"]), "identical": 0, "stricter": [], "different": []}
    for n, case in enumerate(reference["cases"]):
        now = json.loads(json.dumps(verdict(reasoning, case["kind"], case["input"],
                                            reference["bundles"][case["bundle"]])))
        if comparable(now) == comparable(case["reference"]):
            out["identical"] += 1
            continue
        entry = {"case": n, "origin": case["origin"], "kind": case["kind"], "input": case["input"],
                 "reference": case["reference"], "now": now}
        out["stricter" if only_stricter(case["reference"], now, mark) else "different"].append(entry)
    return out


def benchmark_corpus(paths: list[str]) -> list[dict]:
    """Cases from scripts/bench_pipeline.py --texts runs: each kept sentence with the attribution the code appended
    taken off again (what the model wrote, as far as the stored text shows), and each rejected sentence, against
    the claims the task's kept sentences cite. Deduplicated. The stored runs hold each cited claim's text and
    state, not its sources: a claim gets two sources when its sentence said "the cited sources", else one."""
    sys.path.insert(0, str(REPO / "src"))
    from neuroscifi.webcortex import reasoning
    from neuroscifi.webcortex.bench import corpus as C
    questions = {q["id"]: q["question"] for q in C.QUESTIONS}
    tails = sorted({", " + h.format(who=w) + "." for h in reasoning.HEDGES.values()
                    for w in ("one source", "the cited sources")}, key=len, reverse=True)
    seen, cases = set(), []

    def add(task, text, claims, cites):
        case = {"task": task, "question": questions.get(task, ""), "claims": claims, "cites": cites, "text": text}
        key = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            cases.append(case)
    for path in paths:
        for t in json.loads(Path(path).read_text())["tasks"]:
            held = {}
            for s in t.get("answer") or []:
                cited = s.get("cited") or []
                if not cited:
                    continue
                tail = next((x for x in tails if s["text"].endswith(x)), None)
                many = tail is not None and "the cited sources" in tail
                rows = [[c["state"], c["text"], [2 * i + 1, 2 * i + 2] if many and len(cited) == 1 else [i + 1]]
                        for i, c in enumerate(cited)]
                cites = [f"C{i}" for i in range(1, len(cited) + 1)]
                add(t["id"], s["text"], rows, cites)                                 # as shown: composed again
                if tail:
                    add(t["id"], s["text"][:-len(tail)] + ".", rows, cites)          # as written
                for c in cited:
                    held.setdefault(c["text"], c["state"])
            rows = [[state, text] for text, state in list(held.items())[:reasoning.MAX_CLAIMS]]
            for r in t.get("rejected_sentences") or []:
                for i in range(1, len(rows) + 1):
                    add(t["id"], r["text"], rows, [f"C{i}"])
    return cases


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record what a git ref does with every input")
    rec.add_argument("--ref", default="307b907")
    sub.add_parser("compare", help="replay the recorded inputs through this tree")
    bench = sub.add_parser("benchmark-corpus", help="rebuild the benchmark corpus from bench_pipeline --texts runs")
    bench.add_argument("runs", nargs="+")
    a = ap.parse_args(argv)
    if a.command == "record":
        reference = record(a.ref)
        REFERENCE.write_text(json.dumps(reference, indent=None, separators=(",", ":"), sort_keys=True) + "\n")
        print(json.dumps({k: reference[k] for k in ("ref", "commit", "reference_guard_tests", "cases_by_origin")}))
        return 0
    if a.command == "benchmark-corpus":
        cases = benchmark_corpus(a.runs)
        BENCHMARK.write_text(json.dumps(cases, indent=0, sort_keys=True) + "\n")
        print(json.dumps({"cases": len(cases), "tasks": len({c["task"] for c in cases})}))
        return 0
    sys.path.insert(0, str(REPO / "src"))
    from neuroscifi.webcortex import reasoning
    result = differences(reasoning)
    by_origin = {}
    for kind in ("stricter", "different"):
        for d in result[kind]:
            by_origin.setdefault(kind, {}).setdefault(d["origin"].split(":")[0], 0)
            by_origin[kind][d["origin"].split(":")[0]] += 1
    print(json.dumps({"compared": result["compared"], "identical": result["identical"],
                      "stricter": len(result["stricter"]), "different": len(result["different"]),
                      "by_origin": by_origin}))
    for d in result["different"][:20]:
        print(json.dumps(d, indent=1))
    return 1 if result["different"] else 0


if __name__ == "__main__":
    sys.exit(main())
