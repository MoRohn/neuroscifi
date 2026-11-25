"""R8 (ADR-053): the follow-up of a spoken research question survives a restart. The voice session keeps which task
it awaits; when its process ends before the answer (the hub closes, its follower stops), the principal's next session
in a new process picks the follow up again and the answer is delivered there, once. With the owner's resume_follow
off it is not. The microphone and speakers are test doubles here (FakeEngines): real-device validation is open."""
from contextlib import closing
import time

from neuroscifi.instance import store
from neuroscifi.voice import config as vconfig, state as vstate, transcripts as tx
from neuroscifi.voice.hub import VoiceHub
from neuroscifi.webcortex import orchestrator

from .conftest import OWNER, FakeEngines, wait_for


def new_hub(instance):
    FakeEngines.instance = None
    vstate.reset()
    hub = VoiceHub(instance, engines_factory=FakeEngines)
    hub.fake = FakeEngines({})
    return hub


def events(hub, sid, kind=None):
    out = hub.events(sid, OWNER, 0, wait=0.1)["events"]
    return [e for e in out if kind is None or e["type"] == kind]


def ask(hub, monkeypatch):
    from neuroscifi.voice import hub as vhub
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    hub.text(sid, OWNER, "Research the Zephyr compiler release")
    ack = wait_for(lambda: [e for e in events(hub, sid, "reply") if e["reply"]["intent"] == "research"])[0]
    return sid, ack["reply"]["task_id"]


def test_the_answer_reaches_the_next_session_after_a_restart(instance, monkeypatch):
    first = new_hub(instance)
    sid, task_id = ask(first, monkeypatch)
    with closing(store.connect(instance)) as db:
        assert tx.session(db, sid)["context"]["follow_task_id"] == task_id
    first.close()                                       # the process ends; its follower stops looking
    with closing(store.connect(instance)) as db:
        tx.end_orphans(db, "console:", time.time())     # what the restarted console does at start
        orchestrator.cancel(db, task_id, time.time())    # the research finishes while nobody follows it
    second = new_hub(instance)
    try:
        again = second.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        done = wait_for(lambda: [e for e in events(second, again, "reply")
                                 if e["reply"]["intent"] == "research_result"], timeout=10)
        assert len(done) == 1 and done[0]["reply"]["task_id"] == task_id and "cancelled" in done[0]["reply"]["text"]
        assert any("before the restart" in e.get("text", "") for e in events(second, again, "notice"))
        with closing(store.connect(instance)) as db:
            assert tx.session(db, again)["context"].get("follow_task_id") is None     # delivered: not again
        third = second.start(OWNER, "console:dev5678", "Console", "text_only")["session"]
        time.sleep(0.3)
        assert not [e for e in events(second, third, "reply") if e["reply"]["intent"] == "research_result"]
    finally:
        for s in list(second.sessions.values()):
            second._end(s, "test")
        second.close()
        vstate.reset()


def test_the_owner_can_switch_it_off(instance, monkeypatch):
    with closing(store.connect(instance)) as db:
        vconfig.update(db, {"resume_follow": False})
    first = new_hub(instance)
    sid, task_id = ask(first, monkeypatch)
    first.close()
    with closing(store.connect(instance)) as db:
        orchestrator.cancel(db, task_id, time.time())
    second = new_hub(instance)
    try:
        again = second.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        time.sleep(0.4)
        assert not [e for e in events(second, again, "reply") if e["reply"]["intent"] == "research_result"]
        with closing(store.connect(instance)) as db:
            assert "follow_task_id" not in tx.session(db, again)["context"]
    finally:
        for s in list(second.sessions.values()):
            second._end(s, "test")
        second.close()
        vstate.reset()


def test_a_session_that_merely_ended_is_not_followed_twice(hub, instance, monkeypatch):
    """Without a restart the hub's own follower still delivers; the next session must not start a second one."""
    sid, task_id = ask(hub, monkeypatch)
    hub.stop(sid, OWNER)
    again = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    assert not [e for e in events(hub, again, "notice") if "before the restart" in e.get("text", "")]
    with closing(store.connect(instance)) as db:
        orchestrator.cancel(db, task_id, time.time())
    with closing(store.connect(instance)) as db:
        wait_for(lambda: db.execute("SELECT count(*) FROM voice_segments WHERE task_id=? AND speaker='neuroscifi' "
                                    "AND text LIKE '%cancelled%'", (task_id,)).fetchone()[0])
        time.sleep(0.3)
        assert db.execute("SELECT count(*) FROM voice_segments WHERE task_id=? AND speaker='neuroscifi' AND text "
                          "LIKE '%cancelled%'", (task_id,)).fetchone()[0] == 1
    assert not [e for e in events(hub, again, "reply") if e["reply"]["intent"] == "research_result"]


def test_resume_follow_is_an_owner_setting(instance):
    import pytest
    from neuroscifi.instance.errors import ValidationError
    with closing(store.connect(instance)) as db:
        assert vconfig.get(db)["resume_follow"] is True
        with pytest.raises(ValidationError):
            vconfig.update(db, {"resume_follow": "yes"})
        with pytest.raises(ValidationError):
            vconfig.update(db, {"resume_follow": False}, owner=False)
