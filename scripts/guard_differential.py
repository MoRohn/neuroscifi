#!/usr/bin/env python3
"""Differential check of the answer path: compose() and the guard of this tree against a recorded reference.

The guard (webcortex/reasoning.py: compose, attribution, check_sentence, guard and what they call) was attack-tested
over eight review rounds. A change to it may move a verdict only in a way that is named here and counted by the
test. This script records what a git ref does with a fixed set of inputs, and
tests/inference/test_guard_differential.py replays the same inputs through the working tree and compares every
result: the composed sentences, each accepted item (text, citations, claims, states, hedged, attributed,
insufficiency) and each rejected sentence with its reasons. Every case is then one of:

  identical   nothing differs
  stricter    sentences the reference accepted are refused, each with reasons that all start with one of the
              tree's refusal marks (reasoning.CUT_WORDS, reasoning.REFUSALS); nothing else moved
  loosened    sentences the reference rejected are accepted, each of a class this script can prove (LOOSENINGS:
              one class, a sentence that is exactly one cited claim and that the reference rejected by its anchor
              rule alone); nothing else moved. A case may be stricter for one sentence and loosened for another; it
              is then counted as loosened
  different   anything else: the test fails

Inputs, four kinds:
  harvested   every (arguments) the reference's own guard tests pass to reasoning.compose and reasoning.guard
              (tests/inference/test_inference_reasoning.py, tests/webcortex/test_shared_verdict.py at the ref): the
              adversarial sentences of the attack rounds, with their bundles
  review      tests/inference/fixtures/guard_corpus_review.json: the security review's corpora for this branch
  benchmark   tests/inference/fixtures/guard_corpus_benchmark.json: the kept and rejected sentences of the live
              pipeline benchmark runs (fictional corpus), kept ones with the code's attribution taken off again
  gap         tests/inference/fixtures/guard_corpus_gaps.json: one adversarial corpus per gap of the 0.7.0 guard
              closed since. Each sentence says what the finished guard must do with it: "reject" (an attack),
              "accept" (a faithful sentence), "lost" (a faithful sentence refused on purpose: the cost of a rule,
              pinned) or "open" (a change of meaning still accepted: left open, listed in the docs)

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
GAPS = FIXTURES / "guard_corpus_gaps.json"
ORIGINS = ("harvested", "review", "benchmark", "gap")
ANCHOR_REASON = "ties a version or name to what its cited claims do not say of it"
WANTED = {"reject": "reject", "accept": "accept", "lost": "reject", "open": "accept"}
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
    for k, case in enumerate(json.loads(GAPS.read_text())):
        b = bundle_of(case["claims"], case.get("q", "Is numpy 2.5.5 secure?"))
        for j, row in enumerate(case["s"]):
            cases.append({"origin": f"gap:{case['gap']}:{k}.{j}", "kind": "answer", "expect": row[2],
                          "input": {"sentences": [{"claims": row[1], "text": row[0]}], "gaps": []}, "bundle": b})
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
                       "reference": result} | ({"expect": case["expect"]} if "expect" in case else {}))
    return {"what": "what compose() and the guard of the reference commit do with each input; see "
                    "scripts/guard_differential.py",
            "ref": ref, "commit": sha, "reference_guard_tests": summary,
            "cases_by_origin": {o: sum(1 for c in packed if c["origin"].split(":")[0] == o)
                                for o in ORIGINS},
            "bundles": bundles, "cases": packed}


def refusal_marks(reasoning) -> tuple[str, ...]:
    """The leading words of every reason by which this tree's guard refuses a sentence it otherwise accepts: the
    text of each template up to its first placeholder or quoted part."""
    templates = [getattr(reasoning, "CUT_WORDS", "")] + list(getattr(reasoning, "REFUSALS", ()))
    return tuple(re.split(r"[{']", t)[0] for t in templates if t)


# The exact-restatement class, frozen here so that the proof does not run the code it proves: a later change to
# reasoning.restates() cannot widen the class without failing the test. The attribution table is 307b907's
# (reasoning.HEDGES, attribution()): the weakest cited state in this order, "one source" for one source.
_HEDGES_307B907 = (("disputed", "according to {who}, though the claim is disputed"),
                   ("conflicting", "according to {who}, though the sources disagree"),
                   ("temporally_unresolved", "according to {who}, possibly out of date"),
                   ("insufficient", "according to {who}"), ("not_evaluated", "according to {who}"))
_UNSEEN_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cn", "Cs", "Zl", "Zp"})
_ATTRIBUTION_WORDS = re.compile(r"\baccording\s+to\b", re.IGNORECASE)
_PRONOUN_LEAD = frozenset("""it its they their them he she his her we this these those that there both such one
another each neither either other others""".split())
_PRONOUN_LEAD_PAIRS = frozenset({"the latter", "the former", "the same", "the other"})


def _frozen_key(text) -> str | None:
    import unicodedata
    text = " ".join(str(text or "").split())
    if not text or any(unicodedata.category(ch) in _UNSEEN_CATEGORIES for ch in text) or \
            unicodedata.normalize("NFKC", text) != text:
        return None
    return text[:-1].rstrip() if text.endswith(".") and not text.endswith("..") else text


def _exact_restatement(reasoning, item: dict, bundle: dict, reference: dict) -> bool:
    """The one class of sentence this tree may accept where the reference did not, checked without the tree's
    code: `item` cites exactly one claim, through that claim's own sources; the claim holds no "according to", no
    character a reader does not see, no compatibility form, and does not start with a pronoun; the item's text is
    the claim's text character for character (white space and one final full stop aside), followed by exactly
    307b907's attribution for the claim's state and source count (nothing when supported); and the reference
    rejected that very text for the anchor rule and for nothing else."""
    claims = {c["id"]: c for c in bundle.get("claims") or []}
    ids = list(dict.fromkeys(item.get("claims") or []))
    if len(ids) != 1 or ids[0] not in claims:
        return False
    claim = claims[ids[0]]
    sources = sorted(set(claim.get("sources") or []))
    if sorted(set(item.get("citations") or [])) != sources:
        return False
    key, text = _frozen_key(claim.get("text")), _frozen_key(item.get("text"))
    lead = [w.lower().strip(",.;:()\"'") for w in (key or "").split(" ")[:2]] + ["", ""]
    if key is None or text is None or _ATTRIBUTION_WORDS.search(key) or lead[0] in _PRONOUN_LEAD or \
            f"{lead[0]} {lead[1]}" in _PRONOUN_LEAD_PAIRS:
        return False
    hedge = next((h for state, h in _HEDGES_307B907 if state == claim.get("state")), None)
    if hedge is None and claim.get("state") != "supported":
        hedge = "according to {who}"
    tail = f", {hedge.format(who='one source' if len(sources) == 1 else 'the cited sources')}" if hedge else ""
    if text != key + tail:
        return False
    theirs = [r for r in reference.get("rejected") or [] if r["text"] == item["text"]]
    return bool(theirs) and all(reason.startswith(ANCHOR_REASON) for r in theirs for reason in r["reasons"])


LOOSENINGS = {"restatement": _exact_restatement}


def classify(reasoning, reference: dict, now: dict, bundle: dict, marks: tuple[str, ...]) -> tuple[str, dict]:
    """("identical" | "stricter" | "loosened" | "different", detail). See the module docstring. The composed
    sentences must be the reference's (a refused one may carry "cut"); the accepted items the two share must be
    identical and in the same order; an item only the reference accepted must be answered by one rejection whose
    reasons all start with a refusal mark; an item only this tree accepts must be of a class in LOOSENINGS, and then
    the reference's rejection of that text is gone; every other rejection is the reference's, reason for reason."""
    if comparable(now) == comparable(reference):
        return "identical", {}
    if "error" in reference or "error" in now or set(reference) != set(now):
        return "different", {}
    if "composed" in now:
        if [{k: v for k, v in item.items() if k != "cut"} for item in now["composed"]] != reference["composed"]:
            return "different", {}
    if "accepted" not in now:
        return "stricter", {"refused": 0}           # a compose case: the "cut" mark is the only difference
    mine, theirs = list(now["accepted"]), list(reference["accepted"])
    extra = [item for item in mine if item not in theirs]
    missing = [item for item in theirs if item not in mine]
    if [x for x in mine if x not in extra] != [x for x in theirs if x not in missing]:
        return "different", {}
    classes = []
    for item in extra:
        fits = [name for name, test in LOOSENINGS.items() if test(reasoning, item, bundle, reference)]
        if not fits:
            return "different", {}
        classes.append(fits[0])
    refusals = [r for r in now["rejected"] if r["reasons"] and all(x.startswith(marks) for x in r["reasons"])]
    kept = [r for r in now["rejected"] if r not in refusals]
    expected = [r for r in reference["rejected"] if r["text"] not in {item["text"] for item in extra}]
    if len(refusals) != len(missing) or comparable({"rejected": kept}) != comparable({"rejected": expected}):
        return "different", {}
    marks_used = sorted({m for r in refusals for x in r["reasons"] for m in marks if x.startswith(m)})
    return ("loosened" if extra else "stricter"), {"refused": len(refusals), "marks": marks_used, "classes": classes}


def differences(reasoning, reference: dict | None = None) -> dict:
    """Every recorded input through this tree: {"compared", "identical", "stricter": [...], "loosened": [...],
    "different": [...]}, each entry {case, origin, kind, input, reference, now, detail} (and "expect" for a case
    of the gap corpora)."""
    reference = reference or json.loads(REFERENCE.read_text())
    marks = refusal_marks(reasoning)
    out = {"compared": len(reference["cases"]), "identical": 0, "stricter": [], "loosened": [], "different": []}
    for n, case in enumerate(reference["cases"]):
        bundle = reference["bundles"][case["bundle"]]
        now = json.loads(json.dumps(verdict(reasoning, case["kind"], case["input"], bundle)))
        kind, detail = classify(reasoning, case["reference"], now, bundle, marks)
        if kind == "identical":
            out["identical"] += 1
            continue
        out[kind].append({"case": n, "origin": case["origin"], "kind": case["kind"], "input": case["input"],
                          "reference": case["reference"], "now": now, "detail": detail}
                         | ({"expect": case["expect"]} if "expect" in case else {}))
    return out


def expectations(reasoning, reference: dict | None = None) -> list[dict]:
    """The gap corpora through this tree: per sentence {origin, gap, expect, want, got, text, shown, reasons}.
    `want` is what `expect` means for the finished guard (WANTED), `got` what this tree does."""
    reference = reference or json.loads(REFERENCE.read_text())
    out = []
    for case in reference["cases"]:
        if "expect" not in case:
            continue
        now = verdict(reasoning, case["kind"], case["input"], reference["bundles"][case["bundle"]])
        got = "accept" if now.get("accepted") else "reject"
        out.append({"origin": case["origin"], "gap": case["origin"].split(":")[1], "expect": case["expect"],
                    "want": WANTED[case["expect"]], "got": got, "text": case["input"]["sentences"][0]["text"],
                    "shown": [x["text"] for x in now.get("accepted") or []],
                    "reasons": [x for r in now.get("rejected") or [] for x in r["reasons"]]})
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
    for kind in ("stricter", "loosened", "different"):
        for d in result[kind]:
            by_origin.setdefault(kind, {}).setdefault(d["origin"].split(":")[0], 0)
            by_origin[kind][d["origin"].split(":")[0]] += 1
    print(json.dumps({"compared": result["compared"], "identical": result["identical"],
                      "stricter": len(result["stricter"]), "loosened": len(result["loosened"]),
                      "different": len(result["different"]), "by_origin": by_origin}))
    unmet = {}
    for e in expectations(reasoning):
        if e["want"] != e["got"]:
            unmet[e["gap"]] = unmet.get(e["gap"], 0) + 1
    print(json.dumps({"gap_expectations_unmet": unmet}))
    for d in result["different"][:20]:
        print(json.dumps(d, indent=1))
    return 1 if result["different"] else 0


if __name__ == "__main__":
    sys.exit(main())
