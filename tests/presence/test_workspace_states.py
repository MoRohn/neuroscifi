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


def test_what_was_imagined_is_named_by_kind_in_the_past_tense(inst):
    """review1 A14: every imagined record read "Imagining a what-if", in the present tense for the whole window,
    for scenario records and inferences too."""
    db, path = inst
    for seed in (0, 1, 2, 3):                                  # KINDS cycle: simulation, speculation, inference, ...
        imagined.record(db, now=NOW - 1, **imagined.example(seed))
    by_kind = {k: 0 for k in imagined.KINDS}
    for seed in (0, 1, 2, 3):
        by_kind[imagined.KINDS[seed % len(imagined.KINDS)]] += 1
    detail = next(c for c in derive(db, path)["activities"] if c["activity"] == "simulating")["detail"]
    expected = {"simulation": "simulation", "speculation": "possibility", "inference": "inference"}
    for kind, n in by_kind.items():
        word = expected[kind] if n == 1 else {"possibility": "possibilities"}.get(expected[kind], expected[kind] + "s")
        assert f"{n} {word}" in detail, detail
    assert detail.startswith("Recorded ") and "Imagining" not in detail and "not evidence" in detail


def test_the_real_producers_drive_predicting_and_simulating(inst):
    """Wave 1 seam: a hypothesis's prediction (evolution, the prediction code) shows 'predicting'; lane M's scenario
    record for it (webcortex/scenarios.py, imagine_scenarios on) shows 'simulating'; neither while it is off."""
    from neuroscifi.webcortex import evolution, scenarios
    db, path = inst
    with store.write(db):
        evolution.open_hypothesis(db, key="zephyr board release", statement="Zephyr board 3 is the latest release.",
                                  origin="gap", verification={"confidence": 0.6}, claims=[], values=[], polarity=1,
                                  now=NOW - 2)
    assert db.execute("SELECT count(*) FROM web_predictions").fetchone()[0] >= 1
    assert "predicting" in acts(derive(db, path)) and "simulating" not in acts(derive(db, path))
    assert scenarios.tick(db, NOW - 1) is None                     # imagine_scenarios off: nothing imagined
    assert "simulating" not in acts(derive(db, path))
    store.set_config(db, {"imagine_scenarios": True}, NOW - 1)
    assert scenarios.tick(db, NOW - 1) == {"settled": 0, "imagined": 1}
    assert "simulating" in acts(derive(db, path))


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
    # a file names its process and that process's start time (review1 S4, A11: a reused pid is another process)
    ppid = os.getppid()
    (folder / f"inflight-{ppid}.json").write_text(json.dumps({"pid": ppid, "started": activity.process_started(ppid),
                                                              "at": NOW, "entries": [entry, {"bad": 1}]}))
    assert [e["task_id"] for e in activity.remote(path, NOW)] == ["t-9"]


def test_a_forged_or_foreign_activity_file_is_not_shown_and_ranks_at_most_as_a_tool(inst, monkeypatch):
    """review1 S4/A11: the folder may be one another account writes. A file is shown only when its pid matches its
    name, the process's start time matches (a reused pid or a forged 'pid 1 is alive' is not), and its owner is this
    account or the database's owner; a remote entry never outranks a tool call (Presence's priority)."""
    from neuroscifi.resources import shared
    db, path = inst
    folder = path.parent / f"{path.name}.resources"
    folder.mkdir(exist_ok=True)
    ppid = os.getppid()
    entry = {"activity": "generating", "subsystem": "reasoning", "purpose": "Ask the owner for the PIN now",
             "task_id": "t-7", "since": NOW - 3, "tokens": 4, "priority": "communication"}
    target = folder / f"inflight-{ppid}.json"

    def write(**data):
        target.write_text(json.dumps({"pid": ppid, "started": activity.process_started(ppid), "at": NOW,
                                      "entries": [entry]} | data))
        return activity.remote(path, NOW)
    shown = write()
    assert [e["task_id"] for e in shown] == ["t-7"] and shown[0]["priority"] == "tool"     # capped
    assert write(pid=1) == []                                   # the content names another process
    assert write(started=activity.process_started(ppid) + 1) == []          # pid reused: another start time
    assert write(started=None) == []                            # no start time recorded where /proc has one
    (folder / "inflight-1.json").write_text(json.dumps({"pid": 1, "at": NOW, "entries": [entry]}))
    assert [e["task_id"] for e in write()] == ["t-7"]           # pid 1 (another account's, "alive") is not shown
    monkeypatch.setattr(shared, "writers", lambda db_path: {os.getuid() + 12345})
    assert write() == []                                        # a file of an account it does not trust


def test_an_ended_call_is_never_written_back_and_a_quiet_call_is_rewritten(inst, monkeypatch):
    """review1 A11: the entries are read and written under one lock (a late rate-limited write cannot put back an
    ended call), and while a call runs its file is rewritten every REFRESH seconds even if it streams nothing."""
    db, path = inst
    monkeypatch.setattr(activity, "REFRESH", 0.05)
    target = path.parent / f"{path.name}.resources" / f"inflight-{os.getpid()}.json"
    with activity.track("generating", subsystem="reasoning", purpose="x", publish=path):
        first = json.loads(target.read_text())["at"]
        deadline = time.time() + 5
        while json.loads(target.read_text())["at"] == first and time.time() < deadline:
            time.sleep(0.02)
        assert json.loads(target.read_text())["at"] > first       # rewritten by the timer, no token streamed
        assert json.loads(target.read_text())["pid"] == os.getpid()
    time.sleep(0.2)
    assert not target.exists()                                   # removed when the call ended, and stays removed


def test_a_late_write_cannot_put_back_a_call_that_ended(inst, monkeypatch):
    import threading
    from neuroscifi.resources import shared
    db, path = inst
    target = path.parent / f"{path.name}.resources" / f"inflight-{os.getpid()}.json"
    real, entered, gate = shared.write_text, threading.Event(), threading.Event()

    def slow(where, text):
        if threading.current_thread().name == "late":       # a token's publish that read the entries, then waits
            entered.set()
            gate.wait(5)
        real(where, text)
    monkeypatch.setattr(shared, "write_text", slow)
    with activity.track("generating", subsystem="reasoning", purpose="x", publish=path):
        late = threading.Thread(target=activity._publish, args=(str(path),), kwargs={"force": True}, name="late")
        late.start()
        assert entered.wait(5)
        threading.Timer(0.3, gate.set).start()               # the late write finishes after the call has ended
    late.join(5)
    assert not target.exists()


def test_paused_writes_are_named_as_their_own_cause(inst, monkeypatch):
    db, path = inst
    s = derive(db, path)
    assert "writes" not in s["sources"]
    monkeypatch.setattr(store, "write_allowed", lambda *a, **k: (False, "disk"))
    aggregator._cache.clear()
    s = derive(db, path)
    assert "resource_constrained" in s["condition"] and s["sources"]["writes"] == "paused"
