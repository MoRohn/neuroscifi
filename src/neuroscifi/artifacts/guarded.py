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
   decoder's own out-of-memory refusal) is the file decoded again, in this process, by `adapters.decode` (`decode`):
   for what needs the content itself (make, facts, relate, the console's view).
   Keeping a file (`store.add`) needs only its index and its words, so `summarize` asks the child for them instead
   (fix lane F4, code review N1): the child returns, in its JSON verdict, what was detected, the representation's
   `describe()` (at most DESCRIPTION_MAX bytes), its notes, `partial`, its text (at most TEXT_MAX bytes of UTF-8), the
   formats that ended undecoded, or the error it raised; the parent validates every field's type and size and decodes
   nothing. Measured on the code reviewer's inputs (`docs/quality/evidence/omnimind_f4.json`): `add` costs one
   decode plus the child's start-up (about 0.1 s: the interpreter, numpy and the adapters); a command that needs the
   content costs two decodes plus the start-up.
- The child also reports which approved external adapters it loaded; when they differ from the parent's, the file is
  not read (`Refused(rule="external_unguarded")`, kept unread): an adapter that loads only in the parent would decode
  there with no guard (security review, second pass, N-new-4).

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
from dataclasses import dataclass
from pathlib import Path

from .types import ArtifactError, Decoded, Limits, Refused

INTERPRETER_BYTES = 256 << 20       # the child's own interpreter, numpy and the adapters, with room (see the docstring)
STARTUP_SECONDS = 5.0               # the child's start-up and imports, over the decode's own max_seconds
VERDICT_MAX = 16 << 10              # the parent reads at most this much of the child's stdout
SUMMARY_MAX = 4 << 20               # ... and this much of a summary verdict (index and words, JSON-escaped)
DESCRIPTION_MAX = 64 << 10          # describe() is bounded to 32 KiB by contract (types.py); more is not sent
TEXT_MAX = 256 << 10                # the text layer the store keeps (store.TEXT_MAX_BYTES)
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
          external: tuple[str, ...] = (), summary: bool = False) -> dict:
    """Run the decode in a child under limits. Returns the child's verdict (a small dict) when it finished within
    bounds; raises `Refused(rule="resource_limit")` when it did not, `Refused(rule="guard_unavailable")` when no
    child could be started."""
    data = bytes(data)
    if len(data) > limits.max_bytes or limits.max_depth < 0:
        return {"v": 1, "status": "not_run"}       # adapters.decode refuses these before it reads anything
    header = json.dumps({"name": name, "declared": declared, "external": list(external), "size": len(data),
                         "mode": "summary" if summary else "check",
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
    most = SUMMARY_MAX if summary else VERDICT_MAX
    out = proc.stdout if len(proc.stdout) <= most else b""
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
    if sorted(str(e) for e in verdict.get("external") or ()) != sorted(external):
        raise Refused("an external reader you approved could not be loaded in the separate process that reads files "
                      "safely, so it is not used: the file is kept, not read", rule="external_unguarded")
    return verdict


class Described:
    """A representation as the guarded child described it (summary mode): its kind, `describe()` and `text()`, for
    the store's index and text layer. It holds no content beyond those, and nothing here decodes."""

    def __init__(self, kind: str, description: dict, text: str):
        self.kind, self._description, self._text = kind, description, text

    def describe(self) -> dict:
        return self._description

    def text(self) -> str:
        return self._text


@dataclass(frozen=True)
class Summary:
    """What `summarize` returns in place of a `Decoded`: the same fields the store reads, a `Described` inside."""

    detected: object
    representation: Described
    notes: tuple[str, ...]
    partial: bool
    flagged: tuple[str, ...]


def _strs(value, n: int, length: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("not a list")
    return tuple(str(v)[:length] for v in value[:n])


def _summary_of(verdict: dict):
    """(Summary | None, the error to raise | None, gaps) from a validated summary verdict."""
    from .types import FAMILIES, Detected, HeaderOnly, Unsupported
    gaps = [(str(f)[:64], str(o)[:16]) for f, o in verdict.get("gaps") or () if o in ("opaque", "unsupported")][:64]
    if verdict.get("status") == "error":
        e = verdict.get("error") or {}
        message, reason = str(e.get("message", ""))[:300], str(e.get("reason", "malformed"))[:32]
        kind = e.get("class")
        if kind == "refused":
            error = Refused(message, rule=str(e.get("rule"))[:64], reason=reason)
            if e.get("findings"):
                error.findings = _strs(e["findings"], 64, 200)
        elif kind == "header_only":
            facts = e.get("facts") if isinstance(e.get("facts"), dict) else {}
            error = HeaderOnly(message, facts=facts, needs=_strs(e.get("needs") or [], 16, 80), size=0,
                               words=str(e.get("words") or "")[:TEXT_MAX])
        elif kind == "unsupported":
            error = Unsupported(message, needs=_strs(e.get("needs") or [], 16, 80), reason=reason)
        else:
            error = ArtifactError(message, reason=reason)
        return None, error, gaps
    d = verdict["detected"]
    if d["family"] not in FAMILIES:
        raise ValueError("unknown family")
    detected = Detected(family=str(d["family"]), format=str(d["format"])[:64], media_type=str(d["media_type"])[:128],
                        basis=str(d["basis"])[:16], container=None if d.get("container") is None
                        else str(d["container"])[:32], reasons=_strs(d.get("reasons") or [], 16, 300))
    description = verdict.get("description")
    if not isinstance(description, dict):
        raise ValueError("no description")
    text = verdict.get("text") or ""
    if not isinstance(text, str) or len(text.encode("utf-8")) > TEXT_MAX:
        raise ValueError("text over its bound")
    rep = Described(str(verdict.get("kind", ""))[:32], description, text)
    decoded = Summary(detected, rep, _strs(verdict.get("notes") or [], 32, 500), bool(verdict.get("partial")),
                      _strs(verdict.get("flagged") or [], 16, 64))
    return decoded, None, gaps


def summarize(data: bytes, *, name: str | None = None, declared: str | None = None,
              limits: Limits = Limits()) -> tuple[Decoded | Summary, list]:
    """(Summary, gaps) for keeping a file: decoded ONCE, in the child, which returns the index and words (module
    docstring); the representation is a `Described`. Raises what `adapters.decode` would (rebuilt from the verdict:
    Refused with its rule and findings, HeaderOnly with its facts and words, Unsupported with what it needs) and the
    guard's own refusals. With the guard switched off (tests), the decode runs here. The gaps are those the child saw
    (in this process `adapters.on_gap` hooks see them directly)."""
    from . import adapters
    if not ENABLED:                 # in this process: the caller's own gap hook sees the gaps
        return adapters.decode(data, name=name, declared=declared, limits=limits), []
    verdict = check(data, name=name, declared=declared, limits=limits, external=adapters.loaded_external(),
                    summary=True)
    if verdict.get("status") == "not_run":
        return adapters.decode(data, name=name, declared=declared, limits=limits), []    # refuses before reading
    try:
        decoded, error, gaps = _summary_of(verdict)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused("the separate process that reads files safely gave an answer that is not well formed; the "
                      "file is kept, not read", rule="guard_unavailable") from None
    if error is not None:
        error.gaps = gaps
        raise error
    return decoded, gaps


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
    summary = header.get("mode") == "summary"
    memory, cpu = _set_limits(limits, int(header["address_space"]))
    data = _read_exact(stdin, size + 1)
    if len(data) != size:
        return {"v": 1, "status": "bad_input"}
    from . import adapters
    if header.get("external"):
        adapters.load_external(header["external"])
    started = time.perf_counter()
    out = {"v": 1, "memory_limit": memory, "cpu_limit": cpu, "external": list(adapters.loaded_external())}
    gaps: list = []
    remove = adapters.on_gap(lambda detected, outcome: gaps.append((detected.format, outcome)))
    try:
        decoded = adapters.decode(data, name=header.get("name"), declared=header.get("declared"), limits=limits)
        out |= {"status": "decoded", "kind": str(getattr(decoded.representation, "kind", ""))[:32],
                "partial": bool(decoded.partial)}
        if summary:
            out |= _describe(decoded)
    except Refused as error:
        out |= {"status": "resources" if error.rule == "resources" else "error", "reason": error.reason[:32],
                "rule": str(error.rule)[:64]}
        if summary:
            out["error"] = {"class": "refused", "reason": error.reason, "rule": error.rule, "message": str(error),
                            "findings": [str(f) for f in getattr(error, "findings", ()) or ()][:64]}
    except ArtifactError as error:
        out |= {"status": "error", "reason": str(error.reason)[:32]}
        if summary:
            from .types import HeaderOnly, Unsupported
            out["error"] = {"class": "header_only" if isinstance(error, HeaderOnly) else
                            "unsupported" if isinstance(error, Unsupported) else "other",
                            "reason": error.reason, "message": str(error),
                            "needs": list(getattr(error, "needs", ()))[:16]}
            if isinstance(error, HeaderOnly):
                from .types import bounded
                out["error"] |= {"facts": bounded(error.facts), "words": error.words}
    except MemoryError:
        out |= {"status": "resources"}
    finally:
        remove()
    if summary:
        out["gaps"] = gaps[:64]
    out["seconds"] = round(time.perf_counter() - started, 3)
    try:
        import resource
        out["peak_kib"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        pass
    return out


def _describe(decoded) -> dict:
    """The summary of a decode for the store (summary mode): what the parent's index and text layer need."""
    from .types import bounded
    rep, d = decoded.representation, decoded.detected
    try:
        description = bounded(rep.describe())
        if len(json.dumps(description, ensure_ascii=False, allow_nan=False)) > DESCRIPTION_MAX:
            raise ValueError("over the bound")
    except Exception as error:      # as store._index records a representation that cannot describe itself
        description = {"kind": str(getattr(rep, "kind", "")), "error": type(error).__name__}
    try:
        text = rep.text() or ""
    except Exception:
        text = ""
    raw = text.encode("utf-8", "replace")
    if len(raw) > TEXT_MAX:
        text = raw[:TEXT_MAX].decode("utf-8", "ignore")
    else:
        text = raw.decode("utf-8")
    return {"detected": {"family": d.family, "format": d.format, "media_type": d.media_type, "basis": d.basis,
                         "container": d.container, "reasons": list(d.reasons)[:16]},
            "description": description, "notes": [str(n) for n in decoded.notes][:32], "text": text,
            "flagged": list(decoded.flagged)[:16]}


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
