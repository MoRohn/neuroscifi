"""Knowledge evolution (ADR-038): ADR-035 consolidation eligibility, provenance kinds, typed graph edges, hypotheses
and the prediction lifecycle, calibration, metacognitive metrics, the "what changed" query, procedural learning,
the model-adaptation guard, migration 8 and forgetting. Real SQLite instance files throughout."""
import json
import sqlite3

import pytest

from neuroscifi.instance import checkpoints, governance, schema, store
from neuroscifi.instance.errors import Conflict
from neuroscifi.webcortex import evolution, knowledge, predictions, state
from neuroscifi.webcortex import text as wtext
from test_consolidate import DAY, T0, TEXT, claim, cluster, consolidated, open_db, reworded, two_sources


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def stable_pair(text="The Corran Spire is in Harrowgate.", hosts=("wiki.example", "heritage.example"), **kw):
    """Two undated, independently worded secondary sources for a stable fact (CLAIM_VERIFICATION F17)."""
    return cluster([claim(f"http://{h}/spire", reworded(text, n), key="corran spire harrowgat", published=None,
                          values=wtext.values(text)) for n, h in enumerate(hosts)], **kw)


def undated_latest(text=TEXT, hosts=("a.example", "b.example"), key="numpy latest version"):
    """A current-state ("latest") statement from undated sources: ADR-035 temporally_unresolved."""
    return two_sources(published=None, text=text, hosts=hosts, key=key)


def edges(db, kind=None):
    rows = db.execute("SELECT src_kind, kind, dst_kind FROM web_claim_links").fetchall()
    return [r for r in rows if kind is None or r[1] == kind]


# ---- VERIFY / CONSOLIDATE ------------------------------------------------------------------------------------------

def test_two_undated_sources_of_a_stable_fact_become_provisional_knowledge(db):
    """The ADR-026 bar (0.75 undated factor) held this pair at 0.688; ADR-035 supports it at 0.722."""
    pair = stable_pair(confidence=0.688)
    verdict = evolution.cluster_verdict(pair, now=T0, freshness_class="stable")
    assert verdict["state"] == "supported" and verdict["basis"] == "corroboration"
    assert verdict["category"] == "stable" and verdict["confidence"] == pytest.approx(2.6 / 3.6, abs=1e-6)
    _, out = consolidated(db, [pair], objective="Where is the Corran Spire?")
    item = knowledge.row(db, out["provisional"][0])
    assert item["status"] == "provisional" and item["provenance"] == "verified_conclusion"
    assert item["verification"]["state"] == "supported" and item["verification"]["policy"] == "ADR-035"
    assert item["published_at"] is None and item["event_at"] is None and item["retrieved_at"] == T0
    question = db.execute("SELECT question FROM pending WHERE id=?", (item["pending_id"],)).fetchone()[0]
    assert "verified by corroboration" in question   # still the owner's decision (ADR-026 governance)


def test_the_subjects_official_site_alone_supports_a_narrow_historical_fact(db):
    official = claim("http://fennick-project.example/about", "Fennick was created by Oda Haraldsen.",
                     key="fennick creat oda haraldsen", published=None)
    _, out = consolidated(db, [cluster([official], confidence=0.6)], objective="Who created Fennick?")
    item = knowledge.row(db, out["provisional"][0])
    assert item["verification"]["basis"] == "official_source" and item["verification"]["category"] == "historical"


