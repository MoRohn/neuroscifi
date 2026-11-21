"""Curiosity: expected information gain x importance / cost, never entropy; irreducible noise does not dominate."""
import math

import pytest

from neuroscifi.instance import executive, store
from neuroscifi.webcortex import claims, curiosity, knowledge, orchestrator, state
from test_consolidate import DAY, T0, claim, cluster, consolidated, open_db


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def pair(key, text, hosts, published):
    return cluster([claim(f"http://{h}/{key.replace(' ', '-')}", text, key=key, published=published)
                    for h in hosts])


def accepted(db, key, text, hosts, now, published=None):
    _, out = consolidated(db, [pair(key, text, hosts, published or now - DAY)], now,
                          objective=f"What is the latest {key} release?")
    kid = out["provisional"][0]
    knowledge.review(db, kid, True, now)
    return kid


def test_information_gain_is_mutual_information_not_entropy():
    h = lambda p: -(p * math.log2(p) + (1 - p) * math.log2(1 - p))  # noqa: E731
    assert curiosity.info_gain(0.5) == pytest.approx(1 - h(0.8))
    assert curiosity.info_gain(0.5) == pytest.approx(claims.info_gain(1.0, 1.0))
    assert curiosity.info_gain(1.0) == 0.0 and curiosity.info_gain(0.0) == 0.0
    assert curiosity.info_gain(0.5, reliability=0.5) == 0.0          # a pure-noise report teaches nothing
    assert curiosity.entropy(0.5) == 1.0                             # though its outcome is maximally uncertain
    four = curiosity.categorical_gain([1, 1, 1, 1])
    assert four == pytest.approx(2 - (-(0.8 * math.log2(0.8)) - 3 * (0.2 / 3) * math.log2(0.2 / 3)))
    assert curiosity.categorical_gain([1, 0, 0, 0]) == pytest.approx(0.0, abs=1e-12)


def test_stale_contested_beats_stale_corroborated_and_fresh_is_excluded(db):
    corroborated = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.",
                            ("a.example", "b.example"), T0)
    contested = accepted(db, "curl latest version", "curl 8.11.0 is the latest curl release.",
                         ("c.example", "d.example"), T0)
    consolidated(db, [pair("curl latest version", "curl 8.10.0 is the latest curl release.",
                           ("e.example", "f.example"), T0 - 30 * DAY)], T0 + 1,
                 objective="What is the latest curl latest version release?")
    assert knowledge.row(db, contested)["contradicting"]
    now = T0 + 20 * DAY
    fresh = accepted(db, "bzip2 latest version", "bzip2 1.0.8 is the latest bzip2 release.",
                     ("g.example", "h.example"), now)
    ranked = curiosity.rank(db, now)
    chosen = [c for c in ranked if not c["excluded"]]
    excluded = [c for c in ranked if c["excluded"]]
    order = [c["target"]["knowledge_id"] for c in chosen]
    assert order[:2] == [contested, corroborated]
    assert chosen[0]["kind"] == "contested" and len(chosen[0]["hypotheses"]) == 4
    assert chosen[0]["info_gain"] > chosen[1]["info_gain"] > curiosity.MIN_GAIN
    assert chosen[1]["kind"] == "stale" and "stale" in chosen[1]["reason"]
    assert [c["target"]["knowledge_id"] for c in excluded] == [fresh]
    assert "avoid unnecessary searches" in excluded[0]["reason"] and excluded[0]["utility"] == 0.0
    for c in ranked:
        assert set(c) >= {"target", "kind", "info_gain", "cost", "utility", "reason"}


def test_irreducibly_noisy_subject_stops_dominating(db):
    """Balanced contradictory sources whose disagreement persists however often they are read: high expected gain
    on paper, zero realised gain. After two futile investigations ('unchanged' feedback) it is down-weighted."""
    stale = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"),
                     T0)
    a = claim("http://n1.example/coin", "The coin index stands at 41 points today.", key="coin index level")
    b = claim("http://n2.example/coin", "The coin index stands at 57 points today.", key="coin index level")
    noisy = cluster([a, b], status="contested", confidence=0.5,
                    contradicting=[{"a": a.id, "b": b.id, "reason": "different values: 41 vs 57"}])
    now = T0 + 20 * DAY
    consolidated(db, [noisy], T0 + DAY)
    first = [c for c in curiosity.rank(db, now) if not c["excluded"]]
    assert first[0]["kind"] == "hypothesis" and first[0]["target"]["key"] == "coin index level"
    consolidated(db, [noisy], T0 + 2 * DAY)
    consolidated(db, [noisy], T0 + 3 * DAY)
    ranked = [c for c in curiosity.rank(db, now) if not c["excluded"]]
    assert ranked[0]["target"].get("knowledge_id") == stale
    coin = next(c for c in ranked if c["target"].get("key") == "coin index level")
    assert "irreducible" in coin["reason"] and coin["utility"] < ranked[0]["utility"]


def test_propose_creates_a_proposal_the_owner_authorises(db):
    kid = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"),
                   T0)
    candidate = next(c for c in curiosity.rank(db, T0 + 20 * DAY) if c["target"].get("knowledge_id") == kid)
    with store.write(db):   # the helper's own tasks were never run; take them out of the queue
        db.execute("UPDATE web_tasks SET status='completed'")
    proposal = curiosity.propose(db, candidate, T0 + 20 * DAY)
    assert proposal["proposed"] is True and proposal["origin"] == "curiosity" and proposal["status"] == "queued"
    from neuroscifi.webcortex import scheduler
    assert scheduler.claim_next(db, "worker", T0 + 20 * DAY) is None      # the worker skips proposals
    done = executive.resolve(db, proposal["pending_id"], "go ahead", T0 + 20 * DAY + 1, confirm=True)
    assert done["web"] == {"kind": "research", "id": proposal["id"], "status": "queued"}
    assert orchestrator.get(db, proposal["id"])["proposed"] is False
    assert scheduler.claim_next(db, "worker", T0 + 20 * DAY + 2) == (proposal["id"], False)
    assert state.task(db, proposal["id"])["lease_holder"] == "worker"


