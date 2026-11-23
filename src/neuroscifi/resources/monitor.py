"""Resource monitoring on the host (RD-46, docs/architecture/RESOURCE_SCHEDULING.md).

What is measured, and how (every read is local, bounded and never raises):
- memory()       /proc/meminfo: MemTotal, MemAvailable, SwapTotal, SwapFree (bytes), plus the kernel's memory
                 pressure stall averages from /proc/pressure/memory when present. On a unified-memory machine (DGX
                 Spark / GB10) GPU allocations come out of the same memory, so MemAvailable is the deciding number.
- gpu()          `nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw` and the
                 clock event (throttle) reasons in the same call (GB10 reports memory "[N/A]": recorded as
                 unreadable, never guessed; a driver that refuses the health fields gets the first three alone), and
                 `nvidia-smi --query-compute-apps` (per-process GPU memory, which GB10 does report).
- load()         /proc/loadavg: the 1, 5 and 15 minute load averages, with the CPU count to read them against.
- containers()   `docker stats --no-stream` for the neuroscifi-* containers (cgroup memory: on unified memory a
                 container's CUDA allocations are NOT in this number; compute-apps carries them).
- cold_starts()  each running neuroscifi-* container's last start: State.StartedAt to its first readiness log
                 line ("Application startup complete." for vLLM servers, "tts: ready" for the TTS server), read with
                 `docker logs --since` bounded to COLD_START_WINDOW. A measurement of the start that happened, not an
                 estimate.
- inference(url) the inference server's state from vLLM's Prometheus /metrics (`prometheus()` parses it; the idle
                 policy reads in-flight and served requests with the same parser): requests running and waiting,
                 KV-cache use, and since the server started the mean queue, prefill, decode, first-token and request
                 times (histogram _sum / _count), prompt tokens with the share served from the prefix cache,
                 generation tokens, preemptions and requests by finish reason; plus the same over the interval since
                 the previous reading in this process, because lifetime means hide a current problem.

pressure(mem, gpu) -> (level, reasons): "ok" | "elevated" | "critical" (thresholds()):
  critical   MemAvailable < max(CRITICAL_GIB, CRITICAL_FRACTION x MemTotal), or a readable GPU memory use >=
             GPU_CRITICAL of its total
  elevated   MemAvailable < max(ELEVATED_GIB, ELEVATED_FRACTION x MemTotal), or GPU use >= GPU_ELEVATED
  ok         otherwise; "unknown" when /proc/meminfo cannot be read
NSF_RESOURCE_ELEVATED_GIB / NSF_RESOURCE_CRITICAL_GIB replace the absolute floors (GiB, positive numbers).

sample(full=False) caches the cheap part (memory) for MEMORY_TTL seconds and the subprocess part (nvidia-smi,
docker) for FULL_TTL seconds, so gates on hot paths cost one small file read at most. The /metrics scrape is cached
for FULL_TTL seconds too (inference_cached) and only `full` readings (nsf resources, GET /api/resources) make it."""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

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
LOADAVG = "/proc/loadavg"
GPU_QUERY = "memory.used,memory.total,utilization.gpu"
GPU_HEALTH_QUERY = GPU_QUERY + (",temperature.gpu,power.draw,clocks_throttle_reasons.active,"
                                "clocks_throttle_reasons.sw_thermal_slowdown,clocks_throttle_reasons.hw_thermal_slowdown,"
                                "clocks_throttle_reasons.sw_power_cap,clocks_throttle_reasons.hw_slowdown")
METRICS_TIMEOUT = 2.0
METRICS_LIMIT = 4 << 20        # bytes of /metrics read at most
# vLLM histogram families (seconds) reported as means: _sum / _count
VLLM_STAGES = (("queue", "vllm:request_queue_time_seconds"), ("prefill", "vllm:request_prefill_time_seconds"),
               ("decode", "vllm:request_decode_time_seconds"), ("first_token", "vllm:time_to_first_token_seconds"),
               ("request", "vllm:e2e_request_latency_seconds"))
VLLM_COUNTERS = (("prompt_tokens", "vllm:prompt_tokens_total"), ("generation_tokens", "vllm:generation_tokens_total"),
                 ("prefix_cache_hits", "vllm:prefix_cache_hits_total"),
                 ("prefix_cache_queries", "vllm:prefix_cache_queries_total"),
                 ("preemptions", "vllm:num_preemptions_total"))
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')

