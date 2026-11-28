"""Where Cognitive Genesis Wave 4 and OMNIMIND meet without a textual conflict (the merge of `main` into the OMNIMIND
line): one restore replays both sides' forgets together, and the restore's scan of the answers stays before its write
transaction with OMNIMIND's replays added inside it."""
from contextlib import closing

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.artifacts import store as A
from neuroscifi.instance import checkpoints, evidence, governance, imagined, store
from neuroscifi.webcortex import emitted

T0, DAY, N = adv.T0, adv.DAY, adv._SN
PARTNER = "Brunhild-zq4"
ANSWER = f"{N} 9.4.0 is the latest {N} release, which brings several fixes."


def _text(db):
    out = []
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in db.execute(f"SELECT * FROM {table}"):
            out.extend(str(v) for v in row if not isinstance(v, bytes))
    return "\n".join(out)


def test_one_restore_replays_own_text_web_imagined_and_artifact_forgets_together(tmp_path, monkeypatch):
    """Live, after the archive: the owner's `--forget-own-text` (Wave 4), a forgotten source (the answer marks Wave 4
    moved before the transaction), a forgotten artifact and a forgotten partner whose name sits in an imagined record
    and in a stored file (OMNIMIND lanes S and A). The restore of the older archive removes all of them from the copy,
    reports each, and makes no full scan of the answers inside its write transaction."""
    with adv._s_individual("merge-replay", attribution_independence=False, echo_recognition=False,
                           own_text_provenance=True) as (db, path):
        adv._s_look(db, T0 - 5 * DAY, [("relnotes.example", f"{N} 1.5.0 is the latest {N} release.",
                                        T0 - 6 * DAY, None)], answer=ANSWER)
        assert emitted.stored(db) > 0
        evidence.start_epoch(db, "merge", T0 - 5 * DAY)
        evidence.observe(db, {"id": "merge-w0", "epoch": "merge", "channel": "word", "action": 0, "bit": 1,
                              "root": PARTNER}, T0 - 5 * DAY + 1)
        naming = imagined.record(db, "speculation", payload={"note": f"talked with {PARTNER}"}, assumptions=[],
                                 uncertainty="untested", mechanism="test", mechanism_version="1", question="what if?",
                                 now=T0 - 5 * DAY + 2)
        other = imagined.record(db, "speculation", payload={"note": "a quiet day"}, assumptions=[],
                                uncertainty="untested", mechanism="test", mechanism_version="1", question="what if?",
                                now=T0 - 5 * DAY + 3)
        keep = A.add(db, b"kept for good\n", name="keep.txt", now=T0 - 5 * DAY)
        drop = A.add(db, b"DROPPED-BYTES " * 30, name="drop.bin", now=T0 - 5 * DAY)
        named = A.add(db, f"a note about {PARTNER}\n".encode(), name="named.txt", now=T0 - 5 * DAY)
        older = checkpoints.create(db, path, tmp_path / "ckpt", T0 - 4.5 * DAY)

        with store.write(db):
            live_own = emitted.remove_all(db, T0 - 4.4 * DAY)["own_text_removed"]
        governance.forget_source(db, "relnotes.example", T0 - 4.3 * DAY)
        A.forget_artifact(db, drop.id, T0 - 4.2 * DAY)
        governance.forget_partner(db, PARTNER, T0 - 4.1 * DAY)
        assert live_own > 0 and emitted.stored(db) == 0

        full_scans = []
        real_rows = governance._answer_rows

        def rows(conn, tokens, rowids=None):
            if rowids is None:
                full_scans.append(conn.in_transaction)
            return real_rows(conn, tokens, rowids)
        monkeypatch.setattr(governance, "_answer_rows", rows)
        dest = tmp_path / "restored.sqlite"
        result = checkpoints.restore(db, older["id"], dest, T0 - 4 * DAY)

        assert full_scans and True not in full_scans, full_scans
        assert result["own_text_removed"] == live_own
        assert result["web_removed"]["sources"] >= 1
        assert result["imagined_removed"] >= 1
        assert result["artifacts_removed"] == 2                         # the dropped file and the one naming him
        with closing(store.connect(dest)) as copy:
            assert emitted.stored(copy) == 0
            assert copy.execute("SELECT count(*) FROM deletion_log WHERE target_kind=?",
                                (emitted.DELETION_KIND,)).fetchone()[0] == 1
            assert copy.execute("SELECT count(*) FROM web_claims WHERE domain='relnotes.example'").fetchone()[0] == 0
            assert {i for (i,) in copy.execute("SELECT id FROM imagined")} == {other}
            assert {i for (i,) in copy.execute("SELECT id FROM artifacts")} == {keep.id}
            text = _text(copy)
            assert PARTNER not in text and naming not in text
            assert drop.id not in text and named.id not in text
