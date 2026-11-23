"""Erasure reaches derived memory: forgotten roots and event ids leave no trace in any table, the WAL or the file."""
import hashlib
import json
import sqlite3

from neuroscifi.instance import beliefs, executive, evidence, governance, schema, store
from conftest import T0

PARTNER = "Alice-zq9"
UNICODE_PARTNER = "Zoë-qq7"
CAMERA = "cam-secret-41"


def text_values(db, *, skip=()):
    """(table, column, value) for every non-blob value of every table, except the (table, column) pairs in skip."""
    out = []
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cursor = db.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cursor.description]
        for row in cursor:
            out.extend((table, c, str(v)) for c, v in zip(columns, row)
                       if (table, c) not in skip and not isinstance(v, bytes))
    return out


def hits(db, needle, *, skip=()):
    return [(t, c) for t, c, v in text_values(db, skip=skip) if needle in v]


def raw_bytes(path):
    data = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


def digest(db, root):
    return hashlib.sha256(f"{store.birth(db)['instance_id']}:{root}".encode()).hexdigest()[:16]


def observe(db, epoch, id, channel, root, action=0, bit=0, now=T0):
    evidence.observe(db, {"id": id, "epoch": epoch, "channel": channel, "action": action, "bit": bit,
                          "root": root}, now)


def teach_ambiguous_partner(db, epoch="bench", partner=PARTNER, camera="cam-a", reference="std"):
    """Camera and references pin the mechanism; the partner's 4 words conflict, so the executive asks about them."""
    evidence.start_epoch(db, epoch, T0)
    for i in range(4):
        observe(db, epoch, f"{epoch}-c{i}", "camera", camera, i % 2, i % 2, T0 + i)
    for r in range(3):
        observe(db, epoch, f"{epoch}-r{r}", "reference", f"{reference}-{r}", now=T0 + 10 + r)
    for i, bit in enumerate((0, 1, 0, 1)):
        observe(db, epoch, f"{epoch}-w{i}", "word", partner, 0, bit, T0 + 20 + i)
    decision = executive.decide(db, epoch, T0 + 50, record=True)
    assert decision["decision"] == "ask" and decision["scope"] == f"partner:{partner}"


def test_forget_partner_scrubs_beliefs_questions_and_decisions(instance):
    db, path = instance
    teach_ambiguous_partner(db)
    for table in ("belief_revisions", "pending", "decisions"):
        assert table in {t for t, _ in hits(db, PARTNER)}, f"precondition: {table} names the partner"
    label = f"partner:#{digest(db, PARTNER)}"

    out = governance.forget_partner(db, PARTNER, T0 + 100)
    assert hits(db, PARTNER) == []
    assert PARTNER.encode() not in raw_bytes(path)
    assert out["status"] == "forgotten" and out["effects"]["roots_forgotten"] == 1
    assert out["effects"]["wal_checkpoint"] == "ok"
    # The labels keep the structure readable and stable.
    assert db.execute("SELECT scope FROM pending").fetchall() == [(label,)]
    assert db.execute("SELECT scope FROM decisions").fetchall() == [(label,)]
    assert label in db.execute("SELECT question FROM pending").fetchone()[0]
    summaries = [json.loads(s) for (s,) in db.execute("SELECT summary FROM belief_revisions ORDER BY id")]
    assert any(label in s["partners"] for s in summaries)
    assert all(set(s["sensors"]) <= {"cam-a"} for s in summaries)  # a root still in use is untouched
    assert db.execute("SELECT digest,kind,forgotten_at FROM forgotten_roots").fetchall() == [
        (label.split("#")[1], "partner", T0 + 100)]
    log = db.execute("SELECT target_id FROM deletion_log").fetchall()
    assert log == [(label,)]


def test_non_ascii_root_is_scrubbed_from_escaped_json(instance):
    db, path = instance
    teach_ambiguous_partner(db, partner=UNICODE_PARTNER)
    stored = db.execute("SELECT summary FROM belief_revisions ORDER BY id DESC LIMIT 1").fetchone()[0]
    escaped = json.dumps(UNICODE_PARTNER)[1:-1]
    assert escaped in stored and UNICODE_PARTNER not in stored  # summaries are ASCII-escaped JSON
    governance.forget_partner(db, UNICODE_PARTNER, T0 + 100)
    assert hits(db, UNICODE_PARTNER) == [] and hits(db, escaped) == []
    raw = raw_bytes(path)
    assert UNICODE_PARTNER.encode() not in raw and escaped.encode() not in raw


