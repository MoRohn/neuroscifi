"""Autonomy ranking regressions from UAT T1 (RC1): drift into autonomous by-products (T1-02), the owner's hypothesis
crowded out and dropped from the ranking (T1-03), hypotheses opened from record titles and context-bound sentences
(T1-17), and two workers running two investigations at once (S6, concurrency 2). Real SQLite throughout."""
from types import SimpleNamespace

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, claims, consolidate, curiosity, evolution, knowledge, orchestrator, \
    scheduler, state
from neuroscifi.webcortex import text as wtext
from test_autonomy import STATEMENT, hypothesize
from test_consolidate import consolidated, open_db
from test_evolution import undated_latest
from test_orchestrator import T0, open_individual


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


def opportunity(db, statement, task_id, *, origin="insufficient", now=T0):
    """A research opportunity consolidation recorded for a task (a hypothesis with a p=0.5 prediction)."""
    with store.write(db):
        hid, _ = evolution.open_hypothesis(
            db, key=claims.claim_key(statement), statement=statement, origin=origin,
            verification={"policy": "ADR-035", "state": origin, "confidence": 0.5}, claims=[],
            values=wtext.values(statement), polarity=1, now=now, task_id=task_id)
        store.bump_state_version(db)
    return hid


def investigating(db, objective, key, now=T0):
    """An autonomous task queued to investigate `key` (autonomy.idle's decision event)."""
    task = orchestrator.submit(db, objective, now, mode="fact_verification", origin="curiosity")
    with store.write(db):
        state.log_event(db, task["id"], "decision", {"autonomy": {"kind": "hypothesis_prediction", "key": key}}, now)
    return task["id"]


def by_statement(ranked, text):
    return next((c for c in ranked["chosen"] + ranked["refused"] if c["statement"] == text), None)


# ---- T1-02: no drift into autonomous by-products ------------------------------------------------------------------

ROOT = "Predictive coding in the primary auditory cortex explains the mismatch negativity"
ON_TOPIC = "Predictive coding models of the auditory cortex reproduce the mismatch negativity in humans"
OFF_TOPIC = "Auditory scene analysis models reproduce the streaming of alternating tone sequences"
DRIFT = "Structural equation modelling estimates all statistical tests simultaneously in one model"


def test_autonomy_never_pursues_its_by_products_beyond_one_relevant_step(individual):
    """UAT T1-02: user research (predictive coding) -> autonomous task -> auditory streaming -> autonomous task ->
    econometric tests / SEM. A token overlap with an owner goal (0.03 in the UAT) let the chain continue."""
    db, _ = individual
    scheduler.monitor_add(db, "statistical tests in neuroscience", T0, interval=6 * 3600, authorized_by="owner")
    user = orchestrator.submit(db, "Recent research on predictive coding in the cortex", T0, mode="deep_research",
                               origin="cli")
    root = opportunity(db, ROOT, user["id"])
    first = investigating(db, "predictive coding primary auditory cortex mismatch negativity",
                          evolution.hypothesis(db, root)["key"])
    on_topic, off_topic = opportunity(db, ON_TOPIC, first), opportunity(db, OFF_TOPIC, first)
    second = investigating(db, OFF_TOPIC, evolution.hypothesis(db, off_topic)["key"], now=T0 + 60)
    drift = opportunity(db, DRIFT, second, now=T0 + 120)

    ranked = autonomy.candidates(db, T0 + 180, limit=50)
    assert by_statement(ranked, DRIFT)["signals"]["goal"] > 0, "the RC1 rule would have pursued it"
    assert by_statement(ranked, ROOT)["depth"] == 0 and "investigated autonomously" in by_statement(ranked, ROOT)["reason"]
    near = by_statement(ranked, ON_TOPIC)
    assert near in ranked["chosen"] and near["depth"] == 1 and "derived 1 step" in near["reason"]
    far = by_statement(ranked, OFF_TOPIC)
    assert far in ranked["refused"] and far["depth"] == 1
    assert "unrelated to the owner subject it came from" in far["reason"] and "own by-products" in far["reason"]
    deep = by_statement(ranked, DRIFT)
    assert deep in ranked["refused"] and deep["depth"] == 2 and "2 steps away" in deep["reason"]
    assert {evolution.hypothesis(db, h)["task_id"] for h in (on_topic, off_topic, drift)} == {first, second}


