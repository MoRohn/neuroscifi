"""Wave 2, lane U (directive §12): the voice follow-up across a restart (ADR-053 R8, setting resume_follow, on by
default) made to fail. Its two entry points: the delivery claim (voice.transcripts.clear_follow: who delivers an
awaited answer, once) and the pick-up at a new session (VoiceHub._resume_follow). With each made to raise, time out or
return garbage: the spoken question still queued its research task, the answer still reaches the person, a new
session still starts and answers, ending it still works, and the failure is visible. Fake audio engines (the voice
suite's FakeEngines): real microphones and speakers are not part of this.

Wave 2 lane X (review2 M1): a claim that cannot be made is not a claim. Nothing is delivered while it fails; the
follow stays and the claim is asked again, so a transient failure delivers once, late; two hubs never both deliver;
a forget that holds the lock during the claim leaves nothing of the forgotten task behind."""
from contextlib import closing, contextmanager
import logging
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.voice import hub as vhub, state as vstate, transcripts as tx
from neuroscifi.voice.hub import VoiceHub
from neuroscifi.webcortex import orchestrator, state as wstate
from voice.conftest import OWNER, FakeEngines, wait_for

MODES = ("raise", "timeout", "garbage")
REAL_CLAIM = tx.clear_follow


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


def transient(mode, calls, failures):
    """The claim fails `failures` times in the chosen way (each failure appends 1 to `calls`), then works: the real
    transcripts.clear_follow (each real claim appends 0)."""
    real, fail = REAL_CLAIM, faulty(mode, calls)

    def claim(*args, **kwargs):
        if len(calls) < failures:
            return fail(*args, **kwargs)
        calls.append(0)
        return real(*args, **kwargs)
    return claim


def ask(hub, sid, objective="Research the Zephyr compiler release"):
    hub.text(sid, OWNER, objective)
    return wait_for(lambda: replies(hub, sid, "research"))[0]["reply"]["task_id"]


def answers(instance, task_id):
    with closing(store.connect(instance)) as db:
        return db.execute("SELECT session_id FROM voice_segments WHERE task_id=? AND speaker='neuroscifi' AND "
                          "json_extract(detail,'$.intent')='research_result'", (task_id,)).fetchall()


def awaiting(instance, task_id):
    with closing(store.connect(instance)) as db:
        return db.execute("SELECT count(*) FROM voice_sessions WHERE json_extract(context,'$.follow_task_id')=?",
                          (task_id,)).fetchone()[0]


@pytest.fixture
def instance(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, time.time())) as db:
        wstate.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, time.time())
    return path


@pytest.mark.parametrize("mode", MODES)
def test_a_transiently_failing_delivery_claim_delivers_the_spoken_answer_once(instance, monkeypatch, caplog, mode):
    """Lane U's intent, kept: an answer is not silently lost when the claim fails for a while. The claim fails twice,
    then works: the answer is delivered once, after the real claim, and each failure is logged with the task id and
    the error's type only (never its message)."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    monkeypatch.setattr(vhub, "CLAIM_WAITS", (0.05,), raising=False)
    calls = []
    monkeypatch.setattr(tx, "clear_follow", transient(mode, calls, 2))
    hub = new_hub(instance)
    try:
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = ask(hub, sid)
        with closing(store.connect(instance)) as db:
            assert orchestrator.get(db, task_id)["status"] == "queued"       # the question became research
            orchestrator.cancel(db, task_id, time.time())                     # it ends (here: cancelled)
        done = wait_for(lambda: replies(hub, sid, "research_result"), timeout=10)
        assert done and done[0]["reply"]["task_id"] == task_id, "the answer did not reach the person"
        time.sleep(0.3)
        assert len(replies(hub, sid, "research_result")) == 1 and len(answers(instance, task_id)) == 1
        assert calls[:3] == [1, 1, 0], calls                  # delivered only after the third, real, claim
        assert awaiting(instance, task_id) == 0
        hub.text(sid, OWNER, "status report")
        assert wait_for(lambda: len(replies(hub, sid)) >= 3), "the session stopped answering"
        assert hub.stop(sid, OWNER)["status"] == "ended"                          # the owner's stop still works
        warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("claim of task " + task_id in w for w in warned), warned
        assert not any("staged" in w for w in warned), warned
    finally:
        close(hub)


@pytest.mark.parametrize("mode", MODES)
def test_a_claim_that_cannot_be_made_delivers_nothing_and_keeps_the_follow(instance, monkeypatch, caplog, mode):
    """review2 M1: "could not ask" is not "yes". While every claim fails nothing is delivered and the follow stays on
    the session; once a claim can be made again the answer is delivered once: late, not lost, not twice."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    monkeypatch.setattr(vhub, "CLAIM_WAITS", (0.05,), raising=False)
    calls = []
    monkeypatch.setattr(tx, "clear_follow", transient(mode, calls, 10 ** 6))
    hub = new_hub(instance)
    try:
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = ask(hub, sid)
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: len(calls) >= 4)
        assert not replies(hub, sid, "research_result") and not answers(instance, task_id)
        assert awaiting(instance, task_id) == 1                                   # still awaited: not lost
        assert any("claim of task " + task_id in r.getMessage() for r in caplog.records)
        calls.clear()
        monkeypatch.setattr(tx, "clear_follow", transient(mode, calls, 0))      # the database answers again
        wait_for(lambda: replies(hub, sid, "research_result"), timeout=10)
        time.sleep(0.3)
        assert len(replies(hub, sid, "research_result")) == 1 and len(answers(instance, task_id)) == 1
        assert awaiting(instance, task_id) == 0
    finally:
        close(hub)