_LOCK = threading.Lock()
_CACHE: dict = {}
_PREVIOUS: dict = {}           # url -> the last /metrics counters read in this process (the interval's start)


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


OVERFLOW_UID = 65534        # what an account outside this user namespace reads as (PrivateUsers=yes): not identifiable
LISTEN_CACHE_SECONDS = 1.0  # one parse of the socket tables serves every check of that second (review S7)
_LISTEN_CACHE: dict = {}    # folder -> (monotonic time, {port: [(address, uid)]} or None)


def _proc_address(text) -> str | None:
    """The local-address column of /proc/net/tcp ("0100007F") or tcp6 (32 hex digits) as an address string. The
    kernel prints each 32-bit word of the address as this machine reads it, so each word is packed back in native
    byte order. An IPv4-mapped IPv6 address is given as its IPv4 address (a dual-stack socket's IPv4 side)."""
    import ipaddress
    import struct
    try:
        if len(text) == 8:
            return str(ipaddress.IPv4Address(struct.pack("=I", int(text, 16))))
        if len(text) == 32:
            packed = b"".join(struct.pack("=I", int(text[i:i + 8], 16)) for i in range(0, 32, 8))
            address = ipaddress.IPv6Address(packed)
            return str(address.ipv4_mapped) if address.ipv4_mapped is not None else str(address)
    except (ValueError, struct.error):
        pass
    return None


def listening(folder="/proc/net", *, fresh=False) -> dict | None:
    """{port: [(local address, uid), ...]} of every listening TCP socket in this network namespace (/proc/net/tcp
    and tcp6: state 0A), None when neither table can be read. Kept for LISTEN_CACHE_SECONDS, so the checks of one
    memory lookup or one model call parse the tables once (`fresh` reads them again)."""
    folder = str(folder)
    now = time.monotonic()
    hit = _LISTEN_CACHE.get(folder)
    if not fresh and hit is not None and 0 <= now - hit[0] < LISTEN_CACHE_SECONDS:
        return hit[1]
    table, read = {}, False
    for name in ("tcp", "tcp6"):
        try:
            with open(f"{folder}/{name}", encoding="ascii", errors="replace") as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        read = True
        for line in lines:
            parts = line.split()
            try:
                if len(parts) <= 7 or parts[3] != "0A":
                    continue
                raw, port = parts[1].rsplit(":", 1)
                address = _proc_address(raw)
                if address is not None:
                    table.setdefault(int(port, 16), []).append((address, int(parts[7])))
            except ValueError:
                continue
    result = table if read else None
    _LISTEN_CACHE[folder] = (now, result)
    return result


def listeners(port, folder="/proc/net") -> list | None:
    """[(local address, uid), ...] of the sockets listening on `port`; [] when none does, None when unreadable."""
    table = listening(folder)
    return None if table is None else list(table.get(int(port), ()))


def listener_uids(port, folder="/proc/net") -> set[int] | None:
    """The user ids that own a listening TCP socket on `port`, on any address; an empty set when nothing listens
    there, None when neither table can be read. (Who would accept a given connection: accepting().)"""
    found = listeners(port, folder)
    return None if found is None else {uid for _, uid in found}


def accepting(entries, addresses) -> dict:
    """{address: {uids}} for each of `addresses` (where a connection will go) that a listener among `entries`
    ((local address, uid) pairs of one port) would accept. A socket bound to exactly that address takes the
    connection before a wildcard one; then the wildcard of the address's own family (0.0.0.0, or ::); then, for an
    IPv4 address, the IPv6 wildcard, which a dual-stack socket also serves. A listener on any other address
    (127.0.0.53, a bridge address, ::1 while the connection goes to 127.0.0.1) never sees the connection and does
    not count (review S1: counting it let any account turn the model off by binding its port somewhere else)."""
    import ipaddress
    out = {}
    for target in addresses:
        try:
            address = ipaddress.ip_address(str(target).strip("[]"))
        except ValueError:
            continue
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        for bound in [str(address)] + (["0.0.0.0", "::"] if address.version == 4 else ["::"]):
            owners = {uid for local, uid in entries if local == bound and type(uid) is int}
            if owners:
                out[str(address)] = owners
                break
    return out


