"""Cognitive Voice Bridge: intent parsing, routing through the research path, authorisation and confirmation."""
from contextlib import closing
import json
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.voice import bridge as vbridge, commands
from neuroscifi.webcortex import orchestrator, state as wstate

from .conftest import OWNER, pocket_principal


@pytest.mark.parametrize("text,kind,arg", [
    ("Stop.", "stop_speaking", None),
    ("stop speaking but keep researching", "stop_speaking", None),
    ("Be quiet please", "stop_speaking", None),
    ("Go on.", "resume_speaking", None),
    ("Cancel the research", "cancel_research", None),
    ("Show me the evidence.", "show_evidence", None),
    ("Remember these conclusions", "remember", None),
    ("What have you learned since yesterday?", "learned_since", "yesterday"),
    ("Explain that more simply.", "simpler", None),
    ("Continue the previous investigation", "continue_research", None),
    ("Switch to text only.", "mode", "text_only"),
    ("Text replies only", "mode", "voice_in_text_out"),
    ("Speak your answers", "mode", "voice_text"),
    ("Forget this conversation", "forget", "conversation"),
    ("Forget the last research", "forget", "research"),
    ("Turn on autonomous research", "enable_autonomy", None),
    ("Disable autonomy", "disable_autonomy", None),
    ("Use this device for the microphone", "handoff", None),
    ("Yes, confirm.", "confirm", None),
    ("No", "deny", None),
    ("What can you do?", "help", None),
    ("Research quantum error correction", "research", "quantum error correction"),
    ("When was Python first released?", "research", "When was Python first released?"),
    ("hello", "unclear", None),
    ("I had a lovely walk in the park today", "chatter", None),        # T5: chatter is not a web search
    ("thanks that was really helpful", "chatter", None),
    ("the weather is nice here", "chatter", None),
    ("the latest numpy version?", "research", "the latest numpy version?"),
    ("how tall is Mount Everest", "research", "how tall is Mount Everest"),
    ("tell me the population of Lisbon", "research", "tell me the population of Lisbon"),
])
def test_parse(text, kind, arg):
    intent = commands.parse(text)
    assert (intent.kind, intent.arg) == (kind, arg)


def test_parse_keeps_owner_wording_for_research_objective():
    assert commands.parse("Research the CRISPR-Cas9 patent dispute").arg == "the CRISPR-Cas9 patent dispute"


@pytest.fixture
def db(instance):
    with closing(store.connect(instance)) as conn:
        yield conn


def handle(db, text, principal=OWNER, ctx=None, pending=None):
    return vbridge.Bridge(time.time).handle(db, principal, ctx or {}, text, pending)


def test_question_goes_through_the_same_research_path_as_text(db):
    r = handle(db, "When was the Zephyr compiler 4.2 released?")
    assert r.intent == "research" and r.follow == r.task_id
    task = orchestrator.get(db, r.task_id)
    assert task["objective"] == "When was the Zephyr compiler 4.2 released?" and task["origin"] == "voice"
    assert task["status"] == "queued"
    again = handle(db, "When was the Zephyr compiler 4.2 released?")
    assert again.task_id == r.task_id and "already" in again.speech[0]   # deduplicated like typed submissions


def test_web_switch_and_privacy_screen_apply(db):
    wstate.set_config(db, {"private_terms": ["Zorblax"]}, time.time())
    r = handle(db, "research the Zorblax project budget")
    assert r.confirm and r.confirm["action"] == "disclose" and r.task_id is None   # nothing sent yet
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0
    done = vbridge.Bridge(time.time).execute(db, OWNER, {}, r.confirm)
    assert done.task_id and orchestrator.get(db, done.task_id)["status"] == "queued"
    pocket = handle(db, "research the Zorblax project budget", pocket_principal())
    assert pocket.intent == "refused" and pocket.confirm is None
    wstate.set_config(db, {"enabled": False}, time.time())
    off = handle(db, "When was Python first released?")
    assert off.intent == "refused" and "not available" in off.text


def test_pocket_without_research_permission_is_refused(db):
    r = handle(db, "When was Python first released?", pocket_principal(research=False))
    assert r.intent == "refused" and db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0


def test_pocket_rate_limit_hook_is_used(db):
    p = pocket_principal()
    p.admit = lambda submit: None     # over the family's hourly limit
    r = handle(db, "When was Python first released?", p)
    assert r.intent == "refused" and "limit" in r.text


@pytest.mark.parametrize("text,action", [("Turn on autonomous research", "enable_autonomy"),
                                         ("Forget this conversation", "forget_conversation"),
                                         ("Use this device for the microphone", "handoff")])