def test_last_camera_event_forgotten_scrubs_root_and_event_id(instance):
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    secret_id = "bob-visit-2027-01-15"
    observe(db, "e", secret_id, "camera", CAMERA, 0, 1, T0)
    observe(db, "e", "other", "camera", "cam-b", 0, 0, T0 + 1)
    assert hits(db, CAMERA) and ("belief_revisions", "cause") in hits(db, secret_id)

    out = governance.forget_evidence(db, secret_id, T0 + 2)
    # The tombstone (and the deletion log entry that names it) must keep the id so it cannot be re-recorded.
    keep = {("tombstones", "id"), ("deletion_log", "target_id")}
    assert hits(db, CAMERA) == [] and hits(db, secret_id, skip=keep) == []
    assert CAMERA.encode() not in raw_bytes(path)
    assert out["effects"]["roots_forgotten"] == 1
    causes = [c for (c,) in db.execute("SELECT cause FROM belief_revisions ORDER BY id")]
    assert causes == ["evidence:#forgotten", "evidence:other", "evidence:#forgotten"]
    label = f"camera:#{digest(db, CAMERA)}"
    first = json.loads(db.execute("SELECT summary FROM belief_revisions ORDER BY id LIMIT 1").fetchone()[0])
    assert list(first["sensors"]) == [label] and first["unidentifiable"][-1]["scope"] == label
    assert db.execute("SELECT kind FROM forgotten_roots").fetchall() == [("camera",)]


def test_sweep_expiry_scrubs_a_root_whose_last_evidence_expired(instance):
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    observe(db, "e", "x1", "camera", CAMERA, now=T0)
    observe(db, "e", "keep", "camera", "cam-b", now=T0 + 1)
    governance.promote(db, "keep", T0 + 2)
    out = governance.sweep(db, T0 + 8 * 86400)
    assert hits(db, CAMERA) == [] and CAMERA.encode() not in raw_bytes(path)
    assert hits(db, "x1", skip={("tombstones", "id")}) == []
    assert out["expired"] == 1 and out["wal_checkpoint"] == "ok"


def test_root_with_evidence_left_is_not_scrubbed(instance):
    db, _ = instance
    evidence.start_epoch(db, "e1", T0)
    evidence.start_epoch(db, "e2", T0)
    observe(db, "e1", "a", "word", PARTNER, now=T0)
    observe(db, "e2", "b", "word", PARTNER, now=T0 + 1)
    out = governance.forget_evidence(db, "a", T0 + 2)
    assert ("belief_revisions", "summary") in hits(db, PARTNER)  # guard: still in use, so not scrubbed
    assert out["effects"]["roots_forgotten"] == 0
    assert db.execute("SELECT count(*) FROM forgotten_roots").fetchone()[0] == 0


def confirm_camera_change(db, epoch, camera, *, start=T0):
    """Interleaved references pin the mechanism; the camera's readings flip after 10 rounds; the owner confirms.

    Returns the recorded changepoint row (a changepoints.scope naming the camera root).
    """
    evidence.start_epoch(db, epoch, start)
    for i in range(16):
        corrected = int(i >= 10)
        observe(db, epoch, f"{epoch}-c{i}", "camera", camera, i % 2, corrected ^ (i % 2), start + 2 * i)
        observe(db, epoch, f"{epoch}-r{i}", "reference", f"{epoch}-std-{i % 3}", now=start + 2 * i + 1)
    asked = executive.decide(db, epoch, start + 50, record=True)
    assert asked["decision"] == "ask" and asked["scope"] == f"camera:{camera}"
    done = executive.resolve(db, asked["pending_id"], "yes, remounted", start + 51, confirm=True)
    assert done["changepoint"]["confirmed"] is True
    return done["changepoint"]


def test_forget_epoch_deletes_its_revisions_decisions_and_questions(instance):
    db, _ = instance
    teach_ambiguous_partner(db, "bench")
    # A distinct camera and distinct reference roots: replaying bench's setup here would (correctly) make the
    # executive ask "This looks like bench; continue there?" instead of asking about the partner.
    teach_ambiguous_partner(db, "other", partner="Carol-x", camera="cam-b", reference="std-b")
    counts = lambda epoch: [db.execute(f"SELECT count(*) FROM {t} WHERE epoch=?", (epoch,)).fetchone()[0]  # noqa: E731
                            for t in ("belief_revisions", "decisions", "pending")]
    assert all(counts("bench")) and all(counts("other"))
    before = counts("other")
    out = governance.forget_epoch(db, "bench", T0 + 100)
    assert out["status"] == "forgotten" and out["effects"]["deleted"] == 11
    assert counts("bench") == [0, 0, 0]
    assert counts("other") == before
    assert out["effects"]["derived_deleted"]["decisions"] == 1
    assert db.execute("SELECT target_kind,target_id FROM deletion_log").fetchall() == [("epoch", "bench")]
    assert hits(db, PARTNER) == []
    assert hits(db, "Carol-x")  # the other epoch's partner is still known