def test_unverified_clusters_are_held_as_hypotheses_with_a_testable_prediction(db):
    tid, out = consolidated(db, [undated_latest()])
    assert out["provisional"] == [] and db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    held = out["held"][0]
    assert held["verification"] == "temporally_unresolved" and "research opportunity" in held["reasons"][0]
    h = evolution.hypothesis(db, held["hypothesis_id"])
    assert h["origin"] == "temporally_unresolved" and h["status"] == "open" and h["provenance"] == "provisional_claim"
    assert h["claim_values"] == ["v2.3.5"] and h["task_id"] == tid
    pred = db.execute("SELECT expectation, probability, deadline, criteria, knowledge_id FROM web_predictions "
                      "WHERE hypothesis_id=?", (h["id"],)).fetchone()
    assert pred[4] is None and 0 < pred[1] < 1 and T0 < pred[2] <= T0 + evolution.MAX_HORIZON
    assert json.loads(pred[3])["values"] == ["v2.3.5"] and "will be confirmed" in pred[0]
    assert ("hypothesis", "predicts", "prediction") in edges(db) and ("claim", "evidence_for", "hypothesis") in edges(db)
    # The same research opportunity seen again is refreshed, never duplicated.
    _, again = consolidated(db, [undated_latest(hosts=("c.example", "d.example"))], T0 + 1)
    assert again["held"][0]["hypothesis_id"] == h["id"]
    assert db.execute("SELECT count(*) FROM web_hypotheses").fetchone()[0] == 1
    assert len(evolution.hypothesis(db, h["id"])["claims"]) == 4


def test_a_contested_cluster_records_its_contradiction_as_a_hypothesis(db):
    a = claim("http://a.example/n")
    b = claim("http://b.example/n", "NumPy 2.4.0 is the latest release.")
    contested = cluster([a, b], status="contested", confidence=0.5,
                        contradicting=[{"a": a.id, "b": b.id, "reason": "different versions: 2.3.5 vs 2.4.0"}])
    _, out = consolidated(db, [contested])
    entry = out["contested"][0]
    assert entry["state"] == "conflicting" and entry["knowledge_id"] is None
    h = evolution.hypothesis(db, entry["hypothesis_id"])
    assert h["origin"] == "conflicting" and db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    assert db.execute("SELECT src FROM web_claim_links WHERE kind='contradicts' AND dst_kind='hypothesis'"
                      ).fetchone() == (b.id,)


# ---- provenance kinds and user-asserted facts --------------------------------------------------------------------

def test_raw_evidence_provisional_claims_verified_conclusions_and_user_assertions_stay_distinct(db):
    _, out = consolidated(db, [two_sources()])
    consolidated(db, [undated_latest(text="Pandas 3.0.1 is the latest release of pandas.",
                                     hosts=("e.example", "f.example"), key="pandas latest version")], T0 + 1)
    owner = knowledge.assert_fact(db, "My lab uses the Kestrel editor for teaching.", T0 + 2)
    verified = knowledge.describe(db, knowledge.row(db, out["provisional"][0]), T0 + 2)
    asserted = knowledge.describe(db, knowledge.row(db, owner["id"]), T0 + 2)
    assert verified["provenance"] == "verified_conclusion" and verified["verification"]["state"] == "supported"
    assert asserted["provenance"] == "user_asserted" and asserted["status"] == "accepted"
    assert asserted["verification"]["state"] == "not_evaluated" and asserted["verification"]["basis"] == "owner_assertion"
    assert verified["times"]["published_at"] == T0 - DAY and verified["times"]["verified_at"] == T0
    assert [h["provenance"] for h in evolution.hypotheses(db)] == ["provisional_claim"]
    assert evolution.PROVENANCE_BY_TABLE["web_sources"] == "raw_evidence"
    assert set(evolution.PROVENANCE_KINDS) == {"raw_evidence", "provisional_claim", "verified_conclusion",
                                                "user_asserted"}


def test_web_evidence_never_supersedes_a_user_asserted_fact(db):
    owner = knowledge.assert_fact(db, "NumPy 2.3.5 is the latest release of the NumPy library.", T0 - 2 * DAY)
    newer = two_sources(text="NumPy 2.4.0 is the latest release of NumPy.", key=knowledge.row(db, owner["id"])["key"])
    _, out = consolidated(db, [newer])
    assert out["superseded"] == [] and out["provisional"] == []
    assert out["contested"][0]["knowledge_id"] == owner["id"]
    item = knowledge.row(db, owner["id"])
    assert item["status"] == "accepted" and item["provenance"] == "user_asserted" and item["contradicting"]
    assert item["confidence"] == 0.5   # recorded, not weakened: the owner decides


