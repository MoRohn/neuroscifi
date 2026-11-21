"""Cognitive State Aggregator (presence/aggregator.py): every activity comes from a real row, family scope never
leaks, voice is unknown without Lane V's module, the version changes only with what the client renders."""
from contextlib import closing
import sys
import time
import types

import pytest

from neuroscifi.instance import store
from neuroscifi.presence import activity, aggregator
from neuroscifi.webcortex import orchestrator, state

NOW = time.time()


@pytest.fixture
def inst(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, NOW - 7200)) as db:
        state.set_config(db, {"enabled": True, "pocket_research": True}, NOW - 7200)
    db = store.connect(path)
    yield db, path
    db.close()


def run(db, task_id, status, *, at=NOW, lease=NOW + 600, events=(), origin=None):
    """Put a task in a running stage as the worker would (status, stage, lease, events)."""
    db.execute("UPDATE web_tasks SET status=?, stage=?, started_at=?, updated_at=?, lease_holder='w:1', "
               "lease_expires_at=? WHERE id=?", (status, status, at - 5, at, lease, task_id))
    if origin:
        db.execute("UPDATE web_tasks SET origin=? WHERE id=?", (origin, task_id))
    state.log_event(db, task_id, "stage", {"stage": status}, at - 4, stage=status)
    for kind, detail in events:
        state.log_event(db, task_id, kind, detail, at - 1, stage=status)


def derive(db, path, **kw):
    return aggregator.derive(db, path, NOW, **kw)


def test_fresh_instance_rests_and_claims_nothing(inst):
    db, path = inst
    s = derive(db, path)
    assert s["schema"] == aggregator.SCHEMA and s["scope"] == "owner"
    assert s["primary"]["activity"] == "resting" and s["activities"] == []
    assert s["condition"] == ["healthy"] and s["intensity"] == 0.0
    assert s["primary"]["progress"] is None


@pytest.mark.parametrize("status,events,expected", [
    ("discovering", [("query", {"text": "q"})], "searching"),
    ("reading", [("fetch", {"url": "http://a.example/"})], "reading"),
    ("evaluating", [("claim", {"n": 1})], "verifying"),
    ("synthesizing", [], "generating"),
    ("synthesizing", [("synthesized", {"answer": 2})], "consolidating"),   # no model configured: consolidation next
    ("synthesizing", [("synthesized", {}), ("decision", {"reasoning": "ok"})], "consolidating"),
])
def test_stage_and_newest_event_choose_the_activity(inst, status, events, expected):
    db, path = inst
    task = orchestrator.submit(db, "Was the Zephyr compiler version 4.2.0 released?", NOW - 60, origin="console")
    run(db, task["id"], status, events=events)
    s = derive(db, path)
    p = s["primary"]
    assert p["activity"] == expected and p["task_id"] == task["id"] and p["priority"] == "foreground"
    assert p["step"] == {"index": aggregator.STAGES.index(status) + 1, "of": 4}
    assert p["progress"] is None   # a stage step, never a percentage
    assert "web_research" in p["secondary"]


def test_contradictions_turn_verification_into_comparison(inst):
    db, path = inst
    task = orchestrator.submit(db, "Is the Zephyr compiler faster than Okapi?", NOW - 60, origin="console")
    run(db, task["id"], "evaluating")
    db.execute("INSERT INTO web_claim_links(task_id,src,dst,kind,created_at) VALUES(?,?,?,?,?)",
               (task["id"], "c1", "c2", "contradicts", NOW))
    p = derive(db, path)["primary"]
    assert p["activity"] == "comparing" and "evidence_verification" in p["secondary"]


def test_queued_without_worker_and_expired_lease(inst):
    db, path = inst
    task = orchestrator.submit(db, "Zephyr compiler releases", NOW - 120, origin="console")
    p = derive(db, path, worker_alive=False)["primary"]
    assert p["activity"] == "waiting" and "no research worker" in p["detail"]
    run(db, task["id"], "reading", lease=NOW - 1)
    s = derive(db, path)
    assert s["primary"]["activity"] == "recovering" and "recovering" in s["condition"]


def test_background_work_ranks_below_foreground_and_is_marked_autonomous(inst):
    db, path = inst
    bg = orchestrator.submit(db, "Okapi conservation status", NOW - 90, origin="curiosity")
    fg = orchestrator.submit(db, "Zephyr compiler 4.2 release date", NOW - 30, origin="console")
    run(db, bg["id"], "reading")
    run(db, fg["id"], "discovering")
    s = derive(db, path)
    assert [c["task_id"] for c in s["activities"][:2]] == [fg["id"], bg["id"]]
    assert s["activities"][1]["priority"] == "background"
    assert "autonomous_research" in s["interaction"] and "busy" in s["condition"]


