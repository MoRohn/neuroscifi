"""Opt-in idle unloading (RD-46 policy (c), resources/idle.py) against a fake docker / systemctl / run.sh host: off by
default, the unload conditions (timeout, in-flight calls and requests, voice sessions, running tasks), the shorter
timeout at critical pressure, reload on demand with what the user is told and the measured reload time, the owner's
manual unload and load, the systemd shape, the event log in `nsf resources`, and Presence while a service starts."""
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


def _iso(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


class FakeHost:
    """docker inspect / run.sh start|stop|wait / systemctl / `nsf web searxng` as one fake host with a clock. A wait
    advances the clock by the service's reload time, so the measured reload is known in advance."""

    RELOAD = {"neuroscifi-vllm": 41.0, "neuroscifi-voice-asr": 18.0, "neuroscifi-voice-tts": 5.0,
              "neuroscifi-searxng": 3.0}

    def __init__(self):
        self.now = T0
        self.running = {c: T0 - 6 * 3600 for c in self.RELOAD}       # container -> started at
        self.calls = []
        self.units = set()                                             # active systemd units
        self.fail = set()

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
        if argv[0] == "systemctl":
            unit = argv[-1]
            if argv[1] == "is-active":
                return subprocess.CompletedProcess(argv, 0 if unit in self.units else 3, "")
            name = idle.SERVICES[{"neuroscifi-vllm.service": "vllm", "neuroscifi-voice-asr.service": "asr",
                                  "neuroscifi-voice-tts.service": "tts"}[unit]].container
            self._act(argv[2], name)
            return ok
        joined = " ".join(argv)
        container = next(svc.container for svc in idle.SERVICES.values()
                         if (svc.name == "vllm" and "deploy/vllm/run.sh" in joined) or
                         (svc.name in ("asr", "tts") and "deploy/voice/run.sh" in joined and argv[-1] == svc.name) or
                         (svc.name in ("asr", "tts") and "wait" in argv and argv[-2] == svc.name) or
                         (svc.name == "searxng" and "searxng" in argv))
        verb = next(v for v in ("start", "stop", "wait") if v in argv)
        if container in self.fail and verb != "stop":
            return subprocess.CompletedProcess(argv, 1, "run.sh: container exited")
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
        name = "vllm" if ":8811/" in url else "asr"
        value = host.metrics.get(name)
        return dict(value) if value is not None else None

    def make():
        return idle.Idle(path, runner=host, clock=host.clock, monotonic=host.clock, metrics=metrics,
                         sample=lambda: {"level": host.level, "reasons": ["memory available 5.0 GiB of 128.0 GiB is "
                                                                          "below the critical threshold 7.7 GiB"]
                                         if host.level == "critical" else []})
    host.make, host.path, host.db = make, path, db
    # Anything that builds its own Idle (using, wake, the gateway, the hub) talks to the fake host too.
    monkeypatch.setattr(idle, "_run", host)
    monkeypatch.setattr(idle, "_metrics", metrics)
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
