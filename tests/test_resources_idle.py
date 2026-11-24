"""Opt-in idle unloading (RD-46 policy (c), resources/idle.py) against a fake docker / systemctl / run.sh host: off by
default, the unload conditions (timeout, in-flight calls and requests, voice sessions, running tasks), the shorter
timeout at critical pressure, reload on demand with what the user is told and the measured reload time, the owner's
manual unload and load, the systemd shape, the event log in `nsf resources`, and Presence while a service starts.
Also the two reload safeguards: the memory headroom check (refused and recorded when the service would not fit) and
a reload bounded by the caller's time (it finishes in the background), plus the deploy scripts' `wait`, which fails at
once on a crash-looping container."""
from datetime import datetime, timezone
import json
import subprocess
import threading

import pytest

from neuroscifi import resources
from neuroscifi.instance import store
from neuroscifi.presence import aggregator
from neuroscifi.resources import idle, policy
from neuroscifi.voice import state as vstate

T0 = 1_795_200_000.0
MIN = 60.0
GIB = 1024 ** 3


def _iso(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def memory_services(server):
    """idle's service list as built when deploy/memory serves the two models with `server` (single | vllm)."""
    real = idle.memory_settings
    try:
        idle.memory_settings = lambda *a, **k: real(*a, **k) | {"server": server, "container": "neuroscifi-memory"}
        return idle._build_services()
    finally:
        idle.memory_settings = real


@pytest.fixture(autouse=True)
def two_vllm_servers(monkeypatch):
    """The tests below describe the policy over one container per memory model (NSF_MEMORY_SERVER=vllm), as they
    were written. The shipped default, one server and one container for both, has its own tests at the end."""
    monkeypatch.setattr(idle, "SERVICES", memory_services("vllm"), raising=False)


class FakeHost:
    """docker inspect / run.sh start|stop|wait / systemctl / `nsf web searxng` as one fake host with a clock. A wait
    advances the clock by the service's reload time, so the measured reload is known in advance."""

    RELOAD = {"neuroscifi-vllm": 41.0, "neuroscifi-voice-asr": 18.0, "neuroscifi-voice-tts": 5.0,
              "neuroscifi-searxng": 3.0, "neuroscifi-memory-embed": 19.0, "neuroscifi-memory-rerank": 21.0}
    PUBLISHED = {"neuroscifi-voice-tts": 18432, "neuroscifi-searxng": 18888}     # what `docker port` answers

    def __init__(self):
        self.now = T0
        self.running = {c: T0 - 6 * 3600 for c in self.RELOAD}       # container -> started at
        self.calls = []
        self.units = set()                                             # active systemd units
        self.fail = set()
        self.fail_wait = set()                                         # containers that start and never answer

    def clock(self):
        return self.now

    def __call__(self, argv, timeout):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "")
        if argv[0] == "docker" and argv[1] == "inspect":
            name = argv[-1]
            if name not in self.running:
                return subprocess.CompletedProcess(argv, 1, "Error: No such object")
            return subprocess.CompletedProcess(argv, 0, f"true {_iso(self.running[name])}\n")
        if argv[0] == "docker" and argv[1] == "port":
            name = argv[2]
            if name not in self.running or name not in self.PUBLISHED:
                return subprocess.CompletedProcess(argv, 1, "Error: No public port")
            return subprocess.CompletedProcess(argv, 0, f"127.0.0.1:{self.PUBLISHED[name]}\n")
        if argv[0] == "systemctl":
            unit = argv[-1]
            if argv[1] == "is-active":
                return subprocess.CompletedProcess(argv, 0 if unit in self.units else 3, "")
            name = idle.SERVICES[{"neuroscifi-vllm.service": "vllm", "neuroscifi-voice-asr.service": "asr",
                                  "neuroscifi-voice-tts.service": "tts", "neuroscifi-memory-embed.service": "embed",
                                  "neuroscifi-memory-rerank.service": "rerank",
                                  "neuroscifi-searxng.service": "searxng"}[unit]].container
            self._act(argv[2], name)
            if argv[2] == "stop":                     # as systemd does: a stopped unit is no longer active
                self.units.discard(unit)
            elif argv[2] == "start":
                self.units.add(unit)
            return ok
        joined = " ".join(argv)
        container = next(svc.container for svc in idle.SERVICES.values()
                         if (svc.name == "vllm" and "deploy/vllm/run.sh" in joined) or
                         (svc.name in ("asr", "tts") and "deploy/voice/run.sh" in joined and argv[-1] == svc.name) or
                         (svc.name in ("asr", "tts") and "wait" in argv and argv[-2] == svc.name) or
                         (svc.name in ("embed", "rerank") and "deploy/memory/run.sh" in joined and svc.name in argv) or
                         (svc.name == "searxng" and "searxng" in argv))
        verb = next(v for v in ("start", "stop", "wait") if v in argv)
        if container in self.fail and verb != "stop":
            return subprocess.CompletedProcess(argv, 1, "run.sh: container exited")
        if container in self.fail_wait and verb == "wait":
            return subprocess.CompletedProcess(argv, 1, "run.sh: not ready after 600s")
        self._act(verb, container)
        return ok

    def _act(self, verb, container):
        if verb == "stop":
            self.running.pop(container, None)
        elif verb == "start":
            self.running[container] = self.now
        elif verb == "wait":
            self.now += self.RELOAD[container]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    host = FakeHost()
    host.metrics = {"vllm": {"in_flight": 0.0, "served": 7.0}, "asr": {"in_flight": 0.0, "served": 3.0}}
    host.level = "ok"
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0 - 7 * 3600)

    def metrics(url):
        name = "vllm" if ":8811/" in url else "embed" if ":18441/" in url else "rerank" if ":18442/" in url else "asr"
        value = host.metrics.get(name)
        return dict(value) if value is not None else None

    host.deaf, host.asked = set(), []          # ports whose readiness address does not answer; every address asked

    def ready(url):
        host.asked.append(url)
        return int(url.split(":")[2].split("/")[0]) not in host.deaf

    def nap(seconds):                          # a wait for readiness passes on the fake clock
        host.now += seconds

    def make():
        return idle.Idle(path, runner=host, clock=host.clock, monotonic=host.clock, metrics=metrics, ready=ready,
                         sleep=nap,
                         sample=lambda: {"level": host.level, "reasons": ["memory available 5.0 GiB of 128.0 GiB is "
                                                                          "below the critical threshold 7.7 GiB"]
                                         if host.level == "critical" else []})
    host.make, host.path, host.db = make, path, db
    # Anything that builds its own Idle (using, wake, the gateway, the hub) talks to the fake host too.
    monkeypatch.setattr(idle, "_run", host)
    monkeypatch.setattr(idle, "_metrics", metrics)
    monkeypatch.setattr(idle, "_answers", ready)
    monkeypatch.setattr(idle, "_clock", host.clock)
    monkeypatch.setattr(idle, "_monotonic", host.clock)
    vstate.reset()
    yield host
    vstate.reset()
    db.close()


def stops(host):
    return [c for c in host.calls if "stop" in c]


def test_off_by_default_nothing_is_unloaded_and_no_file_is_written(fake):
    policy_ = fake.make()
    fake.now += 24 * 3600
    assert policy_.tick() == {"enabled": False, "decisions": {}}
    with idle.using(fake.path, "vllm", purpose="chat") as loaded:
        assert loaded is None
    assert fake.calls == [] and not (fake.path.parent / "individual.sqlite.resources").exists()
    shown = resources.status(fake.path, full=False)["policy"]["idle_unload"]
    assert shown["automatic"] is False and shown["events"] == []
    assert {n: s["timeout_s"] for n, s in shown["services"].items()} == dict.fromkeys(idle.SERVICES)


