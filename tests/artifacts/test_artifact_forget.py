"""Forget and restore reach the artifact store (OMNIMIND lane A; contracts C5 and C7 of the cognition audit): the
owner's forget of an artifact, of a partner or camera root (whose name may sit in a label, an index, a text layer,
the raw bytes in several encodings, or a compressed member of a stored archive), and a restore of an older archive.
Each test seeds the store, forgets, then scans every table, the individual's file and WAL, and the sidecar's file and
WAL, byte for byte. The seeded files are built here by hand or with the standard library's own writers (zipfile,
gzip, bz2, lzma, zstandard), independently of the scanner."""
from contextlib import closing
import base64
import bz2
import gzip
import io
import json
import lzma
import os
from pathlib import Path
import re
import sqlite3
import struct
import zipfile
import zlib

import pytest
import zstandard

from neuroscifi import archive
from neuroscifi.artifacts import store as A
from neuroscifi.instance import checkpoints, evidence, governance, store

import test_artifact_store as base
from test_artifact_store import T0, count, made_of, raw, side_path

# the fixtures of the store's own tests, shared (a conftest here would clash with tests/instance's)
registry, individual, form = base.registry, base.individual, base.form

ROOT = "Ottoline-zq7"
NOISE = bytes(range(256)) * 4


def all_text(db):
    out = []
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in db.execute(f"SELECT * FROM {name}"):
            out.extend(v.decode("latin-1") if isinstance(v, bytes) else str(v) for v in row)
    return "\n".join(out)


SENTENCE = f"Met {ROOT} at the lab.".encode()


def forms_of(root):
    """The forms the seeded files hold the root in, written out here (not taken from the scanner)."""
    return [root.encode("utf-16-le"), root.encode("utf-16-be"), base64.b64encode(b"..." + SENTENCE)]


# Enough varied words before the name that every compressor below codes it (Huffman or otherwise), not as raw bytes:
# test_the_compressed_cases_hide_the_name_from_a_plain_search proves it.
WORDS = (b" ".join(b"word%d" % i for i in range(400)) + SENTENCE) * 3


def names_root(data: bytes) -> bool:
    """The root as a whole word in UTF-8 (a longer word that contains it, kept on purpose, does not count)."""
    return re.search(rb"(?<![A-Za-z0-9_-])" + re.escape(ROOT.encode()) + rb"(?![A-Za-z0-9_-])", data) is not None


def png_with_ztxt(keyword, text):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"zTXt", keyword + b"\0\0" + zlib.compress(text))
            + chunk(b"IDAT", zlib.compress(b"\0\0")) + chunk(b"IEND", b""))


def zip_holding_a_gzip(text):
    inner = gzip.compress(text, mtime=0)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("notes/inner.txt.gz", inner)
        z.writestr("readme.txt", b"nothing to see " * 50)
    return out.getvalue()


def seed_named(db):
    """{case: artifact id} of artifacts that name ROOT in one place each, and {case: id} of ones that must stay."""
    sentence = SENTENCE
    named = {
        "label": A.add(db, NOISE, name=f"{ROOT} holiday.bin", now=T0).id,
        "index": A.add(db, json.dumps({"title": f"Readings of {ROOT}", "columns": {"x": [1, 2]}}).encode(),
                       name="readings.json", now=T0).id,
        "text": A.add(db, sentence + b"\n", name="note.txt", now=T0).id,
        "utf16": A.add(db, NOISE + ROOT.encode("utf-16-le") + NOISE, name="u16.bin", now=T0).id,
        "utf16be": A.add(db, NOISE + b"\0 " + ROOT.encode("utf-16-be") + b"\0." + NOISE, name="u16be.bin", now=T0).id,
        "base64": A.add(db, b"data:" + base64.b64encode(b"..." + sentence) + b";" + NOISE, name="b64.bin", now=T0).id,
        "zip>gzip": A.add(db, zip_holding_a_gzip(sentence * 20), name="bundle.zip", now=T0).id,
        "zstd": A.add(db, NOISE + zstandard.ZstdCompressor().compress(WORDS + sentence), name="z.bin", now=T0).id,
        "bzip2": A.add(db, NOISE + bz2.compress(sentence * 30), name="b.bin", now=T0).id,
        "xz": A.add(db, NOISE + lzma.compress(sentence * 30), name="x.bin", now=T0).id,
        "png-ztxt": A.add(db, png_with_ztxt(b"Comment", sentence), name="p.png", now=T0).id,
    }
    kept = {
        "longer word": A.add(db, f"{ROOT}x and x{ROOT} are other names\n".encode(), name="other.txt", now=T0).id,
        "unrelated": A.add(db, b"nothing about anyone\n", name="plain.txt", now=T0).id,
        # Text drawn as pixels: an 8-bit grey picture whose dark pixels spell the name cannot be read here (no OCR);
        # the scan does not find it and says so in the module doc. Here the "letters" are a pattern of 0/255 bytes.
        "pixels": A.add(db, bytes(255 if (i * 7) % 11 < 3 else 0 for i in range(4096)), name="drawn.raw", now=T0).id,
    }
    return named, kept