def test_sensitive_commands_need_confirmation(db, text, action):
    r = handle(db, text)
    assert r.confirm["action"] == action and "confirm" in " ".join(r.speech).lower()
    assert wstate.get_config(db).autonomous is False


def test_confirmed_autonomy_and_unconfirmed_disable(db):
    r = handle(db, "Enable autonomy")
    assert wstate.get_config(db).autonomous is False
    deny = handle(db, "no", pending=r.confirm)
    assert deny.intent == "deny" and wstate.get_config(db).autonomous is False
    ok = handle(db, "confirm", pending=r.confirm)
    assert ok.intent == "enable_autonomy" and wstate.get_config(db).autonomous is True
    off = handle(db, "Disable autonomous research")
    assert off.confirm is None and wstate.get_config(db).autonomous is False


def test_owner_only_commands_refused_for_pocket(db):
    for text in ("Turn on autonomous research", "Forget the last research", "Remember the conclusions",
                 "Disable autonomy"):
        r = handle(db, text, pocket_principal())
        assert r.intent == "refused" and r.confirm is None, text


def test_confirm_without_pending_does_nothing(db):
    assert handle(db, "confirm").intent == "confirm"
    assert "nothing" in handle(db, "confirm").text


def test_forget_research_after_confirmation(db):
    r = handle(db, "Research the history of the Zephyr compiler")
    ctx = {"last_task_id": r.task_id}
    ask = handle(db, "Forget the last research", ctx=ctx)
    assert ask.confirm["action"] == "forget_research"
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 1
    handle(db, "confirm", ctx=ctx, pending=ask.confirm)
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0


def test_cancel_and_continue_research(db):
    r = handle(db, "Research the history of the Zephyr compiler")
    ctx = {"last_task_id": r.task_id}
    c = handle(db, "cancel the research", ctx=ctx)
    assert c.intent == "cancel_research" and orchestrator.get(db, r.task_id)["status"] == "cancelled"
    cont = handle(db, "continue the previous investigation", ctx=ctx)
    assert cont.intent == "continue_research" and cont.task_id != r.task_id and cont.follow == cont.task_id
    assert orchestrator.get(db, cont.task_id)["parent_id"] == r.task_id


def test_stop_speaking_keeps_research_running(db):
    r = handle(db, "Research the history of the Zephyr compiler")
    stop = handle(db, "Stop speaking but keep researching", ctx={"last_task_id": r.task_id})
    assert stop.speech == [] and {"kind": "stop_speaking"} in stop.actions and "continues" in stop.text
    assert orchestrator.get(db, r.task_id)["status"] == "queued"


def test_mode_switch_commands(db):
    r = handle(db, "switch to text only")
    assert r.actions == [{"kind": "mode", "mode": "text_only"}] and r.speech == []


REPORT_MODEL = {
    "answer": [{"text": "Lead.", "status": "summary", "citations": [1]},
               {"text": "Zephyr 4.2.0 was released on 17 November 2026.", "citations": [1], "status": "corroborated"}],
    "sources": [{"n": 1, "title": "Zephyr 4.2", "url": "https://news.example/z", "domain": "news.example"},
                {"n": 2, "title": "Other", "url": "https://other.example/", "domain": "other.example"}],
    "reasoning": {"status": "model", "label": "Advanced reasoning by m", "answer": [
        {"text": "Zephyr 4.2.0 came out on 17 November 2026.", "citations": [1], "claims": ["C1"]},
        {"text": "Two sources agree.", "citations": [1, 2], "claims": ["C1"]},
        {"text": "A third sentence.", "citations": [2], "claims": ["C1"]}]},
    "verdict": {"label": "supported", "statement": "Released 17 November 2026."},
}


def test_compose_answer_speaks_checked_model_text_concisely():
    r = vbridge.compose_answer({"id": "t", "status": "completed", "objective": "q", "result": REPORT_MODEL})
    assert r.source == "model" and r.label.startswith("Advanced reasoning")
    assert r.speech[:2] == ["Zephyr 4.2.0 came out on 17 November 2026.", "Two sources agree."]
    assert "[1, 2]" in r.text and "Verdict: supported" in r.text and len(r.sources) == 2


def test_compose_answer_extractive_is_attributed_and_labelled():
    report = dict(REPORT_MODEL, reasoning={"status": "unavailable", "reason": "unavailable: cannot connect"})
    r = vbridge.compose_answer({"id": "t", "status": "completed", "objective": "q", "result": report})
    assert r.source == "template" and "Extractive" in r.label and "cannot connect" in r.label
    assert r.speech[0].startswith("According to news.example: Zephyr 4.2.0")
    failed = vbridge.compose_answer({"id": "t", "status": "failed", "objective": "q", "result": None, "error": "x"})
    assert "did not finish" in failed.text


