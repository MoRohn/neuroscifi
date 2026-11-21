"""Transcripts, capture ownership, retention, modes, barge-in and the Presence snapshot contract."""
from contextlib import closing
import threading
import time

import pytest

from neuroscifi.instance import schema, store
from neuroscifi.instance.errors import Conflict, NotFound, ValidationError
from neuroscifi.voice import config as vconfig, state as vstate, transcripts as tx
from neuroscifi.webcortex import orchestrator, scheduler

from .conftest import OWNER, pcm, pocket_principal, wait_for


def test_migration_is_forward_only_and_creates_voice_tables(instance):
    with closing(store.connect(instance)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"voice_sessions", "voice_segments", "voice_capture", "voice_audio"} <= names
    assert "voice_sessions" in schema.MIGRATIONS[-1]


def test_transcript_model_order_status_authority_correction_search(instance):
    with closing(store.connect(instance)) as db:
        sid = tx.start_session(db, "owner", "console:x", "voice_text", 100.0)
        tx.upsert(db, sid, "u1", speaker="user", source="asr", status="provisional", text="what is", now=101.0)
        tx.upsert(db, sid, "r1", speaker="neuroscifi", source="model", status="final", text="Answer [1]", now=103.0)
        seg = tx.upsert(db, sid, "u1", speaker="user", source="asr", status="final", text="what is 50% of_it",
                        now=102.0, ended_at=102.0)
        assert seg["seq"] == 1 and seg["status"] == "final" and seg["authority"] == "approximate"
        rows = tx.listing(db, session_id=sid)
        assert [r["utterance"] for r in rows] == ["u1", "r1"]
        assert rows[1]["authority"] == "authoritative" and rows[1]["label"] == "NeuroSciFi"
        fixed = tx.correct(db, seg["id"], "what is fifty percent of it", 104.0)
        assert fixed["text"] == "what is 50% of_it" and fixed["display_text"] == "what is fifty percent of it"
        with pytest.raises(Conflict):
            tx.correct(db, rows[1]["id"], "rewrite the answer", 105.0)
        with pytest.raises(NotFound):
            tx.correct(db, seg["id"], "x", 105.0, principal="pocket:other")
        assert [r["id"] for r in tx.search(db, "fifty")] == [seg["id"]]
        assert tx.search(db, "50%") and not tx.search(db, "5_%")          # LIKE wildcards are escaped
        assert tx.search(db, "fifty", principal="pocket:other") == []
        out = tx.forget_session(db, sid)
        assert out["segments"] == 2 and tx.listing(db) == []


def test_capture_ownership_one_device_at_a_time(instance):
    with closing(store.connect(instance)) as db:
        assert tx.acquire(db, "console:a", "Console", "owner", "s1", 100.0)["granted"]
        refused = tx.acquire(db, "pocket:p:b", "Phone", "pocket:p", "s2", 101.0)
        assert refused == {"granted": False, "holder": {"device": "console:a", "label": "Console",
                                                         "expires_in": 19.0}}
        assert tx.renew(db, "console:a", 110.0) and not tx.renew(db, "pocket:p:b", 110.0)
        handed = tx.acquire(db, "pocket:p:b", "Phone", "pocket:p", "s2", 111.0, handoff=True)
        assert handed["granted"] and handed["handed_off_from"]["device"] == "console:a"
        assert not tx.renew(db, "console:a", 112.0) and not tx.holds(db, "console:a", 112.0)
        assert tx.acquire(db, "console:a", "Console", "owner", "s1", 112.0 + tx.LEASE_SECONDS)["granted"]  # expired
        assert tx.release(db, "console:a") and tx.holder(db, 200.0) is None