def test_forget_epoch_clears_the_owners_free_text_note_even_when_the_epoch_holds_nothing_else(instance):
    """The note is the owner's own words about the epoch: forget_epoch keeps the name (tombstones and the deletion
    log refer to it) but no copy of the note, in the table or in a freed page."""
    db, path = instance
    teach_ambiguous_partner(db, "bench")
    evidence.start_epoch(db, "bare", T0, note="bare note: Alice's flat qz")
    with store.write(db):
        db.execute("UPDATE epochs SET note=? WHERE name='bench'", ("bench note: Alice's office qz",))
    assert governance.forget_epoch(db, "bench", T0 + 100)["effects"]["derived_deleted"]["epoch_note"] == 1
    out = governance.forget_epoch(db, "bare", T0 + 101)
    assert out["status"] == "forgotten" and out["effects"]["derived_deleted"]["epoch_note"] == 1
    assert db.execute("SELECT name, note FROM epochs ORDER BY name").fetchall() == [("bare", None), ("bench", None)]
    assert b"note: Alice" not in raw_bytes(path)
    assert governance.forget_epoch(db, "bare", T0 + 102)["status"] == "absent"


def test_wal_is_truncated_after_forgetting_while_another_reader_is_open(instance):
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    observe(db, "e", "x", "word", PARTNER, now=T0)
    wal = path.with_name(path.name + "-wal")
    reader = store.connect(path)
    try:
        assert reader.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1  # idle reader, no open txn
        assert PARTNER.encode() in wal.read_bytes()  # precondition: the insert is in the WAL
        out = governance.forget_partner(db, PARTNER, T0 + 1)
        assert wal.stat().st_size == 0
        assert PARTNER.encode() not in raw_bytes(path)
        assert out["effects"]["wal_checkpoint"] == "ok"
        assert reader.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    finally:
        reader.close()


def test_busy_wal_checkpoint_is_reported_not_raised(instance):
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    observe(db, "e", "x", "word", PARTNER, now=T0)
    reader = store.connect(path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM evidence").fetchone()  # holds a read snapshot
        out = governance.forget_partner(db, PARTNER, T0 + 1)
        assert out["status"] == "forgotten" and out["effects"]["wal_checkpoint"] == "busy"
        reader.execute("COMMIT")
    finally:
        reader.close()
    assert store.truncate_wal(db) == "ok"
    assert PARTNER.encode() not in raw_bytes(path)


def test_wal_size_limit_is_zero_on_every_connection(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        assert db.execute("PRAGMA journal_size_limit").fetchone()[0] == 0
        assert db.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    finally:
        db.close()
    with sqlite3.connect(tmp_path / "i.sqlite") as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION >= 8


def test_forget_epoch_deletes_its_changepoints_and_keeps_other_epochs(instance):
    db, _ = instance
    confirm_camera_change(db, "bench", "cam-a")
    kept = confirm_camera_change(db, "other", "cam-b")
    out = governance.forget_epoch(db, "bench", T0 + 100)
    assert out["effects"]["derived_deleted"]["changepoints"] == 1
    assert db.execute("SELECT id,epoch,scope FROM changepoints").fetchall() == [(kept["id"], "other", "camera:cam-b")]
    assert hits(db, "cam-a") == []
    assert beliefs.changes(db, "other")["confirmed"] == {"camera:cam-b": [kept["since_seq"]]}


def test_forgotten_root_deletes_its_changepoints_in_every_epoch(instance):
    db, path = instance
    confirm_camera_change(db, "bench", CAMERA)
    evidence.start_epoch(db, "bench2", T0 + 199)
    for i in range(3):  # the same camera seen in a second epoch, so the root survives the first forget
        observe(db, "bench2", f"b2-{i}", "camera", CAMERA, now=T0 + 200 + i)
    with store.write(db):  # a rejected change of the same root in the second epoch
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('bench2',?,0,NULL,?,0)", (f"camera:{CAMERA}", T0 + 210))
    assert ("changepoints", "scope") in hits(db, CAMERA)  # precondition for the scan below

    first = governance.forget_epoch(db, "bench", T0 + 300)
    assert first["effects"]["roots_forgotten"] == 0  # bench2 still has CAMERA evidence
    assert db.execute("SELECT epoch FROM changepoints").fetchall() == [("bench2",)]

    for i in range(3):
        governance.forget_evidence(db, f"b2-{i}", T0 + 301 + i)
    assert db.execute("SELECT count(*) FROM changepoints").fetchone()[0] == 0
    assert hits(db, CAMERA) == []
    assert CAMERA.encode() not in raw_bytes(path)
    assert db.execute("SELECT kind FROM forgotten_roots").fetchall() == [("camera",)]
