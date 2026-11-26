"""Wave 4, second fix round: what the reviews' second passes found (review4 security and measurement, second pass).
Each case fails on the tree before its fix (71e5672) and passes with it."""
from contextlib import contextmanager
import json

from neuroscifi.acceptance.cognition import adversarial as adv
from neuroscifi.webcortex import emitted

T0, DAY, N = adv.T0, adv.DAY, adv._SN
ANSWER = f"{N} 9.4.0 is the latest {N} release, which brings several fixes."
ECHOES = [f"By now {N} 9.4.0 is the latest {N} release.", f"Once again {N} 9.4.0 is the latest {N} release."]


@contextmanager
def _own(name, **settings):
    with adv._s_individual(name, **({"attribution_independence": False, "echo_recognition": False,
                                     "own_text_provenance": True} | settings)) as (db, path):
        yield db, path


def _pages(texts, published):
    return [(f"site{i}.example", t, published, None) for i, t in enumerate(texts)]


def _rows(db, value="9.4.0"):
    return [r["status"] for r in adv._s_rows(db, value) if N in r["statement"]]


def _emissions(db):
    return [json.loads(s)["e"] for (s,) in db.execute("SELECT state FROM web_strategies WHERE name LIKE 'emitted:%'")]


# ---- item 1 (security, second pass, 2b(ii)): an expired emission never masks a fresh one ----------------------------

def test_a_fresh_emission_counts_though_the_pairs_first_emission_has_expired():
    """review4 s/r2/h3_expiry: emitted on day 0 and again on day 40, echoed on day 42: an echo."""
    with _own("expiry-fresh") as (db, _):
        adv._s_look(db, T0, [], answer=ANSWER)
        adv._s_look(db, T0 + 40 * DAY, [], answer=ANSWER)
        out = adv._s_look(db, T0 + 42 * DAY, _pages(ECHOES, T0 + 42 * DAY - 3600))
        assert len(out.get("echoes") or []) == 2 and _rows(db) == []


def test_expired_emissions_are_dropped_before_the_list_is_cut_to_its_maximum():
    """Three emissions (the most kept) on days 0 to 2, a fourth on day 40: the fourth is kept and counts on day 42;
    the expired ones are gone, and so are their tasks' lists."""
    with _own("expiry-full") as (db, _):
        for day in range(emitted.MAX_EMISSIONS):
            adv._s_look(db, T0 + day * DAY, [], answer=ANSWER)
        adv._s_look(db, T0 + 40 * DAY, [], answer=ANSWER)
        assert [[e[0] for e in row] for row in _emissions(db)] == [[T0 + 40 * DAY]]
        tasks = db.execute("SELECT count(*) FROM web_strategies WHERE name LIKE 'emitted-task:%'").fetchone()[0]
        assert tasks == 1
        out = adv._s_look(db, T0 + 42 * DAY, _pages(ECHOES, T0 + 42 * DAY - 3600))
        assert len(out.get("echoes") or []) == 2 and _rows(db) == []



# ---- item 2 (security, second pass, 2b(iii)): `--forget-own-text` is recorded and replayed on restore ---------------

def test_forget_own_text_survives_a_restore_of_an_archive_taken_before_it(tmp_path, capsys):
    """review4 s/r2/l3_forget_restore: before the fix the removal left no record, every checkpoint stayed valid, and a
    restore of an older archive brought the fingerprints back. Now it is a deletion_log row (kind own_text) that a
    restore replays, and the archives taken before it are stale (then rewritten without the rows)."""
    from contextlib import closing
    from neuroscifi.cli import main
    from neuroscifi.instance import checkpoints, store
    with _own("forget-restore") as (db, path):
        adv._s_look(db, T0 - 5 * DAY, [], answer=ANSWER)
        older = checkpoints.create(db, path, tmp_path / "ckpt", T0 - 4.5 * DAY)
        db.commit()
        main(["web", "config", "--db", str(path), "--forget-own-text"])
        out = json.loads(capsys.readouterr().out)
        assert out["own_text_removed"] == 2 and emitted.stored(db) == 0
        logged = db.execute("SELECT target_id, effects FROM deletion_log WHERE target_kind=?",
                            (emitted.DELETION_KIND,)).fetchall()
        assert len(logged) == 1 and json.loads(logged[0][1])["own_text_removed"] == 2
        assert older["id"] in json.loads(logged[0][1])["checkpoints_stale"]
        assert emitted.DELETION_KIND in checkpoints.REWRITABLE_KINDS
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, older["id"], copy, T0 - 3 * DAY)
        with closing(store.connect(copy)) as restored:
            assert emitted.stored(restored) == 0
            assert restored.execute("SELECT count(*) FROM web_strategies WHERE name LIKE 'emitted%'").fetchone()[0] == 0
            # the copy remembers the removal: an older archive restored FROM it loses the rows too
            assert restored.execute("SELECT count(*) FROM deletion_log WHERE target_kind=?",
                                    (emitted.DELETION_KIND,)).fetchone()[0] == 1
        # what the individual emitted AFTER the removal is kept by a restore of an archive taken after it
        adv._s_look(db, T0 - 2 * DAY, [], answer=ANSWER)
        newer = checkpoints.create(db, path, tmp_path / "ckpt", T0 - 1.5 * DAY)
        copy2 = tmp_path / "restored2.sqlite"
        checkpoints.restore(db, newer["id"], copy2, T0 - DAY)
        with closing(store.connect(copy2)) as restored:
            assert emitted.stored(restored) == 1