def test_open_questions_are_user_critical_only_while_fresh(inst):
    db, path = inst
    db.execute("INSERT INTO pending(kind,scope,question,created_at) VALUES('ask','web:research:x','May I?',?)",
               (NOW - 30,))
    s = derive(db, path)
    assert s["primary"]["priority"] == "user_critical" and s["primary"]["concept"] == "asking"
    assert "awaiting_permission" in s["condition"] and "tool_awaiting_confirmation" in s["interaction"]
    db.execute("UPDATE pending SET created_at=?", (NOW - aggregator.FRESH_QUESTION - 5,))
    s = derive(db, path)
    assert s["primary"]["priority"] == "idle" and "awaiting_permission" in s["condition"]


def test_finished_and_failed_tasks(inst):
    db, path = inst
    done = orchestrator.submit(db, "Zephyr compiler latest", NOW - 60, origin="console")
    db.execute("UPDATE web_tasks SET status='completed', finished_at=? WHERE id=?", (NOW - 2, done["id"]))
    s = derive(db, path)
    assert s["primary"]["activity"] == "responding" and s["primary"]["concept"] == "achievement"
    db.execute("UPDATE web_tasks SET status='failed', finished_at=?, error='boom' WHERE id=?", (NOW - 2, done["id"]))
    s = derive(db, path)
    assert "error" in s["condition"] and s["primary"]["concept"] == "error"
    db.execute("UPDATE web_tasks SET finished_at=? WHERE id=?", (NOW - 30, done["id"]))
    s = derive(db, path)
    assert "error" in s["condition"] and s["primary"]["activity"] == "idle"


def test_family_scope_sees_only_its_own_tasks(inst):
    db, path = inst
    mine = state.family_digest(db, "family-a")
    theirs = state.family_digest(db, "family-b")
    a = orchestrator.submit(db, "Zephyr compiler 4.2 notes", NOW - 30, origin="pocket", family=mine)
    b = orchestrator.submit(db, "Secret project of family B", NOW - 30, origin="pocket", family=theirs)
    run(db, b["id"], "reading")
    db.execute("INSERT INTO pending(kind,scope,question,created_at) VALUES('ask','web:x','owner only',?)", (NOW,))
    s = derive(db, path, family=mine)
    assert s["scope"] == "family" and [c["task_id"] for c in s["activities"]] == [a["id"]]
    assert "Secret" not in str(s) and "awaiting_permission" not in s["condition"] and s["voice"] is None
    s_owner = derive(db, path)
    assert {c["task_id"] for c in s_owner["activities"]} >= {a["id"], b["id"]}


def test_voice_unknown_without_module_and_mapped_with_it(inst, monkeypatch):
    db, path = inst
    monkeypatch.setitem(sys.modules, "neuroscifi.voice", None)   # Lane V absent
    s = derive(db, path)
    assert s["voice"] is None and s["sources"]["voice"] == "absent"
    snap = {"mode": "voice_text", "session_active": True, "capture_device": "mic", "mic_capturing": True,
            "output_muted": False, "speaking": False, "user_speaking": True, "interrupted_at": NOW - 1,
            "updated_at": NOW}
    pkg = types.ModuleType("neuroscifi.voice")
    mod = types.ModuleType("neuroscifi.voice.state")
    mod.snapshot = lambda: dict(snap)
    pkg.state = mod
    monkeypatch.setitem(sys.modules, "neuroscifi.voice", pkg)
    monkeypatch.setitem(sys.modules, "neuroscifi.voice.state", mod)
    s = derive(db, path)
    assert s["primary"]["activity"] == "listening" and s["primary"]["priority"] == "communication"
    assert {"user_speaking", "user_interruption"} <= set(s["interaction"])
    snap.update(mic_capturing=False, speaking=True, output_muted=True)
    s = derive(db, path)
    assert s["primary"]["activity"] != "responding" and "voice_muted" in s["interaction"]   # muted: no speech shown
    snap.update(mode="text_only", speaking=True, output_muted=False)
    s = derive(db, path)
    assert "text_only" in s["interaction"] and not any(c["subsystem"] == "voice" for c in s["activities"])


def test_inflight_registry_counts_streamed_tokens(inst):
    db, path = inst
    with activity.track("generating", subsystem="reasoning", purpose="presence_scene") as tokens:
        for _ in range(50):
            tokens()
        s = derive(db, path)
        assert s["primary"]["activity"] == "generating" and s["signals"]["tokens_30s"] == 50
        assert s["signals"]["inflight_inference"] == 1
    assert derive(db, path)["signals"]["inflight_inference"] == 0


