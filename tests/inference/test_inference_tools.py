"""Tool calls: the model suggests, NeuroSciFi validates against the allow-list, permissions and argument schemas,
and executes through the existing authorised functions."""
import json

import pytest

from neuroscifi.inference import tools
from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, state

T0 = 1_795_200_000.0


@pytest.fixture
def db(tmp_path):
    conn = store.create(tmp_path / "i.sqlite", T0)
    try:
        yield conn
    finally:
        conn.close()


def call(name, arguments):
    return {"id": "c1", "type": "function", "function": {"name": name, "arguments": arguments}}


def test_decide_refuses_unlisted_ungranted_and_invalid_calls():
    reg = tools.default_registry()
    assert reg.decide(call("forget_everything", "{}"))["reason"] == "not on the allow-list"
    assert "not granted" in reg.decide(call("web_research", '{"objective": "rivers"}'))["reason"]
    assert reg.decide(call("knowledge_query", "{not json"))["reason"] == "arguments are not JSON"
    assert reg.decide(call("knowledge_query", "[1]"))["reason"] == "arguments must be a JSON object"
    bad = reg.decide(call("knowledge_query", '{"topic": "x", "limit": 99, "sql": "drop"}'))
    assert not bad["allowed"] and "limit" in bad["reason"] and "sql" in bad["reason"]
    ok = reg.decide(call("knowledge_query", '{"topic": "rivers", "limit": 3}'))
    assert ok["allowed"] and ok["arguments"] == {"topic": "rivers", "limit": 3}


def test_only_granted_tools_are_offered():
    names = [t["function"]["name"] for t in tools.default_registry().specs()]
    assert names == ["knowledge_query", "research_task"]
    assert "web_research" in [t["function"]["name"] for t in tools.default_registry({tools.READ, tools.SUBMIT}).specs()]


def test_execute_runs_the_authorised_function(db):
    out = tools.default_registry().execute(call("knowledge_query", '{"topic": "rivers"}'), tools.Context(db, T0))
    assert out["status"] == "executed" and out["result"]["items"] == []


def test_granted_research_still_obeys_the_web_switch(db):
    reg = tools.default_registry({tools.READ, tools.SUBMIT})
    out = reg.execute(call("web_research", '{"objective": "latest Zephyr compiler"}'), tools.Context(db, T0))
    assert out["status"] == "error" and "web" in out["error"].lower()          # web access is off by default
    state.set_config(db, {"enabled": True}, T0)
    out = reg.execute(call("web_research", '{"objective": "latest Zephyr compiler"}'), tools.Context(db, T0))
    assert out["status"] == "executed" and orchestrator.get(db, out["result"]["id"])["status"] == "queued"


def test_tool_loop_refuses_a_suggested_call_and_returns_untrusted_output(gateway, fake, db):
    fake.replies += [{"tool_calls": [{"name": "web_research", "arguments": '{"objective": "x y z"}'},
                                     {"name": "knowledge_query", "arguments": '{"topic": "rivers"}'}]},
                     {"content": "Nothing is known yet."}]
    out = tools.run_with_tools(gateway, [{"role": "user", "content": "research rivers"}], tools.default_registry(),
                               tools.Context(db, T0), purpose="t")
    assert out["result"].text == "Nothing is known yet." and out["rounds"] == 2
    assert [(d["name"], d["status"]) for d in out["decisions"]] == [("web_research", "refused"),
                                                                    ("knowledge_query", "executed")]
    tool_msgs = [m for m in fake.requests[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and all(json.loads(m["content"])["untrusted"] for m in tool_msgs)
    assert [t["function"]["name"] for t in fake.requests[0]["tools"]] == ["knowledge_query", "research_task"]
    assert state.has_tables(db) and orchestrator.listing(db) == []                  # nothing was queued


def test_tool_loop_is_bounded(gateway, fake, db):
    fake.replies += [{"tool_calls": [{"name": "knowledge_query", "arguments": '{"topic": "a b"}'}]}] * 5
    out = tools.run_with_tools(gateway, [{"role": "user", "content": "loop"}], tools.default_registry(),
                               tools.Context(db, T0), purpose="t", max_rounds=3)
    assert out["rounds"] == 3 and len(fake.requests) == 3 and "tools" not in fake.requests[-1]
