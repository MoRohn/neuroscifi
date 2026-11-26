"""Wave 2, lane U (directive §12, area 20 "rollback and state restoration"): for every switch Wave 1 added, turning it
off restores the behaviour before it on the NEXT call, in the same process and on the same connection (no restart),
without changing any data; what it wrote while on stays readable, forgettable and restorable.

Each test: the behaviour with the switch off (the reference), on (it must differ: the switch is not vacuous), off
again (it must equal the reference), and the rows written while on counted before and after turning it off. Switches
are changed the way the owner changes them (store.set_config / webcortex state.set_config / voice config.update).
The numeric parameters Wave 1 added (imagine_max_seconds, imagine_max_bytes, recheck_per_tick, recheck_daily,
reflection_interval_days, the broadcast threshold) are bounds, not switches, and are not part of this file."""
from contextlib import closing
import json
import random
import time

import pytest

from neuroscifi import background, broadcast, internal_state
from neuroscifi.acceptance.cognition import infovalue, knowledge as kworld
from neuroscifi.instance import checkpoints, evidence, executive, store
from neuroscifi.instance.errors import InstanceError
from neuroscifi.resources import monitor, policy
from neuroscifi.webcortex import autonomy, curiosity, reflection, scenarios, state, strategies

T0 = 1_800_000_000.0
DAY = 86_400.0
GIB = 1024 ** 3


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    yield db, path
    db.close()


def world(db):
    w = kworld._World.__new__(kworld._World)
    w.db, w.n, w.rng = db, 0, random.Random("uat-rollback")
    return w


def cycle(set_on, set_off, probe):
    """(off, on, off again) of a probe around the owner's switch."""
    set_off()
    reference = probe()
    set_on()
    on = probe()
    set_off()
    again = probe()
    return reference, on, again


def instance_switch(db, name, on, off=False):
    return (lambda: store.set_config(db, {name: on}, time.time()), lambda: store.set_config(db, {name: off}, time.time()))


def web_switch(db, name, on=True, off=False):
    return (lambda: state.set_config(db, {name: on}, T0), lambda: state.set_config(db, {name: off}, T0))


def assert_rolls_back(reference, on, again):
    assert on != reference, "the switch changed nothing here: this check would prove nothing"
    assert again == reference, (reference, again)


def readable_forgettable_restorable(db, path, tmp_path, count, forget):
    """What a switch wrote: still read (count > 0) with the switch off; a checkpoint restored to a new file holds it;
    the owner's forget removes it; the same checkpoint restored again does not bring it back."""
    held = count(db)
    assert held > 0
    made = checkpoints.create(db, path, tmp_path / "checkpoints", time.time())
    copy = tmp_path / "restored.sqlite"
    checkpoints.restore(db, made["id"], copy, time.time())
    with closing(store.connect(copy)) as restored:
        assert count(restored) == held
    forget(db)
    assert count(db) < held
    again = tmp_path / "restored-after-forget.sqlite"
    checkpoints.restore(db, made["id"], again, time.time())
    with closing(store.connect(again)) as restored:
        assert count(restored) == count(db)


# ---- instance switches -------------------------------------------------------------------------------------------

def test_internal_state_history(individual):
    db, path = individual
    on, off = instance_switch(db, "internal_state_history", True)
    lines = []

    def probe():
        recorded = internal_state.record(str(path), now=time.time()) is not None
        lines.append(len(internal_state.history(str(path), now=time.time() + 1)))
        return recorded
    assert_rolls_back(*cycle(on, off, probe))
    assert lines[-1] == lines[-2] and lines[-1] > 0     # the samples written while on are still read, none removed


def _falling_history(path, now):
    from contextlib import contextmanager

    @contextmanager
    def memory(avail):
        original = monitor.memory
        monitor.memory = lambda *a, **k: {"total_bytes": 128 * GIB, "available_bytes": avail * GIB,
                                          "swap_total_bytes": 0, "swap_free_bytes": 0, "psi": None}
        monitor.clear_cache()
        try:
            yield
        finally:
            monitor.memory = original
            monitor.clear_cache()
    for k, avail in enumerate((40, 36, 32, 28, 24, 21)):
        with memory(avail):
            internal_state.record(str(path), now=now - (5 - k) * 30.0)
    return memory


