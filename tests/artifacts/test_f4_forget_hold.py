"""Fix lane F4, item 1 (security review, second pass, M-new-1): a root forget HOLDS what its scan could not finish
instead of deleting it silently. A held artifact keeps its bytes but is never decoded, shown, exported, made from,
related or listed with its content; the forget's result lists it by id and label and says how to end the hold
(`nsf artifact forget ID` or `nsf artifact release ID`); a restore replays holds and releases. And the scan has its
own, larger budget, so that the reviewer's honest 47 MiB dataset zip and a notebook with nested compressed output are
checked to the end and neither held nor removed (`review1/s/r2/forget_over.py`, reproduced here)."""
from contextlib import closing
import base64
import gzip
import io
import json
import os
import random
import zipfile

import pytest

from neuroscifi.artifacts import cli as acli
from neuroscifi.artifacts import produce
from neuroscifi.artifacts import store as A
from neuroscifi.instance import checkpoints, governance, store
import test_artifact_store as base
from test_artifact_store import T0

registry, individual, form = base.registry, base.individual, base.form

ROOT = "Ottoline-zq7"


def _bomb_then_root() -> bytes:
    """8 MiB of zeros in about 8 KiB, then the root in its own gzip member: with a 1 MiB scan budget the name is
    never reached, so the scan cannot finish."""
    return gzip.compress(b"\0" * (8 << 20), mtime=0) + gzip.compress(b"nothing about anyone", mtime=0)


@pytest.fixture
def held_one(individual, monkeypatch):
    db, path = individual
    monkeypatch.setattr(A, "SCAN_BUDGET", 1 << 20)
    unchecked = A.add(db, _bomb_then_root(), name="unchecked.gz", now=T0)
    plain = A.add(db, b"a,b\n1,2\n3,4\n", name="plain.csv", now=T0)
    result = governance.forget_partner(db, ROOT, T0 + 1)
    return db, path, unchecked, plain, result


def test_what_the_scan_cannot_finish_is_held_listed_and_explained_not_removed(held_one):
    db, _, unchecked, plain, result = held_one
    assert {i for (i,) in db.execute("SELECT id FROM artifacts")} == {unchecked.id, plain.id}    # nothing removed
    assert result["artifacts_held"] == [{"id": unchecked.id, "label": "unchecked.gz"}]         # by id and label
    assert result["effects"]["artifacts_held"] == 1 and "artifacts_removed" not in result["effects"]
    hint = " ".join(result["next_steps"])
    assert "nsf artifact forget ID" in hint and "nsf artifact release ID" in hint
    # the log row says what happened, with counts only (no label, no root)
    rows = [json.loads(e) for (e,) in db.execute("SELECT effects FROM deletion_log WHERE target_kind=?",
                                                 (A.DELETION_KIND,))]
    assert rows[-1]["held"] == 1 and "unchecked" not in json.dumps(rows)
    assert A.get(db, unchecked.id).held and not A.get(db, plain.id).held


def test_a_held_artifact_is_never_read_shown_exported_or_made_from(held_one, tmp_path):
    db, _, unchecked, plain, _ = held_one
    listed = {i["id"]: i for i in A.listing(db)["items"]}
    assert listed[unchecked.id]["held"] == A.HELD_WORDS and listed[unchecked.id]["kind"] == "held"
    assert "held" not in listed[plain.id]
    shown = A.show(db, unchecked.id)
    assert shown["index"] == {} and shown["held"] == A.HELD_WORDS
    for read in (lambda: A.read_bytes(db, unchecked.id), lambda: A.facts(db, unchecked.id),
                 lambda: A.text_for_model(db, unchecked.id), lambda: A.export(db, unchecked.id, tmp_path / "out.gz"),
                 lambda: A.decoded_held(db, unchecked.id)):
        with pytest.raises(A.StoreRefused) as caught:
            read()
        assert caught.value.rule == "held"
    assert not (tmp_path / "out.gz").exists()
    assert A.spec_of(db, unchecked.id) is None
    plain_words = acli._plain("list", A.listing(db))
    assert f"{unchecked.id}  unchecked.gz" in plain_words and A.HELD_WORDS in plain_words
    assert "nsf artifact release" in acli._plain("show", A.show(db, unchecked.id))


def test_making_from_a_held_artifact_does_not_use_it(held_one):
    db, _, unchecked, _, _ = held_one
    result = produce.produce(db, "a table of it", [unchecked.id], now=T0 + 2)
    assert result.status != "made" and result.artifact is None
    used = [n for n in result.record["sources"] if n.get("id") == unchecked.id]
    assert used and used[0]["used"] is False and used[0]["why"] == A.HELD_WORDS


