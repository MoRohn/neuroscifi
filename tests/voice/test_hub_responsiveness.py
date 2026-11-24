"""Responsiveness and reliability of the voice hub: how soon a followed research answer arrives, replies spoken in the
order the owner asked (a stale reply is shown but never spoken), and an audio chunk retried after a lost response."""
from contextlib import closing
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.instance.errors import Conflict
from neuroscifi.voice import bridge as vbridge, hub as vhub
from neuroscifi.webcortex import orchestrator

from .conftest import OWNER, pcm, wait_for
from .test_hub_transcripts import events, start


def test_a_followed_research_task_that_finishes_quickly_is_answered_well_under_the_old_one_second_poll(hub, instance):
    """Defect 3: the follower checked the task every 1 s, so a fast answer waited up to a second. It now checks
    soon after the question and backs off to FOLLOW_POLL, so a long task is checked as rarely as before."""
    s = start(hub, mic=False)
    with closing(store.connect(instance)) as db:
        task = orchestrator.submit(db, "Zephyr compiler release date", time.time(), origin="voice")
    timer = threading.Timer(0.15, lambda: _cancel(instance, task["id"]))
    began = time.monotonic()
    timer.start()
    hub._follow_task(hub.sessions[s], task["id"])
    took = time.monotonic() - began
    timer.join()
    assert any(e["type"] == "task_done" for e in events(hub, s))
    assert took < 0.6, f"the answer waited {took:.2f} s for the follower's next check"


def test_the_follow_interval_starts_short_and_backs_off_to_the_poll():
    waits = vhub.follow_waits()
    naps = [next(waits) for _ in range(8)]
    assert naps[0] <= 0.1 and naps == sorted(naps) and naps[-1] == vhub.FOLLOW_POLL
    assert sum(naps[:4]) < 1.6                    # the first second is covered by a few checks, not dozens


def _cancel(instance, task_id):
    with closing(store.connect(instance)) as db:
        orchestrator.cancel(db, task_id, time.time())


class GatedBridge:
    """A bridge whose replies wait until the test releases them, so replies can finish in any chosen order."""

    def __init__(self):
        self.gates, self.started = {}, {}

    def handle(self, db, principal, ctx, text, pending=None):
        self.started.setdefault(text, threading.Event()).set()
        self.gates.setdefault(text, threading.Event()).wait(10)
        if text == "stop":
            return vbridge.Reply("stop_speaking", speech=[], text="Stopped.", actions=[{"kind": "stop_speaking"}])
        return vbridge.Reply("answer", speech=[f"Answer to {text}."], text=f"Answer to {text}.")

    def release(self, text):
        self.gates.setdefault(text, threading.Event()).set()

    def wait_started(self, text):
        assert self.started.setdefault(text, threading.Event()).wait(5)


def _replies(hub, s):
    return {e["reply"]["text"]: e for e in events(hub, s, kind="reply")}


def test_an_older_slower_reply_is_never_spoken_after_a_newer_one(hub):
    """Defect 5: replies of one session run concurrently and the last delivered superseded whatever was speaking,
    so the answer to the first question, finishing late, cut off the answer to the second and was spoken last.
    Each user turn now has a generation; a reply older than one already delivered is shown but not spoken."""
    gated = hub.bridge = GatedBridge()
    s = start(hub, mic=False)
    hub.text(s, OWNER, "first")
    gated.wait_started("first")
    hub.text(s, OWNER, "second")
    gated.wait_started("second")
    gated.release("second")
    second = wait_for(lambda: _replies(hub, s).get("Answer to second."))
    gated.release("first")
    first = wait_for(lambda: _replies(hub, s).get("Answer to first."))
    assert second["turn"] is not None and first["turn"] is None          # shown as text, not spoken
    session = hub.sessions[s]
    assert session.last_turn.id == second["turn"]["id"]
    assert session.turns[second["turn"]["id"]].status == "speaking"     # the newer answer was not cut off
    assert hub.speech(s, OWNER, second["turn"]["id"], 0).startswith(b"RIFF")


def test_a_reply_still_pending_when_the_owner_says_stop_is_not_spoken(hub):
    gated = hub.bridge = GatedBridge()
    s = start(hub, mic=False)
    hub.text(s, OWNER, "first")
    gated.wait_started("first")
    hub.text(s, OWNER, "stop")
    gated.release("stop")
    wait_for(lambda: _replies(hub, s).get("Stopped."))
    gated.release("first")
    assert wait_for(lambda: _replies(hub, s).get("Answer to first."))["turn"] is None


@pytest.mark.parametrize("kind", ["barge_in", "stop"])
def test_a_reply_to_a_question_asked_before_the_interrupted_speech_is_not_spoken_afterwards(hub, kind):
    """The owner asked, then a research result started speaking while that answer was still being prepared; the
    owner interrupted it. The older answer, arriving later, is shown but not spoken; a question asked while the
    result was speaking (the barge-in itself, whichever request reaches the hub first) is answered aloud."""
    gated = hub.bridge = GatedBridge()
    s = start(hub, mic=False)
    hub.text(s, OWNER, "older")
    gated.wait_started("older")
    hub._deliver(hub.sessions[s], vbridge.Reply("research_result", speech=["The research is done."],
                                                text="The research is done."))
    speaking = _replies(hub, s)["The research is done."]["turn"]
    hub.text(s, OWNER, "newer")                       # asked over the speech, before the interruption report
    gated.wait_started("newer")
    hub.interrupt(s, OWNER, speaking["id"], 0, kind)
    gated.release("older")
    assert wait_for(lambda: _replies(hub, s).get("Answer to older."))["turn"] is None
    gated.release("newer")
    assert wait_for(lambda: _replies(hub, s).get("Answer to newer."))["turn"] is not None


def test_a_retried_audio_chunk_is_accepted_once_and_a_gap_is_still_refused(hub):
    """Defect 6: one lost response killed the utterance (seq had to be last+1, and the client never retried). A
    repeat of the last chunk is now acknowledged without being added twice, and so is a retried final chunk of an
    utterance already closed; a gap is still refused."""
    s = start(hub)
    hub.fake.asr.script = ["show me the evidence"]
    assert hub.audio(s, OWNER, "u0001", 0, False, pcm(0.3))["status"] == "buffered"
    assert hub.audio(s, OWNER, "u0001", 0, False, pcm(0.3))["status"] == "duplicate"
    with pytest.raises(Conflict, match="other audio"):       # a retry sends the same bytes; other bytes are no retry
        hub.audio(s, OWNER, "u0001", 0, False, pcm(0.2))
    assert hub.audio(s, OWNER, "u0001", 1, False, pcm(0.1))["status"] == "buffered"
    assert len(hub.sessions[s].utterances["u0001"]["pcm"]) == len(pcm(0.4))      # the repeat was not added
    with pytest.raises(Conflict, match="expected chunk"):
        hub.audio(s, OWNER, "u0001", 3, False, pcm(0.1))
    assert hub.audio(s, OWNER, "u0001", 2, True, pcm(0.2))["status"] == "final"
    assert hub.audio(s, OWNER, "u0001", 2, True, pcm(0.2))["status"] == "duplicate"
    hub.drain()
    wait_for(lambda: events(hub, s, kind="reply"))
    assert hub.metrics["utterances"] == 1                                         # transcribed and answered once
    with pytest.raises(Conflict, match="expected chunk"):
        hub.audio(s, OWNER, "u0001", 1, False, pcm(0.1))     # an older chunk of the closed utterance is no repeat