def test_an_owner_assertion_supersedes_web_knowledge_with_a_new_version(db):
    _, out = consolidated(db, [two_sources()])
    kid = out["provisional"][0]
    same = knowledge.assert_fact(db, TEXT, T0 + 1)
    assert same["status"] == "unchanged" and same["id"] == kid
    owner = knowledge.assert_fact(db, "NumPy 2.4.0 is the latest release of the NumPy library.", T0 + 2)
    old, new = knowledge.row(db, kid), knowledge.row(db, owner["id"])
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"] and old["valid_until"] == T0 + 2
    assert new["version"] == 2 and new["provenance"] == "user_asserted" and new["key"] == old["key"]
    assert db.execute("SELECT count(*) FROM pending WHERE scope=? AND resolved_at IS NULL",
                      (f"web:knowledge:{kid}",)).fetchone()[0] == 0
    assert ("knowledge", "supersedes", "knowledge") in edges(db)


# ---- CONNECT ----------------------------------------------------------------------------------------------------

def test_typed_graph_edges_link_claims_sources_knowledge_intervals_and_entities(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    old, new = first["provisional"][0], second["superseded"][0]["new"]
    consolidated(db, [stable_pair("The Rust Foundation and Mozilla Research released Rust 1.91 in Berlin.",
                                  hosts=("rust.example", "news.example"))], T0 + DAY,
                 objective="What did the Rust Foundation release?")
    kinds = set(edges(db))
    for edge in (("claim", "derived_from", "source"), ("claim", "evidence_for", "knowledge"),
                 ("claim", "valid_during", "interval"), ("knowledge", "supersedes", "knowledge"),
                 ("entity", "related_to", "entity")):
        assert edge in kinds, edge
    view = evolution.graph(db, "knowledge", new, depth=2)
    assert any(e["kind"] == "supersedes" and e["dst"] == {"kind": "knowledge", "id": str(old)} for e in view["edges"])
    interval = db.execute("SELECT detail FROM web_claim_links WHERE kind='valid_during' AND dst=?",
                          (str(new),)).fetchone()[0]
    assert json.loads(interval)["published_at"] == T0 - DAY and json.loads(interval)["verified_at"] == T0 + DAY
    # state.links (the evidence view) still lists claim -> claim links only
    assert {lk["kind"] for lk in state.links(db, second["task_id"])} <= set(state.CLAIM_LINK_KINDS)


# ---- PREDICT / REASSESS ------------------------------------------------------------------------------------------

def test_prediction_lifecycle_confirm_error_revision_expiry_and_calibration(db):
    _, first = consolidated(db, [undated_latest()])
    h1 = first["held"][0]["hypothesis_id"]
    p1 = db.execute("SELECT id, probability FROM web_predictions WHERE hypothesis_id=?", (h1,)).fetchone()
    # New, timely, independent evidence verifies the same values: confirmed.
    tid2, second = consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + 1)
    assert second["predictions_settled"] == [{"prediction_id": p1[0], "hypothesis_id": h1, "outcome": "confirmed"}]
    assert evolution.hypothesis(db, h1)["status"] == "supported"
    assert evolution.hypothesis(db, h1)["knowledge_id"] == second["provisional"][0]
    assert ("prediction", "observed", "observation") in edges(db)

    # A second hypothesis about another package; the observation verifies a conflicting value: error + revision.
    other = "Zlib 1.3.1 is the latest release of the zlib library."
    _, third = consolidated(db, [undated_latest(text=other, hosts=("e.example", "f.example"),
                                                key="zlib latest version")], T0 + 2,
                            objective="What is the latest zlib release?")
    h2 = third["held"][0]["hypothesis_id"]
    p2 = db.execute("SELECT id, probability FROM web_predictions WHERE hypothesis_id=?", (h2,)).fetchone()
    _, fourth = consolidated(db, [two_sources(text="Zlib 1.3.2 is the latest release of the zlib library.",
                                              hosts=("g.example", "h.example"), key="zlib latest version")], T0 + 3,
                             objective="What is the latest zlib release?")
    settled = [s for s in fourth["predictions_settled"] if s["prediction_id"] == p2[0]][0]
    assert settled["outcome"] == "error" and settled["revised_hypothesis"]
    pred = evolution.prediction(db, p2[0])
    analysis = pred["detail"]["error_analysis"]
    assert analysis["expected"]["values"] == ["v1.3.1"] and analysis["observed"]["values"] == ["v1.3.2"]
    assert any("subject changed" in alt for alt in analysis["alternatives"])
    revised = evolution.hypothesis(db, settled["revised_hypothesis"])
    assert revised["parent_id"] == h2 and revised["origin"] == "prediction_error"
    assert revised["claim_values"] == ["v1.3.2"] and evolution.hypothesis(db, h2)["status"] == "revised"
    for edge in (("observation", "error_of", "error"), ("error", "revised_to", "hypothesis")):
        assert edge in edges(db), edge

    # Calibration over the two settled forecasts, persisted.
    cal = evolution.calibration(db)
    expected = ((p1[1] - 1) ** 2 + (p2[1] - 0) ** 2) / 2
    assert cal["n"] == 2 and cal["brier"] == pytest.approx(expected, abs=1e-6)
    assert sum(b["n"] for b in cal["bins"]) == 2 and cal["base_rate"] == 0.5
    latest = evolution.calibration_history(db, limit=1)[0]
    assert latest["n"] == 2 and latest["brier"] == cal["brier"]

    # The revision's own prediction expires without decisive evidence: inconclusive, hypothesis stays open.
    with store.write(db):
        expired = evolution.expire(db, T0 + 3 + evolution.MAX_HORIZON + 1)
    assert {e["hypothesis_id"] for e in expired} == {revised["id"]}
    assert evolution.hypothesis(db, revised["id"])["status"] == "open"
    assert evolution.calibration(db)["inconclusive"] == 1


