"""Wave 2, lane U (directive §12): "a passing system must remain controllable and functionally reliable even when new
cognitive mechanisms fail". For every entry point of every Wave 1 mechanism, with every Wave 1 setting switched ON,
the entry point is made to raise, to time out (TimeoutError, as a timed-out call surfaces in-process) or to return
garbage (monkeypatched where the product looks it up), and one owner session is run through the real product paths:

  answered   the owner's question, asked on the console (POST /api/web/tasks), is run by the research worker
             (scheduler.work, offline: FixtureWeb + CorpusProvider) and completes with a quoted answer
  owner      with a background task queued first, the worker claims the owner's task first (scheduler.claim_next)
  beliefs    a belief read (instance.beliefs.compute, without its state_version) is what it was before the fault
  status     the console (GET /api/self, /api/web/status, /api/presence, /api/inventory, the epoch's decision) and
             Pocket (state, self_status) status endpoints answer 200
  control    quiet on: a background task is not claimed, the owner's is; the owner's stop (autonomy disabled): the
             idle investigator queues nothing; the owner's cancel cancels
  timer      the timer's pass (scheduler.tick, as its service runs it) completes, so the work it did before Wave 1
             (monitors, prediction refreshes, curiosity) is not lost to a Wave 1 failure. Not in the lane brief's
             list: added from the directive's sentence above
  visible    the failure shows: a log record (WARNING or above), an 'error' event of a task, a self-description
             field that says it could not be read, a timer report entry, the error returned to the owner's own
             command, or the garbage itself shown as it came where the owner reads it; never silent
  fired      the faulty entry point was reached on these paths (otherwise the case proves nothing)

The voice follow-up has its own test below (a hub with fake audio engines). Nothing here needs a model, a GPU, a
phone or systemd."""
from contextlib import closing, redirect_stderr, redirect_stdout
import http.client
import importlib
import io
import json
import logging
import random
import threading
import time

import pytest

from neuroscifi import cli as nsf
from neuroscifi.console import server as console_server
from neuroscifi.instance import beliefs, evidence, store
from neuroscifi.webcortex import autonomy, orchestrator, scheduler, state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from test_pocket_research import PAGES, QUESTION, Spark, corpus, html

TOKEN = "uat-fault-token-" + "f" * 32
GARBAGE = "staged-garbage-value"     # ASCII, so it is found in JSON as it is shown
MODES = ("raise", "timeout", "garbage")
MARKER = "SUBJECT-MARKER-5513"

# Every Wave 1 setting on (the "everything on" regime), with what the scenario needs.
INSTANCE_ON = {"internal_state_history": True, "predictive_regulation": True, "executive_rule": "information-v1",
               "decision_information": True, "imagine_what_if": True, "imagine_forecast": True,
               "imagine_retrospect": True, "imagine_scenarios": True, "broadcast": "on"}
WEB_ON = {"enabled": True, "pocket_research": True, "min_domain_interval": 0.0, "salience_record": True,
          "curiosity_learning_progress": True, "answer_monitoring": True, "selective_answering": True,
          "verification_escalation": True, "adaptive_depth": True, "hypothesis_fields": True, "premise_recheck": True,
          "idle_recheck": True, "hypothesis_abandon": True, "reflection": True, "reflection_interval_days": 1.0,
          "pocket_recent": True}

# (mechanism, module, attribute): every entry point the product calls
ENTRIES = (
    ("internal_state", "neuroscifi.internal_state", "snapshot"),
    ("internal_state", "neuroscifi.internal_state", "record"),
    ("internal_state", "neuroscifi.internal_state", "history"),
    ("predictive_gate", "neuroscifi.resources.forecast", "assess"),
    ("salience", "neuroscifi.webcortex.salience", "score"),
    ("information_rule", "neuroscifi.instance.information", "information_policy"),
    ("information_rule", "neuroscifi.instance.information", "assessed_gains"),
    ("information_rule", "neuroscifi.instance.information", "processing"),
    ("imagine", "neuroscifi.instance.imagine", "what_if"),
    ("imagine", "neuroscifi.instance.imagine", "question_values"),
    ("imagine", "neuroscifi.instance.imagine", "retrospect"),
    ("imagine", "neuroscifi.instance.forecast", "forecast"),
    ("imagine_scenarios", "neuroscifi.webcortex.scenarios", "tick"),
    ("self_description", "neuroscifi.self_description", "_build"),
    ("broadcast", "neuroscifi.broadcast", "offer"),
    ("broadcast", "neuroscifi.broadcast", "score"),
    ("monitoring", "neuroscifi.webcortex.monitoring", "assess"),
    ("monitoring", "neuroscifi.webcortex.monitoring", "depth"),
    ("monitoring", "neuroscifi.webcortex.monitoring", "escalation"),
    ("hypothesis_fields", "neuroscifi.webcortex.evolution", "refresh_counter_evidence"),
    ("premises", "neuroscifi.webcortex.premises", "after_consolidation"),
    ("recheck", "neuroscifi.webcortex.recheck", "run"),
    ("reflection", "neuroscifi.webcortex.reflection", "scheduled"),
    ("strategies", "neuroscifi.webcortex.strategies", "active"),
    ("curiosity_progress", "neuroscifi.webcortex.curiosity", "progress"),
)


