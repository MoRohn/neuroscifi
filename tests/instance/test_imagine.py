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


def test_the_copy_is_refused_by_size_before_it_starts_and_by_time_as_soon_as_it_ends(individual):
    """review1b N5: the one-step copy cannot be interrupted, so imagine_max_seconds was said to bound what it cannot.
    What holds: a file above max_bytes is never copied, and a copy that alone took longer than max_seconds is refused
    as soon as it ends, before any work runs on it."""
    db, path = individual
    copies = []

    class Counting(sqlite3.Connection):
        def backup(self, *args, **kwargs):
            copies.append(kwargs.get("pages"))
            return super().backup(*args, **kwargs)
    with closing(sqlite3.connect(path, factory=Counting, isolation_level=None)) as counted:
        with pytest.raises(imagine.Refused, match="above"):
            with imagine.sandbox(counted, max_bytes=1024):
                pass
        assert copies == []                                              # refused before anything was copied
        ran = []
        with pytest.raises(imagine.Refused, match="ran out of time copying the file"):
            with imagine.sandbox(counted, max_seconds=0.0):
                ran.append(1)
        assert copies == [-1] and ran == []                              # one step; no work after it


def test_the_sandbox_copies_a_file_another_connection_keeps_writing(individual):
    """review1 Q2: the paged copy restarted at every commit of another connection; under a busy writer it ran out of
    time on every try. One step reads one snapshot."""
    import threading
    db, path = individual
    with store.write(db):
        db.execute("CREATE TABLE pad(x BLOB)")
        db.executemany("INSERT INTO pad VALUES(randomblob(4000))", [()] * 10000)     # ~40 MiB: several paged steps
    stop = threading.Event()

    def writer():
        with closing(store.connect(path)) as other:
            while not stop.is_set():
                other.execute("BEGIN IMMEDIATE")
                other.execute("INSERT INTO pad VALUES(randomblob(10))")
                other.execute("COMMIT")
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(3):
            with imagine.sandbox(db, max_seconds=1.0) as box:
                assert box.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 19
    finally:
        stop.set()
        thread.join()


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


def test_the_joint_information_of_a_reading_is_lane_is_and_agrees_with_this_copys_prediction(individual):
    """Wave 1 seam: lane I's exact information gain (information.gains, on the tallies) and this module's predictive
    probability (on a sandbox copy) are two computations of the same quantity for one reading of a camera or a
    partner: I(Y; hidden bits) = h(P(Y=1)) - h(reading error). question_values takes it from lane I's definition."""
    from neuroscifi.instance import information
    db, _ = individual
    out = imagine.question_values(db, "e", now=T0 + 100)
    lane_i = {c["subject"]: c["eig_bits"] for c in information.gains(db, "e", k=1)["candidates"]}
    checked = 0
    for c in out["candidates"]:
        if c["factor"].startswith("reference:"):
            assert c["information_bits_joint"] is None
            continue
        p1 = c["p_reading"][1]
        from_prediction = information._h(p1) - information._noise_entropy("camera", 1)
        assert c["information_bits_joint"] == lane_i[c["factor"]]
        assert c["information_bits_joint"] == pytest.approx(from_prediction, abs=2e-6)
        checked += 1
    assert checked == 2                     # camera:c and partner:p


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
    assert out["tried"]["owner_answers"] == 1 and answer and answer[0]["answer"] == "confirmed change"
    assert answer[0]["scope"] == "partner:p" and answer[0]["since_seq"] == 16
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


# ---- a kept answer names every source it shows (review1 CH1) -------------------------------------------------------

def _keep_each(db):
    """One kept answer of each kind; each names partner p, camera c and some readings of the fixture."""
    with store.write(db):
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('e','partner:p',16,NULL,?,1)", (T0 + 50,))
    return {"what_if": imagine.keep(db, imagine.what_if(db, "e", flip=["r1"], add=[{"factor": "partner:p",
                                                                                     "count": 3}], now=T0 + 100),
                                    T0 + 100),
            "question_values": imagine.keep(db, imagine.question_values(db, "e", now=T0 + 100), T0 + 100),
            "retrospect": imagine.keep(db, imagine.retrospect(db, "e", "partner:p"), T0 + 100)}


