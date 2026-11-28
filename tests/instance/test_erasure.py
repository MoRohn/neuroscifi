"""Erasure reaches derived memory: forgotten roots and event ids leave no trace in any table, the WAL or the file."""
import base64 as _b64
import gzip as _gzip
import hashlib
import json
import sqlite3
import struct as _struct
import zlib as _zlib

import pytest

from neuroscifi.instance import beliefs, checkpoints, executive, evidence, governance, imagined, schema, store
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
    """With consolidation off an expired event leaves nothing, as before schema 14: its root is scrubbed."""
    db, path = instance
    store.set_config(db, {"consolidation": False})
    evidence.start_epoch(db, "e", T0)
    observe(db, "e", "x1", "camera", CAMERA, now=T0)
    observe(db, "e", "keep", "camera", "cam-b", now=T0 + 1)
    governance.promote(db, "keep", T0 + 2)
    out = governance.sweep(db, T0 + 8 * 86400)
    assert hits(db, CAMERA) == [] and CAMERA.encode() not in raw_bytes(path)
    assert hits(db, "x1", skip={("tombstones", "id")}) == []
    assert out["expired"] == 1 and out["wal_checkpoint"] == "ok" and "consolidated" not in out


def test_sweep_keeps_the_root_of_a_consolidated_reading_until_it_is_forgotten(instance):
    """With consolidation (the default, ADR-044) the expired event's root is still evidence the individual holds: it
    is not a forgotten root. Its id leaves every table but the tombstone. Forgetting the event by its id then
    removes the reading and scrubs the root from the file."""
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    observe(db, "e", "x1", "camera", CAMERA, now=T0)
    observe(db, "e", "keep", "camera", "cam-b", now=T0 + 1)
    governance.promote(db, "keep", T0 + 2)
    out = governance.sweep(db, T0 + 8 * 86400)
    assert out["expired"] == 1 and out["consolidated"] == 1 and out["wal_checkpoint"] == "ok"
    assert ("evidence_consolidated", "root") in hits(db, CAMERA)
    assert db.execute("SELECT count(*) FROM forgotten_roots").fetchone()[0] == 0
    assert hits(db, "x1", skip={("tombstones", "id")}) == []
    gone = governance.forget_evidence(db, "x1", T0 + 8 * 86400 + 1)
    assert gone["status"] == "forgotten" and gone["effects"]["consolidated_deleted"] == 1
    assert gone["effects"]["roots_forgotten"] == 1
    assert hits(db, CAMERA) == [] and CAMERA.encode() not in raw_bytes(path)
    assert db.execute("SELECT kind FROM forgotten_roots").fetchall() == [("camera",)]


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


# ---- imagined payloads, including encoded values (OMNIMIND lane S, audit-cognition hazard 1) ---------------------
# The canary below decodes on its own (no product code): base64 (and data: URLs), zlib and gzip streams, and the
# compressed text chunks of a PNG, nested a few levels, so a forgotten name cannot hide in an encoded value.


def _png(*chunks):
    """A PNG built by hand: signature, a 1x1 grey IHDR, the given (type, body) chunks, IDAT, IEND."""
    def chunk(kind, body):
        return _struct.pack(">I", len(body)) + kind + body + _struct.pack(">I", _zlib.crc32(kind + body))
    ihdr = _struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + b"".join(chunk(k, b) for k, b in chunks)
            + chunk(b"IDAT", _zlib.compress(b"\x00\x80")) + chunk(b"IEND", b""))