def test_retention_off_by_default_and_ttl_bounded(instance):
    with closing(store.connect(instance)) as db:
        cfg = vconfig.get(db)
        assert cfg["retain_audio"] is False and cfg["audio_ttl_hours"] == 24
        with pytest.raises(ValidationError):
            vconfig.update(db, {"audio_ttl_hours": 500})
        with pytest.raises(ValidationError):
            vconfig.update(db, {"retain_audio": True}, owner=False)
        with pytest.raises(ValidationError):
            vconfig.update(db, {"asr_url": "http://10.0.0.5:18431"})   # speech never leaves the host
        sid = tx.start_session(db, "owner", "console:x", "voice_text", 100.0)
        seg = tx.upsert(db, sid, "u1", speaker="user", source="asr", status="final", text="hi there you", now=100.0)
        vconfig.update(db, {"retain_audio": True, "audio_ttl_hours": 1})
        tx.retain_audio(db, seg["id"], b"\x00\x01" * 10, 16000, 100.0, 1)
        assert tx.sweep(db, 100.0 + 3599) == 0 and tx.audio_count(db) == 1
        assert tx.sweep(db, 100.0 + 3600) == 1 and tx.audio_count(db) == 0
        tx.retain_audio(db, seg["id"], b"\x00\x01", 16000, 200.0, 1)
        vconfig.update(db, {"retain_audio": False})
        assert tx.sweep(db, 201.0) == 1 and tx.audio_count(db) == 0     # switching retention off deletes it all


def start(hub, principal=OWNER, device="dev1234", mode=None, consent=True, mic=True):
    s = hub.start(principal, principal.device_prefix + device, principal.label, mode)
    if consent:
        hub.consent(s["session"], principal, True)
    if mic:
        assert hub.capture(s["session"], principal, "acquire")["granted"]
    return s["session"]


def events(hub, sid, since=0, kind=None, wait=0.2):
    out = hub.events(sid, OWNER, since, wait=wait)["events"]
    return [e for e in out if kind is None or e["type"] == kind]


def test_audio_refused_without_consent_lease_or_in_text_mode(hub):
    s = hub.start(OWNER, "console:dev1", "Console", None)["session"]
    with pytest.raises(Conflict, match="consent"):
        hub.audio(s, OWNER, "u0001", 0, False, pcm(0.1))
    hub.consent(s, OWNER, True)
    with pytest.raises(Conflict, match="hold the microphone"):
        hub.audio(s, OWNER, "u0001", 0, False, pcm(0.1))
    hub.capture(s, OWNER, "acquire")
    assert hub.audio(s, OWNER, "u0001", 0, False, pcm(0.1))["status"] == "buffered"
    hub.capture(s, OWNER, "release")                       # mic off: server refuses anything that still arrives
    with pytest.raises(Conflict):
        hub.audio(s, OWNER, "u0001", 1, False, pcm(0.1))
    hub.set_mode(s, OWNER, "text_only")
    with pytest.raises(Conflict, match="text-only"):
        hub.audio(s, OWNER, "u0002", 0, False, pcm(0.1))
    with pytest.raises(Conflict, match="text-only"):
        hub.capture(s, OWNER, "acquire")
    assert hub.metrics["refused_audio"] == 4


def test_second_device_refused_then_explicit_handoff(hub):
    a = start(hub, device="consoleA")
    phone = pocket_principal()
    b = start(hub, phone, device="phoneB", mic=False)
    assert hub.capture(b, phone, "acquire")["granted"] is False
    with pytest.raises(Conflict, match="confirmation"):
        hub.capture(b, phone, "handoff", handoff_confirm="nope")
    confirm = hub.capture(b, phone, "request_handoff")["confirm"]
    assert hub.capture(b, phone, "handoff", handoff_confirm=confirm["id"])["granted"]
    assert hub.capture(a, OWNER, "renew")["granted"] is False      # the first device learns it lost the mic
    with pytest.raises(Conflict):
        hub.audio(a, OWNER, "u0001", 0, False, pcm(0.1))


