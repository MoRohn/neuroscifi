"""The gateway reloads a local model that the idle policy unloaded before it sends a request (resources/idle.py):
the user is told how long it takes, the reload is recorded, and a gateway without an instance never touches it. A
reload never holds a call past its deadline (it finishes in the background), and a reload into too little free
memory is refused and recorded."""
import subprocess
import time

from fake_openai import make_gateway
import pytest

from neuroscifi.inference.gateway import InferenceError
from neuroscifi.resources import idle

GIB = 1024 ** 3


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


def _slow_host(calls, wait_seconds):
    """A docker host whose vLLM is unloaded and whose `run.sh wait` takes `wait_seconds` of real time (a container
    that starts slowly, or crash-loops until the deploy's wait gives up)."""
    def runner(argv, timeout):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            started = any(c[-1] == "start" for c in calls)
            return subprocess.CompletedProcess(argv, 0 if started else 1, "true 2026-11-21T10:00:00Z")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 3, "")
        if "wait" in argv:
            time.sleep(wait_seconds)
        return subprocess.CompletedProcess(argv, 0, "")
    return runner


def _unloaded_instance(tmp_path):
    db_path = tmp_path / "x.sqlite"
    policy = idle.Idle(db_path)
    policy.configure({"vllm": 1800})
    policy.unload("vllm", reason="idle 31 min (timeout 30 min)", by="idle policy")
    return db_path, policy


def test_a_reload_never_holds_a_call_past_its_deadline_and_the_model_finishes_loading_in_the_background(
        fake, key_file, tmp_path, monkeypatch):
    # Before the fix, Gateway._loaded waited for the whole reload (run.sh wait, up to 1200 s) before the call's
    # deadline existed: this 0.5 s call took the 3 s of the slow wait.
    calls = []
    monkeypatch.setattr(idle, "_run", _slow_host(calls, 3.0))
    db_path, policy = _unloaded_instance(tmp_path)
    gw = make_gateway(fake, key_file, tmp_path)
    gw.db_path = db_path
    began = time.monotonic()
    with pytest.raises(InferenceError) as raised:
        gw.chat([{"role": "user", "content": "hello"}], purpose="test", timeout=0.5)
    assert time.monotonic() - began < 2.0
    assert raised.value.code == "unavailable" and "Starting the local model, about 33 s" in str(raised.value)
    assert gw.notices == ["Starting the local model, about 33 s"]
    assert not fake.requests                                   # nothing was sent to a server that is not up
    for _ in range(100):                                       # the load keeps going and is recorded when done
        if policy.events()[-1]["action"] == "load":
            break
        time.sleep(0.1)
    assert policy.events()[-1]["action"] == "load" and policy.events()[-1]["ok"] is True
    fake.replies.append({"content": "ready"})
    assert gw.chat([{"role": "user", "content": "again"}], purpose="test", timeout=5).text == "ready"
    assert [c[-1] for c in calls if c[-1] in ("start", "1200")] == ["start", "1200"]   # started once
    assert gw.notices == ["Starting the local model, about 33 s"]


def test_a_reload_without_enough_free_memory_is_refused_and_the_call_is_unavailable_at_once(
        fake, key_file, tmp_path, monkeypatch):
    from neuroscifi.resources import monitor
    calls = []
    monkeypatch.setattr(idle, "_run", _slow_host(calls, 0.0))
    db_path, policy = _unloaded_instance(tmp_path)
    monkeypatch.setattr(monitor, "memory", lambda *a, **k: {"total_bytes": 128 * GIB, "available_bytes": 30 * GIB,
                                                            "swap_total_bytes": 0, "swap_free_bytes": 0, "psi": None})
    gw = make_gateway(fake, key_file, tmp_path)
    gw.db_path = db_path
    with pytest.raises(InferenceError) as raised:
        gw.chat([{"role": "user", "content": "hello"}], purpose="test", timeout=5)
    assert raised.value.code == "unavailable" and "not enough memory to load the local model" in str(raised.value)
    assert not [c for c in calls if c[-1] == "start"] and not fake.requests
    event = policy.events()[-1]
    assert event["action"] == "load" and event["ok"] is False and event["refused"] is True
    assert event["need_bytes"] == int(44.1 * GIB) and event["available_bytes"] == 30 * GIB
