"""Opt-in idle unloading of the local model services (RD-46 policy (c); docs/architecture/RESOURCE_SCHEDULING.md §3).

Services: vllm (the reasoning model), asr (Whisper), tts (Kokoro) and searxng. Each has an idle timeout, OFF by default
(`nsf resources idle --set vllm=30m --set asr=15m --set tts=15m`). `Idle.tick()` (`nsf resources tick`, run by
neuroscifi-idle.timer) unloads a service only when every condition holds:
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

Stop and start use the deploy's own commands: deploy/vllm/run.sh and deploy/voice/run.sh (docker shape), or systemctl
when the service's unit is active (so the policy never fights the unit), and `nsf web searxng stop|start`.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

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


def _port(env, default):
    return os.environ.get(env, default)


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
}


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
        with urlopen(url, timeout=timeout) as resp:   # noqa: S310 - a loopback URL from SERVICES
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
                data["services"].setdefault(name, {}).update(state="unloaded", by=by, at=event["at"])
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
        for event in reversed(self.events(limit=0)):
            if event.get("service") != name or not event.get("ok"):
                continue
            for field, how in (("took_bytes", "taken by its last load"), ("freed_bytes", "freed by its last unload")):
                if isinstance(event.get(field), (int, float)) and event[field] > 0:
                    return {"bytes": int(event[field]), "source": f"{how} (measured)"}
        gib = self.services[name].documented_gib
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
            with self._state() as data:     # Presence shows this while the service starts (any process)
                data["services"].setdefault(name, {}).update(loading={"told": text, "since": self.clock()})
            if notify is not None:
                try:
                    notify(text)
                except Exception:   # a closed client must not stop the load
                    pass
            systemd = self._systemd(svc)
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
            event = self._event(name, "load", reason, by=by, ok=ok, seconds=seconds, told=text,
                                shape="systemd" if systemd else "docker", headroom=room["why"],
                                **({"took_bytes": took} if took and took > 0 else {}),
                                **({} if ok else {"error": (getattr(done, "stdout", "") or "")[-400:]}))
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
                "how": "nsf resources idle --set vllm=30m --set asr=15m --set tts=15m; nsf resources tick "
                       "(neuroscifi-idle.timer); nsf resources unload|load SERVICE"}


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
        yield idle.ensure_loaded(name, purpose=purpose, notify=notify) if timeout is None else \
            _bounded_load(idle, name, purpose=purpose, notify=notify, timeout=timeout)


def wake(db_path, names, *, purpose, notify=None) -> list[threading.Thread]:
    """Reload, each in a background thread, the services in `names` that the idle policy unloaded (a voice session
    starting needs speech recognition and synthesis); `notify` tells the user. Returns the threads started."""
    if not db_path:
        return []
    idle = Idle(db_path)
    threads = []
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
           "parse_timeout", "using", "wake"]
