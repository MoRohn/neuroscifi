"""The integration loop on one staged individual (Wave 2 lane L, directive §8; ADR-056). Offline (the fixture web on
loopback), deterministic, no answer model.

A stimulus travels the directive's reference flow through the real modules, and every hand-over is asserted on the
stored records:

  internal observation and salience   the knowledge item's "still holds" prediction falls due; the item is stale
  admission (the one rule)            the timer's knowledge re-check admits it through autonomy.admission, with the
                                      salience record in its decision event; curiosity's cooldown then refuses the
                                      subject, but it is a due prediction, never in the timer's curiosity pool, so
                                      no arbitration is recorded (lane Y, H1: only a real conflict is one)
  imagined store                      the timer's scenario records imagine the prediction's three futures (kind
                                      simulation, quarantined) before anything is observed
  the real observation                the re-check runs as a real research task on the fixture web, whose pages now say
                                      something else; monitoring records the answer's support
  the prediction settles              the next pass settles it as an error (contradicted), from the task's report
  the error, and only the error       re-check marks on what rested on the item; curiosity's ranking and salience read
                                      the failed prediction; the scenario record's outcome slot is filled and its
                                      error computed on read
  verified outcome                    the error analysis's link chain (prediction -> observation -> error) and the
                                      contradiction's hypothesis record
  self-description                    "what am I doing / why" shows each step from those records

and the forbidden paths stay closed: curiosity, autonomy and the orchestrator never read the imagined store, and
removing every imagined record changes no ranking, no knowledge and no belief.
"""
import pytest

from neuroscifi import self_description as sd
from neuroscifi.instance import imagined, store
from neuroscifi.webcortex import (autonomy, curiosity, evolution, orchestrator, predictions, premises, salience,
                                  scenarios, scheduler, state)
from test_orchestrator import PAGES, T0, build, html, open_individual, serve

HOUR = 3600.0
QUESTION = "What is the latest version of the Zephyr compiler?"
LOOP = {"autonomous": True, "idle_recheck": True, "premise_recheck": True, "salience_record": True,
        "arbitration_record": True, "answer_monitoring": True, "hypothesis_fields": True}


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


def negating_pages(web):
    """The world now says the release did not happen: a contradiction of the item (opposite polarity), not a newer
    release, so consolidation keeps the item live and records the disagreement."""
    for (host, path), (title, _, paragraphs) in PAGES.items():
        new = [p.replace("was released", "was not released") for p in paragraphs]
        web.add(host, path, html(title, "2026-11-22T08:00:00Z", new))


def decisions(db, task_id):
    return [state.loads(d, {}) for (d,) in db.execute("SELECT detail FROM web_events WHERE task_id=? AND "
                                                      "kind='decision' ORDER BY id", (task_id,))]


class Spy:
    """Every call of the imagined store's API, with the module that made it."""

    def __init__(self, monkeypatch):
        import inspect
        self.calls = []
        for name in ("written_by", "listing", "show", "counts", "derived_ids", "record", "observe", "room"):
            real = getattr(imagined, name)

            def spy(*a, _real=real, _name=name, **k):
                caller = inspect.stack()[1].frame.f_globals.get("__name__")
                self.calls.append((_name, caller))
                return _real(*a, **k)
            monkeypatch.setattr(imagined, name, spy)


