"""Pocket voice counts for the resource policy (RD-46): the Pocket bridge publishes the audio beacon like the
console, and two hubs writing the one beacon file never clobber each other's live state."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

from neuroscifi.resources import policy
from neuroscifi.voice import http as vhttp, state as vstate


def _hidden(monkeypatch):
    monkeypatch.setattr(policy, "local_audio", lambda: False)
    monkeypatch.setattr(policy, "local_session", lambda: False)


def test_a_pocket_voice_session_defers_background_research_in_the_worker_process(instance, monkeypatch):
    """Defect 4: only the console started an AudioBeacon, so a Pocket voice session (the Pocket bridge hosts its own
    hub) was invisible to the worker: background research started and the idle unloader could unload speech
    services mid-session. The Pocket bridge's hub now publishes the beacon too."""
    pocket = SimpleNamespace()                                    # the Pocket server: it has no beacon of its own
    vhttp.hub_for(pocket, str(instance))
    try:
        assert pocket.audio_beacon is not None
        vstate.update("p1", owner=False, mode="voice_text", session_active=True, mic_capturing=True)
        pocket.audio_beacon.tick()
        vstate.reset()
        _hidden(monkeypatch)
        assert policy.audio_active(str(instance)) is True
        assert policy.voice_session_active(str(instance)) is True
        assert policy.background_gate(str(instance), sample={"level": "normal", "reasons": []})["allowed"] is False
    finally:
        pocket.audio_beacon.close()
        vstate.reset()


def test_the_console_keeps_its_own_beacon_and_gets_no_second_one(instance):
    console = SimpleNamespace(audio_beacon="the console's")
    vhttp.hub_for(console, str(instance))
    assert console.audio_beacon == "the console's"


def _dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_two_hubs_writing_one_beacon_never_clobber_each_others_audio(tmp_path, monkeypatch):
    from neuroscifi.voice.beacon import SharedAudioBeacon
    db = tmp_path / "i.sqlite"
    console = SharedAudioBeacon(db, pid=os.getpid())
    pocket = SharedAudioBeacon(db, pid=os.getppid())             # another live process
    _hidden(monkeypatch)
    pocket.write(True, True)                                      # a Pocket voice session is capturing
    console.write(False, False)                                   # the console's session ended
    assert policy.audio_active(db) is True and policy.voice_session_active(db) is True
    console.write(True, True)
    pocket.write(False, False)
    assert policy.audio_active(db) is True                       # the console still has audio
    console.write(False, True)
    assert policy.audio_active(db) is False and policy.voice_session_active(db) is True
    console.write(False, False)
    assert policy.audio_active(db) is False and policy.voice_session_active(db) is False

    crashed = SharedAudioBeacon(db, pid=_dead_pid())               # a bridge that died while capturing
    crashed.write(True, True)
    console.write(False, False)
    assert policy.audio_active(db) is False                      # its entry goes with it
    written = json.loads((tmp_path / "i.sqlite.resources" / "audio.json").read_text())
    assert set(written["writers"]) == {str(os.getpid()), str(os.getppid())}
