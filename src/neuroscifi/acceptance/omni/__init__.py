"""The OMNIMIND benchmark (lane Z): taking things in and making things, written BEFORE the mechanisms it measures.

docs/quality/OMNIMIND_BENCHMARK.md says what each family measures and what it cannot show; scripts/bench_omni.py is
the command line; ADR-075 records the decisions.

A family is a module of this package exposing `tasks(split) -> list[Task]` and `score(subject, split) -> dict`:

  intake      intake.py     one modality at a time: tables, documents, images, sound, meshes, geography, trees,
                            code, notebooks, arrays, archives
  honest      honest.py     formats nothing here decodes by design: identification and header facts are
                            answerable, content is not (an answer about content is a fabrication)
  unknown     unknown.py    decodable files in disguise, and bytes that are truly unknown
  compound    compound.py   archives and documents whose question needs the right member
  hostile     hostile.py    bombs, lies, traversal, depth, polyglots, injected instructions: each task runs in its
                            own process under a time and memory bound
  made        made.py       requests to MAKE something; the benchmark's own readers recover the content
  transform   transform.py  several steps: decode, make, recover

Every file is assembled by the benchmark's own encoders (enc.py) from values the family's generator chose, so the
truth is known by construction. Generators and readers never import `neuroscifi.artifacts` or other product code;
only subject.py (the thin driver) does, lazily.

Questions are MEASUREMENTS from a fixed vocabulary (subject.MEASURES) that a driver reads off a representation, so
that scoring is exact comparison within a tolerance, never a judgment. A question may name a member first:
`data/values.csv::table.n_rows` (members nest: `inner.zip::a.csv::table.n_rows`).

Three outcomes per task, all reported with their denominators (score_task):
  right      the expected answer within tolerance, or the expected abstention ("cannot" / "refused")
  abstained  an honest "cannot" or "refused" where an answer was expected (no credit, no harm)
  wrong      a false answer: a value outside tolerance, or any answer where only an abstention is right. This is
             fabrication, the worst outcome; it is reported on its own and never averaged away.
and `failed` for what is none of these: a crash (an unnamed exception: a traceback to the owner), a hang past the
task's time bound, memory past its bound, or a file written outside the scratch directory.

Splits: `development` (tune on it) and `held_out` (report on it, never choose with it) are separately written case
tables in cases.py with disjoint seed ranges (DEV_SEEDS, HELD_OUT_SEEDS). cases.py's SHA-256 and every family's
task digest per split are pinned in tests/acceptance/test_omni_bench.py: a changed task fails that test. The
withheld split is written later by another lane (acceptance/omni/withheld_*.py, seeds WITHHELD_SEEDS); nothing here
reads it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import json
import math
import time
from typing import Callable, Mapping, Protocol

SPLITS = ("development", "held_out")
FAMILIES = ("intake", "honest", "unknown", "compound", "hostile", "made", "transform")
KINDS = ("measure", "cannot", "cannot_or_value", "refuse", "make", "transform")
STATUSES = ("answered", "cannot", "refused")
OUTCOMES = ("right", "abstained", "wrong", "failed")
DEV_SEEDS = range(3100, 3600)
HELD_OUT_SEEDS = range(3600, 4100)
WITHHELD_SEEDS = range(9100, 9600)          # reserved for the withheld lane; no case here uses them
LABEL = ("OMNIMIND benchmark: synthetic files assembled by the benchmark's own encoders from known values; offline; "
         "no model; exact comparison within stated tolerances")


@dataclass(frozen=True)
class Task:
    id: str
    family: str
    files: Mapping[str, bytes]          # name -> bytes (a task's input; for "make", request.json)
    declared: Mapping[str, str]         # name -> declared media type (a hint, possibly a lie)
    question: str                       # a measurement (subject.MEASURES), "make:<form>" or "transform:<steps>"
    expect: object                      # the truth (JSON-able); for make/transform, the source the reader checks
    tolerance: object = None            # None (exact) | {"abs": x} | {"rel": x} | {"set": True} | {"one_of": True}
    kind: str = "measure"               # one of KINDS

    def digest(self) -> str:
        """The task's identity: everything but the bytes, plus each file's SHA-256."""
        body = {"id": self.id, "family": self.family, "question": self.question, "expect": self.expect,
                "tolerance": self.tolerance, "kind": self.kind, "declared": dict(sorted(self.declared.items())),
                "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(self.files.items())}}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=_jsonable).encode()).hexdigest()


def _jsonable(value):
    if isinstance(value, (bytes, bytearray)):
        return {"sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(type(value).__name__)


@dataclass(frozen=True)
class Answer:
    value: object | None
    status: str                         # "answered" | "cannot" | "refused"
    reason: str = ""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"status {self.status!r} not in {STATUSES}")


class Subject(Protocol):
    name: str

    def answer(self, task: Task) -> Answer: ...


@dataclass
class Result:
    outcome: str                        # one of OUTCOMES
    detail: dict = field(default_factory=dict)


# ---- comparison -------------------------------------------------------------------------------------------------------

def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        import numpy as np
        if isinstance(value, np.generic) and not isinstance(value, np.bool_):
            return float(value)
    except ImportError:                 # pragma: no cover
        pass
    return None


def _norm_text(value) -> str:
    return " ".join(str(value).split())


def same(expect, got, tolerance=None) -> bool:
    """Whether `got` equals `expect` under the tolerance. Numbers: {"abs": x} or {"rel": x} (relative to |expect|,
    with an absolute floor of x * 1e-9), exact otherwise; strings: exact after collapsing whitespace; lists:
    elementwise, or as multisets with {"set": True}; {"one_of": True}: `expect` lists the acceptable answers."""
    tol = tolerance or {}
    if tol.get("one_of"):
        rest = {k: v for k, v in tol.items() if k != "one_of"}
        return any(same(e, got, rest) for e in expect)
    if expect is None:
        return got is None
    if isinstance(expect, bool):
        return isinstance(got, bool) and got == expect or (_is_np_bool(got) and bool(got) == expect)
    e = _num(expect)
    if e is not None:
        g = _num(got)
        if g is None and isinstance(got, str):
            try:
                g = float(got.strip())
            except ValueError:
                return False
        if g is None or math.isnan(g):
            return math.isnan(e) if g is not None else False
        if "abs" in tol:
            return abs(g - e) <= tol["abs"]
        if "rel" in tol:
            return abs(g - e) <= tol["rel"] * max(abs(e), 1e-9)
        return g == e
    if isinstance(expect, str):
        return isinstance(got, str) and _norm_text(got) == _norm_text(expect)
    if isinstance(expect, (list, tuple)):
        try:
            got = list(got)
        except TypeError:
            return False
        if len(got) != len(expect):
            return False
        if tol.get("set"):
            rest = {k: v for k, v in tol.items() if k != "set"}
            left = list(got)
            for e_item in expect:
                k = next((i for i, g_item in enumerate(left) if same(e_item, g_item, rest)), None)
                if k is None:
                    return False
                left.pop(k)
            return True
        return all(same(a, b, tol) for a, b in zip(expect, got))
    if isinstance(expect, dict):
        return isinstance(got, dict) and set(got) == set(expect) and all(same(expect[k], got[k], tol) for k in expect)
    return expect == got


def _is_np_bool(value) -> bool:
    try:
        import numpy as np
        return isinstance(value, np.bool_)
    except ImportError:                 # pragma: no cover
        return False


def score_task(task: Task, answer: Answer) -> Result:
    """The outcome of an answer to a measurement task (make and transform tasks are judged by their family)."""
    if task.kind in ("cannot", "refuse"):
        if answer.status == "answered":
            return Result("wrong", {"why": "answered where only an abstention is right"})
        if task.kind == "refuse" and answer.status != "refused":
            return Result("abstained", {"why": "cannot, where a refusal was expected"})
        return Result("right")
    if answer.status != "answered":
        if task.kind == "cannot_or_value":
            return Result("right")
        return Result("abstained")
    if same(task.expect, answer.value, task.tolerance):
        return Result("right")
    return Result("wrong", {"why": "value outside tolerance"})


# ---- running a family ---------------------------------------------------------------------------------------------

class Hang(Exception):
    """A task ran past its time bound (in-process guard)."""


def run_in_process(task: Task, subject: Subject, *, seconds: float = 60.0) -> tuple[Answer | None, dict]:
    """Ask the subject, with a wall-clock guard (SIGALRM, main thread only). Returns (answer, failure detail)."""
    import signal
    import threading

    def on_alarm(signum, frame):
        raise Hang()

    use_alarm = threading.current_thread() is threading.main_thread() and hasattr(signal, "setitimer")
    if use_alarm:
        old = signal.signal(signal.SIGALRM, on_alarm)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    start = time.perf_counter()
    try:
        return subject.answer(task), {"seconds": round(time.perf_counter() - start, 4)}
    except Hang:
        return None, {"failure": "hang", "seconds": seconds}
    except Exception as error:              # a crash of the subject is a result, not an abort of the run
        return None, {"failure": "crash", "error": type(error).__name__}
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


def summarise(family: str, split: str, subject: Subject, rows: list[tuple[Task, Result]], wall: float,
              extra: dict | None = None) -> dict:
    counts = {o: 0 for o in OUTCOMES}
    by_kind: dict[str, dict[str, int]] = {}
    for task, result in rows:
        counts[result.outcome] += 1
        k = by_kind.setdefault(task.kind, {o: 0 for o in OUTCOMES})
        k[result.outcome] += 1
    n = len(rows)
    out = {"family": family, "split": split, "subject": subject.name, "n": n, **counts,
           "score": round(counts["right"] / n, 4) if n else None,
           "fabrication": counts["wrong"],
           "fabrication_rate": round(counts["wrong"] / n, 4) if n else None,
           "by_kind": by_kind,
           "tasks": {task.id: result.outcome for task, result in rows},
           "failures": {task.id: result.detail for task, result in rows if result.outcome in ("failed", "wrong")},
           "wall_s": round(wall, 2)}
    if extra:
        out.update(extra)
    return out


def run_tasks(family: str, split: str, subject: Subject, tasks: list[Task],
              judge: Callable[[Task, Answer, Subject], Result] | None = None, *, seconds: float = 60.0) -> list:
    rows = []
    for task in tasks:
        answer, info = run_in_process(task, subject, seconds=seconds)
        if answer is None:
            rows.append((task, Result("failed", info)))
            continue
        result = judge(task, answer, subject) if judge else score_task(task, answer)
        if answer.status != "answered" and answer.reason:
            result.detail.setdefault("reason", answer.reason[:160])
        rows.append((task, result))
    return rows


def default_score(family: str, tasks_fn: Callable[[str], list[Task]], subject: Subject, split: str) -> dict:
    start = time.perf_counter()
    rows = run_tasks(family, split, subject, tasks_fn(split))
    return summarise(family, split, subject, rows, time.perf_counter() - start)


# ---- registry and freezing ------------------------------------------------------------------------------------------

def family(name: str):
    if name not in FAMILIES:
        raise ValueError(f"unknown family {name!r} (one of {', '.join(FAMILIES)})")
    return importlib.import_module(f"{__name__}.{name}")


def check_split(split: str) -> None:
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r} (one of {', '.join(SPLITS)})")


def digests(split: str, families=FAMILIES) -> dict[str, dict]:
    """Per family: the number of tasks and the SHA-256 over their digests (the frozen identity of the split)."""
    check_split(split)
    out = {}
    for name in families:
        tasks = family(name).tasks(split)
        ids = [t.id for t in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{name}/{split}: duplicate task ids")
        joined = "\n".join(sorted(t.digest() for t in tasks))
        out[name] = {"n": len(tasks), "sha256": hashlib.sha256(joined.encode()).hexdigest()}
    return out


def run(subject: Subject, split: str, families=FAMILIES) -> dict:
    check_split(split)
    return {name: family(name).score(subject, split) for name in families}
