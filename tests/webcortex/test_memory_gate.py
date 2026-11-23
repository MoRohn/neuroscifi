"""The memory-answer check (webcortex/memory_gate.py, _Run.memory_check): memory answers without the web only when
the answer model confirms that a chosen statement states the answer to exactly what is asked; the check can only
send a question to the web, and without a usable model the lexical decision stands."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "inference"))
from fake_openai import FakeOpenAI, make_gateway                                    # noqa: E402

from neuroscifi.inference.gateway import InferenceError                             # noqa: E402
from neuroscifi.instance import store                                               # noqa: E402
from neuroscifi.webcortex import intent, knowledge, memory_gate, orchestrator, state   # noqa: E402
from neuroscifi.webcortex import services as svc_mod                                # noqa: E402

T0 = 1_795_200_000.0
AT_REST = "The Orbix server does not support encryption at rest."
ITEMS = [{"id": 7, "statement": AT_REST}, {"id": 9, "statement": "Orbix listens on port 7420."}]


def booleans(*values):
    return {"content": json.dumps({"answers": list(values)})}


@pytest.fixture
def fake():
    server = FakeOpenAI().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def gateway(fake, tmp_path):
    key = tmp_path / "api_key"
    key.write_text("test-key\n")
    key.chmod(0o600)
    return make_gateway(fake, key, tmp_path)


def test_the_model_is_asked_once_with_the_statements_as_numbered_data(gateway, fake):
    fake.replies.append(booleans(False, True))
    out = memory_gate.confirm(None, None, "Which port does Orbix use?", ITEMS, gateway=gateway)
    assert out == {"status": "checked", "confirmed": [9], "refused": [7]}
    request = fake.requests[0]
    assert "1. The Orbix server does not support encryption at rest." in request["messages"][1]["content"]
    assert "same subject, the same property and the same scope" in request["messages"][0]["content"]
    assert request["response_format"]["json_schema"]["schema"]["properties"]["answers"]["minItems"] == 2
    assert request["max_tokens"] <= 32 and request["temperature"] == 0.0
    assert gateway.ledger.recent(1)[0]["purpose"] == "memory_gate"


@pytest.mark.parametrize("reply", [{"status": 503}, {"content": "not json"}, booleans(True),
                                   {"content": json.dumps({"answers": ["yes", "no"]})}])
def test_a_failed_or_malformed_check_is_skipped_never_guessed(gateway, fake, reply):
    fake.replies += [reply, reply, reply, reply]
    gateway.cfg.max_retries = 0
    out = memory_gate.confirm(None, None, "Which port does Orbix use?", ITEMS, gateway=gateway)
    assert out["status"] == "skipped" and out["confirmed"] == [] and out["reason"]


def test_no_model_reasoning_off_or_no_time_is_skipped_without_a_call(gateway, fake, tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    assert memory_gate.confirm(db, None, "q", ITEMS)["status"] == "skipped"           # nothing configured
    gateway.cfg.reasoning = False
    assert memory_gate.confirm(None, None, "q", ITEMS, gateway=gateway)["status"] == "skipped"
    gateway.cfg.reasoning = True
    assert memory_gate.confirm(None, None, "q", ITEMS, gateway=gateway, timeout=0.2)["status"] == "skipped"
    assert fake.requests == []


def test_a_background_task_sends_the_background_priority(gateway, fake):
    gateway.cfg.background_priority = 10
    fake.replies += [booleans(True, True), booleans(True, True)]
    memory_gate.confirm(None, None, "q", ITEMS, gateway=gateway, background=True)
    memory_gate.confirm(None, None, "q", ITEMS, gateway=gateway)
    assert fake.requests[0]["priority"] == 10 and "priority" not in fake.requests[1]


# ---- through the orchestrator ----------------------------------------------------------------------------------

class Stub:
    """An injected gateway (services.inference) whose memory check answers as told and records the questions."""

    def __init__(self, answers=None, error=None):
        from neuroscifi.inference import config as iconfig
        self.cfg = iconfig.InferenceConfig(base_url="http://127.0.0.1:1/v1", model="stub", self_critique=False)
        self.answers, self.error, self.asked = answers, error, []

    def structured(self, messages, schema, *, name, purpose, **kwargs):
        if purpose != "memory_gate":
            raise InferenceError("unavailable", "the stub writes no answers")
        self.asked.append(messages[1]["content"])
        if self.error:
            raise self.error
        n = schema["properties"]["answers"]["minItems"]
        return {"answers": [self.answers] * n}, []


def individual(tmp_path, stub):
    path = tmp_path / "i.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True}, T0)
    knowledge.assert_fact(db, AT_REST, T0 + 1)
    services = svc_mod.build(db, path, providers=[], clock=lambda: T0 + 60, sleep=lambda s: None)
    services.inference = stub
    return db, services


def run(db, services, question):
    task = orchestrator.submit(db, question, T0 + 60, origin="console")
    return orchestrator.run(db, task["id"], services), state.events(db, task["id"])


IN_TRANSIT = "Does the Orbix server support encryption in transit?"


def test_the_lexical_test_alone_answers_the_wrong_property_from_memory(tmp_path):
    """The defect, pinned: with the check off the question about encryption IN TRANSIT is answered from the
    statement about encryption AT REST (shared stems), without the web."""
    known = [{"id": 1, "statement": AT_REST, "status": "accepted", "stale": False, "contested": False}]
    assessment = intent.assess(IN_TRANSIT, now=T0, known=known)
    assert assessment["needs_web"] is False and assessment["memory_items"] == [1]
    stub = Stub(answers=False)
    db, services = individual(tmp_path, stub)
    state.set_config(db, {"memory_check": False}, T0)
    done, _ = run(db, services, IN_TRANSIT)
    assert done["result"]["memory"]["answered_from_memory"] is True and stub.asked == []


def test_a_statement_the_model_refuses_sends_the_question_to_the_web_instead(tmp_path):
    stub = Stub(answers=False)
    db, services = individual(tmp_path, stub)
    done, events = run(db, services, IN_TRANSIT)
    assert len(stub.asked) == 1 and AT_REST in stub.asked[0]
    assert not (done["result"] or {}).get("memory", {}).get("answered_from_memory")
    decision = next(e["detail"] for e in events if e["kind"] == "decision")
    assert decision["needs_web"] is True and "do not state its answer" in decision["reasons"][0]
    assert decision["memory_check"] == {"status": "checked", "confirmed": [], "refused": [1]}


def test_a_statement_the_model_confirms_is_answered_from_memory_as_before(tmp_path):
    stub = Stub(answers=True)
    db, services = individual(tmp_path, stub)
    done, events = run(db, services, "Does the Orbix server support encryption at rest?")
    assert done["status"] == "completed" and done["result"]["memory"]["answered_from_memory"] is True
    decision = next(e["detail"] for e in events if e["kind"] == "decision")
    assert decision["needs_web"] is False and decision["memory_check"] == {"status": "checked", "confirmed": [1],
                                                                             "refused": []}


def test_when_the_check_cannot_run_the_lexical_decision_stands_and_the_event_says_so(tmp_path):
    stub = Stub(error=InferenceError("unavailable", "cannot connect: Connection refused"))
    db, services = individual(tmp_path, stub)
    done, events = run(db, services, "Does the Orbix server support encryption at rest?")
    assert done["result"]["memory"]["answered_from_memory"] is True
    decision = next(e["detail"] for e in events if e["kind"] == "decision")
    assert decision["needs_web"] is False and decision["memory_check"]["status"] == "skipped"
    assert "unavailable" in decision["memory_check"]["reason"]


def test_without_any_model_the_decision_event_is_exactly_the_0_7_0_one(tmp_path):
    db, services = individual(tmp_path, None)
    done, events = run(db, services, "Does the Orbix server support encryption at rest?")
    decision = next(e["detail"] for e in events if e["kind"] == "decision")
    assert done["result"]["memory"]["answered_from_memory"] is True
    assert set(decision) == {"needs_web", "reasons", "knowledge"} and decision["needs_web"] is False