def stranger(addresses, port, *, key_file=None, table=None, environ=None, what="the model service") -> str | None:
    """Why nothing may be sent to `port` at `addresses` on this computer, or None.

    A loopback port belongs to whoever binds it first, and a model server's port is free whenever its container is
    down. So the owner of the socket that would accept the connection is read (`table`: port -> [(address, uid)],
    default listeners()). Root (Docker's port proxy), this user and the owner of the key file are the service; a
    process of any other account is not, and the reason names its address, port and uid. Never a refusal: nothing
    listening, unreadable tables, any surprise while reading them, uid 65534 (an account this user namespace
    cannot identify: neither trusted, even as the key file's owner, nor refused; so under PrivateUsers=yes the
    check refuses nobody), and NSF_LISTENER_CHECK=0 in the environment (the owner runs the service under another
    account on purpose)."""
    try:
        if (os.environ if environ is None else environ).get("NSF_LISTENER_CHECK", "").strip() == "0":
            return None
        entries = (table or listeners)(int(port))
        if not entries:
            return None
        trusted = {0, os.getuid()}
        try:
            owner = os.stat(key_file).st_uid if key_file else None
        except (OSError, TypeError, ValueError):
            owner = None
        if owner is not None and owner != OVERFLOW_UID:
            trusted.add(owner)
        for address, owners in accepting(entries, addresses).items():
            strangers = sorted(owners - trusted - {OVERFLOW_UID})
            if strangers:
                host = f"[{address}]" if ":" in address else address
                return (f"{host}:{int(port)} on this computer is held by a process of another account (uid "
                        f"{', '.join(map(str, strangers))}), not by {what}: nothing was sent to it "
                        "(NSF_LISTENER_CHECK=0 if that account runs the service on purpose)")
    except Exception:                          # the check must never be the reason a call fails
        return None
    return None


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


def _flag(text: str):
    """nvidia-smi's "Active" / "Not Active" as True / False; anything else ("[N/A]") is unreadable: None."""
    text = text.strip()
    return True if text == "Active" else False if text == "Not Active" else None


def gpu() -> dict:
    """GPU memory, utilisation, temperature (°C), power draw (W) and the clock throttle reasons, plus per-process GPU
    memory. A field nvidia-smi cannot read stays None. `throttle.thermal` is True while a software or hardware
    thermal slowdown holds the clocks down; `active_mask` is the raw bitmask of every active clock event reason."""
    out = {"available": shutil.which("nvidia-smi") is not None, "memory_used_bytes": None,
           "memory_total_bytes": None, "utilization_percent": None, "memory_readable": False,
           "temperature_c": None, "power_draw_w": None, "throttle": None, "compute_apps": []}
    if not out["available"]:
        return out
    text = _run(["nvidia-smi", "--query-gpu=" + GPU_HEALTH_QUERY, "--format=csv,noheader,nounits"])
    if not (text and text.strip()):   # an older driver refuses the whole query when it lacks one field
        text = _run(["nvidia-smi", "--query-gpu=" + GPU_QUERY, "--format=csv,noheader,nounits"])
    if text and text.strip():
        fields = text.splitlines()[0].split(",")
        used, total, util = (list(map(_number, fields[:3])) + [None, None, None])[:3]
        if used is not None and total:
            out.update(memory_used_bytes=int(used * 1024 * 1024), memory_total_bytes=int(total * 1024 * 1024),
                       memory_readable=True)
        out["utilization_percent"] = util
        if len(fields) >= 10:
            mask = fields[5].strip()
            sw_thermal, hw_thermal, power_cap, hw_slowdown = (_flag(f) for f in fields[6:10])
            thermal = True if (sw_thermal or hw_thermal) else \
                False if (sw_thermal is False and hw_thermal is False) else None
            out.update(temperature_c=_number(fields[3]), power_draw_w=_number(fields[4]),
                       throttle={"thermal": thermal, "sw_thermal_slowdown": sw_thermal,
                                 "hw_thermal_slowdown": hw_thermal, "sw_power_cap": power_cap,
                                 "hw_slowdown": hw_slowdown, "active_mask": mask if mask.startswith("0x") else None})
    text = _run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    for line in (text or "").splitlines():
        head, _, mib = line.rpartition(",")      # the process name may itself contain commas
        pid, _, name = head.partition(",")
        used = _number(mib)
        if pid.strip().isdigit():
            out["compute_apps"].append({"pid": int(pid), "name": name.strip().split(" ")[0][:80],
                                        "memory_bytes": None if used is None else int(used * 1024 * 1024)})
    return out