def test_a_newer_release_makes_fresh_knowledge_a_curiosity_candidate(db):
    """R6: knowledge within its TTL is excluded, until a newer release of its key is observed."""
    from test_consolidate import task_with
    kid = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"),
                   T0)
    assert [c["target"].get("knowledge_id") for c in curiosity.rank(db, T0 + DAY) if c["excluded"]] == [kid]
    task_with(db, [pair("zlib latest version", "zlib 1.3.2 is the latest zlib release.", ("c.example",), T0)],
              now=T0 + DAY)
    chosen = [c for c in curiosity.rank(db, T0 + 2 * DAY) if not c["excluded"] and c["kind"] == "stale"]
    assert [c["target"]["knowledge_id"] for c in chosen] == [kid] and "newer release" in chosen[0]["reason"]
    assert chosen[0]["info_gain"] == pytest.approx(curiosity.info_gain(0.5))


def test_propose_is_one_transaction_and_respects_the_question_budget(db):
    """C-P2-12 + A-P1-3: task and question in one state bump; no proposal once the web question budget is spent."""
    from neuroscifi.instance.errors import Conflict, ValidationError
    kid = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"),
                   T0)
    candidate = next(c for c in curiosity.rank(db, T0 + 20 * DAY) if c["target"].get("knowledge_id") == kid)
    before = store.state_version(db)
    curiosity.propose(db, candidate, T0 + 20 * DAY)
    assert store.state_version(db) == before + 1
    open_now = knowledge.open_web_questions(db)
    state.set_config(db, {"max_open_web_questions": open_now}, T0)
    tasks = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0]
    with pytest.raises(Conflict, match="open web questions"):
        curiosity.propose(db, candidate, T0 + 20 * DAY)
    assert db.execute("SELECT count(*) FROM web_tasks").fetchone()[0] == tasks   # nothing half-written
    with pytest.raises(ValidationError):
        curiosity.rank(db, T0, limit="10")


# ---- UAT RC1 regressions (lane L2) --------------------------------------------------------------------------------

def test_curiosity_proposals_count_against_the_daily_autonomous_cap(db):
    """W-12: a proposal is sized to the allowance left; confirming re-checks (nothing left -> the proposal stays
    open), so does the start (an autonomous task with nothing left is cancelled, never run); with nothing left
    no proposal is made at all."""
    from neuroscifi.instance.errors import Conflict, ValidationError
    from neuroscifi.webcortex import scheduler
    kid = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"),
                   T0)
    with store.write(db):
        db.execute("UPDATE web_tasks SET status='completed'")
    now = T0 + 20 * DAY
    state.set_config(db, {"daily_autonomous": {"tasks": 10, "requests": 25, "bytes": 10_000_000}}, now)
    candidate = next(c for c in curiosity.rank(db, now) if c["target"].get("knowledge_id") == kid)
    proposal = curiosity.propose(db, candidate, now)
    assert proposal["budget"]["max_requests"] == 25                     # sized to what is left, not 40
    spender = orchestrator.submit(db, "zlib compression level defaults", now, mode="fact_verification",
                                  origin="curiosity")                    # commits the whole allowance meanwhile
    assert spender["budget"]["max_requests"] == 25
    with pytest.raises(Conflict, match="allowance is spent"):
        executive.resolve(db, proposal["pending_id"], "go", now + 1, confirm=True)
    assert orchestrator.get(db, proposal["id"])["proposed"] is True    # still open
    with pytest.raises(ValidationError, match="daily autonomous cap reached"):
        curiosity.propose(db, candidate, now + 2)
    with store.write(db):   # the allowance frees up; the proposal is confirmed; then something else spends it all
        state.update_task(db, spender["id"], now, status="completed", usage={"requests": 0, "bytes": 0})
    executive.resolve(db, proposal["pending_id"], "go", now + 3, confirm=True)
    with store.write(db):
        db.execute("UPDATE web_tasks SET budget=json_set(budget, '$.max_requests', 0) WHERE id=?", (proposal["id"],))
        state.update_task(db, spender["id"], now, status="completed", usage={"requests": 25, "bytes": 0})
    assert scheduler.claim_next(db, "worker", now + 4) == (proposal["id"], False)
    out = orchestrator.run(db, proposal["id"], type("S", (), {"holder": "worker", "clock": lambda self: now + 5})())
    assert out["status"] == "cancelled" and "daily autonomous cap" in out["error"]


def test_curiosity_queries_use_the_statements_words_not_key_stems(db):
    """W-20: hypotheses and gap queries are built from the statement (intent.query_text), never from stems such
    as 'releas'."""
    a = claim("http://n1.example/req", "Requests 2.32.3 is the latest PyPI release of requests.",
              key="latest pypi releas request")
    b = claim("http://n2.example/req", "Requests 2.31.0 is the latest PyPI release of requests.",
              key="latest pypi releas request")
    noisy = cluster([a, b], status="contested", confidence=0.5,
                    contradicting=[{"a": a.id, "b": b.id, "reason": "different values"}])
    consolidated(db, [noisy], T0)
    entry = next(c for c in curiosity.rank(db, T0 + DAY) if c["target"].get("key") == "latest pypi releas request")
    queries = [h["query"] for h in entry["hypotheses"]]
    assert all("releas " not in q + " " for q in queries) and all("Requests" in q or "requests" in q for q in queries)
