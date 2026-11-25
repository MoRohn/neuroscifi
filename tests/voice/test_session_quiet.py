"""An open voice session that nobody uses stops holding off idle unloading (ENGINEERING_PLAN.md §6 item 13).

Before: a Pocket voice session counted as a session for as long as the phone kept it open, so a paired device in a
pocket held off every unload for ever. Now a session counts while it is used: audio, a typed turn, a capture or
mode change, a reply fetched, an interruption, a confirmation, within `voice` (default 30 min) of the idle
policy's settings; `--set voice=off` is the rule of 0.7.0."""
import json
import time

import pytest

from neuroscifi.resources import idle, policy, shared
from neuroscifi.voice import state as vstate
from neuroscifi.voice.beacon import SharedAudioBeacon

from .conftest import pocket_principal

QUIET = idle.VOICE_QUIET


def age(sid, seconds):
    """The session's last use moved `seconds` into the past (the hub's own clock is the wall clock)."""
    with vstate._LOCK:
        vstate._SESSIONS[sid]["_used_at"] -= seconds


@pytest.fixture(autouse=True)
def clean():
    vstate.reset()
    yield
    vstate.reset()


def test_a_session_is_used_when_it_starts_and_when_audio_is_reported_not_when_nothing_plays():
    vstate.update("s1", owner=False, mode="voice_text", session_active=True)
    first = vstate.last_used()
    assert first is not None and abs(first - time.time()) < 2
    age("s1", 3600)
    vstate.update("s1", owner=False, mic_capturing=False, speaking=False, user_speaking=False, output_muted=True)
    assert time.time() - vstate.last_used() > 3500               # a report that nothing plays is not a use
    vstate.update("s1", owner=False, speaking=True)
    assert time.time() - vstate.last_used() < 2                   # audio reported: in use now
    vstate.update("s1", owner=False, speaking=False)
    age("s1", 3600)
    assert time.time() - vstate.last_used() > 3500
    vstate.touch("s1")
    assert time.time() - vstate.last_used() < 2
    vstate.end("s1")
    assert vstate.last_used() is None


def test_audio_still_flowing_is_a_use_whatever_the_last_call(monkeypatch):
    vstate.update("s1", owner=False, session_active=True, mic_capturing=True)
    age("s1", 3600)
    assert time.time() - vstate.last_used() < 2                   # the mic is open and the client reports it


def test_this_processs_session_counts_only_while_used_when_a_bound_is_set():
    vstate.update("s1", owner=False, session_active=True)
    now = time.time()
    assert policy.voice_session_active(None, now=now, quiet=QUIET)
    age("s1", QUIET + 60)
    assert not policy.voice_session_active(None, now=now, quiet=QUIET)
    assert policy.voice_session_active(None, now=now)             # no bound: the rule of 0.7.0
    assert policy.voice_session_active(None, now=now, quiet=QUIET + 120)


def test_the_beacon_carries_the_last_use_to_the_idle_pass_of_another_process(tmp_path):
    db = tmp_path / "individual.sqlite"
    beacon = SharedAudioBeacon(db, pid=11)
    beacon.write(False, True, time.time() - QUIET - 60)          # a quiet Pocket session in the bridge's process
    data = shared.read_json(policy._beacon_path(db))
    assert data["session"] is True and data["writers"]["11"]["used_at"] == data["used_at"]
    assert not policy.voice_session_active(db, quiet=QUIET)
    assert policy.voice_session_active(db)                       # bound off: open is enough
    SharedAudioBeacon(db, pid=os_pid()).write(False, True, time.time() - 60)     # the console's session, used now
    assert policy.voice_session_active(db, quiet=QUIET)          # the newest use of either counts


def os_pid():
    import os
    return os.getpid()


def test_a_beacon_without_a_last_use_an_older_writer_counts_as_used(tmp_path):
    db = tmp_path / "individual.sqlite"
    path = policy._beacon_path(db)
    shared.ensure_folder(path.parent)
    path.write_text(json.dumps({"active": False, "session": True, "at": time.time(), "pid": 1}))
    assert policy.voice_session_active(db, quiet=QUIET)
    path.write_text(json.dumps({"active": False, "session": True, "at": time.time(), "pid": 1, "used_at": True}))
    assert policy.voice_session_active(db, quiet=QUIET)
    # A shared beacon in which another live process's open session says nothing of its use (an older writer): the
    # merged record says nothing either, and the session counts. pid 1 is alive, so its entry is kept.
    path.write_text(json.dumps({"active": False, "session": True, "at": time.time(), "pid": 1,
                                "writers": {"1": {"active": False, "session": True, "at": time.time()}}}))
    SharedAudioBeacon(db, pid=os_pid()).write(False, True, time.time() - QUIET - 60)
    merged = shared.read_json(path)
    assert set(merged["writers"]) == {"1", str(os_pid())} and "used_at" not in merged
    assert policy.voice_session_active(db, quiet=QUIET)


