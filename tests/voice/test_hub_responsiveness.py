"""Responsiveness and reliability of the voice hub: how soon a followed research answer arrives, replies spoken in the
order the owner asked (a stale reply is shown but never spoken), and an audio chunk retried after a lost response."""
from contextlib import closing
import threading
import time

from neuroscifi.instance import store
from neuroscifi.voice import hub as vhub
from neuroscifi.webcortex import orchestrator

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
