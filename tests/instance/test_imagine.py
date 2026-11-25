"""Imagination on the beliefs (ADR-052): the sandbox never reaches the live file, what-ifs are the belief model's own
exact posterior on an altered copy, the comparison of next readings and the retrospection agree with it, every
switch is the owner's, and the only write (--keep) goes to the imagined store, which forgetting covers."""
from contextlib import closing
import hashlib
import json
import random
import sqlite3

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import beliefs, evidence, executive, governance, imagine, imagined, store

T0 = 1_800_000_000.0


def _file_state(path, db):
    data = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        data += wal.read_bytes()
    return hashlib.sha256(data).hexdigest(), store.state_version(db), db.execute(
        "SELECT count(*) FROM evidence").fetchone()[0]


@pytest.fixture
def individual(tmp_path):
    """An epoch with three reference roots (mechanism pinned), a partner and a camera."""
    path = tmp_path / "x.sqlite"
    db = store.create(path, T0)
    rng = random.Random(5)
    evidence.start_epoch(db, "e", T0)
    n = 0

    def obs(channel, root, relation):
        nonlocal n
        n += 1
        action = rng.randint(0, 1)
        evidence.observe(db, {"id": f"r{n}", "epoch": "e", "channel": channel, "root": root, "action": action,
                              "bit": relation ^ action}, T0 + n)
    for r in range(3):
        for _ in range(4):
            obs("reference", f"ref-{r}", 0)
    for relation in (1, 1, 1, 0, 1):
        obs("word", "p", relation)
    for relation in (0, 1):
        obs("camera", "c", relation)
    with closing(db):
        yield db, path


def _copy_beliefs(db, change):
    """The truth by hand: the same exact inference on a plain sqlite copy changed by `change` (SQL)."""
    with closing(sqlite3.connect(":memory:")) as copy:
        db.backup(copy)
        change(copy)
        copy.commit()
        return beliefs.compute(copy, "e")


# ---- the sandbox -----------------------------------------------------------------------------------------------------

def test_a_write_in_the_sandbox_never_reaches_the_live_file(individual):
    db, path = individual
    before = _file_state(path, db)
    with imagine.sandbox(db) as box:
        assert store.database_path(box.db) is None and box.db is not db
        box.db.execute("DELETE FROM evidence")
        box.db.execute("UPDATE counters SET value=value+100 WHERE name='state_version'")
        assert box.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    assert _file_state(path, db) == before
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 19


def test_the_sandbox_is_bounded_by_size_and_by_time(individual):
    db, _ = individual
    with pytest.raises(imagine.Refused, match="above"):
        with imagine.sandbox(db, max_bytes=1024):
            pass
    with pytest.raises(imagine.Refused, match="ran out of time"):
        with imagine.sandbox(db, max_seconds=0.1) as box:
            box.db.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n) SELECT count(*) FROM n "
                           "WHERE i < 10000000000").fetchone()


# ---- what if ---------------------------------------------------------------------------------------------------------

def test_what_if_is_the_exact_posterior_of_the_altered_individual_and_writes_nothing(individual):
    db, path = individual
    before = _file_state(path, db)
    live = beliefs.compute(db, "e")
    out = imagine.what_if(db, "e", flip=["r13"], drop=["r17"], now=T0 + 100)
    truth = _copy_beliefs(db, lambda c: (c.execute("UPDATE evidence SET bit=1-bit WHERE id='r13'"),
                                         c.execute("DELETE FROM evidence WHERE id='r17'")))
    assert out["before"]["partner:p"]["p_inverted"] == live["partners"]["p"]["p_inverted"]
    assert out["after"]["partner:p"]["p_inverted"] == truth["partners"]["p"]["p_inverted"]
    assert out["after"]["mechanism"]["p_inverted"] == truth["mechanism"]["p_inverted"]
    assert out["label"] == imagined.NOT_EVIDENCE and out["kind"] == "simulation" and out["exact"]
    assert any("read 0 instead of 1" in a for a in out["assumptions"])
    assert _file_state(path, db) == before