def test_utterance_streaming_provisional_final_and_reply(hub):
    s = start(hub)
    hub.fake.asr.script = ["show me the evidence"]
    assert hub.audio(s, OWNER, "u0001", 0, False, pcm(0.3))["status"] == "buffered"
    prov = hub.audio(s, OWNER, "u0001", 1, False, pcm(0.5))
    assert prov["status"] == "provisional"
    with pytest.raises(Conflict, match="expected chunk"):
        hub.audio(s, OWNER, "u0001", 5, False, pcm(0.1))
    final = hub.audio(s, OWNER, "u0001", 2, True, pcm(0.2))
    assert final == {"status": "final", "text": "show me the evidence", "segment": prov["segment"], "asr_ms": 10.0}
    reply = wait_for(lambda: events(hub, s, kind="reply"))[0]
    assert reply["reply"]["intent"] == "show_evidence" and reply["turn"]["sentences"] == 1
    assert reply["segment"]["authority"] == "authoritative" and reply["segment"]["detail"]["responding_to"] == "u0001"
    wav = hub.speech(s, OWNER, reply["turn"]["id"], 0)
    assert wav.startswith(b"RIFF") and vstate.snapshot()["speaking"] is True
    with closing(store.connect(hub.db_path)) as db:
        assert tx.audio_count(db) == 0                     # raw audio not stored by default
        segs = tx.listing(db, session_id=s)
    assert [(x["speaker"], x["status"]) for x in segs] == [("user", "final"), ("neuroscifi", "final")]


def test_retention_on_keeps_final_utterance_with_ttl(hub):
    s = start(hub)
    hub.settings(OWNER, {"retain_audio": True, "audio_ttl_hours": 2})
    hub.fake.asr.script = ["hello there friend"]
    hub.audio(s, OWNER, "u0001", 0, True, pcm(0.5))
    with closing(store.connect(hub.db_path)) as db:
        row = db.execute("SELECT length(pcm), expires_at-created_at FROM voice_audio").fetchone()
    assert row == (16000, 7200.0)


def test_noise_and_short_utterances_dropped(hub):
    s = start(hub)
    hub.fake.asr.script = ["Thank you."]
    assert hub.audio(s, OWNER, "u0001", 0, True, pcm(0.5))["status"] == "empty"
    assert hub.audio(s, OWNER, "u0002", 0, True, pcm(0.1))["status"] == "too_short"
    with closing(store.connect(hub.db_path)) as db:
        assert tx.listing(db, session_id=s) == []


def test_asr_down_reports_error_and_text_still_works(hub):
    s = start(hub)
    hub.fake.asr.fail = "unavailable"
    assert hub.audio(s, OWNER, "u0001", 0, True, pcm(0.5))["status"] == "asr_failed"
    assert events(hub, s, kind="error")[0]["what"] == "asr"
    hub.text(s, OWNER, "help")
    assert wait_for(lambda: events(hub, s, kind="reply"))[0]["reply"]["intent"] == "help"


def test_barge_in_marks_reply_interrupted_and_resume_continues(hub):
    s = start(hub)
    hub.text(s, OWNER, "help")
    reply = wait_for(lambda: events(hub, s, kind="reply"))[0]
    turn = reply["turn"]["id"]
    out = hub.interrupt(s, OWNER, turn, 0, "barge_in", stop_ms=312)
    assert out == {"interrupted": True, "spoken_until": 0}
    snap = vstate.snapshot()
    assert snap["speaking"] is False and snap["interrupted_at"] is not None
    seg = events(hub, s, kind="interrupted")[0]["segment"]
    assert seg["status"] == "interrupted" and seg["detail"]["spoken_until"] == 0
    with pytest.raises(Conflict):
        hub.speech(s, OWNER, turn, 0, wait=0.5)
    assert hub.interrupt(s, OWNER, turn, 0, "overlap") == {"overlap": True}
    hub.text(s, OWNER, "go on")
    resumed = wait_for(lambda: [e for e in events(hub, s, kind="reply") if e["reply"]["intent"] == "resume_speaking"
                                and e["turn"]])
    assert resumed[0]["reply"]["speech"][0].startswith("You can ask a question")
    assert hub.metrics["barge_ins"] == 1 and hub.metrics["overlaps"] == 1