def test_predictive_regulation(individual, monkeypatch):
    db, path = individual
    now = time.time()
    memory = _falling_history(path, now)
    on, off = instance_switch(db, "predictive_regulation", True)

    def probe():
        with memory(21):
            return policy.background_gate(str(path))["why"]
    reference, on_, again = cycle(on, off, probe)
    assert on_ == "forecast" and reference is None
    assert_rolls_back(reference, on_, again)


def _epoch(db):
    rng = random.Random("uat-epoch")
    evidence.start_epoch(db, "e", T0)
    for i in range(24):
        action = rng.randint(0, 1)
        evidence.observe(db, {"id": f"r-{i}", "epoch": "e", "channel": "reference" if i < 12 else "word",
                              "root": f"ref-{i % 3}" if i < 12 else "p", "action": action,
                              "bit": (i % 2) ^ action if i >= 12 else action, "provenance": "simulated",
                              "actor": "simulator"}, T0 + i)


def test_executive_rule(individual):
    db, _ = individual
    _epoch(db)
    on, off = instance_switch(db, "executive_rule", "information-v1", "rules-v1")
    assert_rolls_back(*cycle(on, off, lambda: executive.decide(db, "e", T0 + 100)))


def test_decision_information(individual):
    db, _ = individual
    _epoch(db)
    on, off = instance_switch(db, "decision_information", True)
    reference, on_, again = cycle(on, off, lambda: executive.decide(db, "e", T0 + 100))
    assert "processing" in on_ and "processing" not in reference
    assert {k: v for k, v in on_.items() if k not in ("processing", "information")} == reference  # display only
    assert_rolls_back(reference, on_, again)


@pytest.mark.parametrize("name,call", [
    ("imagine_what_if", lambda db: __import__("neuroscifi.instance.imagine", fromlist=["x"]).what_if(
        db, "e", flip=["r-12"], detect=False)),
    ("imagine_retrospect", lambda db: __import__("neuroscifi.instance.imagine", fromlist=["x"]).retrospect(
        db, "e", "partner:p")),
    ("imagine_forecast", lambda db: __import__("neuroscifi.instance.forecast", fromlist=["x"]).forecast(
        db, "1", {}, 1)),
])
def test_imagination_commands(individual, name, call):
    """On, the command answers (or refuses for a reason of its own: no graph to forecast from); off, it refuses with
    the switch's own reason; on again is not needed: off equals off."""
    db, _ = individual
    _epoch(db)
    on, off = instance_switch(db, name, True)

    def probe():
        try:
            call(db)
            return "answered"
        except InstanceError as error:
            return "off" if name in str(error) else "refused for another reason"
    reference, on_, again = cycle(on, off, probe)
    assert reference == "off" and on_ != "off"
    assert_rolls_back(reference, on_, again)


def test_imagine_scenarios(individual):
    db, _ = individual
    on, off = instance_switch(db, "imagine_scenarios", True)
    assert_rolls_back(*cycle(on, off, lambda: scenarios.tick(db, T0 + 10) is None))


def test_broadcast(individual):
    """Off delivers a finding at once; on holds a low-salience one back; off again delivers. The decisions written
    while on stay in the log."""
    db, _ = individual
    on, off = instance_switch(db, "broadcast", "on", "off")
    n = iter(range(100))

    def probe():
        delivered = []
        with store.write(db):
            out = broadcast.offer(db, "autonomy.finding", now=T0 + 1, signals={"new_verified": 0},
                                  scope=f"uat:{next(n)}", task_id=None, deliver=lambda: delivered.append(1) or 7,
                                  salience=0.0, salience_inputs={})
        return out["outcome"], bool(delivered)
    reference, on_, again = cycle(on, off, probe)
    assert reference[1] is True and on_[1] is False
    assert_rolls_back(reference, on_, again)
    assert broadcast.log(db, limit=10)


# ---- Web Cortex switches -----------------------------------------------------------------------------------------

def _web_world(db):
    spec = {"name": "uat rollback", "budget": 3, "step_hours": 30.0,
            "subjects": ({"id": "static", "kind": "noise"},
                         {"id": "harbour", "kind": "stale", "prior": 0.7, "reliability": 0.95, "age_days": 120.0},
                         {"id": "lantern", "kind": "gap", "prior": 0.6, "reliability": 0.9})}
    w = infovalue._Web(spec, 3, db)
    autonomy.enable(db, w.now)
    return w.now + DAY


