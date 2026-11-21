"""Transcripts, capture ownership, retention, modes, barge-in and the Presence snapshot contract."""
from contextlib import closing
import threading
import time

import pytest

from neuroscifi.instance import schema, store
from neuroscifi.instance.errors import Conflict, NotFound, ValidationError
from neuroscifi.voice import config as vconfig, state as vstate, transcripts as tx
from neuroscifi.webcortex import orchestrator, scheduler

from neuroscifi.voice.bridge import Principal

from .conftest import OWNER, pcm, pocket_principal, wait_for


def test_migration_is_forward_only_and_creates_voice_tables(instance):
    with closing(store.connect(instance)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"voice_sessions", "voice_segments", "voice_capture", "voice_audio"} <= names
    assert "voice_sessions" in schema.MIGRATIONS[8]     # version 9 (version 10 is controlled autonomy)


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
    """The owner takes the microphone from a Pocket device on the owner's own confirmation."""
    phone = pocket_principal()
    b = start(hub, phone, device="phoneB")
    a = start(hub, device="consoleA", mic=False)
    assert hub.capture(a, OWNER, "acquire")["granted"] is False
    with pytest.raises(Conflict, match="confirmation"):
        hub.capture(a, OWNER, "handoff", handoff_confirm="nope")
    confirm = hub.capture(a, OWNER, "request_handoff")["confirm"]
    assert hub.capture(a, OWNER, "handoff", handoff_confirm=confirm["id"])["granted"]
    assert hub.capture(b, phone, "renew")["granted"] is False      # the first device learns it lost the mic
    with pytest.raises(Conflict):
        hub.audio(b, phone, "u0001", 0, False, pcm(0.1))


def test_pocket_handoff_from_the_owner_needs_the_owners_consent(hub):
    """M2: a Pocket device cannot move the owner's microphone by confirming its own request; the holder decides."""
    a = start(hub, device="consoleA")
    phone = pocket_principal()
    b = start(hub, phone, device="phoneB", mic=False)
    assert hub.capture(b, phone, "acquire") == {"granted": False, "holder": {"label": "another device"}}
    out = hub.capture(b, phone, "request_handoff")
    assert "confirm" not in out and out["requested"]["id"]
    with pytest.raises(Conflict):
        hub.capture(b, phone, "handoff", handoff_confirm=out["requested"]["id"])
    hub.sessions[b].pending = {"id": "self", "action": "handoff", "summary": "x", "expires_at": time.time() + 60}
    with pytest.raises(Conflict, match="must allow"):              # even a self-held confirmation is not enough
        hub.capture(b, phone, "handoff", handoff_confirm="self")
    with pytest.raises(Conflict):                                  # only the holder can grant it
        hub.capture(b, phone, "grant_handoff", handoff_confirm=out["requested"]["id"])
    assert hub.capture(a, OWNER, "renew")["granted"]               # the owner still holds it ...
    request = hub.capture(a, OWNER, "renew")["handoff_request"]    # ... and is asked
    assert request["id"] == out["requested"]["id"]
    assert hub.capture(a, OWNER, "refuse_handoff", handoff_confirm=request["id"]) == {"refused": True}
    assert hub.capture(a, OWNER, "renew") == {"granted": True, "holder": hub.capture(a, OWNER, "renew")["holder"],
                                              "handoff_request": None}
    again = hub.capture(b, phone, "request_handoff")["requested"]
    assert hub.capture(a, OWNER, "grant_handoff", handoff_confirm=again["id"])["handed_off"]
    assert hub.capture(a, OWNER, "renew")["granted"] is False
    assert hub.capture(b, phone, "acquire")["granted"]
    assert hub.status(phone, b)["capture"]["this_device"]


