"""Opt-in idle unloading of the local model services (RD-46 policy (c); docs/architecture/RESOURCE_SCHEDULING.md §3).

Services: vllm (the reasoning model), asr (Whisper), tts (Kokoro), searxng, and the two memory models embed and
rerank (deploy/memory). Each has an idle timeout, OFF by default
(`nsf resources idle --set vllm=30m --set asr=15m --set tts=15m --set embed=15m --set rerank=15m`).
`Idle.tick()` (`nsf resources tick`, run by neuroscifi-idle.timer) unloads a service only when every condition holds:
  - its timeout is set and its container runs;
  - no voice session is open (this process's voice hub, or the beacon of the console that hosts it) and no research
    task is running (web_tasks in a running stage with a live lease);
  - nothing is in flight: no NeuroSciFi call holds the service's in-use lock (`using()` takes it shared; the unload
    takes it exclusively and holds it while the service stops, so no call starts half-way), and the service's own
    /metrics (vllm, asr: vLLM servers) report no running or waiting request;
  - nothing used it within the timeout. The last use is the newest of: the in-use marker (every `using()`), a change
    of the served-request counter in /metrics, the container's start, and the policy's own last load.
At pressure `critical` the timeout shrinks to CRITICAL_SHARE of the configured one (never below CRITICAL_FLOOR s), the
same level at which background autonomy and rendering are refused. Timeouts that are off stay off.

`ensure_loaded()` reloads on demand a service the idle policy unloaded: it tells the user "Starting the local model,
about N s" (N: the last measured reload, else the documented cold start), runs the deploy's start and wait commands
and records the measured reload time. A service the owner unloaded is left alone (`nsf resources load` loads it).
Before any load, `headroom()` compares MemAvailable with the service's footprint (the memory its last load took or
its last unload freed, else the documented figure): a load that would leave less than the critical threshold free is
refused and recorded, rather than starting a container that crash-loops. `using(..., timeout=)` (the gateway passes
its call's time) waits for a reload no longer than the caller may; the reload then finishes in the background.
Every load and unload is an event in <db>.resources/idle-events.jsonl with its reason; `nsf resources` shows them.

Stop and start use the deploy's own commands: deploy/vllm/run.sh, deploy/voice/run.sh and deploy/memory/run.sh
(docker shape), or systemctl when the service's unit is active (so the policy never fights the unit), and
`nsf web searxng stop|start`.

The memory models differ in one way: nobody waits for them. A memory lookup that finds one unloaded is answered at
once from the lexical ranking and starts the reload in the background (`using_now()`, which never blocks;
webcortex/semantic.py), so a later lookup is ranked by meaning again. Their reload is therefore not announced
(`Service.background`): Presence shows "starting" only for a service somebody is waiting for.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener

_DIRECT = build_opener(ProxyHandler({}))      # a loopback service's /metrics is never read through a proxy

from . import monitor, policy

CRITICAL_SHARE = 0.25          # at critical pressure the idle timeout is a quarter of the configured one ...
CRITICAL_FLOOR = 120.0         # ... but never under two minutes
COMMAND_TIMEOUT = 120.0        # stop / start; the wait commands carry their own limit
EVENTS_KEPT = 500
RUNNING = ("discovering", "reading", "evaluating", "synthesizing")
REPO = Path(os.environ.get("NSF_REPO") or Path(__file__).resolve().parents[3])


@dataclass(frozen=True)
class Service:
    name: str
    label: str                       # what the user is told is starting
    container: str
    start: tuple                     # argv; "{repo}", "{db}" and "{py}" are filled in
    stop: tuple
    wait: tuple = ()                 # argv that returns once the service answers
    unit: str | None = None          # its systemd unit: when active, systemctl stops and starts it
    metrics: str | None = None       # a vLLM /metrics URL (requests running / waiting / served)
    documented_seconds: float | None = None   # measured cold start (RESOURCE_SCHEDULING.md §4)
    suggested_minutes: int | None = None
    documented_gib: float | None = None       # memory it takes when loaded (§4), until a load or unload measures it
    background: bool = False         # nobody waits for its reload (the memory models): not announced as "starting"


def _port(env, default):
    return os.environ.get(env, default)


_MEMORY_LINE = re.compile(r"^(NSF_MEMORY_(?:BIND|EMBED_PORT|RERANK_PORT))=([0-9A-Za-z.:\[\]]{1,64})$")


def memory_settings(environ=None, repo=None) -> dict:
    """{"dir", "key_file", "bind", "embed_port", "rerank_port"}: where deploy/memory/run.sh puts the two memory
    models and their key, read the way the script resolves them (review F8: `nsf setup` and the idle policy looked
    at the environment only, so an owner's override of a port was seen by the script alone). The folder is
    NSF_MEMORY_DIR or ~/.config/neuroscifi/memory; address and ports come from deploy/memory/memory.env and then
    from the owner's override `<folder>/memory.env`. Both files are read as plain `NAME=value` lines for these
    three names; nothing is evaluated, and a line with anything else in its value is ignored."""
    environ = os.environ if environ is None else environ
    folder = Path(environ.get("NSF_MEMORY_DIR") or Path.home() / ".config" / "neuroscifi" / "memory")
    found = {"NSF_MEMORY_BIND": environ.get("NSF_MEMORY_BIND") or "127.0.0.1",
             "NSF_MEMORY_EMBED_PORT": environ.get("NSF_MEMORY_EMBED_PORT") or "18441",
             "NSF_MEMORY_RERANK_PORT": environ.get("NSF_MEMORY_RERANK_PORT") or "18442"}
    for path in (Path(repo or REPO) / "deploy" / "memory" / "memory.env", folder / "memory.env"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = _MEMORY_LINE.match(line.strip())
            if match:
                found[match.group(1)] = match.group(2)

    def port(name, default):
        return int(found[name]) if found[name].isdigit() and 0 < int(found[name]) < 65536 else default
    return {"dir": folder, "key_file": folder / "api_key", "bind": found["NSF_MEMORY_BIND"],
            "embed_port": port("NSF_MEMORY_EMBED_PORT", 18441), "rerank_port": port("NSF_MEMORY_RERANK_PORT", 18442)}


_MEMORY = memory_settings()

SERVICES = {
    "vllm": Service("vllm", "the local model", "neuroscifi-vllm",
                    ("bash", "{repo}/deploy/vllm/run.sh", "start"), ("bash", "{repo}/deploy/vllm/run.sh", "stop"),
                    ("bash", "{repo}/deploy/vllm/run.sh", "wait", "1200"), "neuroscifi-vllm.service",
                    f"http://127.0.0.1:{_port('NSF_VLLM_PORT', '8811')}/metrics", 32.5, 30, 44.1),
    "asr": Service("asr", "speech recognition", "neuroscifi-voice-asr",
                   ("bash", "{repo}/deploy/voice/run.sh", "start", "asr"), ("bash", "{repo}/deploy/voice/run.sh", "stop", "asr"),
                   ("bash", "{repo}/deploy/voice/run.sh", "wait", "asr", "900"), "neuroscifi-voice-asr.service",
                   f"http://127.0.0.1:{_port('NSF_VOICE_ASR_PORT', '18431')}/metrics", 17.2, 15, 4.1),
    "tts": Service("tts", "speech synthesis", "neuroscifi-voice-tts",
                   ("bash", "{repo}/deploy/voice/run.sh", "start", "tts"), ("bash", "{repo}/deploy/voice/run.sh", "stop", "tts"),
                   ("bash", "{repo}/deploy/voice/run.sh", "wait", "tts", "900"), "neuroscifi-voice-tts.service",
                   None, 5.2, 15, 1.5),
    "searxng": Service("searxng", "the search engine", "neuroscifi-searxng",
                       ("{py}", "-m", "neuroscifi.cli", "web", "searxng", "start", "--db", "{db}"),
                       ("{py}", "-m", "neuroscifi.cli", "web", "searxng", "stop", "--db", "{db}")),
    # The memory models (deploy/memory; webcortex/semantic.py). Measured on the reference host, each: 18 to 31 s from
    # start to ready with warm files, 4.4 GiB (docs/quality/evidence/accel_decisions_memory_service.json).
    "embed": Service("embed", "memory search (the embedding model)", "neuroscifi-memory-embed",
                     ("bash", "{repo}/deploy/memory/run.sh", "start", "embed"),
                     ("bash", "{repo}/deploy/memory/run.sh", "stop", "embed"),
                     ("bash", "{repo}/deploy/memory/run.sh", "wait", "embed", "600"), "neuroscifi-memory-embed.service",
                     f"http://127.0.0.1:{_MEMORY['embed_port']}/metrics", 20.0, 15, 4.4,
                     background=True),
    "rerank": Service("rerank", "memory search (the reranker)", "neuroscifi-memory-rerank",
                      ("bash", "{repo}/deploy/memory/run.sh", "start", "rerank"),
                      ("bash", "{repo}/deploy/memory/run.sh", "stop", "rerank"),
                      ("bash", "{repo}/deploy/memory/run.sh", "wait", "rerank", "600"),
                      "neuroscifi-memory-rerank.service",
                      f"http://127.0.0.1:{_MEMORY['rerank_port']}/metrics", 20.0, 15, 4.4,
                      background=True),
}


def service_port(name, services=None) -> int | None:
    """The loopback port of a service's own server (from its /metrics address), None when it has none."""
    svc = (services or SERVICES).get(name)
    if svc is None or not svc.metrics:
        return None
    try:
        return int(svc.metrics.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def parse_timeout(text) -> float | None:
    """'30m', '15min', '900s', '900', '1h' -> seconds; 'off' / '0' / None -> None (off)."""
    if text is None:
        return None
    text = str(text).strip().lower()
    if text in ("", "off", "none", "0", "0s", "0m"):
        return None
    for suffix, scale in (("min", 60), ("h", 3600), ("m", 60), ("s", 1)):
        if text.endswith(suffix):
            text, unit = text[: -len(suffix)], scale
            break
    else:
        unit = 1
    try:
        value = float(text) * unit
    except ValueError:
        raise ValueError(f"not a duration: {text!r} (use e.g. 30m, 900s or off)") from None
    if value < 60:
        raise ValueError("an idle timeout under one minute would unload a service between two requests")
    return value


def _metrics(url, timeout=2.0) -> dict | None:
    """{"in_flight", "served"} from a vLLM server's Prometheus /metrics (no key needed), or None if unreadable."""
    try:
        with _DIRECT.open(url, timeout=timeout) as resp:   # noqa: S310 - a loopback URL from SERVICES
            text = resp.read(4 << 20).decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    samples = monitor.prometheus(text)       # the parser `nsf resources` reads the inference server's state with
    in_flight = samples.get("vllm:num_requests_running", []) + samples.get("vllm:num_requests_waiting", [])
    served = samples.get("vllm:request_success_total", [])
    if not in_flight and not served:
        return None
    return {"in_flight": sum((v for _, v in in_flight), 0.0), "served": sum((v for _, v in served), 0.0)}


_clock, _monotonic = time.time, time.monotonic      # module-level so tests can give every Idle one fake clock


def _run(args, timeout):
    try:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(args, 127, f"{type(error).__name__}: {error}")


@contextmanager
def _flock(path, mode):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, mode)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _minutes(seconds) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


MEASURED_CEILING = 1.5     # a measured footprint above this multiple of the documented one is not believed
BACKGROUND_RETRY = 30.0    # seconds before using_now() starts again a reload that was refused or failed


class Idle:
    """The idle policy of one instance (`db_path`); its files live in <db>.resources/ next to the audio beacon."""

    def __init__(self, db_path, *, runner=None, clock=None, monotonic=None, services=None, metrics=None,
                 sample=None, memory=None):
        self.db_path = Path(db_path)
        self.folder = Path(f"{db_path}.resources")
        self.runner = runner or _run
        self.clock, self.monotonic = clock or _clock, monotonic or _monotonic
        self.services = services or SERVICES
        self.read_metrics = metrics or _metrics
        self.sample = sample or (lambda: monitor.sample())
        self.read_memory = memory or (lambda: monitor.memory())     # uncached: a load or unload measures the change

    # ---- files ------------------------------------------------------------------------------------------------
    def _file(self, name) -> Path:
        return self.folder / name

    def read(self) -> dict:
        try:
            data = json.loads(self._file("idle.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("timeouts", {})
        data.setdefault("services", {})
        return data

    def _write(self, data) -> None:
        self.folder.mkdir(mode=0o700, exist_ok=True)
        tmp = self._file("idle.json.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._file("idle.json"))

    @contextmanager
    def _state(self):
        """Read-modify-write of idle.json under an exclusive lock (the timer, the console and the CLI share it)."""
        with _flock(self._file("idle.lock"), fcntl.LOCK_EX):
            data = self.read()
            yield data
            self._write(data)

    def events(self, limit=20) -> list[dict]:
        try:
            lines = self._file("idle-events.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-limit:] if limit else lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _event(self, service, action, reason, **extra) -> dict:
        event = {"at": round(self.clock(), 3), "service": service, "action": action, "reason": reason, **extra}
        self.folder.mkdir(mode=0o700, exist_ok=True)
        path = self._file("idle-events.jsonl")
        with _flock(self._file("idle-events.lock"), fcntl.LOCK_EX):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2 * EVENTS_KEPT:
                tmp = path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines[-EVENTS_KEPT:]) + "\n", encoding="utf-8")
                os.replace(tmp, path)
        return event

    # ---- configuration ----------------------------------------------------------------------------------------
    def configure(self, changes: dict) -> dict:
        """{service: seconds or None}. Returns the timeouts now in force."""
        for name in changes:
            if name not in self.services:
                raise ValueError(f"unknown service {name!r} (one of {', '.join(self.services)})")
        with self._state() as data:
            for name, seconds in changes.items():
                data["timeouts"][name] = seconds
            timeouts = dict(data["timeouts"])
        for name, seconds in changes.items():
            self._event(name, "configure", "owner set the idle timeout to " +
                        ("off" if seconds is None else _minutes(seconds)), timeout_s=seconds)
        return timeouts

    def timeouts(self) -> dict:
        data = self.read()["timeouts"]
        return {name: data.get(name) for name in self.services}

    # ---- facts ------------------------------------------------------------------------------------------------
    def _cmd(self, argv) -> list[str]:
        return [str(a).format(repo=REPO, db=self.db_path, py=sys.executable) for a in argv]

    def _systemd(self, svc) -> bool:
        if not svc.unit:
            return False
        done = self.runner(["systemctl", "is-active", "--quiet", svc.unit], 10)
        return getattr(done, "returncode", 1) == 0

    def container(self, svc) -> dict | None:
        """{"running", "started_at"} of the service's container, None when it does not exist."""
        done = self.runner(["docker", "inspect", "-f", "{{.State.Running}} {{.State.StartedAt}}", svc.container], 30)
        if getattr(done, "returncode", 1) != 0:
            return None
        running, _, started = (done.stdout or "").strip().partition(" ")
        return {"running": running == "true", "started_at": monitor._ts(started)}

    def voice_session(self) -> bool:
        return policy.voice_session_active(self.db_path, now=self.clock())

    def running_tasks(self) -> int:
        if not self.db_path.exists():
            return 0
        try:
            with closing(sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)) as db:
                return db.execute(f"SELECT count(*) FROM web_tasks WHERE status IN ({','.join('?' * len(RUNNING))}) "
                                  "AND lease_holder IS NOT NULL AND lease_expires_at>=?",
                                  (*RUNNING, self.clock())).fetchone()[0]
        except sqlite3.Error:
            return 0          # no web cortex tables yet: nothing runs

    def last_used(self, name) -> float | None:
        try:
            return self._file(f"idle-used-{name}").stat().st_mtime
        except OSError:
            return None

    def touch(self, name) -> None:
        self.folder.mkdir(mode=0o700, exist_ok=True)
        path = self._file(f"idle-used-{name}")
        now = self.clock()
        with open(path, "a"):
            pass
        os.utime(path, (now, now))

    # ---- the policy -------------------------------------------------------------------------------------------
    def effective_timeout(self, seconds, level) -> float | None:
        if seconds is None:
            return None
        return max(CRITICAL_FLOOR, seconds * CRITICAL_SHARE) if level == "critical" else seconds

    def tick(self) -> dict:
        """One pass: unload every service whose idle conditions all hold. Returns the decision per service."""
        timeouts = self.timeouts()
        if not any(v is not None for v in timeouts.values()):
            return {"enabled": False, "decisions": {}}
        sample = self.sample()
        level = sample["level"]
        blockers = []
        if self.voice_session():
            blockers.append("a voice session is open")
        tasks = self.running_tasks()
        if tasks:
            blockers.append(f"{tasks} research task(s) running")
        now = self.clock()
        decisions = {}
        for name, seconds in timeouts.items():
            if seconds is None:
                continue
            decisions[name] = self._consider(self.services[name], seconds, level, blockers, now)
        return {"enabled": True, "level": level, "blockers": blockers, "decisions": decisions}

    def _consider(self, svc, seconds, level, blockers, now) -> dict:
        timeout = self.effective_timeout(seconds, level)
        box = self.container(svc)
        if not box or not box["running"]:
            return {"action": "none", "why": "not loaded"}
        state = self.read()["services"].get(svc.name, {})
        uses = [self.last_used(svc.name), box.get("started_at"), state.get("loaded_at"), state.get("served_at")]
        metrics = self.read_metrics(svc.metrics) if svc.metrics else None
        if metrics is not None:
            if metrics["in_flight"] > 0:
                uses.append(now)
            if state.get("served") != metrics["served"]:
                with self._state() as data:
                    entry = data["services"].setdefault(svc.name, {})
                    if entry.get("served") is not None:          # the first reading is a baseline, not a use
                        entry["served_at"] = now
                        uses.append(now)
                    entry["served"] = metrics["served"]
        elif svc.metrics:
            return {"action": "none", "why": "its /metrics is unreadable, so in-flight requests cannot be ruled out"}
        last = max((u for u in uses if u is not None), default=now)
        idle_for = now - last
        if blockers:
            return {"action": "none", "why": "; ".join(blockers), "idle_s": round(idle_for, 1)}
        if idle_for < timeout:
            return {"action": "none", "why": f"used {_minutes(idle_for)} ago (unloads after {_minutes(timeout)} idle)",
                    "idle_s": round(idle_for, 1)}
        reason = f"idle {_minutes(idle_for)} (timeout {_minutes(seconds)})"
        if timeout != seconds:
            reason = f"idle {_minutes(idle_for)} (timeout {_minutes(seconds)}, shortened to {_minutes(timeout)} at " \
                     f"critical memory pressure: {'; '.join(self.sample()['reasons'])})"
        try:
            with _flock(self._file(f"idle-inuse-{svc.name}.lock"), fcntl.LOCK_EX | fcntl.LOCK_NB):
                if self.last_used(svc.name) and now - self.last_used(svc.name) < timeout:
                    return {"action": "none", "why": "used while the decision was being made"}
                return self.unload(svc.name, reason=reason, by="idle policy", _locked=True)
        except BlockingIOError:
            return {"action": "none", "why": "a request is in flight"}

    # ---- load and unload --------------------------------------------------------------------------------------
    def unload(self, name, *, reason, by="owner", _locked=False) -> dict:
        svc = self.services[name]
        if not _locked:
            with _flock(self._file(f"idle-inuse-{name}.lock"), fcntl.LOCK_EX):   # waits for in-flight calls
                return self.unload(name, reason=reason, by=by, _locked=True)
        systemd = self._systemd(svc)
        argv = ["systemctl", "--no-ask-password", "stop", svc.unit] if systemd else self._cmd(svc.stop)
        before = self._available()
        started = self.monotonic()
        done = self.runner(argv, COMMAND_TIMEOUT)
        ok = getattr(done, "returncode", 1) == 0
        seconds = round(self.monotonic() - started, 1)
        after = self._available() if ok and before is not None else None
        freed = None if after is None else after - before
        event = self._event(name, "unload", reason, by=by, ok=ok, seconds=seconds,
                            shape="systemd" if systemd else "docker",
                            **({"freed_bytes": freed} if freed and freed > 0 else {}),
                            **({} if ok else {"error": (getattr(done, "stdout", "") or "")[-400:]}))
        if ok:
            with self._state() as data:
                data["services"].setdefault(name, {}).update(state="unloaded", by=by, at=event["at"],
                                                             shape="systemd" if systemd else "docker")
        return {"action": "unload" if ok else "failed", "why": reason, "event": event}

    def unloaded_by_policy(self, name) -> bool:
        entry = self.read()["services"].get(name) or {}
        return entry.get("state") == "unloaded" and entry.get("by") == "idle policy"

    def estimate(self, name) -> float | None:
        """The last measured reload of this service, else its documented cold start."""
        for event in reversed(self.events(limit=0)):
            if event.get("service") == name and event.get("action") == "load" and event.get("ok") and \
                    event.get("seconds"):
                return event["seconds"]
        return self.services[name].documented_seconds

    def _available(self) -> int | None:
        """MemAvailable in bytes now (uncached), None when /proc/meminfo cannot be read."""
        try:
            return (self.read_memory() or {}).get("available_bytes")
        except Exception:      # a measurement must never stop a load or an unload
            return None

    def footprint(self, name) -> dict | None:
        """{"bytes", "source"}: the memory the service took when it last loaded, or freed when it last unloaded
        (MemAvailable before and after: on unified memory this counts its GPU allocations too), else the documented
        figure; None when neither exists (no headroom check). Another workload changing at the same moment skews a
        measurement; a reading that was not an increase is not recorded."""
        gib = self.services[name].documented_gib
        ceiling = None if gib is None else MEASURED_CEILING * gib * monitor.GIB
        for event in reversed(self.events(limit=0)):
            if event.get("service") != name or not event.get("ok"):
                continue
            for field, how in (("took_bytes", "taken by its last load"), ("freed_bytes", "freed by its last unload")):
                # On a shared host another workload can free tens of GiB in the same seconds. A reading beyond
                # MEASURED_CEILING times the documented figure is that, not the service: believing it would refuse
                # every later load, and a refused load records nothing that could correct it (review).
                if isinstance(event.get(field), (int, float)) and event[field] > 0 \
                        and (ceiling is None or event[field] <= ceiling):
                    return {"bytes": int(event[field]), "source": f"{how} (measured)"}
        return None if gib is None else {"bytes": int(gib * monitor.GIB),
                                         "source": "documented (RESOURCE_SCHEDULING.md §4)"}

    def headroom(self, name) -> dict:
        """{"fits", "why"}: may the service load without pushing MemAvailable under the critical threshold (§2)? On
        a shared host another workload may have taken the memory the unload freed; loading into too little memory
        is what makes a container crash-loop. Unknown memory or footprint is not a refusal (the check is skipped and
        says so)."""
        need = self.footprint(name)
        mem = None
        try:
            mem = self.read_memory()
        except Exception:
            pass
        if need is None:
            return {"fits": True, "why": "not checked: no measured or documented footprint"}
        if not mem or mem.get("available_bytes") is None:
            return {"fits": True, "why": "not checked: /proc/meminfo is not readable"}
        floor = monitor.thresholds(mem.get("total_bytes"))["critical_below_bytes"]
        avail = mem["available_bytes"]
        gib = monitor._gib
        detail = (f"{gib(avail)} available, {self.services[name].label} needs about {gib(need['bytes'])} "
                  f"({need['source']}) and {gib(floor)} must stay free (the critical threshold)")
        if avail - need["bytes"] < floor:
            return {"fits": False, "why": f"not enough memory to load {self.services[name].label}: {detail}",
                    "available_bytes": avail, "need_bytes": need["bytes"], "floor_bytes": floor}
        return {"fits": True, "why": detail}

    def message(self, name) -> str:
        svc = self.services[name]
        n = self.estimate(name)
        about = f", about {math.ceil(n)} s" if n else " (no reload time measured yet)"
        return f"Starting {svc.label}{about}"

    def load(self, name, *, reason, by="owner", notify=None) -> dict:
        """Start the service with the deploy's commands and wait until it answers; the measured time is recorded."""
        svc = self.services[name]
        with _flock(self._file(f"idle-load-{name}.lock"), fcntl.LOCK_EX):      # one start at a time, any process
            box = self.container(svc)
            if box and box["running"]:          # never restart a running service (run.sh start replaces it)
                # Running is not answering (review F4): a container left behind by a reload that failed or was
                # killed may still be starting, or crash-looping. It counts as loaded once it answers; one that
                # does not is stopped, so that "unloaded" stays true and the next attempt starts from nothing.
                if svc.wait and svc.metrics and self.read_metrics(svc.metrics) is None:
                    started = self.monotonic()
                    done = self.runner(self._cmd(svc.wait), 1500)
                    if getattr(done, "returncode", 1) != 0:
                        stopped = self._stop_after_failure(svc, self._systemd(svc))
                        event = self._event(name, "load", reason, by=by, ok=False, found_running=True,
                                            seconds=round(self.monotonic() - started, 1), stopped=stopped,
                                            error=(getattr(done, "stdout", "") or "")[-400:])
                        with self._state() as data:
                            entry = data["services"].setdefault(name, {})
                            entry.update(state="unloaded" if entry.get("state") == "unloaded" else "unknown",
                                         failed_at=event["at"])
                        return {"action": "failed", "why": "it was running but did not answer", "event": event}
                with self._state() as data:
                    data["services"].setdefault(name, {}).update(state="loaded", by="already running",
                                                                 at=self.clock())
                return {"action": "none", "why": "already running"}
            room = self.headroom(name)
            if not room["fits"]:       # refuse rather than start a container the kernel or vLLM will kill
                event = self._event(name, "load", reason, by=by, ok=False, refused=True, seconds=0.0,
                                    error=room["why"], **{k: room[k] for k in room if k.endswith("_bytes")})
                with self._state() as data:
                    data["services"].setdefault(name, {}).update(refused_at=event["at"])
                return {"action": "refused", "why": room["why"], "event": event}
            text = self.message(name)
            if not svc.background:          # a reload nobody waits for is not shown as "starting"
                with self._state() as data:     # Presence shows this while the service starts (any process)
                    data["services"].setdefault(name, {}).update(loading={"told": text, "since": self.clock()})
            if notify is not None:
                try:
                    notify(text)
                except Exception:   # a closed client must not stop the load
                    pass
            systemd = self._systemd(svc)
            if not systemd and svc.unit:
                # A unit this policy stopped is no longer active. It is started as the unit it is, not as a second,
                # unsupervised container beside it (found in review: the reload went through run.sh).
                was = self.read()["services"].get(name) or {}
                systemd = was.get("state") == "unloaded" and was.get("shape") == "systemd"
            steps = [["systemctl", "--no-ask-password", "start", svc.unit]] if systemd else [self._cmd(svc.start)]
            if svc.wait:
                steps.append(self._cmd(svc.wait))
            before = self._available()
            started = self.monotonic()
            done = None
            for argv in steps:
                done = self.runner(argv, COMMAND_TIMEOUT if argv is steps[0] else 1500)
                if getattr(done, "returncode", 1) != 0:
                    break
            ok = getattr(done, "returncode", 1) == 0
            seconds = round(self.monotonic() - started, 1)
            after = self._available() if ok and before is not None else None
            took = None if after is None else before - after
            # A start that did not become ready is stopped (review F4): with --restart unless-stopped the container
            # would stay, or keep restarting, holding its memory while the state says "unloaded".
            stopped = None if ok else self._stop_after_failure(svc, systemd)
            event = self._event(name, "load", reason, by=by, ok=ok, seconds=seconds, told=text,
                                shape="systemd" if systemd else "docker", headroom=room["why"],
                                **({"took_bytes": took} if took and took > 0 else {}),
                                **({} if ok else {"error": (getattr(done, "stdout", "") or "")[-400:],
                                                  "stopped": stopped}))
            with self._state() as data:
                entry = data["services"].setdefault(name, {})
                entry.pop("loading", None)
                if ok:
                    entry.update(state="loaded", by=by, at=event["at"], loaded_at=event["at"])
                else:
                    entry.update(state="unloaded" if entry.get("state") == "unloaded" else "unknown",
                                 failed_at=event["at"])
            return {"action": "load" if ok else "failed", "why": reason, "seconds": seconds, "told": text,
                    "event": event}

    def _stop_after_failure(self, svc, systemd) -> bool:
        """Stop what a failed load left running, with the shape's own command. True when the stop succeeded."""
        argv = ["systemctl", "--no-ask-password", "stop", svc.unit] if systemd and svc.unit else self._cmd(svc.stop)
        return getattr(self.runner(argv, COMMAND_TIMEOUT), "returncode", 1) == 0

    def ensure_loaded(self, name, *, purpose, notify=None) -> dict | None:
        """Reload a service the idle policy unloaded (on demand); None when there was nothing to do."""
        if not self.unloaded_by_policy(name):
            return None
        return self.load(name, reason=f"on demand: {purpose}", by="on demand", notify=notify)

    # ---- surfaces ---------------------------------------------------------------------------------------------
    def loading(self, *, now=None, stale=1800.0) -> list[dict]:
        """[{service, told, since}] for each service starting right now (what Presence shows); an entry left by a
        process killed mid-load is ignored after `stale` seconds."""
        now = self.clock() if now is None else now
        out = []
        for name, entry in self.read()["services"].items():
            info = (entry or {}).get("loading") or {}
            if isinstance(info.get("since"), (int, float)) and 0 <= now - info["since"] <= stale:
                out.append({"service": name, "told": info.get("told"), "since": info["since"]})
        return out

    def status(self, containers=None) -> dict:
        """What `nsf resources` shows. `containers`: the running neuroscifi-* containers when already measured."""
        timeouts = self.timeouts()
        data = self.read()["services"]
        running = {c.get("name") for c in containers} if containers is not None else None
        now = self.clock()
        services = {}
        for name, svc in self.services.items():
            entry = data.get(name) or {}
            used = max((u for u in (self.last_used(name), entry.get("served_at"), entry.get("loaded_at"))
                        if u is not None), default=None)
            services[name] = {
                "timeout_s": timeouts[name], "suggested_minutes": svc.suggested_minutes,
                "loaded": None if running is None else svc.container in running,
                "state": entry.get("state"), "state_by": entry.get("by"),
                "last_used_at": used, "idle_s": None if used is None else round(now - used, 1),
                "reload_estimate_s": self.estimate(name)}
        return {"automatic": any(v is not None for v in timeouts.values()), "services": services,
                "critical_share": CRITICAL_SHARE, "events": self.events(limit=20),
                "how": "nsf resources idle --set vllm=30m --set asr=15m --set tts=15m --set embed=15m --set "
                       "rerank=15m; nsf resources tick (neuroscifi-idle.timer); nsf resources unload|load SERVICE"}


_LOADS: dict = {}                   # (db_path, service) -> the _Load running in this process
_LOADS_LOCK = threading.Lock()


class _Load(threading.Thread):
    """One on-demand reload running in the background. Every caller waiting for it is a listener: each is told
    once what the user is told (the first message from Idle.load, or the estimate when it has not come yet)."""

    def __init__(self, idle, name, purpose):
        super().__init__(daemon=True, name=f"nsf-idle-load-{name}")
        self.idle, self.service, self.purpose = idle, name, purpose
        self.lock = threading.Lock()
        self.listeners: dict = {}
        self.told = None
        self.result = None

    def listen(self, token, notify) -> None:
        with self.lock:
            told = self.told
            if told is None and notify is not None:
                self.listeners[token] = notify
        if told is not None and notify is not None:
            _tell(notify, told)

    def leave(self, token) -> str | None:
        """Stop listening; returns what this listener was told (None: nothing yet)."""
        with self.lock:
            heard = token not in self.listeners
            self.listeners.pop(token, None)
            return self.told if heard else None

    def _notify(self, text) -> None:
        with self.lock:
            self.told = text
            listeners, self.listeners = list(self.listeners.values()), {}
        for notify in listeners:
            _tell(notify, text)

    def run(self) -> None:
        try:
            self.result = self.idle.ensure_loaded(self.service, purpose=self.purpose, notify=self._notify)
        except Exception as error:   # recorded for the callers; the next use tries again
            self.result = {"action": "failed", "why": f"{type(error).__name__}: {error}"}


def _tell(notify, text) -> None:
    try:
        notify(text)
    except Exception:   # a closed client must not stop the load
        pass


def _bounded_load(idle, name, *, purpose, notify, timeout) -> dict | None:
    """Idle.ensure_loaded, waiting at most `timeout` seconds. The load runs in a background thread (one per instance
    and service in this process; later callers wait for the same one), so a caller whose time runs out gets
    {"action": "loading", "why", "told"} at once while the service keeps starting, and the next call finds it
    loaded. A crash-looping container therefore costs a call its own deadline, not the deploy's wait limit."""
    if not idle.unloaded_by_policy(name):
        return None
    key = (str(idle.db_path), name)
    token = object()
    with _LOADS_LOCK:
        job = _LOADS.get(key)
        fresh = job is None or not job.is_alive()
        if fresh:
            job = _LOADS[key] = _Load(idle, name, purpose)
    job.listen(token, notify)
    if fresh:
        job.start()
    job.join(max(0.0, timeout))
    if not job.is_alive():
        job.leave(token)
        return job.result
    told = job.leave(token)
    if told is None:
        told = idle.message(name)
        if notify is not None:
            _tell(notify, told)
    return {"action": "loading", "told": told,
            "why": f"{told}: not ready within this call's {timeout:.0f} s; it keeps starting in the background"}


@contextmanager
def using(db_path, name, *, purpose, notify=None, idle=None, timeout=None):
    """Wrap one call to a service: hold its in-use lock (shared) so the idle policy cannot stop it mid-call, mark the
    use, and reload it first if the idle policy unloaded it (the user is told through `notify`). No-op without a
    db_path, and while the policy is off for the service. Yields the reload result (None: nothing was loaded).
    With `timeout` (the caller's remaining seconds) the reload waits no longer than that: the result is then
    {"action": "loading", ...} and the reload finishes in the background (_bounded_load); a reload refused for
    lack of memory yields {"action": "refused", "why"}."""
    idle = idle or (Idle(db_path) if db_path else None)
    data = idle.read() if idle is not None else {}
    if idle is None or (data["timeouts"].get(name) is None and
                        (data["services"].get(name) or {}).get("state") != "unloaded"):
        yield None           # the policy is off for this service: no lock, no marker, no file
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(_flock(idle._file(f"idle-inuse-{name}.lock"), fcntl.LOCK_SH))
            idle.touch(name)
            stack.callback(idle.touch, name)
        except OSError:      # an unwritable folder must never break a call
            pass
        if wake_dir() is not None:      # this process may not start services itself: ask, and wait (review F5)
            yield _requested_load(idle, name, purpose=purpose, notify=notify, timeout=timeout)
        else:
            yield idle.ensure_loaded(name, purpose=purpose, notify=notify) if timeout is None else \
                _bounded_load(idle, name, purpose=purpose, notify=notify, timeout=timeout)


_DETACHED: dict = {}                # (db_path, service) -> (the reload process this process started, when)


def _spawn_load(idle, name, purpose):
    """Start the reload of `name` as a process of its own: `nsf resources load NAME --on-demand PURPOSE`, in its own
    session, with nothing inherited but the environment. It outlives the process whose lookup asked for it (a
    one-shot `nsf web knowledge` ends at once), so the service is really started, waited for, measured and
    recorded. Returns the process (only `poll()` is used)."""
    argv = [sys.executable, "-P", "-m", "neuroscifi.cli", "resources", "load", name, "--db", str(idle.db_path),
            "--on-demand", purpose]
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True)


def _load_in_progress(idle, name) -> bool:
    """Whether some process holds the service's load lock right now (Idle.load keeps it while it starts)."""
    try:
        with _flock(idle._file(f"idle-load-{name}.lock"), fcntl.LOCK_EX | fcntl.LOCK_NB):
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


SPAWN_GRACE = 15.0         # seconds a just-started reload process may need before it holds the load lock


def _just_spawned(idle, name, *, mark=False) -> bool:
    """Whether some process started this service's reload process less than SPAWN_GRACE seconds ago (a new Python
    takes a moment to reach Idle.load and its lock; measured: a lookup 0.2 s later started a second one, which
    then only waited). `mark` records that this process just did."""
    path = idle._file(f"idle-spawned-{name}")
    try:
        if mark:
            idle.folder.mkdir(mode=0o700, exist_ok=True)
            with open(path, "a"):
                pass
            os.utime(path, (idle.clock(), idle.clock()))
            return True
        return 0 <= idle.clock() - path.stat().st_mtime < SPAWN_GRACE
    except OSError:
        return False


def _background_reload(idle, name, purpose) -> dict:
    """{"action": "loading" | "refused" | "failed", "why"} for a service the idle policy unloaded, without waiting:
    its reload runs in a process of its own, started here unless one already runs (this process's, or another's:
    one holding the load lock or started a moment ago). A reload that ended refused or failed is reported as such,
    and the next one starts no sooner than BACKGROUND_RETRY seconds after it ended.

    Both limits hold across processes (review F6: they held inside one process only, so one-shot commands started
    a reload each). The failure time is the one Idle.load writes to idle.json; and the decision to start, the
    start and its marker happen under one short lock, so two processes that look at the same moment start one."""
    svc = idle.services[name]
    key = (str(idle.db_path), name)
    n = idle.estimate(name)
    loading = {"action": "loading", "why": f"{svc.label} is unloaded while idle and is starting again"
                                           + (f" (about {math.ceil(n)} s)" if n else "")}
    with _LOADS_LOCK:
        entry = _DETACHED.get(key)
        if entry is not None and entry[0].poll() is None:
            return loading
        try:
            with _flock(idle._file(f"idle-spawn-{name}.lock"), fcntl.LOCK_EX):       # held for milliseconds
                state = idle.read()["services"].get(name) or {}
                ended = max((t for t in (state.get("failed_at"), state.get("refused_at"))
                             if isinstance(t, (int, float))), default=None)
                if ended is not None and 0 <= idle.clock() - ended < BACKGROUND_RETRY:
                    last = next((e for e in reversed(idle.events(limit=50))
                                 if e.get("service") == name and e.get("action") == "load"), None) or {}
                    if last.get("refused"):
                        return {"action": "refused",
                                "why": last.get("error") or f"not enough memory to load {svc.label}"}
                    detail = " ".join(str(last.get("error") or "see nsf resources").split())[:160]
                    return {"action": "failed", "why": f"{svc.label} did not start: {detail}"}
                if _load_in_progress(idle, name) or _just_spawned(idle, name):
                    return loading
                _DETACHED[key] = (_start_reload(idle, name, purpose), idle.monotonic())
                _just_spawned(idle, name, mark=True)
        except OSError as error:
            return {"action": "failed", "why": f"{svc.label} did not start: {type(error).__name__}: {error}"[:200]}
    return loading


WAKE_SUFFIX = ".wake"
WAKE_PICKUP = 60.0         # seconds a wake request may wait for neuroscifi-wake.path before it counts as not served


def wake_dir() -> Path | None:
    """The wake-request folder (NSF_WAKE_DIR), None when none is configured.

    Why it exists (review F5). In the installed deployment the console and the worker run as an unprivileged,
    sandboxed user that can reach neither Docker nor `systemctl start`, so a reload they start themselves cannot
    work, for the memory models or for the answer model; and a process they spawn dies with their unit. With
    NSF_WAKE_DIR set they only leave a request: an empty file `<service>.wake` in that folder. A systemd path unit
    (deploy/systemd/neuroscifi-wake.path) sees it and runs `nsf resources wake` as the user that owns the services
    (neuroscifi-wake.service), which does the load with the same headroom check, wait and event as any other.
    Without NSF_WAKE_DIR (one user runs everything: `nsf start`) the reload is started directly, as before."""
    value = os.environ.get("NSF_WAKE_DIR")
    return Path(value) if value else None


class _Requested:
    """A wake request left for neuroscifi-wake.path. poll() is None until it has been picked up."""

    def __init__(self, path):
        self.path = path

    def poll(self):
        return None if self.path.exists() else 0


def _request_wake(idle, name):
    """Leave the wake request of `name` (created atomically, empty, 0600): the file's name is the whole message.
    One that has waited longer than WAKE_PICKUP was not picked up: OSError, so the caller reports a failure."""
    folder = wake_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{name}{WAKE_SUFFIX}"
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.utime(path, (idle.clock(), idle.clock()))
    except FileExistsError:
        if idle.clock() - path.stat().st_mtime > WAKE_PICKUP:
            raise OSError(f"its wake request was not picked up within {WAKE_PICKUP:.0f} s (is neuroscifi-wake.path "
                          "active?)") from None
    return _Requested(path)


def serve_wake_requests(db_path, folder, *, idle=None) -> list[dict]:
    """What neuroscifi-wake.service runs (`nsf resources wake`): take every request out of `folder` and reload the
    service it names, if the idle policy unloaded it. A request is its file name only: it is removed first and
    never opened, and a name that is not a service of this policy is dropped without any command being run."""
    idle = idle or Idle(db_path)
    out = []
    for path in sorted(Path(folder).glob(f"*{WAKE_SUFFIX}")):
        name = path.name[: -len(WAKE_SUFFIX)]
        try:
            path.unlink()
        except FileNotFoundError:
            continue                         # another pass took it
        except OSError as error:
            out.append({"request": name[:64], "action": "failed", "why": f"cannot remove the request: {error}"[:200]})
            continue                         # left in place it would be served again and again
        if name not in idle.services:
            out.append({"request": name[:64], "action": "ignored", "why": "not a service of the idle policy"})
            continue
        result = idle.ensure_loaded(name, purpose="wake request") or \
            {"action": "none", "why": "nothing to do: the idle policy has not unloaded it"}
        out.append({"service": name, "action": result["action"], "why": result.get("why")})
    return out


def _start_reload(idle, name, purpose):
    """How a background reload is started: a wake request where NSF_WAKE_DIR names the folder of the path unit
    that serves them, else a process of its own."""
    return _request_wake(idle, name) if wake_dir() is not None else _spawn_load(idle, name, purpose)


def _requested_load(idle, name, *, purpose, notify, timeout) -> dict | None:
    """using() where reloads go through wake requests: ask for the reload and wait, no longer than `timeout`, until
    the state says the service is loaded. The results are those of _bounded_load: None (nothing to do), "load",
    "loading" (the caller's time ran out; it keeps starting), "refused" or "failed"."""
    if not idle.unloaded_by_policy(name):
        return None
    told = idle.message(name)
    if notify is not None:
        _tell(notify, told)
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    asked = False
    while True:
        if asked and not idle.unloaded_by_policy(name):      # looked at first: a loaded service is not asked for
            return {"action": "load", "why": f"on demand: {purpose}", "told": told}
        state = _background_reload(idle, name, purpose)      # asks once; later rounds find the request or the load
        asked = True
        if state["action"] in ("refused", "failed"):
            return state | {"told": told}
        if deadline is not None and time.monotonic() >= deadline:
            return {"action": "loading", "told": told,
                    "why": f"{told}: not ready within this call's {timeout:.0f} s; it keeps starting in the background"}
        time.sleep(0.2)


@contextmanager
def using_now(db_path, name, *, purpose, idle=None):
    """using() for a caller that must not wait (a memory lookup): it never blocks and never waits for a reload.
    Yields None when the call may be made: the policy is off for the service, or its in-use lock is held (shared)
    for the block and the use is marked. Otherwise yields {"action", "why"} and the caller must not call the service:

      "loading"    the idle policy unloaded it; its reload runs in the background, in a process of its own
                   (_background_reload), so a later call finds it loaded
      "refused"    that reload was refused for lack of free memory (headroom)
      "failed"     that reload failed (the event says why)
      "unloading"  the idle policy is stopping it at this moment
    """
    idle = idle or (Idle(db_path) if db_path else None)
    data = idle.read() if idle is not None else {}
    if idle is None or (data["timeouts"].get(name) is None and
                        (data["services"].get(name) or {}).get("state") != "unloaded"):
        yield None           # the policy is off for this service: no lock, no marker, no file
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(_flock(idle._file(f"idle-inuse-{name}.lock"), fcntl.LOCK_SH | fcntl.LOCK_NB))
        except BlockingIOError:
            yield {"action": "unloading", "why": f"{idle.services[name].label} is being unloaded while idle"}
            return
        except OSError:      # an unwritable folder must never break a lookup
            pass
        if idle.unloaded_by_policy(name):
            yield _background_reload(idle, name, purpose)
            return
        try:
            idle.touch(name)
            stack.callback(idle.touch, name)
        except OSError:
            pass
        yield None


def wake(db_path, names, *, purpose, notify=None) -> list[threading.Thread]:
    """Reload, each in a background thread, the services in `names` that the idle policy unloaded (a voice session
    starting needs speech recognition and synthesis); `notify` tells the user. Returns the threads started."""
    if not db_path:
        return []
    idle = Idle(db_path)
    threads = []
    if wake_dir() is not None:          # ask the unit that owns the services; nothing to wait for here
        for name in names:
            if idle.unloaded_by_policy(name):
                state = _background_reload(idle, name, purpose)
                if notify is not None:
                    _tell(notify, idle.message(name) if state["action"] == "loading" else state["why"])
        return threads
    for name in names:
        if idle.unloaded_by_policy(name):
            thread = threading.Thread(target=idle.ensure_loaded, args=(name,),
                                      kwargs={"purpose": purpose, "notify": notify}, daemon=True,
                                      name=f"nsf-idle-load-{name}")
            thread.start()
            threads.append(thread)
    return threads


def on_demand(provider, name, db_path):
    """Route a provider's search() through using(): a service the idle policy unloaded starts again on the first
    search (Presence shows what the user is told). Returns the provider."""
    if not db_path:
        return provider
    original = provider.search

    def search(*args, **kwargs):
        with using(db_path, name, purpose="web search"):
            return original(*args, **kwargs)
    provider.search = search
    return provider


def mark_used(db_path, names) -> None:
    """Record a use of each service whose idle policy is on (a voice session ending used ASR and TTS until now)."""
    if not db_path:
        return
    idle = Idle(db_path)
    timeouts = idle.timeouts()
    for name in names:
        if timeouts.get(name) is not None:
            try:
                idle.touch(name)
            except OSError:
                pass


__all__ = ["CRITICAL_FLOOR", "CRITICAL_SHARE", "Idle", "SERVICES", "Service", "mark_used", "on_demand",
           "parse_timeout", "serve_wake_requests", "service_port", "using", "using_now", "wake", "wake_dir"]
