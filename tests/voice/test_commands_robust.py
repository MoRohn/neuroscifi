"""Voice command grammar beyond the literal phrases (docs/voice/VOICE_VALIDATION.md, "Command grammar"):
canonicalisation, synonyms, clause splitting, frames and fuzzy matching, without turning ordinary questions into
commands, and with a confirmation before a fuzzy match of a command that changes something."""
from contextlib import closing
import json
from pathlib import Path
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.voice import bridge as vbridge, commands
from neuroscifi.webcortex import orchestrator

from .conftest import OWNER

DATA = Path(__file__).resolve().parent / "data"


def label(intent):
    return f"{intent.kind}:{intent.arg}" if intent.kind in ("mode", "forget") else intent.kind


def load(name):
    return json.loads((DATA / name).read_text())


@pytest.mark.parametrize("name,floor", [("command_paraphrases.json", 1.0),
                                        ("command_paraphrases_independent.json", 0.65),
                                        ("command_paraphrases_fresh.json", 0.6)])
def test_paraphrase_accuracy_floor_and_no_false_commands(name, floor):
    """Floors at the measured accuracy (a regression guard, not a target); ordinary questions never become commands."""
    data = load(name)
    hits = sum(label(commands.parse(t)) == want for t, want in data["commands"])
    assert hits / len(data["commands"]) >= floor
    assert [t for t in data["ordinary"] if commands.parse(t).kind in commands_kinds()] == []


def commands_kinds():
    return {"confirm", "deny", "stop_speaking", "resume_speaking", "cancel_research", "show_evidence", "remember",
            "learned_since", "simpler", "continue_research", "mode", "forget", "enable_autonomy", "disable_autonomy",
            "handoff", "help", "history"}


@pytest.mark.parametrize("text,want", [
    ("Um, okay, could you stop for a second please?", "stop_speaking"),
    ("Hold on.", "stop_speaking"), ("Hang on a moment", "stop_speaking"), ("stop stop", "stop_speaking"),
    ("Continue.", "resume_speaking"), ("Go ahead", "resume_speaking"),
    ("Where did you get that?", "show_evidence"), ("Hold on, stop reading that out", "stop_speaking"),
    ("What was I re-searching?", "history"), ("Remind me what I was researching", "history"),
    ("NeuroSciFi, be quiet.", "stop_speaking"), ("Never mind.", "deny"),
])
def test_paraphrases(text, want):
    assert label(commands.parse(text)) == want


@pytest.mark.parametrize("text", [
    "wait, who invented the telephone", "how do I stop my dog from barking at night", "show me the evidence for "
    "climate change in the arctic", "what is the evidence that coffee is good for the heart", "help me understand "
    "quantum computing", "stop by the store on the way home", "continue", "the history of the internet is interesting",
])
def test_questions_and_remarks_stay_what_they_were(text):
    before = {"continue": "resume_speaking"}   # the one deliberate change: a bare "continue" resumes speech
    got = commands.parse(text)
    assert got.kind not in commands_kinds() or before.get(text) == got.kind


def test_fuzzy_matches_are_marked_and_never_confirm():
    got = commands.parse("cancel the reseach")
    assert got.kind == "cancel_research" and got.fuzzy
    assert commands.parse("stop talkin").kind == "stop_speaking"
    for text in ("confrim", "confirmm", "yes confirn", "conform", "please confirm it now"):
        assert commands.parse(text).kind != "confirm"      # a sensitive action runs only on the literal words
    assert commands.parse("Yes, confirm.").kind == "confirm" and not commands.parse("Yes, confirm.").fuzzy
    assert commands.parse("okay yes").kind != "confirm" and commands.parse("yes").kind != "confirm"


@pytest.fixture
def db(instance):
    with closing(store.connect(instance)) as conn:
        yield conn


def test_a_fuzzy_cancel_asks_first_and_runs_after_confirmation(db):
    bridge = vbridge.Bridge(time.time)
    r = bridge.handle(db, OWNER, {}, "Research the history of the Zephyr compiler")
    ctx = {"last_task_id": r.task_id}
    ask = bridge.handle(db, OWNER, ctx, "cancel the reseach")
    assert ask.confirm and ask.confirm["action"] == "intent" and "Did you mean" in ask.confirm["summary"]
    assert orchestrator.get(db, r.task_id)["status"] != "cancelled"
    done = bridge.execute(db, OWNER, ctx, ask.confirm)
    assert done.intent == "cancel_research" and orchestrator.get(db, r.task_id)["status"] == "cancelled"
    assert {"kind": "clear_confirm"} in done.actions


def test_a_fuzzy_stop_acts_at_once_and_sensitive_commands_still_ask(db):
    bridge = vbridge.Bridge(time.time)
    assert bridge.handle(db, OWNER, {}, "stop talkin").actions[0]["kind"] == "stop_speaking"
    forget = bridge.handle(db, OWNER, {}, "forget this conversaton")
    assert forget.confirm and forget.confirm["action"] == "forget_conversation"
    forged = {"id": "x", "action": "intent", "args": {"kind": "forget", "arg": "conversation"}, "summary": "",
              "expires_at": time.time() + 60}
    assert bridge.execute(db, OWNER, {}, forged).intent == "error"       # only CONFIRM_IF_FUZZY kinds run this way


def test_the_spoken_help_names_only_commands_that_work_with_the_default_settings():
    """review1c: the help listed "what are you doing", which starts research unless the owner turned status_questions
    on. Every phrase the help offers as a command must be that command as a new individual parses it."""
    assert "what are you doing" not in vbridge.HELP and "status report" in vbridge.HELP
    assert commands.parse("status report").kind == "self_status"
    assert commands.parse("what are you doing").kind == "research"