def test_pocket_voice_handoff_command_asks_the_holder(hub):
    a = start(hub, device="consoleA")
    phone = pocket_principal()
    b = start(hub, phone, device="phoneB", mic=False)
    hub.text(b, phone, "Use this device for the microphone")
    pending = wait_for(lambda: [e for e in hub.events(b, phone, 0, wait=0.1)["events"] if e["type"] == "reply"
                                and e["reply"]["confirm"]])[0]["reply"]["confirm"]
    hub.confirm(b, phone, pending["id"], True)
    assert hub.capture(a, OWNER, "renew")["granted"]               # not moved by the requester's own confirmation
    assert hub.capture(a, OWNER, "renew")["handoff_request"]
    reply = [e for e in hub.events(b, phone, 0, wait=0)["events"] if e["type"] == "reply"][-1]["reply"]
    assert "asked" in reply["text"]


def test_utterance_streaming_provisional_final_and_reply(hub):
    s = start(hub)
    hub.fake.asr.script = ["show me the evidence"]
    assert hub.audio(s, OWNER, "u0001", 0, False, pcm(0.3))["status"] == "buffered"
    prov = hub.audio(s, OWNER, "u0001", 1, False, pcm(0.5))
    assert prov["status"] == "provisional"
    with pytest.raises(Conflict, match="expected chunk"):
        hub.audio(s, OWNER, "u0001", 5, False, pcm(0.1))
    final = hub.audio(s, OWNER, "u0001", 2, True, pcm(0.2))
    assert final == {"status": "final", "text": "show me the evidence", "asr_ms": 10.0}
    hub.drain()
    transcript = [e["segment"] for e in events(hub, s, kind="transcript")]
    assert [x["status"] for x in transcript] == ["provisional", "final"] and len({x["id"] for x in transcript}) == 1
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
    hub.drain()
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