def test_a_failed_knowledge_prediction_links_the_error_to_its_revision(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    pid = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)["id"]
    assert evolution.prediction(db, pid)["probability"] == 0.85
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    settled = predictions.check(db, pid, {"consolidation": second, "task_id": second["task_id"]}, T0 + DAY)
    assert settled["outcome"] == "error"
    revision = settled["detail"]["error_analysis"]["revision"]
    assert revision == {"knowledge_id": second["superseded"][0]["new"]}
    assert db.execute("SELECT count(*) FROM web_claim_links WHERE kind='revised_to' AND dst_kind='knowledge'"
                      ).fetchone()[0] == 1
    assert evolution.calibration(db)["n"] == 1 and evolution.calibration(db)["brier"] == pytest.approx(0.85 ** 2)


# ---- EVOLVE: metrics and "what changed" --------------------------------------------------------------------------

def test_metrics_are_computed_from_the_records(db):
    tid, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    consolidated(db, [undated_latest(text="Zlib 1.3.1 is the latest release of the zlib library.",
                                     hosts=("e.example", "f.example"), key="zlib latest version")], T0 + 1)
    _, second = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                              hosts=("c.example", "d.example"))], T0 + DAY)
    with store.write(db):
        state.update_task(db, tid, T0, status="completed", usage={"requests": 12}, finished_at=T0)
        state.update_task(db, second["task_id"], T0 + DAY, status="completed", usage={"requests": 8},
                          result={"memory": {"answered_from_memory": False}}, finished_at=T0 + DAY)
    m = evolution.metrics(db, T0 + DAY, record=True)
    assert m["verified_knowledge"]["gained"] == 2 and m["contradictions"]["superseded"] == 1
    assert m["gaps"] == {"opened": 1, "resolved": 0, "open": 1}
    assert m["research_efficiency"] == {"requests": 20, "verified_claims": 2, "requests_per_verified_claim": 10.0}
    assert m["retrieval"]["measurable"] and m["retrieval"]["memory_answer_rate"] == 0.0
    assert m["information_gain_bits"] > 0 and m["predictions"]["open"] == 1
    assert "not more understanding" in m["not_metrics"]
    assert db.execute("SELECT kind FROM web_metrics WHERE id=?", (m["snapshot_id"],)).fetchone() == ("evolution",)
    later = evolution.metrics(db, T0 + DAY, since=T0 + DAY / 2)
    assert later["verified_knowledge"]["gained"] == 1 and later["gaps"]["opened"] == 0