def test_compose_answer_speaks_what_was_not_established_when_the_guard_rejected_everything():
    """T2-03 follow-up: when reasoning rejects every model sentence, voice says what could not be established (the
    report's reasoning.unanswered, as the console shows it) instead of an unrelated extractive quote; the text
    answer leads with the same sentence and keeps the quotes below it as evidence."""
    from neuroscifi.webcortex import reasoning
    objective = "the latest developments in agentic AI"
    report = dict(REPORT_MODEL, reasoning={"status": "rejected", "generated_by": "template",
                                           "reason": "every model sentence failed NeuroSciFi's checks",
                                           "unanswered": reasoning.unanswered(objective)})
    r = vbridge.compose_answer({"id": "t", "status": "completed", "objective": objective, "result": report})
    said = r.speech[0]
    assert said == "The sources read do not establish an answer to: the latest developments in agentic AI."
    assert not any("According to" in s for s in r.speech) and r.source == "template"
    lines = r.text.splitlines()
    assert lines[0] == said and "Zephyr 4.2.0" in r.text and r.text.index("Zephyr") > r.text.index(said)


def test_spoken_budget():
    out = vbridge.spoken(["word " * 50 + "end.", "second [3] sentence.", "third."])
    assert out == ["word " * 50 + "end.", "second sentence."]
    assert vbridge.spoken(["word " * 80 + ".", "x."]) == ["word " * 80 + "."]


def test_learned_since_reads_memory(db):
    now = time.time()
    db.execute("INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
               "first_verified_at,last_verified_at,created_at,updated_at) VALUES('k',1,'Zephyr 4.2.0 was released.',"
               "'provisional',0.7,0.3,'release',?,?,?,?)", (now, now, now, now))
    r = handle(db, "What have you learned since yesterday?")
    assert "added 1 item" in r.text and "provisionally" in " ".join(r.speech)


def test_learned_since_is_owner_only(db):
    """A paired device must not read the owner's whole knowledge base by voice (H1)."""
    now = time.time()
    db.execute("INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
               "first_verified_at,last_verified_at,created_at,updated_at) VALUES('k',1,'The owner secret fact.',"
               "'provisional',0.7,0.3,'release',?,?,?,?)", (now, now, now, now))
    r = handle(db, "What have you learned since yesterday?", pocket_principal())
    assert r.intent == "refused" and "secret" not in r.text and "secret" not in " ".join(r.speech)


def test_voice_autonomy_switch_is_recorded(db):
    """The voice toggle goes through autonomy.enable/disable, so the owner's switch is audited (L6)."""
    from neuroscifi.webcortex import autonomy
    r = handle(db, "Turn on autonomous research")
    ok = handle(db, "confirm", pending=r.confirm)
    assert ok.intent == "enable_autonomy" and autonomy.enabled(db)
    record = autonomy.policy(db)["record"]
    assert record["enabled_by"] == "owner:voice" and record["enabled_at"]
    assert "on" in ok.text.lower()
    off = handle(db, "Turn off autonomous research")
    assert off.intent == "disable_autonomy" and not autonomy.enabled(db)
    assert autonomy.policy(db)["record"]["disabled_by"] == "owner:voice"
    decisions = [json.loads(d) for (d,) in db.execute("SELECT detail FROM web_events WHERE kind='decision'")]
    assert [d.get("by") for d in decisions if d.get("autonomy") in ("enabled", "disabled")] == ["owner:voice"] * 2


def test_voice_autonomy_reply_reflects_a_failed_switch(db, monkeypatch):
    from neuroscifi.instance.errors import Conflict
    from neuroscifi.webcortex import autonomy
    r = handle(db, "Turn on autonomous research")

    def refuse(*a, **k):
        raise Conflict("writes are paused")
    monkeypatch.setattr(autonomy, "enable", refuse)
    out = handle(db, "confirm", pending=r.confirm)
    assert "is now on" not in out.text and "couldn't" in out.text.lower() and not autonomy.enabled(db)


@pytest.mark.parametrize("text,objective,instruction", [
    ("NeuroSciFi, research the latest developments in agentic AI and summarize their importance.",
     "the latest developments in agentic AI", "summarize their importance"),
    ("New Roe Sci-Fi research the latest developments in AI agents and summarize their importance.",
     "the latest developments in AI agents", "summarize their importance"),
    ("Hey neuro sci fi, when was Python first released?", "when was Python first released?", None),
    ("Research quantum error correction, then explain why it matters", "quantum error correction",
     "explain why it matters"),
    ("Research cats and dogs", "cats and dogs", None),           # 'and' alone is part of the subject
])
def test_wake_word_and_instruction_are_not_the_research_subject(text, objective, instruction):
    """T2-03: the address and the instruction do not pollute the research query; the instruction is kept."""
    intent = commands.parse(text)
    assert intent.kind == "research" and intent.arg == objective and intent.instruction == instruction


