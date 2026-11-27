"""The look at the OMNIMIND benchmark's withheld split (lane ZW): the only code that loads a `withheld_*.py` file.

One look per mechanism, at the end, by the main session (docs/quality/OMNIMIND_BENCHMARK.md and
OMNIMIND_BENCHMARK_FORMS.md, "The withheld split"; the discipline follows the cognitive benchmark's final runs).
`look()` refuses, before anything is recorded or loaded:

  - a missing or blank note (it is the ledger line's reason);
  - a tree that is dirty or not committed, as `git status --porcelain` says (the ledger itself excepted, so that a
    look's own line does not make the next look read as dirty), so that the line names the code it measured;
  - a withheld file whose SHA-256 is not the one pinned in WITHHELD_SHA256 (a changed file is never scored);
  - a benchmark driver (DRIVERS: `acceptance/omni/subject.py`, `scripts/omni_forms_driver.py`) that is not pinned, or
    whose SHA-256 differs from its pin, in the tracked file DRIVER_PINS (docs/quality/evidence/
    omnimind_driver_pins.json). The main session writes that file deliberately (`scripts/bench_omni.py pin-driver`
    prints it with the current hashes) and commits it before a look; until then every look is refused. Review
    finding B4: the driver translates the product's answers into the benchmark's outcomes, so a look names the
    driver it went through, and each ledger line records the drivers' hashes.

`scripts/bench_omni.py final` and `scripts/bench_omni_forms.py final` also refuse an output path inside the checkout
(a new file would make the tree dirty), an output they cannot write, and a source tree other than their own. Then
look() appends one line per family to the ledger (LEDGER: time, commit, family, subject, note, the withheld file and
its SHA-256) BEFORE it imports or scores anything, so a run that crashes still counts as a look; only then does it
import the withheld module. The result is summaries only: counts with their denominators, never task ids, case
names or content.

Scoring reuses each family's own judges (score_task, made.judge_made, transform.judge, hostile.judge with isolate,
forms.label, the cross block counts, learn.run_person), applied to the withheld tasks. Nothing here imports product
code: a subject is handed in (subject.get for lane Z's families; a chooser, relater or learner factory for the
forms, cross and learn families).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[4]
LEDGER_NAME = "docs/quality/evidence/omnimind_final_uses.jsonl"
LEDGER = REPO / LEDGER_NAME
DRIVERS = ("src/neuroscifi/acceptance/omni/subject.py", "scripts/omni_forms_driver.py")
DRIVER_PINS_NAME = "docs/quality/evidence/omnimind_driver_pins.json"
DRIVER_PINS = REPO / DRIVER_PINS_NAME
HERE = Path(__file__).resolve().parent
LABEL = ("OMNIMIND benchmark, the WITHHELD split: one look per mechanism, recorded in the ledger before it is "
         "scored; written by lane ZW without reading product code; summaries only")

Z_FAMILIES = ("intake", "honest", "unknown", "compound", "hostile", "made", "transform")
FORMS_FAMILIES = ("forms", "cross", "learn")
FILE_OF = {**{f: "withheld_cases.py" for f in Z_FAMILIES}, "forms": "withheld_forms.py",
           "cross": "withheld_cross.py", "learn": "withheld_learn.py"}
# The withheld files' SHA-256 as committed by lane ZW (also pinned in tests/acceptance/test_omni_final.py). A file
# whose bytes differ is refused before its ledger line.
WITHHELD_SHA256 = {
    "withheld_forms.py": "587712dc30fd8f89e4b9577d08480fe8523a1e0b81df3cc4f7b3818c9f346504",
    "withheld_cases.py": "f55d51098ca03ea3ecdedc6b234cd803fa1cb9b02d56a334dcd0fbdebcaefeef",
    "withheld_cross.py": "ef8601bc5090deb2ad6c80c480a27038a25b9c181cc4865a2652b1a8a4104846",
    "withheld_learn.py": "511264dc08ecf02d6b796ee9b12df656f1ff48f8749fda54f53b154b3a1b4cfc",
}
# The SECOND withheld split (lane ZW2, `final --split withheld2`): new cases of the three mechanisms the first looks did
# not support (lane Z's `unknown` and `hostile`, and `cross`). Its files are pinned here and in
# tests/acceptance/test_omni_withheld2.py; a ledger line of this split carries "split": "withheld2".
SPLITS = ("withheld", "withheld2")
FILE_OF2 = {"unknown": "withheld2_unknown_hostile.py", "hostile": "withheld2_unknown_hostile.py",
            "cross": "withheld2_cross.py"}
WITHHELD2_SHA256 = {
    "withheld2_unknown_hostile.py": "bf296a3d4fd65ca4a833aa41de4a9eec055b516e5bbde8dd346ec9530f2e1ad3",
    "withheld2_cross.py": "a8b60d0df21fbed228a917a16ce69cb3d8e82977323bdd599ec28ecced4b2fe9",
}
# The THIRD withheld split (lane ZW3, `final --split withheld3`): new cases of unknown, hostile and cross, written after
# the owner opened the first two splits of these families (WAVE3.md). Pinned here and in
# tests/acceptance/test_omni_withheld3.py; a ledger line of this split carries "split": "withheld3".
FILE_OF3 = {"unknown": "withheld3_unknown_hostile.py", "hostile": "withheld3_unknown_hostile.py",
            "cross": "withheld3_cross.py"}
WITHHELD3_SHA256 = {
    "withheld3_unknown_hostile.py": "cdf4c891a652ccc3ce103975a6ce303f0497237d3dc132de27572361503f7c38",
    "withheld3_cross.py": "058ddc14897093ca8723d8bdaa38c3dea76aff1ae7c879f2cef6c3bd6f2f3821",
}


def files_of(split: str) -> tuple[dict, dict]:
    """(family -> withheld file, file -> pinned SHA-256) of a withheld split."""
    if split == "withheld":
        return FILE_OF, WITHHELD_SHA256
    if split == "withheld2":
        return FILE_OF2, WITHHELD2_SHA256
    raise Refused(f"unknown withheld split {split!r} (one of {', '.join(SPLITS)})")


class Refused(ValueError):
    """A look that may not happen (no note, a dirty tree, a changed withheld file, an output it may not write)."""


def digest(filename: str) -> str:
    """SHA-256 of a withheld file's bytes (nothing is imported)."""
    return hashlib.sha256((HERE / filename).read_bytes()).hexdigest()


