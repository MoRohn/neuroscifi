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
import stat
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener

_DIRECT = build_opener(ProxyHandler({}))      # a loopback service's /metrics is never read through a proxy

from . import monitor, policy, shared

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
    ready: tuple | None = None       # (container port, path) answering 200 once it serves, for one without /metrics


def _port(env, default):
    return os.environ.get(env, default)


_MEMORY_NAMES = ("NSF_MEMORY_DIR", "NSF_MEMORY_BIND", "NSF_MEMORY_EMBED_PORT", "NSF_MEMORY_RERANK_PORT",
                 "NSF_MEMORY_SERVER", "NSF_MEMORY_CONTAINER")
_MEMORY_LINE = re.compile(r"^(?:export\s+)?(NSF_MEMORY_(?:DIR|BIND|EMBED_PORT|RERANK_PORT|SERVER|CONTAINER))=(.*)$")
_MEMORY_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_MEMORY_PLAIN = re.compile(r"^[0-9A-Za-z_.:/\[\]@%+,=~-]*$")       # what a value may hold once it is expanded
_LOOPBACK = ("127.0.0.1", "::1", "[::1]", "localhost")


def _home(environ) -> str:
    """The home folder as bash's $HOME would give it, without ever raising: HOME, else the account's entry, else
    "~" unchanged (a uid with no passwd entry and no HOME: nothing under it exists, so the defaults apply)."""
    return environ.get("HOME") or os.path.expanduser("~")