def test_two_hubs_with_a_failing_claim_deliver_the_answer_once(instance, monkeypatch):
    """review2 M1 (lane U's guard could deliver from both): two hubs (two server processes) follow one task and the
    claim fails for both for a while. Exactly one answer is given, only after a real claim."""
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    monkeypatch.setattr(vhub, "CLAIM_WAITS", (0.05,), raising=False)
    calls = []
    monkeypatch.setattr(tx, "clear_follow", transient("raise", calls, 6))
    first = new_hub(instance)
    sid = first.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    task_id = ask(first, sid)
    second = new_hub(instance)
    try:
        other = second.start(OWNER, "console:dev5678", "Console", "text_only")["session"]
        notices = [e.get("text", "") for e in second.events(other, OWNER, 0, wait=0.1)["events"]
                   if e["type"] == "notice"]
        assert any("asked about earlier" in t for t in notices), notices         # both hubs follow it
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: answers(instance, task_id), timeout=10)
        wait_for(lambda: calls.count(0) >= 2, timeout=10)                       # both followers made a real claim
        time.sleep(0.3)
        assert len(answers(instance, task_id)) == 1
        assert len(replies(first, sid, "research_result")) + len(replies(second, other, "research_result")) == 1
        assert awaiting(instance, task_id) == 0
    finally:
        close(first)
        close(second)


def test_a_forget_holding_the_lock_past_the_wait_leaves_nothing_of_the_task(instance, monkeypatch):
    """review2 M1, the reviewer's case with a real forget: when the follower claims the answer, the owner's forget of
    the task holds the write lock longer than the database wait, so the claim fails; the forget then commits. Nothing
    about the forgotten task is written to a transcript or a session's context afterwards (the forget of a research
task scrubs the session contexts; the question's own earlier segments belong to the transcript, which the owner
forgets separately). The wait of the hub's
    connections is shortened (300 ms instead of 10 s) so the test does not sleep 10 s."""
    from neuroscifi.instance import governance
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    monkeypatch.setattr(vhub, "CLAIM_WAITS", (0.3,), raising=False)
    hub = new_hub(instance)

    @contextmanager
    def short_wait():
        conn = store.connect(instance)
        conn.execute("PRAGMA busy_timeout=300")
        try:
            yield conn
        finally:
            conn.close()
    hub.db = short_wait
    real = tx.clear_follow
    locked, forgot, outcome, seen = threading.Event(), threading.Event(), [], set()

    def long_forget(task_id):
        conn = store.connect(instance)
        conn.execute("BEGIN IMMEDIATE")
        governance._forget_research_core(conn, task_id, time.time())
        locked.set()
        time.sleep(0.45)                   # longer than the hub's wait, shorter than the pause before a new claim
        conn.execute("COMMIT")
        conn.close()
        forgot.set()

    def claim(db, principal, task_id):
        if not locked.is_set():            # what the transcripts held before the claim and the forget
            seen.update(i for (i,) in db.execute("SELECT id FROM voice_segments"))
            threading.Thread(target=long_forget, args=(task_id,), daemon=True).start()
            locked.wait(5)
        try:
            outcome.append(real(db, principal, task_id))
        except Exception as exc:          # noqa: BLE001 - recorded, then raised as the database raised it
            outcome.append(type(exc).__name__)
            raise
        return outcome[-1]
    monkeypatch.setattr(tx, "clear_follow", claim)
    try:
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = ask(hub, sid, "Research the Zephyr compiler release FORGET-MARKER-4417")
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        assert forgot.wait(10)
        time.sleep(1.0)                    # the pause before a new claim, and a poll that finds the task gone
        # the claim failed; afterwards the follower either found the task gone, or found nothing left to claim
        assert outcome and outcome[0] == "OperationalError" and outcome[1:] in ([], [0]), outcome
        with closing(store.connect(instance)) as db:
            assert db.execute("SELECT count(*) FROM web_tasks WHERE id=?", (task_id,)).fetchone()[0] == 0
            segments = [s for s in db.execute("SELECT id, text, task_id FROM voice_segments") if s[0] not in seen]
            contexts = [c for (c,) in db.execute("SELECT context FROM voice_sessions")]
        assert not [s for s in segments if s[2] == task_id or "FORGET-MARKER" in (s[1] or "")], segments
        assert not [c for c in contexts if task_id in c or "FORGET-MARKER" in c], contexts
        assert not replies(hub, sid, "research_result")
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


