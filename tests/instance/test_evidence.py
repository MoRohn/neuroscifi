"""Evidence journal: validation, idempotency, expiry, counters, revisions, restart durability and the CLI."""
import json

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import beliefs, evidence, executive, store
from neuroscifi.instance.errors import Conflict, Gone, NotFound, ValidationError, WritesPaused

from conftest import T0

BASE = {"id": "ev-1", "epoch": "desk", "channel": "camera", "action": 1, "bit": 0, "root": "cam-a"}


@pytest.fixture
def desk(instance):
    db, path = instance
    evidence.start_epoch(db, "desk", T0)
    return db, path


def revisions(db):
    return db.execute("SELECT id,epoch,state_version,created_at,cause,summary FROM belief_revisions "
                      "ORDER BY id").fetchall()


def test_start_epoch_is_idempotent_and_validated(instance):
    db, _ = instance
    version = store.state_version(db)
    assert evidence.start_epoch(db, "desk", T0, note="kitchen table") == {"epoch": "desk", "created": True}
    assert evidence.start_epoch(db, "desk", T0 + 5) == {"epoch": "desk", "created": False}
    assert store.state_version(db) == version + 1
    for bad in ("", "  ", "x" * 65, 7):
        with pytest.raises(ValidationError):
            evidence.start_epoch(db, bad, T0)


def test_observe_creates_with_expiry_counters_and_revision(desk):
    db, _ = desk
    version = store.state_version(db)
    out = evidence.observe(db, dict(BASE, acquired_at=T0 - 3), T0 + 10)
    assert out["status"] == "created" and out["state_version"] == version + 1
    assert out["belief_revision"] is not None
    row = db.execute("SELECT recorded_at, expires_at, retention, actor, consent, provenance, acquired_at "
                     "FROM evidence WHERE id='ev-1'").fetchone()
    ttl = store.config(db).evidence_ttl_seconds
    assert row == (T0 + 10, T0 + 10 + ttl, "standard", "owner", "learn", "observed", T0 - 3)
    assert store.counter(db, "evidence_written") == 1
    assert store.counter(db, "encoder_exposure:camera") == 1
    cause, summary = db.execute("SELECT cause, summary FROM belief_revisions WHERE id=?",
                                (out["belief_revision"],)).fetchone()
    assert cause == "evidence:ev-1"
    assert json.loads(summary) == beliefs.compute(db, "desk")


def test_duplicate_is_idempotent_and_never_extends_expiry(desk):
    db, _ = desk
    first = evidence.observe(db, BASE, T0)
    expiry = db.execute("SELECT expires_at FROM evidence").fetchone()[0]
    version = store.state_version(db)
    again = evidence.observe(db, dict(BASE), T0 + 3600)
    assert again == {"status": "duplicate", "seq": first["seq"], "state_version": version, "belief_revision": None}
    assert db.execute("SELECT count(*), max(expires_at) FROM evidence").fetchone() == (1, expiry)
    assert store.counter(db, "repeat_views") == 1 and store.counter(db, "evidence_written") == 1


def test_same_id_with_different_payload_is_conflict(desk):
    db, _ = desk
    evidence.observe(db, BASE, T0)
    for change in ({"bit": 1}, {"root": "cam-b"}, {"consent": "memory_only"}, {"sample_id": "f1"}):
        with pytest.raises(Conflict):
            evidence.observe(db, dict(BASE, **change), T0)
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_tombstoned_id_is_gone(desk):
    db, _ = desk
    db.execute("INSERT INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES('ev-1',1,'desk',?,'forgotten')", (T0,))
    with pytest.raises(Gone):
        evidence.observe(db, BASE, T0)
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"colour": "red"}, {"seq": 4}, {"action": True}, {"action": 2}, {"bit": "1"}, {"bit": 1.0},
    {"channel": "smell"}, {"id": ""}, {"id": "x" * 129}, {"root": " "}, {"root": "r" * 129},
    {"sample_id": ""}, {"sample_id": 5}, {"actor": None}, {"consent": "maybe"}, {"provenance": "dreamed"},
    {"acquired_at": float("nan")}, {"acquired_at": "noon"}, {"acquired_at": True}, {"epoch": None},
])
def test_invalid_observations_are_rejected(desk, change):
    db, _ = desk
    with pytest.raises(ValidationError):
        evidence.observe(db, dict(BASE, **change), T0)
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_missing_fields_and_non_objects_are_rejected(desk):
    db, _ = desk
    with pytest.raises(ValidationError, match="missing"):
        evidence.observe(db, {k: v for k, v in BASE.items() if k != "root"}, T0)
    with pytest.raises(ValidationError):
        evidence.observe(db, [BASE], T0)