def test_the_compressed_cases_hide_the_name_from_a_plain_search():
    """Without this the compressed cases below would prove nothing: a compressor that stored the name as raw bytes
    would be found by the plain search, not by looking inside the stream."""
    for data in (zip_holding_a_gzip(SENTENCE * 20), zstandard.ZstdCompressor().compress(WORDS + SENTENCE),
                 bz2.compress(SENTENCE * 30), lzma.compress(SENTENCE * 30), png_with_ztxt(b"Comment", SENTENCE)):
        assert ROOT.encode() not in data and ROOT.encode("utf-16-le") not in data
        assert not A._Needles(ROOT).found(data)


def held(db):
    return {i for (i,) in db.execute("SELECT id FROM artifacts")}


def assert_root_gone_everywhere(db, path):
    assert not names_root(all_text(db).encode())
    for where in (path, side_path(path)):
        data = raw(where)
        assert not names_root(data), where.name
        for form_ in forms_of(ROOT):
            assert form_ not in data, (where.name, form_)


# ---- forget of one artifact ----------------------------------------------------------------------------------------------

def test_forget_an_artifact_and_everything_made_from_it(individual, form, tmp_path):
    db, path = individual
    secret = b"PRIVATE-BYTES-" + os.urandom(8).hex().encode()
    source = A.add(db, secret * 40 + b"\nwords of the source\n", name="source.txt", now=T0)
    twin = A.add(db, b"same bytes twice\n", name="twin-1.txt", now=T0)
    twin2 = A.add(db, b"same bytes twice\n", name="twin-2.txt", now=T0)
    made, s = made_of([source.id], title="Chart of the source")
    first = A.add_made(db, made, s, [source.id], now=T0 + 1)
    made2, s2 = made_of([first.id], title="Chart of the chart", values=(9,))
    second = A.add_made(db, made2, s2, [first.id], now=T0 + 2)
    A.link(db, twin.id, "same_subject", "artifact", source.id, "owner.said", 1.0)
    ck = checkpoints.create(db, path, tmp_path / "ck", T0 + 3)
    version = store.state_version(db)

    result = A.forget_artifact(db, source.id, T0 + 4)

    assert result["status"] == "forgotten"
    effects = result["effects"]
    assert effects["artifacts_removed"] == 3 and effects["made_or_contained_removed"] == 2
    assert effects["checkpoints_stale"] == [ck["id"]] and effects["state_version"] == version + 1
    assert effects["artifact_bytes_removed"] == 3 and effects["wal_checkpoint"] == "ok"
    assert held(db) == {twin.id, twin2.id}
    assert count(db, "artifact_forgotten") == 3 and count(db, "artifact_links") == 0
    assert {i for (i,) in db.execute("SELECT artifact_id FROM artifact_events")} == {twin.id, twin2.id}
    log = db.execute("SELECT target_kind, target_id, effects FROM deletion_log WHERE target_kind='artifact'").fetchall()
    assert len(log) == 1 and log[0][1] == "#" + A.digest(source.id, store.birth(db)["instance_id"])
    text = all_text(db)
    for gone in (source.id, first.id, second.id, "Chart of the", "source.txt"):
        assert gone not in text
    assert secret not in raw(side_path(path)) and b"Chart of the" not in raw(side_path(path))
    assert A.read_bytes(db, twin2.id) == b"same bytes twice\n"
    assert A.forget_artifact(db, source.id, T0 + 5)["status"] == "absent"
    # the archive made stale is rewritten in place without them (the kind is rewritable)
    after = checkpoints.after_forget(db, T0 + 6)
    assert [r["id"] for r in after["checkpoints_rewritten"]] == [ck["id"]] and after["checkpoints_stale"] == []
    decoded = tmp_path / "decoded.sqlite"
    with open(checkpoints.listing(db)[0]["path"], "rb") as source_file, decoded.open("wb") as out:
        archive.unpack(source_file, out)
    with closing(sqlite3.connect(decoded)) as copy:
        assert {i for (i,) in copy.execute("SELECT id FROM artifacts")} == {twin.id, twin2.id}


