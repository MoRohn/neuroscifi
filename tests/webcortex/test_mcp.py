"""`nsf web mcp`: JSON-RPC 2.0 over stdio. In-process round-trips use in-memory streams and an injected services
factory (offline FixtureWeb + CorpusProvider); one test spawns the real command."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import mcp, services as svc_mod, state
from test_orchestrator import QUESTION, T0, corpus, serve

ROOT = Path(__file__).resolve().parents[2]


def request(rid, method, params=None):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def call(rid, name, arguments):
    return request(rid, "tools/call", {"name": name, "arguments": arguments})


def exchange(path, lines, factory=None):
    out, err = io.StringIO(), io.StringIO()
    assert mcp.serve(path, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out, stderr=err,
                     services_factory=factory, clock=lambda: T0) == 0
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert all(r["jsonrpc"] == "2.0" for r in responses)   # stdout carries protocol messages only
    return {r["id"]: r for r in responses}, err.getvalue()


def payload(response):
    result = response["result"]
    assert result["isError"] is False, result
    text = result["content"][0]["text"]
    assert text.startswith(mcp.UNTRUSTED_HEADER)
    return json.loads(text[len(mcp.UNTRUSTED_HEADER):])


def error_text(response):
    result = response["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text.startswith("Error: ") and "Traceback" not in text
    return text


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    store.create(path, T0).close()
    return path


def enable(path, **extra):
    db = store.connect(path)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, **extra}, T0)
    finally:
        db.close()


@pytest.fixture
def factory(web):
    serve(web)
    return lambda db, path: svc_mod.build(db, path, fixture=web, providers=[corpus()], clock=lambda: T0)


def test_handshake_listing_and_protocol_errors(individual):
    lines = [request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             request(2, "tools/list"), request(3, "ping"), request(4, "resources/list"),
             request(5, "tools/call", {"name": "web_forget", "arguments": {}}), "",
             json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}}),
             request(8, "initialize", {"protocolVersion": "1999-01-01"})]
    got, err = exchange(individual, lines)
    init = got[1]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "neuroscifi-webcortex"
    names = [t["name"] for t in got[2]["result"]["tools"]]
    assert names == ["web_search", "web_research", "web_task", "web_evidence", "web_knowledge", "self_status"]
    from neuroscifi import __version__
    assert init["serverInfo"]["version"] == __version__      # the package version, not a hard-coded one
    assert not any(word in n for n in names for word in ("forget", "config", "review", "monitor", "delete"))
    for tool in got[2]["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object" and tool["inputSchema"]["additionalProperties"] is False
    assert got[3]["result"] == {}
    assert got[4]["error"]["code"] == -32601
    assert got[5]["error"]["code"] == -32602
    assert got[8]["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert set(got) == {1, 2, 3, 4, 5, 8}   # notifications are never answered
    assert "ready" in err


def test_invalid_requests_are_answered_not_crashed(individual):
    lines = ["{not json", json.dumps([1, 2]), json.dumps({"id": 9, "method": "ping"}),
             json.dumps({"jsonrpc": "2.0", "id": True, "method": "ping"}), request(10, "ping")]
    out = io.StringIO()
    mcp.serve(individual, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out, stderr=io.StringIO())
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("error", {}).get("code") for r in responses] == [-32700, -32600, -32600, -32600, None]
    assert responses[2]["id"] == 9 and responses[-1] == {"jsonrpc": "2.0", "id": 10, "result": {}}


def test_tools_refuse_while_disabled_and_validate_inputs(individual):
    lines = [call(1, "web_search", {"query": "zephyr compiler"}), call(2, "web_research", {"objective": QUESTION}),
             call(3, "web_search", {"query": "x" * 513}), call(4, "web_research", {"objective": "y" * 2001}),
             call(5, "web_search", {"query": "ok", "limit": 0}), call(6, "web_search", {"query": "ok", "evil": 1}),
             call(7, "web_task", {}), call(8, "web_task", {"task_id": "../../etc"}),
             call(9, "web_research", {"objective": QUESTION, "mode": "rm -rf"}),
             call(10, "web_knowledge", {"topic": "   "}), call(11, "web_task", {"task_id": "nope"}),
             call(12, "web_knowledge", {"topic": "zephyr"})]
    got, _ = exchange(individual, lines)
    assert "disabled" in error_text(got[1]) and "disabled" in error_text(got[2])
    assert "1 to 512" in error_text(got[3]) and "1 to 2000" in error_text(got[4])
    assert "between 1 and 20" in error_text(got[5]) and "unknown argument" in error_text(got[6])
    assert "missing required argument 'task_id'" in error_text(got[7]) and "invalid format" in error_text(got[8])
    assert "must be one of" in error_text(got[9]) and "blank" in error_text(got[10])
    assert "No research task" in error_text(got[11])
    assert payload(got[12])["items"] == []   # local reads work offline


def test_research_task_evidence_and_knowledge_are_wrapped_as_untrusted(individual, factory):
    enable(individual)
    got, err = exchange(individual, [call(1, "web_research", {"objective": QUESTION, "mode": "fact_verification"})],
                        factory)
    research = payload(got[1])
    task_id = research["task"]["id"]
    assert research["task"]["status"] == "completed" and research["task"]["mode"] == "fact_verification"
    assert "report" not in research   # token-bounded by default: the context pack, the full report on request
    context = research["context"]
    assert context["untrusted"] is True and context["source"] == f"neuroscifi:web-task/{task_id}/context"
    pack = json.loads(context["text"])
    assert 0 < pack["tokens"] == research["context_tokens"] <= 2000 and pack["items"]
    assert any("4.2.0" in json.dumps(i) for i in pack["items"])
    assert "web_research ok" in err and QUESTION not in err   # logs name the tool, never the arguments
    got, _ = exchange(individual, [call(6, "web_task", {"task_id": task_id, "full_report": True})], factory)
    full = payload(got[6])
    report = full["report"]
    assert report["untrusted"] is True and report["source"] == f"neuroscifi:web-task/{task_id}"
    parsed = json.loads(report["text"])
    assert any("4.2.0" in a["text"] for a in parsed["answer"] if a.get("type") == "quote")
    assert parsed["context_pack"] == pack and parsed["usage"]["tokens"] == pack["tokens"]   # tokens are charged
    got, _ = exchange(individual, [call(2, "web_task", {"task_id": task_id}),
                                   call(3, "web_evidence", {"task_id": task_id}),
                                   call(4, "web_knowledge", {"topic": "Zephyr compiler version"}),
                                   call(5, "web_search", {"query": "Zephyr compiler release", "limit": 2})], factory)
    task = payload(got[2])
    assert task["task"]["status"] == "completed" and task["context"]["untrusted"] is True and "report" not in task
    evidence = payload(got[3])
    assert len(evidence["sources"]) == 3 and evidence["claims"]
    assert all(s["content"]["untrusted"] and s["content"]["source"] == s["url"] for s in evidence["sources"])
    assert all(c["content"]["untrusted"] and c["content"]["text"] for c in evidence["claims"])
    assert evidence["events"]["untrusted"] is True
    known = payload(got[4])
    assert known["matched"] >= 1 and all(i["content"]["untrusted"] for i in known["items"])
    found = payload(got[5])
    assert 1 <= len(found["results"]) <= 2
    assert all(r["content"]["untrusted"] and r["content"]["source"] == r["url"] for r in found["results"])
    assert all("title" not in r and "snippet" not in r for r in found["results"])


def test_privacy_screen_applies(individual, factory):
    enable(individual, private_terms=["Zorblax"])
    got, _ = exchange(individual, [call(1, "web_search", {"query": "Zorblax compiler"}),
                                   call(2, "web_research", {"objective": "Zorblax compiler release"}),
                                   call(3, "web_search", {"query": "write to me@example.org"})], factory)
    assert all("private data" in error_text(got[i]) for i in (1, 2, 3))


def test_stdout_stays_protocol_only_when_a_handler_prints(individual, factory, monkeypatch):
    from neuroscifi.webcortex import knowledge
    real = knowledge.query

    def noisy(*args, **kwargs):
        print("stray output from a module")
        return real(*args, **kwargs)
    monkeypatch.setattr(knowledge, "query", noisy)
    got, err = exchange(individual, [call(1, "web_knowledge", {"topic": "zephyr"})])
    assert payload(got[1])["items"] == [] and "stray output" in err


def test_internal_errors_have_no_stack_trace(individual, monkeypatch):
    from neuroscifi.webcortex import knowledge

    def broken(*args, **kwargs):
        raise RuntimeError("secret internals /home/x")
    monkeypatch.setattr(knowledge, "query", broken)
    got, _ = exchange(individual, [call(1, "web_knowledge", {"topic": "zephyr"})])
    text = error_text(got[1])
    assert text == "Error: internal error (RuntimeError)"


def test_spawned_server_round_trip(individual):
    lines = [request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             request(2, "tools/list"), call(3, "web_search", {"query": "zephyr"}),
             call(4, "web_knowledge", {"topic": "zephyr"}), request(5, "shutdown")]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-m", "neuroscifi.cli", "web", "mcp", "--db", str(individual)],
                          input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60, env=env,
                          cwd=str(individual.parent))
    assert proc.returncode == 0, proc.stderr
    responses = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines())}
    assert set(responses) == {1, 2, 3, 4, 5}
    assert responses[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(responses[2]["result"]["tools"]) == 6        # five web tools and self_status (ADR-053)
    assert "disabled" in error_text(responses[3])
    assert payload(responses[4])["matched"] == 0
    assert responses[5]["error"]["code"] == -32601


# ---- review remediation V2 ---------------------------------------------------------------------------------------

def test_non_finite_ids_and_deep_nesting_get_json_rpc_errors(individual):
    """C-P2-5 (coderev r5): NaN ids and runaway nesting are answered with JSON-RPC errors; the server keeps
    serving the next message."""
    out, err = io.StringIO(), io.StringIO()
    lines = ['{"jsonrpc":"1.0","id":NaN}', '{"jsonrpc":"2.0","id":NaN,"method":5}', "[" * 200000,
             '{"jsonrpc":"2.0","id":Infinity,"method":"ping"}', '{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}',
             request(9, "ping")]
    assert mcp.serve(individual, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out, stderr=err) == 0
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(responses) == 6 and responses[-1] == {"jsonrpc": "2.0", "id": 9, "result": {}}
    assert all(r["error"]["code"] in (-32700, -32600) and r["id"] is None for r in responses[:5])
    assert "nesting too deep" in responses[2]["error"]["message"]


def test_web_research_is_rate_limited_per_process(individual, factory):
    """S-P3-10: at most config.mcp_research_per_hour web_research calls per hour per server process."""
    enable(individual, mcp_research_per_hour=1)
    got, _ = exchange(individual, [call(1, "web_research", {"objective": QUESTION, "mode": "quick_lookup"}),
                                   call(2, "web_research", {"objective": QUESTION, "mode": "quick_lookup"})], factory)
    assert got[1]["result"]["isError"] is False
    assert "rate limit" in error_text(got[2]) and "mcp_research_per_hour" in error_text(got[2])


def test_mcp_origin_tasks_share_a_daily_cap(individual, factory):
    """S-P3-10: MCP-origin tasks are clamped to config.daily_mcp like autonomous research."""
    enable(individual, daily_mcp={"tasks": 1, "requests": 400, "bytes": 80_000_000})
    got, _ = exchange(individual, [call(1, "web_research", {"objective": QUESTION, "mode": "quick_lookup"})], factory)
    assert got[1]["result"]["isError"] is False
    got, _ = exchange(individual, [call(2, "web_research", {"objective": QUESTION, "mode": "quick_lookup"})], factory)
    assert "daily MCP cap reached: tasks 1" in error_text(got[2])
    db = store.connect(individual)
    try:
        from neuroscifi.webcortex import orchestrator, scheduler
        used = scheduler.autonomous_usage(db, T0, mcp=True)["requests"]
        state.set_config(db, {"daily_mcp": {"tasks": 9, "requests": used + 5, "bytes": 80_000_000}}, T0)
        clamped = orchestrator.submit(db, QUESTION, T0, mode="quick_lookup", origin="mcp")
        assert clamped["budget"]["max_requests"] <= 5
    finally:
        db.close()