def load(path=LOADAVG) -> dict:
    """{"available", "avg1", "avg5", "avg15", "cpus"} from /proc/loadavg: runnable and uninterruptible tasks averaged
    over 1, 5 and 15 minutes (a load at or above `cpus` means work is queueing for a CPU)."""
    try:
        with open(path, encoding="ascii") as fh:
            parts = fh.read().split()
        avg1, avg5, avg15 = (float(p) for p in parts[:3])
    except (OSError, ValueError):
        return {"available": False, "reason": f"{path} is not readable"}
    return {"available": True, "avg1": avg1, "avg5": avg5, "avg15": avg15, "cpus": os.cpu_count()}


def prometheus(text: str) -> dict[str, list[tuple[dict, float]]]:
    """The samples of a Prometheus text exposition: {metric name: [(labels, value), ...]}. Comments and lines that
    do not parse are skipped; a trailing timestamp is ignored."""
    out: dict[str, list[tuple[dict, float]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "{" in line:
            name, _, rest = line.partition("{")
            body, _, tail = rest.rpartition("}")
            labels = dict(_LABEL.findall(body))
        else:
            name, _, tail = line.partition(" ")
            labels = {}
        fields = tail.split()
        try:
            value = float(fields[0])
        except (IndexError, ValueError):
            continue
        out.setdefault(name.strip(), []).append((labels, value))
    return out


def _total(samples, name):
    """The sum of a metric over its label sets (engines, models), None when the server does not expose it."""
    rows = samples.get(name)
    return None if not rows else sum(v for _, v in rows)


def vllm_counters(samples) -> dict | None:
    """The raw readings of a vLLM server's /metrics (cumulative since it started), None when it exposes no
    `vllm:` metric (not a vLLM server)."""
    if not any(name.startswith("vllm:") for name in samples):
        return None
    finished: dict[str, float] = {}
    for labels, value in samples.get("vllm:request_success_total", []):
        reason = labels.get("finished_reason", "unknown")
        finished[reason] = finished.get(reason, 0.0) + value
    started = _total(samples, "process_start_time_seconds")
    return {"running": _total(samples, "vllm:num_requests_running"),
            "waiting": _total(samples, "vllm:num_requests_waiting"),
            "kv_cache_usage": _total(samples, "vllm:kv_cache_usage_perc"),
            "models": sorted({labels["model_name"] for rows in samples.values() for labels, _ in rows
                              if "model_name" in labels}),
            "server_started_at": started,
            "counters": {key: _total(samples, name) for key, name in VLLM_COUNTERS},
            "finished": finished if "vllm:request_success_total" in samples else None,
            "stages": {key: (_total(samples, name + "_sum"), _total(samples, name + "_count"))
                       for key, name in VLLM_STAGES}}


def _share(part, whole):
    return round(part / whole, 4) if part is not None and whole else None


def _vllm_figures(counters, stages, finished) -> dict:
    """Means and totals from cumulative counters (since the start) or from their differences (an interval)."""
    means = {key: (round(s / n, 4) if s is not None and n else None) for key, (s, n) in stages.items()}
    return {"requests_finished": stages["request"][1], "mean_seconds": means,
            "prompt_tokens": counters["prompt_tokens"],
            "prefix_cache_hit_share": _share(counters["prefix_cache_hits"], counters["prefix_cache_queries"]),
            "generation_tokens": counters["generation_tokens"], "preemptions": counters["preemptions"],
            "finished_by_reason": finished}


def _difference(now: dict, before: dict) -> dict:
    """The interval since `before` (two readings of the same server), or why it cannot be given."""
    seconds = round(now["at"] - before["at"], 1)
    if now["server_started_at"] != before["server_started_at"]:
        return {"available": False, "reason": "the server restarted since the previous reading"}

    def minus(a, b):
        return None if a is None or b is None else a - b
    counters = {key: minus(now["counters"][key], before["counters"].get(key)) for key in now["counters"]}
    stages = {key: (minus(s, before["stages"][key][0]), minus(n, before["stages"][key][1]))
              for key, (s, n) in now["stages"].items()}
    finished = None if now["finished"] is None or before["finished"] is None else \
        {k: v - before["finished"].get(k, 0.0) for k, v in now["finished"].items()}
    values = [v for v in counters.values() if v is not None] + [n for _, n in stages.values() if n is not None]
    if any(v < 0 for v in values):
        return {"available": False, "reason": "the counters went back (the server restarted) since the previous "
                                              "reading"}
    return {"available": True, "seconds": seconds, **_vllm_figures(counters, stages, finished)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # urllib then raises HTTPError(code): reported as "answered HTTP 302"


# No proxy either: with http_proxy set and no_proxy not covering the loopback address, urllib would hand the request,
# key included, to the proxy.
_NO_REDIRECT = build_opener(ProxyHandler({}), _NoRedirect)


def inference(url: str, key: str | None = None, *, timeout: float = METRICS_TIMEOUT, clock=time.time) -> dict:
    """The inference server's state from its /metrics (see the module doc); {"available": False, "reason"} when it
    cannot be read or is not a vLLM server. `key` is sent as a bearer token and never logged or returned. Every
    reading becomes the start of the next one's interval (in this process)."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        # no redirect is followed: urllib would resend the bearer key to wherever a 3xx points (review: a process
        # on the endpoint's port answered 302 to another address and received the key there). A 3xx is an error.
        with _NO_REDIRECT.open(Request(url, headers=headers), timeout=timeout) as resp:  # noqa: S310 - loopback URL
            text = resp.read(METRICS_LIMIT).decode("utf-8", "replace")
    except HTTPError as error:
        return {"available": False, "url": url, "reason": f"{url} answered HTTP {error.code}"}
    except (OSError, ValueError) as error:
        why = getattr(error, "reason", None) or error
        return {"available": False, "url": url, "reason": f"{url} is unreachable ({why})"}
    now = vllm_counters(prometheus(text))
    if now is None:
        return {"available": False, "url": url, "reason": f"{url} has no vllm: metrics (not a vLLM server)"}
    now["at"] = clock()
    with _LOCK:
        before = _PREVIOUS.get(url)
        _PREVIOUS[url] = now
    interval = _difference(now, before) if before else \
        {"available": False, "reason": "no earlier reading in this process (an interval appears from the second "
                                       "reading, at least 15 s later)"}
    return {"available": True, "url": url, "models": now["models"], "requests_running": now["running"],
            "requests_waiting": now["waiting"], "kv_cache_usage": now["kv_cache_usage"],
            "server_started_at": now["server_started_at"],
            "since_start": _vllm_figures(now["counters"], now["stages"], now["finished"]), "interval": interval}


def inference_cached(url: str, key: str | None = None) -> dict:
    """inference(), cached FULL_TTL seconds per URL (the scrape never runs on a hot path; see sample())."""
    return _cached(("inference", url), FULL_TTL, lambda: inference(url, key))


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
    """Forget every cached reading and the previous /metrics reading (tests start from a clean process)."""
    with _LOCK:
        _CACHE.clear()
        _PREVIOUS.clear()


def sample(*, full: bool = False) -> dict:
    """{"at", "memory", "load", "gpu", "containers", "level", "reasons", "thresholds"}; load, gpu and containers
    are None unless `full` (gpu and containers are subprocess calls; the gates never ask for them)."""
    mem = _cached("memory", MEMORY_TTL, memory)
    gpu_info = _cached("gpu", FULL_TTL, gpu) if full else None
    level, reasons = pressure(mem, gpu_info)
    return {"at": time.time(), "memory": mem, "load": _cached("load", MEMORY_TTL, load) if full else None,
            "gpu": gpu_info, "containers": _cached("containers", FULL_TTL, containers) if full else None,
            "level": level, "reasons": reasons, "thresholds": thresholds((mem or {}).get("total_bytes"))}


__all__ = ["CRITICAL_FRACTION", "CRITICAL_GIB", "ELEVATED_FRACTION", "ELEVATED_GIB", "GPU_CRITICAL", "GPU_ELEVATED",
           "LEVELS", "clear_cache", "cold_starts", "containers", "gpu", "inference", "inference_cached", "load",
           "memory", "pressure", "prometheus", "sample", "thresholds", "vllm_counters"]
