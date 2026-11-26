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
          "pocket_recent": True, "salience_attention": True, "arbitration_record": True}

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
    # Wave 2 lane L (ADR-056), added by lane X: salience orders what admission admitted (salience_attention), the
    # arbitration record (arbitration_record), the background loop in the self-description (doing.cycle)
    ("salience_attention", "neuroscifi.webcortex.salience", "attention"),
    ("salience_attention", "neuroscifi.webcortex.salience", "order"),
    ("arbitration_record", "neuroscifi.webcortex.scheduler", "_arbitrated"),
    ("self_description_cycle", "neuroscifi.self_description", "_cycle"),
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
            # Wave 2 lane X: an item verified 30 days ago, which the knowledge re-check and the timer's curiosity
            # both want in the timer's pass, so lane L's arbitration (scheduler._arbitrated) is reached
            from neuroscifi.acceptance.cognition import adversarial
            kid = adversarial._k_item(db, now - 30 * 86_400.0)["provisional"][0]
            # Wave 2 reconciliation: since lane Y (H1) the record is written for a real conflict only, the subject in
            # the timer's curiosity pool as the kind curiosity launches. With its "still holds" prediction due, the
            # subject is left to the due refresh (kind 'prediction', never in that pool); not yet due, curiosity
            # ranks it as stale knowledge, so the re-check's pick takes a subject curiosity could have launched
            with store.write(db):
                db.execute("UPDATE web_predictions SET check_after=? WHERE knowledge_id=? AND outcome IS NULL",
                           (now + 86_400.0, kid))
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


def session(r, web, stage=None):
    """One owner session through the product, with whatever fault is installed. Returns what each check saw.
    `stage` (Wave 3): what makes a Wave 3 entry point reachable, run after the owner's question (WAVE3_ENTRIES)."""
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
    if stage is not None:
        try:
            stage(r, seen)
        except Exception as exc:                   # noqa: BLE001 - recorded: the staged research failed
            seen["stage_error"] = repr(exc)
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


def check(r, web, monkeypatch, caplog, module, name, mode, stage=None):
    caplog.set_level(logging.WARNING)
    before, errors = _beliefs(r), _errors(r)
    fault = Fault(monkeypatch, module, name, mode)
    seen = session(r, web, stage)
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
    if seen.get("stage_error"):
        problems.append(f"the staged research failed: {seen['stage_error']}")
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


# ---- Wave 3 (reconciliation): the settings Wave 3 added, each made to fail with the setting on ------------------------
#
# The same owner session and checks, on an individual with every Wave 1 setting on and every Wave 3 setting on:
# recheck_order (ADR-060) another order than lane K's; attribution_independence and echo_recognition (ADR-061, on by
# default); supersession "independent" (ADR-061; off by default); predictive_regulation "shadow" (ADR-058; the third
# value of a Wave 1 setting, so the Wave 1 predictive gate's own case above keeps it True); the self-description's
# readiness field (ADR-058, owner scope); the front door of `nsf imagine scm` (ADR-059, a flag of an owner's command,
# on by default). Where the owner's question cannot reach an entry point, a stage makes it reachable through the
# product's own code: a refresh whose sources disagree with a held item, read through consolidation (supersession), and
# the owner's own front-door question (the front door).
WAVE3_WEB_ON = {"recheck_order": "rate+seen+rotation", "attribution_independence": True, "echo_recognition": True,
                "supersession": "independent"}
WAVE3_INSTANCE_ON = {"predictive_regulation": "shadow"}


def _superseding_refresh(r, seen):
    """A held version from two outlets, then a refresh where three independent outlets state a newer one and one
    lagging page the held one: with supersession "independent" consolidation reaches the rule (lane S's staging)."""
    import uat.test_uat_rollback as rollback
    now = time.time()
    templates = {"devdesk.example": "By now Quorin {v} is the latest Quorin release.",
                 "factline.example": "Once again Quorin {v} is the latest Quorin release.",
                 "dailytrack.example": "As of yesterday, Quorin {v} is the latest Quorin release."}
    with r.db() as db:
        rollback._s_look(db, now - 10 * 86_400.0, "Quorin", [
            ("relnotes.example", "Quorin 1.4.0 is the latest Quorin release."),
            ("pkgwatch.example", "As of today, Quorin 1.4.0 is the latest Quorin release.")])
        out = rollback._s_look(db, now - 7 * 86_400.0, "Quorin", [
            (h, t.format(v="1.5.0")) for h, t in templates.items()] + [
            ("weekly-builds.example", "As of now, Quorin 1.4.0 is the latest Quorin release.")])
    seen["shown"].append(json.dumps(out, default=str))