def driver_hashes(repo: Path = REPO) -> dict[str, str | None]:
    """The SHA-256 of each benchmark driver as it is on disk (None for a missing file)."""
    out: dict[str, str | None] = {}
    for rel in DRIVERS:
        path = Path(repo) / rel
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return out


def pinned_drivers(path: Path | None = None) -> dict:
    """The drivers' pins from the tracked file ({} when it is missing or not readable as the expected JSON)."""
    try:
        data = json.loads(Path(path or DRIVER_PINS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pins = data.get("pins") if isinstance(data, dict) else None
    return pins if isinstance(pins, dict) else {}


def pin_document(repo: Path = REPO, commit: str | None = None) -> dict:
    """What `bench_omni.py pin-driver` prints: the pin file's content with the drivers' current hashes."""
    return {"label": "OMNIMIND benchmark drivers pinned for the withheld looks (acceptance/omni/final.py refuses a "
                     "look through a driver whose SHA-256 differs from its pin)",
            "how": "written by the main session from `scripts/bench_omni.py pin-driver`, committed deliberately "
                   "before a look; a later driver change needs a new pin and is visible in git",
            "pinned_at_commit": commit, "pins": driver_hashes(repo)}


def environment(repo: Path = REPO) -> dict:
    """The commit and whether the tree is dirty; dirty is True unless git itself said "clean" (the ledger excepted)."""
    commit = dirty = None
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=10)
        commit = (head.stdout.strip() or None) if head.returncode == 0 else None
        if commit:
            status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                                    capture_output=True, text=True, timeout=10)
            dirty = True if status.returncode != 0 else any(
                line[3:].strip('"') != LEDGER_NAME for line in status.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        dirty = True if commit else None
    return {"git_commit": commit, "git_dirty": dirty}


def out_problem(out: str, repo: Path = REPO) -> str | None:
    """Why --out may not be written, or None (checked before the ledger line)."""
    path = Path(out).resolve()
    if path.is_relative_to(Path(repo).resolve()):
        return f"--out {out} is inside the checkout: write it outside and copy it in afterwards"
    parent = path.parent
    if not parent.is_dir() or path.is_dir():
        return f"--out {out} cannot be written (its directory must exist)"
    import os
    if not os.access(parent, os.W_OK) or (path.exists() and not os.access(path, os.W_OK)):
        return f"--out {out} cannot be written"
    return None


def check(note: str, families, env: dict, *, pins: dict | None = None, drivers: dict | None = None,
          split: str = "withheld") -> None:
    """Every refusal that comes before the ledger line. `pins` default: the tracked DRIVER_PINS file; `drivers`
    default: the drivers' hashes on disk."""
    file_of, pinned = files_of(split)
    if not (note or "").strip():
        raise Refused("a note is required (it is the ledger line's reason)")
    unknown = [f for f in families if f not in file_of]
    if unknown or not families:
        raise Refused(f"unknown or missing family: {', '.join(unknown) or 'none given'}")
    if env.get("git_dirty") is not False or not env.get("git_commit"):
        raise Refused("the tree must be clean and committed (and git must say so), so the ledger line names the code "
                      "it measured")
    for name in sorted({file_of[f] for f in families}):
        got = digest(name)
        if got != pinned[name]:
            raise Refused(f"{name}'s SHA-256 is {got[:12]}..., not the pinned {(pinned[name] or 'none')[:12]}...: it "
                          "was changed and is not scored")
    pins = pinned_drivers() if pins is None else pins
    drivers = driver_hashes() if drivers is None else drivers
    for rel in DRIVERS:
        want, got = pins.get(rel), drivers.get(rel)
        if not want:
            raise Refused(f"{rel} is not pinned in {DRIVER_PINS_NAME}: the main session pins the driver "
                          "(scripts/bench_omni.py pin-driver) and commits the pin before a look")
        if got != want:
            raise Refused(f"{rel}'s SHA-256 is {(got or 'missing')[:12]}..., not the pinned {want[:12]}...: the "
                          "driver changed after it was pinned")


def record(entries: list[dict], ledger: Path = LEDGER) -> int:
    """Append one JSON line per entry; returns the number of lines in the ledger."""
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
    return sum(1 for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())


def look(families, subject, subject_name: str, note: str, *, script: str, scratch: str | None = None,
         ledger: Path = LEDGER, env: dict | None = None, pins: dict | None = None, split: str = "withheld") -> dict:
    """THE look: refusals, the ledger lines, then the withheld tasks scored. `subject`: a lane Z Subject for lane Z's
    families; for forms / cross / learn a chooser / relater / learner factory, or a reference name. `split`:
    "withheld" (lane ZW's files) or "withheld2" (lane ZW2's: unknown, hostile, cross)."""
    families = list(dict.fromkeys(families))
    env = environment() if env is None else env
    drivers = driver_hashes()
    check(note, families, env, pins=pins, drivers=drivers, split=split)
    file_of, pinned = files_of(split)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [{"at": at, "commit": env["git_commit"], "dirty": env["git_dirty"], "family": f,
                "subject": subject_name, "note": note, "script": script, "withheld_file": file_of[f],
                "withheld_sha256": pinned[file_of[f]], "drivers": drivers,
                **({"split": split} if split != "withheld" else {})} for f in families]
    lines = record(entries, ledger)
    out = {"label": LABEL, "generated_at": at, "environment": env, "ledger_lines": lines,
           "ledger_entries": entries, "families": {}}
    for f in families:
        start = time.perf_counter()
        if split == "withheld2":
            value = score_cross2(subject) if f == "cross" else score_z(f, subject, scratch=scratch, second=True)
        elif f in Z_FAMILIES:
            value = score_z(f, subject, scratch=scratch)
        else:
            value = {"forms": score_forms, "cross": score_cross, "learn": score_learn}[f](subject)
        value["wall_s"] = round(time.perf_counter() - start, 2)
        out["families"][f] = value
    return out


def _summary_only(value: dict) -> dict:
    return {k: v for k, v in value.items() if k not in ("tasks", "failures", "cases")}


# ---- lane Z's families ------------------------------------------------------------------------------------------------

def score_z(family: str, subject, *, scratch: str | None = None, second: bool = False) -> dict:
    from . import Result, run_in_process, run_tasks, summarise
    from . import hostile, made, transform
    if second:
        from . import withheld2_unknown_hostile as W
    else:
        from . import withheld_cases as W
    split, start = W.SPLIT, time.perf_counter()
    if family in ("intake", "honest", "unknown", "compound"):
        rows = run_tasks(family, split, subject, W.tasks(family))
        extra = None
        if family == "unknown":
            disguised = [(t, r) for t, r in rows if W.is_disguised(t.id)]
            truly = [(t, r) for t, r in rows if not W.is_disguised(t.id)]
            missed = sum(r.outcome == "abstained" for _, r in disguised)
            n_id = sum(1 for t, _ in truly if t.question.startswith("detect."))
            false_id = sum(r.outcome == "wrong" for t, r in truly if t.question.startswith("detect."))
            extra = {"disguised": {"n": len(disguised), "right": sum(r.outcome == "right" for _, r in disguised),
                                   "missed_decodable": missed},
                     "truly_unknown": {"n": len(truly), "right": sum(r.outcome == "right" for _, r in truly),
                                       "false_identification": false_id, "identification_questions": n_id}}
        out = summarise(family, split, subject, rows, time.perf_counter() - start, extra)
        if family == "honest":
            by = out["by_kind"]
            out["header_facts"] = by.get("measure", {})
            out["content"] = {k: sum(by.get(kind, {}).get(k, 0) for kind in ("cannot", "cannot_or_value"))
                              for k in ("right", "abstained", "wrong", "failed")}
        return _summary_only(out)
    if family == "hostile":
        from .isolate import run_isolated
        rows = []
        with tempfile.TemporaryDirectory(prefix="omni-final-hostile-", dir=scratch) as root:
            for task, escapes in W.hostile_tasks():
                report = run_isolated(task, subject.name, Path(root), seconds=hostile.SECONDS, escape_paths=escapes)
                rows.append((task, hostile.judge(task, report)))
        peaks = [r.detail.get("peak_traced", 0) for _, r in rows]
        safe = sum(r.outcome in ("right", "abstained") for _, r in rows)
        out = summarise("hostile", split, subject, rows, time.perf_counter() - start,
                        {"safe": safe, "peak_traced_max": max(peaks) if peaks else 0,
                         "failure_kinds": dict(Counter(r.detail.get("failure") for _, r in rows
                                                       if r.outcome == "failed"))})
        return _summary_only(out)
    if family in ("made", "transform"):
        judge = made.judge_made if family == "made" else transform.judge
        rows = []
        for task in W.tasks(family):
            first, info = run_in_process(task, subject)
            if first is None:
                rows.append((task, Result("failed", info)))
                continue
            second = run_in_process(task, subject)[0] if first.status == "answered" else None
            rows.append((task, judge(task, first, second)))
        produced = [r.detail for _, r in rows if "carried" in r.detail]
        extra = {"produced": len(produced),
                 "carried_mean": round(sum(d["carried"] for d in produced) / len(produced), 4) if produced else None,
                 "deterministic": sum(1 for d in produced if d.get("deterministic")),
                 "with_problems": sum(1 for d in produced if d.get("problems"))}
        return _summary_only(summarise(family, split, subject, rows, time.perf_counter() - start, extra))
    raise Refused(f"not one of lane Z's families: {family}")


# ---- forms, cross, learn ------------------------------------------------------------------------------------------------

def forms_tasks() -> list:
    from . import forms, withheld_forms as WF
    out = []
    for case in WF.WITHHELD:
        view = {"id": case["id"], "request": case["request"], "explicit": case["explicit"],
                "purpose": case["purpose"], "content": [dict(s) for s in case["content"]],
                "context": {k: (dict(v) if isinstance(v, dict) else v) for k, v in case["context"].items()}}
        out.append(forms.Task(case["id"], "withheld", view, case))
    return out


def score_forms(subject) -> dict:
    """forms.score's measures over the withheld cases (same labels, explicit-request counts, slices and pairs)."""
    from . import forms
    subject = forms._resolve(subject)
    tasks = forms_tasks()
    rows = []
    for task in tasks:
        for form, w in forms._pick(subject, task):
            lab, why = forms.label(task.case, form)
            rows.append((task, form, w, lab, why))
    n = len(tasks)
    count = Counter()
    for _, _, w, lab, _ in rows:
        count[lab] += w
    bad = count["too_poor"] + count["too_much"] + count["unfit"] + count["violation"]
    frac = forms._frac
    out = {"label": forms.LABEL, "split": "withheld", "n_cases": n,
           "good": frac(count["good"], n), "good_or_fine": frac(count["good"] + count["fine"], n),
           "bad": {**frac(bad, n), "too_poor": frac(count["too_poor"], n), "too_much": frac(count["too_much"], n),
                   "unfit": frac(count["unfit"], n), "violation": frac(count["violation"], n)},
           "hard_constraints_violated": frac(count["violation"], n)}
    ef = [r for r in rows if r[0].case["explicit"] and forms.explicit_feasible(r[0].case)]
    ei = [r for r in rows if r[0].case["explicit"] and not forms.explicit_feasible(r[0].case)]
    n_ef, n_ei = len({r[0].id for r in ef}), len({r[0].id for r in ei})
    out["explicit"] = {
        "feasible_honoured": frac(sum(w for t, f, w, _, _ in ef if t.case["explicit"] in f.split("+")), n_ef),
        "infeasible_handled": frac(sum(w for _, _, w, lab, _ in ei if lab == "good"), n_ei),
        "infeasible_chosen_anyway": frac(sum(w for t, f, w, _, _ in ei if t.case["explicit"] in f.split("+")), n_ei)}
    out["slices"] = forms._slices(rows)
    out["slices"]["purpose_given"] = forms._slice_of(rows, lambda c: bool(c["purpose"]))
    out["pairs"] = forms._pairs(rows)
    return out


def score_cross(subject) -> dict:
    """cross.score's measures over the withheld pairs (by kind, including the kinds only this split has)."""
    from . import cross, withheld_cross as WX
    if isinstance(subject, str):
        subject = cross.REFERENCES[subject][1]
    relate = getattr(subject, "relate", subject)
    rows = []
    for t in WX.tasks():
        reply = relate(dict(t.files), t.question) or {}
        answer = reply.get("answer")
        rows.append((t, answer if isinstance(answer, bool) else None, bool(reply.get("because"))))
    n = len(rows)

    def frac(k, m):
        return {"k": k, "n": m, "share": round(k / m, 4) if m else None}

    def block(sel):
        answered = [r for r in sel if r[1] is not None]
        correct = sum(1 for t, a, _ in sel if a is t.truth)
        return {"cases": len(sel), "correct": frac(correct, len(sel)), "answered": frac(len(answered), len(sel)),
                "correct_of_answered": frac(correct, len(answered))}
    return {"label": cross.LABEL, "split": "withheld", "n_cases": n, "n_pairs": n // 2, **block(rows),
            "matching": block([r for r in rows if r[0].truth]),
            "non_matching": block([r for r in rows if not r[0].truth]),
            "both_twins_correct": frac(sum(1 for a, b in zip(rows[::2], rows[1::2])
                                           if a[1] is a[0].truth and b[1] is b[0].truth), n // 2),
            "with_reasons": frac(sum(1 for r in rows if r[2]), n),
            "kinds_only_in_this_split": block([r for r in rows if r[0].kind in WX.NEW_KINDS]),
            "by_kind": {kind: block([r for r in rows if r[0].kind == kind]) for kind in WX.KINDS}}


def score_learn(subject) -> dict:
    """learn.score's measures over the withheld SIMULATED people and seeds."""
    from collections import defaultdict
    from . import learn, withheld_learn as WL
    make = learn.REFERENCES[subject][1] if isinstance(subject, str) else subject
    cycles, per = WL.CYCLES, WL.PER_CYCLE
    late_from = cycles // 2
    by_seed_cycle, late_seed, all_seed = defaultdict(list), defaultdict(list), defaultdict(list)
    late_person = defaultdict(list)
    for person in WL.PEOPLE:
        for seed in WL.SEEDS:
            kept = learn.run_person(make, person, seed)
            for c in range(cycles):
                block = kept[c * per:(c + 1) * per]
                by_seed_cycle[(seed, c)] += block
                all_seed[seed] += block
                if c >= late_from:
                    late_seed[seed] += block
                    late_person[person["id"]] += block

    def sd(values):
        return round(statistics.stdev(values), 4) if len(values) > 1 else 0.0
    means, sds = [], []
    for c in range(cycles):
        per_seed = [sum(by_seed_cycle[(s, c)]) / len(by_seed_cycle[(s, c)]) for s in WL.SEEDS]
        means.append(round(statistics.fmean(per_seed), 4))
        sds.append(sd(per_seed))
    late = [sum(late_seed[s]) / len(late_seed[s]) for s in WL.SEEDS]
    whole = [sum(all_seed[s]) / len(all_seed[s]) for s in WL.SEEDS]
    return {"label": learn.LABEL, "split": "withheld", "people": len(WL.PEOPLE), "seeds": len(WL.SEEDS),
            "cycles": cycles, "requests_per_cycle": per,
            "denominator_per_cycle": len(WL.PEOPLE) * len(WL.SEEDS) * per,
            "kept_by_cycle": means, "kept_by_cycle_sd_over_seeds": sds, "late_cycles": [late_from + 1, cycles],
            "late_kept": {"mean": round(statistics.fmean(late), 4), "sd_over_seeds": sd(late),
                          "per_seed": [round(x, 4) for x in late]},
            "all_kept": {"mean": round(statistics.fmean(whole), 4), "sd_over_seeds": sd(whole),
                         "per_seed": [round(x, 4) for x in whole]},
            "late_kept_by_person": {pid: round(sum(v) / len(v), 4) for pid, v in late_person.items()}}


def score_cross2(subject) -> dict:
    """The second withheld split's cross measures. A case is correct when the answer equals its `expect`: the truth
    where the kind is decidable by measurement, None ("cannot") on both twins where it is not (any answer there is
    wrong, so a guess gains nothing). Blocks: all, decidable, undecidable, the kinds only this split has, by kind."""
    from . import cross, withheld2_cross as WX
    if isinstance(subject, str):
        subject = cross.REFERENCES[subject][1]
    relate = getattr(subject, "relate", subject)
    rows = []
    for t in WX.tasks():
        reply = relate(dict(t.files), t.question) or {}
        answer = reply.get("answer")
        rows.append((t, answer if isinstance(answer, bool) else None, bool(reply.get("because"))))
    n = len(rows)

    def frac(k, m):
        return {"k": k, "n": m, "share": round(k / m, 4) if m else None}

    def block(sel):
        answered = [r for r in sel if r[1] is not None]
        correct = sum(1 for t, a, _ in sel if a is t.expect)
        right_answers = sum(1 for t, a, _ in answered if a is t.expect)
        return {"cases": len(sel), "correct": frac(correct, len(sel)), "answered": frac(len(answered), len(sel)),
                "correct_of_answered": frac(right_answers, len(answered)),
                "wrong": len(answered) - right_answers}
    decidable = [r for r in rows if WX.DECIDABLE[r[0].kind][0]]
    return {"label": cross.LABEL, "split": "withheld2", "n_cases": n, "n_pairs": n // 2, **block(rows),
            "both_twins_correct": frac(sum(1 for a, b in zip(rows[::2], rows[1::2])
                                           if a[1] is a[0].expect and b[1] is b[0].expect), n // 2),
            "with_reasons": frac(sum(1 for r in rows if r[2]), n),
            "decidable": block(decidable),
            "matching": block([r for r in decidable if r[0].truth]),
            "non_matching": block([r for r in decidable if not r[0].truth]),
            "undecidable": block([r for r in rows if not WX.DECIDABLE[r[0].kind][0]]),
            "kinds_only_in_this_split": block([r for r in rows if r[0].kind in WX.NEW_KINDS]),
            "by_kind": {kind: {**block([r for r in rows if r[0].kind == kind]),
                               "decidable_by_measurement": WX.DECIDABLE[kind][0]} for kind in WX.KINDS}}