def test_a_forgotten_contained_member_goes_with_its_bundle(individual):
    db, _ = individual
    bundle = A.add(db, zip_holding_a_gzip(b"x"), name="b.zip", now=T0)
    member = A.add(db, b"member words\n", name="m.txt", now=T0)
    A.link(db, bundle.id, "contains", "artifact", member.id, "zip.member", 1.0)
    assert A.forget_artifact(db, bundle.id, T0 + 1)["effects"]["artifacts_removed"] == 2
    assert held(db) == set()


# ---- forget of a root ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["partner", "camera"])
def test_forgetting_a_root_removes_every_artifact_that_names_it(individual, form, kind):
    db, path = individual
    evidence.start_epoch(db, "e1", T0)
    channel = "word" if kind == "partner" else "camera"
    evidence.observe(db, {"id": "ev1", "epoch": "e1", "channel": channel, "action": 0, "bit": 1, "root": ROOT}, T0)
    named, kept = seed_named(db)
    made, s = made_of([named["text"]], title="made from the note")
    derived = A.add_made(db, made, s, [named["text"]], now=T0 + 1).id

    forget = governance.forget_partner if kind == "partner" else governance.forget_camera
    result = forget(db, ROOT, T0 + 2)

    assert result["status"] == "forgotten"
    assert result["effects"]["artifacts_removed"] == len(named) + 1
    assert result["effects"]["artifact_bytes_removed"] == len(named) + 1
    assert held(db) == set(kept.values())
    assert derived not in held(db)
    log = db.execute("SELECT target_id, effects FROM deletion_log WHERE target_kind='artifact'").fetchone()
    assert log[0].startswith(f"{kind}:#") and json.loads(log[1])["made_or_contained_removed"] == 1
    reasons = sorted(r for (r,) in db.execute("SELECT reason FROM artifact_forgotten"))
    assert reasons == ["root_forgotten"] * len(named) + ["source_forgotten"]
    assert_root_gone_everywhere(db, path)


def test_a_root_with_no_evidence_still_takes_its_artifacts(individual):
    db, path = individual
    named = A.add(db, f"a letter to {ROOT}\n".encode(), name="letter.txt", now=T0).id
    result = governance.forget_partner(db, ROOT, T0 + 1)
    assert result["status"] == "forgotten" and result["effects"]["artifacts_removed"] == 1
    assert named not in held(db)
    assert_root_gone_everywhere(db, path)
    assert governance.forget_partner(db, ROOT, T0 + 2)["status"] == "absent"


def test_an_artifact_added_between_the_scan_and_the_transaction_is_found(individual):
    db, _ = individual
    early = A.add(db, f"{ROOT} early\n".encode(), name="early.txt", now=T0).id
    scan = A.scan_root(db, ROOT)
    assert scan.hits == [early]
    late = A.add(db, f"{ROOT} late\n".encode(), name="late.txt", now=T0 + 1).id
    with store.write(db):
        effects = A.forget_found(db, scan, T0 + 2, None, "partner:#test")
    assert effects["artifacts_removed"] == 2 and held(db) == set()
    assert {early, late} == set(scan.removed)
    A.finish(db, scan, {"status": "forgotten", "effects": effects})


