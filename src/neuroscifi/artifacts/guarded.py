"""Decode under operating-system limits first (OMNIMIND fix lane F1, ruling A; ADR-072 addendum).

The adapters bound what they can foresee (declared sizes, members, depth, ratio, a cooperative deadline), and a file
can still cost more than its decoder's arithmetic says: a computed SQLite column, a value repeated by reference, a
parser's own working set. So every decode of bytes the owner hands over runs twice:

1. In a CHILD process started clean (`python -P -m neuroscifi.artifacts.guarded`): the bytes on its stdin, a short
   JSON header before them, a bounded JSON verdict on its stdout (never a pickle), stderr discarded, no inherited
   file descriptor (`close_fds`), an environment of its own (HOME and TMPDIR a private empty directory that is also
   its working directory; one thread for numpy's libraries), and in the child, before anything is read:
   - `RLIMIT_AS` = 256 MiB for the interpreter and its modules (measured: about 110 MiB of address space after
     importing the adapters with one numerical thread) + 2 x the input (the bytes read from stdin and their copy in
     `decode`) + 2 x `max_decoded_bytes` (what a decoder may materialise, and one temporary of the same size that
     numpy makes when it converts or reshapes it). At the defaults and a 64 MiB input: 896 MiB.
   - `RLIMIT_CPU` = `max_seconds` + 5 s (start-up), hard limit 2 s above it; `RLIMIT_FSIZE` = 0 (it writes no
     file); `RLIMIT_CORE` = 0.
   - the parent kills it after `max_seconds` + 5 s of wall clock.
2. Only when the child finished within those bounds (a `Decoded`, or a named `ArtifactError` other than the
   decoder's own out-of-memory refusal) is the file decoded again, in this process, by `adapters.decode`. It costs
   about twice the decode time (measured in `docs/quality/evidence/omnimind_f1.json`).

Killed, over time, over memory or crashed: `Refused(rule="resource_limit")` in plain words ("reading this file
needed more memory or time than is allowed here: N MiB / N s"); nothing of the file is decoded in this process, and
the store keeps the file as an opaque artifact with that refusal recorded (`store.KEPT_REFUSALS`).

Platforms: `RLIMIT_AS` and `RLIMIT_CPU` are set where `resource` offers them and `setrlimit` accepts them (Linux;
on macOS `RLIMIT_AS` is refused by the kernel; Windows has no `resource`). Where they cannot be set, the wall-clock
kill is the only bound and the verdict says so (`memory_limit: false`); a refusal then names time only.
A child that cannot be started at all (no interpreter path, the process table full) is `Refused(rule=
"guard_unavailable")`: the file is kept, not read.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .types import ArtifactError, Decoded, Limits, Refused

INTERPRETER_BYTES = 256 << 20       # the child's own interpreter, numpy and the adapters, with room (see the docstring)
STARTUP_SECONDS = 5.0               # the child's start-up and imports, over the decode's own max_seconds
VERDICT_MAX = 16 << 10              # the parent reads at most this much of the child's stdout
_HEADER = struct.Struct(">I")
_SRC = str(Path(__file__).resolve().parents[2])     # the directory that holds this `neuroscifi` package


def address_space(limits: Limits, size: int) -> int:
    """RLIMIT_AS for a child decoding `size` bytes under `limits` (the factors are the module docstring's)."""
    return INTERPRETER_BYTES + 2 * int(size) + 2 * int(limits.max_decoded_bytes)


def wall_seconds(limits: Limits) -> float:
    return float(limits.max_seconds) + STARTUP_SECONDS


def _mib(n: int) -> int:
    return int(round(n / (1 << 20)))


def _limit_words(limits: Limits, size: int, memory: bool) -> str:
    seconds = f"{wall_seconds(limits):g} s"
    if memory:
        return f"{_mib(address_space(limits, size))} MiB / {seconds}"
    return f"{seconds} (no memory limit can be set on this system)"


def _refused(limits: Limits, size: int, memory: bool, what: str) -> Refused:
    error = Refused(f"reading this file needed more memory or time than is allowed here: "
                    f"{_limit_words(limits, size, memory)} ({what})", rule="resource_limit")
    error.guard = {"what": what, "memory_limit": memory}
    return error


def check(data: bytes, *, name: str | None = None, declared: str | None = None, limits: Limits = Limits(),
          external: tuple[str, ...] = ()) -> dict:
    """Run the decode in a child under limits. Returns the child's verdict (a small dict) when it finished within
    bounds; raises `Refused(rule="resource_limit")` when it did not, `Refused(rule="guard_unavailable")` when no
    child could be started."""
    data = bytes(data)
    if len(data) > limits.max_bytes or limits.max_depth < 0:
        return {"v": 1, "status": "not_run"}       # adapters.decode refuses these before it reads anything
    header = json.dumps({"name": name, "declared": declared, "external": list(external), "size": len(data),
                         "address_space": address_space(limits, len(data)),
                         "limits": {k: getattr(limits, k) for k in limits.__dataclass_fields__}}).encode("utf-8")
    payload = _HEADER.pack(len(header)) + header + data
    work = tempfile.mkdtemp(prefix="nsf-guard-")
    env = {"PATH": os.defpath, "PYTHONPATH": _SRC, "HOME": work, "TMPDIR": work, "LC_ALL": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONHASHSEED": "0",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    memory = True
    try:
        if not sys.executable:
            raise OSError("no interpreter path")
        try:
            proc = subprocess.run([sys.executable, "-P", "-m", "neuroscifi.artifacts.guarded"], input=payload,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=work, env=env,
                                  close_fds=True, timeout=wall_seconds(limits), check=False)
        except subprocess.TimeoutExpired:
            raise _refused(limits, len(data), memory, "stopped after the time allowed") from None
        except OSError as error:
            raise Refused("a separate process to read this file safely could not be started "
                          f"({error.strerror or 'the system refused'}); the file is kept, not read",
                          rule="guard_unavailable") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out = proc.stdout[-VERDICT_MAX:] if len(proc.stdout) <= VERDICT_MAX else b""
    verdict = None
    for line in reversed(out.decode("utf-8", "replace").splitlines()):
        if line.startswith("{"):
            try:
                verdict = json.loads(line)
            except ValueError:
                verdict = None
            break
    if not isinstance(verdict, dict) or verdict.get("v") != 1:
        what = "stopped by the system" if proc.returncode < 0 else "the reading process ended without an answer"
        raise _refused(limits, len(data), memory, what)
    memory = bool(verdict.get("memory_limit"))
    if verdict.get("status") == "resources":
        raise _refused(limits, len(data), memory, "it ran out of the memory allowed")
    if verdict.get("status") not in ("decoded", "error") or proc.returncode != 0:
        raise _refused(limits, len(data), memory, "the reading process ended without an answer")
    if verdict.get("status") == "error" and verdict.get("reason") == "timeout":
        # the decoder's own deadline stopped it: the same decode here would only spend that time again
        raise ArtifactError(f"decoding took longer than the limit of {limits.max_seconds:g} s", reason="timeout")
    return verdict


def decode(data: bytes, *, name: str | None = None, declared: str | None = None,
           limits: Limits = Limits()) -> Decoded:
    """`adapters.decode`, after the same decode finished within bounds in a child (module docstring). Raises what
    `adapters.decode` raises, and `Refused(rule="resource_limit" | "guard_unavailable")`."""
    from . import adapters
    if ENABLED:
        check(data, name=name, declared=declared, limits=limits, external=adapters.loaded_external())
    return adapters.decode(data, name=name, declared=declared, limits=limits)


# Tests that exercise everything but the guard switch it off for speed (tests/artifacts/conftest.py); the owner
# cannot: it is not a setting, and nothing in the product writes it.
ENABLED = True


# ---------------------------------------------------------------------------------------------------------------
# The child


def _set_limits(limits: Limits, cap: int) -> tuple[bool, bool]:
    try:
        import resource
    except ImportError:
        return False, False
    memory = cpu = False
    try:
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        memory = True
    except (ValueError, OSError, AttributeError):
        pass
    try:
        soft = int(limits.max_seconds + STARTUP_SECONDS) + 1
        resource.setrlimit(resource.RLIMIT_CPU, (soft, soft + 2))
        cpu = True
    except (ValueError, OSError, AttributeError):
        pass
    for name in ("RLIMIT_FSIZE", "RLIMIT_CORE"):
        try:
            resource.setrlimit(getattr(resource, name), (0, 0))
        except (ValueError, OSError, AttributeError):
            pass
    return memory, cpu


def _read_exact(stream, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        part = stream.read(n - len(out))
        if not part:
            break
        out += part
    return bytes(out)


def _child() -> dict:
    stdin = sys.stdin.buffer
    (length,) = _HEADER.unpack(_read_exact(stdin, _HEADER.size))
    if length > 1 << 20:
        return {"v": 1, "status": "bad_input"}
    header = json.loads(_read_exact(stdin, length))
    fields = {k: v for k, v in header["limits"].items() if k in Limits.__dataclass_fields__}
    limits = Limits(**fields)
    size = int(header["size"])
    if not 0 <= size <= limits.max_bytes:
        return {"v": 1, "status": "bad_input"}
    memory, cpu = _set_limits(limits, int(header["address_space"]))
    data = _read_exact(stdin, size + 1)
    if len(data) != size:
        return {"v": 1, "status": "bad_input"}
    from . import adapters
    if header.get("external"):
        adapters.load_external(header["external"])
    started = time.perf_counter()
    out = {"v": 1, "memory_limit": memory, "cpu_limit": cpu}
    try:
        decoded = adapters.decode(data, name=header.get("name"), declared=header.get("declared"), limits=limits)
        out |= {"status": "decoded", "kind": str(getattr(decoded.representation, "kind", ""))[:32],
                "partial": bool(decoded.partial)}
    except Refused as error:
        out |= {"status": "resources" if error.rule == "resources" else "error", "reason": error.reason[:32],
                "rule": str(error.rule)[:64]}
    except ArtifactError as error:
        out |= {"status": "error", "reason": str(error.reason)[:32]}
    except MemoryError:
        out |= {"status": "resources"}
    out["seconds"] = round(time.perf_counter() - started, 3)
    try:
        import resource
        out["peak_kib"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        pass
    return out


def main() -> int:
    try:
        verdict = _child()
    except MemoryError:
        verdict = {"v": 1, "status": "resources"}
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