def test_wake_word_before_a_command():
    assert commands.parse("NeuroSciFi, show me the evidence.").kind == "show_evidence"
    assert commands.parse("NeuroSciFi.").kind == "unclear"


def test_research_keeps_the_instruction_in_the_conversation(db):
    r = handle(db, "NeuroSciFi, research the Zephyr compiler history and summarize its importance.")
    assert orchestrator.get(db, r.task_id)["objective"] == "the Zephyr compiler history"
    ctx = next(a for a in r.actions if a["kind"] == "context")
    assert ctx["last_instruction"] == "summarize its importance"


def test_continue_and_evidence_find_research_started_on_another_device(db):
    """T2-06: research submitted from Pocket (no voice context) is found by voice within the principal's scope."""
    phone = pocket_principal(family="fam-A")
    task = orchestrator.submit(db, "Zephyr compiler release notes", time.time(), origin="pocket", family="fam-A")
    for who in (phone, OWNER):
        r = handle(db, "Continue my previous investigation", who)
        assert r.intent == "continue_research" and r.task_id == task["id"], who.name
        r = handle(db, "Show me the evidence", who)
        assert r.intent == "show_evidence" and r.task_id == task["id"] and "Zephyr" in r.text, who.name
    stranger = pocket_principal(family="fam-B")
    r = handle(db, "Continue my previous investigation", stranger)
    assert r.task_id is None and "no previous investigation" in r.text
    r = handle(db, "Show me the evidence", stranger)
    assert r.task_id is None and "Zephyr" not in r.text


def test_evidence_numbers_match_the_answer_citations(db, monkeypatch):
    """T2-09: 'show the evidence' numbers sources as the answer cites them; T2-10: no empty '[]' markers."""
    report = dict(REPORT_MODEL, reasoning={"status": "model", "label": "m", "answer": [
        {"text": "Cited by the second source.", "citations": [2]}, {"text": "Uncited sentence.", "citations": []}]})
    r = vbridge.compose_answer({"id": "t", "status": "completed", "objective": "q", "result": report})
    assert "[]" not in r.text and "Cited by the second source. [2]" in r.text
    extractive = dict(report, reasoning={"status": "unavailable"},
                      answer=[{"text": "A quote.", "citations": [], "status": "supported"}])
    assert "[]" not in vbridge.compose_answer({"id": "t", "status": "completed", "objective": "q",
                                               "result": extractive}).text
    task = {"id": "t1", "objective": "q", "status": "completed", "result": report}
    monkeypatch.setattr(orchestrator, "get", lambda db, tid, family=None: task)
    sources = [{"url": "https://other.example/", "canonical_url": "https://other.example/", "title": "Other",
                "domain": "other.example"},
               {"url": "https://news.example/z", "canonical_url": "https://news.example/z", "title": "News",
                "domain": "news.example"}]
    monkeypatch.setattr(orchestrator, "evidence", lambda db, tid, family=None: {"sources": sources})
    r = handle(db, "Show me the evidence", ctx={"last_task_id": "t1"})
    by_n = {s["n"]: s["url"] for s in REPORT_MODEL["sources"]}
    for line in r.text.splitlines()[1:]:
        n = int(line[1:line.index("]")])
        assert by_n[n].split("//")[1].split("/")[0] in line, line


def test_chatter_starts_no_research(db):
    r = handle(db, "I had a lovely walk in the park today")
    assert r.intent == "chatter" and r.task_id is None and "didn't start a web search" in r.text
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == 0


def test_research_from_conversation_is_rate_limited_per_principal(db):
    """T5: 385 research tasks were queued from conversation in about 5 s."""
    clock = [time.time()]
    bridge = vbridge.Bridge(lambda: clock[0])
    for n in range(vbridge.CONVERSATION_RESEARCH_LIMIT):
        assert bridge.handle(db, OWNER, {}, f"What is the lifespan of armadillo species {n}?").intent == "research"
    over = bridge.handle(db, OWNER, {}, "What is the lifespan of a tortoise?")
    assert over.intent == "rate_limited" and over.task_id is None and "Ask again in about 10 minutes" in over.text
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == vbridge.CONVERSATION_RESEARCH_LIMIT
    phone = bridge.handle(db, pocket_principal(), {}, "What is the lifespan of a tortoise?")
    assert phone.intent == "research"                              # another principal has its own allowance
    clock[0] += vbridge.CONVERSATION_RESEARCH_WINDOW + 1
    assert bridge.handle(db, OWNER, {}, "What is the lifespan of a tortoise?").intent == "research"