def _canary_decodings(data, depth=0):
    """Every byte string reachable from `data` by base64, zlib, gzip and PNG text chunks (independent of product)."""
    yield data
    if depth >= 4:
        return
    found = []
    text = data.strip()
    if text[:5].lower() == b"data:" and b"," in text:
        text = text.split(b",", 1)[1]
    for decode in (_b64.b64decode, _b64.urlsafe_b64decode):
        try:
            found.append(decode(text + b"=" * (-len(text) % 4)))
        except Exception:
            pass
    for wbits in (15, 31):
        try:
            found.append(_zlib.decompress(data, wbits))
        except Exception:
            pass
    if data.startswith(b"\x89PNG"):
        at = 8
        while at + 8 <= len(data):
            length, kind = _struct.unpack(">I4s", data[at:at + 8])
            body = data[at + 8:at + 8 + length]
            if kind == b"zTXt":
                found.append(_zlib.decompress(body.split(b"\0", 1)[1][1:]))
            elif kind == b"iTXt" and body.split(b"\0", 1)[1][:1] == b"\x01":
                found.append(_zlib.decompress(body.split(b"\0", 1)[1][2:].split(b"\0", 2)[2]))
            at += 12 + length
    for inner in found:
        yield from _canary_decodings(inner, depth + 1)


def encoded_hits(db, needle):
    """(table, column) of every text value, JSON strings walked, that holds `needle` once decoded."""
    forms = [needle.encode(), needle.encode("utf-16-le"), needle.encode("utf-16-be")]

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
    out = set()
    for table, column, value in text_values(db):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
        for text in strings(parsed if isinstance(parsed, (str, dict, list)) else value):
            if any(f in d for d in _canary_decodings(text.encode()) for f in forms):
                out.add((table, column))
    return sorted(out)


def _imagine(db, payload, now=T0, **kw):
    return imagined.record(db, "speculation", payload=payload, assumptions=kw.pop("assumptions", []),
                           uncertainty=kw.pop("uncertainty", "untested"), mechanism="test", mechanism_version="1",
                           question=kw.pop("question", "what if?"), now=now, **kw)


def seed_imagined(db, name):
    """Records naming `name` in every form the forget must reach, and records that must survive it."""
    b64 = lambda b: _b64.b64encode(b).decode()      # noqa: E731
    exif_like = b"Exif\x00\x00II*\x00" + name.encode("utf-16-le") + b"\x00\x00"
    naming = [
        {"note": f"talked with {name}"},                                                     # plain text
        {"image": "data:image/png;base64," + b64(_png((b"tEXt", b"Author\x00" + name.encode())) + b"\0" * 40000)},
        {"image": b64(_png((b"zTXt", b"Comment\x00\x00" + _zlib.compress(f"with {name}".encode()))))},
        {"image": b64(_png((b"iTXt", b"Comment\x00\x01\x00en\x00\x00" + _zlib.compress(name.encode()))))},
        {"blob": b64(_gzip.compress(json.dumps({"who": name}).encode()))},
        {"blob": _b64.urlsafe_b64encode(_zlib.compress(("x" * 500 + name).encode())).decode().rstrip("=")},
        {"nested": b64(b64(f"{name} again".encode()).encode())},
        {"exif": b64(exif_like)},
        {f"key-{b64(name.encode())}": 1},
    ]
    gone = [_imagine(db, p, now=T0 + i) for i, p in enumerate(naming)]
    gone.append(_imagine(db, {"x": 1}, now=T0 + 20, assumptions=[f"{name} is home"]))
    gone.append(_imagine(db, {"x": 2}, now=T0 + 21, question=f"will {name} call?"))
    random = _b64.b64encode(bytes((i * 37 + 11) % 256 for i in range(30000))).decode()
    kept = [_imagine(db, {"note": "a different partner, Dora-k2"}, now=T0 + 30),
            _imagine(db, {"image": random}, now=T0 + 31),
            _imagine(db, {"blob": b64(_gzip.compress(b"nothing to see"))}, now=T0 + 32)]
    return gone, kept