def test_salience_record(individual):
    db, _ = individual
    now = _web_world(db)
    on, off = web_switch(db, "salience_record")

    def probe():
        out = autonomy.admission(db, now, path="tick")
        return [sorted(c) for c in out["chosen"] + out["refused"]], [c["key"] for c in out["chosen"]]
    reference, on_, again = cycle(on, off, probe)
    assert on_[1] == reference[1]                          # the record never changes what is admitted
    assert_rolls_back(reference, on_, again)


def test_curiosity_learning_progress(individual):
    db, _ = individual
    now = _web_world(db)
    on, off = web_switch(db, "curiosity_learning_progress")
    assert_rolls_back(*cycle(on, off, lambda: json.dumps(curiosity.rank(db, now, limit=10), sort_keys=True,
                                                         default=str)))


def test_premise_recheck_and_its_records(individual, tmp_path):
    db, path = individual
    w = world(db)
    on, off = web_switch(db, "premise_recheck")
    n = iter(range(100))

    def probe():
        before = db.execute("SELECT count(*) FROM web_premises").fetchone()[0]
        sid = f"subject{next(n)}"
        w._consolidate([w._agree(sid, 1, T0)], T0, sid=sid)
        return db.execute("SELECT count(*) FROM web_premises").fetchone()[0] - before
    reference, on_, again = cycle(on, off, probe)
    assert reference == 0 and on_ > 0
    assert_rolls_back(reference, on_, again)
    kid = db.execute("SELECT dependent_id FROM web_premises WHERE dependent_kind='knowledge' LIMIT 1").fetchone()[0]
    readable_forgettable_restorable(
        db, path, tmp_path, lambda d: d.execute("SELECT count(*) FROM web_premises WHERE dependent_kind='knowledge' "
                                                "AND dependent_id=?", (kid,)).fetchone()[0],
        lambda d: __import__("neuroscifi.instance.governance", fromlist=["x"]).forget_knowledge(d, kid, time.time()))


def test_hypothesis_fields(individual):
    db, _ = individual
    w = world(db)
    on, off = web_switch(db, "hypothesis_fields")
    n = iter(range(100))

    def probe():
        before = db.execute("SELECT count(*) FROM web_hypotheses").fetchone()[0]
        w._noise(f"gauge{next(n)}", T0)
        opened = db.execute("SELECT count(*) FROM web_hypotheses").fetchone()[0] - before
        details = db.execute("SELECT count(*) FROM web_hypothesis_details WHERE hypothesis_id IN (SELECT id FROM "
                             "web_hypotheses ORDER BY id DESC LIMIT ?)", (opened,)).fetchone()[0]
        return opened > 0, details
    reference, on_, again = cycle(on, off, probe)
    assert reference == (True, 0) and on_[1] > 0
    assert_rolls_back(reference, on_, again)
    assert db.execute("SELECT count(*) FROM web_hypothesis_details").fetchone()[0] == on_[1]   # kept, readable


def _old_item(db):
    from neuroscifi.acceptance.cognition import adversarial
    return adversarial._k_item(db, T0 - 30 * DAY)["provisional"][0]


def test_idle_recheck(individual):
    db, _ = individual
    _old_item(db)
    autonomy.enable(db, T0)
    state.set_config(db, {"autonomous": True}, T0)
    on, off = web_switch(db, "idle_recheck")
    hour = iter(range(1000))

    def probe():
        return len(recheck_run(db, T0 + next(hour) * DAY)["submitted"])
    reference, on_, again = cycle(on, off, probe)
    assert reference == 0 and on_ == 1
    assert_rolls_back(reference, on_, again)


def recheck_run(db, now):
    from neuroscifi.webcortex import recheck
    return recheck.run(db, now)