@pytest.fixture
def pass_(tmp_path, monkeypatch):
    from tests.test_resources_idle import FakeHost
    host = FakeHost()
    host.metrics = {"vllm": {"in_flight": 0.0, "served": 7.0}}
    host.level = "ok"
    db = tmp_path / "individual.sqlite"
    monkeypatch.setattr(idle, "_run", host)

    def make():
        return idle.Idle(db, runner=host, clock=time.time, monotonic=time.monotonic,
                         metrics=lambda url: {"in_flight": 0.0, "served": 7.0}, ready=lambda url: True,
                         sleep=lambda s: None, sample=lambda: {"level": "ok", "reasons": []})
    host.make, host.db = make, db
    host.running = {"neuroscifi-vllm": time.time() - 7200}
    return host


def test_the_idle_pass_unloads_past_a_session_left_unused_and_says_so(pass_):
    p = pass_.make()
    p.configure({"vllm": 900})
    SharedAudioBeacon(pass_.db, pid=1).write(False, True, time.time() - QUIET - 60)
    out = p.tick()
    assert "a voice session is open" not in out["blockers"]
    assert out["decisions"]["vllm"]["action"] == "unload", out
    assert any("unused for more than 30 min" in n and "voice=off" in n for n in out["notes"])


def test_a_session_used_lately_or_the_bound_off_still_holds_it(pass_):
    p = pass_.make()
    p.configure({"vllm": 900})
    SharedAudioBeacon(pass_.db, pid=1).write(False, True, time.time() - 300)
    out = p.tick()
    assert "a voice session is open" in out["blockers"] and out["decisions"]["vllm"]["action"] == "none"
    SharedAudioBeacon(pass_.db, pid=1).write(False, True, time.time() - QUIET - 60)
    p.configure({idle.VOICE: None})
    assert p.voice_quiet() is None and p.status()["voice_quiet_s"] is None
    out = p.tick()
    assert "a voice session is open" in out["blockers"] and out["decisions"]["vllm"]["action"] == "none"
    p.configure({idle.VOICE: 7200})
    assert p.voice_quiet() == 7200 and "a voice session is open" in p.tick()["blockers"]
    assert any(e["service"] == "voice" and e["action"] == "configure" for e in p.events())


@pytest.mark.parametrize("value", ["soon", -5, 0, True, [1], {"a": 1}, float("inf")])
def test_a_malformed_bound_reads_as_the_default(pass_, value):
    p = pass_.make()
    p._write({"timeouts": {}, "services": {}, "voice_quiet_s": value})
    assert p.voice_quiet() == QUIET


def test_the_owner_sets_it_from_the_command_line(pass_, capsys):
    from neuroscifi.cli import main
    assert main(["resources", "idle", "--db", str(pass_.db), "--set", "voice=45m"]) == 0
    assert json.loads(capsys.readouterr().out)["voice_quiet_s"] == 2700
    assert main(["resources", "idle", "--db", str(pass_.db), "--set", "voice=off"]) == 0
    assert json.loads(capsys.readouterr().out)["voice_quiet_s"] is None
    with pytest.raises(SystemExit, match="under one minute"):
        main(["resources", "idle", "--db", str(pass_.db), "--set", "voice=30s"])


def test_the_hub_counts_uses_not_polls(hub):
    pocket = pocket_principal()
    sid = hub.start(pocket, "pocket:" + "d" * 16, "Phone", "text_only")["session"]
    age(sid, 3600)
    hub.events(sid, pocket, 0, wait=0.05)                        # polling for events
    hub.client_status(sid, pocket, speaking=False, user_speaking=False)
    assert time.time() - vstate.last_used() > 3500
    hub.text(sid, pocket, "hello")                               # a typed turn
    assert time.time() - vstate.last_used() < 5
    age(sid, 3600)
    hub.set_mode(sid, pocket, "text_only")
    assert time.time() - vstate.last_used() < 5
    hub.stop(sid, pocket)
    assert vstate.last_used() is None
