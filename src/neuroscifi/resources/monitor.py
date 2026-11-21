"""Resource monitoring on the host (RD-46, docs/architecture/RESOURCE_SCHEDULING.md).

What is measured, and how (every read is local, bounded and never raises):
- memory()       /proc/meminfo: MemTotal, MemAvailable, SwapTotal, SwapFree (bytes), plus the kernel's memory
                 pressure stall averages from /proc/pressure/memory when present. On a unified-memory machine (DGX
                 Spark / GB10) GPU allocations come out of the same memory, so MemAvailable is the deciding number.
- gpu()          `nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu` (GB10 reports memory "[N/A]":
                 recorded as unreadable, never guessed) and `nvidia-smi --query-compute-apps` (per-process GPU
                 memory, which GB10 does report).
- containers()   `docker stats --no-stream` for the neuroscifi-* containers (cgroup memory: on unified memory a
                 container's CUDA allocations are NOT in this number; compute-apps carries them).
- cold_starts()  each running neuroscifi-* container's last start: State.StartedAt to its first readiness log
                 line ("Application startup complete." for vLLM servers, "tts: ready" for the TTS server), read with
                 `docker logs --since` bounded to COLD_START_WINDOW. A measurement of the start that happened, not an
                 estimate.

pressure(mem, gpu) -> (level, reasons): "ok" | "elevated" | "critical" (thresholds()):
  critical   MemAvailable < max(CRITICAL_GIB, CRITICAL_FRACTION x MemTotal), or a readable GPU memory use >=
             GPU_CRITICAL of its total
  elevated   MemAvailable < max(ELEVATED_GIB, ELEVATED_FRACTION x MemTotal), or GPU use >= GPU_ELEVATED
  ok         otherwise; "unknown" when /proc/meminfo cannot be read
NSF_RESOURCE_ELEVATED_GIB / NSF_RESOURCE_CRITICAL_GIB replace the absolute floors (GiB, positive numbers).

sample(full=False) caches the cheap part (memory) for MEMORY_TTL seconds and the subprocess part (nvidia-smi,
docker) for FULL_TTL seconds, so gates on hot paths cost one small file read at most."""
from __future__ import annotations

from datetime import datetime
import json
import os
import shutil
import subprocess
import threading
import time

GIB = 1024 ** 3
ELEVATED_GIB = 16.0            # below this much MemAvailable (or 15 % of MemTotal): elevated
ELEVATED_FRACTION = 0.15
CRITICAL_GIB = 6.0             # below this much MemAvailable (or 6 % of MemTotal): critical
CRITICAL_FRACTION = 0.06
GPU_ELEVATED = 0.90            # share of a readable GPU memory total in use
GPU_CRITICAL = 0.97
LEVELS = ("ok", "elevated", "critical")
CONTAINER_PREFIX = "neuroscifi-"
MEMORY_TTL = 2.0
FULL_TTL = 15.0
COMMAND_TIMEOUT = 8.0
COLD_START_WINDOW = 900        # seconds of log after a container start searched for its readiness line
READY_MARKERS = ("Application startup complete.", "tts: ready")
MEMINFO = "/proc/meminfo"
PSI_MEMORY = "/proc/pressure/memory"

_LOCK = threading.Lock()
_CACHE: dict = {}


def _run(args, timeout=COMMAND_TIMEOUT, *, merge=False) -> str | None:
    """stdout of a successful command (with `merge`, stderr too: `docker logs` replays a container's stderr
    there), else None. Never raises."""
    if shutil.which(args[0]) is None:
        return None
    try:
        out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge else subprocess.PIPE,
                             text=True, errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def memory(path=MEMINFO, psi_path=PSI_MEMORY) -> dict | None:
    try:
        with open(path, encoding="ascii") as fh:
            rows = dict(line.split(":", 1) for line in fh if ":" in line)
    except OSError:
        return None

    def kib(key):
        try:
            return int(rows[key].split()[0]) * 1024
        except (KeyError, ValueError, IndexError):
            return None
    out = {"total_bytes": kib("MemTotal"), "available_bytes": kib("MemAvailable"),
           "swap_total_bytes": kib("SwapTotal"), "swap_free_bytes": kib("SwapFree"), "psi": None}
    if out["total_bytes"] is None or out["available_bytes"] is None:
        return None
    try:
        with open(psi_path, encoding="ascii") as fh:
            psi = {}
            for line in fh:
                kind, *fields = line.split()
                values = dict(f.split("=", 1) for f in fields)
                psi[f"{kind}_avg10"] = float(values["avg10"])
        out["psi"] = psi
    except (OSError, ValueError, KeyError):
        pass
    return out


