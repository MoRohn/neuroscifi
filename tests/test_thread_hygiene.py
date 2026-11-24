"""Threads end with what started them (open-items round, lane D). A voice hub had no way to end its transcript
writer or the threads that follow a research task; a bridge run in a thread could not be stopped, and its control
socket's thread stayed in accept() after the socket was closed. In a service these ended with the process. In the
test process they gathered (35 by the time one test forked six children), which is what the session check in
tests/conftest.py now refuses."""
import threading
import time

import thread_check


def _hub_threads():
    return [t for t in threading.enumerate() if t.name in ("nsf-voice-writer", "nsf-voice-follow")]


def test_a_voice_hubs_writer_ends_when_the_hub_closes_and_a_hub_used_again_starts_a_new_one(tmp_path):
    from neuroscifi.instance import store
    from neuroscifi.voice.hub import VoiceHub
    path = tmp_path / "i.sqlite"
    store.create(path, time.time()).close()
    before = set(_hub_threads())
    hub = VoiceHub(path)
    done = []
    assert hub._write_later(lambda: done.append(1))
    writer = hub._writer
    assert writer.is_alive()
    hub.close()
    assert done == [1] and not writer.is_alive() and set(_hub_threads()) == before      # the write was finished first
    assert hub._write_later(lambda: done.append(2))                # used again: a new writer
    hub.drain()
    assert done == [1, 2] and hub._writer is not writer
    hub.close()
    hub.close()                                                    # twice is harmless
    assert set(_hub_threads()) == before


def test_a_follower_of_a_task_that_never_ends_stops_when_the_hub_closes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from neuroscifi.instance import store
    from neuroscifi.voice import hub as hub_mod
    from neuroscifi.webcortex import orchestrator
    path = tmp_path / "i.sqlite"
    store.create(path, time.time()).close()
    hub = hub_mod.VoiceHub(path)
    monkeypatch.setattr(orchestrator, "get", lambda db, task_id, family=None: {"status": "reading", "stage": "reading"})
    session = SimpleNamespace(principal=SimpleNamespace(family=None, owner=True), active=False, mode="text",
                              turns={}, events=[], seq=0, cond=threading.Condition(), id="s")
    thread = threading.Thread(target=hub._follow, args=(session, "task-1"), daemon=True, name="nsf-voice-follow")
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive()                                       # it would look for up to FOLLOW_MAX (3 hours)
    hub.close()
    thread.join(5)
    assert not thread.is_alive()


def test_a_server_that_closes_closes_its_voice_hub(tmp_path):
    from neuroscifi.console.server import ConsoleServer
    from neuroscifi.instance import store
    from neuroscifi.voice.http import hub_for
    path = tmp_path / "i.sqlite"
    store.create(path, time.time()).close()
    before = set(_hub_threads())
    server = ConsoleServer(path, 0)
    hub = hub_for(server, path)
    assert hub._write_later(lambda: None)
    hub.drain()
    assert set(_hub_threads()) - before
    server.server_close()
    assert set(_hub_threads()) == before and hub._closed.is_set()


def test_a_bridge_run_in_a_thread_can_be_stopped_and_leaves_no_thread_behind(tmp_path, monkeypatch):
    """`started(server)` hands the server to whoever embeds the bridge; shutdown() then runs main()'s own cleanup:
    the control socket's thread (it stayed in accept() when the socket was only closed), the upkeep thread and the
    listener all end."""
    from neuroscifi.pocket import server as bridge
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    before = set(threading.enumerate())
    servers = []
    thread = threading.Thread(target=bridge.main, args=(["--db", str(tmp_path / "p.sqlite"), "--port", "0"],),
                              kwargs={"started": servers.append}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not servers and time.monotonic() < deadline:
        time.sleep(0.02)
    assert servers, "the bridge did not start"
    assert bridge.invitation_status(tmp_path / "p.sqlite", "0" * 16)       # the control socket answers
    servers[0].shutdown()
    thread.join(15)
    assert not thread.is_alive()
    deadline = time.monotonic() + 5
    while set(threading.enumerate()) - before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not set(threading.enumerate()) - before, [t.name for t in set(threading.enumerate()) - before]


def test_the_session_check_names_a_thread_that_was_left_and_passes_once_it_is_gone():
    """The check itself: it must see a thread of ours by name and by where its target is defined, ignore others,
    and say which test file started it."""
    from neuroscifi.resources import policy
    stop = threading.Event()
    before = set(threading.enumerate())
    named = threading.Thread(target=stop.wait, name="nsf-left-behind", daemon=True)
    foreign = threading.Thread(target=stop.wait, name="somebody-elses", daemon=True)
    beacon = policy.AudioBeacon("/nonexistent/x.sqlite")           # its target is the package's, its name aside
    for thread in (named, foreign):
        thread.start()
    beacon.start()
    thread_check.note_births(before, "tests/test_thread_hygiene.py::this")
    try:
        rows = "\n".join(thread_check.left_behind(grace=0.3))
        assert "nsf-left-behind (Event.wait)" in rows and "started in tests/test_thread_hygiene.py" in rows
        assert "nsf-audio-beacon (AudioBeacon._loop)" in rows and "somebody-elses" not in rows
    finally:
        stop.set()
        beacon.close()
        named.join(5)
        foreign.join(5)
    assert not [r for r in thread_check.left_behind(grace=2.0) if "left-behind" in r or "audio-beacon" in r]