def test_reflection_and_its_records(individual, tmp_path):
    """The reflection written while on names the item verified in its period; forgetting the item drops those lines
    (reflection.purge, called by the forget), and a restore replays the forget."""
    from neuroscifi.acceptance.cognition import adversarial
    from neuroscifi.instance import governance
    db, path = individual
    kid = adversarial._k_item(db, T0 + 5 * DAY)["provisional"][0]
    on, off = web_switch(db, "reflection")
    day = iter(range(100))
    assert_rolls_back(*cycle(on, off, lambda: reflection.scheduled(db, T0 + next(day) * 8 * DAY) is None))

    def naming(d):
        return sum(1 for (rep,) in d.execute("SELECT report FROM web_reflections") for s in reflection.SECTIONS
                   for line in json.loads(rep).get(s) or [] if ["knowledge", str(kid)] in line.get("refs", []))
    readable_forgettable_restorable(db, path, tmp_path, naming,
                                    lambda d: governance.forget_knowledge(d, kid, time.time()))


def test_reflection_model_summary(individual):
    db, _ = individual
    _old_item(db)

    class Gateway:
        def guard(self):
            return "local"

        def chat(self, messages, **kw):
            return type("Reply", (), {"text": "A short phrasing of the report.", "model": "uat"})()
    on, off = web_switch(db, "reflection_model_summary")
    day = iter(range(100))
    assert_rolls_back(*cycle(on, off, lambda: "summary" in reflection.reflect(
        db, T0 + next(day) * DAY, since=T0 - 40 * DAY, gateway=Gateway())))


def test_strategy_selection_off_closes_the_gate(individual):
    """`off` stops ranking on the next call even after a promotion was retained; the ledger stays as it was (it is
    append-only by design: migration 18's triggers refuse a change)."""
    db, _ = individual
    strategies.record(db, capability=strategies.CAPABILITY, level=3, change="uat: a staged promotion",
                      evidence={"run": "uat"}, generalisation={}, regressions={}, cost={}, reproducibility={},
                      decision="retain", decided_by="owner", now=T0)
    rows = len(strategies.ledger(db))
    on, off = web_switch(db, "strategy_selection", "gate", "off")
    assert_rolls_back(*cycle(on, off, lambda: strategies.active(db)))
    assert len(strategies.ledger(db)) == rows


def test_pocket_recent(individual):
    from neuroscifi import self_description
    from neuroscifi.webcortex import orchestrator
    db, path = individual
    prepared = orchestrator.prepare(db, "What is the latest zephyr release?", T0, mode="quick_lookup",
                                    origin="console")
    with store.write(db):
        orchestrator.insert(db, prepared, T0)
    on, off = web_switch(db, "pocket_recent")

    def probe():
        record = self_description.build(db, path, T0 + 1, scope="family", family="f" * 16)
        return json.dumps((record.get("recent") or {}).get("value"), sort_keys=True, default=str)
    assert_rolls_back(*cycle(on, off, probe))


def test_quiet_holds_and_releases_without_a_restart(individual):
    """Wave 0's switch over all of them (ADR-050), as the same check: on holds a background activity on the next
    call, off releases it on the next call."""
    db, _ = individual
    on, off = instance_switch(db, "quiet", True)
    assert_rolls_back(*cycle(on, off, lambda: background.allowed(db, "tick.curiosity")[0]))


def test_hypothesis_abandon(individual):
    """A derived hypothesis whose every prediction expired inconclusive with nothing observed: abandoned by the rule
    (evolution.abandon_if_unsupported) only while the switch is on; a new such hypothesis for each call."""
    from neuroscifi.webcortex import evolution
    db, _ = individual
    w = world(db)
    on, off = web_switch(db, "hypothesis_abandon")
    n = iter(range(100))

    def probe():
        w._noise(f"meter{next(n)}", T0)
        hid = db.execute("SELECT max(id) FROM web_hypotheses WHERE status='open' AND origin<>'owner'").fetchone()[0]
        with store.write(db):          # staged: its predictions expired with nothing observed
            staged = db.execute("UPDATE web_predictions SET outcome='inconclusive', detail='{}' WHERE "
                                "hypothesis_id=?", (hid,)).rowcount
            return staged > 0, evolution.abandon_if_unsupported(db, hid, T0 + DAY)
    reference, on_, again = cycle(on, off, probe)
    assert reference == (True, False) and on_ == (True, True)
    assert_rolls_back(reference, on_, again)
    assert db.execute("SELECT count(*) FROM web_hypotheses WHERE status='abandoned'").fetchone()[0] == 1  # kept