def test_forgetting_a_conversation_forgets_the_research_its_words_created(hub, instance):
    """T5-C02: after the session was forgotten its research task still held the spoken sentence, queued for web
    search. An older task the conversation only pointed at is not the session's to forget."""
    with closing(store.connect(instance)) as db:
        older = orchestrator.submit(db, "Zephyr compiler supported boards", time.time() - 60, mode="quick_lookup")
    s = start(hub)
    hub.text(s, OWNER, "Research the Zephyr compiler release for my clinic in Ashford")
    ack = wait_for(lambda: [e for e in events(hub, s, kind="reply") if e["reply"]["intent"] == "research"])[0]
    task_id = ack["reply"]["task_id"]
    with closing(store.connect(instance)) as db:
        tx.upsert(db, s, "r-old", speaker="neuroscifi", source="template", status="final", text="see that",
                  now=time.time(), task_id=older["id"], detail={"intent": "research"})
        out = tx.forget_session(db, s)
        assert out["research_tasks_forgotten"] == [task_id]
        assert db.execute("SELECT count(*) FROM web_tasks WHERE id=?", (task_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM web_tasks WHERE objective LIKE '%Ashford%'").fetchone()[0] == 0
        assert orchestrator.get(db, older["id"])["status"] == "queued"
    wal = instance.parent / (instance.name + "-wal")
    assert b"Ashford" not in instance.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


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


# ---- principals kept apart (M2, M3), concurrency and the audio path ---------------------------------------------------

def test_pocket_sessions_never_evict_the_owners(hub, monkeypatch):
    from neuroscifi.voice import hub as vhub
    owner = start(hub, device="consoleA", mic=False)
    phone = pocket_principal()
    for i in range(3 * vhub.MAX_SESSIONS):
        start(hub, phone, device=f"phone{i:04d}", consent=False, mic=False)
    assert owner in hub.sessions and hub.sessions[owner].active
    assert sum(x.principal.name == phone.name for x in hub.sessions.values()) == vhub.MAX_SESSIONS_PER_PRINCIPAL
    monkeypatch.setattr(vhub, "MAX_SESSIONS", 3)                    # a full hub: other families cannot push it out
    others = [Principal(name=f"pocket:fam{i}", owner=False, research=True, family=f"f{i}", label=f"P{i}",
                        device_prefix=f"pocket:fam{i}:") for i in range(4)]
    for p in others:
        try:
            start(hub, p, device="phone0001", consent=False, mic=False)
        except Conflict:
            pass
    assert owner in hub.sessions


def test_pocket_settings_and_mode_are_its_own(hub, instance):
    owner = start(hub, device="consoleA", mic=False)
    phone = pocket_principal()
    b = start(hub, phone, device="phoneB", mic=False)
    assert hub.settings(phone, {"voice": "bm_george", "speed": 1.5})["voice"] == "bm_george"
    hub.set_mode(b, phone, "text_only")
    with closing(store.connect(instance)) as db:
        cfg = vconfig.get(db)
    assert (cfg["voice"], cfg["speed"], cfg["mode"]) == ("af_heart", 1.0, "voice_text")   # the owner's untouched
    assert hub.status(OWNER, owner)["settings"]["voice"] == "af_heart"
    st = hub.status(phone, b)["settings"]
    assert (st["voice"], st["speed"], st["mode"]) == ("bm_george", 1.5, "text_only")
    assert hub.start(phone, "pocket:abcd1234:phoneC", phone.label)["mode"] == "text_only"
    assert hub.start(OWNER, "console:consoleB", OWNER.label)["mode"] == "voice_text"
    with pytest.raises(ValidationError):
        hub.settings(phone, {"retain_audio": True})


def test_pocket_status_is_scoped_and_cannot_steer_the_owners_presence(hub):
    owner = start(hub, device="consoleA")
    hub.client_status(owner, OWNER, mic_capturing=True, user_speaking=True)
    phone = pocket_principal()
    st = hub.status(phone)
    assert st["snapshot"]["session_active"] is False and st["metrics"] is None
    assert st["retained_audio_segments"] is None and st["capture"] == {"held": True, "this_device": False,
                                                                        "label": "another device"}
    b = start(hub, phone, device="phoneB", mic=False)
    hub.set_mode(b, phone, "voice_in_text_out")
    hub.client_status(b, phone, output_muted=True)
    snap = vstate.snapshot()                                         # still the owner's session
    assert snap["capture_device"] == "Owner console" and snap["mode"] == "voice_text" and snap["mic_capturing"]
    mine = hub.status(phone, b)["snapshot"]
    assert mine["mode"] == "voice_in_text_out" and mine["output_muted"] and not mine["mic_capturing"]
    assert hub.status(OWNER, owner)["metrics"] is not None


def test_a_confirmation_executes_once(hub, monkeypatch):
    s = start(hub)
    hub.text(s, OWNER, "Turn on autonomous research")
    pending = wait_for(lambda: events(hub, s, kind="reply"))[0]["reply"]["confirm"]
    ran = []
    real = hub.bridge.execute

    def slow(*a):
        ran.append(1)
        time.sleep(0.3)
        return real(*a)
    monkeypatch.setattr(hub.bridge, "execute", slow)
    results = []

    def go():
        try:
            results.append(hub.confirm(s, OWNER, pending["id"], True)["done"])
        except Conflict:
            results.append("conflict")
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, results)) == ["True", "conflict"] and len(ran) == 1


def test_reply_threads_are_bounded_with_a_busy_answer(hub, monkeypatch):
    from neuroscifi.voice import hub as vhub
    s = start(hub)
    gate = threading.Event()
    real = hub.bridge.handle
    monkeypatch.setattr(hub.bridge, "handle", lambda *a: (gate.wait(5), real(*a))[1])
    try:
        for i in range(vhub.MAX_REPLY_WORKERS):
            hub.text(s, OWNER, f"help {i}")
        with pytest.raises(Conflict, match="busy"):
            hub.text(s, OWNER, "help again")
        hub.fake.asr.script = ["what is the weather on mars"]
        out = hub.audio(s, OWNER, "u0001", 0, True, pcm(0.5))
        assert out["status"] == "busy" and events(hub, s, kind="error")[-1]["code"] == "busy"
    finally:
        gate.set()
    wait_for(lambda: len(events(hub, s, kind="reply")) == vhub.MAX_REPLY_WORKERS)
    hub.text(s, OWNER, "help")                                     # slots come back


