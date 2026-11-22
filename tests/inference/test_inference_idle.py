"""The gateway reloads a local model that the idle policy unloaded before it sends a request (resources/idle.py):
the user is told how long it takes, the reload is recorded, and a gateway without an instance never touches it."""
import subprocess

from fake_openai import make_gateway

from neuroscifi.resources import idle


def test_a_chat_reloads_an_idle_unloaded_model_first_and_tells_the_user(fake, key_file, tmp_path, monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append([str(a) for a in argv])
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if fake.requests or any("start" in c for c in calls) else 1,
                                               "true 2026-11-21T10:00:00Z")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 3, "")
        return subprocess.CompletedProcess(argv, 0, "")
    monkeypatch.setattr(idle, "_run", runner)
    db_path = tmp_path / "x.sqlite"
    policy = idle.Idle(db_path)
    policy.configure({"vllm": 1800})
    policy.unload("vllm", reason="idle 31 min (timeout 30 min)", by="idle policy")
    gw = make_gateway(fake, key_file, tmp_path)
    gw.db_path = db_path
    down = make_gateway(type("Gone", (), {"url": "http://127.0.0.1:9/v1"})(), key_file, tmp_path)
    down.db_path = db_path
    health = down.health()                     # nothing listens: unloaded by the policy, not an outage
    assert health["state"] == "unavailable" and health["idle_unloaded"] is True
    assert health["reason"] == "the local model is unloaded while idle; the next request starts it again (about 33 s)"
    fake.replies.append({"content": "ready"})
    result = gw.chat([{"role": "user", "content": "hello"}], purpose="test")
    assert result.text == "ready"
    assert gw.notices == ["Starting the local model, about 33 s"]
    started = [c for c in calls if c[-1] in ("start", "1200")]
    assert [c[2:] for c in started] == [["start"], ["wait", "1200"]]      # run.sh start, then run.sh wait
    assert policy.events()[-1]["action"] == "load" and policy.events()[-1]["reason"] == "on demand: test"
    gw.chat([{"role": "user", "content": "again"}], purpose="test")        # loaded now: nothing more to do
    assert gw.notices == ["Starting the local model, about 33 s"] and len(started) == 2


def test_a_gateway_without_an_instance_never_consults_the_idle_policy(gateway, fake, monkeypatch):
    monkeypatch.setattr(idle, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no docker call")))
    fake.replies.append({"content": "ok"})
    assert gateway.db_path is None and gateway.chat([{"role": "user", "content": "x"}], purpose="t").text == "ok"
