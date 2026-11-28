"""Run one task in its own process, under bounds, so that a crash, a hang or a runaway allocation is a RESULT of the
benchmark and never an abort of it (family `hostile`; usable by any family).

Parent (`run_isolated`): writes the task's files and a manifest into a fresh directory under a scratch root, starts
`python -m neuroscifi.acceptance.omni.isolate MANIFEST SUBJECT` with its working directory, HOME and TMPDIR inside
that directory, waits at most `seconds`, and afterwards checks that nothing was written outside the task's working
directory: the traversal targets a task names (`escape_paths`) must not exist, the scratch root must hold nothing
new, and no symbolic link may have appeared.

Child (`main`): caps its address space (RLIMIT_AS), traces Python allocations (tracemalloc: numpy's buffers are
traced too), asks the subject, and prints one JSON line: status, a JSON-safe value, reason, peak traced bytes, peak
resident set, seconds, or the failure (crash with the exception's type, memory).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

from . import Answer, Task

ADDRESS_SPACE = 6 << 30          # the child's hard cap (a net under the measured bound, not the bound itself)
SRC = Path(__file__).resolve().parents[3]


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def run_isolated(task: Task, subject_name: str, root: Path, *, seconds: float, escape_paths=()) -> dict:
    """The child's report, plus "failure" (hang, crash, escape) when the process or the filesystem says so."""
    slot = root / ("task-" + hashlib.sha256(task.id.encode()).hexdigest()[:16])
    inbox, work = slot / "in", slot / "work"
    inbox.mkdir(parents=True)
    (work / "tmp").mkdir(parents=True)
    names = []
    for i, (name, data) in enumerate(task.files.items()):
        (inbox / f"{i:03d}").write_bytes(data)
        names.append(name)
    manifest = {"id": task.id, "family": task.family, "names": names, "declared": dict(task.declared),
                "question": task.question, "expect": task.expect, "tolerance": task.tolerance, "kind": task.kind}
    (inbox / "manifest.json").write_text(json.dumps(manifest))
    escape_paths = [p if os.path.isabs(p) else os.path.normpath(work / p) for p in escape_paths]
    before = _snapshot(root)
    existed = {p: os.path.lexists(p) for p in escape_paths}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(SRC), "HOME": str(work),
           "TMPDIR": str(work / "tmp"), "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8"}
    start = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, "-m", "neuroscifi.acceptance.omni.isolate", str(inbox), subject_name],
                              cwd=work, env=env, capture_output=True, timeout=seconds)
        elapsed = time.perf_counter() - start
        lines = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")]
        report = json.loads(lines[-1]) if lines else {"failure": "crash", "error": f"exit {proc.returncode}"}
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        report = {"failure": "hang"}
    report["wall_s"] = round(elapsed, 3)
    escaped = [p for p in escape_paths if os.path.lexists(p) and not existed[p]]
    new_outside = sorted(p for p in _snapshot(root) - before if not p.startswith(slot.name))
    links = [str(p.relative_to(work)) for p in work.rglob("*") if p.is_symlink()]
    if escaped or new_outside or links:
        report["failure"] = "escape"
        report["escape"] = (escaped + new_outside + links)[:5]
        for p in escaped:
            if os.path.islink(p) or os.path.isfile(p):
                os.unlink(p)
    shutil.rmtree(slot, ignore_errors=True)
    return report


def answer_from(report: dict) -> Answer | None:
    if "failure" in report:
        return None
    return Answer(report.get("value"), report["status"], report.get("reason", ""))


def _json_safe(value):
    if isinstance(value, dict) and "bytes" in value:
        return {"media_type": value.get("media_type"), "size": len(value["bytes"])}
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)[:200]


def main(argv=None) -> int:
    inbox, subject_name = (argv or sys.argv[1:])[:2]
    inbox = Path(inbox)
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, ADDRESS_SPACE))
    manifest = json.loads((inbox / "manifest.json").read_text())
    files = {name: (inbox / f"{i:03d}").read_bytes() for i, name in enumerate(manifest["names"])}
    task = Task(manifest["id"], manifest["family"], files, manifest["declared"], manifest["question"],
                manifest["expect"], manifest["tolerance"], manifest["kind"])
    import tracemalloc
    from . import subject as subjects
    subject = subjects.get(subject_name)
    tracemalloc.start()
    start = time.perf_counter()
    try:
        answer = subject.answer(task)
        report = {"status": answer.status, "value": _json_safe(answer.value), "reason": answer.reason[:200]}
    except MemoryError:
        report = {"failure": "memory", "error": "MemoryError"}
    except BaseException as error:              # noqa: BLE001 - every failure is a result here
        report = {"failure": "crash", "error": type(error).__name__}
    report["seconds"] = round(time.perf_counter() - start, 4)
    report["peak_traced"] = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    report["maxrss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sys.stdout.write(json.dumps(report) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