def test_audio_chunks_never_wait_for_a_write_transaction(hub, instance):
    """(c) A research worker holding the write lock does not delay chunk acknowledgements; the transcript lands
    when the lock is free."""
    s = start(hub)
    hub.fake.asr.script = ["show me the evidence"]
    worker = store.connect(instance)
    worker.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        assert hub.audio(s, OWNER, "u0001", 0, False, pcm(0.3))["status"] == "buffered"
        assert hub.audio(s, OWNER, "u0001", 1, False, pcm(0.5))["status"] == "provisional"
        assert hub.audio(s, OWNER, "u0001", 2, True, pcm(0.2))["status"] == "final"
        assert time.monotonic() - started < 1.0
    finally:
        worker.execute("COMMIT")
        worker.close()
    hub.drain()
    with closing(store.connect(instance)) as db:
        assert [(x["status"], x["text"]) for x in tx.listing(db, session_id=s) if x["speaker"] == "user"] == \
            [("final", "show me the evidence")]


def test_many_console_session_starts_never_hang(hub):
    """T2-07: the 17th start in one console process used to deadlock (eviction re-took the hub lock)."""
    done = []

    def go():
        for i in range(20):
            done.append(hub.start(OWNER, f"console:dev{i:04d}", OWNER.label, "text_only")["session"])
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(10)
    assert len(done) == 20 and done[-1] in hub.sessions


def test_sessions_of_closed_clients_expire(hub, instance):
    """T2-07: a closed tab stops polling; its session ends (and releases the microphone) after SESSION_IDLE."""
    from neuroscifi.voice import hub as vhub
    gone = start(hub, device="closedtab")
    hub.events(start(hub, device="livetab", mic=False), OWNER, 0, wait=0)
    hub.sessions[gone].seen -= vhub.SESSION_IDLE + 1
    hub.status(OWNER)
    assert gone not in hub.sessions and vstate.get(gone)["session_active"] is False
    with closing(store.connect(instance)) as db:
        assert tx.session(db, gone)["status"] == "ended" and tx.holder(db, time.time()) is None


def test_unrelated_pocket_families_are_isolated(hub):
    """T2-04: a device of another family sees no holder identity and cannot take the microphone on its own."""
    fam1 = pocket_principal()
    fam2 = Principal(name="pocket:feedbeef0000", owner=False, research=True, family="other", label="Pocket (x)",
                     device_prefix="pocket:feedbeef0000:")
    a = start(hub, fam1, device="phoneA")
    b = start(hub, fam2, device="phoneB", mic=False)
    assert hub.status(fam2, b)["capture"] == {"held": True, "this_device": False, "label": "another device"}
    assert hub.capture(b, fam2, "acquire")["holder"] == {"label": "another device"}
    req = hub.capture(b, fam2, "request_handoff")["requested"]
    with pytest.raises(Conflict):
        hub.capture(b, fam2, "handoff", handoff_confirm=req["id"])
    assert hub.capture(a, fam1, "renew")["granted"] and hub.capture(a, fam1, "renew")["handoff_request"]
    same = start(hub, fam1, device="tabletA", mic=False)              # the same family confirms on its own
    confirm = hub.capture(same, fam1, "request_handoff")["confirm"]
    assert hub.capture(same, fam1, "handoff", handoff_confirm=confirm["id"])["granted"]


def test_a_restarted_server_ends_the_voice_sessions_of_the_one_that_died(instance):
    """T5-08: after the console was killed its voice sessions stayed 'active' for good."""
    from neuroscifi.console.server import ConsoleServer
    with closing(store.connect(instance)) as db:
        mine = tx.start_session(db, "owner", "console:dev1234", "voice_text", 100.0)
        phone = tx.start_session(db, "pocket:fam", "pocket:ab12:dev1", "text_only", 100.0)
    srv = ConsoleServer(instance, 0, clock=lambda: 200.0)
    srv.server_close()
    with closing(store.connect(instance)) as db:
        assert tx.session(db, mine)["status"] == "ended" and tx.session(db, mine)["ended_at"] == 200.0
        assert tx.session(db, phone)["status"] == "active"       # the Pocket bridge's own, ended by its restart
        assert tx.end_orphans(db, "pocket:", 300.0) == 1 and tx.session(db, phone)["status"] == "ended"
        assert tx.end_orphans(db, "pocket:", 301.0) == 0