def test_more_agreeing_readings_equal_observing_them(individual, tmp_path):
    db, path = individual
    out = imagine.what_if(db, "e", add=[{"factor": "partner:p", "reading": "agree", "count": 4}], now=T0 + 100)
    assert out["changes"][0]["reading"] == 1          # the partner's most probable next reading
    clone = store.create(tmp_path / "clone.sqlite", T0)
    with closing(clone):
        db.backup(clone)
        for k in range(4):
            evidence.observe(clone, {"id": f"more-{k}", "epoch": "e", "channel": "word", "root": "p", "action": 0,
                                     "bit": 1}, T0 + 200 + k)
        observed = beliefs.compute(clone, "e")
    assert out["after"]["partner:p"]["p_inverted"] == pytest.approx(observed["partners"]["p"]["p_inverted"],
                                                                     abs=1e-12)
    assert out["difference"]["partner:p"] > 0


def test_a_consolidated_reading_can_be_flipped_and_bad_questions_are_refused(individual):
    db, _ = individual
    governance.sweep(db, T0 + 30 * 86400)          # the journal's learning events become consolidated readings
    seq = db.execute("SELECT seq FROM evidence_consolidated WHERE root='p' ORDER BY seq LIMIT 1").fetchone()[0]
    out = imagine.what_if(db, "e", flip=[f"#{seq}"], detect=False)
    truth = _copy_beliefs(db, lambda c: c.execute("UPDATE evidence_consolidated SET value=1-value WHERE seq=?",
                                                  (seq,)))
    assert out["after"]["partner:p"]["p_inverted"] == truth["partners"]["p"]["p_inverted"]
    for kwargs, match in (({}, "at least one"), ({"flip": ["nope"]}, "No reading"),
                          ({"add": [{"factor": "elbow:x"}]}, "a factor is"),
                          ({"add": [{"factor": "partner:p", "count": 0}]}, "count")):
        with pytest.raises(Exception, match=match):
            imagine.what_if(db, "e", **kwargs)


# ---- comparing the next readings -------------------------------------------------------------------------------------

def test_question_values_are_exact_and_agree_with_what_if(individual):
    db, _ = individual
    out = imagine.question_values(db, "e", now=T0 + 100)
    by = {c["factor"]: c for c in out["candidates"]}
    assert set(by) == {"camera:c", "partner:p", "reference:ref-0", "reference:ref-1", "reference:ref-2"}
    for c in out["candidates"]:
        assert c["p_reading"][0] + c["p_reading"][1] == pytest.approx(1.0, abs=1e-12)
        assert all(v >= -1e-12 for v in c["information_bits"].values())
    camera = by["camera:c"]
    for value in (0, 1):
        direct = imagine.what_if(db, "e", add=[{"factor": "camera:c", "reading": value}], detect=False, now=T0 + 100)
        assert camera["after"][value]["camera:c"]["p_inverted"] == direct["after"]["camera:c"]["p_inverted"]
    # The camera has one reading each way: the most to learn there. Sorted by information, highest first.
    totals = [c["information_bits_total"] for c in out["candidates"]]
    assert totals == sorted(totals, reverse=True) and out["candidates"][0]["factor"] == "camera:c"


# ---- retrospection ---------------------------------------------------------------------------------------------------

def test_retrospection_is_exact_leave_one_out_and_groups_interchangeable_readings(individual):
    db, _ = individual
    out = imagine.retrospect(db, "e", "partner:p")
    assert out["tried"] == {"readings": 19, "owner_answers": 0}
    for found in out["ranked"]:
        without = imagine.what_if(db, "e", drop=[found["readings"][0]], detect=False)
        assert found["influence"] == pytest.approx(out["belief_now"] - without["after"].get(
            "partner:p", {"p_inverted": 0.5})["p_inverted"], abs=1e-11)
    partner = [f for f in out["ranked"] if f.get("factor") == "partner:p"]
    assert {f["reading"]: f["count"] for f in partner} == {1: 4, 0: 1}
    assert abs(out["most"]["influence"]) == max(abs(f["influence"]) for f in out["ranked"])