def test_forget_partner_removes_imagined_records_that_name_it_even_inside_encoded_values(instance, tmp_path):
    db, path = instance
    teach_ambiguous_partner(db)
    gone, kept = seed_imagined(db, PARTNER)
    assert {("imagined", "payload"), ("imagined", "assumptions"), ("imagined", "question")} <= set(
        encoded_hits(db, PARTNER)), "precondition: the canary sees every encoded form"
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 50)
    out = governance.forget_partner(db, PARTNER, T0 + 100)
    assert hits(db, PARTNER) == [] and encoded_hits(db, PARTNER) == []
    assert PARTNER.encode() not in raw_bytes(path)
    held = {i for (i,) in db.execute("SELECT id FROM imagined")}
    assert held == set(kept)
    assert db.execute("SELECT count(*) FROM imagined_forgotten WHERE reason='forgotten'").fetchone()[0] == len(gone)
    assert out["effects"]["imagined_removed"] == len(gone) and cp["id"] in out["effects"]["checkpoints_stale"]
    logged = json.loads(db.execute("SELECT effects FROM deletion_log ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert logged["imagined_removed"] == len(gone)


def test_a_partner_forgotten_as_one_kind_removes_only_records_naming_that_kind(instance):
    """Scoped forget (a camera of the same name is still held): `partner:<name>` goes, the bare name may be the
    camera's and stays, as in scrub_roots."""
    db, _ = instance
    evidence.start_epoch(db, "e", T0)
    observe(db, "e", "w1", "word", PARTNER, now=T0)
    observe(db, "e", "c1", "camera", PARTNER, now=T0 + 1)
    scoped = _imagine(db, {"blob": _b64.b64encode(f"partner:{PARTNER}".encode()).decode()})
    bare = _imagine(db, {"note": f"{PARTNER} on the shelf"}, now=T0 + 1)
    governance.forget_evidence(db, "w1", T0 + 2)
    assert {i for (i,) in db.execute("SELECT id FROM imagined")} == {bare} and scoped != bare


def test_nothing_is_removed_when_no_record_names_the_forgotten_root(instance):
    db, _ = instance
    teach_ambiguous_partner(db)
    _, kept = seed_imagined(db, "Someone-else-77")
    before = db.execute("SELECT * FROM imagined ORDER BY id").fetchall()
    governance.forget_partner(db, PARTNER, T0 + 100)
    assert db.execute("SELECT * FROM imagined ORDER BY id").fetchall() == before
    assert db.execute("SELECT count(*) FROM imagined_forgotten").fetchone()[0] == 0


def test_a_value_too_large_to_inspect_within_the_bounds_counts_as_naming_it(instance):
    db, _ = instance
    teach_ambiguous_partner(db)
    bomb = _b64.b64encode(_zlib.compress(b"\0" * (imagined.DECODE_MAX_BYTES + 1), 9)).decode()
    assert len(bomb) < 65000
    _imagine(db, {"blob": bomb})
    governance.forget_partner(db, PARTNER, T0 + 100)
    assert db.execute("SELECT count(*) FROM imagined").fetchone()[0] == 0


@pytest.mark.parametrize("tombstones", ["kept", "dropped"])
def test_a_restore_of_an_archive_taken_before_the_forget_removes_the_same_records(instance, tmp_path, tombstones):
    """The replay removes them twice over: by the live tombstones (imagined.replay) and, with those dropped, by the
    same text scan (scrub_roots in _scrub_copy)."""
    db, path = instance
    teach_ambiguous_partner(db)
    gone, kept = seed_imagined(db, PARTNER)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 50)
    governance.forget_partner(db, PARTNER, T0 + 100)
    if tombstones == "dropped":
        with store.write(db):
            db.execute("DELETE FROM imagined_forgotten")
    checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 200)
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert {i for (i,) in copy.execute("SELECT id FROM imagined")} == set(kept)
        assert hits(copy, PARTNER) == [] and encoded_hits(copy, PARTNER) == []
    finally:
        copy.close()
    assert PARTNER.encode() not in raw_bytes(tmp_path / "restored.sqlite")


def test_an_archive_holding_only_the_record_and_not_the_partners_evidence_becomes_stale(instance, tmp_path):
    db, path = instance
    _imagine(db, {"note": f"{PARTNER} may visit"}, now=T0 - 10)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 - 5)          # before any evidence of the partner
    teach_ambiguous_partner(db)
    out = governance.forget_partner(db, PARTNER, T0 + 100)
    assert out["effects"]["imagined_removed"] == 1 and cp["id"] in out["effects"]["checkpoints_stale"]
    assert db.execute("SELECT status FROM checkpoints WHERE id=?", (cp["id"],)).fetchone()[0] == "stale"