def test_what_has_changed_returns_versions_supersessions_evidence_and_dates(db):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    consolidated(db, [two_sources(published=T0 - 4 * DAY, hosts=("c.example", "d.example"))], T0 + 1)
    _, third = consolidated(db, [two_sources(published=T0 - DAY, text="NumPy 2.4.0 is the latest release of NumPy.",
                                             hosts=("e.example", "f.example"))], T0 + DAY)
    out = evolution.changes(db, "What is the latest numpy release?", T0 + DAY)
    subject = out["subjects"][0]
    assert [v["version"] for v in subject["versions"]] == [1, 2] and subject["changed"] is True
    assert subject["current"][0]["statement"].startswith("NumPy 2.4.0")
    assert subject["versions"][0]["verification"]["state"] == "supported"
    kinds = [e["kind"] for e in subject["timeline"]]
    assert kinds.count("version") == 2 and "superseded" in kinds and kinds.count("evidence") == 3
    assert all(e["date"] for e in subject["timeline"])
    assert [e["at"] for e in subject["timeline"]] == sorted(e["at"] for e in subject["timeline"])
    assert out["summary"]["generated_by"] == "template" and "superseded on" in out["summary"]["text"]
    assert evolution.changes(db, "okapi habitat", T0)["subjects"] == []
    # T1-18: versions[].valid_from / valid_until were null while versions[].times held them
    old, new = subject["versions"]
    assert old["valid_from"] == old["times"]["valid_from"] is not None
    assert old["valid_until"] == old["times"]["valid_until"] is not None
    assert new["valid_from"] == new["times"]["valid_from"] is not None and new["valid_until"] is None


class _Gateway:
    def __init__(self, where="loopback", fail=False):
        self.where, self.fail, self.seen = where, fail, None

    def guard(self):
        return self.where

    def chat(self, messages, **kw):
        if self.fail:
            raise RuntimeError("down")
        self.seen = messages
        return type("R", (), {"text": "NumPy moved from 2.3.5 to 2.4.0.", "model": "local-test"})()


def test_model_phrasing_is_labelled_uses_only_the_records_and_degrades(db):
    consolidated(db, [two_sources()])
    gw = _Gateway()
    out = evolution.changes(db, "numpy latest", T0, phrase=True, gateway=gw)
    assert out["summary"]["generated_by"] == "model:local-test" and "not this text" in out["summary"]["label"]
    assert "timeline" in gw.seen[1]["content"]
    remote = evolution.changes(db, "numpy latest", T0, phrase=True, gateway=_Gateway("remote"))
    assert remote["summary"]["generated_by"] == "template" and "remote" in remote["summary"]["label"]
    down = evolution.changes(db, "numpy latest", T0, phrase=True, gateway=_Gateway(fail=True))
    assert down["summary"]["generated_by"] == "template" and "unavailable" in down["summary"]["label"]


# ---- procedural learning and the model-adaptation guard ---------------------------------------------------------