def _number(text: str):
    try:
        return float(text.strip())
    except ValueError:
        return None          # "[N/A]" (GB10 unified memory) and the like: unreadable, not zero


def gpu() -> dict:
    out = {"available": shutil.which("nvidia-smi") is not None, "memory_used_bytes": None,
           "memory_total_bytes": None, "utilization_percent": None, "memory_readable": False, "compute_apps": []}
    if not out["available"]:
        return out
    text = _run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                 "--format=csv,noheader,nounits"])
    if text and text.strip():
        used, total, util = (list(map(_number, text.splitlines()[0].split(","))) + [None, None, None])[:3]
        if used is not None and total:
            out.update(memory_used_bytes=int(used * 1024 * 1024), memory_total_bytes=int(total * 1024 * 1024),
                       memory_readable=True)
        out["utilization_percent"] = util
    text = _run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    for line in (text or "").splitlines():
        head, _, mib = line.rpartition(",")      # the process name may itself contain commas
        pid, _, name = head.partition(",")
        used = _number(mib)
        if pid.strip().isdigit():
            out["compute_apps"].append({"pid": int(pid), "name": name.strip().split(" ")[0][:80],
                                        "memory_bytes": None if used is None else int(used * 1024 * 1024)})
    return out


def _size(text: str) -> int | None:
    units = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4, "kB": 1000, "MB": 1000 ** 2,
             "GB": 1000 ** 3, "TB": 1000 ** 4}
    text = text.strip()
    for unit in sorted(units, key=len, reverse=True):
        if text.endswith(unit):
            try:
                return int(float(text[: -len(unit)]) * units[unit])
            except ValueError:
                return None
    return None


