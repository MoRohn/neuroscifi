"""Pocket voice obeys the research routes' rules (security review A-1; voice/bridge.py, ADR-042 draft).

A typed or spoken command over /api/voice_text reached orchestrator.cancel and requeue without research_routes.gate,
nodes.requires_signature or nodes.may_drive: a family device cancelled and continued a session a signed node owned,
and a stolen node credential cancelled without its key. Over the real in-process bridge and voice hub."""
import time

import pytest

from neuroscifi.voice import bridge as vbridge
from neuroscifi.voice.hub import VoiceHub
from nodekeys import enrol
from test_pocket_research import QUESTION, Spark
from voice.conftest import FakeEngines


@pytest.fixture
def spark(tmp_path):
    FakeEngines.instance = None
    s = Spark(tmp_path)
    s.server.voice_hub = VoiceHub(s.instance, engines_factory=FakeEngines)
    yield s
    s.close()


@pytest.fixture
def family(spark):
    """Phone A (enrolled as a signed node) and tablet B (a plain device) of one family."""
    a = spark.req("pair", {"code": "invite"})[1]["key"]
    b = spark.req("pair", {"code": spark.req("link", {}, a)[1]["code"]})[1]["key"]
    enrol(spark, a, "phone")
    return a, b


def start(spark, token):
    status, out = spark.req("voice_session", {"action": "start", "device": "phone123"}, token)
    assert status == 200, out
    return out["session"]


def say(spark, token, text, session=None):
    """The reply text to one typed command in this device's voice session."""
    session = session or start(spark, token)
    seen = spark.req("voice_events", {"session": session, "since": 0, "wait": 0}, token)[1]["events"]
    since = max((e["seq"] for e in seen), default=0)                   # replies to earlier commands are not this one's
    status, out = spark.req("voice_text", {"session": session, "text": text}, token)
    assert status == 200, out
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = spark.req("voice_events", {"session": session, "since": since, "wait": 2}, token)[1]["events"]
        for event in events:
            since = max(since, event["seq"])
            if event["type"] == "reply":
                return event["reply"]["text"]
    raise AssertionError("no reply")


def task(spark, token, ident):
    return spark.req("research_get", {"id": ident}, token)[1]


def tasks(spark, token):
    return [t["id"] for t in spark.req("research_list", {}, token)[1]["tasks"]]


def test_a_family_device_cannot_cancel_or_continue_by_voice_a_session_a_node_owns(spark, family):
    a, b = family
    ident = spark.req("research_submit", {"objective": QUESTION}, a)[1]["id"]
    assert spark.req("research_session", {"id": ident, "action": "take"}, a)[1]["you"] is True
    assert spark.req("research_cancel", {"id": ident}, b)[1]["error"] == "owned"        # HTTP refuses B
    assert say(spark, b, "cancel the research") == vbridge.SESSION_OWNED                 # and so does voice
    assert task(spark, a, ident)["status"] == "queued" and not task(spark, a, ident)["cancel_requested"]
    assert spark.req("research_cancel", {"id": ident}, a)[1]["status"] == "cancelled"   # the owner, signed
    assert say(spark, b, "continue the previous investigation") == vbridge.SESSION_OWNED
    assert tasks(spark, b) == [ident]                                                    # nothing requeued
    assert spark.req("research_session", {"id": ident, "action": "release"}, a)[0] == 200   # released: B may
    assert say(spark, b, "continue the previous investigation").startswith("Continuing:")
    assert len(tasks(spark, b)) == 2


def test_a_signed_node_changes_research_only_with_its_signed_controls(spark, family):
    """A stolen node credential without the key: HTTP answers signature_required, and voice refuses too. Reading
    by voice stays open."""
    a, _ = family
    ident = spark.req("research_submit", {"objective": QUESTION}, a)[1]["id"]
    assert spark.req("research_cancel", {"id": ident}, a, sign=False) == (401, {"error": "signature_required"})
    session = start(spark, a)
    assert say(spark, a, "cancel the research", session) == vbridge.NODE_SIGNED
    assert task(spark, a, ident)["status"] == "queued"
    assert say(spark, a, "research whether Zephyr 4.2 was released", session) == vbridge.NODE_SIGNED
    assert tasks(spark, a) == [ident]
    assert QUESTION[:40] in say(spark, a, "what was I researching", session)
    spark.req("research_cancel", {"id": ident}, a)                                       # signed: accepted
    assert say(spark, a, "continue the previous investigation", session) == vbridge.NODE_SIGNED
    assert tasks(spark, a) == [ident]


def test_a_voice_session_belongs_to_the_device_that_started_it(spark, family):
    """A node's credential cannot borrow a plain family device's voice session to act as that device."""
    a, b = family
    theirs = start(spark, b)
    assert spark.req("voice_text", {"session": theirs, "text": "cancel the research"}, a)[0] == 404
    assert spark.req("voice_events", {"session": theirs, "since": 0, "wait": 0}, a)[0] == 404


def test_a_plain_device_still_changes_unowned_research_by_voice(spark, family):
    _, b = family
    ident = spark.req("research_submit", {"objective": QUESTION}, b)[1]["id"]
    assert say(spark, b, "cancel the research").startswith("Cancelled the research")
    assert task(spark, b, ident)["status"] == "cancelled"