def test_procedural_policy_is_bounded_logged_and_reversible(db):
    from neuroscifi.webcortex.types import Document
    tid, out = consolidated(db, [two_sources()])
    with store.write(db):
        for n in range(6):
            url = "http://a.example/numpy" if n == 0 else f"http://other{n}.example/x"
            state.save_source(db, tid, Document(url=url, final_url=url, canonical_url=url, domain="a.example",
                                                title="t", text="x", retrieved_at=T0, retrieval_mode="live"), T0,
                              provider="searxng")
    first = evolution.update_research_policy(db, T0 + 1)
    w = first["weights"]["searxng"]
    assert w == pytest.approx(0.9) and first["changes"]["searxng"] == {"from": 1.0, "to": 0.9}   # step <= 0.1
    second = evolution.update_research_policy(db, T0 + 2)
    assert second["weights"]["searxng"] == pytest.approx(0.8) and second["previous"]["searxng"] == pytest.approx(0.9)
    reverted = evolution.revert_research_policy(db, T0 + 3)
    assert reverted["reverted"] and reverted["weights"]["searxng"] == pytest.approx(0.9)
    assert evolution.research_policy(db)["weights"]["searxng"] == pytest.approx(0.9)
    assert db.execute("SELECT count(*) FROM web_metrics WHERE kind='procedural'").fetchone()[0] == 3
    assert db.execute("SELECT count(*) FROM web_events WHERE json_extract(detail,'$.procedural_policy') IS NOT NULL"
                      ).fetchone()[0] == 3


def test_model_adaptation_needs_authorisation_isolated_evaluation_and_rollback(db):
    graphs_before = db.execute("SELECT count(*) FROM graph_revisions").fetchone()[0]
    with pytest.raises(Conflict, match="owner_authorization"):
        evolution.model_adaptation_guard(db, {"action": "fine-tune"}, T0)
    full = {"action": "fine-tune", "origin": "owner-cli",
            "owner_authorization": {"by": "owner", "confirmed": True, "at": T0},
            "isolated_evaluation": {"isolated": True, "heldout": "eval-set-1", "metric": "accuracy", "pass_bar": 0.9},
            "rollback_plan": {"checkpoint": "weights-2026-11-21", "procedure": "restore the checkpoint"}}
    with pytest.raises(Conflict, match="autonomous origin"):
        evolution.model_adaptation_guard(db, full | {"origin": "curiosity"}, T0)
    with pytest.raises(Conflict, match="rollback_plan"):
        evolution.model_adaptation_guard(db, {k: v for k, v in full.items() if k != "rollback_plan"}, T0)
    ok = evolution.model_adaptation_guard(db, full, T0)
    assert ok["allowed"] is True and ok["implemented"] is False
    logged = [json.loads(v) for (v,) in db.execute("SELECT value FROM web_metrics WHERE kind='adaptation' ORDER BY id")]
    assert [d["allowed"] for d in logged] == [False, False, False, True]
    assert db.execute("SELECT count(*) FROM graph_revisions").fetchone()[0] == graphs_before


# ---- migration 8 ---------------------------------------------------------------------------------------------------

def _v7_file(tmp_path):
    """A schema-7 individual with web knowledge, a prediction and links written by the v7 schema itself."""
    source = tmp_path / "source.sqlite"
    store.create(source, T0).close()
    path = tmp_path / "v7.sqlite"
    raw = sqlite3.connect(path, isolation_level=None)
    for number, script in enumerate(schema.MIGRATIONS[:7], start=1):
        raw.executescript("BEGIN;" + script + f";PRAGMA user_version={number};COMMIT;")
    raw.execute("ATTACH DATABASE ? AS src", (str(source),))
    for table in ("meta", "counters", "scoring_rules"):
        raw.execute(f"INSERT INTO main.{table} SELECT * FROM src.{table}")
    raw.execute("DETACH DATABASE src")
    sources = json.dumps([{"url": "http://a.example/n", "published_at": T0 - 3 * DAY, "retrieved_at": T0 - DAY},
                          {"url": "http://b.example/n", "published_at": T0 - 2 * DAY, "retrieved_at": T0}])
    raw.execute("INSERT INTO web_knowledge(id,key,version,statement,status,confidence,uncertainty,freshness_class,"
                "first_verified_at,last_verified_at,valid_from,supporting,sources,claim_values,created_at,updated_at) "
                "VALUES(5,?,1,?,'accepted',0.8,0.1,'release',?,?,?,'[\"c1\",\"c2\"]',?,'[\"v2.3.5\"]',?,?)",
                ("numpy latest version", TEXT, T0, T0, T0 - 2 * DAY, sources, T0, T0))
    raw.execute("INSERT INTO web_predictions(id,knowledge_id,expectation,check_after,created_at,outcome,detail) "
                "VALUES(9,5,'still holds at next refresh',?,?,'confirmed','{\"reason\":\"same\"}')", (T0 + DAY, T0))
    raw.execute("INSERT INTO web_claim_links(id,task_id,src,dst,kind,detail,created_at) "
                "VALUES(3,'t1','c2','c1','supports','{}',?)", (T0,))
    raw.close()
    return path


