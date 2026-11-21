"""Controlled consolidation: thresholds, duplication, supersession, contradiction, questions, feedback, entities.

Clusters are built directly from types.Claim / types.ClaimCluster (the evaluating stage's output contract), so the
rules of consolidate.run are tested independently of claim extraction. Helpers here are reused by the other
Lane D test modules.
"""
import hashlib
from urllib.parse import urlsplit

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import consolidate, knowledge, orchestrator, state, text as wtext
from neuroscifi.webcortex.types import Claim, ClaimCluster

T0 = 1_795_200_000.0
DAY = 86400.0
KEY = "numpy latest version"
TEXT = "NumPy 2.3.5 is the latest release of the NumPy library."


def open_db(tmp_path):
    """A fresh individual with web access enabled (generator for the `db` fixtures of the Lane D tests)."""
    conn = store.create(tmp_path / "individual.sqlite", T0)
    state.set_config(conn, {"enabled": True, "min_domain_interval": 0.0}, T0)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def claim(url, text=TEXT, *, key=KEY, values=None, polarity=1, published=T0 - DAY, relevance=0.8,
          kind="statement", primary=False, retrieved=T0):
    return Claim(id=hashlib.sha256(f"{url}\n{text}".encode()).hexdigest()[:24], text=text, source_url=url,
                 domain=urlsplit(url).hostname, title="t", retrieved_at=retrieved, key=key, kind=kind,
                 polarity=polarity, values=wtext.values(text) if values is None else list(values), published_at=published, relevance=relevance,
                 flags={"source_type": "primary" if primary else "secondary"})


def cluster(claims, *, status=None, confidence=0.85, contradicting=()):
    independent = len({c.domain for c in claims})
    return ClaimCluster(key=claims[0].key, claims=list(claims), independent_sources=independent,
                        supporting=[c.id for c in claims], contradicting=list(contradicting), confidence=confidence,
                        uncertainty=0.1, status=status or ("corroborated" if independent > 1 else "single_source"),
                        statement=claims[0].text)


def task_with(db, clusters, now=T0, objective="What is the latest numpy release version?"):
    task = orchestrator.submit(db, objective, now, mode="fact_verification", origin="test")
    with store.write(db):
        state.save_clusters(db, task["id"], clusters, now)
    return task["id"]


def consolidated(db, clusters, now=T0, **kw):
    tid = task_with(db, clusters, now, **kw)
    return tid, consolidate.run(db, tid, now)


def two_sources(values=None, published=T0 - DAY, text=TEXT, hosts=("a.example", "b.example"), **kw):
    return cluster([claim(f"http://{h}/numpy", text, values=values, published=published) for h in hosts], **kw)


def test_two_independent_agreeing_sources_become_provisional_with_a_question(db):
    before = db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0]
    tid, out = consolidated(db, [two_sources()])
    assert len(out["provisional"]) == 1 and out["held"] == []
    item = knowledge.row(db, out["provisional"][0])
    assert item["status"] == "provisional" and item["version"] == 1 and item["key"] == KEY
    assert item["claim_values"] == ["v2.3.5"] and len(item["supporting"]) == 2 and len(item["sources"]) == 2
    assert item["valid_from"] == T0 - DAY and item["valid_until"] is None and item["task_id"] == tid
    pending = db.execute("SELECT id, epoch, kind, scope, question, resolved_at FROM pending").fetchall()
    assert pending == [(item["pending_id"], None, "ask", f"web:knowledge:{item['id']}", pending[0][4], None)]
    assert "2 independent sources" in pending[0][4] and "confidence 0.85" in pending[0][4]
    assert f"freshness class {item['freshness_class']}" in pending[0][4]
    feedback = db.execute("SELECT effect, before_confidence, after_confidence, info_gain FROM web_feedback").fetchall()
    assert feedback[0][:3] == ("new", None, 0.85) and feedback[0][3] > 0
    entities = {n for (n,) in db.execute("SELECT normalized FROM web_entities")}
    assert "numpy" in entities
    # The binary-belief memory is untouched.
    assert db.execute("SELECT count(*) FROM belief_revisions").fetchone()[0] == before
    links = state.links(db, tid)
    assert [lk["kind"] for lk in links] == ["supports"]


@pytest.mark.parametrize("make, reason", [
    (lambda: cluster([claim("http://a.example/x")]), "single source"),
    (lambda: two_sources(confidence=0.6), "confidence 0.60 < 0.75"),
    (lambda: cluster([claim("http://a.example/x", relevance=0.0), claim("http://b.example/x", relevance=0.0)]),
     "no claim is relevant"),
    (lambda: two_sources(status="insufficient"), "insufficient evidence"),
])
def test_weak_evidence_is_held_as_claims_only(db, make, reason):
    _, out = consolidated(db, [make()])
    assert out["provisional"] == [] and len(out["held"]) == 1
    assert any(reason in r for r in out["held"][0]["reasons"])
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM web_claims").fetchone()[0] >= 1   # the claims stay


def test_primary_structured_single_source_needs_the_source_threshold(db):
    primary = cluster([claim("http://pypi.example/numpy", primary=True, kind="metadata")], confidence=0.9)
    _, out = consolidated(db, [primary])
    assert out["provisional"] == [] and "1 independent source(s) < 2 required" in out["held"][0]["reasons"]
    state.set_config(db, {"auto_provisional_min_sources": 1}, T0)
    _, out = consolidated(db, [primary], T0 + 1)
    assert len(out["provisional"]) == 1