class Fault:
    """Replaces one entry point by a function that counts its calls and fails in the chosen way."""

    def __init__(self, monkeypatch, module, name, mode):
        self.mode, self.calls, self.where = mode, 0, f"{module}.{name}"
        monkeypatch.setattr(importlib.import_module(module), name, self._faulty)

    def _faulty(self, *args, **kwargs):
        self.calls += 1
        if self.mode == "marked":        # a message that names a subject (review2 L1)
            raise RuntimeError(f"failure about the subject {MARKER}")
        if self.mode == "raise":
            raise RuntimeError(f"staged failure of {self.where}")
        if self.mode == "timeout":
            time.sleep(0.01)
            raise TimeoutError(f"staged timeout of {self.where}")
        return GARBAGE


class Rig:
    """One individual with every Wave 1 setting on, its console, its Pocket bridge with one paired phone, readings in
    an epoch, and a web question the fixture web answers."""

    def __init__(self, tmp_path):
        self.spark = Spark(tmp_path)
        self.path = self.spark.instance
        now = time.time()
        rng = random.Random("uat-fault")
        with closing(store.connect(self.path)) as db:
            state.set_config(db, WEB_ON, now)
            store.set_config(db, INSTANCE_ON, now)
            evidence.start_epoch(db, "e", now - 300)
            for i in range(24):
                action = rng.randint(0, 1)
                evidence.observe(db, {"id": f"r-{i}", "epoch": "e", "channel": "reference" if i < 12 else "word",
                                      "root": f"ref-{i % 3}" if i < 12 else "p", "action": action,
                                      "bit": (i % 2) ^ action if i >= 12 else action, "provenance": "simulated",
                                      "actor": "simulator"}, now - 200 + i)
            autonomy.enable(db, now - 100)
        self.console = console_server.ConsoleServer(self.path, 0, token=TOKEN)
        threading.Thread(target=self.console.serve_forever, daemon=True).start()
        status, phone = self.spark.req("pair", {"code": "invite"})
        assert status == 201
        self.phone = phone["key"]

    def http(self, method, route, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.console.server_port, timeout=60)
        try:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            data = None
            if body is not None:
                data, headers["Content-Type"] = json.dumps(body), "application/json"
            conn.request(method, route, data, headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            conn.close()

    def db(self):
        return closing(store.connect(self.path))

    def close(self):
        self.console.shutdown()
        self.console.server_close()
        self.spark.close()


@pytest.fixture
def web():
    """The offline web of tests/test_pocket_research.py (three pages about the Zephyr compiler), on loopback."""
    w = FixtureWeb().start()
    for (host, path), (title, date, paragraphs) in PAGES.items():
        w.add(host, path, html(title, date, paragraphs))
    try:
        yield w
    finally:
        w.stop()


@pytest.fixture
def rig(tmp_path):
    from neuroscifi.resources import policy
    policy._FORECAST_FAILURES.clear()     # logged once per kind of failure per process: each case is a new process
    r = Rig(tmp_path)
    yield r
    r.close()


def _beliefs(r):
    """The belief read without its state_version (every write to the file, a research task's included, moves that)."""
    with r.db() as db:
        read = beliefs.compute(db, "e")
    read.pop("state_version", None)
    return json.dumps(read, sort_keys=True, default=str)


def _errors(r):
    with r.db() as db:
        return db.execute("SELECT count(*) FROM web_events WHERE kind='error'").fetchone()[0]


def _queue(db, objective, origin, at):
    prepared = orchestrator.prepare(db, objective, at, mode="quick_lookup", origin=origin)
    with store.write(db):
        return orchestrator.insert(db, prepared, at)


def _field_errors(record, out=None):
    """Every self-description field that reports it could not be read."""
    out = [] if out is None else out
    if isinstance(record, dict):
        if "error" in record and record.get("value") is None:
            out.append(record["error"])
        for v in record.values():
            _field_errors(v, out)
    return out


def _owner_command(r, *argv):
    """`nsf imagine ...` in this process: (exit code, stdout, stderr); an exception is what the owner's terminal shows
    (a traceback)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = nsf.main(["imagine", *argv[:1], "--db", str(r.path), *argv[1:]])
    except SystemExit as exc:
        code = exc.code
    except Exception as exc:                      # noqa: BLE001 - the owner's command failing is the visible outcome
        return 1, out.getvalue(), repr(exc)
    return code, out.getvalue(), err.getvalue()


def session(r, web):
    """One owner session through the product, with whatever fault is installed. Returns what each check saw."""
    seen = {"surfaced": [], "shown": []}
    with r.db() as db:              # queued long enough ago that it may run first (claim_next's AUTONOMOUS_DEFER): its
        background = _queue(db, QUESTION, "curiosity", time.time() - scheduler.AUTONOMOUS_DEFER - 60)  # finding
    status, task = r.http("POST", "/api/web/tasks", {"objective": QUESTION, "mode": "quick_lookup"})
    owner = task.get("id")
    try:
        out = scheduler.work(r.path, once=True, max_tasks=2, fixture=web, providers=[corpus()])
        seen["processed"] = {p["task_id"]: p["status"] for p in out["processed"]}
    except Exception as exc:                       # noqa: BLE001 - recorded: the owner would see the worker fail
        seen["processed"], seen["worker_error"] = {}, repr(exc)
    with r.db() as db:
        done = orchestrator.get(db, owner) if owner else {}
        bg = orchestrator.get(db, background)
    seen["shown"].append(json.dumps(done.get("result"), default=str))
    quotes = [a for a in (done.get("result") or {}).get("answer") or [] if a.get("type") == "quote"]
    seen["answered"] = status == 200 and seen["processed"].get(owner) == "completed" and bool(quotes)
    seen["background_status"] = bg["status"]
    # the owner first, whatever is queued before
    with r.db() as db:
        now = time.time()
        _queue(db, "Zephyr compiler release cadence", "curiosity", now - 30)
        mine = _queue(db, "Zephyr compiler maintainers", "console", now - 10)
        first = scheduler.claim_next(db, "uat-first", now)
        seen["owner_first"] = bool(first) and first[0] == mine
        with store.write(db):
            db.execute("UPDATE web_tasks SET status='cancelled', lease_holder=NULL WHERE status='queued'")
    # status endpoints
    codes = {}
    for route in ("/api/self", "/api/web/status", "/api/presence", "/api/inventory", "/api/epochs/e/decision",
                  "/api/web/autonomy"):
        code, body = r.http("GET", route)
        codes[route] = code
        seen["shown"].append(json.dumps(body, default=str))
        if route == "/api/self":
            seen["self_errors"] = _field_errors(body.get("self")) + ([body["self_error"]] if body.get("self_error")
                                                                     else [])
    for route in ("state", "self_status"):
        code, body = r.spark.req(route, {}, r.phone)
        codes[f"pocket:{route}"] = code
        seen["shown"].append(json.dumps(body, default=str))
    seen["status"] = codes
    # the timer, as its service runs it (queue only: the worker runs what it queues)
    with r.db() as db:
        try:
            report = scheduler.tick(db, time.time(), None, queue=True)
            seen["tick"] = json.dumps(report, default=str)
        except Exception as exc:                   # noqa: BLE001 - recorded
            seen["tick_error"] = repr(exc)
    # the owner's own imagination commands: a failure is returned to the owner, who asked
    for argv in (("what-if", "e", "--flip", "r-12"), ("questions", "e"), ("retrospect", "e", "partner:p"),
                 ("forecast", "1", "--state", "{}")):
        code, out, err = _owner_command(r, *argv)
        seen["shown"].append(out)
        if "staged" in err or "Error" in err:
            seen["surfaced"].append(err)
    # control: quiet, the owner's stop, the owner's cancel
    with r.db() as db:
        now = time.time()
        store.set_config(db, {"quiet": True}, now)
        held = _queue(db, "Zephyr compiler release notes archive", "curiosity", now - 900)
        asked = _queue(db, "Zephyr compiler licence", "console", now - 1)
        first = scheduler.claim_next(db, "uat", now)
        second = scheduler.claim_next(db, "uat", now + 1)
        seen["quiet"] = bool(first) and first[0] == asked and (second is None or second[0] != held)
        store.set_config(db, {"quiet": False}, now + 2)
        autonomy.disable(db, now + 3)
        before = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0]
        autonomy.idle(db, now + 4, worker="uat")
        seen["stop"] = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == before
        queued = _queue(db, "Zephyr compiler mirror list", "console", now + 5)
    code, cancelled = r.http("POST", f"/api/web/tasks/{queued}/cancel", {})
    seen["cancel"] = code == 200 and cancelled.get("status") == "cancelled"
    return seen


def check(r, web, monkeypatch, caplog, module, name, mode):
    caplog.set_level(logging.WARNING)
    before, errors = _beliefs(r), _errors(r)
    fault = Fault(monkeypatch, module, name, mode)
    seen = session(r, web)
    after = _beliefs(r)
    visible = {"log": [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING],
               "error_events": _errors(r) - errors, "self_fields": seen.get("self_errors"),
               "tick": [x for x in (seen.get("tick_error"), seen.get("tick")) if x and ("staged" in x or
                                                                                     "Error" in x)],
               "surfaced": seen["surfaced"] + ([seen["worker_error"]] if seen.get("worker_error") else []),
               "garbage_shown": [s[:80] for s in seen["shown"] + [seen.get("tick") or ""] if GARBAGE in s]}
    problems = []
    if not fault.calls:
        problems.append(f"the fault was never reached ({fault.where})")
    if not seen["answered"]:
        problems.append(f"the owner's question was not answered: {seen['processed']} {seen.get('worker_error')}")
    if not seen["owner_first"]:
        problems.append("the owner's task was not claimed first")
    if after != before:
        problems.append("a belief read changed")
    bad = {k: v for k, v in seen["status"].items() if v != 200}
    if bad:
        problems.append(f"status endpoints failed: {bad}")
    if not seen["quiet"]:
        problems.append("quiet did not hold the background task / the owner's task was not claimed")
    if not (seen["stop"] and seen["cancel"]):
        problems.append(f"the owner's stop or cancel failed: stop={seen['stop']} cancel={seen['cancel']}")
    if seen.get("tick_error"):
        problems.append(f"the timer's pass failed: {seen['tick_error']}")
    if fault.calls and not any(visible.values()):
        problems.append("the failure was silent")
    return problems, fault, {k: v for k, v in visible.items() if v}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("mechanism,module,name", ENTRIES, ids=[f"{m}:{n}" for m, _, n in ENTRIES])
def test_a_failing_mechanism_leaves_the_owner_in_control(rig, web, monkeypatch, caplog, mechanism, module, name,
                                                         mode):
    problems, fault, visible = check(rig, web, monkeypatch, caplog, module, name, mode)
    assert not problems, (problems, fault.calls, visible)


def _stored_text(r):
    """Every stored text a failure could be written into: task events, task errors, decisions, the timer's own
    records."""
    with r.db() as db:
        return [t for (t,) in db.execute("SELECT detail FROM web_events UNION ALL SELECT coalesce(error,'') FROM "
                                         "web_tasks UNION ALL SELECT reason FROM decisions")]


@pytest.mark.parametrize("mechanism,module,name", ENTRIES, ids=[f"{m}:{n}" for m, _, n in ENTRIES])
def test_a_failure_message_naming_a_subject_reaches_no_log_and_no_stored_record(rig, web, monkeypatch, caplog,
                                                                                mechanism, module, name):
    """Wave 2 lane X (review2 L1): the guards log and store the exception's TYPE and ids only. A message may name a
    subject, and the process log (journal) is out of the owner's forget's reach, as is an event on another subject's
    task. Every entry point fails with a message holding a marker; the owner keeps control (the checks above) and the
    marker is in no log record and no stored event, task error or decision."""
    problems, fault, visible = check(rig, web, monkeypatch, caplog, module, name, "marked")
    assert not problems, (problems, fault.calls, visible)
    logged = [rec.getMessage() for rec in caplog.records if MARKER in rec.getMessage()]
    assert not logged, logged
    stored = [t for t in _stored_text(rig) if MARKER in t]
    assert not stored, stored