def test_mode_switching_preserves_context_and_controls_audio(hub, instance):
    s = start(hub)
    hub.text(s, OWNER, "Research the history of the Zephyr compiler")
    wait_for(lambda: [e for e in events(hub, s, kind="reply") if e["reply"]["intent"] == "research"])
    with closing(store.connect(instance)) as db:
        task_id = tx.session(db, s)["context"]["last_task_id"]
    for mode in ("voice_in_text_out", "text_only", "voice_text", "text_only", "voice_in_text_out", "voice_text"):
        hub.set_mode(s, OWNER, mode)
        assert vstate.snapshot()["mode"] == mode
        with closing(store.connect(instance)) as db:
            assert tx.session(db, s)["context"]["last_task_id"] == task_id
    hub.set_mode(s, OWNER, "voice_in_text_out")
    since = hub.events(s, OWNER, 0, wait=0)["seq"]
    hub.text(s, OWNER, "show the evidence")
    reply = wait_for(lambda: events(hub, s, since, kind="reply"))[0]
    assert reply["turn"] is None and reply["reply"]["text"].startswith("Evidence for:")  # mode B: text, no audio
    assert reply["reply"]["task_id"] == task_id
    hub.set_mode(s, OWNER, "text_only")
    assert vstate.snapshot()["mic_capturing"] is False
    with closing(store.connect(instance)) as db:
        assert not tx.holds(db, "console:dev1234", time.time())  # text-only released the microphone


def test_voice_command_switches_mode(hub):
    s = start(hub)
    hub.text(s, OWNER, "Switch to text only")
    wait_for(lambda: events(hub, s, kind="mode"))
    assert hub.sessions[s].mode == "text_only" and vstate.snapshot()["mode"] == "text_only"


def test_confirmation_via_route_and_expiry(hub):
    s = start(hub)
    hub.text(s, OWNER, "Turn on autonomous research")
    pending = wait_for(lambda: events(hub, s, kind="reply"))[0]["reply"]["confirm"]
    with pytest.raises(Conflict):
        hub.confirm(s, OWNER, "wrong", True)
    assert hub.confirm(s, OWNER, pending["id"], True) == {"done": True, "intent": "enable_autonomy"}
    hub.text(s, OWNER, "Turn on autonomous research")
    later = wait_for(lambda: [e for e in events(hub, s, kind="reply") if e["reply"]["confirm"]
                              and e["reply"]["confirm"]["id"] != pending["id"]])[0]["reply"]["confirm"]
    hub.sessions[s].pending["expires_at"] = time.time() - 1
    with pytest.raises(Conflict, match="expired"):
        hub.confirm(s, OWNER, later["id"], True)


def test_forget_conversation_by_voice(hub, instance):
    s = start(hub)
    hub.text(s, OWNER, "Forget this conversation")
    pending = wait_for(lambda: events(hub, s, kind="reply"))[0]["reply"]["confirm"]
    hub.confirm(s, OWNER, pending["id"], True)
    new = wait_for(lambda: events(hub, s, kind="forgotten"))[0]["new_session"]
    with closing(store.connect(instance)) as db:
        assert db.execute("SELECT count(*) FROM voice_sessions WHERE id=?", (s,)).fetchone()[0] == 0
        assert tx.listing(db, session_id=s) == []
    assert new in hub.sessions


def test_snapshot_contract(hub):
    assert vstate.snapshot() == {"mode": "text_only", "session_active": False, "capture_device": None,
                                 "mic_capturing": False, "output_muted": False, "speaking": False,
                                 "user_speaking": False, "interrupted_at": None, "updated_at": None}
    s = start(hub)
    hub.client_status(s, OWNER, mic_capturing=True, user_speaking=True)
    snap = vstate.snapshot()
    assert set(snap) == set(vstate.FIELDS) and snap["mic_capturing"] and snap["capture_device"] == "Owner console"
    hub.client_status(s, OWNER, output_muted=True, speaking=True)
    assert vstate.snapshot()["speaking"] is False                 # muted output never claims audible speech
    hub.capture(s, OWNER, "release")
    with pytest.raises(Conflict):
        hub.client_status(s, OWNER, mic_capturing=True)         # cannot claim capture without the lease
    hub.stop(s, OWNER)
    assert vstate.snapshot()["session_active"] is False