# ---- Wave 2 lane L's switches (ADR-056), added by lane X -------------------------------------------------------

# two knowledge items whose orders disagree: A has the greater information gain (admission's priority order), B the
# greater salience (accepted, held with little confidence); as tests/webcortex/test_integration_arbitration.py
_A = (1, "ostra basin area", "The Ostra basin covers 9,400 square kilometres of lowland.", 0.5)
_B = (2, "varnel river length", "The Varnel River is 412 kilometres long and drains the Ostra basin.", 0.3)


def _two_subjects(db, monkeypatch, *rows):
    with store.write(db):
        for (kid, key, statement, _), status, confidence in ((_A, "provisional", 0.9), (_B, "accepted", 0.2)):
            db.execute("INSERT INTO web_knowledge(id,key,version,statement,status,confidence,uncertainty,"
                       "freshness_class,first_verified_at,last_verified_at,created_at,updated_at) "
                       "VALUES(?,?,1,?,?,?,0.2,'stable',1,1,1,1)", (kid, key, statement, status, confidence))
    ranked = [{"excluded": False, "kind": "stale", "mode": "fact_verification", "reason": "stale", "info_gain": gain,
               "target": {"knowledge_id": kid, "key": key, "statement": statement}}
              for kid, key, statement, gain in (rows or (_A, _B))]
    monkeypatch.setattr(curiosity, "rank", lambda db, now, limit=10: ranked)
    autonomy.enable(db, T0)
    autonomy.set_policy(db, {"idle_interval": 0.0}, T0)
    state.set_config(db, {"autonomous": True}, T0)


def _forget_task(task_id):
    from neuroscifi.instance import governance
    return lambda d: governance.forget_research(d, task_id, time.time())


def test_salience_attention_and_its_records(individual, tmp_path, monkeypatch):
    """Off: admission's own (priority) order; on: the more salient first; off again: the priority order on the next
    call. The idle investigation queued while on keeps its decision event (taken in salience order): readable with
    the switch off, in a restored checkpoint, removed by forgetting the task, not brought back by a restore."""
    from neuroscifi.webcortex import predictions
    db, path = individual
    _two_subjects(db, monkeypatch)
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: [])
    on, off = web_switch(db, "salience_attention")
    assert_rolls_back(*cycle(on, off, lambda: [c["key"] for c in autonomy.admission(db, T0, path="idle",
                                                                                     limit=10)["chosen"]]))
    on()
    done = autonomy.idle(db, T0)
    off()
    assert done["action"] == "queued"

    def count(d):
        return d.execute("SELECT count(*) FROM web_events WHERE task_id=? AND kind='decision' AND "
                         "json_extract(detail,'$.autonomy.admission.order')='salience'",
                         (done["task_id"],)).fetchone()[0]
    readable_forgettable_restorable(db, path, tmp_path, count, _forget_task(done["task_id"]))


def test_arbitration_record_and_its_records(individual, tmp_path, monkeypatch):
    """A due refresh and the timer's curiosity want one subject in one pass. Off: no record; on: a decision event on
    the refresh's task names curiosity as the loser; off again: none, on the next pass, and the same launches each
    time. The record written while on is readable with the switch off, in a restored checkpoint, removed by
    forgetting the winner's task, not brought back by a restore."""
    from neuroscifi.webcortex import orchestrator, predictions, scheduler
    db, path = individual
    _two_subjects(db, monkeypatch, _A)
    prepared = orchestrator.prepare(db, _A[2], T0, mode="fact_verification", origin=f"refresh:{_A[0]}")
    with store.write(db):
        winner = orchestrator.insert(db, prepared, T0)
    refresh = [{"prediction_id": 1, "action": "refresh_submitted", "task_id": winner, "knowledge_id": _A[0]}]
    monkeypatch.setattr(predictions, "check_due", lambda *a, **k: refresh)
    on, off = web_switch(db, "arbitration_record")
    tick = iter(range(1, 100))

    def probe():
        report = scheduler.tick(db, T0 + next(tick), None, queue=True)
        return report["queued"], [a["loser"] for a in report.get("arbitration", [])]
    reference, on_, again = cycle(on, off, probe)
    assert reference[1] == [] and on_[1] == ["curiosity"] and reference[0] == on_[0]   # a record only
    assert_rolls_back(reference, on_, again)

    def count(d):
        return d.execute("SELECT count(*) FROM web_events WHERE task_id=? AND kind='decision' AND "
                         "json_extract(detail,'$.arbitration') IS NOT NULL", (winner,)).fetchone()[0]
    readable_forgettable_restorable(db, path, tmp_path, count, _forget_task(winner))