def test_the_decompression_budget_bounds_the_scan_and_is_reported(individual, monkeypatch):
    db, _ = individual
    bomb = gzip.compress(b"\0" * (8 << 20), mtime=0)            # 8 MiB of zeros in about 8 KiB
    A.add(db, bomb + gzip.compress(ROOT.encode(), mtime=0), name="bomb.gz", now=T0)
    monkeypatch.setattr(A, "limits", lambda cfg: A.Limits(max_decoded_bytes=1 << 20))
    scan = A.scan_root(db, ROOT)
    assert scan.hits == [] and scan.budget_reached == 1          # the name after the bomb was not reached


# ---- restore -----------------------------------------------------------------------------------------------------------------

def test_restore_replays_artifact_forgets_and_gives_the_copy_its_bytes(individual, form, tmp_path):
    db, path = individual
    keep = A.add(db, b"kept for good\n", name="keep.txt", now=T0)
    drop = A.add(db, b"DROPPED-BYTES " * 30, name="drop.bin", now=T0)
    made, s = made_of([drop.id], title="made from the dropped")
    derived = A.add_made(db, made, s, [drop.id], now=T0)
    named = A.add(db, f"about {ROOT}\n".encode(), name="named.txt", now=T0)
    ck = checkpoints.create(db, path, tmp_path / "ck", T0 + 1)
    A.forget_artifact(db, drop.id, T0 + 2)
    governance.forget_partner(db, ROOT, T0 + 3)

    dest = tmp_path / "restored.sqlite"
    result = checkpoints.restore(db, ck["id"], dest, T0 + 4)

    assert result["artifacts_removed"] == 3 and result["artifact_bytes_copied"] == 1
    assert result["artifacts_missing"] == 0
    with closing(store.open_existing(dest, graph_migration=False)) as copy:
        assert held(copy) == {keep.id}
        assert A.read_bytes(copy, keep.id) == b"kept for good\n"
        assert count(copy, "artifact_forgotten") == 3
        for gone in (drop.id, derived.id, named.id, ROOT):
            assert gone not in all_text(copy)
    side = raw(side_path(dest))
    assert b"DROPPED-BYTES" not in side and ROOT.encode() not in side
    assert (side_path(dest).stat().st_mode & 0o777) == 0o600


def test_restore_marks_what_the_live_store_no_longer_has(individual, tmp_path):
    db, path = individual
    record = A.add(db, b"to lose\n", name="lose.txt", now=T0)
    ck = checkpoints.create(db, path, tmp_path / "ck", T0 + 1)
    for suffix in ("", "-wal", "-shm"):
        Path(str(side_path(path)) + suffix).unlink(missing_ok=True)
    result = checkpoints.restore(db, ck["id"], tmp_path / "r.sqlite", T0 + 2)
    assert result["artifacts_missing"] == 1 and result["artifact_bytes_copied"] == 0
    with closing(store.open_existing(tmp_path / "r.sqlite", graph_migration=False)) as copy:
        assert A.get(copy, record.id).state == "missing"


def test_a_restore_without_artifacts_changes_nothing_it_returned_before(individual, tmp_path):
    db, path = individual
    ck = checkpoints.create(db, path, tmp_path / "ck", T0)
    result = checkpoints.restore(db, ck["id"], tmp_path / "r.sqlite", T0 + 1)
    assert not [k for k in result if "artifact" in k]
    assert not side_path(tmp_path / "r.sqlite").exists()


def test_reconcile_sweeps_unnamed_bytes_after_the_grace_and_finds_missing_ones(individual):
    db, path = individual
    record = A.add(db, b"named bytes\n", name="n.txt", now=T0)
    with A.Sidecar(side_path(path)) as side:
        side.put(b"orphan bytes nobody names", "f" * 64, T0, cap=1 << 30, layers=[("artifact-" + "1" * 32, "text",
                                                                                   "orphan text")])
    young = A.reconcile(db, T0 + 10)
    assert young["bytes_swept"] == 0 and young["layers_swept"] == 0
    old = A.reconcile(db, T0 + A.SWEEP_GRACE + 10)
    assert old["bytes_swept"] == 1 and old["layers_swept"] == 1
    assert b"orphan bytes nobody names" not in raw(side_path(path))
    with A.Sidecar(side_path(path)) as side:
        side.drop(shas=[record.sha256])
    assert A.reconcile(db, T0 + 20)["marked_missing"] == 1
    assert A.get(db, record.id).state == "missing"