def test_release_and_forget_end_the_hold(held_one):
    db, _, unchecked, plain, _ = held_one
    assert A.release(db, plain.id, T0 + 2)["status"] == "not_held"
    released = A.release(db, unchecked.id, T0 + 3)
    assert released["status"] == "released" and not A.get(db, unchecked.id).held
    assert A.read_bytes(db, unchecked.id) == _bomb_then_root()
    assert db.execute("SELECT count(*) FROM deletion_log WHERE target_kind=?", (A.HOLD_KIND,)).fetchone()[0] == 2
    # held again by a later forget it cannot finish either; then the owner forgets it
    governance.forget_partner(db, "Another-zq8", T0 + 4)
    assert A.get(db, unchecked.id).held
    assert A.forget_artifact(db, unchecked.id, T0 + 5)["effects"]["artifacts_removed"] == 1
    assert not any(i.get("held") for i in A.listing(db)["items"])     # the forgotten one is gone, nothing held


def test_a_restore_replays_holds_and_releases(individual, monkeypatch, tmp_path):
    db, path = individual
    monkeypatch.setattr(A, "SCAN_BUDGET", 1 << 20)
    unchecked = A.add(db, _bomb_then_root(), name="unchecked.gz", now=T0)
    ck = checkpoints.create(db, path, tmp_path / "ck", T0 + 1)          # taken before the hold
    governance.forget_partner(db, ROOT, T0 + 2)
    result = checkpoints.restore(db, ck["id"], tmp_path / "held.sqlite", T0 + 3)
    assert result["artifact_holds_carried"] == 1
    with closing(store.open_existing(tmp_path / "held.sqlite", graph_migration=False)) as copy:
        assert A.get(copy, unchecked.id).held
        with pytest.raises(A.StoreRefused):
            A.read_bytes(copy, unchecked.id)
    ck2 = checkpoints.create(db, path, tmp_path / "ck2", T0 + 4)        # taken while held
    A.release(db, unchecked.id, T0 + 5)
    checkpoints.restore(db, ck2["id"], tmp_path / "released.sqlite", T0 + 6)
    with closing(store.open_existing(tmp_path / "released.sqlite", graph_migration=False)) as copy:
        assert not A.get(copy, unchecked.id).held


def test_the_console_shows_a_held_artifact_in_plain_words_only(held_one):
    from neuroscifi.console import artifacts as console
    db, _, unchecked, _, _ = held_one
    summary = console._summary(db, A.get(db, unchecked.id))
    assert summary["held"] == A.HELD_WORDS


# ---- the reviewer's reproduction: honest large files are checked to the end ----------------------------------------

def test_the_reviewers_dataset_zip_and_notebook_are_checked_to_the_end(individual):
    """`review1/s/r2/forget_over.py`: a 47 MiB zip of three sensor-log CSVs (about 230 MiB of text, ratio about 5)
    and a notebook whose output is five nested gzips in base64; a forget of a root named nowhere neither removes nor
    holds them (it removed both before fix lane F4). By default one of the three members is written (a 16 MiB zip of
    78 MiB of text: already past the old per-stream buffer and base64-run count, so it failed before the fix too);
    NSF_BENCH_OMNI=1 writes all three, the reviewer's file exactly (about 17 s)."""
    db, _ = individual
    random.seed(7)
    members = 3 if os.environ.get("NSF_BENCH_OMNI") == "1" else 1
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(members):
            z.writestr(f"part{i}.csv", "".join(f"{t},{t % 86400},{(t * 7919) % 1000 / 10},sensor-{t % 12}\n"
                                               for t in range(i * 3000000, (i + 1) * 3000000)))
    big = b.getvalue()
    assert len(big) > (members * 13) << 20
    inner = b"plain"
    for _ in range(5):
        inner = gzip.compress(inner, mtime=0)
    nb = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "code", "source": "x", "metadata": {}, "execution_count": 1, "outputs": [
            {"output_type": "display_data", "metadata": {},
             "data": {"application/gzip": base64.b64encode(inner).decode()}}]}]}).encode()
    ids = {name: A.add(db, data, name=name, now=T0).id for name, data in
           {"dataset.zip": big, "notebook.ipynb": nb, "plain.txt": b"just text\n"}.items()}
    del big
    result = governance.forget_partner(db, "Unrelated-qq1", T0 + 1)
    assert result["status"] == "absent" and "artifacts_held" not in result
    assert {i for (i,) in db.execute("SELECT id FROM artifacts")} == set(ids.values())
    assert not A.held_digests(db)