# ---- the research run's switches (lane C, ADR-054): through the worker, offline ---------------------------------

_SAID = ("The Zephyr compiler version 4.2.0 was released on 17 November 2026 by the Zephyr project team.",
         "Zephyr compiler 4.2.0 came out on 17 November 2026 after a long beta.",
         "On 17 November 2026 the Zephyr project shipped version 4.2.0 of its compiler.",
         "Version 4.2.0 of the Zephyr compiler, released 17 November 2026, adds faster builds.",
         "We tested the Zephyr compiler version 4.2.0, released on 17 November 2026, on three boards.",
         "The latest Zephyr compiler is version 4.2.0, published 17 November 2026.",
         "Zephyr's compiler reached version 4.2.0 on 17 November 2026.",
         "Developers can now download the Zephyr compiler version 4.2.0, out since 17 November 2026.")
MANY = {f"site{i}.example": (f"Zephyr compiler 4.2 report {i}", text) for i, text in enumerate(_SAID)}
ONE = {"lone.example": ("A Zephyr compiler benchmark", "The Zephyr compiler 4.2.0 cut build times by 30 percent in "
                                                       "one benchmark on three boards.")}
VERSION_QUESTIONS = ("What is the latest version of the Zephyr compiler?",
                     "Which Zephyr compiler version is the newest?", "What is the current Zephyr compiler release?")
BUILD_QUESTIONS = ("How much did the Zephyr compiler 4.2.0 cut build times?",
                   "By how much did Zephyr compiler 4.2.0 reduce build times?",
                   "What build time cut did the Zephyr compiler 4.2.0 give?")


def _web(pages):
    from neuroscifi.webcortex.fixtureweb import FixtureWeb
    from neuroscifi.webcortex.providers.corpus import CorpusProvider
    from test_pocket_research import html
    w = FixtureWeb().start()
    for host, (title, text) in pages.items():
        w.add(host, "/zephyr", html(title, "2026-11-18T10:00:00Z", [text, "More about the compiler."]))
    provider = CorpusProvider([{"url": f"http://{host}/zephyr", "title": title, "snippet": text[:160]}
                               for host, (title, text) in pages.items()])
    return w, provider


def _research(path, web, provider, objective, mode="quick_lookup"):
    from neuroscifi.webcortex import orchestrator, scheduler
    with closing(store.connect(path)) as db:
        tid = orchestrator.submit(db, objective, time.time(), mode=mode, origin="console")["id"]
    scheduler.work(path, once=True, max_tasks=1, fixture=web, providers=[provider])
    with closing(store.connect(path)) as db:
        task = orchestrator.get(db, tid)
        events = [json.loads(d) for (d,) in db.execute("SELECT detail FROM web_events WHERE task_id=? AND "
                                                       "kind='decision'", (tid,))]
    return task, events


@pytest.mark.parametrize("name,pages,questions,probe", [
    ("answer_monitoring", MANY, VERSION_QUESTIONS, lambda task, events: "monitoring" in (task["result"] or {})),
    ("verification_escalation", MANY, VERSION_QUESTIONS,
     lambda task, events: "escalating" in (task.get("stages_done") or [])),
    ("adaptive_depth", MANY, VERSION_QUESTIONS, lambda task, events: (
        any("adaptive_depth" in e for e in events), (task["result"] or {}).get("usage", {}).get("pages"))),
    ("selective_answering", ONE, BUILD_QUESTIONS,
     lambda task, events: ((task["result"] or {}).get("monitoring") or {}).get("decision")),
])
def test_research_run_switches(tmp_path, name, pages, questions, probe):
    """A new question for each call (a repeated one could be answered from memory); the switch is read by the run."""
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, time.time())) as db:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, time.time())
    web, provider = _web(pages)
    asked = iter(questions)

    def switch(value):
        with closing(store.connect(path)) as db:
            state.set_config(db, {name: value}, time.time())
    try:
        result = cycle(lambda: switch(True), lambda: switch(False),
                       lambda: probe(*_research(path, web, provider, next(asked),
                                                mode="deep_research" if name == "adaptive_depth" else "quick_lookup")))
    finally:
        web.stop()
    assert_rolls_back(*result)


