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


def answers(instance, task_id):
    """The research answers written to any transcript: (session id, text)."""
    with closing(store.connect(instance)) as db:
        return db.execute("SELECT session_id, text FROM voice_segments WHERE task_id=? AND speaker='neuroscifi' AND "
                          "text LIKE '%cancelled%'", (task_id,)).fetchall()


def awaiting(instance, task_id):
    with closing(store.connect(instance)) as db:
        return db.execute("SELECT count(*) FROM voice_sessions WHERE json_extract(context,'$.follow_task_id')=?",
                          (task_id,)).fetchone()[0]


def test_a_session_that_merely_ended_is_not_followed_twice(hub, instance, monkeypatch):
    """Without a restart the hub's own follower still delivers; the next session must not start a second one. The
    answer goes to the person's session that is open when it is ready (review1 A9: it used to go to the ended one,
    and stayed awaited in the open one, which a later session then replayed "before the restart")."""
    sid, task_id = ask(hub, monkeypatch)
    hub.stop(sid, OWNER)
    again = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    assert not [e for e in events(hub, again, "notice") if "Picking up" in e.get("text", "")]
    with closing(store.connect(instance)) as db:
        orchestrator.cancel(db, task_id, time.time())
    wait_for(lambda: answers(instance, task_id))
    time.sleep(0.3)
    assert [s for s, _ in answers(instance, task_id)] == [again]                 # once, to the open session
    assert len([e for e in events(hub, again, "reply") if e["reply"]["intent"] == "research_result"]) == 1
    assert awaiting(instance, task_id) == 0                                      # off every session of the person
    later = hub.start(OWNER, "console:dev5678", "Console", "text_only")["session"]
    time.sleep(0.3)
    assert not [e for e in events(hub, later, "reply") if e["reply"]["intent"] == "research_result"]
    assert not [e for e in events(hub, later, "notice") if "Picking up" in e.get("text", "")]


def test_two_processes_following_one_task_deliver_it_once(instance, monkeypatch):
    """review1 A10: a second hub (another server process: the hub that asked is still open) picks the follow up for
    the person's new session; the delivery is claimed in the database, so the answer is given once. Neither says
    "before the restart": there was none."""
    first = new_hub(instance)
    sid, task_id = ask(first, monkeypatch)
    second = new_hub(instance)
    try:
        other = second.start(OWNER, "console:dev5678", "Console", "text_only")["session"]
        notices = [e.get("text", "") for e in events(second, other, "notice")]
        assert any("asked about earlier" in t for t in notices) and not any("restart" in t for t in notices)
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: answers(instance, task_id))
        time.sleep(0.5)
        assert len(answers(instance, task_id)) == 1
        assert awaiting(instance, task_id) == 0
    finally:
        for h in (first, second):
            for s in list(h.sessions.values()):
                h._end(s, "test")
            h.close()
        vstate.reset()


def test_an_answer_is_not_lost_when_the_person_is_live_on_a_process_that_does_not_follow_it(instance, monkeypatch):
    """review1b R3: the person is live in session B of another process (started before the question, so it never
    picked the follow up); they asked in session A of this process, and A ended. When the task finishes, A's follower
    used to return because the person is active elsewhere: nobody delivered, and the task stayed awaited on A. Now,
    once no other follower claimed it within ELSEWHERE_GRACE, the answer goes into A's transcript, once."""
    from neuroscifi.voice import hub as vhub
    monkeypatch.setattr(vhub, "ELSEWHERE_GRACE", 0.2)
    second = new_hub(instance)
    first = new_hub(instance)
    try:
        other = second.start(OWNER, "console:dev5678", "Console", "text_only")["session"]
        sid, task_id = ask(first, monkeypatch)
        first.stop(sid, OWNER)
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: answers(instance, task_id), timeout=10)
        time.sleep(0.5)
        assert [s for s, _ in answers(instance, task_id)] == [sid]                # once, to the asking transcript
        assert awaiting(instance, task_id) == 0
        assert not [e for e in events(second, other, "reply") if e["reply"]["intent"] == "research_result"]
    finally:
        for h in (first, second):
            for s in list(h.sessions.values()):
                h._end(s, "test")
            h.close()
        vstate.reset()