def test_unknown_epoch_is_not_found(desk):
    db, _ = desk
    with pytest.raises(NotFound):
        evidence.observe(db, dict(BASE, epoch="garage"), T0)
    with pytest.raises(NotFound):
        evidence.list_evidence(db, "garage")


def test_regulation_pauses_new_writes_but_not_duplicates(tmp_path):
    db = store.create(tmp_path / "small.sqlite", T0, config=store.InstanceConfig(max_evidence_rows=2))
    try:
        evidence.start_epoch(db, "desk", T0)
        evidence.observe(db, BASE, T0)
        evidence.observe(db, dict(BASE, id="ev-2"), T0)
        with pytest.raises(WritesPaused):
            evidence.observe(db, dict(BASE, id="ev-3"), T0)
        assert evidence.observe(db, BASE, T0)["status"] == "duplicate"
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 2
    finally:
        db.close()


def test_revision_only_when_map_or_identifiability_changes(desk):
    db, _ = desk
    j = 0

    def add(channel, root, corrected, **extra):
        nonlocal j
        j += 1
        return evidence.observe(db, {"id": f"e{j}", "epoch": "desk", "channel": channel, "action": 0,
                                     "bit": corrected, "root": root, **extra}, T0 + j)["belief_revision"]

    assert add("camera", "cam-a", 1) is not None       # a new sensor appears
    assert add("camera", "cam-a", 1) is None           # same MAP (still a tie under joint flip symmetry)
    assert add("camera", "cam-a", 0, consent="memory_only") is None
    assert add("reference", "ref-1", 0) is not None     # mechanism MAP moves off the tie
    assert add("reference", "ref-1", 0) is None         # same root again: same MAP, still not identifiable
    assert add("reference", "ref-2", 0) is not None     # a distinct root: identifiability flips
    assert add("reference", "ref-3", 0) is None
    causes = [r[4] for r in revisions(db)]
    assert causes == ["evidence:e1", "evidence:e4", "evidence:e6"]


def test_list_evidence_filters_and_limits(desk):
    db, _ = desk
    for i, (channel, root) in enumerate([("camera", "a"), ("word", "p"), ("camera", "b"), ("camera", "a")]):
        evidence.observe(db, dict(BASE, id=f"e{i}", channel=channel, root=root), T0)
    assert [e["id"] for e in evidence.list_evidence(db, "desk")] == ["e0", "e1", "e2", "e3"]
    assert [e["id"] for e in evidence.list_evidence(db, "desk", channel="camera", root="a")] == ["e0", "e3"]
    assert [e["id"] for e in evidence.list_evidence(db, "desk", limit=2)] == ["e2", "e3"]
    assert set(evidence.list_evidence(db, "desk")[0]) == set(beliefs.EVENT_COLUMNS)
    for bad in ({"channel": "smell"}, {"limit": 0}, {"limit": True}):
        with pytest.raises(ValidationError):
            evidence.list_evidence(db, "desk", **bad)


def snapshot(db):
    return {"compute": beliefs.compute(db, "desk"), "explain": beliefs.explain(db, "desk"),
            "pending": executive.pending(db, include_resolved=True), "revisions": revisions(db),
            "decide": executive.decide(db, "desk", T0 + 999)}