def test_a_pick_up_failing_part_way_gives_back_its_count_and_slot(instance, monkeypatch):
    """review2 L6: when the pick-up fails after it counted the task as followed (here: the read of the task raises
    a database error), the count and the follower slot go back, so the same hub can still pick the answer up at the
    next session."""
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    first = new_hub(instance)
    sid = first.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    task_id = ask(first, sid)
    first.close()
    vstate.reset()
    second = new_hub(instance)
    try:
        with closing(store.connect(instance)) as db:
            tx.end_orphans(db, "console:", time.time())
        real_get = orchestrator.get
        broken = {"on": True}

        def get(db, tid, **kw):
            if broken["on"]:
                raise RuntimeError("staged: the read failed part-way")
            return real_get(db, tid, **kw)
        monkeypatch.setattr(orchestrator, "get", get)
        slots = second._followers._value
        again = second.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        assert second._following.get(task_id) is None                       # the count went back
        assert second._followers._value == slots                             # and the slot
        broken["on"] = False
        second.stop(again, OWNER)
        later = second.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        notices = [e.get("text", "") for e in second.events(later, OWNER, 0, wait=0.1)["events"]
                   if e["type"] == "notice"]
        assert any("Picking up" in t for t in notices), notices              # picked up at the next session
    finally:
        close(second)


# ---- Wave 2 reconciliation: the check of the fixes (review2b N1, N2, N4) ------------------------------------------

def _claims_fail_in_the_follower_until(gate, calls):
    """The delivery claim fails (a locked database) in the follower thread until `gate` is set; a claim made anywhere
    else (a new session's pick-up) and every claim after it are the real one."""
    import sqlite3

    def claim(db, principal, task_id):
        if threading.current_thread().name == "nsf-voice-follow" and not gate.is_set():
            calls.append(1)
            raise sqlite3.OperationalError("database is locked")
        calls.append(0)
        return REAL_CLAIM(db, principal, task_id)
    return claim


@pytest.mark.parametrize("order", ["the_first_hub_recovers_first", "the_second_hub_starts_first"])
def test_a_new_session_on_another_hub_never_takes_a_follow_whose_follower_still_claims(instance, monkeypatch, order):
    """review2b N1: the first hub's claim fails and it pauses; the person ends that session and starts one on a
    second hub (another process). The second hub's pick-up judged the follow stale and cleared it, so the first hub's
    next claim found nothing and the answer was never given. Now a follow whose follower lives is left to it: the
    answer is given exactly once, whichever happens first."""
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    monkeypatch.setattr(vhub, "CLAIM_WAITS", (0.1,), raising=False)
    gate, calls = threading.Event(), []
    monkeypatch.setattr(tx, "clear_follow", _claims_fail_in_the_follower_until(gate, calls))
    first = new_hub(instance)
    second = None
    try:
        sid = first.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = ask(first, sid)
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: calls.count(1) >= 1)                    # the first hub could not claim; it pauses
        if order == "the_first_hub_recovers_first":
            gate.set()
            wait_for(lambda: answers(instance, task_id), timeout=10)
        time.sleep(0.2)
        first.stop(sid, OWNER)                                   # the person leaves this session
        second = VoiceHub(instance, engines_factory=FakeEngines)
        second.fake = FakeEngines({})
        other = second.start(OWNER, "pocket:dev9999", "Pocket", "text_only")["session"]
        if order == "the_second_hub_starts_first":
            assert awaiting(instance, task_id) == 2             # left to its living follower: the ended session
            gate.set()                                           # and the new one (which carried it) still await it
        wait_for(lambda: answers(instance, task_id), timeout=10)
        time.sleep(0.5)
        assert len(answers(instance, task_id)) == 1, "the answer was lost or given twice"
        assert not replies(second, other, "research_result")      # the second hub did not deliver it as well
        assert awaiting(instance, task_id) == 0
    finally:
        close(first)
        if second is not None:
            close(second)