def _memory_value(raw, found, environ):
    """The value of one `NAME=value` line as bash's `.` gives it, for the forms a settings file uses, or None when
    the line uses anything else (it is then ignored and reported, never evaluated): one pair of quotes, a
    trailing comment, $HOME / ${HOME}, and ${NAME:-default} for one of the memory names."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote, end = value[0], value.find(value[0], 1)
        if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
            return None
        value, literal = value[1:end], quote == "'"
    else:
        value, literal = re.split(r"\s+#", value, maxsplit=1)[0].strip(), False
        if value.startswith("#"):
            value = ""
    if not literal:
        for _ in range(4):                               # ${NAME:-default}, innermost form first
            match = re.search(r"\$\{(NSF_MEMORY_[A-Z_]+):-([^${}]*(?:\$\{HOME\}[^${}]*|\$HOME[^${}]*)*)\}", value)
            if match is None:
                break
            current = found.get(match.group(1)) or environ.get(match.group(1)) or ""
            value = value[:match.start()] + (current or match.group(2)) + value[match.end():]
        value = value.replace("${HOME}", _home(environ))
        value = re.sub(r"\$HOME(?![A-Za-z0-9_])", lambda _: _home(environ), value)
    return value if _MEMORY_PLAIN.match(value) and len(value) <= 512 else None


def memory_settings(environ=None, repo=None) -> dict:
    """{"dir", "key_file", "bind", "embed_port", "rerank_port", "server", "container", "ignored", "error"}: where
    deploy/memory/run.sh puts the two memory models and their key, and which server holds them ("single": one
    process and one container, `container`, for both; "vllm": a vLLM server and a container each), read the way
    the script resolves them (review F8: `nsf setup` and the idle
    policy looked at the environment only, so an owner's override of a port was seen by the script alone).

    As in the script: the environment first, then deploy/memory/memory.env, then the owner's override
    `<NSF_MEMORY_DIR>/memory.env` (the folder as resolved before that file is read), each later one replacing the
    earlier; the key is `<NSF_MEMORY_DIR>/api_key` with the folder as it stands at the end, so an override file
    may move it. The files are read as `NAME=value` lines for the four memory names; nothing is evaluated. The
    forms a settings file uses are understood (review S6: a quoted value, a leading `export`, a trailing comment,
    $HOME, ${NAME:-default}); a line whose value needs anything else is skipped and listed in "ignored"
    ("file:line"). "error" is set, and the bind address reset to 127.0.0.1, when the files name an address that is
    not loopback: the script refuses to start there, and nothing here may contact such an address. Never raises:
    an account without a home folder gets the defaults."""
    environ = os.environ if environ is None else environ
    found = {name: environ.get(name) or "" for name in _MEMORY_NAMES}
    found["NSF_MEMORY_DIR"] = found["NSF_MEMORY_DIR"] or os.path.join(_home(environ), ".config", "neuroscifi", "memory")
    ignored = []

    def read(path):
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            return
        for number, line in enumerate(lines, 1):
            match = _MEMORY_LINE.match(line.strip())
            if match is None:
                continue
            value = _memory_value(match.group(2), found, environ)
            if value is None:
                ignored.append(f"{path}:{number}")
            elif value or match.group(1) != "NSF_MEMORY_DIR":
                found[match.group(1)] = value

    read(Path(repo or REPO) / "deploy" / "memory" / "memory.env")
    read(Path(found["NSF_MEMORY_DIR"]) / "memory.env")

    def port(name, default):
        return int(found[name]) if found[name].isdigit() and 0 < int(found[name]) < 65536 else default
    bind, error = found["NSF_MEMORY_BIND"] or "127.0.0.1", None
    if bind not in _LOOPBACK:
        error, bind = f"NSF_MEMORY_BIND must be loopback (got {bind[:64]}): deploy/memory/run.sh refuses it", "127.0.0.1"
    folder = Path(found["NSF_MEMORY_DIR"])
    server = found["NSF_MEMORY_SERVER"] if found["NSF_MEMORY_SERVER"] in ("single", "vllm") else "single"
    shared = found["NSF_MEMORY_CONTAINER"] if _MEMORY_CONTAINER.match(found["NSF_MEMORY_CONTAINER"]) \
        else "neuroscifi-memory"
    return {"dir": folder, "key_file": folder / "api_key", "bind": bind,
            "embed_port": port("NSF_MEMORY_EMBED_PORT", 18441), "rerank_port": port("NSF_MEMORY_RERANK_PORT", 18442),
            "server": server, "container": shared, "ignored": ignored, "error": error}


def _build_services() -> dict:
    try:
        memory = memory_settings()
    except Exception:                 # a settings file must never be why the policy, or an import, fails
        memory = {"embed_port": 18441, "rerank_port": 18442}
    # One server for both memory models (deploy/memory/server.py, the default) is one container and one unit under
    # both names: starting either starts it, stopping either stops it, and its /metrics counts the requests of
    # both, so the two are idle together. Measured, both models in it: 4.4 GiB, 16 to 29 s from start to ready
    # (docs/quality/evidence/open_items_memory_server.json).
    single = memory.get("server", "single") == "single"
    boxes = {name: memory.get("container", "neuroscifi-memory") if single else f"neuroscifi-memory-{name}"
             for name in ("embed", "rerank")}
    units = {name: "neuroscifi-memory.service" if single else f"neuroscifi-memory-{name}.service"
             for name in ("embed", "rerank")}
    return {
        "vllm": Service("vllm", "the local model", "neuroscifi-vllm",
                        ("bash", "{repo}/deploy/vllm/run.sh", "start"), ("bash", "{repo}/deploy/vllm/run.sh", "stop"),
                        ("bash", "{repo}/deploy/vllm/run.sh", "wait", "1200"), "neuroscifi-vllm.service",
                        f"http://127.0.0.1:{_port('NSF_VLLM_PORT', '8811')}/metrics", 32.5, 30, 44.1),
        "asr": Service("asr", "speech recognition", "neuroscifi-voice-asr",
                       ("bash", "{repo}/deploy/voice/run.sh", "start", "asr"),
                       ("bash", "{repo}/deploy/voice/run.sh", "stop", "asr"),
                       ("bash", "{repo}/deploy/voice/run.sh", "wait", "asr", "900"), "neuroscifi-voice-asr.service",
                       f"http://127.0.0.1:{_port('NSF_VOICE_ASR_PORT', '18431')}/metrics", 17.2, 15, 4.1),
        "tts": Service("tts", "speech synthesis", "neuroscifi-voice-tts",
                       ("bash", "{repo}/deploy/voice/run.sh", "start", "tts"),
                       ("bash", "{repo}/deploy/voice/run.sh", "stop", "tts"),
                       ("bash", "{repo}/deploy/voice/run.sh", "wait", "tts", "900"), "neuroscifi-voice-tts.service",
                       None, 5.2, 15, 1.5, ready=("8000/tcp", "/health")),
        # SearXNG's own start command waits for /healthz; its unit (deploy/install.sh --searxng) is stopped and
        # started as a unit when it is active, like the others: `nsf web searxng` writes the instance database,
        # which the account that runs the idle pass in an installed deployment may only read.
        "searxng": Service("searxng", "the search engine", "neuroscifi-searxng",
                           ("{py}", "-m", "neuroscifi.cli", "web", "searxng", "start", "--db", "{db}"),
                           ("{py}", "-m", "neuroscifi.cli", "web", "searxng", "stop", "--db", "{db}"),
                           unit="neuroscifi-searxng.service", ready=("8080/tcp", "/healthz")),
        # The memory models (deploy/memory; webcortex/semantic.py). Measured on the reference host as two vLLM
        # servers, each: 18 to 31 s from start to ready with warm files, 4.4 GiB
        # (docs/quality/evidence/accel_decisions_memory_service.json).
        "embed": Service("embed", "memory search (the embedding model)", boxes["embed"],
                         ("bash", "{repo}/deploy/memory/run.sh", "start", "embed"),
                         ("bash", "{repo}/deploy/memory/run.sh", "stop", "embed"),
                         ("bash", "{repo}/deploy/memory/run.sh", "wait", "embed", "600"),
                         units["embed"],
                         f"http://127.0.0.1:{memory['embed_port']}/metrics", 20.0, 15, 4.4,
                         background=True),
        "rerank": Service("rerank", "memory search (the reranker)", boxes["rerank"],
                          ("bash", "{repo}/deploy/memory/run.sh", "start", "rerank"),
                          ("bash", "{repo}/deploy/memory/run.sh", "stop", "rerank"),
                          ("bash", "{repo}/deploy/memory/run.sh", "wait", "rerank", "600"),
                          units["rerank"],
                          f"http://127.0.0.1:{memory['rerank_port']}/metrics", 20.0, 15, 4.4,
                          background=True),
    }


def _services() -> dict:
    """SERVICES, built on first use (review S6: the memory models' ports come from settings files under the home
    folder, and reading them while this module is imported made every importer, the inference gateway included,
    depend on that folder being resolvable). A test or caller that replaces `idle.SERVICES` is honoured."""
    scope = globals()
    if "SERVICES" not in scope:
        scope["SERVICES"] = _build_services()
    return scope["SERVICES"]


def __getattr__(name):                # idle.SERVICES for every importer, without building it at import
    if name == "SERVICES":
        return _services()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def service_port(name, services=None) -> int | None:
    """The loopback port of a service's own server (from its /metrics address), None when it has none."""
    svc = (services or _services()).get(name)
    if svc is None or not svc.metrics:
        return None
    try:
        return int(svc.metrics.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def managed_service(port, services=None) -> str | None:
    """The name of this deployment's own model service that listens on loopback `port`, None for any other port."""
    services = services or _services()
    try:
        return next((name for name in services if service_port(name, services) == int(port)), None)
    except (TypeError, ValueError):
        return None


def listener_refusal(addresses, port, key_file=None, *, table=None, environ=None, services=None) -> str | None:
    """Why nothing may be sent to loopback `port` (connection going to `addresses`), or None: monitor.stranger for
    the ports of this deployment's own services, where it is known who must listen. Any other endpoint the owner
    configured on this computer (Ollama under its own account, another server on its own port) is not checked,
    exactly as before the check existed (review S1). The one implementation behind the inference gateway
    (every provider), `nsf web memory` and `nsf setup`."""
    name = managed_service(port, services)
    if name is None:
        return None
    label = (services or _services())[name].label
    return monitor.stranger(addresses, port, key_file=key_file, table=table, environ=environ, what=label)


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


def _answers(url, timeout=3.0) -> bool:
    """Whether a loopback readiness address answers 200 (speech synthesis /health, SearXNG /healthz)."""
    try:
        with _DIRECT.open(url, timeout=timeout) as resp:   # noqa: S310 - a loopback URL built from SERVICES
            return resp.status == 200
    except (OSError, ValueError):
        return False


READY_WAIT = 60.0          # seconds a running service without a wait command may take to answer (SearXNG's own limit)
READY_POLL = 2.0

_clock, _monotonic = time.time, time.monotonic      # module-level so tests can give every Idle one fake clock


def _run(args, timeout):
    try:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(args, 127, f"{type(error).__name__}: {error}")


@contextmanager
def _flock(path, mode):
    shared.ensure_folder(path.parent)
    fd = shared.open_file(path, os.O_RDWR)
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
                 sample=None, memory=None, ready=None, sleep=None):
        self.db_path = Path(db_path)
        self.folder = Path(f"{db_path}.resources")
        self.runner = runner or _run
        self.clock, self.monotonic = clock or _clock, monotonic or _monotonic
        self.services = services or _services()
        self.read_metrics = metrics or _metrics
        self.sample = sample or (lambda: monitor.sample())
        self.read_memory = memory or (lambda: monitor.memory())     # uncached: a load or unload measures the change
        self.read_ready = ready or _answers
        self.sleep = sleep or time.sleep

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
        shared.ensure_folder(self.folder)
        shared.write_text(self._file("idle.json"), json.dumps(data, indent=1, sort_keys=True))

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
        shared.ensure_folder(self.folder)
        path = self._file("idle-events.jsonl")
        with _flock(self._file("idle-events.lock"), fcntl.LOCK_EX):
            fd = shared.open_file(path, os.O_WRONLY | os.O_APPEND)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2 * EVENTS_KEPT:
                shared.write_text(path, "\n".join(lines[-EVENTS_KEPT:]) + "\n")
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

    def answers(self, svc) -> bool | None:
        """Whether the running service answers: its /metrics is readable (the vLLM servers), or its readiness
        address answers 200 on the port Docker publishes for it (speech synthesis, SearXNG). None when the service
        has neither, or the published port cannot be told: it then counts as before, loaded because it runs."""
        if svc.metrics:
            return self.read_metrics(svc.metrics) is not None
        if not svc.ready:
            return None
        inside, path = svc.ready
        done = self.runner(["docker", "port", svc.container, inside], 30)
        if getattr(done, "returncode", 1) != 0:
            return None
        for line in (getattr(done, "stdout", "") or "").splitlines():
            host, _, port = line.strip().rpartition(":")
            if host in ("127.0.0.1", "[::1]", "::1", "0.0.0.0", "[::]") and port.isdigit():
                where = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else "[::1]"
                return bool(self.read_ready(f"http://{where}:{port}{path}"))
        return None

    def voice_session(self) -> bool:
        return policy.voice_session_active(self.db_path, now=self.clock())

    def running_tasks(self) -> int | None:
        """How many research tasks run now (a running stage with a live lease); None when this account cannot
        tell. The pass then unloads nothing: where the idle policy runs as another account than the one that owns
        the database (deploy/install.sh --idle), a database it may not read must not read as "nothing runs"."""
        try:
            if not self.db_path.exists():
                return 0
        except OSError:
            return None       # the folder itself is closed to this account
        if not os.access(self.db_path, os.R_OK):
            return None
        sql = (f"SELECT count(*) FROM web_tasks WHERE status IN ({','.join('?' * len(RUNNING))}) "
               "AND lease_holder IS NOT NULL AND lease_expires_at>=?")
        # A reader that may not write beside the database (the operations account: the folder is the service
        # account's) cannot create the write-ahead log's files. While a process has the database open they exist
        # and the ordinary read-only open works. When they do not exist nobody has it open, so the file is complete
        # as it stands and is read as it stands (immutable: no lock file is needed).
        plain = f"file:{self.db_path}?mode=ro"
        attempts = [plain]
        if not os.path.exists(f"{self.db_path}-wal") and not os.access(self.db_path.parent, os.W_OK):
            attempts.append(f"{plain}&immutable=1")
        for n, uri in enumerate(attempts):
            try:
                with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
                    return db.execute(sql, (*RUNNING, self.clock())).fetchone()[0]
            except sqlite3.OperationalError as error:
                if "no such table" in str(error):
                    return 0  # no web cortex tables yet: nothing runs
                if n + 1 == len(attempts):
                    return None
            except sqlite3.Error:
                return None
        return None

    def last_used(self, name) -> float | None:
        try:
            return self._file(f"idle-used-{name}").stat().st_mtime
        except OSError:
            return None

    def touch(self, name) -> None:
        shared.ensure_folder(self.folder)
        shared.touch(self._file(f"idle-used-{name}"), self.clock())

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
        if tasks is None:
            blockers.append("whether a research task runs cannot be told: this account may not read the instance "
                            "database (deploy/install.sh --idle-access grants it)")
        elif tasks:
            blockers.append(f"{tasks} research task(s) running")
        now = self.clock()
        decisions, decided = {}, {}
        for name, seconds in timeouts.items():
            if seconds is None:
                continue
            names = self.group(name)
            if len(names) > 1:
                first = decided.get(self.services[name].container)
                if first is not None:            # one container, one decision: made under its first name
                    decisions[name] = {"action": "none", "why": f"one container with {first}: decided there"}
                    continue
                decided[self.services[name].container] = name
                seconds = self.group_timeout(name, timeouts)
                if seconds is None:
                    off = [other for other in names if timeouts.get(other) is None]
                    decisions[name] = {"action": "none", "why": f"one container with {', '.join(off)}, whose idle "
                                       "timeout is off: it is never unloaded while idle"}
                    continue
            decisions[name] = self._consider(self.services[name], seconds, level, blockers, now)
        return {"enabled": True, "level": level, "blockers": blockers, "decisions": decisions}

    def group(self, name) -> list[str]:
        """The services that are one container with `name`, in the order of the service list (the single memory
        server is `embed` and `rerank` at once). The group is the unit of the policy: it is in use while any of its
        names is, its locks are taken in this order, it is stopped and started once, and every name's state
        follows it."""
        box = self.services[name].container
        return [other for other, svc in self.services.items() if svc.container == box]

    def sharing(self, name) -> list[str]:
        """group(name) with `name` first."""
        return [name] + [other for other in self.group(name) if other != name]

    def group_timeout(self, name, timeouts=None) -> float | None:
        """The idle timeout of the container of `name`: the most conservative of its names. A name set to never
        unload (off) keeps the container for good; otherwise the longest timeout of the names (review F4)."""
        timeouts = self.timeouts() if timeouts is None else timeouts
        values = [timeouts.get(other) for other in self.group(name)]
        return None if any(v is None for v in values) else max(values)

    def policy_on(self, name, data=None) -> bool:
        """Whether calls to `name` take its in-use lock and mark their use: the policy is on for any name of its
        container (a timeout set, or the container unloaded). One name's calls must keep the container from being
        stopped under them even when only the other name has a timeout (review F4)."""
        data = self.read() if data is None else data
        names = self.group(name) if name in self.services else [name]
        return any(data["timeouts"].get(other) is not None or
                   (data["services"].get(other) or {}).get("state") == "unloaded" for other in names)

    def _hold_all(self, stack, name, mode) -> None:
        """The in-use lock of every name of `name`'s container, in the group's order (one order: no deadlock)."""
        for other in self.group(name):
            stack.enter_context(_flock(self._file(f"idle-inuse-{other}.lock"), mode))

    def _consider(self, svc, seconds, level, blockers, now) -> dict:
        timeout = self.effective_timeout(seconds, level)
        box = self.container(svc)
        if not box or not box["running"]:
            return {"action": "none", "why": "not loaded"}
        state = self.read()["services"].get(svc.name, {})
        names = self.group(svc.name)
        uses = [box.get("started_at"), state.get("loaded_at"), state.get("served_at")]
        uses += [self.last_used(name) for name in names]
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
            with ExitStack() as held:           # no request in flight under any name of this container
                self._hold_all(held, svc.name, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if any(self.last_used(name) and now - self.last_used(name) < timeout for name in names):
                    return {"action": "none", "why": "used while the decision was being made"}
                return self.unload(svc.name, reason=reason, by="idle policy", _locked=True)
        except BlockingIOError:
            return {"action": "none", "why": "a request is in flight"}

    # ---- load and unload --------------------------------------------------------------------------------------
    def unload(self, name, *, reason, by="owner", _locked=False) -> dict:
        """Stop the service's container. It waits until no call is in flight under any name of that container
        (review F3: the owner's `nsf resources unload embed` stopped the single memory server under a rerank call);
        the result names the other names it unloaded too (`also`)."""
        svc = self.services[name]
        if not _locked:
            with ExitStack() as held:           # waits for in-flight calls, under every name of the container
                self._hold_all(held, name, fcntl.LOCK_EX)
                return self.unload(name, reason=reason, by=by, _locked=True)
        also = self.sharing(name)[1:]
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
                            shape="systemd" if systemd else "docker", **({"also": also} if also else {}),
                            **({"freed_bytes": freed} if freed and freed > 0 else {}),
                            **({} if ok else {"error": (getattr(done, "stdout", "") or "")[-400:]}))
        if ok:
            with self._state() as data:
                for shared in self.group(name):         # one container: every name of it is unloaded now
                    data["services"].setdefault(shared, {}).update(state="unloaded", by=by, at=event["at"],
                                                                   shape="systemd" if systemd else "docker")
        return {"action": "unload" if ok else "failed", "why": reason, "event": event} | ({"also": also} if also else {})

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
        first = self.group(name)[0]            # one start at a time per container, any process, under any name
        with _flock(self._file(f"idle-load-{first}.lock"), fcntl.LOCK_EX):
            box = self.container(svc)
            if box and box["running"]:          # never restart a running service (run.sh start replaces it)
                # Running is not answering (review F4): a container left behind by a reload that failed or was
                # killed may still be starting, or crash-looping. It counts as loaded once it answers; one that
                # does not is stopped, so that "unloaded" stays true and the next attempt starts from nothing.
                # Every service has the rule now: the vLLM servers by their /metrics, speech synthesis and SearXNG by
                # their readiness address (they had kept "running means loaded").
                if self.answers(svc) is False:
                    started = self.monotonic()
                    if svc.wait:
                        done = self.runner(self._cmd(svc.wait), 1500)
                    else:                       # no wait command (SearXNG): ask again for as long as its start may
                        answered = False
                        while not answered and self.monotonic() - started < READY_WAIT:
                            self.sleep(READY_POLL)
                            answered = self.answers(svc) is not False
                        done = subprocess.CompletedProcess([], 0 if answered else 1,
                                                           "" if answered else f"{svc.label} runs but did not answer "
                                                           f"its readiness address within {READY_WAIT:.0f} s")
                    if getattr(done, "returncode", 1) != 0:
                        stopped = self._stop_after_failure(svc, self._systemd(svc))
                        event = self._event(name, "load", reason, by=by, ok=False, found_running=True,
                                            seconds=round(self.monotonic() - started, 1), stopped=stopped,
                                            error=(getattr(done, "stdout", "") or "")[-400:])
                        with self._state() as data:
                            for shared in self.group(name):
                                entry = data["services"].setdefault(shared, {})
                                entry.update(state="unloaded" if entry.get("state") == "unloaded" else "unknown",
                                             failed_at=event["at"])
                        return {"action": "failed", "why": "it was running but did not answer", "event": event}
                with self._state() as data:             # one container: every name of it is loaded
                    for shared in self.group(name):
                        data["services"].setdefault(shared, {}).update(state="loaded", by="already running",
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
                    for shared in self.sharing(name)[1:]:       # one container: its other names answer too
                        data["services"].setdefault(shared, {}).update(state="loaded", by=by, at=event["at"],
                                                                       loaded_at=event["at"])
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
            shared = self.sharing(name)[1:]
            services[name] = {
                "timeout_s": timeouts[name], "suggested_minutes": svc.suggested_minutes,
                **({"shared_with": shared, "effective_timeout_s": self.group_timeout(name, timeouts)} if shared else {}),
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
    if idle is None or not idle.policy_on(name):
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
            shared.ensure_folder(idle.folder)
            shared.touch(path, idle.clock())
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
            if wake_dir() is not None:
                # The reload is another account's to do (review S2): this process only leaves a request. The request
                # file itself is the "one at a time" (created exclusively), so nothing is locked or marked in the
                # policy's folder, which this process may be unable to write.
                ended = _retry_not_due(idle, name)
                if ended is not None:
                    return ended
                _DETACHED[key] = (_request_wake(idle, name), idle.monotonic())
                return loading
            try:
                # Held for milliseconds, and never waited for (review S8: a lookup must not block behind a process
                # stopped while it holds this lock): whoever holds it is starting the reload.
                held = _flock(idle._file(f"idle-spawn-{name}.lock"), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with held:
                    ended = _retry_not_due(idle, name)
                    if ended is not None:
                        return ended
                    if _load_in_progress(idle, name) or _just_spawned(idle, name):
                        return loading
                    _DETACHED[key] = (_spawn_load(idle, name, purpose), idle.monotonic())
                    _just_spawned(idle, name, mark=True)
            except BlockingIOError:
                return loading
        except OSError as error:
            return {"action": "failed", "why": f"{svc.label} did not start: {type(error).__name__}: {error}"[:200]}
    return loading


def _retry_not_due(idle, name) -> dict | None:
    """{"action": "refused" | "failed", "why"} while the last reload of `name` ended refused or failed less than
    BACKGROUND_RETRY seconds ago (by the time Idle.load wrote to idle.json), else None. A state this process cannot
    read gives None: the unit that serves wake requests applies the same limit from its own view."""
    svc = idle.services[name]
    state = idle.read()["services"].get(name) or {}
    ended = max((t for t in (state.get("failed_at"), state.get("refused_at")) if isinstance(t, (int, float))),
                default=None)
    if ended is None or not 0 <= idle.clock() - ended < BACKGROUND_RETRY:
        return None
    last = next((e for e in reversed(idle.events(limit=50))
                 if e.get("service") == name and e.get("action") == "load"), None) or {}
    if last.get("refused"):
        return {"action": "refused", "why": last.get("error") or f"not enough memory to load {svc.label}"}
    detail = " ".join(str(last.get("error") or "see nsf resources").split())[:160]
    return {"action": "failed", "why": f"{svc.label} did not start: {detail}"}


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


WAKE_DIR_MODE = 0o3770     # the request folder: its owner (the account that serves) and its group (who may ask);
#                            sticky, so a request is removed by whoever left it or by the folder's owner, nobody else
STUCK_HOLD = 30.0          # seconds `nsf resources wake` stays when an entry can be neither removed nor moved aside
WAKE_LINGER = 25.0         # seconds neuroscifi-wake.service stays after it starts: at most 24 starts in the unit's
#                            10-minute window, under its limit of 30 (tests/test_deploy_units.py keeps the two apart)
STUCK_PREFIX = ".stuck-"   # hidden, and no *.wake name: systemd's PathExistsGlob= does not see it


def _request_wake(idle, name, *, clock=None):
    """Leave the wake request of `name`: an empty file `<name>.wake`, created exclusively. Its name is the whole
    message, so its mode and its content do not matter. The folder is the serving account's (review S2): this
    process never creates it and never changes its mode; it must already exist and let this account create a file
    (deploy/install.sh makes it setgid and group-writable, WAKE_DIR_MODE, for the console's group). One that has
    waited longer than WAKE_PICKUP was not picked up: OSError, so the caller reports a failure."""
    clock = clock or (idle.clock if idle is not None else _clock)
    folder = wake_dir()
    path = folder / f"{name}{WAKE_SUFFIX}"
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        if idle is not None:                 # the policy's clock (a test's fake one) decides when it is overdue
            os.utime(path, (clock(), clock()))
    except FileExistsError:
        if clock() - path.lstat().st_mtime > WAKE_PICKUP:
            raise OSError(f"its wake request was not picked up within {WAKE_PICKUP:.0f} s (is neuroscifi-wake.path "
                          "active?)") from None
    except FileNotFoundError:
        raise OSError(f"the wake-request folder {folder} does not exist (NSF_WAKE_DIR; `nsf resources wake` and "
                      "deploy/install.sh --idle create it)") from None
    return _Requested(path)


_ASKED: dict = {}                   # service -> when this process last left a request after a refused connection


def asked_on_refusal(port, *, monotonic=None) -> str | None:
    """A connection to loopback `port` was refused. Where this process may not start services itself (NSF_WAKE_DIR
    is set) and the port is one of this deployment's model services, leave a wake request for it and say so; None
    when there is nothing to ask (no request folder, not a managed port).

    This is the path that needs nothing of the idle policy's state (review S2). In the installed deployment the
    policy's files belong to the account that runs the services, and the console and the worker cannot read them;
    they then cannot know that a service was unloaded while idle. They do not need to: the refused connection is
    the signal, and the unit that serves the request decides from its own state whether a reload is due (unloaded
    by the policy, enough free memory, not just failed). At most one request per service every BACKGROUND_RETRY
    seconds from one process; a request already waiting is not repeated."""
    if wake_dir() is None:
        return None
    name = managed_service(port)
    if name is None:
        return None
    now = (monotonic or _monotonic)()
    with _LOADS_LOCK:
        last = _ASKED.get(name)
        if last is not None and 0 <= now - last < BACKGROUND_RETRY:
            return "its start was asked for a moment ago (wake request)"
        try:
            _request_wake(None, name)
        except OSError as error:
            return f"a wake request could not be left: {error}"[:200]
        _ASKED[name] = now
    return "its start was asked for (wake request); a later call finds it loaded if the idle policy had unloaded it"


def _set_aside(path) -> str:
    """Take an entry that is not a request (a folder, a device, anything but a regular file) out of the way of the
    path unit: remove it if it is an empty folder, else rename it to a hidden name that no pattern matches
    (review S3: left under its `*.wake` name it started the unit again after every pass, into its start limit).
    Returns what was done; "stuck" when neither is possible."""
    try:
        os.rmdir(path)
        return "removed"
    except OSError:
        pass
    try:
        os.unlink(path)
        return "removed"
    except OSError:
        pass
    try:
        os.rename(path, path.with_name(f"{STUCK_PREFIX}{path.name}.{os.getpid()}.{time.time_ns()}"))
        return "moved aside"
    except OSError:
        return "stuck"


def _serve_once(idle, folder) -> tuple[list[dict], bool]:
    """One pass over the request folder -> (results, whether an entry is stuck under a request's name)."""
    out, stuck = [], False
    try:
        entries = sorted(p for p in Path(folder).iterdir() if p.name.endswith(WAKE_SUFFIX))
    except OSError:
        return out, False
    for path in entries:
        name = path.name[: -len(WAKE_SUFFIX)]
        try:
            kind = path.lstat().st_mode
        except FileNotFoundError:
            continue                         # another pass took it
        except OSError as error:
            out.append({"request": name[:64], "action": "ignored", "why": f"cannot look at the request: {error}"[:200]})
            stuck = True
            continue
        if not (stat.S_ISREG(kind) or stat.S_ISLNK(kind)):     # only a file is a request (a link is removed, unread)
            done = _set_aside(path)
            stuck = stuck or done == "stuck"
            out.append({"request": name[:64], "action": "ignored", "why": f"not a file: {done}"})
            continue
        try:
            path.unlink()                    # removed first and never opened; a symbolic link is not followed
        except FileNotFoundError:
            continue
        except OSError as error:
            done = _set_aside(path)
            stuck = stuck or done == "stuck"
            out.append({"request": name[:64], "action": "ignored",
                        "why": f"cannot remove the request ({done}): {error}"[:200]})
            continue                         # served without being removed it would be served again and again
        if name not in idle.services:
            out.append({"request": name[:64], "action": "ignored", "why": "not a service of the idle policy"})
            continue
        waits = _retry_not_due(idle, name)
        if waits is not None:                # the limit of _background_reload, applied by the one that loads
            out.append({"service": name, "action": "skipped",
                        "why": f"not retried within {BACKGROUND_RETRY:.0f} s of: {waits['why']}"[:240]})
            continue
        result = idle.ensure_loaded(name, purpose="wake request") or \
            {"action": "none", "why": "nothing to do: the idle policy has not unloaded it"}
        out.append({"service": name, "action": result["action"], "why": result.get("why")})
    return out, stuck


def serve_wake_requests(db_path, folder, *, idle=None, hold=STUCK_HOLD, linger=0.0, sleep=time.sleep) -> list[dict]:
    """What neuroscifi-wake.service runs (`nsf resources wake`): take every request out of `folder` and reload the
    service it names, if the idle policy unloaded it and a reload is due. A request is a regular file's name only:
    it is removed first and never opened, a symbolic link is removed without being followed, and a name that is
    not a service of this policy is dropped without any command being run. The folder is created if it is missing
    (WAKE_DIR_MODE; an existing folder's mode and owner are never changed).

    Anything else under a request's name is removed or moved aside (_set_aside). If even that fails (a read-only
    folder), the path unit would start this command again the moment it exits; so it stays for `hold` seconds,
    serving the requests that arrive meanwhile (review S3).

    `linger` (the unit passes WAKE_LINGER): the command stays that long in any case and serves what arrives, within
    a second. The path unit starts the service once per request that finds it stopped, so without it the number of
    starts is the asker's to choose: whoever may write the folder (the console's group), or simply three processes
    asking for six services every 30 s, could start it more than StartLimitBurst times in StartLimitIntervalSec,
    after which systemd refuses to start it until someone resets it and no request is served at all. Staying
    WAKE_LINGER seconds bounds the starts by the clock (see WAKE_LINGER), whatever is written into the folder."""
    idle = idle or Idle(db_path)
    folder = Path(folder)
    try:
        folder.mkdir(parents=True)
        os.chmod(folder, WAKE_DIR_MODE)      # just created by this process, so its mode is this process's to set
    except OSError:
        pass                                 # it exists (its modes are the installer's or the owner's), or cannot be made
    out, stuck = _serve_once(idle, folder)
    waited = 0.0
    while waited < linger or (stuck and waited < hold):
        sleep(1.0)
        waited += 1.0
        more, stuck = _serve_once(idle, folder)
        out += [item for item in more if item.get("service")]      # a stuck or foreign entry is reported once
    return out


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
    if idle is None or not idle.policy_on(name):
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
    data = idle.read()
    for name in names:
        if name in idle.services and idle.policy_on(name, data):
            try:
                idle.touch(name)
            except OSError:
                pass


__all__ = ["CRITICAL_FLOOR", "CRITICAL_SHARE", "Idle", "SERVICES", "Service",        # noqa: F822 (SERVICES: __getattr__)
           "asked_on_refusal", "listener_refusal", "managed_service", "mark_used", "memory_settings", "on_demand",
           "parse_timeout", "serve_wake_requests", "service_port", "using", "using_now", "wake", "wake_dir"]
