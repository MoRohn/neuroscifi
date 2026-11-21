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
    assert task["origin"] == f"refresh-owner:{kid}" and task["parent_id"] == tid   # the owner's refresh (A-P2-2)
    assert task["origin_label"] == f"Refresh of knowledge #{kid}"           # C-08: shown as a refresh
    assert not orchestrator.is_autonomous(task["origin"])                    # never clamped as autonomous
    assert task["objective"] == knowledge.row(db, kid)["statement"]
    assert orchestrator.get(db, task["id"])["stages_done"] == []


def test_a_newer_release_observed_later_makes_release_knowledge_stale(db):
    """R6: release-class knowledge goes stale as soon as a higher version of the same key is observed in a later
    task's claims (even a held single-source claim), not only when its TTL runs out."""
    from test_consolidate import task_with
    _, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    assert knowledge.row(db, kid)["freshness_class"] == "release"
    assert knowledge.query(db, "latest NumPy release", T0 + DAY)["items"][0]["stale"] is False
    same = "NumPy 2.3.5 is still the latest release of the NumPy library."
    task_with(db, [cluster([claim("http://c.example/numpy", same)])], now=T0 + DAY)      # same version: no effect
    assert knowledge.query(db, "latest NumPy release", T0 + 2 * DAY)["items"][0]["stale"] is False
    newer = "NumPy 2.4.0 is the latest release of the NumPy library."
    task_with(db, [cluster([claim("http://c.example/numpy-240", newer)])], now=T0 + 2 * DAY)
    item = knowledge.query(db, "latest NumPy release", T0 + 3 * DAY)["items"][0]
    assert item["id"] == kid and item["stale"] is True and "newer release" in item["stale_reason"]
    assert knowledge.staleness(knowledge.row(db, kid), T0 + 3 * DAY)[0] is False     # without db: TTL only


def test_a_new_arxiv_version_of_a_source_makes_knowledge_stale(db):
    """R6: knowledge resting on an arXiv record is stale once a later read shows a higher arXiv version."""
    from neuroscifi.webcortex import state
    from neuroscifi.webcortex.types import Document
    from test_consolidate import task_with
    arxiv = "https://arxiv.org/abs/2611.00001"
    text = "Dendritic spikes double the memory capacity of pyramidal neurons in the model."
    sources = cluster([claim(u, text, key="dendritic spikes memory capacity") for u in
                       (arxiv, "http://lab.example/dendrites")])
    tid, out = consolidated(db, [sources], objective="What do studies of dendritic spikes in neurons show?")
    kid = out["provisional"][0]
    assert knowledge.row(db, kid)["freshness_class"] == "scientific"

    def read(task_id, version, at):
        doc = Document(url=arxiv, final_url=arxiv, canonical_url=arxiv, domain="arxiv.org", title="t", text=text,
                       retrieved_at=at, retrieval_mode="api", kind="api",
                       metadata={"arxiv_id": "2611.00001", "version": version, "provider": "arxiv"})
        with store.write(db):
            state.save_source(db, task_id, doc, at)

    read(tid, "v1", T0)
    assert knowledge.query(db, "dendritic spikes", T0 + DAY)["items"][0]["stale"] is False
    read(task_with(db, [], now=T0 + DAY), "v1", T0 + DAY)            # re-read, same version
    assert knowledge.query(db, "dendritic spikes", T0 + 2 * DAY)["items"][0]["stale"] is False
    read(task_with(db, [], now=T0 + 2 * DAY), "v2", T0 + 2 * DAY)
    item = knowledge.query(db, "dendritic spikes", T0 + 3 * DAY)["items"][0]
    assert item["stale"] is True and "new version" in item["stale_reason"]
    assert state.sources(db, tid)[0]["metadata"]["version"] == "v1"     # the version is kept in web_sources


def test_owner_refresh_is_not_autonomous_but_prediction_refresh_is(db):
    """A-P2-2: with the daily autonomous allowance at zero the owner's refresh still runs; 'refresh:<id>' (a due
    prediction) is autonomous and refused."""
    from neuroscifi.webcortex import state
    state.set_config(db, {"autonomous": False, "daily_autonomous": {"requests": 0}}, T0)
    _, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    task = knowledge.refresh(db, kid, T0 + 5)
    assert task["origin"] == f"refresh-owner:{kid}" and task["budget"]["max_requests"] > 0
    again = knowledge.refresh(db, kid, T0 + 5, origin="console")     # S-14: the same refresh is already queued
    assert again["id"] == task["id"] and again["deduplicated"] is True
    assert not orchestrator.is_autonomous("cli") and orchestrator.is_autonomous(f"refresh:{kid}")
    with pytest.raises(ValidationError, match="daily autonomous cap"):
        knowledge.refresh(db, kid, T0 + 5, origin=f"refresh:{kid}")


def test_query_limit_must_be_an_integer(db):
    """C-P3: a malformed limit is a ValidationError (exit 2 / HTTP 400), never a bare ValueError."""
    with pytest.raises(ValidationError):
        knowledge.query(db, "numpy", T0, limit="ten")
    with pytest.raises(ValidationError):
        knowledge.query(db, "numpy", T0, limit=0)


# ---- UAT RC1 regressions (lane L2) --------------------------------------------------------------------------------

def test_review_after_resolve_revises_the_decision_on_the_question_with_history(db):
    """W-22: knowledge.review and `nsf resolve` share one transition (knowledge.decide). Rejecting an item the owner
    accepted through its question records the new decision on that question's resolution (history kept); re-
    accepting a rejected item is allowed and says so in the item's note."""
    from neuroscifi.instance import executive
    _, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    pid = db.execute("SELECT id FROM pending WHERE scope=?", (f"web:knowledge:{kid}",)).fetchone()[0]
    executive.resolve(db, pid, "looks right", T0 + 1, confirm=True)
    assert knowledge.row(db, kid)["status"] == "accepted"
    done = knowledge.review(db, kid, False, T0 + DAY, note="the release was withdrawn")
    assert done["status"] == "rejected" and done["previous_status"] == "accepted" and done["decisions_revised"] == [pid]
    resolution = db.execute("SELECT resolution FROM pending WHERE id=?", (pid,)).fetchone()[0]
    assert resolution.startswith("looks right\n") and "rejected it after an earlier acceptance" in resolution
    assert "the release was withdrawn" in resolution
    assert knowledge.review_outcome(db, {"id": pid, "scope": f"web:knowledge:{kid}"}) is False   # consistent
    back = knowledge.review(db, kid, True, T0 + 2 * DAY, note="re-released")
    assert back["status"] == "accepted" and back["previous_status"] == "rejected"
    item = knowledge.row(db, kid)
    assert item["status"] == "accepted" and "re-accepted it after an earlier rejection" in item["note"]
    assert db.execute("SELECT resolution FROM pending WHERE id=?", (pid,)).fetchone()[0].count("\n") == 2
    knowledge.archive(db, kid, T0 + 3 * DAY)
    with pytest.raises(Conflict):
        knowledge.review(db, kid, True, T0 + 3 * DAY)          # archived is history, not reviewable