# ---- voice switches ----------------------------------------------------------------------------------------------

@pytest.fixture
def hub(tmp_path):
    from neuroscifi.voice import state as vstate
    from neuroscifi.voice.hub import VoiceHub
    from voice.conftest import FakeEngines
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, time.time())) as db:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, time.time())
    FakeEngines.instance = None
    vstate.reset()
    h = VoiceHub(path, engines_factory=FakeEngines)
    h.fake = FakeEngines({})
    h.path = path
    yield h
    for s in list(h.sessions.values()):
        h._end(s, "test")
    h.close()
    vstate.reset()


def _voice(hub, name):
    from neuroscifi.voice import config as vconfig

    def switch(value):
        with closing(store.connect(hub.path)) as db:
            vconfig.update(db, {name: value})
    return lambda: switch(True), lambda: switch(False)


def _replies(hub, sid):
    from voice.conftest import OWNER
    return [e for e in hub.events(sid, OWNER, 0, wait=0.1)["events"] if e["type"] == "reply"]


def test_voice_status_questions(hub):
    from voice.conftest import OWNER, wait_for
    sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]

    def probe():
        before = len(_replies(hub, sid))
        hub.text(sid, OWNER, "What are you working on?")
        return wait_for(lambda: _replies(hub, sid)[before:])[0]["reply"]["intent"]
    on, off = _voice(hub, "status_questions")
    reference, on_, again = cycle(on, off, probe)
    assert reference == "research" and on_ == "self_status"
    assert_rolls_back(reference, on_, again)


def test_voice_resume_follow(hub):
    from neuroscifi.voice import transcripts as tx
    from voice.conftest import OWNER, wait_for
    sid = hub.start(OWNER, "console:dev1234", "Console", "text_only")["session"]
    n = iter(range(10))

    def probe():
        with closing(store.connect(hub.path)) as db:
            tx.update_session(db, sid, time.time(), context={"follow_task_id": None})
        before = len(_replies(hub, sid))
        hub.text(sid, OWNER, f"Research the Zephyr compiler release number {next(n)}")
        wait_for(lambda: [e for e in _replies(hub, sid)[before:] if e["reply"]["intent"] == "research"])
        with closing(store.connect(hub.path)) as db:
            return tx.session(db, sid)["context"].get("follow_task_id") is not None
    on, off = _voice(hub, "resume_follow")
    assert_rolls_back(*cycle(on, off, probe))


# Every switch this file rolls back, and the test that does it (tests/uat/test_uat_setting_lists.py checks that every
# Wave 1/2 switch of the config classes is here; Wave 2 lane X)
SWITCHES = {
    "internal_state_history": test_internal_state_history, "predictive_regulation": test_predictive_regulation,
    "executive_rule": test_executive_rule, "decision_information": test_decision_information,
    "imagine_what_if": test_imagination_commands, "imagine_retrospect": test_imagination_commands,
    "imagine_forecast": test_imagination_commands, "imagine_scenarios": test_imagine_scenarios,
    "broadcast": test_broadcast, "salience_record": test_salience_record,
    "curiosity_learning_progress": test_curiosity_learning_progress,
    "premise_recheck": test_premise_recheck_and_its_records, "hypothesis_fields": test_hypothesis_fields,
    "idle_recheck": test_idle_recheck, "reflection": test_reflection_and_its_records,
    "reflection_model_summary": test_reflection_model_summary,
    "strategy_selection": test_strategy_selection_off_closes_the_gate, "pocket_recent": test_pocket_recent,
    "hypothesis_abandon": test_hypothesis_abandon, "answer_monitoring": test_research_run_switches,
    "verification_escalation": test_research_run_switches, "adaptive_depth": test_research_run_switches,
    "selective_answering": test_research_run_switches, "status_questions": test_voice_status_questions,
    "resume_follow": test_voice_resume_follow, "salience_attention": test_salience_attention_and_its_records,
    "arbitration_record": test_arbitration_record_and_its_records,
}
