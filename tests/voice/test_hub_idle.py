"""A voice session that starts after the idle policy unloaded the speech services loads them again and tells the
client while they start; a session's end counts as their last use (resources/idle.py)."""
import subprocess

from neuroscifi.resources import idle

from .conftest import OWNER, wait_for


def test_a_voice_session_reloads_idle_unloaded_speech_services_and_says_so(hub, monkeypatch):
    running = set()
    calls = []

    def runner(argv, timeout):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in running else 1, "true 2026-11-21T10:00:00Z")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 3, "")
        if "start" in argv:
            running.add({"asr": "neuroscifi-voice-asr", "tts": "neuroscifi-voice-tts"}[argv[-1]])
        return subprocess.CompletedProcess(argv, 0, "")
    monkeypatch.setattr(idle, "_run", runner)
    policy = idle.Idle(hub.db_path)
    policy.configure({"asr": 900, "tts": 900})
    for name in ("asr", "tts"):
        policy.unload(name, reason="idle 16 min (timeout 15 min)", by="idle policy")
    sid = hub.start(OWNER, "console:dev1", "Console", "voice_text")["session"]
    def told():
        found = sorted(e["text"] for e in hub.events(sid, OWNER, 0, wait=0.1)["events"] if e["type"] == "notice")
        return found if len(found) == 2 else None
    notices = wait_for(told)
    assert notices == ["Starting speech recognition, about 18 s.", "Starting speech synthesis, about 6 s."]
    wait_for(lambda: not policy.unloaded_by_policy("asr") and not policy.unloaded_by_policy("tts"))
    assert running == {"neuroscifi-voice-asr", "neuroscifi-voice-tts"}
    hub.stop(sid, OWNER)
    assert policy.last_used("asr") is not None and policy.last_used("tts") is not None
