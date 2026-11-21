"""knowledge.py: lexical query with freshness, provenance and versions; owner review; archive/reject; refresh."""
import pytest

from neuroscifi.instance import store
from neuroscifi.instance.errors import Conflict, NotFound, ValidationError
from neuroscifi.webcortex import knowledge, orchestrator
from test_consolidate import DAY, T0, cluster, claim, consolidated, two_sources, open_db


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def other_topic(hosts=("c.example", "d.example")):
    text = "The Rust compiler 1.91 stabilised async closures for every target."
    return cluster([claim(f"http://{h}/rust", text, key="rust compiler async closures") for h in hosts])


def test_query_ranks_matches_and_reports_freshness_provenance_and_status(db):
    _, out = consolidated(db, [two_sources(), other_topic()])
    numpy_id = next(k for k in out["provisional"] if knowledge.row(db, k)["key"] == "numpy latest version")
    view = knowledge.query(db, "latest NumPy release", T0 + 60)
    assert view["matched"] == 1 and view["known"] == 0
    item = view["items"][0]
    assert item["id"] == numpy_id and item["provisional"] is True and item["contested"] is False
    assert item["stale"] is False and item["stale_reason"].startswith("fresh")
    assert [s["url"] for s in item["sources"]] == ["http://a.example/numpy", "http://b.example/numpy"]
    assert item["sources"][0]["published_at"] == T0 - DAY and item["last_verified_at"] == T0
    assert item["versions"] == [{"id": numpy_id, "version": 1, "statement": item["statement"],
                                 "status": "provisional", "valid_from": T0 - DAY, "valid_until": None,
                                 "superseded_by": None, "confidence": 0.85}]
    later = knowledge.query(db, "latest NumPy release", T0 + 400 * DAY)["items"][0]
    assert later["stale"] is True and "stale: last verified" in later["stale_reason"]
    assert knowledge.query(db, "photosynthesis in algae", T0)["items"] == []
    with pytest.raises(ValidationError):
        knowledge.query(db, "  ", T0)


def test_review_accepts_once_and_closes_the_question(db):
    _, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    teacher = store.counter(db, "teacher_interventions")
    done = knowledge.review(db, kid, True, T0 + 1, note="checked the release notes")
    assert done["status"] == "accepted" and done["previous_status"] == "provisional" and len(done["pending_closed"]) == 1
    assert db.execute("SELECT resolution FROM pending").fetchone() == ("checked the release notes",)
    assert store.counter(db, "teacher_interventions") == teacher + 1
    assert knowledge.query(db, "latest numpy", T0 + 2)["known"] == 1
    assert knowledge.review(db, kid, True, T0 + 3)["status"] == "accepted"
    archived = knowledge.archive(db, kid, T0 + 4)
    assert archived["status"] == "archived" and knowledge.query(db, "latest numpy", T0 + 5)["items"] == []
    with pytest.raises(Conflict):
        knowledge.review(db, kid, True, T0 + 6)
    with pytest.raises(NotFound):
        knowledge.review(db, 999, True, T0)
    with pytest.raises(ValidationError):
        knowledge.review(db, kid, "yes", T0)


def test_reject_and_superseded_items_cannot_be_reviewed(db):
    _, first = consolidated(db, [two_sources(published=T0 - 10 * DAY)])
    old = first["provisional"][0]
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    new = second["superseded"][0]["new"]
    with pytest.raises(Conflict):
        knowledge.review(db, old, True, T0 + 2 * DAY)
    with pytest.raises(Conflict):
        knowledge.refresh(db, old, T0 + 2 * DAY)
    assert knowledge.reject(db, new, T0 + 2 * DAY)["status"] == "rejected"


def test_refresh_submits_a_fact_verification_task_without_running_it(db):
    tid, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    task = knowledge.refresh(db, kid, T0 + 10 * DAY)
    assert task["status"] == "queued" and task["mode"] == "fact_verification"
    assert task["origin"] == f"refresh:{kid}" and task["parent_id"] == tid
    assert task["objective"] == knowledge.row(db, kid)["statement"]
    assert orchestrator.get(db, task["id"])["stages_done"] == []