def test_sessions_are_scoped_to_principal(hub):
    s = start(hub)
    with pytest.raises(NotFound):
        hub.text(s, pocket_principal(), "help")
    hub.text(s, OWNER, "help")
    wait_for(lambda: events(hub, s, kind="reply"))
    phone = pocket_principal()
    assert hub.transcripts(phone)["segments"] == []               # a Pocket device sees only its own transcript
    assert len(hub.transcripts(OWNER)["segments"]) == 2


def test_long_research_task_survives_session_end_and_reports(hub, instance, monkeypatch):
    """A real queued research task: the voice session ends, the worker finishes it, the answer still lands in the
    transcript (the task belongs to the research system, not the voice session)."""
    from neuroscifi.voice import hub as vhub
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    s = start(hub)
    hub.text(s, OWNER, "Research the Zephyr compiler release")
    ack = wait_for(lambda: [e for e in events(hub, s, kind="reply") if e["reply"]["intent"] == "research"])[0]
    task_id = ack["reply"]["task_id"]
    hub.stop(s, OWNER)                                            # voice session ends; task keeps its queue slot
    with closing(store.connect(instance)) as db:
        assert orchestrator.get(db, task_id)["status"] == "queued"
        orchestrator.cancel(db, task_id, time.time())           # finish it quickly (cancelled is terminal)
    with closing(store.connect(instance)) as db:
        seg = wait_for(lambda: db.execute("SELECT text FROM voice_segments WHERE task_id=? AND speaker='neuroscifi' "
                                          "AND text LIKE '%cancelled%'", (task_id,)).fetchone())
    assert "was cancelled" in seg[0]


def test_research_answer_delivered_from_real_worker(hub, instance, monkeypatch, tmp_path):
    """Offline web fixture + the real scheduler: the follower delivers the report-derived answer as speech."""
    from neuroscifi.voice import hub as vhub
    from neuroscifi.webcortex.fixtureweb import FixtureWeb
    from neuroscifi.webcortex.providers.corpus import CorpusProvider
    monkeypatch.setattr(vhub, "FOLLOW_POLL", 0.05)
    pages = {("news-one.example", "/z"): "The Zephyr compiler version 4.2.0 was released on 17 November 2026.",
             ("tech-two.example", "/z"): "Zephyr compiler version 4.2.0 was released on 17 November 2026 by the team."}
    web = FixtureWeb().start()
    try:
        for (host, path), text in pages.items():
            web.add(host, path, f"<html><head><title>Zephyr</title></head><body><article><p>{text}</p>"
                                f"<p>Other notes about the compiler release.</p></article></body></html>")
        corpus = CorpusProvider([{"url": f"http://{h}{p}", "title": "Zephyr", "snippet": t[:120]}
                                 for (h, p), t in pages.items()])
        s = start(hub)
        hub.text(s, OWNER, "When was Zephyr compiler 4.2.0 released?")
        wait_for(lambda: [e for e in events(hub, s, kind="reply") if e["reply"]["intent"] == "research"])
        worker = threading.Thread(target=scheduler.work, args=(instance,),
                                  kwargs={"once": True, "fixture": web, "providers": [corpus]})
        worker.start()
        worker.join(60)
        done = wait_for(lambda: [e for e in events(hub, s, kind="reply")
                                 if e["reply"]["intent"] == "research_result"], timeout=10)[0]
    finally:
        web.stop()
    r = done["reply"]
    assert r["source"] in ("model", "template") and r["task_id"] and r["text"]
    assert done["turn"] and r["speech"]                               # mode A: spoken concisely
    assert any(e["type"] == "task_done" for e in events(hub, s))