def test_a_stimulus_travels_the_loop_and_each_hand_over_is_on_the_records(individual, web, monkeypatch):
    db, path = individual
    serve(web)
    task = orchestrator.submit(db, QUESTION, T0)
    services = build(db, path, web)
    try:
        done = orchestrator.run(db, task["id"], services)
    finally:
        services.close()
    kid = done["result"]["consolidation"]["provisional"][0]
    key = db.execute("SELECT key FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0]
    with store.write(db):           # a plan that rests on the item (what a failed prediction about it should reach)
        hid, _ = evolution.open_hypothesis(db, key=f"{key} rollout plan", statement="The rollout assumes 4.2.0.",
                                           origin="insufficient", verification={"confidence": 0.5}, claims=[],
                                           values=[], polarity=1, now=T0, knowledge_id=kid, with_prediction=False)
        store.bump_state_version(db)
    state.set_config(db, LOOP, T0)
    store.set_config(db, {"imagine_scenarios": True}, T0)
    autonomy.enable(db, T0)
    pred = predictions.listing(db, open_only=True)[0]
    assert pred["knowledge_id"] == kid
    due = pred["check_after"] + 1
    negating_pages(web)
    beliefs = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]

    # 1. the timer's pass: observation and salience -> admission -> a forecast in the imagined store
    first = scheduler.tick(db, due, None, max_tasks=3, queue=True)
    open_predictions = db.execute("SELECT count(*) FROM web_predictions WHERE outcome IS NULL").fetchone()[0]
    assert first["scenarios"] == {"settled": 0, "imagined": open_predictions}
    record = imagined.written_by(db, scenarios.MECHANISM, derived_from=("prediction", pred["id"]))[0]
    assert record["kind"] == "simulation" and record["payload"]["prediction_id"] == pred["id"]
    queued = [q["task_id"] for q in first["queued"]]
    assert [q["origin"] for q in first["queued"]] == ["recheck"]
    recheck_task = queued[0]
    picked = next(d["autonomy"] for d in decisions(db, recheck_task) if isinstance(d.get("autonomy"), dict))
    assert picked["admission"] == {"rule": "unified", "path": "recheck"} and picked["knowledge_id"] == kid
    assert picked["salience"]["signals"]["staleness"] > 0 and picked["salience"]["explanation"].startswith("salience")
    # the subject is a due prediction: the timer's curiosity never launches one (its pool is empty here), so the
    # cooldown refusal is no conflict and nothing names curiosity as a loser (lane Y, H1)
    lost = [d["arbitration"] for d in decisions(db, recheck_task) if "arbitration" in d]
    assert lost == [] and "arbitration" not in first
    assert db.execute("SELECT task_id FROM web_predictions WHERE id=?", (pred["id"],)).fetchone()[0] == recheck_task

    # 2. the real observation arrives (the re-check runs as research on the fixture web); monitoring records support
    spy = Spy(monkeypatch)
    services = build(db, path, web, clock=lambda: due)
    try:
        ran = orchestrator.run(db, recheck_task, services)
    finally:
        services.close()
    assert ran["status"] == "completed"
    assert ran["result"]["consolidation"]["contested"][0]["knowledge_id"] == kid
    support = ran["result"]["monitoring"]
    assert support["kind"] != "none" and isinstance(support["sufficiency"]["enough"], bool)

    # 3. the next pass settles the prediction from that report: an error, and only its error travels on
    second = scheduler.tick(db, due + HOUR, None, max_tasks=3, queue=True)
    settled = predictions.listing(db)
    failed = next(p for p in settled if p["id"] == pred["id"])
    assert failed["outcome"] == "error" and failed["detail"]["reason"].startswith("contradicted")
    marks = {}
    for m in premises.listing(db):
        marks.setdefault((m["subject"]["kind"], m["subject"]["id"]), []).append(m)
    # the re-check's own mark closed with what the evidence did; the failed prediction opened new ones
    assert [(m["reason"], m["status"]) for m in marks[("knowledge", kid)]] == [
        ("prediction_failed", "marked"), ("idle", "done")]
    assert marks[("knowledge", kid)][1]["outcome"] == "contradicted by new evidence"
    assert [m["reason"] for m in marks[("hypothesis", hid)]][-1] in ("premise_contradicted", "prediction_failed")
    rank = {c["target"].get("knowledge_id"): c for c in curiosity.rank(db, due + HOUR)}
    assert "a recent prediction about it failed" in rank[kid]["reason"]
    assert salience.score(db, {"key": key, "knowledge_id": kid}, due + HOUR)["signals"]["prediction_discrepancy"] \
        == 1.0
    edges = {(s, k, d) for s, k, d in db.execute("SELECT src_kind, kind, dst_kind FROM web_claim_links WHERE "
                                                 "src_kind IN ('prediction','observation')")}
    assert {("prediction", "observed", "observation"), ("observation", "error_of", "error")} <= edges
    assert "scenarios" in second
    third = scheduler.tick(db, due + 2 * HOUR, None, max_tasks=3, queue=True)
    assert third["scenarios"]["settled"] >= 1      # the scenario pass runs before the settlement in a pass: one later
    ours = imagined.written_by(db, scenarios.MECHANISM, derived_from=("prediction", pred["id"]), observed=True)
    assert scenarios.observed_future(ours[0]) == "error"
    compared = scenarios.compare(ours[0]["payload"], "error")
    assert 0 < compared["brier_3way"] <= 2 and compared["forecast_brier"] is not None
    assert next(iter(scenarios.errors(db)["mechanisms"].values()))["n"] >= 1

    # the forbidden paths: no imagined content read by curiosity, autonomy or the orchestrator ...
    readers = {caller for _, caller in spy.calls}
    assert not readers & {"neuroscifi.webcortex.curiosity", "neuroscifi.webcortex.autonomy",
                          "neuroscifi.webcortex.orchestrator"}
    assert readers <= {"neuroscifi.webcortex.scenarios", "neuroscifi.instance.imagined", __name__}
    # ... and no belief or ranking moved by an imagined record: everything reads the same without them
    now = due + 3 * HOUR
    before = (curiosity.rank(db, now), autonomy.candidates(db, now)["chosen"],
              db.execute("SELECT id, status, confidence FROM web_knowledge ORDER BY id").fetchall())
    with store.write(db):
        db.execute("DELETE FROM imagined_refs")
        db.execute("DELETE FROM imagined")
    after = (curiosity.rank(db, now), autonomy.candidates(db, now)["chosen"],
             db.execute("SELECT id, status, confidence FROM web_knowledge ORDER BY id").fetchall())
    assert before == after
    assert db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0] == beliefs

    # 4. the self-description shows each step from those records
    rec = sd.build(db, path, now)
    cycle = sd.value(rec, "doing.cycle")
    last = cycle["last_investigation"]
    assert last["task_id"] == recheck_task and last["admitted"]["path"] == "recheck"
    assert last["admitted"]["salience"] == picked["salience"]["score"]
    assert cycle["arbitration"] == []          # no real conflict happened (a real one: test_integration_arbitration)
    assert cycle["predictions"]["errors"] == 1 and cycle["predictions"]["latest"]["prediction_id"] == pred["id"]
    assert sd.value(rec, "uncertain.to_recheck")["open"] >= 2
    assert any("re-check of something that may be out of date" in s and "1 turned out wrong" in s
               for s in sd.short(rec))
