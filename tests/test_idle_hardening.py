"""The idle policy against what the service account can put in front of it (review round of the open-items round:
runtime F5, F6; security L4, L5). In a two-account install the idle pass and the wake command run as an account that
can stop services, and they read files the service account writes. Each test fails on 76f32d2."""
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.resources import idle, policy, shared

ROOT = Path(__file__).resolve().parents[1]


def _within(seconds, fn, *args):
    """fn(*args) in a thread; its result, or a failure if it has not returned within `seconds` (a FIFO hangs)."""
    box = {}

    def run():
        try:
            box["value"] = fn(*args)
        except BaseException as error:         # handed back to the test
            box["error"] = error
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"{getattr(fn, '__name__', fn)} did not return within {seconds} s"
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.fixture
def instance(tmp_path):
    path = tmp_path / "i.sqlite"
    store.create(path, time.time()).close()
    return path


# ---- F5: the in-use lock does not hold a call past its deadline ----------------------------------------------------

def test_a_call_with_a_deadline_does_not_wait_past_it_for_an_unload_in_progress(instance):
    p = idle.Idle(instance)
    p.configure({"vllm": 1800.0})
    lock = os.open(p._file("idle-inuse-vllm.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)                          # what the pass holds while it stops the service
    try:
        started = time.monotonic()
        with idle.using(instance, "vllm", purpose="chat", idle=p, timeout=1.0) as state:
            took = time.monotonic() - started
        assert state["action"] == "unloading" and "being unloaded" in state["why"]
        assert 0.9 <= took < 1.5, took                         # it was 4.0 s with a 4 s unload on 76f32d2
    finally:
        os.close(lock)
    with idle.using(instance, "vllm", purpose="chat", idle=p, timeout=1.0) as state:
        assert state is None                                   # free again: the call goes ahead


def test_the_gateway_ends_such_a_call_unavailable_in_time_and_claims_no_reload(instance, tmp_path):
    sys.path.insert(0, str(ROOT / "tests" / "inference"))
    from fake_openai import FakeOpenAI, make_gateway
    from neuroscifi.inference.gateway import InferenceError
    p = idle.Idle(instance)
    p.configure({"vllm": 1800.0})
    server = FakeOpenAI().start()
    lock = os.open(p._file("idle-inuse-vllm.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        key = tmp_path / "api_key"
        key.write_text("test-key\n")
        key.chmod(0o600)
        gw = make_gateway(server, key, tmp_path)
        gw.db_path = instance
        gw._model = "fake-model"
        started = time.monotonic()
        with pytest.raises(InferenceError) as err:
            gw.chat([{"role": "user", "content": "hello"}], purpose="t", timeout=1.0)
        assert time.monotonic() - started < 1.5
        assert err.value.code == "unavailable" and "being unloaded" in str(err.value)
        assert "started again" not in str(err.value) and not server.requests
    finally:
        os.close(lock)
        server.stop()


# ---- F6: the wake command lingers on every way out -----------------------------------------------------------------

def test_a_malformed_state_file_is_reported_and_read_as_empty(instance):
    p = idle.Idle(instance)
    p.configure({"vllm": 1800.0})
    Path(p._file("idle.json")).write_text(json.dumps({"timeouts": {"vllm": "soon", "asr": 60.0, "tts": True},
                                                      "services": {"vllm": "x", "asr": {"state": "loaded"}}}))
    data = p.read()
    assert data["timeouts"] == {"vllm": None, "asr": 60.0, "tts": None}
    assert data["services"] == {"asr": {"state": "loaded"}}
    assert len(p.problems) == 3 and p.status([])["state_problems"] == p.problems
    for raw in ("[1, 2]", '{"timeouts": [], "services": 7}', "not json"):
        Path(p._file("idle.json")).write_text(raw)
        assert p.read() == {"timeouts": {}, "services": {}} and p.problems
    assert p.unloaded_by_policy("vllm") is False and p.tick()["enabled"] is False


@pytest.mark.parametrize("break_it", ["state", "request", "pass"])
def test_the_wake_command_lingers_whatever_goes_wrong(instance, tmp_path, monkeypatch, break_it):
    """On 76f32d2 a malformed idle.json (which the service account may write) made the command raise at once with
    the request already removed, so the starts were again counted by whoever writes the folder."""
    folder = tmp_path / "wake"
    folder.mkdir()
    (folder / "vllm.wake").write_text("")
    p = idle.Idle(instance)
    if break_it == "state":
        Path(p._file("idle.json")).parent.mkdir(exist_ok=True)
        Path(p._file("idle.json")).write_text(json.dumps({"services": {"vllm": "x"}}))
    elif break_it == "request":
        monkeypatch.setattr(p, "ensure_loaded", lambda *a, **k: 1 / 0)
        monkeypatch.setattr(idle, "_retry_not_due", lambda idle_, name: None)
    else:
        monkeypatch.setattr(idle, "_serve_once", lambda idle_, folder_: 1 / 0)
    naps = []
    served = idle.serve_wake_requests(instance, folder, idle=p, linger=5.0, sleep=naps.append)
    assert sum(naps) == 5.0, served                            # it stayed its whole linger
    if break_it == "request":
        assert served[0]["service"] == "vllm" and served[0]["action"] == "failed" and "ZeroDivision" in served[0]["why"]
    if break_it == "pass":
        assert served and served[0]["action"] == "failed"
    assert not list(folder.glob("*.wake")) or break_it == "pass"


# ---- L4: no link, no FIFO, no endless file -------------------------------------------------------------------------

def _plant(path, what):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.unlink()
    if what == "fifo":
        os.mkfifo(path)
    elif what == "zero":
        path.symlink_to("/dev/zero")
    elif what == "link":
        target = path.parent / "elsewhere.json"
        target.write_text('{"timeouts": {"vllm": 60.0}, "services": {}}')
        path.symlink_to(target)
    elif what == "huge":
        with open(path, "wb") as fh:
            fh.truncate(shared.READ_LIMIT + 1)


@pytest.mark.parametrize("what", ["fifo", "zero", "link", "huge"])
def test_the_policy_state_is_never_read_through_a_link_a_fifo_or_without_end(instance, what):
    p = idle.Idle(instance)
    _plant(Path(p._file("idle.json")), what)
    data = _within(5, p.read)
    assert data == {"timeouts": {}, "services": {}} and p.problems and "treated as empty" in p.problems[0]


@pytest.mark.parametrize("what", ["fifo", "zero", "link"])
def test_the_events_and_the_audio_beacon_are_never_read_through_a_link_or_a_fifo(instance, what):
    p = idle.Idle(instance)
    _plant(Path(p._file("idle-events.jsonl")), what)
    assert _within(5, p.events) == []
    _plant(Path(p._file("audio.json")), what)
    assert _within(5, policy.voice_session_active, instance) is False
    with pytest.raises(OSError):                               # appending an event refuses it too, at once
        _within(5, p._event, "vllm", "configure", "test")


def test_a_huge_event_log_is_read_from_its_tail(instance):
    p = idle.Idle(instance)
    path = Path(p._file("idle-events.jsonl"))
    path.parent.mkdir(exist_ok=True)
    line = json.dumps({"service": "vllm", "action": "load", "ok": True, "seconds": 3.0}) + "\n"
    with open(path, "w") as fh:
        fh.write("x" * (shared.READ_LIMIT + 10) + "\n")       # more than is ever read
        fh.write(line * 3)
    assert p.events(limit=0) == [json.loads(line)] * 3


def test_a_file_the_other_account_swaps_for_a_link_is_not_touched_through_it(tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("x")
    os.utime(victim, (1.0, 1.0))
    (folder / "used").symlink_to(victim)
    with pytest.raises(OSError):
        shared.touch(folder / "used", 5.0)
    assert victim.stat().st_mtime == 1.0


# ---- L5: the database is the service account's -------------------------------------------------------------------

def test_running_tasks_reads_only_a_plain_database_whose_web_tasks_is_a_table(tmp_path):
    view = tmp_path / "view.sqlite"
    with sqlite3.connect(view) as db:
        db.execute("CREATE TABLE t(x)")
        db.execute("CREATE VIEW web_tasks AS SELECT 'reading' AS status, 'w' AS lease_holder, 9e99 AS lease_expires_at "
                   "FROM t")
    assert idle.Idle(view).running_tasks() is None             # not a table: it cannot tell, so nothing unloads
    real = tmp_path / "real.sqlite"
    store.create(real, time.time()).close()
    assert idle.Idle(real).running_tasks() == 0
    for what in ("fifo", "link"):
        path = tmp_path / f"{what}.sqlite"
        if what == "fifo":
            os.mkfifo(path)
        else:
            path.symlink_to(real)
        assert _within(5, idle.Idle(path).running_tasks) is None
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    assert idle.Idle(empty).running_tasks() == 0


def test_running_tasks_trusts_nothing_in_the_schema(tmp_path, monkeypatch):
    seen = []
    real = sqlite3.connect

    def connect(*args, **kwargs):
        con = real(*args, **kwargs)
        con.set_trace_callback(seen.append)
        return con
    monkeypatch.setattr(sqlite3, "connect", connect)
    path = tmp_path / "i.sqlite"
    store.create(path, time.time()).close()
    assert idle.Idle(path).running_tasks() == 0
    assert "PRAGMA trusted_schema=OFF" in seen and "PRAGMA query_only=ON" in seen