def test_an_idle_service_unloads_after_its_timeout_through_run_sh_and_the_event_says_why(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    with idle.using(fake.path, "vllm", purpose="chat"):
        pass                                                   # last use: now
    fake.now += 20 * MIN
    out = p.tick()
    assert out["decisions"]["vllm"]["action"] == "none" and "used 20 min ago" in out["decisions"]["vllm"]["why"]
    assert stops(fake) == []
    fake.now += 11 * MIN
    out = p.tick()
    assert out["decisions"]["vllm"]["action"] == "unload"
    assert stops(fake) == [["bash", str(idle.REPO / "deploy/vllm/run.sh"), "stop"]]
    assert "neuroscifi-vllm" not in fake.running and "neuroscifi-voice-asr" in fake.running   # only vLLM had a timeout
    event = p.events()[-1]
    assert event["action"] == "unload" and event["by"] == "idle policy" and event["ok"] is True
    assert event["reason"] == "idle 31 min (timeout 30 min)" and event["shape"] == "docker"
    assert p.unloaded_by_policy("vllm")
    shown = resources.status(fake.path, full=False)["policy"]["idle_unload"]
    assert shown["automatic"] is True and shown["services"]["vllm"]["timeout_s"] == 1800
    assert shown["services"]["vllm"]["state"] == "unloaded" and shown["events"][-1]["reason"] == event["reason"]
    assert p.tick()["decisions"]["vllm"] == {"action": "none", "why": "not loaded"}


def test_requests_seen_in_the_service_metrics_count_as_use(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    fake.now += 40 * MIN
    fake.metrics["vllm"]["in_flight"] = 2.0                    # a client that does not go through NeuroSciFi
    assert p.tick()["decisions"]["vllm"]["action"] == "none"
    fake.metrics["vllm"]["in_flight"] = 0.0
    fake.now += 20 * MIN
    fake.metrics["vllm"]["served"] = 9.0                       # served more since the last pass
    assert p.tick()["decisions"]["vllm"]["action"] == "none"
    fake.now += 31 * MIN
    assert p.tick()["decisions"]["vllm"]["action"] == "unload"
    fake.metrics["vllm"] = None                                # unreadable: in-flight work cannot be ruled out
    fake.running["neuroscifi-vllm"] = fake.now - 3600
    fake.now += 3600
    assert "unreadable" in p.tick()["decisions"]["vllm"]["why"]


def test_never_unloads_while_a_call_is_in_flight(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    entered, release = threading.Event(), threading.Event()

    def call():
        with idle.using(fake.path, "vllm", purpose="chat"):
            entered.set()
            release.wait(10)
    worker = threading.Thread(target=call)
    worker.start()
    entered.wait(10)
    fake.now += 3600                                           # the call started an hour ago and still runs
    try:
        assert p.tick()["decisions"]["vllm"] == {"action": "none", "why": "a request is in flight"}
    finally:
        release.set()
        worker.join(10)
    assert stops(fake) == []


def test_never_unloads_during_a_voice_session_or_a_running_task(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN, "asr": 15 * MIN, "tts": 15 * MIN})
    fake.now += 3600
    vstate.update("s1", owner=False, mode="voice_text", session_active=True)     # a Pocket session counts too
    out = p.tick()
    assert out["blockers"] == ["a voice session is open"]
    assert all(d["action"] == "none" for d in out["decisions"].values()) and stops(fake) == []
    vstate.reset()
    # The console that hosts the voice hub runs in another process: its beacon carries the open session.
    beacon = policy.AudioBeacon(fake.path, clock=fake.clock)
    vstate.update("s2", mode="voice_text", session_active=True)
    beacon.tick()
    vstate.reset()
    assert policy.voice_session_active(fake.path, now=fake.now) and not policy.audio_active(fake.path, now=fake.now)
    assert p.tick()["blockers"] == ["a voice session is open"]
    beacon.close()                                              # the session ended: the beacon says so at once
    assert not policy.voice_session_active(fake.path, now=fake.now)
    with store.write(fake.db):
        fake.db.execute("INSERT INTO web_tasks(id,objective,mode,status,origin,created_at,updated_at,lease_holder,"
                        "lease_expires_at) VALUES('t1','q','quick_lookup','reading','user',?,?,'w',?)",
                        (fake.now, fake.now, fake.now + 60))
    assert p.tick()["blockers"] == ["1 research task(s) running"] and stops(fake) == []
    with store.write(fake.db):
        fake.db.execute("UPDATE web_tasks SET status='completed', lease_holder=NULL")
    out = p.tick()
    assert out["blockers"] == [] and {d["action"] for d in out["decisions"].values()} == {"unload"}
    assert len(stops(fake)) == 3


def test_critical_pressure_shortens_the_timeout(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    with idle.using(fake.path, "vllm", purpose="chat"):
        pass
    fake.now += 8 * MIN
    assert p.tick()["decisions"]["vllm"]["action"] == "none"
    fake.level = "critical"
    out = p.tick()["decisions"]["vllm"]
    assert out["action"] == "unload"
    assert out["why"].startswith("idle 8 min (timeout 30 min, shortened to 8 min at critical memory pressure: "
                                 "memory available 5.0 GiB")
    assert p.effective_timeout(30 * MIN, "critical") == 7.5 * MIN
    assert p.effective_timeout(4 * MIN, "critical") == idle.CRITICAL_FLOOR
    assert p.effective_timeout(None, "critical") is None        # off stays off


def test_reload_on_demand_tells_the_user_and_records_the_measured_time(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    fake.now += 3600
    p.tick()
    told = []
    with idle.using(fake.path, "vllm", purpose="chat", notify=told.append) as loaded:
        assert "neuroscifi-vllm" in fake.running
    assert told == ["Starting the local model, about 33 s"]     # the documented cold start, 32.5 s
    assert loaded["action"] == "load" and loaded["seconds"] == 41.0
    starts = [c for c in fake.calls if "start" in c or "wait" in c]
    assert starts == [["bash", str(idle.REPO / "deploy/vllm/run.sh"), "start"],
                      ["bash", str(idle.REPO / "deploy/vllm/run.sh"), "wait", "1200"]]
    event = p.events()[-1]
    assert (event["action"], event["by"], event["reason"], event["seconds"]) == ("load", "on demand", "on demand: chat",
                                                                                41.0)
    assert not p.unloaded_by_policy("vllm") and p.loading() == []
    # The next reload announces the measured time, not the documented one.
    fake.now += 3600
    p.tick()
    assert p.message("vllm") == "Starting the local model, about 41 s"
    # A second caller while one load runs waits for it and does not start the service again.
    with idle.using(fake.path, "vllm", purpose="chat"), idle.using(fake.path, "vllm", purpose="chat") as again:
        assert again is None
    assert sum("start" in c for c in fake.calls) == 2


def test_a_failed_reload_is_recorded_and_retried_by_the_next_call(fake):
    p = fake.make()
    p.configure({"asr": 15 * MIN})
    fake.now += 3600
    p.tick()
    fake.fail.add("neuroscifi-voice-asr")
    with idle.using(fake.path, "asr", purpose="voice session") as loaded:
        assert loaded["action"] == "failed"
    assert p.events()[-1]["ok"] is False and "container exited" in p.events()[-1]["error"]
    assert p.unloaded_by_policy("asr")
    fake.fail.clear()
    with idle.using(fake.path, "asr", purpose="voice session") as loaded:
        assert loaded["action"] == "load" and loaded["seconds"] == 18.0


def test_the_owner_unloads_and_loads_by_hand_and_an_owner_unload_is_not_undone(fake, capsys):
    from neuroscifi.cli import main
    p = fake.make()
    out = p.unload("tts", reason="owner command")                # no timeout needed
    assert out["action"] == "unload" and "neuroscifi-voice-tts" not in fake.running
    with idle.using(fake.path, "tts", purpose="voice session") as loaded:
        assert loaded is None                                   # the owner's choice stands
    assert "neuroscifi-voice-tts" not in fake.running
    told = []
    out = p.load("tts", reason="owner command", notify=told.append)
    assert out["action"] == "load" and told == ["Starting speech synthesis, about 6 s"]
    assert p.load("tts", reason="owner command") == {"action": "none", "why": "already running"}
    assert sum("start" in c for c in fake.calls) == 1            # a running service is never restarted
    assert main(["resources", "idle", "--db", str(fake.path), "--set", "vllm=30m", "--set", "asr=15min"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["services"]["vllm"]["timeout_s"] == 1800 and shown["services"]["asr"]["timeout_s"] == 900
    assert main(["resources", "idle", "--db", str(fake.path), "--set", "asr=off"]) == 0
    assert json.loads(capsys.readouterr().out)["services"]["asr"]["timeout_s"] is None
    with pytest.raises(SystemExit, match="under one minute"):
        main(["resources", "idle", "--db", str(fake.path), "--set", "vllm=10s"])
    with pytest.raises(SystemExit, match="unknown service"):
        main(["resources", "idle", "--db", str(fake.path), "--set", "gpu=10m"])
    assert main(["resources", "unload", "searxng", "--db", str(fake.path)]) == 0
    assert json.loads(capsys.readouterr().out)["event"]["by"] == "owner"
    assert any(c[-4:] == ["searxng", "stop", "--db", str(fake.path)] for c in fake.calls)


def test_an_active_systemd_unit_is_stopped_and_started_with_systemctl(fake):
    fake.units.add("neuroscifi-vllm.service")
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    fake.now += 3600
    assert p.tick()["decisions"]["vllm"]["event"]["shape"] == "systemd"
    assert ["systemctl", "--no-ask-password", "stop", "neuroscifi-vllm.service"] in fake.calls
    with idle.using(fake.path, "vllm", purpose="chat"):
        pass
    assert ["systemctl", "--no-ask-password", "start", "neuroscifi-vllm.service"] in fake.calls
    # The unit this policy stopped is started as the unit, not as a second container through run.sh (the fake host
    # now reports a stopped unit as inactive, as systemd does: with that, the earlier code ran `run.sh start`).
    assert not [c for c in fake.calls if "deploy/vllm/run.sh" in " ".join(c) and "start" in c]


def test_presence_shows_what_the_user_is_told_while_a_service_starts(fake):
    p = fake.make()
    p.configure({"vllm": 30 * MIN})
    fake.now += 3600
    p.tick()
    seen = []
    real = fake._act

    def act(verb, container):                                   # look at Presence while the model is starting
        if verb == "wait":
            seen.append(aggregator.derive(fake.db, fake.path, fake.now))
        real(verb, container)
    fake._act = act
    with idle.using(fake.path, "vllm", purpose="chat"):
        pass
    assert "Starting the local model, about 33 s" in json.dumps(seen[0])
    after = json.dumps(aggregator.derive(fake.db, fake.path, fake.now))
    assert "Starting the local model" not in after


def test_wake_loads_the_speech_services_a_voice_session_needs(fake):
    p = fake.make()
    p.configure({"asr": 15 * MIN, "tts": 15 * MIN})
    fake.now += 3600
    p.tick()
    told = []
    for thread in idle.wake(fake.path, ("asr", "tts"), purpose="voice session", notify=told.append):
        thread.join(10)
    assert sorted(told) == ["Starting speech recognition, about 18 s", "Starting speech synthesis, about 6 s"]
    assert {"neuroscifi-voice-asr", "neuroscifi-voice-tts"} <= set(fake.running)
    assert idle.wake(fake.path, ("asr",), purpose="voice session") == []        # nothing left to load


def test_parse_timeout():
    assert idle.parse_timeout("30m") == 1800 and idle.parse_timeout("15min") == 900
    assert idle.parse_timeout("1h") == 3600 and idle.parse_timeout("900") == 900
    assert idle.parse_timeout("off") is None and idle.parse_timeout("0") is None
    with pytest.raises(ValueError):
        idle.parse_timeout("soon")


def test_resources_idle_shows_loaded_as_resources_does(fake, capsys, monkeypatch):
    """R6 minor: `nsf resources idle` showed `loaded: null` while `nsf resources` showed `true` for the same service.
    Both now read the running neuroscifi-* containers."""
    from neuroscifi.cli import main
    from neuroscifi.resources import monitor
    running = [{"name": "neuroscifi-vllm"}, {"name": "neuroscifi-searxng"}]
    monkeypatch.setattr(monitor, "containers", lambda: running)
    monitor.clear_cache()
    assert main(["resources", "idle", "--db", str(fake.path)]) == 0
    idle_view = {k: v["loaded"] for k, v in json.loads(capsys.readouterr().out)["services"].items()}
    assert main(["resources", "--db", str(fake.path)]) == 0
    status_view = {k: v["loaded"] for k, v in
                   json.loads(capsys.readouterr().out)["policy"]["idle_unload"]["services"].items()}
    assert idle_view == status_view == {"vllm": True, "asr": False, "tts": False, "searxng": True,
                                        "embed": False, "rerank": False}
    monitor.clear_cache()


# ---- memory headroom before a reload --------------------------------------------------------------------------

def _memory(*available_gib):
    """A /proc/meminfo reader that returns the given MemAvailable readings in turn (the last one repeats)."""
    readings = list(available_gib)

    def read():
        gib = readings.pop(0) if len(readings) > 1 else readings[0]
        return {"total_bytes": 128 * GIB, "available_bytes": int(gib * GIB), "swap_total_bytes": 0,
                "swap_free_bytes": 0, "psi": None}
    return read


def _unloaded_by_policy(fake, memory):
    policy_ = fake.make()
    policy_.read_memory = memory
    policy_.configure({"vllm": 30 * MIN})
    policy_.unload("vllm", reason="idle 31 min (timeout 30 min)", by="idle policy")
    return policy_


def test_a_reload_that_would_not_fit_in_free_memory_is_refused_recorded_and_starts_nothing(fake):
    policy_ = _unloaded_by_policy(fake, _memory(40))     # 40 GiB free; vLLM is documented at 44.1 GiB
    told = []
    with idle.using(fake.path, "vllm", purpose="chat", notify=told.append, idle=policy_) as result:
        assert result["action"] == "refused"
    assert "not enough memory to load the local model: 40.0 GiB available" in result["why"]
    assert "44.1 GiB (documented (RESOURCE_SCHEDULING.md §4))" in result["why"] and "7.7 GiB must stay free" in \
        result["why"]
    assert not [c for c in fake.calls if "start" in c] and told == []          # nothing started, nobody told
    event = policy_.events()[-1]
    assert event["action"] == "load" and event["ok"] is False and event["refused"] is True
    assert event["error"] == result["why"] and event["reason"] == "on demand: chat"
    assert policy_.unloaded_by_policy("vllm")                                   # the next use checks again
    assert policy_.estimate("vllm") == 32.5                                     # a refusal is not a reload time


def test_a_reload_that_fits_records_how_much_memory_it_took_and_the_next_check_uses_it(fake):
    # Unload: 20 GiB free before the stop, 50 after (30 GiB freed). Reload: 50 GiB free before, 18 after (32 taken).
    policy_ = _unloaded_by_policy(fake, _memory(20, 50))
    assert policy_.events()[-1]["freed_bytes"] == 30 * GIB
    assert policy_.footprint("vllm") == {"bytes": 30 * GIB, "source": "freed by its last unload (measured)"}
    policy_.read_memory = _memory(50, 50, 18)    # headroom check, then before and after the start
    out = policy_.ensure_loaded("vllm", purpose="chat")
    assert out["action"] == "load"
    assert out["event"]["took_bytes"] == 32 * GIB and "50.0 GiB available" in out["event"]["headroom"]
    assert policy_.footprint("vllm") == {"bytes": 32 * GIB, "source": "taken by its last load (measured)"}


def test_unreadable_memory_skips_the_headroom_check_and_says_so(fake):
    policy_ = _unloaded_by_policy(fake, lambda: None)
    out = policy_.ensure_loaded("vllm", purpose="chat")
    assert out["action"] == "load" and out["event"]["headroom"] == "not checked: /proc/meminfo is not readable"


def test_the_owner_load_command_exits_non_zero_when_the_reload_is_refused(fake, capsys, monkeypatch):
    from neuroscifi.cli import main
    from neuroscifi.resources import monitor
    _unloaded_by_policy(fake, _memory(64))
    monkeypatch.setattr(monitor, "memory", lambda *a, **k: _memory(10)())
    assert main(["resources", "load", "vllm", "--db", str(fake.path)]) == 1
    assert json.loads(capsys.readouterr().out)["action"] == "refused"


# ---- a reload bounded by the caller's time --------------------------------------------------------------------

def test_using_with_a_timeout_returns_the_reload_when_it_finishes_in_time(fake):
    policy_ = _unloaded_by_policy(fake, _memory(64))
    told = []
    with idle.using(fake.path, "vllm", purpose="chat", notify=told.append, idle=policy_, timeout=30) as result:
        assert result["action"] == "load" and result["seconds"] == 41.0
    assert told == ["Starting the local model, about 33 s"]
    with idle.using(fake.path, "vllm", purpose="chat", notify=told.append, idle=policy_, timeout=30) as result:
        assert result is None                                                   # loaded: nothing to wait for
    assert len(told) == 1


def test_callers_that_run_out_of_time_share_one_background_reload_and_each_is_told_once(fake):
    policy_ = _unloaded_by_policy(fake, _memory(64))
    gate = threading.Event()
    runner = policy_.runner

    def slow(argv, timeout):                     # the deploy's wait blocks until the test lets it finish
        if "wait" in [str(a) for a in argv]:
            gate.wait(10)
        return runner(argv, timeout)
    policy_.runner = slow
    first, second = [], []
    with idle.using(fake.path, "vllm", purpose="chat", notify=first.append, idle=policy_, timeout=0.2) as a:
        pass
    with idle.using(fake.path, "vllm", purpose="chat", notify=second.append, idle=policy_, timeout=0.2) as b:
        pass
    assert a["action"] == b["action"] == "loading"
    assert first == second == ["Starting the local model, about 33 s"] and a["told"] == first[0]
    assert "keeps starting in the background" in a["why"]
    gate.set()
    idle._LOADS[(str(policy_.db_path), "vllm")].join(10)
    assert len([c for c in fake.calls if "start" in c]) == 1                    # one start for both callers
    assert policy_.events()[-1]["action"] == "load" and policy_.events()[-1]["ok"] is True
    assert first == second == ["Starting the local model, about 33 s"]          # nobody was told twice


# ---- run.sh wait fails promptly on a crash-looping container --------------------------------------------------

def _fake_tools(tmp_path, *, states, healthy=False):
    """docker / curl / sleep on PATH: `docker inspect` reports the container states in turn (the last repeats),
    `curl` fails unless `healthy`, and `sleep` returns at once (and counts the polls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "states").write_text("\n".join(states) + "\n")
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'S="{tmp_path}/states"\n'
        'case "$*" in\n'
        '  *"{{.State.Status}} {{.RestartCount}}"*)\n'
        '    head -n1 "$S"; [ "$(wc -l < "$S")" -gt 1 ] && sed -i 1d "$S"; exit 0 ;;\n'
        '  *"{{.RestartCount}}"*) head -n1 "$S" | cut -d" " -f2 ;;\n'
        "esac\n")
    (bin_dir / "curl").write_text(f"#!/usr/bin/env bash\nexit {0 if healthy else 7}\n")
    (bin_dir / "sleep").write_text(f'#!/usr/bin/env bash\necho x >> "{tmp_path}/polls"\n')
    for tool in ("docker", "curl", "sleep"):
        (bin_dir / tool).chmod(0o755)
    import os
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}


def _wait(env, *args):
    return subprocess.run(["bash", *args], env=env, capture_output=True, text=True, timeout=60, check=False,
                          cwd=str(idle.REPO))


@pytest.mark.parametrize("script", [("deploy/vllm/run.sh", "wait", "1200"),
                                    ("deploy/voice/run.sh", "wait", "asr", "900")])
def test_run_sh_wait_fails_at_once_when_the_container_is_restarting(tmp_path, script):
    done = _wait(_fake_tools(tmp_path, states=["restarting 1"]), *script)
    assert done.returncode != 0 and "is restarting (it stopped while starting)" in done.stderr
    assert not (tmp_path / "polls").exists()                                   # failed on the first look


@pytest.mark.parametrize("script", [("deploy/vllm/run.sh", "wait", "1200"),
                                    ("deploy/voice/run.sh", "wait", "asr", "900")])
def test_run_sh_wait_fails_when_the_restart_count_grows_between_two_polls(tmp_path, script):
    # docker shows the container `running` again between crashes: only the restart count gives the loop away
    done = _wait(_fake_tools(tmp_path, states=["running 2", "running 3"]), *script)
    assert done.returncode != 0 and "restarted 1 time(s) while starting" in done.stderr
    assert (tmp_path / "polls").read_text().count("x") == 1


def test_run_sh_wait_still_returns_when_the_server_is_healthy(tmp_path):
    done = _wait(_fake_tools(tmp_path, states=["running 0"], healthy=True), "deploy/vllm/run.sh", "wait", "60")
    assert done.returncode == 0 and "healthy after" in done.stdout


def test_a_measured_footprint_far_above_the_documented_one_is_not_believed(tmp_path, monkeypatch):
    """Review: another workload freeing memory while the model unloaded left a "footprint" of 100 GiB, after which
    every load was refused, the owner's included, and a refused load records nothing that could correct it."""
    from neuroscifi.resources import idle as idle_mod, monitor
    policy_ = idle_mod.Idle(tmp_path / "i.sqlite")
    documented = policy_.services["vllm"].documented_gib
    events = [{"service": "vllm", "ok": True, "action": "unload", "freed_bytes": 100 * monitor.GIB}]
    monkeypatch.setattr(idle_mod.Idle, "events", lambda self, limit=20: events)
    assert policy_.footprint("vllm") == {"bytes": int(documented * monitor.GIB),
                                         "source": "documented (RESOURCE_SCHEDULING.md §4)"}
    events.append({"service": "vllm", "ok": True, "action": "load", "took_bytes": int(1.2 * documented * monitor.GIB)})
    assert policy_.footprint("vllm")["source"].endswith("(measured)")


# ---- the memory models (embed, rerank): unloaded when idle, never waited for -----------------------------------

MEMORY = ("embed", "rerank")


_REAL_SPAWN_LOAD = idle._spawn_load


class _Reload:
    """Stands in for the reload process of using_now (`nsf resources load NAME --on-demand PURPOSE`, started by
    idle._spawn_load): the same call against the fake host, in a thread. poll() is the process's exit code."""

    def __init__(self, path, name, purpose):
        self.code = None
        self.thread = threading.Thread(target=self._run, args=(path, name, purpose), daemon=True)
        self.thread.start()

    def _run(self, path, name, purpose):
        out = idle.Idle(path).ensure_loaded(name, purpose=purpose) or {}
        self.code = 1 if out.get("action") in ("failed", "refused") else 0

    def poll(self):
        return None if self.thread.is_alive() else self.code


@pytest.fixture
def memory_host(fake, monkeypatch):
    """The fake host with both memory services answering /metrics, plenty of free memory, and a gate: with
    `fake.gate` cleared the deploy's `wait` blocks, so a test decides when a background reload ends."""
    from neuroscifi.resources import monitor
    fake.metrics.update(embed={"in_flight": 0.0, "served": 11.0}, rerank={"in_flight": 0.0, "served": 5.0})
    monkeypatch.setattr(monitor, "memory", lambda *a, **k: _memory(64)())
    fake.gate = threading.Event()
    fake.gate.set()
    fake.shown_while_starting = []

    def gated(argv, timeout):
        if "wait" in [str(a) for a in argv]:
            fake.shown_while_starting.append(fake.make().loading())     # what Presence would show meanwhile
            assert fake.gate.wait(10)
        return fake(argv, timeout)
    monkeypatch.setattr(idle, "_run", gated)
    monkeypatch.setattr(idle, "_spawn_load", lambda policy_, name, purpose: _Reload(policy_.db_path, name, purpose))
    idle._DETACHED.clear()
    yield fake
    _finish_loads(fake)
    idle._DETACHED.clear()


def _finish_loads(fake):
    fake.gate.set()
    for reload, _ in list(idle._DETACHED.values()):
        thread = getattr(reload, "thread", None)          # a wake request (review F5) has no thread of ours
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive()


def _unload_idle(fake, *names):
    p = fake.make()
    p.configure(dict.fromkeys(names, 15 * MIN))
    fake.now += 16 * MIN
    out = p.tick()["decisions"]
    assert all(out[name]["action"] == "unload" for name in names)
    return p


def test_the_memory_models_are_services_of_the_policy_off_by_default(memory_host):
    fake = memory_host
    p = fake.make()
    assert set(MEMORY) <= set(idle.SERVICES) and all(idle.SERVICES[n].background for n in MEMORY)
    assert not any(idle.SERVICES[n].background for n in ("vllm", "asr", "tts", "searxng"))
    assert (idle.service_port("embed"), idle.service_port("rerank"), idle.service_port("tts")) == (18441, 18442, None)
    assert p.timeouts()["embed"] is None and p.timeouts()["rerank"] is None
    fake.now += 24 * 3600
    assert p.tick() == {"enabled": False, "decisions": {}}
    for name in MEMORY:                                   # off: no lock, no marker, no file, nothing to wait for
        with idle.using_now(fake.path, name, purpose="memory lookup") as state:
            assert state is None
    assert not p.folder.exists() and stops(fake) == []


def test_an_idle_memory_model_is_unloaded_with_the_deploys_own_command_and_the_other_stays(memory_host):
    fake = memory_host
    p = fake.make()
    p.configure({"embed": 15 * MIN})
    with idle.using_now(fake.path, "embed", purpose="memory lookup"):      # a lookup uses it now
        pass
    fake.now += 10 * MIN
    assert p.tick()["decisions"]["embed"]["action"] == "none"
    fake.now += 6 * MIN
    out = p.tick()["decisions"]
    assert out["embed"]["action"] == "unload" and "rerank" not in out
    assert stops(fake)[-1][-3:] == [str(idle.REPO / "deploy/memory/run.sh"), "stop", "embed"]
    assert "neuroscifi-memory-embed" not in fake.running and "neuroscifi-memory-rerank" in fake.running
    assert p.unloaded_by_policy("embed") and not p.unloaded_by_policy("rerank")


def test_a_use_keeps_a_memory_model_loaded_and_a_request_in_flight_blocks_the_unload(memory_host):
    fake = memory_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    fake.now += 14 * MIN
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state is None
        fake.now += 30 * MIN                              # a lookup in flight holds the in-use lock
        out = p.tick()["decisions"]
        assert out["embed"] == {"action": "none", "why": "a request is in flight"}
        assert out["rerank"]["action"] == "unload"        # the other one was idle all along
    assert p.tick()["decisions"]["embed"]["action"] == "none"       # just used
    assert "neuroscifi-memory-embed" in fake.running and "neuroscifi-memory-rerank" not in fake.running
    fake.metrics["embed"]["in_flight"] = 1.0              # the server itself says a request runs
    fake.now += 3600
    assert p.tick()["decisions"]["embed"]["action"] == "none"


def test_a_lookup_never_waits_for_an_unloaded_memory_model_it_starts_the_reload_in_the_background(memory_host):
    fake = memory_host
    p = _unload_idle(fake, "embed")
    fake.gate.clear()                                     # the reload will take until the test says so
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state == {"action": "loading", "why": "memory search (the embedding model) is unloaded while idle "
                                                     "and is starting again (about 20 s)"}
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as again:
        assert again["action"] == "loading"               # the same reload, not a second start
    _finish_loads(fake)
    assert sum("start" in c for c in fake.calls) == 1 and "neuroscifi-memory-embed" in fake.running
    assert fake.shown_while_starting == [[]]              # nobody waits for it: nothing is shown as starting
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state is None
    event = p.events()[-1]
    assert (event["action"], event["by"], event["reason"], event["seconds"]) == \
        ("load", "on demand", "on demand: memory lookup", 19.0)
    assert p.estimate("embed") == 19.0


def test_a_model_somebody_waits_for_is_still_shown_as_starting(memory_host):
    """The change above is for the memory models only: the answer model's reload is announced as before."""
    fake = memory_host
    _unload_idle(fake, "vllm")
    with idle.using(fake.path, "vllm", purpose="chat") as loaded:
        assert loaded["action"] == "load"
    assert [entry["service"] for entry in fake.shown_while_starting[-1]] == ["vllm"]


def test_a_memory_model_being_unloaded_right_now_is_not_waited_for(memory_host):
    import fcntl
    import os
    fake = memory_host
    p = fake.make()
    p.configure({"rerank": 15 * MIN})
    fd = os.open(p._file("idle-inuse-rerank.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)                    # the policy holds this while the service stops
        with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
            assert state == {"action": "unloading", "why": "memory search (the reranker) is being unloaded while idle"}
    finally:
        os.close(fd)


def test_a_memory_model_that_does_not_fit_is_refused_once_per_back_off_not_on_every_lookup(memory_host, monkeypatch):
    from neuroscifi.resources import monitor
    fake = memory_host
    p = _unload_idle(fake, "rerank")
    monkeypatch.setattr(monitor, "memory", lambda *a, **k: _memory(9)())      # 9 GiB free, 7.7 must stay free
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "loading"               # the refusal comes from the reload, in the background
    _finish_loads(fake)
    for _ in range(5):
        with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
            assert state["action"] == "refused"
        _finish_loads(fake)
    assert "not enough memory to load memory search (the reranker)" in state["why"] and "4.4 GiB" in state["why"]
    refusals = [e for e in p.events() if e.get("refused")]
    assert len(refusals) == 1 and not [c for c in fake.calls if "start" in c]
    fake.now += idle.BACKGROUND_RETRY + 1                 # after the back-off the next lookup tries again
    with idle.using_now(fake.path, "rerank", purpose="memory lookup"):
        pass
    _finish_loads(fake)
    assert len([e for e in p.events() if e.get("refused")]) == 2
    monkeypatch.setattr(monitor, "memory", lambda *a, **k: _memory(64)())     # memory is back
    fake.now += idle.BACKGROUND_RETRY + 1
    with idle.using_now(fake.path, "rerank", purpose="memory lookup"):
        pass
    _finish_loads(fake)
    assert "neuroscifi-memory-rerank" in fake.running
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state is None


def test_the_memory_units_are_stopped_and_started_through_systemd_when_active(memory_host):
    fake = memory_host
    fake.units.add("neuroscifi-memory-embed.service")
    p = fake.make()
    p.configure({"embed": 15 * MIN})
    fake.now += 16 * MIN
    assert p.tick()["decisions"]["embed"]["event"]["shape"] == "systemd"
    assert ["systemctl", "--no-ask-password", "stop", "neuroscifi-memory-embed.service"] in fake.calls
    assert "neuroscifi-memory-embed.service" not in fake.units       # stopped: `systemctl is-active` now says no
    out = p.ensure_loaded("embed", purpose="memory lookup")
    assert out["action"] == "load" and out["event"]["shape"] == "systemd"
    assert ["systemctl", "--no-ask-password", "start", "neuroscifi-memory-embed.service"] in fake.calls
    assert not [c for c in fake.calls if "deploy/memory/run.sh" in " ".join(c) and "start" in c]   # no second container
    assert "neuroscifi-memory-embed.service" in fake.units


class _MemoryModel:
    """A stand-in memory model at the local service's own address."""

    def __init__(self, port, model):
        from neuroscifi.inference import config as iconfig
        self.cfg = iconfig.InferenceConfig(base_url=f"http://127.0.0.1:{port}/v1", model=model)
        self.calls = 0

    def embed(self, texts, *, purpose, timeout=None, input_type=None):
        self.calls += 1
        return [[1.0, 0.0] if "harbour" in t.lower() or "port" in t.lower() else [0.0, 1.0] for t in texts]

    def rerank(self, query, documents, *, purpose, timeout=None):
        self.calls += 1
        return [0.9 if "port" in d.lower() else 0.001 for d in documents]

    def health(self):
        return {"state": "unavailable", "reason": "connection refused"}


QUESTION = "which harbour does it accept traffic on"      # no word of the stored fact: only meaning finds it


@pytest.fixture
def remembering(memory_host, monkeypatch):
    """An individual with two facts and semantic retrieval on, its memory models at the local services' ports."""
    from neuroscifi.webcortex import knowledge, semantic, state as wstate
    fake = memory_host
    wstate.set_config(fake.db, {"enabled": True}, T0)
    knowledge.assert_fact(fake.db, "By default, the Orbix server listens on port 7420 for client connections.", T0)
    knowledge.assert_fact(fake.db, "The Tolvane Lighthouse was built in 1874.", T0 + 1)
    semantic.set_config(fake.db, {"enabled": True}, T0)
    fake.models = [_MemoryModel(18441, "embedder"), _MemoryModel(18442, "reranker")]
    monkeypatch.setattr(semantic, "_gateways", lambda db: tuple(fake.models))
    semantic.clear()
    yield fake
    semantic.clear()


def test_a_memory_lookup_is_lexical_at_once_while_its_models_sleep_and_semantic_again_once_they_are_back(remembering):
    from neuroscifi.webcortex import knowledge
    fake = remembering
    first = knowledge.query(fake.db, QUESTION, T0 + 10)
    assert first["retrieval"]["mode"] == "semantic" and len(first["items"]) == 1
    _unload_idle(fake, "embed", "rerank")
    calls = [m.calls for m in fake.models]
    knowledge.assert_fact(fake.db, "Quillstream 4.2.0 was released on 2 October 2026.", T0 + 20)   # new memory state
    fake.gate.clear()
    asleep = knowledge.query(fake.db, QUESTION, T0 + 30)
    assert asleep["items"] == [] and asleep["retrieval"]["mode"] == "lexical"
    assert asleep["retrieval"]["waking"] == ["embed", "rerank"]
    assert asleep["retrieval"]["reason"] == (
        "memory search (the embedding model) is unloaded while idle and is starting again (about 20 s); "
        "memory search (the reranker) is unloaded while idle and is starting again (about 20 s)")
    assert [m.calls for m in fake.models] == calls        # a sleeping model is never called
    _finish_loads(fake)
    assert {"neuroscifi-memory-embed", "neuroscifi-memory-rerank"} <= set(fake.running)
    back = knowledge.query(fake.db, QUESTION, T0 + 40)
    assert back["retrieval"]["mode"] == "semantic" and len(back["items"]) == 1
    assert fake.shown_while_starting == [[], []]          # Presence never showed a service as starting


def test_with_one_memory_model_asleep_the_lookup_is_lexical_not_half_semantic(remembering):
    from neuroscifi.webcortex import knowledge
    fake = remembering
    _unload_idle(fake, "rerank")
    fake.gate.clear()
    out = knowledge.query(fake.db, QUESTION, T0 + 30)
    assert out["retrieval"]["mode"] == "lexical" and out["retrieval"]["waking"] == ["rerank"]
    assert [m.calls for m in fake.models] == [0, 0]       # the embedding model was awake and still not asked


def test_a_memory_model_that_cannot_start_is_tried_once_per_back_off_not_on_every_lookup(remembering, monkeypatch):
    from neuroscifi.webcortex import knowledge, semantic
    fake = remembering
    _unload_idle(fake, "embed")
    fake.fail.add("neuroscifi-memory-embed")
    clock = [1000.0]
    monkeypatch.setattr(semantic.time, "monotonic", lambda: clock[0])
    knowledge.query(fake.db, QUESTION, T0 + 30)           # starts the reload, which fails in the background
    _finish_loads(fake)
    told = knowledge.query(fake.db, QUESTION + " now", T0 + 31)["retrieval"]
    assert told["mode"] == "lexical" and "memory search (the embedding model) did not start" in told["reason"]
    assert "run.sh: container exited" in told["reason"] and "waking" not in told     # nothing is starting now
    starts = sum("start" in c for c in fake.calls)
    assert starts == 1
    later = knowledge.query(fake.db, QUESTION + " later", T0 + 32)["retrieval"]
    assert later["mode"] == "lexical" and later["retry_in_seconds"] > 0
    assert sum("start" in c for c in fake.calls) == starts           # left alone during the back-off
    fake.fail.clear()
    clock[0] += semantic.RETRY_AFTER + 1
    fake.now += idle.BACKGROUND_RETRY + 1
    knowledge.query(fake.db, QUESTION + " again", T0 + 33)
    _finish_loads(fake)
    assert knowledge.query(fake.db, QUESTION, T0 + 34)["retrieval"]["mode"] == "semantic"


def test_a_memory_endpoint_elsewhere_is_never_touched_by_the_idle_policy(remembering):
    from neuroscifi.webcortex import knowledge
    fake = remembering
    fake.models[0].cfg.base_url = "http://127.0.0.1:9000/v1"         # the owner's own embedding server
    fake.models[1].cfg.base_url = "http://memory.example:18442/v1"   # and a reranker on another host
    p = _unload_idle(fake, "embed", "rerank")                        # the local containers are unloaded ...
    assert p.unloaded_by_policy("embed") and p.unloaded_by_policy("rerank")
    starts = sum("start" in c for c in fake.calls)
    out = knowledge.query(fake.db, QUESTION, T0 + 30)
    assert out["retrieval"]["mode"] == "semantic"                    # ... and these endpoints are simply called
    assert sum("start" in c for c in fake.calls) == starts and idle._DETACHED == {}


def test_memory_status_says_asleep_not_unavailable_for_a_model_unloaded_while_idle(remembering):
    from neuroscifi.webcortex import semantic
    fake = remembering
    _unload_idle(fake, "embed")
    shown = semantic.status(fake.db)["providers"]
    assert shown["embed"]["state"] == "asleep" and "starts it again" in shown["embed"]["reason"]
    assert shown["rerank"]["state"] == "unavailable" and shown["rerank"]["reason"] == "connection refused"


def test_the_background_reload_is_a_process_of_its_own_that_outlives_the_lookup(memory_host, monkeypatch):
    """A one-shot command (`nsf web knowledge TOPIC`) ends right after its lookup. A reload in one of its threads
    would die with it and the model would never start, whatever the lookup said: so the reload is
    `nsf resources load NAME --on-demand PURPOSE` in its own session, with no pipe back to the caller."""
    import sys
    fake = memory_host
    _unload_idle(fake, "embed")
    started = []

    class Process:
        def poll(self):
            return None

    def popen(argv, **options):
        started.append((argv, options))
        return Process()
    monkeypatch.setattr(idle, "_spawn_load", _REAL_SPAWN_LOAD)       # the real one ...
    monkeypatch.setattr(idle.subprocess, "Popen", popen)             # ... which must start exactly this
    idle._DETACHED.clear()
    with idle.using_now(fake.path, "embed", purpose="memory lookup", idle=fake.make()) as state:
        assert state["action"] == "loading"
    with idle.using_now(fake.path, "embed", purpose="memory lookup", idle=fake.make()) as state:
        assert state["action"] == "loading"               # one process, not one per lookup
    idle._DETACHED.clear()
    assert len(started) == 1
    argv, options = started[0]
    assert argv == [sys.executable, "-P", "-m", "neuroscifi.cli", "resources", "load", "embed", "--db", str(fake.path),
                    "--on-demand", "memory lookup"]
    assert options["start_new_session"] is True and options["close_fds"] is True
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL


def test_a_reload_another_process_is_running_is_not_started_twice(memory_host):
    import fcntl
    import os
    fake = memory_host
    p = _unload_idle(fake, "embed")
    fd = os.open(p._file("idle-load-embed.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)                    # another process is inside Idle.load
        with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
            assert state["action"] == "loading"
        assert idle._DETACHED == {} and not [c for c in fake.calls if "start" in c]
    finally:
        os.close(fd)


def test_a_reload_process_started_a_moment_ago_by_another_process_is_not_started_again(memory_host, monkeypatch):
    """Measured live: a lookup 0.2 s after a one-shot command's lookup found no load lock held yet (the reload
    process was still starting Python) and started a second process, which then only waited for the first."""
    fake = memory_host
    _unload_idle(fake, "rerank")
    spawned = []

    class StillStarting:                                  # a process that has not reached Idle.load yet
        def poll(self):
            return None
    monkeypatch.setattr(idle, "_spawn_load", lambda *a: spawned.append(a[1]) or StillStarting())
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    idle._DETACHED.clear()                                # what any other process knows: nothing
    fake.now += 1
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    assert spawned == ["rerank"]
    idle._DETACHED.clear()
    fake.now += idle.SPAWN_GRACE                          # it never took the lock: it died; start again
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    assert spawned == ["rerank", "rerank"]
    idle._DETACHED.clear()


def test_the_on_demand_load_command_reloads_only_what_the_idle_policy_unloaded(memory_host, capsys):
    from neuroscifi.cli import main
    fake = memory_host
    p = fake.make()
    argv = ["resources", "load", "embed", "--db", str(fake.path), "--on-demand", "memory lookup"]
    assert main(argv) == 0                                # loaded: nothing to do
    assert json.loads(capsys.readouterr().out)["action"] == "none" and not [c for c in fake.calls if "start" in c]
    p.unload("embed", reason="owner command")             # the owner's unload stands
    assert main(argv) == 0 and "neuroscifi-memory-embed" not in fake.running
    capsys.readouterr()
    p.load("embed", reason="owner command")
    _unload_idle(fake, "embed")
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "load" and out["event"]["by"] == "on demand" and \
        out["event"]["reason"] == "on demand: memory lookup"
    assert "neuroscifi-memory-embed" in fake.running and fake.make().loading() == []
    _unload_idle(fake, "embed")
    fake.fail.add("neuroscifi-memory-embed")
    assert main(argv) == 1                                # the caller's process sees the failure in the exit code
    assert json.loads(capsys.readouterr().out)["action"] == "failed"


# ---- review F4: running is not answering ----------------------------------------------------------------------

def test_a_start_that_never_answers_is_stopped_so_unloaded_stays_true(memory_host):
    """Review F4: `run.sh start` leaves a container with --restart unless-stopped. When the wait then failed, it
    stayed (or kept restarting) with its memory while the state said "unloaded"."""
    fake = memory_host
    p = _unload_idle(fake, "embed")
    fake.fail_wait.add("neuroscifi-memory-embed")
    out = p.ensure_loaded("embed", purpose="memory lookup")
    assert out["action"] == "failed" and out["event"]["stopped"] is True
    assert "neuroscifi-memory-embed" not in fake.running and p.unloaded_by_policy("embed")
    assert stops(fake)[-1][-2:] == ["stop", "embed"]


def test_a_container_found_running_counts_as_loaded_only_once_it_answers(memory_host):
    """Review F4: the reload of a service whose container already ran marked it "loaded" at once, although a
    container left by a killed or failed reload may still be starting or crash-looping; lookups then called a
    model that did not answer."""
    fake = memory_host
    p = _unload_idle(fake, "embed")
    fake.running["neuroscifi-memory-embed"] = fake.now       # left behind by a reload that was killed mid-way
    answering = fake.metrics.pop("embed")                    # it runs, and does not answer yet
    waits = sum("wait" in c for c in fake.calls)
    out = p.ensure_loaded("embed", purpose="memory lookup")
    assert out == {"action": "none", "why": "already running"} and not p.unloaded_by_policy("embed")
    assert sum("wait" in c for c in fake.calls) == waits + 1  # it was waited for first
    # ... and one that never answers is stopped, recorded, and stays unloaded
    p = _unload_idle(fake, "rerank")
    fake.running["neuroscifi-memory-rerank"] = fake.now
    del fake.metrics["rerank"]
    fake.fail_wait.add("neuroscifi-memory-rerank")
    out = p.ensure_loaded("rerank", purpose="memory lookup")
    assert out["action"] == "failed" and out["why"] == "it was running but did not answer"
    assert out["event"]["found_running"] is True and out["event"]["stopped"] is True
    assert "neuroscifi-memory-rerank" not in fake.running and p.unloaded_by_policy("rerank")
    fake.metrics["embed"] = answering
    waits = sum("wait" in c for c in fake.calls)
    assert p.load("embed", reason="owner command") == {"action": "none", "why": "already running"}
    assert sum("wait" in c for c in fake.calls) == waits      # one that answers is not waited for again


# ---- review F6: the retry limit holds across processes ---------------------------------------------------------

def test_a_failed_reload_is_not_started_again_by_another_process_inside_the_back_off(memory_host):
    """Review F6: the 30 s limit lived in the process that started the reload, so every one-shot command started a
    new one. It is now the failure time Idle.load records, which every process reads."""
    fake = memory_host
    p = _unload_idle(fake, "embed")
    fake.fail.add("neuroscifi-memory-embed")
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    _finish_loads(fake)
    starts = sum("start" in c for c in fake.calls)
    for _ in range(3):                                    # three other processes: they know nothing of the first
        idle._DETACHED.clear()
        fake.now += 5
        with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
            assert state["action"] == "failed" and "did not start: run.sh: container exited" in state["why"]
    assert idle._DETACHED == {} and sum("start" in c for c in fake.calls) == starts
    assert len([e for e in p.events() if e.get("action") == "load"]) == 1
    fake.now += idle.BACKGROUND_RETRY                     # the back-off is over: the next process tries once
    fake.fail.clear()
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    _finish_loads(fake)
    assert "neuroscifi-memory-embed" in fake.running


# ---- review F5: wake requests, for processes that may not start services themselves ---------------------------

@pytest.fixture
def spool(memory_host, monkeypatch, tmp_path):
    """The installed shape: NSF_WAKE_DIR is set, and starting a process or a service from here is an error. The
    folder exists: it is the serving account's (deploy/install.sh --idle, or `nsf resources wake`, makes it), and a
    process that only asks never creates it (review S2)."""
    folder = tmp_path / "wake"
    folder.mkdir()
    monkeypatch.setenv("NSF_WAKE_DIR", str(folder))
    monkeypatch.setattr(idle, "_ASKED", {})

    def forbidden(*args):
        raise AssertionError("with NSF_WAKE_DIR set this process starts nothing itself")
    monkeypatch.setattr(idle, "_spawn_load", forbidden)
    memory_host.spool = folder
    return memory_host


def _serve(fake):
    from neuroscifi.cli import main
    return main(["resources", "wake", "--db", str(fake.path), "--dir", str(fake.spool)])


def test_a_lookup_leaves_a_wake_request_and_the_wake_command_reloads_the_service(spool, capsys):
    import stat
    fake = spool
    _unload_idle(fake, "embed")
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    request = fake.spool / "embed.wake"
    assert request.read_bytes() == b"" and stat.S_IMODE(request.stat().st_mode) == 0o600
    assert not [c for c in fake.calls if "start" in c]            # nothing was started from the lookup's process
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state["action"] == "loading"                        # the same request, still waiting
    assert _serve(fake) == 0                                       # what neuroscifi-wake.service runs
    served = json.loads(capsys.readouterr().out)["served"]
    assert served == [{"service": "embed", "action": "load", "why": "on demand: wake request"}]
    assert not request.exists() and "neuroscifi-memory-embed" in fake.running
    event = fake.make().events()[-1]
    assert (event["action"], event["by"], event["ok"]) == ("load", "on demand", True) and "headroom" in event
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state is None
    assert _serve(fake) == 0 and json.loads(capsys.readouterr().out) == {"served": []}


def test_a_wake_request_is_only_a_service_name_anything_else_is_dropped_and_nothing_is_run(spool, capsys):
    fake = spool
    for name in ("nope", "vllm; touch pwned", "$(id)", "--help", "embed.wake"):
        (fake.spool / f"{name}.wake").write_text("resources unload vllm")      # content is never read
    (fake.spool / "link.wake").symlink_to(fake.path)                           # nor followed
    (fake.spool / "embed.txt").write_text("")                                   # not a request
    calls = len(fake.calls)
    assert _serve(fake) == 0
    served = json.loads(capsys.readouterr().out)["served"]
    assert len(served) == 6 and {item["action"] for item in served} == {"ignored"}
    assert len(fake.calls) == calls and fake.path.exists()                      # no command, and the target stands
    assert sorted(p.name for p in fake.spool.iterdir()) == ["embed.txt"]
    # a request for a real service that is loaded, or that the owner unloaded, starts nothing either
    (fake.spool / "rerank.wake").write_text("")
    fake.make().unload("embed", reason="owner command")
    (fake.spool / "embed.wake").write_text("")
    assert _serve(fake) == 0
    assert {item["action"] for item in json.loads(capsys.readouterr().out)["served"]} == {"none"}
    assert "neuroscifi-memory-embed" not in fake.running


def test_a_wake_request_nobody_picks_up_is_reported_not_called_starting_forever(spool):
    fake = spool
    _unload_idle(fake, "rerank")
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    idle._DETACHED.clear()                                # another process, a minute later, the request still there
    fake.now += idle.WAKE_PICKUP + 1
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "failed" and "was not picked up within 60 s" in state["why"]
        assert "neuroscifi-wake.path" in state["why"]


def test_a_call_that_waits_for_its_service_asks_and_waits_for_the_wake_unit(spool, monkeypatch):
    """The answer model in the installed shape: using() leaves the request, tells the user, and waits until the
    unit that may start services has loaded it; a caller out of time gets "loading" and the load goes on."""
    fake = spool
    _unload_idle(fake, "vllm")
    told = []
    with idle.using(fake.path, "vllm", purpose="chat", notify=told.append, timeout=0.0) as result:
        assert result["action"] == "loading" and (fake.spool / "vllm.wake").exists()
    assert told == ["Starting the local model, about 33 s"]
    server = threading.Timer(0.3, lambda: idle.serve_wake_requests(fake.path, fake.spool))
    server.start()
    with idle.using(fake.path, "vllm", purpose="chat", notify=told.append, timeout=20.0) as result:
        assert result["action"] == "load"
    server.join(10)
    assert "neuroscifi-vllm" in fake.running and not (fake.spool / "vllm.wake").exists()
    fake.make().configure({"asr": 15 * MIN})              # and a voice session's wake() only asks
    fake.now += 3600
    fake.make().tick()
    assert idle.wake(fake.path, ("asr",), purpose="voice session", notify=told.append) == []
    assert (fake.spool / "asr.wake").exists() and told[-1].startswith("Starting speech recognition")


# ---- review F8: one reading of where the memory models are -----------------------------------------------------

def test_memory_settings_are_read_as_run_sh_resolves_them_and_nothing_is_evaluated(tmp_path):
    repo, folder = tmp_path / "repo", tmp_path / "cfg"
    (repo / "deploy" / "memory").mkdir(parents=True)
    folder.mkdir()
    (repo / "deploy" / "memory" / "memory.env").write_text(
        "# shipped\nNSF_MEMORY_BIND=127.0.0.1\nNSF_MEMORY_EMBED_PORT=18441\nNSF_MEMORY_RERANK_PORT=18442\n"
        "NSF_MEMORY_DIR=${NSF_MEMORY_DIR:-${HOME}/.config/neuroscifi/memory}\n")
    env = {"NSF_MEMORY_DIR": str(folder), "NSF_MEMORY_EMBED_PORT": "1"}       # the script ignores this port too
    assert idle.memory_settings(env, repo) == {"dir": folder, "key_file": folder / "api_key", "bind": "127.0.0.1",
                                               "embed_port": 18441, "rerank_port": 18442, "server": "single",
                                               "container": "neuroscifi-memory", "ignored": [], "error": None}
    marker = tmp_path / "evaluated"
    (folder / "memory.env").write_text(                                   # the owner's override
        f"NSF_MEMORY_EMBED_PORT=28441\nNSF_MEMORY_RERANK_PORT=$(touch {marker})\n"
        f"NSF_MEMORY_BIND=`touch {marker}`\nexport NSF_MEMORY_RERANK_PORT=9\nNSF_MEMORY_RERANK_PORT=99999999\n")
    found = idle.memory_settings(env, repo)
    assert (found["embed_port"], found["rerank_port"], found["bind"]) == (28441, 18442, "127.0.0.1")
    assert not marker.exists()
    override = folder / "memory.env"
    assert found["ignored"] == [f"{override}:2", f"{override}:3"] and found["error"] is None   # said, not evaluated


def test_setup_and_the_policy_look_where_the_script_listens(tmp_path, monkeypatch):
    from neuroscifi import setup_cli
    folder = tmp_path / "cfg"
    folder.mkdir()
    (folder / "memory.env").write_text("NSF_MEMORY_EMBED_PORT=28441\nNSF_MEMORY_RERANK_PORT=28442\n")
    monkeypatch.setenv("NSF_MEMORY_DIR", str(folder))
    found = setup_cli.memory_defaults()
    assert found == {"urls": {"embed": "http://127.0.0.1:28441/v1", "rerank": "http://127.0.0.1:28442/v1"},
                     "key_file": folder / "api_key", "error": None, "ignored": []}
    script = (idle.REPO / "deploy" / "memory" / "memory.env").read_text()
    assert "NSF_MEMORY_DIR=${NSF_MEMORY_DIR:-${HOME}/.config/neuroscifi/memory}" in script   # run.sh honours it too



# ---- one server for both memory models (NSF_MEMORY_SERVER=single, the shipped default) ---------------------------

SHARED = "neuroscifi-memory"


@pytest.fixture
def single_host(memory_host, monkeypatch):
    """The fake host with deploy/memory in its single mode: `embed` and `rerank` are one container."""
    monkeypatch.setattr(idle, "SERVICES", memory_services("single"), raising=False)
    fake = memory_host
    fake.RELOAD = dict(fake.RELOAD, **{SHARED: 17.0})
    for name in ("neuroscifi-memory-embed", "neuroscifi-memory-rerank"):
        fake.running.pop(name, None)
    fake.running[SHARED] = T0 - 6 * 3600
    return fake


def test_which_server_holds_the_memory_models_is_read_from_the_scripts_settings(tmp_path):
    repo, folder = tmp_path / "repo", tmp_path / "cfg"
    (repo / "deploy" / "memory").mkdir(parents=True)
    folder.mkdir()
    shipped = repo / "deploy" / "memory" / "memory.env"
    shipped.write_text("NSF_MEMORY_SERVER=single\nNSF_MEMORY_CONTAINER=neuroscifi-memory\n")
    env = {"NSF_MEMORY_DIR": str(folder), "NSF_MEMORY_SERVER": "vllm"}        # the script's file wins over this too
    assert (idle.memory_settings(env, repo)["server"], idle.memory_settings(env, repo)["container"]) == \
        ("single", "neuroscifi-memory")
    (folder / "memory.env").write_text("NSF_MEMORY_SERVER=vllm\n")          # the owner goes back to two servers
    assert idle.memory_settings(env, repo)["server"] == "vllm"
    (folder / "memory.env").write_text("NSF_MEMORY_SERVER=triton\nNSF_MEMORY_CONTAINER=my-memory.1\n")
    found = idle.memory_settings(env, repo)
    assert (found["server"], found["container"]) == ("single", "my-memory.1")     # run.sh refuses to start "triton"
    (folder / "memory.env").write_text("NSF_MEMORY_CONTAINER=a/b\n")
    assert idle.memory_settings(env, repo)["container"] == "neuroscifi-memory"   # not a container name: the default


def test_with_one_server_both_names_are_one_container_and_one_unit():
    single, pair = memory_services("single"), memory_services("vllm")
    assert {single[n].container for n in MEMORY} == {SHARED}
    assert {single[n].unit for n in MEMORY} == {"neuroscifi-memory.service"}
    assert (single["embed"].metrics, single["rerank"].metrics) == (pair["embed"].metrics, pair["rerank"].metrics)
    assert [single[n].stop[-2:] for n in MEMORY] == [("stop", "embed"), ("stop", "rerank")]   # the script decides
    assert [pair[n].container for n in MEMORY] == ["neuroscifi-memory-embed", "neuroscifi-memory-rerank"]
    assert [pair[n].unit for n in MEMORY] == ["neuroscifi-memory-embed.service", "neuroscifi-memory-rerank.service"]
    assert {n: s for n, s in single.items() if n not in MEMORY} == {n: s for n, s in pair.items() if n not in MEMORY}


def test_with_one_server_an_idle_unload_of_either_name_stops_the_one_container(single_host):
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    with idle.using_now(fake.path, "embed", purpose="memory lookup"):
        pass
    fake.now += 10 * MIN
    out = p.tick()["decisions"]                           # the reranker was never used by name: it is the same
    assert out["embed"]["action"] == out["rerank"]["action"] == "none", out       # container, used 10 min ago
    fake.now += 6 * MIN
    out = p.tick()["decisions"]
    assert "unload" in (out["embed"]["action"], out["rerank"]["action"])
    assert SHARED not in fake.running                     # the one container is gone, whichever name asked
    assert p.unloaded_by_policy("embed") and p.unloaded_by_policy("rerank")       # and both names know it
    after = p.tick()["decisions"]                         # one decision per container, under its first name
    assert after == {"embed": {"action": "none", "why": "not loaded"},
                     "rerank": {"action": "none", "why": "one container with embed: decided there"}}
    assert sum(c[-2] == "stop" and c[-1] in MEMORY for c in fake.calls) == 1      # stopped once, not twice


def test_with_one_server_a_use_under_one_name_keeps_the_container_for_both(single_host):
    """An individual that uses the embedding model alone must not lose the container to the reranker's timeout,
    and a request in flight under one name blocks the unload asked for under the other."""
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    for _ in range(4):
        with idle.using_now(fake.path, "embed", purpose="memory lookup"):
            pass
        fake.now += 10 * MIN
        out = p.tick()["decisions"]
        assert out["embed"]["action"] == out["rerank"]["action"] == "none", out
    assert SHARED in fake.running
    with idle.using_now(fake.path, "embed", purpose="memory lookup"):
        fake.now += 3600                                  # a lookup in flight holds the embedding name's lock
        out = p.tick()["decisions"]
        assert out["embed"] == {"action": "none", "why": "a request is in flight"}
        assert out["rerank"] == {"action": "none", "why": "one container with embed: decided there"}
    assert SHARED in fake.running


def test_with_one_server_loading_both_names_starts_the_container_once(single_host):
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    assert p.unload("embed", reason="test")["action"] == "unload" and SHARED not in fake.running
    del fake.calls[:]
    first = p.load("embed", reason="memory lookup", by="on demand")
    assert first["action"] == "load" and SHARED in fake.running
    second = p.load("rerank", reason="memory lookup", by="on demand")
    assert second == {"action": "none", "why": "already running"}
    assert sum("start" in c for c in fake.calls) == 1 and sum("wait" in c for c in fake.calls) == 1


# ---- review round: one container, two names (findings F3 and F4 of the open-items review) ---------------------------

def _in_thread(fn):
    out, done = {}, threading.Event()

    def run():
        try:
            out["result"] = fn()
        finally:
            done.set()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return out, done, thread


def test_an_owners_unload_of_one_name_waits_for_a_call_under_the_other_name(single_host):
    """F3: `nsf resources unload embed` took only embed's in-use lock and stopped the one container under a rerank
    call in flight. It now waits until no call under either name is in flight, then stops it once and both names
    say so."""
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state is None                              # a rerank call is in flight
        out, done, thread = _in_thread(lambda: p.unload("embed", reason="owner", by="owner"))
        assert not done.wait(0.5), "the owner's unload stopped the container under a rerank call"
        assert SHARED in fake.running
    thread.join(10)
    assert out["result"]["action"] == "unload" and out["result"]["also"] == ["rerank"]
    assert SHARED not in fake.running and sum("stop" in c for c in fake.calls) == 1
    state = p.read()["services"]
    assert state["embed"]["state"] == state["rerank"]["state"] == "unloaded"
    assert state["embed"]["by"] == state["rerank"]["by"] == "owner"


@pytest.mark.parametrize("on,off", [("embed", "rerank"), ("rerank", "embed")])
def test_a_shared_container_is_never_unloaded_while_idle_when_one_of_its_names_says_never(single_host, on, off):
    """F4: with a timeout set for one name only, the other name's calls took no lock and marked no use, and the pass
    unloaded the container during such a call and recorded the other name as unloaded by the idle policy, although
    the owner had set it to never unload. Now: the most conservative of the names decides (any "off": never)."""
    fake = single_host
    p = fake.make()
    p.configure({on: 15 * MIN})
    assert p.timeouts()[off] is None
    with idle.using_now(fake.path, off, purpose="memory lookup") as state:
        assert state is None
        assert p.last_used(off) is not None               # the off name's call is marked: the policy covers it
        fake.now += 3600
        out = p.tick()["decisions"]
    assert out[on]["action"] == "none" and off in out[on]["why"] and "never" in out[on]["why"]
    fake.now += 24 * 3600
    assert p.tick()["decisions"][on]["action"] == "none"
    assert SHARED in fake.running and not p.unloaded_by_policy(off) and not p.unloaded_by_policy(on)
    shown = p.status()["services"]
    assert shown[on]["shared_with"] == [off] and shown[on]["effective_timeout_s"] is None


def test_a_shared_container_follows_the_longest_timeout_of_its_names(single_host):
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 30 * MIN})
    with idle.using_now(fake.path, "embed", purpose="memory lookup"):
        pass
    fake.now += 20 * MIN                                  # past embed's 15 min, inside rerank's 30
    out = p.tick()["decisions"]
    assert all(out[n]["action"] == "none" for n in MEMORY) and SHARED in fake.running
    fake.now += 11 * MIN
    out = p.tick()["decisions"]
    assert [out[n]["action"] for n in MEMORY].count("unload") == 1 and SHARED not in fake.running
    assert p.unloaded_by_policy("embed") and p.unloaded_by_policy("rerank")
    assert p.status()["services"]["embed"]["effective_timeout_s"] == 30 * MIN


def test_a_running_shared_container_counts_as_loaded_for_both_names(single_host):
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    fake.now += 16 * MIN
    p.tick()
    assert p.unloaded_by_policy("embed") and p.unloaded_by_policy("rerank")
    fake.running[SHARED] = fake.now                       # started by other means (run.sh start, the unit)
    assert p.load("rerank", reason="memory lookup", by="on demand") == {"action": "none", "why": "already running"}
    assert not p.unloaded_by_policy("embed") and not p.unloaded_by_policy("rerank")


def test_loads_of_both_names_at_once_start_the_shared_container_once(single_host):
    """Concurrent reloads of embed and rerank took two different load locks; one lock per container now."""
    fake = single_host
    p = fake.make()
    p.configure({"embed": 15 * MIN, "rerank": 15 * MIN})
    assert p.unload("embed", reason="test")["action"] == "unload"
    del fake.calls[:]
    started = threading.Event()
    release = threading.Event()

    def runner(argv, timeout):                        # the deploy's wait blocks until the test lets it go
        if "wait" in [str(a) for a in argv]:
            started.set()
            assert release.wait(10)
        return fake(argv, timeout)

    def make():
        q = fake.make()
        q.runner = runner
        return q
    first, _, one = _in_thread(lambda: make().load("embed", reason="lookup", by="on demand"))
    assert started.wait(10)
    second, second_done, two = _in_thread(lambda: make().load("rerank", reason="lookup", by="on demand"))
    assert not second_done.wait(0.5), "the second load did not wait for the first one's start"
    release.set()
    one.join(10)
    two.join(10)
    assert first["result"]["action"] == "load" and second["result"] == {"action": "none", "why": "already running"}
    assert sum("start" in c for c in fake.calls) == 1