def test_a_follower_that_gives_up_leaves_nothing_to_replay(hub, instance, monkeypatch):
    """review1 S9: after FOLLOW_MAX the follower stops; the task is taken off the sessions, so its answer is not
    spoken at the person's next session, possibly days later."""
    from neuroscifi.voice import hub as vhub
    monkeypatch.setattr(vhub, "FOLLOW_MAX", 0.3)
    sid, task_id = ask(hub, monkeypatch)
    wait_for(lambda: awaiting(instance, task_id) == 0, timeout=10)
    with closing(store.connect(instance)) as db:
        orchestrator.cancel(db, task_id, time.time())
    hub.stop(sid, OWNER)
    again = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    time.sleep(0.4)
    assert not answers(instance, task_id)
    assert not [e for e in events(hub, again, "notice") if "Picking up" in e.get("text", "")]


def test_a_task_that_finished_before_the_previous_session_ended_is_not_replayed(hub, instance, monkeypatch):
    """review1 S9: the hub that followed it is still open, so it had its chance: a stale mark (a follower that
    stopped without clearing it) does not make the next session speak an old answer; nor does an answer that
    finished more than RESUME_MAX_AGE ago, even after a restart."""
    from neuroscifi.voice import hub as vhub
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    with closing(store.connect(instance)) as db:
        task = orchestrator.submit(db, "Research the Zephyr compiler release", time.time(), origin="voice")
        orchestrator.cancel(db, task["id"], time.time())
        old = tx.start_session(db, "owner", "console:dev1234", "text_only", time.time())
        tx.update_session(db, old, time.time(), context={"follow_task_id": task["id"], "follow_by": hub.token})
        tx.update_session(db, old, time.time(), status="ended")      # after the task finished
    again = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    time.sleep(0.4)
    assert not answers(instance, task["id"]) and awaiting(instance, task["id"]) == 0
    assert not [e for e in events(hub, again, "notice") if "Picking up" in e.get("text", "")]
    with closing(store.connect(instance)) as db:            # a hub that is gone, but an answer a day old
        tx.update_session(db, again, time.time(), context={"follow_task_id": task["id"], "follow_by": "1:0:gone"})
        tx.update_session(db, again, time.time(), status="ended")
    monkeypatch.setattr(vhub, "RESUME_MAX_AGE", 0.0)
    third = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    time.sleep(0.4)
    assert not answers(instance, task["id"]) and awaiting(instance, task["id"]) == 0
    assert not [e for e in events(hub, third, "notice") if "Picking up" in e.get("text", "")]


def test_with_resume_follow_off_nothing_is_written(hub, instance, monkeypatch):
    """review1 A15: the follow is not kept in the session at all when the owner switched it off."""
    with closing(store.connect(instance)) as db:
        vconfig.update(db, {"resume_follow": False})
    sid, task_id = ask(hub, monkeypatch)
    time.sleep(0.2)
    with closing(store.connect(instance)) as db:
        assert "follow_task_id" not in tx.session(db, sid)["context"]
        orchestrator.cancel(db, task_id, time.time())
    wait_for(lambda: answers(instance, task_id))                # its own follower still delivers it
    assert [s for s, _ in answers(instance, task_id)] == [sid]


def test_resume_follow_is_an_owner_setting(instance):
    import pytest
    from neuroscifi.instance.errors import ValidationError
    with closing(store.connect(instance)) as db:
        assert vconfig.get(db)["resume_follow"] is True
        with pytest.raises(ValidationError):
            vconfig.update(db, {"resume_follow": "yes"})
        with pytest.raises(ValidationError):
            vconfig.update(db, {"resume_follow": False}, owner=False)