def test_a_schema_7_file_migrates_forward_with_its_data_intact(tmp_path):
    path = _v7_file(tmp_path)
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION >= 8
        item = knowledge.row(db, 5)
        assert (item["statement"], item["status"], item["supporting"], item["claim_values"]) == \
            (TEXT, "accepted", ["c1", "c2"], ["v2.3.5"])
        assert item["provenance"] == "verified_conclusion" and item["verification"] == {"policy": "ADR-026"}
        assert item["published_at"] == T0 - 2 * DAY and item["retrieved_at"] == T0 and item["event_at"] is None
        pred = evolution.prediction(db, 9)
        assert (pred["knowledge_id"], pred["outcome"], pred["detail"], pred["probability"], pred["hypothesis_id"]) \
            == (5, "confirmed", {"reason": "same"}, None, None)
        assert db.execute("SELECT src_kind, src, dst_kind, dst, kind FROM web_claim_links WHERE id=3").fetchone() == \
            ("claim", "c2", "claim", "c1", "supports")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        m = evolution.metrics(db, T0)
        assert m["verified_knowledge"]["pre_adr035_versions"] == 1 and m["verified_knowledge"]["gained"] == 0
        # The migrated individual keeps evolving: a superseding version takes the next version number.
        state.set_config(db, {"enabled": True}, T0)
        _, out = consolidated(db, [two_sources(text="NumPy 2.4.0 is the latest release of NumPy.")], T0 + 1)
        assert out["superseded"][0]["old"] == 5 and knowledge.row(db, out["superseded"][0]["new"])["version"] == 2
    finally:
        db.close()


def test_migration_8_is_atomic_and_replays_after_an_interruption(tmp_path, monkeypatch):
    path = _v7_file(tmp_path)
    broken = list(schema.MIGRATIONS)
    broken[7] = schema.MIGRATIONS[7] + "\nCREATE TABLE web_hypotheses(x);"   # fails after most statements ran
    monkeypatch.setattr(schema, "MIGRATIONS", broken)
    raw = sqlite3.connect(path, isolation_level=None)
    with pytest.raises(sqlite3.OperationalError):
        schema.migrate(raw)
    raw.close()
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 7
    assert "provenance" not in [r[1] for r in raw.execute("PRAGMA table_info(web_knowledge)")]
    assert raw.execute("SELECT count(*) FROM web_predictions").fetchone()[0] == 1
    raw.close()
    monkeypatch.undo()
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert knowledge.row(db, 5)["provenance"] == "verified_conclusion"
        assert schema.migrate(db) == schema.SCHEMA_VERSION   # a second run is a no-op
    finally:
        db.close()


# ---- forgetting wins -----------------------------------------------------------------------------------------------

SECRET = "Quillzorp 9.9.1 is the latest release of the quillzorp toolkit."