def test_retrospection_weighs_the_owners_answers(individual):
    db, _ = individual
    with store.write(db):
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('e','partner:p',16,NULL,?,1)", (T0 + 50,))
    out = imagine.retrospect(db, "e", "partner:p")
    answer = [f for f in out["ranked"] if f["what"] == "owner answer"]
    assert out["tried"]["owner_answers"] == 1 and answer and "confirmed change of partner:p" in answer[0]["answer"]
    assert answer[0]["influence"] != 0


# ---- the owner's switches, keeping an answer, forgetting it ----------------------------------------------------------

def test_each_switch_is_the_owners_and_a_damaged_switch_reads_as_off(individual):
    db, _ = individual
    for name, call in (("imagine_what_if", lambda: imagine.what_if(db, "e", flip=["r13"])),
                       ("imagine_what_if", lambda: imagine.question_values(db, "e")),
                       ("imagine_retrospect", lambda: imagine.retrospect(db, "e"))):
        store.set_config(db, {name: False}, T0)
        with pytest.raises(imagine.Refused, match=f"{name} is off"):
            call()
        store.set_config(db, {name: True}, T0)
    with store.write(db):
        raw = json.loads(store._meta(db, "config")) | {"imagine_what_if": "yes", "imagine_max_seconds": -1}
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    cfg = store.config(db)
    assert cfg.imagine_what_if is False and cfg.imagine_max_seconds == store.InstanceConfig.imagine_max_seconds
    assert any("imagine_what_if" in p for p in store.setting_problems(db))


def test_a_kept_answer_is_imagined_and_goes_when_its_reading_is_forgotten(individual):
    db, _ = individual
    out = imagine.what_if(db, "e", flip=["r13"], now=T0 + 100)
    record_id = imagine.keep(db, out, T0 + 100)
    shown = imagined.show(db, record_id)
    assert shown["kind"] == "simulation" and shown["label"] == imagined.NOT_EVIDENCE
    assert ["evidence", "r13"] in shown["derived_from"] and ["epoch", "e"] in shown["derived_from"]
    assert db.execute("SELECT count(*) FROM evidence WHERE id LIKE 'what-if:%'").fetchone()[0] == 0
    effects = governance.forget_evidence(db, "r13", T0 + 200)
    assert effects["effects"]["imagined_removed"] == 1
    assert imagined.listing(db, T0 + 300)["items"] == []


def test_the_decision_path_never_imports_imagination():
    """The executive, beliefs and evidence modules stay as they were: imagination reads them, never the reverse."""
    import inspect
    for module in (beliefs, evidence, executive):
        source = inspect.getsource(module)
        assert "imagine" not in source.replace("imagined", ""), module.__name__


def test_nsf_imagine_and_config(individual, capsys):
    db, path = individual
    db.close()
    args = ["--db", str(path)]
    assert main(["imagine", "what-if", "e", "--flip", "r13", "--add", "partner:p=agreex2", *args]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [c["change"] for c in out["changes"]] == ["flip", "add"] and out["label"] == imagined.NOT_EVIDENCE
    assert main(["imagine", "retrospect", "e", "partner:p", "--top", "2", *args]) == 0
    assert len(json.loads(capsys.readouterr().out)["ranked"]) == 2
    assert main(["config", "--no-imagine-retrospect", "--imagine-max-seconds", "3", *args]) == 0
    capsys.readouterr()
    assert main(["imagine", "retrospect", "e", *args]) == 2
    assert "imagine_retrospect is off" in capsys.readouterr().err
    assert main(["imagine", "what-if", "e", "--add", "partner:p=maybe", *args]) == 2