def test_duplicate_evidence_strengthens_and_never_duplicates(db):
    _, first = consolidated(db, [two_sources()])
    kid = first["provisional"][0]
    later = T0 + 3 * DAY
    _, again = consolidated(db, [two_sources(hosts=("c.example", "d.example"), confidence=0.9)], later)
    assert again["provisional"] == [] and again["strengthened"] == [kid]
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 1
    item = knowledge.row(db, kid)
    assert item["last_verified_at"] == later and item["first_verified_at"] == T0
    assert len(item["supporting"]) == 4 and len(item["sources"]) == 4 and item["confidence"] == 0.9
    _, same = consolidated(db, [two_sources(hosts=("c.example", "d.example"), confidence=0.9)], later + 1)
    assert same["unchanged"] == [kid] and same["strengthened"] == []
    effects = [e for (e,) in db.execute("SELECT effect FROM web_feedback ORDER BY id")]
    assert effects == ["new", "strengthened", "unchanged"]
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 1   # no second question


def test_newer_conflicting_value_supersedes_with_a_new_version(db):
    _, first = consolidated(db, [two_sources(published=T0 - 10 * DAY)])
    old = first["provisional"][0]
    newer = two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.")
    _, out = consolidated(db, [newer], T0 + DAY)
    assert len(out["superseded"]) == 1 and out["superseded"][0]["old"] == old
    new = out["superseded"][0]["new"]
    old_row, new_row = knowledge.row(db, old), knowledge.row(db, new)
    assert old_row["status"] == "superseded" and old_row["superseded_by"] == new
    assert old_row["valid_until"] == new_row["valid_from"] == T0 - DAY
    assert new_row["version"] == 2 and new_row["status"] == "provisional" and new_row["claim_values"] == ["v2.4.0"]
    view = knowledge.query(db, "latest numpy release", T0 + DAY)["items"]
    assert [i["id"] for i in view] == [new]
    assert [v["version"] for v in view[0]["versions"]] == [1, 2]
    assert db.execute("SELECT effect FROM web_feedback ORDER BY id DESC LIMIT 1").fetchone() == ("reversed",)
    assert any(lk["kind"] == "supersedes" for lk in state.links(db, out["task_id"]))


def test_older_conflicting_value_is_recorded_as_a_contradiction(db):
    _, first = consolidated(db, [two_sources()])
    kid = first["provisional"][0]
    older = two_sources(published=T0 - 30 * DAY, text="NumPy 2.2.0 is the latest NumPy release.",
                        hosts=("old1.example", "old2.example"))
    _, out = consolidated(db, [older], T0 + 1)
    assert out["superseded"] == [] and out["provisional"] == []
    assert out["contested"][0]["knowledge_id"] == kid and "2.2.0" in out["contested"][0]["reason"]
    item = knowledge.row(db, kid)
    assert item["status"] == "provisional" and item["contradicting"][0]["url"].startswith("http://old1.example")
    assert item["confidence"] < 0.85
    view = knowledge.query(db, "numpy latest", T0 + 1)["items"][0]
    assert view["contested"] is True
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 1
    assert any(lk["kind"] == "contradicts" for lk in state.links(db, out["task_id"]))
    assert db.execute("SELECT effect FROM web_feedback ORDER BY id DESC LIMIT 1").fetchone() == ("weakened",)


def test_contested_cluster_becomes_a_question_not_knowledge(db):
    a, b = claim("http://a.example/n"), claim("http://b.example/n", "NumPy 2.4.0 is the latest release.")
    contested = cluster([a, b], status="contested", confidence=0.5,
                        contradicting=[{"a": a.id, "b": b.id, "reason": "different versions: 2.3.5 vs 2.4.0"}])
    tid, out = consolidated(db, [contested])
    assert out["provisional"] == [] and len(out["contested"]) == 1
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    scope, question = db.execute("SELECT scope, question FROM pending").fetchone()
    assert scope == f"web:contested:{tid}:0" and "different versions" in question
    tid2, _ = consolidated(db, [contested], T0 + 1)
    effects = [e for (e,) in db.execute("SELECT effect FROM web_feedback ORDER BY id")]
    assert effects == ["new", "unchanged"]


def test_new_knowledge_needs_no_contradiction_even_when_corroborated(db):
    a, b = claim("http://a.example/n"), claim("http://b.example/n")
    corroborated = cluster([a, b], contradicting=[{"a": a.id, "b": b.id, "reason": "explicit opposite"}])
    _, out = consolidated(db, [corroborated])
    assert out["provisional"] == [] and "the sources contradict each other" in out["held"][0]["reasons"]


def test_later_statement_of_a_series_updates_and_an_older_one_is_held(db):
    old_text = "Zephyr 4.2.0 was released on 17 November 2026."
    new_text = "Zephyr 4.3.0 was released on 21 November 2026."
    key = "zephyr releas"
    pair = lambda text, published, hosts: cluster([claim(f"http://{h}/z", text, key=key, published=published)  # noqa: E731
                                                   for h in hosts])
    _, first = consolidated(db, [pair(old_text, T0 - 3 * DAY, ("a.example", "b.example"))])
    _, out = consolidated(db, [pair(new_text, T0, ("c.example", "d.example"))], T0 + 1)
    assert out["superseded"] and out["superseded"][0]["old"] == first["provisional"][0]
    assert "another reference period" in out["superseded"][0]["reason"]
    _, stale = consolidated(db, [pair(old_text, T0 - 3 * DAY, ("e.example", "f.example"))], T0 + 2)
    assert stale["superseded"] == [] and stale["contested"] == []
    assert "older statement of a series" in stale["held"][0]["reasons"][0]