def test_restart_reproduces_beliefs_questions_and_revisions(desk):
    """INS-02: closing and reopening the file reproduces every belief, question and revision."""
    db, path = desk
    for i in range(8):
        evidence.observe(db, dict(BASE, id=f"c{i}", channel="camera", bit=int(i % 3 == 0)), T0 + i)
        evidence.observe(db, dict(BASE, id=f"w{i}", channel="word", root="ana", bit=0), T0 + i)
    executive.decide(db, "desk", T0 + 50, record=True)
    before = snapshot(db)
    assert before["pending"] and before["revisions"]
    db.close()
    reopened = store.open_existing(path)
    try:
        assert snapshot(reopened) == before
    finally:
        reopened.close()


def run_cli(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_cli_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    code, born, _ = run_cli(capsys, "init", "--db", db)
    assert code == 0 and born["memory"]["epochs"] == 0
    code, out, _ = run_cli(capsys, "epoch", "start", "desk", "--db", db, "--note", "first table")
    assert code == 0 and out == {"epoch": "desk", "created": True}
    for i in range(6):
        code, out, _ = run_cli(capsys, "observe", "desk", f"c{i}", "camera", "1", "0", "cam-a", "--db", db)
        assert code == 0 and out["status"] == "created"
        code, out, _ = run_cli(capsys, "observe", "desk", f"w{i}", "word", "1", "0", "ana", "--db", db,
                               "--sample-id", f"utt{i}", "--actor", "ana", "--acquired-at", "1800000000.5")
        assert code == 0
    code, out, _ = run_cli(capsys, "observe", "desk", "m0", "camera", "0", "0", "cam-a", "--db", db,
                           "--memory-only", "--simulated")
    assert code == 0
    code, out, _ = run_cli(capsys, "observe", "desk", "c0", "camera", "1", "0", "cam-a", "--db", db)
    assert code == 0 and out["status"] == "duplicate"
    code, out, err = run_cli(capsys, "observe", "desk", "c0", "camera", "1", "1", "cam-a", "--db", db)
    assert code == 2 and out is None and "different observation" in err
    code, out, err = run_cli(capsys, "observe", "attic", "x", "camera", "1", "1", "cam-a", "--db", db)
    assert code == 2 and "Unknown epoch" in err

    code, status, _ = run_cli(capsys, "status", "desk", "--db", db)
    assert code == 0 and status["evidence"] == {"total": 13, "learning": 12, "memory_only": 1,
                                                 "duplicates_ignored": 0}
    assert status["mechanism"]["identifiable"] is False and status["legacy"] is not None
    code, decision, _ = run_cli(capsys, "decide", "desk", "--record", "--db", db)
    assert code == 0 and decision["decision"] == "test_reference" and decision["scope"] == "mechanism"
    assert "try a known mechanical reference from a distinct root" in decision["reason"]
    code, questions, _ = run_cli(capsys, "pending", "--db", db, "--epoch", "desk")
    assert code == 0 and [q["id"] for q in questions] == [decision["pending_id"]]
    code, resolved, _ = run_cli(capsys, "resolve", str(decision["pending_id"]), "ran the end-stop test", "--db", db)
    assert code == 0 and resolved["resolution"] == "ran the end-stop test" and resolved["resolved_at"]
    code, questions, _ = run_cli(capsys, "pending", "--db", db)
    assert code == 0 and questions == []
    code, questions, _ = run_cli(capsys, "pending", "--all", "--db", db)
    assert len(questions) == 1
    code, out, err = run_cli(capsys, "resolve", str(decision["pending_id"]), "something else", "--db", db)
    assert code == 2 and "already resolved" in err
    code, report, _ = run_cli(capsys, "explain", "desk", "--db", db)
    assert code == 0 and report["beliefs"]["evidence"]["learning"] == 12
    assert {f["scope"] for f in report["factors"]} == {"mechanism", "camera:cam-a", "partner:ana"}
    code, inv, _ = run_cli(capsys, "birth", "--db", db)
    assert inv["development"]["teacher_interventions"] == 1 and inv["development"]["repeat_views"] == 1
    assert inv["development"]["encoder_exposure:word"] == 6