FORGETS = {"partner": lambda db, now: governance.forget_partner(db, "p", now),
           "camera": lambda db, now: governance.forget_camera(db, "c", now),
           "evidence": lambda db, now: [governance.forget_evidence(db, i, now) for i in ("r18", "r19")],
           "epoch": lambda db, now: governance.forget_epoch(db, "e", now)}


def _held(path, rid):
    with closing(sqlite3.connect(path)) as copy:
        return copy.execute("SELECT 1 FROM imagined WHERE id=?", (rid,)).fetchone() is not None


def test_a_kept_answer_records_every_root_and_reading_it_names(individual):
    db, _ = individual
    kept = _keep_each(db)
    refs = {name: {tuple(r) for r in imagined.show(db, rid)["derived_from"]} for name, rid in kept.items()}
    for name in kept:
        assert {("epoch", "e"), ("partner", "p"), ("camera", "c")} <= refs[name], name
    assert ("evidence", "r1") in refs["what_if"]
    ranked = imagined.show(db, kept["retrospect"])["payload"]["ranked"]
    named = {r for f in ranked for r in f.get("readings", ())}
    assert named and {("evidence", r) for r in named} <= refs["retrospect"]
    answer = [f for f in ranked if f["what"] == "owner answer"]       # structured: no sentence repeats the root
    assert answer and answer[0]["scope"] == "partner:p" and "p" not in answer[0]["answer"].split()


@pytest.mark.parametrize("forget", sorted(FORGETS))
def test_forgetting_any_named_source_removes_every_kind_of_kept_answer(individual, forget):
    db, _ = individual
    kept = _keep_each(db)
    FORGETS[forget](db, T0 + 200)
    for name, rid in kept.items():
        assert db.execute("SELECT 1 FROM imagined WHERE id=?", (rid,)).fetchone() is None, name


@pytest.mark.parametrize("forget", sorted(FORGETS))
def test_a_restore_after_the_forget_does_not_bring_a_kept_answer_back(individual, tmp_path, forget):
    from neuroscifi.instance import checkpoints
    db, path = individual
    kept = _keep_each(db)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 150)
    FORGETS[forget](db, T0 + 200)
    checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 300)
    for name, rid in kept.items():
        assert not _held(tmp_path / "restored.sqlite", rid), name


def test_keeping_refuses_more_sources_than_a_record_may_name_and_never_truncates(tmp_path):
    db = store.create(tmp_path / "y.sqlite", T0)
    evidence.start_epoch(db, "e", T0)
    for i in range(80):
        evidence.observe(db, {"id": f"w{i}", "epoch": "e", "channel": "word", "root": "bob", "action": 0, "bit": 1},
                         T0 + i)
    answer = imagine.what_if(db, "e", flip=[f"w{i}" for i in range(80)], now=T0 + 100)
    with pytest.raises(imagine.Refused, match=f"at most {imagined.MAX_REFS}"):
        imagine.keep(db, answer, T0 + 100)
    assert db.execute("SELECT count(*) FROM imagined").fetchone()[0] == 0
    rid = imagine.keep(db, imagine.what_if(db, "e", flip=["w79"], now=T0 + 100), T0 + 100)
    governance.forget_evidence(db, "w79", T0 + 200)
    assert db.execute("SELECT 1 FROM imagined WHERE id=?", (rid,)).fetchone() is None
    db.close()


def test_an_answer_about_a_root_with_no_reading_is_shown_but_not_kept(individual):
    db, _ = individual
    answer = imagine.what_if(db, "e", add=[{"factor": "partner:newcomer", "reading": 1, "count": 2}], now=T0 + 100)
    assert "partner:newcomer" in answer["after"]
    with pytest.raises(imagine.Refused, match="holds nothing of"):
        imagine.keep(db, answer, T0 + 100)


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