def test_forgetting_a_task_removes_its_hypotheses_predictions_edges_and_observations(db):
    tid, out = consolidated(db, [undated_latest(text=SECRET, hosts=("q1.example", "q2.example"),
                                                key="quillzorp latest version")],
                            objective="What is the latest quillzorp release?")
    hid = out["held"][0]["hypothesis_id"]
    _, kept = consolidated(db, [undated_latest()], T0 + 1)
    other_h = kept["held"][0]["hypothesis_id"]
    tid3, third = consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + 2)
    assert third["predictions_settled"][0]["hypothesis_id"] == other_h   # settled by task 3
    governance.forget_research(db, tid, T0 + 3)
    assert db.execute("SELECT count(*) FROM web_hypotheses WHERE id=?", (hid,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM web_predictions WHERE hypothesis_id=?", (hid,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM web_claim_links WHERE (src_kind='hypothesis' AND src=?) OR "
                      "(dst_kind='hypothesis' AND dst=?)", (str(hid), str(hid))).fetchone()[0] == 0
    path = __import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2])
    data = path.read_bytes() + (path.with_name(path.name + "-wal").read_bytes()
                                if path.with_name(path.name + "-wal").exists() else b"")
    assert b"Quillzorp" not in data and b"quillzorp" not in data
    governance.forget_research(db, tid3, T0 + 4)
    pred = db.execute("SELECT outcome, detail, task_id FROM web_predictions WHERE hypothesis_id=?",
                      (other_h,)).fetchone()
    assert pred[0] == "confirmed" and json.loads(pred[1])["observation"] == {"forgotten": True} and pred[2] is None
    assert db.execute("SELECT count(*) FROM web_claim_links WHERE kind='observed'").fetchone()[0] == 0


def test_forgetting_knowledge_removes_its_edges_and_predictions_and_restore_replays_it(db, tmp_path):
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)
    with store.write(db):
        hid, _ = evolution.open_hypothesis(db, key="numpy latest version", statement="an alternative", origin="owner",
                                           verification={}, claims=[], values=["v9"], polarity=1, now=T0,
                                           knowledge_id=kid)
        store.bump_state_version(db)
    path = __import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2])
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    governance.forget_knowledge(db, kid, T0 + 2)
    for copy_path in (None, tmp_path / "restored.sqlite"):
        if copy_path is not None:
            checkpoints.restore(db, cp["id"], copy_path, T0 + 3)
        conn = db if copy_path is None else sqlite3.connect(copy_path)
        try:
            assert conn.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM web_predictions WHERE knowledge_id IS NOT NULL").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM web_claim_links WHERE src_kind IN ('knowledge','interval') OR "
                                "dst_kind IN ('knowledge','interval')").fetchone()[0] == 0
            assert conn.execute("SELECT knowledge_id FROM web_hypotheses WHERE id=?", (hid,)).fetchone() == (None,)
        finally:
            if copy_path is not None:
                conn.close()


# ---- CLI ---------------------------------------------------------------------------------------------------------

def test_nsf_knowledge_commands(db, capsys):
    from neuroscifi.cli import main
    consolidated(db, [two_sources()])
    consolidated(db, [undated_latest(text="Zlib 1.3.1 is the latest release of the zlib library.",
                                     key="zlib latest version")], T0 + 1)
    path = str(__import__("pathlib").Path(db.execute("PRAGMA database_list").fetchone()[2]))

    def run(*argv):
        capsys.readouterr()
        assert main(["knowledge", *argv, "--db", path]) == 0
        return json.loads(capsys.readouterr().out)

    assert run("evolution")["verified_knowledge"]["gained"] == 1
    assert run("changes", "numpy latest")["subjects"][0]["key"] == "numpy latest version"
    assert run("hypotheses", "--status", "open")["hypotheses"][0]["origin"] == "temporally_unresolved"
    made = run("hypothesize", "Kestrel 8.0 will ship a plugin API.", "--probability", "0.3")
    assert made["prediction"]["probability"] == 0.3 and made["hypothesis"]["origin"] == "owner"
    assert run("calibration")["current"]["n"] == 0
    assert run("graph", "knowledge:1")["edges"]
    assert run("assert", "Our lab uses Kestrel 7.1.")["provenance"] == "user_asserted"
    capsys.readouterr()
    assert main(["knowledge", "adapt", "fine-tune", "--db", path]) == 2
    assert "refused" in capsys.readouterr().err
