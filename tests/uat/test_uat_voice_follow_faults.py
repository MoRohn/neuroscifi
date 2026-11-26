"""Wave 2, lane U (directive §12): the voice follow-up across a restart (ADR-053 R8, setting resume_follow, on by
default) made to fail. Its two entry points: the delivery claim (voice.transcripts.clear_follow: who delivers an
awaited answer, once) and the pick-up at a new session (VoiceHub._resume_follow). With each made to raise, time out or
return garbage: the spoken question still queued its research task, the answer still reaches the person, a new
session still starts and answers, ending it still works, and the failure is visible. Fake audio engines (the voice
suite's FakeEngines): real microphones and speakers are not part of this."""
from contextlib import closing
import logging
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.voice import hub as vhub, state as vstate, transcripts as tx
from neuroscifi.voice.hub import VoiceHub
from neuroscifi.webcortex import orchestrator, state as wstate
from voice.conftest import OWNER, FakeEngines, wait_for

MODES = ("raise", "timeout", "garbage")


def new_hub(instance):
    FakeEngines.instance = None
    vstate.reset()
    hub = VoiceHub(instance, engines_factory=FakeEngines)
    hub.fake = FakeEngines({})
    return hub


def close(hub):
    for s in list(hub.sessions.values()):
        hub._end(s, "test")
    hub.close()
    vstate.reset()


def replies(hub, sid, intent=None):
    out = hub.events(sid, OWNER, 0, wait=0.1)["events"]
    return [e for e in out if e["type"] == "reply" and (intent is None or e["reply"]["intent"] == intent)]


def faulty(mode, calls):
    def fail(*args, **kwargs):
        calls.append(1)
        if mode == "raise":
            raise RuntimeError("staged failure of the voice follow-up")
        if mode == "timeout":
            time.sleep(0.01)
            raise TimeoutError("staged timeout of the voice follow-up")
        return "staged-garbage-value"
    return fail


@pytest.fixture
def instance(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, time.time())) as db:
        wstate.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, time.time())
    return path


@pytest.mark.parametrize("mode", MODES)
def test_a_failing_delivery_claim_still_delivers_the_spoken_answer(instance, monkeypatch, caplog, mode):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    calls = []
    monkeypatch.setattr(tx, "clear_follow", faulty(mode, calls))
    hub = new_hub(instance)
    try:
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        hub.text(sid, OWNER, "Research the Zephyr compiler release")
        ack = wait_for(lambda: replies(hub, sid, "research"))[0]
        task_id = ack["reply"]["task_id"]
        with closing(store.connect(instance)) as db:
            assert orchestrator.get(db, task_id)["status"] == "queued"       # the question became research
            orchestrator.cancel(db, task_id, time.time())                     # it ends (here: cancelled)
        done = wait_for(lambda: replies(hub, sid, "research_result"), timeout=10)
        assert done and done[0]["reply"]["task_id"] == task_id, "the answer did not reach the person"
        assert calls, "the delivery claim was never reached"
        hub.text(sid, OWNER, "status report")
        assert wait_for(lambda: len(replies(hub, sid)) >= 3), "the session stopped answering"
        assert hub.stop(sid, OWNER)["status"] == "ended"                          # the owner's stop still works
        warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("follow" in w for w in warned), warned
    finally:
        close(hub)


@pytest.mark.parametrize("mode", MODES)
def test_a_failing_pick_up_after_a_restart_never_stops_a_session_from_starting(instance, monkeypatch, caplog, mode):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    first = new_hub(instance)
    sid = first.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    first.text(sid, OWNER, "Research the Zephyr compiler release")
    task_id = wait_for(lambda: replies(first, sid, "research"))[0]["reply"]["task_id"]
    first.close()                                    # the process ends while the answer is awaited
    vstate.reset()
    calls = []
    monkeypatch.setattr(VoiceHub, "_resume_follow", faulty(mode, calls))
    second = new_hub(instance)
    try:
        with closing(store.connect(instance)) as db:
            tx.end_orphans(db, "console:", time.time())
        again = second.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        assert calls, "the pick-up was never reached"
        second.text(again, OWNER, "status report")
        assert wait_for(lambda: replies(second, again)), "the new session does not answer"
        with closing(store.connect(instance)) as db:
            assert orchestrator.get(db, task_id)["status"] == "queued"      # the research itself is untouched
        assert second.stop(again, OWNER)["status"] == "ended"                  # the owner's stop still works
        warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("follow" in w for w in warned), warned
    finally:
        close(second)