def test_a_cycle_of_autonomous_tasks_never_reaches_the_owner(individual):
    db, _ = individual
    a = investigating(db, "Zephyr compiler backend", "compil zephyr backend")
    hid = opportunity(db, "The Zephyr compiler backend emits position independent code", a)
    with store.write(db):   # the task's own output is what it was queued for: no owner record anywhere
        db.execute("UPDATE web_events SET detail=json_set(detail,'$.autonomy.key',?) WHERE task_id=?",
                   (evolution.hypothesis(db, hid)["key"], a))
    item = by_statement(autonomy.candidates(db, T0 + 60), "The Zephyr compiler backend emits position independent code")
    assert item["depth"] is None and "in a cycle" in item["reason"]


# ---- T1-03: the owner's records are always ranked, first ---------------------------------------------------------

def test_the_owners_hypothesis_is_never_crowded_out_by_newer_auto_hypotheses(individual):
    """UAT T1-03: hypothesis 230 ranked 15th, then vanished from `candidates --limit 500` once more than 50 newer
    predictions existed (ORDER BY p.id DESC LIMIT 50)."""
    db, _ = individual
    hid, pid = hypothesize(db, "scipy 1.18.1 was released", 0.5)
    user = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="quick_lookup", origin="cli")
    for n in range(autonomy.MAX_CANDIDATES + 10):    # contested auto hypotheses: higher signals than the owner's
        opportunity(db, f"The Zephyr compiler supports the Targ{chr(97 + n // 26)}{chr(97 + n % 26)}x backend",
                    user["id"], origin="conflicting", now=T0 + n)
    ranked = autonomy.candidates(db, T0 + 600, limit=500)
    top = ranked["chosen"][0]
    assert top["hypothesis_id"] == hid and top["prediction_id"] == pid and top["owner"] is True
    assert "the owner's own record" in top["reason"]
    auto = ranked["chosen"][1]
    assert auto["info_gain"] == top["info_gain"] and auto["priority"] > top["priority"], \
        "an auto hypothesis with the same gain and stronger signals still ranks after the owner's"
    assert ranked["total"]["chosen"] == len(ranked["chosen"]) > autonomy.MAX_CANDIDATES
    # The idle investigator picks it first.
    autonomy.enable(db, T0)
    out = autonomy.idle(db, T0 + 600)
    assert out["action"] == "queued" and out["candidate"]["hypothesis_id"] == hid and out["candidate"]["owner"]
    # Cooling down, it is still listed (first among the refused, with the reason), even with a limit of 1.
    later = autonomy.candidates(db, T0 + 700, limit=1)
    assert later["refused"][0]["hypothesis_id"] == hid and "investigated autonomously" in later["refused"][0]["reason"]
    assert later["chosen"][0]["owner"] is False and later["total"]["refused"] >= 1


def test_an_owner_hypothesis_without_an_open_prediction_and_an_owner_assertion_are_ranked(individual):
    db, _ = individual
    hid, pid = hypothesize(db, STATEMENT, 0.5)
    with store.write(db):
        db.execute("UPDATE web_predictions SET outcome='inconclusive', checked_at=? WHERE id=?", (T0 + 1, pid))
    asserted = knowledge.assert_fact(db, "The Orchid database supports 12 replicas in version 3.1", T0)
    ranked = autonomy.candidates(db, T0 + 60)
    owner = [c for c in ranked["chosen"] if c["owner"]]
    assert {c["kind"] for c in owner} == {"hypothesis_prediction", "owner_assertion"}
    h = next(c for c in owner if c["kind"] == "hypothesis_prediction")
    assert h["hypothesis_id"] == hid and h["prediction_id"] is None and h["info_gain"] > ranked["min_gain"]
    assert next(c for c in owner if c["kind"] == "owner_assertion")["knowledge_id"] == asserted["id"]


def test_an_owner_hypothesis_below_the_minimum_gain_is_listed_with_its_reason(individual):
    db, _ = individual
    user = orchestrator.submit(db, "Zephyr compiler targets", T0, mode="quick_lookup", origin="cli")
    for n in range(3):
        opportunity(db, f"The Zephyr compiler supports the Targ{chr(97 + n)}x backend", user["id"])
    hid, _ = hypothesize(db, "The Quartz editor licence changed to MIT in 2025", 0.995)
    ranked = autonomy.candidates(db, T0, limit=1)
    assert ranked["refused"][0]["hypothesis_id"] == hid and "minimum" in ranked["refused"][0]["reason"]


# ---- T1-17: hypotheses only from claim-shaped statements, deduplicated, capped per task ---------------------------

@pytest.mark.parametrize("text", [
    'Crossref record: "Reply: Yes, the human brain has around 86 billion neurons", dated 2025-02-06.',
    "It has been employed to explain important auditory perceptual phenomena.",
    "We present detailed comparisons between the model simulations and data from an auditory experiment.",
    "As a test case, we demonstrate that such a model can explain several phenomena.",
    "These procedures are shown to be closely related to the non-nested hypothesis tests.",
    "A total of 37 pull requests were merged for this release.",
    "Is numpy 2.5.5 stable?",
    "If you use an AI chatbot, your conversations may be used for training.",
])
def test_context_bound_and_record_statements_are_not_claims(text):
    assert curiosity.claim_shaped(text) is not None