def containers() -> list[dict] | None:
    """Memory and CPU of the running neuroscifi-* containers, or None without a usable docker CLI."""
    names = _run(["docker", "ps", "--format", "{{.Names}}"])
    if names is None:
        return None
    ours = sorted(n for n in names.split() if n.startswith(CONTAINER_PREFIX))
    if not ours:
        return []
    text = _run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ours], timeout=COMMAND_TIMEOUT * 2)
    out = []
    for line in (text or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        used, _, limit = str(row.get("MemUsage", "")).partition("/")
        try:
            cpu = float(str(row.get("CPUPerc", "")).rstrip("%"))
        except ValueError:
            cpu = None
        out.append({"name": row.get("Name"), "memory_bytes": _size(used), "memory_limit_bytes": _size(limit),
                    "cpu_percent": cpu})
    return out


def _ts(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    head, _, frac = text.rstrip("Z").partition(".")
    try:
        base = datetime.fromisoformat(head + "+00:00").timestamp()
    except ValueError:
        return None
    return base + (float("0." + frac[:6]) if frac[:6].isdigit() else 0.0)


def cold_starts(names=None) -> list[dict] | None:
    """[{name, started_at, ready_at, seconds}] for each running neuroscifi-* container (or `names`): the time from
    its last start to its first readiness log line; seconds None when no readiness line is in the window."""
    if names is None:
        listed = _run(["docker", "ps", "--format", "{{.Names}}"])
        if listed is None:
            return None
        names = sorted(n for n in listed.split() if n.startswith(CONTAINER_PREFIX))
    out = []
    for name in names:
        started = _run(["docker", "inspect", "-f", "{{.State.StartedAt}}", name])
        at = _ts(started or "")
        if at is None:
            continue
        logs = _run(["docker", "logs", "--timestamps", "--since", str(int(at)), "--until",
                     str(int(at) + COLD_START_WINDOW), name], timeout=COMMAND_TIMEOUT * 2, merge=True) or ""
        ready = None
        for line in logs.splitlines():
            stamp, _, message = line.partition(" ")
            if any(m in message for m in READY_MARKERS):
                ready = _ts(stamp)
                break
        out.append({"name": name, "started_at": round(at, 3), "ready_at": None if ready is None else round(ready, 3),
                    "seconds": None if ready is None else round(ready - at, 1)})
    return out


def _floor(env: str, default: float) -> float:
    try:
        value = float(os.environ.get(env, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def thresholds(total_bytes: int | None) -> dict:
    total = total_bytes or 0
    return {"elevated_below_bytes": int(max(_floor("NSF_RESOURCE_ELEVATED_GIB", ELEVATED_GIB) * GIB,
                                            ELEVATED_FRACTION * total)),
            "critical_below_bytes": int(max(_floor("NSF_RESOURCE_CRITICAL_GIB", CRITICAL_GIB) * GIB,
                                            CRITICAL_FRACTION * total)),
            "gpu_elevated_share": GPU_ELEVATED, "gpu_critical_share": GPU_CRITICAL}


def _gib(n) -> str:
    return f"{n / GIB:.1f} GiB"


def pressure(mem: dict | None, gpu_info: dict | None = None) -> tuple[str, list[str]]:
    if not mem:
        return "unknown", ["/proc/meminfo is not readable"]
    limits = thresholds(mem["total_bytes"])
    avail = mem["available_bytes"]
    level, reasons = "ok", []
    if avail < limits["critical_below_bytes"]:
        level = "critical"
        reasons.append(f"memory available {_gib(avail)} of {_gib(mem['total_bytes'])} is below the critical "
                       f"threshold {_gib(limits['critical_below_bytes'])}")
    elif avail < limits["elevated_below_bytes"]:
        level = "elevated"
        reasons.append(f"memory available {_gib(avail)} of {_gib(mem['total_bytes'])} is below the elevated "
                       f"threshold {_gib(limits['elevated_below_bytes'])}")
    if gpu_info and gpu_info.get("memory_readable"):
        share = gpu_info["memory_used_bytes"] / gpu_info["memory_total_bytes"]
        if share >= GPU_CRITICAL:
            level = "critical"
            reasons.append(f"GPU memory {share:.0%} in use (critical at {GPU_CRITICAL:.0%})")
        elif share >= GPU_ELEVATED and level == "ok":
            level = "elevated"
            reasons.append(f"GPU memory {share:.0%} in use (elevated at {GPU_ELEVATED:.0%})")
    return level, reasons


def _cached(key, ttl, compute):
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    value = compute()
    with _LOCK:
        _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def sample(*, full: bool = False) -> dict:
    """{"at", "memory", "gpu", "containers", "level", "reasons", "thresholds"}; gpu and containers are None
    unless `full` (subprocess calls)."""
    mem = _cached("memory", MEMORY_TTL, memory)
    gpu_info = _cached("gpu", FULL_TTL, gpu) if full else None
    level, reasons = pressure(mem, gpu_info)
    return {"at": time.time(), "memory": mem, "gpu": gpu_info,
            "containers": _cached("containers", FULL_TTL, containers) if full else None,
            "level": level, "reasons": reasons, "thresholds": thresholds((mem or {}).get("total_bytes"))}


__all__ = ["CRITICAL_FRACTION", "CRITICAL_GIB", "ELEVATED_FRACTION", "ELEVATED_GIB", "GPU_CRITICAL", "GPU_ELEVATED",
           "LEVELS", "clear_cache", "cold_starts", "containers", "gpu", "memory", "pressure", "sample", "thresholds"]
