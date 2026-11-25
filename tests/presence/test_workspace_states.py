"""Presence states that were declared and never shown, now driven by real rows (ADR-053): planning, predicting,
simulating, analyzing, interpreting and executing; model calls of another process (the research worker) published
through <db>.resources/; paused writes named as their own cause."""
from contextlib import closing
import json
import os
import subprocess
import sys
import time

import pytest

from neuroscifi.instance import graphs, imagined, store
from neuroscifi.presence import activity, aggregator
from neuroscifi.webcortex import orchestrator, state

NOW = time.time()


@pytest.fixture
def inst(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, NOW - 7200)) as db:
        state.set_config(db, {"enabled": True}, NOW - 7200)
    db = store.connect(path)
    yield db, path
    db.close()


def derive(db, path):
    return aggregator.derive(db, path, NOW)


def acts(s):
    return [c["activity"] for c in s["activities"]]


def test_planning_while_the_query_plan_is_made(inst):
    db, path = inst
    task = orchestrator.submit(db, "Was the Zephyr compiler version 4.2.0 released?", NOW - 60, origin="console")
    db.execute("UPDATE web_tasks SET status='discovering', stage='discovering', started_at=?, lease_holder='w:1', "
               "lease_expires_at=? WHERE id=?", (NOW - 5, NOW + 600, task["id"]))
    state.log_event(db, task["id"], "decision", {"needs_web": True, "reasons": []}, NOW - 1, stage="discovering")
    assert derive(db, path)["primary"]["activity"] == "planning"
    state.log_event(db, task["id"], "query", {"text": "zephyr 4.2.0"}, NOW - 0.5, stage="discovering")
    assert derive(db, path)["primary"]["activity"] == "searching"


def test_predicting_and_simulating_come_from_recorded_rows_only(inst):
    db, path = inst
    assert "predicting" not in acts(derive(db, path)) and "simulating" not in acts(derive(db, path))
    state.log_event(db, None, "prediction", {"prediction_id": 1}, NOW - 1)
    assert "predicting" in acts(derive(db, path))
    imagined.record(db, now=NOW - 1, **imagined.example(5))
    sim = next(c for c in derive(db, path)["activities"] if c["activity"] == "simulating")
    assert sim["priority"] == "background" and "not evidence" in sim["detail"]
    later = aggregator.derive(db, path, NOW + aggregator.FINISHED_WINDOW + 5)
    assert "predicting" not in acts(later) and "simulating" not in acts(later)
    family = aggregator.derive(db, path, NOW, family="f" * 16)
    assert "predicting" not in acts(family) and "simulating" not in acts(family)


def test_analyzing_while_a_graph_job_holds_its_lease(inst):
    db, path = inst
    with graphs.lease(db, "ctx-a", "graphs.learn:1:2", NOW):
        c = next(c for c in derive(db, path)["activities"] if c["activity"] == "analyzing")
        assert c["priority"] == "foreground" and "ctx-a" in c["detail"] and "learn" in c["detail"]
    assert "analyzing" not in acts(derive(db, path))


def test_interpreting_and_executing_are_in_flight_work_with_their_own_rank(inst):
    db, path = inst
    with activity.track("interpreting", subsystem="voice", purpose="voice request", priority="communication"):
        s = derive(db, path)
        assert s["primary"]["activity"] == "interpreting" and s["primary"]["priority"] == "communication"
        assert s["signals"]["inflight_inference"] == 0          # not a model call
    with activity.track("executing", subsystem="reasoning", purpose="model tool knowledge_query",
                        secondary=("tool_execution",)):
        p = derive(db, path)["primary"]
        assert p["activity"] == "executing" and p["secondary"] == ["tool_execution"]
        assert "knowledge_query" in p["detail"]


def test_an_allow_listed_tool_shows_as_executing_while_it_runs(inst):
    from neuroscifi.inference import tools
    db, path = inst
    seen = []

    def handler(args, ctx):
        seen.append(aggregator.derive(db, path, NOW)["primary"]["activity"])
        return {"ok": True}
    registry = tools.ToolRegistry([tools.Tool("probe", "a probe", {"type": "object", "additionalProperties": False,
                                                                     "properties": {}}, tools.READ, handler)],
                                  granted=(tools.READ,))
    out = registry.execute({"id": "1", "function": {"name": "probe", "arguments": "{}"}}, tools.Context(db, NOW))
    assert out["status"] == "executed" and seen == ["executing"]


def test_a_streaming_call_of_another_process_is_published_and_read(inst, tmp_path):
    db, path = inst
    script = (
        "import sys, time\n"
        "from neuroscifi.presence import activity\n"
        "with activity.track('generating', subsystem='reasoning', purpose='research_answer', task_id='t-1',"
        " publish=sys.argv[1]) as tokens:\n"
        "    tokens(7)\n"
        "    print('ready', flush=True)\n"
        "    sys.stdin.readline()\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    proc = subprocess.Popen([sys.executable, "-c", script, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, env=env)
    try:
        assert proc.stdout.readline().strip() == "ready"
        s = aggregator.derive(db, path, time.time())
        remote = [c for c in s["activities"] if c["task_id"] == "t-1"]
        assert remote and remote[0]["activity"] == "generating" and "research worker" in remote[0]["detail"]
        assert s["signals"]["inflight_inference"] == 1
        published = list((path.parent / f"{path.name}.resources").glob("inflight-*.json"))
        text = published[0].read_text()
        assert "research_answer" in text and "prompt" not in text
    finally:
        proc.stdin.write("\n")
        proc.stdin.close()
        proc.wait(30)
    assert not list((path.parent / f"{path.name}.resources").glob("inflight-*.json"))     # removed when done
    assert not [c for c in aggregator.derive(db, path, time.time())["activities"] if c["task_id"] == "t-1"]


def test_files_of_dead_stale_or_own_processes_are_ignored(inst):
    db, path = inst
    folder = path.parent / f"{path.name}.resources"
    folder.mkdir(exist_ok=True)
    entry = {"activity": "generating", "subsystem": "reasoning", "purpose": "x", "task_id": "t-9", "since": NOW - 3,
             "tokens": 4}
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                          text=True).stdout.strip()
    (folder / f"inflight-{dead}.json").write_text(json.dumps({"at": NOW, "entries": [entry]}))
    (folder / f"inflight-{os.getpid()}.json").write_text(json.dumps({"at": NOW, "entries": [entry]}))
    (folder / f"inflight-{os.getppid()}.json").write_text(json.dumps({"at": NOW - activity.STALE - 1,
                                                                       "entries": [entry]}))
    (folder / "inflight-1.json").write_text("[[[[not json")
    assert activity.remote(path, NOW) == []
    (folder / f"inflight-{os.getppid()}.json").write_text(json.dumps({"at": NOW, "entries": [entry, {"bad": 1}]}))
    assert [e["task_id"] for e in activity.remote(path, NOW)] == ["t-9"]


def test_paused_writes_are_named_as_their_own_cause(inst, monkeypatch):
    db, path = inst
    s = derive(db, path)
    assert "writes" not in s["sources"]
    monkeypatch.setattr(store, "write_allowed", lambda *a, **k: (False, "disk"))
    aggregator._cache.clear()
    s = derive(db, path)
    assert "resource_constrained" in s["condition"] and s["sources"]["writes"] == "paused"