@pytest.mark.parametrize("text", [
    "NumPy 2.3.5 is the latest release of the NumPy library.",
    "The human cerebral cortex contains approximately 16 billion neurons.",
    "The US Federal Reserve raised its policy rate in March 2026.",
])
def test_self_contained_statements_are_claims(text):
    assert curiosity.claim_shaped(text) is None


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def test_consolidation_opens_hypotheses_only_from_claims_deduplicated_and_capped(db):
    """UAT T1-17: 384 open hypotheses after ~35 tasks, many 'Crossref record: «title», dated D'."""
    record = undated_latest(text='Crossref record: "Advances in the latest taxonomy of innate immune cells", dated '
                                 '2026-11-19.', key="advanc immun innat taxonomi")
    context = undated_latest(text="These procedures are the latest non-nested hypothesis tests.",
                             key="hypothesi nest procedur test")
    _, out = consolidated(db, [record, context])
    assert [h.get("hypothesis_id") for h in out["held"]] == [None, None]
    assert db.execute("SELECT count(*) FROM web_hypotheses").fetchone()[0] == 0

    many = [undated_latest(text=f"Corvane {n}.4.1 is the latest release of the Corvane {w} library.",
                           key=f"corvan {w} librari", hosts=(f"a{n}.example", f"b{n}.example"))
            for n, w in enumerate(("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"))]
    tid, out = consolidated(db, many, T0 + 1)
    opened = db.execute("SELECT count(*) FROM web_hypotheses WHERE task_id=?", (tid,)).fetchone()[0]
    assert opened == consolidate.MAX_NEW_HYPOTHESES_PER_TASK < len(many)
    # Seen again by another task: the open ones are refreshed (not counted against the cap); and the same statement
    # under another key is the same hypothesis.
    tid2, again = consolidated(db, many[:2] + [undated_latest(text=many[0].statement, key="corvan alpha other",
                                                              hosts=("c.example", "d.example"))], T0 + 2)
    ids = {h["key"]: h["hypothesis_id"] for h in again["held"]}
    assert ids["corvan alpha other"] == ids["corvan alpha librari"] and None not in ids.values()
    assert db.execute("SELECT count(*) FROM web_hypotheses WHERE task_id=?", (tid2,)).fetchone()[0] == 0


# ---- S6: two workers, two concurrent investigations --------------------------------------------------------------

def test_two_workers_start_two_autonomous_investigations_at_once(individual):
    """UAT S6 left 'two concurrent autonomous tasks' unexercised; with one instance-wide idle_interval the second
    worker only waited (RC1), so the cap of 2 was never reached by idle investigations."""
    db, path = individual
    hypothesize(db, STATEMENT, 0.5)
    hypothesize(db, "The Orchid database supports 12 replicas in its 3.1 version", 0.5)
    hypothesize(db, "The Quartz editor licence changed to MIT in 2025", 0.5)
    autonomy.enable(db, T0)
    import threading
    started, barrier = [], threading.Barrier(2)

    def worker(n):
        conn = store.open_existing(path)
        try:
            barrier.wait()
            holder = f"worker-{n}"
            if n:
                done.wait(10)
            out = autonomy.idle(conn, T0 + 10)
            claimed = scheduler.claim_next(conn, holder, T0 + 10)
            task = orchestrator._claim(conn, claimed[0], SimpleNamespace(holder=holder, clock=lambda: T0 + 10),
                                       resume=False) if claimed else None
            started.append((out["action"], task["id"] if task else None))
            done.set()
        finally:
            conn.close()

    done = threading.Event()
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert [a for a, _ in started] == ["queued", "queued"] and all(tid for _, tid in started)
    assert len({tid for _, tid in started}) == 2 and autonomy.running(db, T0 + 10) == 2
    keys = {e["detail"]["autonomy"]["key"] for _, tid in started for e in state.events(db, tid)
            if e["kind"] == "decision" and isinstance(e["detail"].get("autonomy"), dict)}
    assert len(keys) == 2, "the two workers investigate two different subjects"
    # A third worker finds the cap reached; each worker still decides at most once per idle_interval.
    assert autonomy.idle(db, T0 + 11, worker="worker-3")["action"] == "busy"
    assert autonomy.idle(db, T0 + 11)["action"] in ("busy", "waiting")
    assert autonomy.idle(db, T0 + 12, worker="worker-3")["action"] == "busy"