def test_the_person_hears_once_that_the_answer_is_coming_while_the_claim_is_retried(instance, monkeypatch):
    """review2b N2: while the claim is asked again, the asking session gets one plain notice, not one per retry."""
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    monkeypatch.setattr(vhub, "CLAIM_WAITS", (0.05,), raising=False)
    calls = []
    monkeypatch.setattr(tx, "clear_follow", transient("raise", calls, 5))
    hub = new_hub(instance)
    try:
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = ask(hub, sid)
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: replies(hub, sid, "research_result"), timeout=10)
        notices = [e["text"] for e in hub.events(sid, OWNER, 0, wait=0.1)["events"] if e["type"] == "notice"
                   and "answer is ready" in e.get("text", "")]
        assert calls.count(1) == 5 and len(notices) == 1, (calls, notices)
        assert "claim" not in notices[0] and "database" not in notices[0]     # plain words
    finally:
        close(hub)


def test_a_failing_follow_write_still_shows_the_acknowledgement(instance, monkeypatch, caplog):
    """review2b N4 (i): the follow is written before the acknowledgement; a database error on that write used to hide
    the acknowledgement. It is shown, the failure is logged (its type only), and this process still follows."""
    import sqlite3
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    real = tx.update_session

    def update(db, sid, now, **kw):
        if (kw.get("context") or {}).get("follow_task_id"):
            raise sqlite3.OperationalError("database is locked")
        return real(db, sid, now, **kw)
    monkeypatch.setattr(tx, "update_session", update)
    hub = new_hub(instance)
    try:
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = ask(hub, sid)                                   # the acknowledgement was shown
        assert any("could not be kept" in r.getMessage() for r in caplog.records)
        assert not any("locked" in r.getMessage() for r in caplog.records)
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        assert wait_for(lambda: replies(hub, sid, "research_result"), timeout=10)
    finally:
        close(hub)


def _old_task(instance, objective="Zephyr compiler maintainers"):
    with closing(store.connect(instance)) as db:
        prepared = orchestrator.prepare(db, objective, time.time() - 60, mode="quick_lookup", origin="console")
        with store.write(db):
            return orchestrator.insert(db, prepared, time.time() - 60)


def test_a_failure_before_the_follower_starts_gives_back_its_slot_and_count(instance, monkeypatch):
    """review2b N4 (ii): between taking a follower slot and starting the follower the reply's other actions run; one
    that fails gives the slot and the follow count back."""
    from neuroscifi.voice import bridge as vbridge
    hub = new_hub(instance)
    try:
        task_id = _old_task(instance)
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        slots = hub._followers._value

        def fail(s, mode):
            raise RuntimeError("staged: the mode change failed")
        monkeypatch.setattr(hub, "_apply_mode", fail)
        reply = vbridge.Reply("research", text="Following it.", task_id=task_id, follow=task_id,
                              actions=[{"kind": "mode", "mode": "text_only"}])
        with pytest.raises(RuntimeError):
            hub._deliver(hub.sessions[sid], reply)
        assert hub._following.get(task_id) is None and hub._followers._value == slots
    finally:
        close(hub)


@pytest.mark.parametrize("created", ["before_the_conversation", "in_the_conversation"])
def test_a_reply_that_follows_a_task_and_forgets_the_conversation(instance, monkeypatch, created):
    """review2b N4 (iii): the follow is written to the session the same reply then forgets. A task from before the
    conversation is still followed on the new session and its answer is given once; a task the conversation created
    is forgotten with it, and nothing of it is written back to any session."""
    from neuroscifi.voice import bridge as vbridge
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    hub = new_hub(instance)
    try:
        before = _old_task(instance) if created == "before_the_conversation" else None
        sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
        task_id = before or ask(hub, sid)
        reply = vbridge.Reply("research", text="Following it.", task_id=task_id, follow=task_id,
                              actions=[{"kind": "forget_conversation"}])
        hub._deliver(hub.sessions[sid], reply)
        if created == "in_the_conversation":
            with closing(store.connect(instance)) as db:
                assert db.execute("SELECT count(*) FROM web_tasks WHERE id=?", (task_id,)).fetchone()[0] == 0
                contexts = [c for (c,) in db.execute("SELECT context FROM voice_sessions")]
            assert not [c for c in contexts if task_id in (c or "")], contexts
            return
        assert awaiting(instance, task_id) == 1                   # kept on the new session
        with closing(store.connect(instance)) as db:
            orchestrator.cancel(db, task_id, time.time())
        wait_for(lambda: answers(instance, task_id), timeout=10)
        time.sleep(0.3)
        assert len(answers(instance, task_id)) == 1 and awaiting(instance, task_id) == 0
    finally:
        close(hub)