def test_version_ignores_timestamps_and_counters_but_not_state(inst):
    db, path = inst
    a = aggregator.derive(db, path, NOW)
    b = aggregator.derive(db, path, NOW + 1)
    assert a["version"] == b["version"] and a["ts"] != b["ts"]
    orchestrator.submit(db, "Zephyr", NOW, origin="console")
    assert aggregator.derive(db, path, NOW + 2)["version"] != a["version"]


def test_unavailable_state_is_idle(inst):
    s = aggregator.unavailable("family", "Research is off")
    assert s["primary"]["activity"] == "idle" and s["sources"]["webcortex"] == "off" and s["version"]


@pytest.mark.parametrize("origin", ["voice", "refresh-owner:7"])
def test_a_spoken_or_owner_requested_task_is_foreground_not_autonomous(inst, origin):
    """T2-02: a question asked by voice (origin 'voice') is the owner's own research, never shown as autonomous
    background research; curiosity still is."""
    db, path = inst
    task = orchestrator.submit(db, "When did the Zephyr compiler 4.2 ship?", NOW - 30, origin="console")
    db.execute("UPDATE web_tasks SET origin=? WHERE id=?", (origin, task["id"]))
    run(db, task["id"], "reading")
    s = derive(db, path)
    assert s["primary"]["task_id"] == task["id"] and s["primary"]["priority"] == "foreground"
    assert s["primary"]["origin"] == "owner" and "autonomous_research" not in s["interaction"]
    db.execute("UPDATE web_tasks SET status='completed', finished_at=? WHERE id=?", (NOW - 2, task["id"]))
    s = derive(db, path)
    assert "background_task_completed" not in s["interaction"]
    bg = orchestrator.submit(db, "Okapi conservation status", NOW - 20, origin="curiosity")
    run(db, bg["id"], "reading")
    assert "autonomous_research" in derive(db, path)["interaction"]


@pytest.mark.parametrize("origin,cls", [("pocket-voice", "pocket"), ("pocket", "pocket"), ("mcp", "owner"),
                                        ("cli", "owner")])
def test_every_user_origin_is_foreground_not_autonomous(inst, origin, cls):
    """R2-02: a question spoken on a Pocket device (origin 'pocket-voice') was shown in the owner's Presence as
    autonomous research. Foreground comes from orchestrator.user_initiated, the one list of user origins."""
    db, path = inst
    assert orchestrator.user_initiated(origin)
    task = orchestrator.submit(db, "When did the Zephyr compiler 4.2 ship?", NOW - 30, origin="console")
    run(db, task["id"], "reading", origin=origin)
    s = derive(db, path)
    assert s["primary"]["priority"] == "foreground" and s["primary"]["origin"] == cls
    assert "autonomous_research" not in s["interaction"]
    for autonomous in ("curiosity", "refresh:3", "learner", "bench"):
        assert not orchestrator.user_initiated(autonomous)


def test_voice_session_with_unreachable_speech_engines_is_not_healthy(inst):
    """R2-08: with ASR and TTS unreachable during a voice session, Presence said "healthy" and voice "ok". The hub's
    measured engine health (voice.state.engines) now makes the condition degraded and says which engines."""
    from neuroscifi.voice import state as vstate
    db, path = inst
    vstate.reset()
    try:
        vstate.update("s1", owner=True, mode="voice_text", session_active=True)
        vstate.set_engines({"asr": {"state": "ready"}, "tts": {"state": "ready"}})
        s = derive(db, path)
        assert s["condition"] == ["healthy"] and s["sources"]["voice"] == "ok"
        vstate.set_engines({"asr": {"state": "unavailable"}, "tts": {"state": "unavailable"}})
        s = derive(db, path)
        assert "network_degraded" in s["condition"] and s["sources"]["voice"] == "engines_unavailable"
        assert s["sources"]["voice_engines_down"] == "asr,tts"
        vstate.update("s1", owner=True, mode="voice_in_text_out")       # text out: only recognition is needed
        vstate.set_engines({"asr": {"state": "ready"}, "tts": {"state": "unavailable"}})
        assert derive(db, path)["condition"] == ["healthy"]
        vstate.update("s1", owner=True, mode="text_only")                # text only needs no speech engine
        vstate.set_engines({"asr": {"state": "unavailable"}, "tts": {"state": "unavailable"}})
        assert derive(db, path)["condition"] == ["healthy"]
    finally:
        vstate.reset()