FRONT_DOOR_QUESTION = {"graph": {"X": [], "M": ["X"], "Y": ["M"]}, "hidden_common_cause": ["X", "Y"],
                       "observational_mean": {"X": 0.5, "M": 2.0, "Y": 5.0}, "do": {"X": 1.5}, "ask": "Y",
                       "observational_covariance": {"X": {"X": 2, "M": 4, "Y": 13}, "M": {"X": 4, "M": 9, "Y": 29},
                                                    "Y": {"X": 13, "M": 29, "Y": 95}}, "moments": "population"}


def _front_door_question(r, seen):
    """The owner's own `nsf imagine scm FILE` on a question only the front door answers (tests/test_causal_front_door.py
    `worked`): a failure is returned to the owner, who asked."""
    path = r.path.parent / "front-door-question.json"
    path.write_text(json.dumps(FRONT_DOOR_QUESTION), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = nsf.main(["imagine", "scm", str(path)])
    except SystemExit as exc:
        code = exc.code
    except Exception as exc:                      # noqa: BLE001 - the owner's command failing is the visible outcome
        code, err = 1, io.StringIO(repr(exc))
    seen["shown"].append(out.getvalue())
    if code:
        seen["surfaced"].append(err.getvalue() or f"exit {code}")


# (setting, module, attribute, stage)
WAVE3_ENTRIES = (
    ("recheck_order", "neuroscifi.webcortex.recheck", "_ordered", None),
    ("attribution_independence", "neuroscifi.webcortex.claims", "attribution_keys", None),
    ("echo_recognition", "neuroscifi.webcortex.consolidate", "_without_echoes", None),
    ("supersession", "neuroscifi.webcortex.consolidate", "_rule_replacement", _superseding_refresh),
    ("predictive_regulation_shadow", "neuroscifi.resources.shadow", "_verdict", None),
    ("readiness", "neuroscifi.readiness", "report", None),
    ("front_door", "neuroscifi.causal", "_frontdoor", _front_door_question),
)
WAVE3_IDS = [f"{m}:{n}" for m, _, n, _ in WAVE3_ENTRIES]


@pytest.fixture
def rig3(rig, monkeypatch):
    from neuroscifi.resources import shadow
    monkeypatch.setattr(shadow, "_failures", {})     # logged at the 1st, 10th, 100th failure of a kind per process
    with rig.db() as db:
        now = time.time()
        state.set_config(db, WAVE3_WEB_ON, now)
        store.set_config(db, WAVE3_INSTANCE_ON, now)
    return rig


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("mechanism,module,name,stage", WAVE3_ENTRIES, ids=WAVE3_IDS)
def test_a_failing_wave3_mechanism_leaves_the_owner_in_control(rig3, web, monkeypatch, caplog, mechanism, module, name,
                                                               stage, mode):
    problems, fault, visible = check(rig3, web, monkeypatch, caplog, module, name, mode, stage)
    assert not problems, (problems, fault.calls, visible)


@pytest.mark.parametrize("mechanism,module,name,stage", WAVE3_ENTRIES, ids=WAVE3_IDS)
def test_a_wave3_failure_message_naming_a_subject_reaches_no_log_and_no_stored_record(rig3, web, monkeypatch, caplog,
                                                                                      mechanism, module, name, stage):
    problems, fault, visible = check(rig3, web, monkeypatch, caplog, module, name, "marked", stage)
    assert not problems, (problems, fault.calls, visible)
    logged = [rec.getMessage() for rec in caplog.records if MARKER in rec.getMessage()]
    assert not logged, logged
    stored = [t for t in _stored_text(rig3) if MARKER in t]
    assert not stored, stored
