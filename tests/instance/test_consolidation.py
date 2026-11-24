"""Consolidation of expiring evidence (ADR-044, schema 14): what an expired learning event taught stays.

The audit reproduced C3: beliefs are computed from evidence with a 7-day lifetime, so on day 8 the posterior was 0.5
again and the executive said "observe". With consolidation (the default) governance.sweep keeps, per expired learning
event, its seq, epoch, channel, root, corrected reading and sample id, and every belief reads those rows with the raw
events. These tests hold it to: the same posteriors before and after expiry, exactly; segments, samples and links
unchanged; every forget still exact; restores that neither lose the learning nor bring back what was forgotten; and
the old behaviour, unchanged, under `consolidation=False`.
"""
from contextlib import closing
import json
import random
import sqlite3

import pytest

from neuroscifi.instance import beliefs, checkpoints, evidence, executive, governance, memory, schema, store
from neuroscifi.instance.errors import Gone

from conftest import T0

DAY = 86400.0
# Everything compute() says about what is believed. Left out: state_version, the evidence counts (a sweep removes
# memory-only events and duplicate copies) and the prototype's legacy status (compared to rounding where it exists).
BELIEF = ("mechanism", "sensors", "partners", "reference_roots", "unidentifiable", "segments", "confirmed_changes",
          "linked_from", "change")


@pytest.fixture(autouse=True, params=["copied_samples_looked_up", "copied_samples_in_one_pass"])
def copy_rule_path(request, monkeypatch):
    """Every test of this file runs twice. The readings that do not count (later copies of a sample) are found one
    sample at a time while few samples have several readings, and in one grouped pass over the index beyond
    beliefs._FEW_COPIED_SAMPLES; the second run forces the grouped pass, which small test data would never reach."""
    if request.param == "copied_samples_in_one_pass":
        monkeypatch.setattr(beliefs, "_FEW_COPIED_SAMPLES", -1)


def obs(db, event_id, epoch, channel, root, corrected, now, *, action=1, sample=None, consent="learn"):
    record = {"id": event_id, "epoch": epoch, "channel": channel, "action": action, "bit": corrected ^ action,
              "root": root, "consent": consent}
    if sample is not None:
        record["sample_id"] = sample
    return evidence.observe(db, record, now)["seq"]


def held(db, epoch):
    result = beliefs.compute(db, epoch)
    return {key: result[key] for key in BELIEF}


def uncounted(db, epoch):
    """An oracle in plain Python for the copy rule: the epoch's consolidated readings that are NOT the earliest copy
    of their sample among the journal's learning copies and the consolidated readings, as (channel, root, value,
    seq), sorted. The code under test never builds this list; it counts in SQL."""
    first = {}
    for channel, root, sample, seq in db.execute(
            "SELECT channel, root, sample_id, seq FROM evidence WHERE epoch=? AND consent='learn' "
            "AND sample_id IS NOT NULL UNION ALL SELECT channel, root, sample_id, seq FROM evidence_consolidated "
            "WHERE epoch=? AND sample_id IS NOT NULL", (epoch, epoch)):
        first[(channel, root, sample)] = min(seq, first.get((channel, root, sample), seq))
    return sorted((channel, root, value, seq) for channel, root, value, sample, seq in db.execute(
        "SELECT channel, root, value, sample_id, seq FROM evidence_consolidated WHERE epoch=? "
        "AND sample_id IS NOT NULL", (epoch,)) if first[(channel, root, sample)] != seq)


def folded(db, where="1", params=()):
    return db.execute(f"SELECT seq, epoch, channel, root, value, sample_id FROM evidence_consolidated WHERE {where} "
                      "ORDER BY seq", params).fetchall()


def count(db, table, where="1", params=()):
    return db.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


def teach(db, epoch="desk", now=T0, rounds=6, prefix=None):
    """A resolved calibration: three reference roots, one camera, one partner. Returns the event ids."""
    prefix = prefix or epoch
    evidence.start_epoch(db, epoch, now)
    ids = []
    for k in range(rounds):
        for channel, root, corrected in (("reference", f"ref-{k % 3}", 0), ("camera", "cam-a", 1), ("word", "ana", 0)):
            ids.append(f"{prefix}-{len(ids)}")
            obs(db, ids[-1], epoch, channel, root, corrected, now + len(ids))
    return ids


# ---- the reproduced defect ---------------------------------------------------------------------------------------

def test_day_eight_the_individual_still_knows_what_it_learned(instance):
    db, _ = instance
    teach(db)
    before = beliefs.compute(db, "desk")
    decision = executive.decide(db, "desk", T0 + 100)
    assert decision["decision"] == "act" and before["mechanism"]["identifiable"]
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["expired"] == 18 and out["consolidated"] == 18 and out["epochs"] == ["desk"]
    after = beliefs.compute(db, "desk")
    assert {k: after[k] for k in BELIEF} == {k: before[k] for k in BELIEF}       # the same numbers, not close ones
    assert after["evidence"] == {"total": 18, "learning": 18, "memory_only": 0, "duplicates_ignored": 0,
                                 "consolidated": 18}
    assert count(db, "evidence") == 0 and count(db, "evidence_consolidated") == 18
    again = executive.decide(db, "desk", T0 + 8 * DAY + 1)
    assert again["decision"] == "act" and again["reason"] == decision["reason"]
    # the prototype's status reads the same readings; without event ids to order by, its sums agree to rounding
    for name, p in before["legacy"]["probability_inverted"].items():
        assert after["legacy"]["probability_inverted"][name] == pytest.approx(p, abs=1e-12)
    assert after["legacy"]["stored_observations"] == 18
    # the journal shows no episode any more, and says what stands in their place
    assert evidence.list_evidence(db, "desk") == []
    assert store.inventory(db)["memory"]["consolidated_readings"] == 18
    explained = beliefs.explain(db, "desk")
    camera = next(f for f in explained["factors"] if f["scope"] == "camera:cam-a")
    assert camera["supporting"] == [] and camera["supporting_total"] == 6 and camera["supporting_consolidated"] == 6


def test_with_consolidation_off_expiry_is_what_it_was(tmp_path):
    """The rollback: `consolidation=False` gives the sweep of schema 13. Day 8: nothing learned is left."""
    db = store.create(tmp_path / "off.sqlite", T0, config=store.InstanceConfig(consolidation=False))
    try:
        teach(db)
        out = governance.sweep(db, T0 + 8 * DAY)
        assert out["expired"] == 18 and "consolidated" not in out
        after = beliefs.compute(db, "desk")
        assert after["mechanism"]["p_inverted"] == 0.5 and after["sensors"] == {} and after["partners"] == {}
        assert after["evidence"] == {"total": 0, "learning": 0, "memory_only": 0, "duplicates_ignored": 0}
        assert executive.decide(db, "desk", T0 + 8 * DAY + 1)["decision"] == "observe"
        assert count(db, "evidence_consolidated") == 0 and count(db, "tombstones", "reason='expired'") == 18
        effects = json.loads(db.execute("SELECT effects FROM deletion_log ORDER BY id DESC LIMIT 1").fetchone()[0])
        assert "consolidated" not in effects
    finally:
        db.close()


def test_turning_consolidation_off_later_stops_folding_and_keeps_what_was_kept(instance):
    db, _ = instance
    teach(db)
    governance.sweep(db, T0 + 8 * DAY)
    kept = held(db, "desk")
    assert store.set_config(db, {"consolidation": False})["changed"] == ["consolidation"]
    obs(db, "late", "desk", "camera", "cam-a", 1, T0 + 9 * DAY)
    assert beliefs.compute(db, "desk")["sensors"]["cam-a"]["n"] == 7
    out = governance.sweep(db, T0 + 20 * DAY)
    assert out["expired"] == 1 and "consolidated" not in out
    assert held(db, "desk") == kept and count(db, "evidence_consolidated") == 18
    with pytest.raises(Exception):
        store.set_config(db, {"consolidation": "yes"})


# ---- exactness -----------------------------------------------------------------------------------------------------

def _random_life(db, seed):
    """Two epochs with everything the model distinguishes: three channels, several roots, samples with copies,
    memory-only events, promoted events, a confirmed change on a camera, a partner and the mechanism, and a second
    epoch that continues segments of the first. Events are spread over ten days, so they expire a few at a time."""
    rng = random.Random(seed)
    evidence.start_epoch(db, "a", T0)
    evidence.start_epoch(db, "b", T0)
    roots = {"camera": ["c1", "c2"], "word": ["p1", "p2"], "reference": ["r1", "r2", "r3"]}
    truth = {(channel, root): rng.randrange(2) for channel, names in roots.items() for root in names}
    seqs = {"a": [], "b": []}
    for i in range(150):
        epoch = "b" if i >= 120 else "a"
        channel = rng.choice(["camera", "camera", "word", "word", "reference"])
        root = rng.choice(roots[channel])
        late = epoch == "a" and i >= 60
        corrected = truth[(channel, root)] ^ (1 if late and root in ("c1", "p1") else 0) ^ (rng.random() < .12)
        sample = f"s{rng.randrange(6)}" if rng.random() < .35 else None
        consent = "memory_only" if rng.random() < .1 else "learn"
        seq = obs(db, f"e{i}", epoch, channel, root, corrected, T0 + i * 1.6 * 3600, action=rng.randrange(2),
                  sample=sample, consent=consent)
        seqs[epoch].append(seq)
        if rng.random() < .1:
            governance.promote(db, f"e{i}", T0 + i * 1.6 * 3600)
    marks = {"camera:c1": seqs["a"][rng.randrange(30, 70)], "partner:p1": seqs["a"][rng.randrange(30, 70)],
             "mechanism": seqs["a"][rng.randrange(20, 100)]}
    with store.write(db):
        for scope, since in marks.items():
            db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                       "VALUES('a',?,?,NULL,?,1)", (scope, since, T0))
        segments = {"mechanism": f"mechanism@{rng.choice([0, marks['mechanism']])}",
                    "camera:c1": f"c1@{rng.choice([0, marks['camera:c1']])}", "camera:c2": "c2@0",
                    "partner:p1": f"p1@{rng.choice([0, marks['partner:p1']])}"}
        db.execute("INSERT INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) VALUES('b','a',?,NULL,?)",
                   (json.dumps(segments, sort_keys=True),  T0))
        store.bump_state_version(db)


def _everything_believed(db):
    out = {epoch: held(db, epoch) for epoch in ("a", "b")}
    out["learning"] = {epoch: beliefs.compute(db, epoch)["evidence"]["learning"] for epoch in ("a", "b")}
    match = beliefs.match_epochs(db, "b")
    out["match"] = {key: match[key] for key in ("events", "ranking", "best", "new_setup_posterior",
                                                "new_setup_log_likelihood")}
    out["decisions"] = {epoch: {k: v for k, v in executive.decide(db, epoch, T0).items()
                                if k in ("decision", "scope", "reason")} for epoch in ("a", "b")}
    out["totals"] = {epoch: [(f["scope"], f["p_inverted"], f["supporting_total"], f["counterevidence_total"])
                             for f in beliefs.explain(db, epoch)["factors"]] for epoch in ("a", "b")}
    return out


@pytest.mark.parametrize("seed", range(8))
def test_posteriors_are_the_same_numbers_before_and_after_expiry(instance, seed):
    db, _ = instance
    _random_life(db, seed)
    reference = _everything_believed(db)
    assert reference["a"]["segments"] and reference["b"]["linked_from"]["learning"] > 0
    learning = sum(reference["learning"].values())
    for days in (7.2, 8.5, 11, 14, 400):
        governance.sweep(db, T0 + days * DAY)
        assert _everything_believed(db) == reference, f"beliefs moved at the sweep of day {days}"
    # at the end only promoted events are still episodes; every other learning event is a consolidated reading
    assert count(db, "evidence", "retention!='promoted'") == 0
    # (a reading that does not count is a later copy of a sample: kept, so that it can count if the first is forgotten)
    promoted_learning = count(db, "evidence", beliefs.learning_sql(db))
    later_copies = len(uncounted(db, "a")) + len(uncounted(db, "b"))
    assert count(db, "evidence_consolidated") - later_copies + promoted_learning == learning
    assert count(db, "evidence_consolidated") > 60 and later_copies > 0
    # every reading has the tombstone of its event (the id-to-seq map a forget by id needs)
    assert count(db, "evidence_consolidated") == count(
        db, "tombstones", "reason='expired' AND seq IN (SELECT seq FROM evidence_consolidated)")
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("seed", range(4))
def test_the_readings_that_do_not_count_are_counted_as_a_plain_python_oracle_lists_them(instance, seed):
    """Second-pass review S1: the count is made in SQL passes (no list of readings is built), on either path. Here
    against the oracle, per root and corrected reading, under two predicates on the seq, while some copies are
    still in the journal and others are consolidated."""
    db, _ = instance
    _random_life(db, seed)
    for days in (7.2, 8.5, 11, 400):
        governance.sweep(db, T0 + days * DAY)
        for epoch in ("a", "b"):
            listed = uncounted(db, epoch)
            middle = db.execute("SELECT coalesce(max(seq), 0) / 2 FROM evidence_consolidated").fetchone()[0]
            expected = {}
            for channel, root, value, seq in listed:
                entry = expected.setdefault((channel, root), [[0, 0], [0, 0]])
                entry[0][value] += 1
                entry[1][value] += seq >= middle
            counted = beliefs._not_counted(db, epoch, [("1", []), ("{s}>=?", [middle])], beliefs._preceded(db, epoch))
            assert {k: v for k, v in counted.items() if v != [[0, 0], [0, 0]]} == expected, (days, epoch)
            tallies, lost = beliefs._consolidated_tallies(db, epoch, [middle])
            assert lost == len(listed)
            assert sum(row[5] for row in tallies) == count(db, "evidence_consolidated", "epoch=?", (epoch,)) - lost
    assert len(uncounted(db, "a")) > 3


def test_a_change_confirmed_after_the_events_expired_cuts_the_consolidated_readings_at_the_same_place(tmp_path):
    """The reason consolidated readings keep their seq. A camera flips; the question about it is answered only after
    the events expired. The changepoint then falls among consolidated readings, and the segments must be exactly
    those of an individual whose events never expired."""
    paths = {"folded": tmp_path / "folded.sqlite", "raw": tmp_path / "raw.sqlite"}
    results = {}
    for name, path in paths.items():
        config = store.InstanceConfig() if name == "folded" else store.InstanceConfig(evidence_ttl_seconds=10**9)
        db = store.create(path, T0, config=config)
        try:
            evidence.start_epoch(db, "desk", T0)
            n = 0
            for k in range(12):
                for channel, root, corrected in (("reference", f"ref-{k % 3}", 0), ("camera", "cam-a", 0),
                                                 ("word", "ana", 0)):
                    n += 1
                    obs(db, f"x{n}", "desk", channel, root, corrected, T0 + n)
            for k in range(8):                                   # the camera now reads the other way
                for channel, root, corrected in (("camera", "cam-a", 1), ("word", "ana", 0)):
                    n += 1
                    obs(db, f"x{n}", "desk", channel, root, corrected, T0 + n)
            asked = executive.decide(db, "desk", T0 + 100, record=True)
            assert asked["decision"] == "ask" and asked["scope"] == "camera:cam-a"
            swept = governance.sweep(db, T0 + 7 * DAY + 60)       # within the question's own 7 days
            if name == "folded":
                assert swept["consolidated"] == n and count(db, "evidence") == 0
                suspicion = beliefs.detect_change(db, "desk")      # still seen, now among consolidated readings
                assert suspicion["suspected"] and suspicion["scope"] == "camera:cam-a"
            done = executive.resolve(db, asked["pending_id"], "yes, I turned it", T0 + 7 * DAY + 61, confirm=True)
            since = done["changepoint"]["since_seq"]
            assert 36 < since <= n
            results[name] = (since, held(db, "desk"), beliefs.compute(db, "desk")["evidence"]["learning"])
        finally:
            db.close()
    assert results["folded"] == results["raw"]
    segments = results["folded"][1]["segments"]["camera:cam-a"]
    assert [s["n"] for s in segments] == [12 + (results["folded"][0] - 37) // 2, 8 - (results["folded"][0] - 37) // 2]


def test_a_sample_counts_once_however_its_copies_expire(instance):
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    obs(db, "first", "desk", "camera", "cam-a", 1, T0, sample="frame-1")
    obs(db, "second", "desk", "camera", "cam-a", 1, T0 + 1, sample="frame-1")           # a copy, expiring with it
    obs(db, "quiet", "desk", "camera", "cam-a", 1, T0 + 2, sample="frame-2", consent="memory_only")
    obs(db, "loud", "desk", "camera", "cam-a", 1, T0 + 3, sample="frame-2")             # the first LEARNING copy
    before = beliefs.compute(db, "desk")
    assert before["evidence"] == {"total": 4, "learning": 2, "memory_only": 1, "duplicates_ignored": 1}
    out = governance.sweep(db, T0 + 8 * DAY)
    # every copy with consent 'learn' leaves its own reading; the memory-only one leaves nothing
    assert out["expired"] == 4 and out["consolidated"] == 3
    assert [(r[0], r[3], r[4], r[5]) for r in folded(db)] == [
        (1, "cam-a", 1, "frame-1"), (2, "cam-a", 1, "frame-1"), (4, "cam-a", 1, "frame-2")]
    after = beliefs.compute(db, "desk")
    assert after["sensors"] == before["sensors"]                    # the sample still counts once
    assert after["evidence"] == {"total": 3, "learning": 2, "memory_only": 0, "duplicates_ignored": 1,
                                 "consolidated": 2}
    assert uncounted(db, "desk") == [("camera", "cam-a", 1, 2)]
    # a copy of a consolidated sample arriving later is a duplicate, not a second reading
    obs(db, "third", "desk", "camera", "cam-a", 1, T0 + 9 * DAY, sample="frame-1")
    obs(db, "other-root", "desk", "camera", "cam-b", 1, T0 + 9 * DAY, sample="frame-1")  # another root: its own
    now = beliefs.compute(db, "desk")
    assert now["sensors"]["cam-a"] == before["sensors"]["cam-a"] and now["sensors"]["cam-b"]["n"] == 1
    assert now["evidence"] == {"total": 5, "learning": 3, "memory_only": 0, "duplicates_ignored": 2,
                               "consolidated": 2}
    assert executive._new_events(db, "desk", "camera:cam-a", 0) == 2
    again = governance.sweep(db, T0 + 20 * DAY)
    assert again["expired"] == 2 and again["consolidated"] == 2
    assert count(db, "evidence_consolidated") == 5
    assert beliefs.compute(db, "desk")["sensors"] == now["sensors"]
    assert sorted(uncounted(db, "desk")) == [("camera", "cam-a", 1, 2), ("camera", "cam-a", 1, 5)]


def test_a_promoted_first_copy_counts_and_the_expired_later_copy_waits_behind_it(instance):
    """The first copy of a sample is still an episode (promoted), a later copy expired. The journal's copy counts,
    where it stands; the reading of the later copy is kept and counts once the first one is forgotten."""
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    obs(db, "kept", "desk", "word", "ana", 0, T0, sample="w1")
    governance.promote(db, "kept", T0 + 1)
    obs(db, "copy", "desk", "word", "ana", 1, T0 + 2, sample="w1")            # says the opposite, as a copy may
    before = beliefs.compute(db, "desk")["partners"]
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["expired"] == 1 and out["consolidated"] == 1 and count(db, "evidence_consolidated") == 1
    after = beliefs.compute(db, "desk")
    assert after["partners"] == before and after["partners"]["ana"]["n"] == 1
    assert after["evidence"] == {"total": 2, "learning": 1, "memory_only": 0, "duplicates_ignored": 1}
    assert uncounted(db, "desk") == [("word", "ana", 1, 2)]
    readings = lambda: [(e["seq"], e["bit"] ^ e["action"]) for e in beliefs._window(db, "desk", 5)]  # noqa: E731
    assert readings() == [(1, 0)]
    governance.forget_evidence(db, "kept", T0 + 8 * DAY + 1)
    now = beliefs.compute(db, "desk")
    assert now["partners"]["ana"]["n"] == 1 and readings() == [(2, 1)]         # the later copy, where it stands
    assert now["evidence"] == {"total": 1, "learning": 1, "memory_only": 0, "duplicates_ignored": 0,
                               "consolidated": 1}


def _one_partner_with_a_copied_sample(db):
    evidence.start_epoch(db, "desk", T0)
    obs(db, "a", "desk", "word", "ana", 0, T0, sample="s1")
    obs(db, "b", "desk", "word", "ana", 1, T0 + 1, sample="s1")               # a later copy of the same sample
    for k in range(3):
        obs(db, f"plain-{k}", "desk", "word", "ana", 0, T0 + 2 + k)


def test_forgetting_the_first_copy_of_a_sample_lets_the_next_one_count_whatever_expired_first(tmp_path):
    """Review F1: sweep-then-forget lost the later copy (n = 3) where forget-then-sweep, or no expiry at all, kept it
    (n = 4). Since every command sweeps first, the wrong order was the normal one. An individual that never had the
    first copy still has the second: that is what "forgotten" promises."""
    beliefs_of, readings = {}, {}
    for order in ("forget_then_sweep", "sweep_then_forget", "no_expiry"):
        ttl = 10**9 if order == "no_expiry" else 7 * 86400
        db = store.create(tmp_path / f"{order}.sqlite", T0, config=store.InstanceConfig(evidence_ttl_seconds=ttl))
        try:
            _one_partner_with_a_copied_sample(db)
            if order == "sweep_then_forget":
                assert governance.sweep(db, T0 + 8 * DAY)["consolidated"] == 5
            out = governance.forget_evidence(db, "a", T0 + 8 * DAY + 1)
            assert out["status"] == "forgotten"
            governance.sweep(db, T0 + 8 * DAY + 2)
            beliefs_of[order] = held(db, "desk")
            readings[order] = [(r[0], r[4], r[5]) for r in folded(db)]
        finally:
            db.close()
    assert beliefs_of["sweep_then_forget"]["partners"]["ana"]["n"] == 4
    assert beliefs_of["sweep_then_forget"] == beliefs_of["forget_then_sweep"] == beliefs_of["no_expiry"]
    assert readings["sweep_then_forget"] == readings["forget_then_sweep"] == [(2, 1, "s1"), (3, 0, None), (4, 0, None),
                                                                              (5, 0, None)]


def test_a_later_copy_that_opens_a_segment_does_not_lend_it_its_place(tmp_path):
    """Factors are ordered by their first reading, and the order is the order of the sums. A later copy of a sample
    that is the first reading of a segment does not count, so the segment's first seq is the next reading's: the
    tallies, first seqs included, are those of a journal where nothing expired."""
    tallies, believed = {}, {}
    for name, ttl in (("raw", 10**9), ("fold", 7 * 86400)):
        db = store.create(tmp_path / f"{name}.sqlite", T0, config=store.InstanceConfig(evidence_ttl_seconds=ttl))
        try:
            evidence.start_epoch(db, "desk", T0)
            obs(db, "c1", "desk", "camera", "cam", 1, T0, sample="s1")            # seq 1
            obs(db, "r1", "desk", "reference", "ref-1", 0, T0 + 1)
            obs(db, "r2", "desk", "reference", "ref-2", 0, T0 + 2)
            obs(db, "w1", "desk", "word", "ana", 0, T0 + 3)
            obs(db, "c2", "desk", "camera", "cam", 1, T0 + 4, sample="s1")        # seq 5: a later copy of s1
            obs(db, "w2", "desk", "word", "bo", 0, T0 + 5)                        # seq 6
            obs(db, "c3", "desk", "camera", "cam", 1, T0 + 6)                     # seq 7
            obs(db, "c4", "desk", "camera", "cam", 1, T0 + 7)
            with store.write(db):
                db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                           "VALUES('desk','camera:cam',5,NULL,?,1)", (T0,))   # the copy opens the new segment
                store.bump_state_version(db)
            governance.sweep(db, T0 + 8 * DAY)
            tallies[name] = beliefs._aggregate(db, "desk", [5])[1]
            believed[name] = held(db, "desk")
        finally:
            db.close()
    assert tallies["raw"][("camera", "cam", 1)] == [0, 2, 7]              # two readings, the first at seq 7, not 5
    assert tallies["fold"] == tallies["raw"] and believed["fold"] == believed["raw"]


def _twin_life(tmp_path, seed, order):
    """The same operations on a file whose events never expire and on one with 7-day lifetimes: observations with
    and without sample ids, promotions, forgets of single events (sampled ones too) and of a partner, and the
    executive's questions answered with a confirmation. `order`: the expiring file sweeps before each forget (as
    every command does) or after it. Returns the first difference in what is believed, or None."""
    rng = random.Random(seed)
    dbs = {"raw": store.create(tmp_path / f"raw-{seed}-{order}.sqlite", T0,
                               config=store.InstanceConfig(evidence_ttl_seconds=10**9)),
           "fold": store.create(tmp_path / f"fold-{seed}-{order}.sqlite", T0)}
    try:
        for db in dbs.values():
            evidence.start_epoch(db, "a", T0)
            evidence.start_epoch(db, "b", T0)
        now, n, events, truth = T0, 0, [], {}

        def forget(action):
            for name, db in dbs.items():
                if name == "fold" and order == "sweep_first":
                    governance.sweep(db, now)
                action(db)
                if name == "fold":
                    governance.sweep(db, now)

        for step in range(90):
            now += rng.choice([600, 3600, 6 * 3600, 86400])
            r = rng.random()
            if r < .72:
                n += 1
                channel = rng.choice(["camera", "word", "word", "reference"])
                root = rng.choice({"camera": ["c1"], "word": ["ana", "bo"], "reference": ["r1", "r2", "r3"]}[channel])
                epoch = "a" if rng.random() < .8 else "b"
                value = truth.setdefault((epoch, channel, root), rng.randrange(2)) ^ (step > 45 and root == "c1")
                sample = f"s{rng.randrange(4)}" if rng.random() < .4 else None
                value, action = value ^ (rng.random() < .1), rng.randrange(2)
                for db in dbs.values():
                    obs(db, f"i{n}", epoch, channel, root, value, now, action=action, sample=sample)
                events.append((f"i{n}", sample is not None))
                if rng.random() < .08:
                    for db in dbs.values():
                        governance.promote(db, f"i{n}", now)
            elif r < .86 and events:
                sampled = [e for e in events if e[1]]
                victim = rng.choice(sampled if sampled and rng.random() < .7 else events)[0]
                forget(lambda db: governance.forget_evidence(db, victim, now))
            elif r < .89:
                forget(lambda db: governance.forget_partner(db, "bo", now))
            else:
                for db in dbs.values():
                    governance.sweep(db, now)
                decided = {name: executive.decide(db, "a", now, record=True) for name, db in dbs.items()}
                answers = {name: (d["decision"], d["scope"], d["reason"]) for name, d in decided.items()}
                if answers["raw"] != answers["fold"]:
                    return ("decide", step, answers)
                if decided["raw"]["decision"] == "ask" and rng.random() < .7:
                    for name, db in dbs.items():
                        try:
                            executive.resolve(db, decided[name]["pending_id"], "yes", now + 1, confirm=True)
                        except Exception as error:      # an epoch question that cannot link: the same in both
                            answers[name] = type(error).__name__
                    if answers["raw"] != answers["fold"] and "Error" in str(answers):
                        return ("resolve", step, answers)
            governance.sweep(dbs["fold"], now)
            for epoch in ("a", "b"):
                left, right = held(dbs["raw"], epoch), held(dbs["fold"], epoch)
                if left != right:
                    keys = [k for k in left if left[k] != right[k]]
                    return (epoch, step, keys, left[keys[0]], right[keys[0]])
                bounds = sorted({q for seqs in beliefs.changes(dbs["raw"], epoch)["confirmed"].values() for q in seqs})
                left, right = (beliefs._aggregate(dbs[name], epoch, bounds)[1] for name in ("raw", "fold"))
                if left != right:               # the counts and the first seq of every (channel, root, segment)
                    return (epoch, step, "tallies", left, right)
        return None
    finally:
        for db in dbs.values():
            db.close()


@pytest.mark.parametrize("order", ["sweep_first", "forget_first"])
@pytest.mark.parametrize("seed", range(10))
def test_an_expiring_individual_believes_what_one_that_never_expires_believes(tmp_path, seed, order):
    """Review F1, the general case: forgets of sampled events included, and in both orders of sweep and forget."""
    assert _twin_life(tmp_path, seed, order) is None


def test_the_owner_can_see_what_was_kept_and_nothing_else_was(instance):
    """What remains of an expired learning event: its seq, epoch, channel, root, corrected reading, sample id. Its
    id is in the tombstone only; its actor and timestamps are nowhere."""
    db, path = instance
    evidence.start_epoch(db, "desk", T0)
    evidence.observe(db, {"id": "secret-event-id", "epoch": "desk", "channel": "word", "action": 1, "bit": 0,
                          "root": "ana", "sample_id": "utterance-9", "actor": "visitor-zq",
                          "acquired_at": 1234567.25}, T0)
    governance.sweep(db, T0 + 8 * DAY)
    item, = memory.retrieve(db, "consolidated", epoch="desk")["items"]
    assert item["id"] == 1 and item["scope"] == {"epoch": "desk", "root": "ana"} and item["time"] == T0 + 8 * DAY
    assert item["payload"] == {"seq": 1, "channel": "word", "reading": 1, "sample_id": "utterance-9"}
    assert item["permissions"] == {"consent": "learn", "retention": "consolidated", "expires_at": None}
    assert memory.retrieve(db, "consolidated", id=1, channel="word", root="ana")["items"] == [item]
    assert memory.retrieve(db, "consolidated", root="bo")["items"] == []
    assert memory.retrieve(db, "evidence", epoch="desk")["items"] == []
    raw = path.read_bytes() + (path.parent / (path.name + "-wal")).read_bytes() \
        if (path.parent / (path.name + "-wal")).exists() else path.read_bytes()
    assert b"visitor-zq" not in raw
    tables = [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    holders = [t for t in tables if t != "tombstones" and any(
        "secret-event-id" in str(value) for row in db.execute(f"SELECT * FROM {t}") for value in row)]
    assert holders == []
    with pytest.raises(Gone):
        obs(db, "secret-event-id", "desk", "word", "ana", 0, T0 + 9 * DAY)
    with pytest.raises(Gone):
        governance.promote(db, "secret-event-id", T0 + 9 * DAY)


# ---- forgetting ----------------------------------------------------------------------------------------------------

def test_forgetting_one_event_after_it_was_consolidated_is_exact(tmp_path):
    """The tombstone keeps the id with the seq, so the owner can still forget exactly that event; beliefs are then
    those of an individual that forgot the raw event."""
    results = {}
    for name, ttl in (("folded", 7 * DAY), ("raw", 10**9)):
        db = store.create(tmp_path / f"{name}.sqlite", T0, config=store.InstanceConfig(evidence_ttl_seconds=int(ttl)))
        try:
            ids = teach(db, rounds=3)
            obs(db, "odd", "desk", "word", "ana", 1, T0 + 50)             # the one to forget
            governance.sweep(db, T0 + 8 * DAY)
            version = store.state_version(db)
            out = governance.forget_evidence(db, "odd", T0 + 8 * DAY + 1)
            assert out["status"] == "forgotten" and store.state_version(db) == version + 1
            if name == "folded":
                assert out["effects"]["deleted"] == 0 and out["effects"]["consolidated_deleted"] == 1
                assert db.execute("SELECT reason, deleted_at FROM tombstones WHERE id='odd'").fetchone() == \
                    ("forgotten", T0 + 8 * DAY + 1)
                assert count(db, "evidence_consolidated") == len(ids)
                last = json.loads(db.execute("SELECT summary FROM belief_revisions ORDER BY id DESC LIMIT 1"
                                             ).fetchone()[0])
                assert last["removed"] == 1 and last["cause"] == governance.FORGOTTEN_CAUSE
                assert governance.forget_evidence(db, "odd", T0 + 8 * DAY + 2)["status"] == "absent"
                assert governance.forget_evidence(db, ids[0], T0 + 8 * DAY + 3, reason="expired")["status"] == "absent"
                assert count(db, "evidence_consolidated") == len(ids)       # an expiry never removes a reading
            with pytest.raises(Gone):
                obs(db, "odd", "desk", "word", "ana", 1, T0 + 9 * DAY)
            results[name] = held(db, "desk")
        finally:
            db.close()
    assert results["folded"] == results["raw"]


def test_forgetting_a_partner_removes_its_consolidated_words_in_every_epoch(instance):
    db, path = instance
    teach(db, "desk")
    teach(db, "garage", now=T0 + 100)
    obs(db, "cam-named-ana", "garage", "camera", "ana", 1, T0 + 300)      # a camera root of the same name stays
    governance.sweep(db, T0 + 8 * DAY)
    assert count(db, "evidence_consolidated", "channel='word' AND root='ana'") == 12
    out = governance.forget_partner(db, "ana", T0 + 8 * DAY + 1)
    assert out["effects"]["deleted"] == 0 and out["effects"]["consolidated_deleted"] == 12
    assert out["effects"]["epochs"] == ["desk", "garage"] and out["effects"]["roots_forgotten"] == 0
    assert count(db, "evidence_consolidated", "channel='word'") == 0
    assert count(db, "evidence_consolidated", "channel='camera' AND root='ana'") == 1
    assert count(db, "tombstones", "reason='partner_forgotten'") == 12
    for epoch in ("desk", "garage"):
        assert beliefs.compute(db, epoch)["partners"] == {}
    gone = governance.forget_evidence(db, "cam-named-ana", T0 + 8 * DAY + 2)   # now the name's last evidence
    assert gone["effects"]["roots_forgotten"] == 1 and gone["effects"]["consolidated_deleted"] == 1
    assert count(db, "evidence_consolidated", "root='ana'") == 0
    summaries = "".join(s for (s,) in db.execute("SELECT summary FROM belief_revisions"))
    assert '"ana"' not in summaries and "partner:ana" not in summaries      # the name is scrubbed from the history
    assert db.execute("SELECT kind FROM forgotten_roots").fetchall() == [("camera",)]


def test_forgetting_an_epoch_removes_its_consolidated_readings_and_revises_an_epoch_that_continued_it(instance):
    db, _ = instance
    teach(db, "desk")
    evidence.start_epoch(db, "later", T0 + 1000)
    obs(db, "l1", "later", "camera", "cam-a", 1, T0 + 1001)
    with store.write(db):
        db.execute("INSERT INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) "
                   "VALUES('later','desk',?,NULL,?)",
                   (json.dumps({"mechanism": "mechanism@0", "camera:cam-a": "cam-a@0", "partner:ana": "ana@0"}), T0))
        store.bump_state_version(db)
    governance.promote(db, "l1", T0 + 1002)
    governance.sweep(db, T0 + 8 * DAY)
    pooled = beliefs.compute(db, "later")
    assert pooled["linked_from"]["learning"] == 18 and pooled["mechanism"]["identifiable"]
    assert pooled["sensors"]["cam-a"]["n"] == 7 and count(db, "evidence_consolidated", "epoch='desk'") == 18
    out = governance.forget_epoch(db, "desk", T0 + 8 * DAY + 1)
    assert out["effects"]["consolidated_deleted"] == 18 and out["effects"]["deleted"] == 0
    assert count(db, "evidence_consolidated") == 0 and count(db, "tombstones", "reason='epoch_forgotten'") == 18
    after = beliefs.compute(db, "later")
    assert after["linked_from"] is None and after["sensors"]["cam-a"]["n"] == 1
    assert after["mechanism"]["identifiable"] is False


def test_a_consolidated_reading_forgotten_in_the_source_changes_the_epoch_that_continues_it(instance):
    db, _ = instance
    ids = teach(db, "desk")
    evidence.start_epoch(db, "later", T0 + 1000)
    with store.write(db):
        db.execute("INSERT INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) "
                   "VALUES('later','desk',?,NULL,?)",
                   (json.dumps({"mechanism": "mechanism@0", "camera:cam-a": "cam-a@0"}), T0))
        store.bump_state_version(db)
    governance.sweep(db, T0 + 8 * DAY)
    assert beliefs.compute(db, "later")["sensors"]["cam-a"]["n"] == 6
    out = governance.forget_evidence(db, ids[1], T0 + 8 * DAY + 1)             # a camera reading of desk
    assert out["effects"]["linked_epochs_revised"] == ["later"]
    assert beliefs.compute(db, "later")["sensors"]["cam-a"]["n"] == 5
    # one revision when desk's events expired (a sweep revises linked epochs too, as before), one for the forget
    assert [c for (c,) in db.execute("SELECT cause FROM belief_revisions WHERE epoch='later' ORDER BY id")] == \
        ["expired", governance.FORGOTTEN_CAUSE]


def test_private_roots_held_only_as_consolidated_readings_stay_private(instance):
    """A root's name must not go out in a web query. It is still the individual's while a consolidated reading
    holds it, though the journal has no event of it left."""
    from neuroscifi.webcortex import orchestrator
    from neuroscifi.webcortex.config import WebConfig
    db, _ = instance
    teach(db)
    obs(db, "fresh", "desk", "camera", "cam-new", 1, T0 + 7 * DAY)
    governance.sweep(db, T0 + 8 * DAY)
    assert governance.held_roots(db) == ["cam-new", "ana", "cam-a"]       # the journal's, then the consolidated ones
    assert orchestrator.private_terms(db, WebConfig(private_terms=["mine"])) == ["mine", "cam-new", "ana", "cam-a"]


# ---- checkpoints and restore ---------------------------------------------------------------------------------------

def _restored(db, checkpoint, dest, now):
    report = checkpoints.restore(db, checkpoint["id"], dest, now)
    return report, store.open_existing(dest)


def test_restoring_an_archive_older_than_the_expiry_keeps_the_learning_and_drops_the_episodes(instance, tmp_path):
    db, path = instance
    ids = teach(db)
    obs(db, "quiet", "desk", "word", "ana", 1, T0 + 30, consent="memory_only")
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)
    governance.sweep(db, T0 + 8 * DAY)
    report, copy = _restored(db, archive, tmp_path / "r.sqlite", T0 + 8 * DAY + 1)
    with closing(copy):
        assert report["evidence_removed"] == 19 and report["evidence_consolidated"] == 18
        assert count(copy, "evidence") == 0
        assert folded(copy) == folded(db) and len(folded(copy)) == 18
        assert copy.execute("SELECT seq,epoch,channel,root,value,sample_id,consolidated_at FROM evidence_consolidated"
                            ).fetchall() == db.execute(
            "SELECT seq,epoch,channel,root,value,sample_id,consolidated_at FROM evidence_consolidated").fetchall()
        assert held(copy, "desk") == held(db, "desk")
        assert count(copy, "tombstones", "reason='expired'") == 19
        for event_id in (*ids, "quiet"):
            assert event_id.encode() not in b"".join(
                str(v).encode() for (v,) in copy.execute("SELECT cause FROM belief_revisions"))
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_a_restore_never_brings_back_a_consolidated_reading_the_owner_forgot(instance, tmp_path):
    """Three archives: before the expiry (raw events), after it (consolidated readings), and after a forget. Then
    one event, a partner and an epoch are forgotten. No restore holds any of them, raw or consolidated."""
    db, path = instance
    ids = teach(db, "desk")
    teach(db, "garage", now=T0 + 100)
    raw_archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 400)
    governance.sweep(db, T0 + 8 * DAY)
    folded_archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 8 * DAY + 1)
    governance.forget_evidence(db, ids[1], T0 + 8 * DAY + 2)                  # one camera reading of desk
    governance.forget_partner(db, "ana", T0 + 8 * DAY + 3)
    governance.forget_epoch(db, "garage", T0 + 8 * DAY + 4)
    assert {s for (s,) in db.execute("SELECT status FROM checkpoints")} == {"stale"}
    live = folded(db)
    assert len(live) == 11 and {r[1] for r in live} == {"desk"} and not [r for r in live if r[2] == "word"]
    for n, archive in enumerate((raw_archive, folded_archive)):
        report, copy = _restored(db, archive, tmp_path / f"r{n}.sqlite", T0 + 8 * DAY + 10 + n)
        with closing(copy):
            assert count(copy, "evidence") == 0, report
            assert folded(copy) == live, report
            assert held(copy, "desk") == held(db, "desk")
            assert count(copy, "evidence_consolidated", "epoch='garage'") == 0
            assert count(copy, "belief_revisions", "epoch='garage'") == 0
            reasons = dict(copy.execute("SELECT reason, count(*) FROM tombstones GROUP BY reason").fetchall())
            assert reasons == dict(db.execute("SELECT reason, count(*) FROM tombstones GROUP BY reason").fetchall())
    assert report["consolidated_removed"] == 25      # the folded archive: 1 event + 6 words of desk + 18 of garage


def test_a_second_generation_restore_keeps_the_learning_and_the_forgets(instance, tmp_path):
    """The class of defect the review found for epochs: a restored copy is a whole individual, and an older archive
    restored FROM it must end in the same state. The copy carries the consolidated readings and the changed
    tombstone reasons, so it can replay both."""
    db, path = instance
    ids = teach(db)
    older = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)             # raw events
    governance.sweep(db, T0 + 8 * DAY)
    middle = checkpoints.create(db, path, tmp_path / "cps", T0 + 8 * DAY + 0.5)  # consolidated readings
    newer = checkpoints.create(db, path, tmp_path / "cps", T0 + 8 * DAY + 1)    # the same, and it lists `middle`
    governance.forget_evidence(db, ids[2], T0 + 8 * DAY + 2)                    # a word of ana
    expected, believed = folded(db), held(db, "desk")
    assert len(expected) == 17
    _, first = _restored(db, newer, tmp_path / "r1.sqlite", T0 + 8 * DAY + 3)
    with closing(first):
        assert folded(first) == expected
        assert first.execute("SELECT reason FROM tombstones WHERE id=?", (ids[2],)).fetchone() == ("forgotten",)
        report, second = _restored(first, older, tmp_path / "r2.sqlite", T0 + 8 * DAY + 4)
        with closing(second):
            assert count(second, "evidence") == 0 and folded(second) == expected, report
            assert held(second, "desk") == believed
            assert second.execute("SELECT reason FROM tombstones WHERE id=?", (ids[2],)).fetchone() == ("forgotten",)
        # the archive that HOLDS the forgotten reading, restored from the copy: the copy's tombstone says forgotten
        _, same = _restored(first, middle, tmp_path / "r2b.sqlite", T0 + 8 * DAY + 4.5)
        with closing(same):
            assert folded(same) == expected
        # the first copy learns something new, lets it expire and forgets one more reading; the older archive
        # restored from it then follows the copy's history, not the abandoned file's
        obs(first, "in-copy", "desk", "camera", "cam-a", 1, T0 + 8 * DAY + 5)
        governance.sweep(first, T0 + 16 * DAY)
        governance.forget_evidence(first, ids[4], T0 + 16 * DAY + 1)            # a camera reading
        assert len(folded(first)) == 17
        _, again = _restored(first, older, tmp_path / "r3.sqlite", T0 + 16 * DAY + 2)
        with closing(again):
            # the archive never held "in-copy", so the copy has the 16 readings of the archive's events that are left
            assert count(again, "evidence") == 0 and folded(again) == [r for r in folded(first) if r[0] <= 18]
            assert len(folded(again)) == 16


def test_a_restored_copy_never_issues_an_evidence_seq_the_live_file_used(instance, tmp_path):
    """A consolidated reading is identified by its seq alone (the tombstone maps an id to it), so a restored copy
    must not give a seq of the live file to another event."""
    db, path = instance
    teach(db, rounds=2)                                                          # seqs 1..6
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)
    for n in range(4):
        obs(db, f"after-{n}", "desk", "camera", "cam-a", 1, T0 + 100 + n)        # seqs 7..10, not in the archive
    governance.forget_evidence(db, "after-3", T0 + 200)                          # the highest one is only a tombstone
    _, copy = _restored(db, archive, tmp_path / "r.sqlite", T0 + 300)
    with closing(copy):
        assert copy.execute("SELECT max(seq) FROM evidence").fetchone()[0] == 6
        assert obs(copy, "new-in-copy", "desk", "camera", "cam-a", 1, T0 + 301) == 11
    # an archive restored without the replay is a plain copy: its counter is its own
    checkpoints.restore(db, archive["id"], tmp_path / "plain.sqlite", T0 + 302, apply_tombstones=False)
    with closing(store.open_existing(tmp_path / "plain.sqlite")) as plain:
        assert obs(plain, "new-in-plain", "desk", "camera", "cam-a", 1, T0 + 303) == 7


def test_an_archive_of_an_individual_without_consolidation_restores_as_before(tmp_path):
    path = tmp_path / "off.sqlite"
    db = store.create(path, T0, config=store.InstanceConfig(consolidation=False))
    try:
        teach(db)
        archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)
        governance.sweep(db, T0 + 8 * DAY)
        report, copy = _restored(db, archive, tmp_path / "r.sqlite", T0 + 8 * DAY + 1)
        with closing(copy):
            assert report["evidence_removed"] == 18 and report["evidence_consolidated"] == 0
            assert count(copy, "evidence") == 0 and count(copy, "evidence_consolidated") == 0
            assert beliefs.compute(copy, "desk")["mechanism"]["p_inverted"] == 0.5
    finally:
        db.close()


# ---- the migration -------------------------------------------------------------------------------------------------

def _version_13_file(tmp_path):
    """A file as 0.7.0 leaves it: a real individual with evidence, questions and a forgotten event, at schema 13
    (migration 14 only adds evidence_consolidated, its indexes and an index on tombstones)."""
    path = tmp_path / "v13.sqlite"
    db = store.create(path, T0)
    teach(db)
    obs(db, "gone", "desk", "word", "bo", 0, T0 + 50)
    governance.forget_evidence(db, "gone", T0 + 51)
    executive.decide(db, "desk", T0 + 52, record=True)
    believed = held(db, "desk")
    db.close()
    raw = sqlite3.connect(path, isolation_level=None)
    raw.executescript("BEGIN; DROP TABLE evidence_consolidated; DROP INDEX tombstones_epoch_seq; "
                      "PRAGMA user_version=13; COMMIT;")
    tables = sorted(name for (name,) in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    dump = {t: raw.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    raw.close()
    return path, dump, believed


def test_a_version_13_file_gains_the_table_and_keeps_every_row(tmp_path):
    path, dump, believed = _version_13_file(tmp_path)
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION >= 14
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {t: db.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in dump} == dump
        assert count(db, "evidence_consolidated") == 0
        assert {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE tbl_name='evidence_consolidated' "
                                               "AND type='index' AND name NOT LIKE 'sqlite_%'")} == \
            {"evidence_consolidated_epoch", "evidence_consolidated_copies", "evidence_consolidated_tally",
             "evidence_consolidated_root"}
        assert db.execute("SELECT tbl_name FROM sqlite_master WHERE name='tombstones_epoch_seq'").fetchone() == \
            ("tombstones",)
        # settings saved by 0.7.0 have no `consolidation`: the default applies, and the individual keeps learning
        assert store.config(db).consolidation is True and held(db, "desk") == believed
        assert governance.sweep(db, T0 + 8 * DAY)["consolidated"] == 18
        assert held(db, "desk") == believed
    finally:
        db.close()


class _UpgradedMeanwhile(sqlite3.Connection):
    """Another process finishes the upgrade right after this one first read user_version (as in test_migrations)."""
    other = None

    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        if sql.strip() == "PRAGMA user_version" and self.other is not None:
            other, self.other = self.other, None
            version = result.fetchone()
            schema.migrate(other)
            return super().execute("SELECT ?", version)
        return result


def test_two_processes_upgrading_a_version_13_file_apply_migration_14_once(tmp_path):
    path, dump, _ = _version_13_file(tmp_path)
    first = sqlite3.connect(path, isolation_level=None, timeout=1)
    second = sqlite3.connect(path, isolation_level=None, timeout=1, factory=_UpgradedMeanwhile)
    try:
        second.other = first
        assert schema.migrate(second) == schema.SCHEMA_VERSION
        assert second.other is None and not second.in_transaction and not first.in_transaction
        assert second.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert second.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {t: second.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in dump} == dump
        assert second.execute("SELECT count(*) FROM evidence_consolidated").fetchone()[0] == 0
    finally:
        first.close()
        second.close()


def test_the_table_refuses_what_the_journal_refuses(instance):
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    bad = [(1, "nowhere", "camera", "r", 0), (1, "desk", "smell", "r", 0), (1, "desk", "camera", "", 0),
           (1, "desk", "camera", "r", 2)]
    for row in bad:
        with pytest.raises(sqlite3.IntegrityError):
            with store.write(db):
                db.execute("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,consolidated_at) "
                           "VALUES(?,?,?,?,?,0)", row)


# ---- the command line ----------------------------------------------------------------------------------------------

def test_cli_sweep_reports_what_it_kept_and_config_turns_it_off(tmp_path, capsys):
    from neuroscifi import cli

    def nsf(*argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if code == 0 else captured.err

    path = str(tmp_path / "cli.sqlite")
    assert nsf("init", "--db", path, "--ttl-days", "0.000001")[0] == 0
    assert nsf("epoch", "start", "e", "--db", path)[0] == 0
    assert nsf("observe", "e", "x1", "camera", "0", "0", "cam", "--db", path)[0] == 0
    import time
    time.sleep(0.2)
    code, swept = nsf("sweep", "--db", path)
    assert code == 0 and swept["expired"] == 1 and swept["consolidated"] == 1
    code, birth = nsf("birth", "--db", path)
    assert birth["memory"]["consolidated_readings"] == 1 and birth["memory"]["evidence"] == 0
    code, listed = nsf("memory", "consolidated", "--db", path, "--epoch", "e")
    assert code == 0 and [i["payload"]["reading"] for i in listed["items"]] == [0]
    # `nsf config` does not sweep first: an event already past its lifetime is still there when the setting changes,
    # so the setting decides what that event leaves
    assert nsf("observe", "e", "x2", "camera", "0", "0", "cam", "--db", path)[0] == 0
    time.sleep(0.2)
    code, out = nsf("config", "--db", path, "--no-consolidation")
    assert code == 0 and out["changed"] == ["consolidation"] and out["config"]["consolidation"] is False
    code, swept = nsf("sweep", "--db", path)
    assert code == 0 and swept["expired"] == 1 and "consolidated" not in swept
    code, status = nsf("status", "e", "--db", path)
    assert status["sensors"]["cam"]["n"] == 1 and status["evidence"]["consolidated"] == 1
    code, gone = nsf("forget", "evidence", "x1", "--db", path)
    assert code == 0 and gone["effects"]["consolidated_deleted"] == 1
    code, status = nsf("status", "e", "--db", path)
    assert status["sensors"] == {} and "consolidated" not in status["evidence"]


# ---- review round: forgets with a handle the owner is shown; settings and restores; the horizon ---------------------

def test_a_consolidated_reading_is_forgotten_by_the_seq_the_owner_is_shown(tmp_path):
    """Security review M2: `nsf memory consolidated` shows a reading's seq, never its event id, so a forget has to
    take the seq. Everything else is as a forget by id: checked against a twin that forgets the same event by id."""
    states = {}
    for name in ("by_seq", "by_id"):
        path = tmp_path / f"{name}.sqlite"
        db = store.create(path, T0)
        try:
            ids = teach(db, rounds=3)
            archive = checkpoints.create(db, path, tmp_path / f"cps-{name}", T0 + 60)
            governance.sweep(db, T0 + 8 * DAY)                       # the archive of raw events is stale from here
            later = checkpoints.create(db, path, tmp_path / f"cps-{name}", T0 + 8 * DAY + 0.5)
            listed = memory.retrieve(db, "consolidated", channel="word")["items"]
            target = listed[-1]["id"]                        # the oldest word reading: seq 3, event ids[2]
            assert target == 3 and "id" not in listed[-1]["payload"]
            version = store.state_version(db)
            out = governance.forget_consolidated(db, target, T0 + 8 * DAY + 1) if name == "by_seq" \
                else governance.forget_evidence(db, ids[2], T0 + 8 * DAY + 1)
            assert out["status"] == "forgotten" and out["effects"]["consolidated_deleted"] == 1
            assert out["effects"]["deleted"] == 0 and out["effects"]["checkpoints_stale"] == [later["id"]]
            assert store.state_version(db) == version + 1
            log = db.execute("SELECT target_kind, target_id FROM deletion_log ORDER BY id DESC LIMIT 1").fetchone()
            assert log == (("consolidated", "3") if name == "by_seq" else ("evidence", ids[2]))
            last = json.loads(db.execute("SELECT summary FROM belief_revisions ORDER BY id DESC LIMIT 1").fetchone()[0])
            assert last["removed"] == 1 and last["cause"] == governance.FORGOTTEN_CAUSE
            # the same reading again, a seq that is no reading, and the seq of an event still in the journal: absent
            fresh = obs(db, "fresh", "desk", "word", "ana", 0, T0 + 8 * DAY + 2)
            for seq in (target, 10_000, fresh):
                assert governance.forget_consolidated(db, seq, T0 + 8 * DAY + 3)["status"] == "absent"
            assert count(db, "evidence") == 1
            for n, held_in in enumerate((archive, later)):               # as raw events, and as readings
                _, copy = _restored(db, held_in, tmp_path / f"r-{name}-{n}.sqlite", T0 + 8 * DAY + 4 + n)
                with closing(copy):
                    assert folded(copy) == folded(db) and count(copy, "evidence") == 0
            states[name] = (held(db, "desk"), folded(db), db.execute(
                "SELECT id, seq, epoch, reason, deleted_at FROM tombstones ORDER BY seq").fetchall())
        finally:
            db.close()
    assert states["by_seq"] == states["by_id"]
    assert [t for t in states["by_seq"][2] if t[3] != "expired"] == [("desk-2", 3, "desk", "forgotten", T0 + 8 * DAY + 1)]


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3", None])
def test_forget_consolidated_refuses_what_is_not_a_seq(instance, bad):
    from neuroscifi.instance.errors import ValidationError
    db, _ = instance
    with pytest.raises(ValidationError):
        governance.forget_consolidated(db, bad, T0)


def test_forgetting_a_camera_removes_it_everywhere_like_a_partner(instance, tmp_path):
    """Security review M2: a camera root is a private name that consolidation now keeps; short of its epoch there
    was no way to forget it. The mirror of forget_partner: every epoch, events and consolidated readings, the changes
    confirmed for it, root scrubbing, a digest label in the log, and no return through a restore."""
    db, path = instance
    teach(db, "desk")
    teach(db, "garage", now=T0 + 100)
    obs(db, "word-named-cam", "garage", "word", "cam-a", 1, T0 + 300)       # a partner of the same name stays
    raw_archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 400)
    governance.sweep(db, T0 + 8 * DAY)
    obs(db, "late", "desk", "camera", "cam-a", 1, T0 + 8 * DAY + 1)         # one more, still in the journal
    with store.write(db):
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('desk','camera:cam-a',5,NULL,?,1)", (T0,))
        store.bump_state_version(db)
    folded_archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 8 * DAY + 2)
    assert beliefs.compute(db, "desk")["sensors"]["cam-a"]["n"] == 6        # the current segment: seq 5 and later
    out = governance.forget_camera(db, "cam-a", T0 + 8 * DAY + 3)
    effects = out["effects"]
    assert out["status"] == "forgotten" and effects["deleted"] == 1 and effects["consolidated_deleted"] == 12
    assert effects["epochs"] == ["desk", "garage"] and effects["roots_forgotten"] == 0
    assert effects["derived_deleted"] == {"changepoints": 1}
    assert count(db, "evidence_consolidated", "channel='camera'") == 0
    assert count(db, "evidence", "channel='camera'") == 0
    assert count(db, "evidence_consolidated", "channel='word' AND root='cam-a'") == 1
    for epoch in ("desk", "garage"):
        now = beliefs.compute(db, epoch)
        assert now["sensors"] == {} and now["segments"] == {} and now["confirmed_changes"] == []
    assert count(db, "tombstones", "reason='forgotten'") == 13
    assert count(db, "tombstones", "reason='partner_forgotten'") == 0
    kind, target, logged = db.execute(
        "SELECT target_kind, target_id, effects FROM deletion_log ORDER BY id DESC LIMIT 1").fetchone()
    assert kind == "camera" and target.startswith("camera:#") and "cam-a" not in target + logged
    assert governance.forget_camera(db, "cam-a", T0 + 8 * DAY + 4)["status"] == "absent"
    live = folded(db)
    for n, archive in enumerate((raw_archive, folded_archive)):
        _, copy = _restored(db, archive, tmp_path / f"r{n}.sqlite", T0 + 8 * DAY + 10 + n)
        with closing(copy):
            assert count(copy, "evidence") == 0 and folded(copy) == live
            assert count(copy, "evidence_consolidated", "channel='camera'") == 0
    # the last evidence of that name goes: now the root is scrubbed from the history
    gone = governance.forget_partner(db, "cam-a", T0 + 8 * DAY + 20)
    assert gone["effects"]["roots_forgotten"] == 1
    assert "cam-a" not in "".join(s for (s,) in db.execute("SELECT summary FROM belief_revisions"))
    from neuroscifi.instance.errors import ValidationError
    for bad in ("", None, 7):
        with pytest.raises(ValidationError):
            governance.forget_camera(db, bad, T0)


def test_forgetting_a_camera_whose_events_never_expired(instance):
    db, path = instance
    teach(db, rounds=2)
    out = governance.forget_camera(db, "cam-a", T0 + 100)
    assert out["effects"]["deleted"] == 2 and "consolidated_deleted" not in out["effects"]
    assert out["effects"]["roots_forgotten"] == 1 and b"cam-a" not in path.read_bytes()
    now = beliefs.compute(db, "desk")
    assert now["sensors"] == {} and now["partners"]["ana"]["n"] == 2


def test_forget_evidence_looks_up_the_tombstone_and_scans_no_reading(instance):
    """Review F3: the statement that found a consolidated event scanned every reading with a correlated subquery,
    for every id: also for an event still in the journal and for an unknown id."""
    db, _ = instance
    ids = teach(db, rounds=3)
    governance.sweep(db, T0 + 8 * DAY)
    obs(db, "raw", "desk", "word", "ana", 0, T0 + 8 * DAY + 1)
    statements = []
    db.set_trace_callback(statements.append)
    try:
        assert governance.forget_evidence(db, "no-such-id", T0 + 8 * DAY + 2)["status"] == "absent"
        assert governance.forget_evidence(db, "raw", T0 + 8 * DAY + 3)["effects"]["deleted"] == 1
        seen = len(statements)
        assert governance.forget_evidence(db, ids[0], T0 + 8 * DAY + 4)["effects"]["consolidated_deleted"] == 1
    finally:
        db.set_trace_callback(None)
    # an unknown id and a journal event never touch the readings; a consolidated one is found by its key
    finds = [s for s in statements if "FROM evidence_consolidated WHERE seq=" in s]
    assert not [s for s in statements[:seen] if "SELECT seq, NULL, epoch, channel, root FROM evidence_consolidated" in s]
    assert len(finds) >= 1 and all(s in statements[seen:] for s in finds)
    for statement in finds:
        plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + statement)]
        assert plan and all(step.startswith("SEARCH") and "INTEGER PRIMARY KEY" in step for step in plan), plan


def test_the_horizon_of_a_question_does_not_fall_when_events_expire(instance):
    """Review F5: "evidence through #N" was the highest seq left in the journal, so it fell at a sweep that dropped a
    memory-only event (9 before, 8 after), and one situation got two question texts."""
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    for root in ("ref-1", "ref-2", "ref-3"):
        obs(db, root, "desk", "reference", root, 0, T0)
    for k in range(4):
        obs(db, f"w{k}", "desk", "word", "ana", k % 2, T0 + 1 + k)
    obs(db, "quiet", "desk", "camera", "cam-a", 1, T0 + 9, consent="memory_only")
    before = executive.decide(db, "desk", T0 + 10, record=True)
    question = executive.pending(db)[0]["question"]
    assert before["decision"] == "ask" and executive._horizon(db, "desk") == 8
    assert "(evidence through #8)" in question
    governance.sweep(db, T0 + 8 * DAY)
    assert count(db, "evidence") == 0 and count(db, "evidence_consolidated") == 7
    assert executive._horizon(db, "desk") == 8
    with store.write(db):
        db.execute("DELETE FROM pending")           # ask afresh: the same situation, the same text
    after = executive.decide(db, "desk", T0 + 8 * DAY + 2, record=True)
    assert after["decision"] == "ask" and executive.pending(db)[0]["question"] == question
    # forgetting the latest reading does not move it either, and the count of new events since it is unchanged
    governance.forget_consolidated(db, 7, T0 + 8 * DAY + 3)
    assert executive._horizon(db, "desk") == 8 and executive._new_events(db, "desk", "partner:ana", 8) == 0
    obs(db, "new", "desk", "word", "ana", 0, T0 + 8 * DAY + 4)
    assert executive._horizon(db, "desk") == 9 and executive._new_events(db, "desk", "partner:ana", 8) == 1


def test_a_restore_carries_the_owners_settings_into_the_copy(instance, tmp_path):
    """Security review M1: an archive taken before `nsf config --no-consolidation` turned consolidation back on in
    the restored copy, which then kept roots, sample ids and readings without limit and told nobody. The settings an
    owner may change are the live file's in every copy, through a second generation, and the report says so."""
    db, path = instance
    teach(db)
    oldest = checkpoints.create(db, path, tmp_path / "cps", T0 + 50)         # consolidation on, budget 3 per 7 days
    older = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)          # the same, and it lists `oldest`
    assert store.set_config(db, {"consolidation": False, "ask_budget_per_epoch": 5}, T0 + 70)["changed"] == [
        "ask_budget_per_epoch", "consolidation"]
    newer = checkpoints.create(db, path, tmp_path / "cps", T0 + 80)          # the settings as they are now
    report, first = _restored(db, older, tmp_path / "r1.sqlite", T0 + 90)
    with closing(first):
        assert report["settings_carried"] == {"consolidation": {"from": True, "to": False},
                                              "ask_budget_per_epoch": {"from": 3, "to": 5}}
        cfg = store.config(first)
        assert cfg.consolidation is False and cfg.ask_budget_per_epoch == 5
        assert cfg.ask_budget_window_seconds == 7 * 86400 and cfg.evidence_ttl_seconds == 7 * 86400
        logged = first.execute("SELECT target_id, effects FROM deletion_log WHERE target_kind='setting' ORDER BY id"
                               ).fetchall()
        assert [(name, json.loads(e)) for name, e in logged] == [
            ("ask_budget_per_epoch", {"from": 3, "to": 5, "carried_by_restore": True}),
            ("consolidation", {"from": True, "to": False, "carried_by_restore": True})]
        swept = governance.sweep(first, T0 + 8 * DAY)                        # the copy honours the opt-out
        assert swept["expired"] == 18 and "consolidated" not in swept and count(first, "evidence_consolidated") == 0
        assert first.execute("SELECT count(*) FROM forgotten_roots").fetchone()[0] == 2
        # second generation: the copy is the individual now; the older archive restored from it stays opted out
        again, second = _restored(first, oldest, tmp_path / "r2.sqlite", T0 + 8 * DAY + 1)
        with closing(second):
            assert again["settings_carried"] == {"consolidation": {"from": True, "to": False},
                                                 "ask_budget_per_epoch": {"from": 3, "to": 5}}
            assert store.config(second).consolidation is False
            assert count(second, "evidence") == 0 and count(second, "evidence_consolidated") == 0
    same, copy = _restored(db, newer, tmp_path / "r3.sqlite", T0 + 100)      # nothing to carry, and the report says so
    with closing(copy):
        assert same["settings_carried"] == {} and store.config(copy).consolidation is False
    # the owner turns it on again: an archive from the opted-out time follows the owner's present choice
    store.set_config(db, {"consolidation": True}, T0 + 110)
    back, copy = _restored(db, newer, tmp_path / "r4.sqlite", T0 + 120)
    with closing(copy):
        assert back["settings_carried"] == {"consolidation": {"from": False, "to": True}}
        assert governance.sweep(copy, T0 + 8 * DAY)["consolidated"] == 18
    # a restore that replays nothing is a plain copy of the archive, settings included
    plain = checkpoints.restore(db, older["id"], tmp_path / "plain.sqlite", T0 + 130, apply_tombstones=False)
    assert plain["settings_carried"] == {}
    with closing(store.open_existing(tmp_path / "plain.sqlite")) as copy:
        assert store.config(copy).ask_budget_per_epoch == 3


def test_a_changed_setting_is_in_the_deletion_log(instance):
    """With consolidation switched off and on again, the log shows the gap in which expired events left nothing."""
    db, _ = instance
    store.set_config(db, {"consolidation": False}, T0 + 5)
    store.set_config(db, {"consolidation": False}, T0 + 6)                   # no change, no row
    store.set_config(db, {"consolidation": True, "ask_budget_window_seconds": 0}, T0 + 9)
    rows = [(kind, name, at, json.loads(effects)) for kind, name, at, effects in db.execute(
        "SELECT target_kind, target_id, requested_at, effects FROM deletion_log ORDER BY id")]
    version = store.state_version(db)
    assert rows == [("setting", "consolidation", T0 + 5, {"from": True, "to": False, "state_version": version - 1}),
                    ("setting", "ask_budget_window_seconds", T0 + 9,
                     {"from": 7 * 86400, "to": 0, "state_version": version}),
                    ("setting", "consolidation", T0 + 9, {"from": False, "to": True, "state_version": version})]
    items = memory.retrieve(db, "deletion")["items"]
    assert [i["payload"]["target_kind"] for i in items] == ["setting"] * 3


def test_the_live_file_is_read_in_one_snapshot_for_a_restore(instance):
    """Review F4: tombstones and consolidated readings were read by separate statements outside a transaction; a
    forget committing between them gave a replay whose tombstone said 'expired' while the reading was already gone."""
    db, _ = instance
    teach(db, rounds=2)
    governance.sweep(db, T0 + 8 * DAY)
    statements = []
    db.set_trace_callback(statements.append)
    try:
        forgets = checkpoints._forgets(db, T0 + 8 * DAY + 1)
    finally:
        db.set_trace_callback(None)
    assert statements[0] == "BEGIN" and statements[-1] == "COMMIT" and not db.in_transaction
    assert len(forgets["consolidated"]) == 6 and forgets["settings"]["consolidation"] is True
    with store.write(db):                               # inside a caller's transaction it opens none of its own
        assert len(checkpoints._forgets(db, T0 + 8 * DAY + 2)["tombstones"]) == 6


def test_the_opt_out_on_a_version_13_file_is_in_place_before_anything_is_swept(tmp_path, capsys):
    """Security review L3, the upgrade path the documents give: with the services stopped, `nsf config
    --no-consolidation` as the first command migrates the file and sets the switch. It does not sweep, so events
    already past their lifetime leave nothing when the next command does."""
    from neuroscifi import cli
    path, _, _ = _version_13_file(tmp_path)
    with closing(sqlite3.connect(path)) as raw:
        raw.execute("UPDATE evidence SET expires_at=1.0")        # everything is past its lifetime at the upgrade
        raw.commit()
        assert raw.execute("SELECT count(*) FROM evidence").fetchone()[0] == 18
    assert cli.main(["config", "--db", str(path), "--no-consolidation"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["changed"] == ["consolidation"] and out["config"]["consolidation"] is False
    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert raw.execute("SELECT count(*) FROM evidence").fetchone()[0] == 18          # nothing was swept
        assert raw.execute("SELECT count(*) FROM evidence_consolidated").fetchone()[0] == 0
    assert cli.main(["status", "desk", "--db", str(path)]) == 0                          # this one sweeps first
    status = json.loads(capsys.readouterr().out)
    assert status["evidence"] == {"total": 0, "learning": 0, "memory_only": 0, "duplicates_ignored": 0}
    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("SELECT count(*) FROM evidence_consolidated").fetchone()[0] == 0
        assert raw.execute("SELECT count(*) FROM tombstones WHERE reason='expired'").fetchone()[0] == 18


def test_cli_forgets_a_reading_by_seq_and_a_camera_by_name(tmp_path, capsys):
    from neuroscifi import cli

    def nsf(*argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if code == 0 else captured.err

    path = str(tmp_path / "cli.sqlite")
    assert nsf("init", "--db", path, "--ttl-days", "0.000001")[0] == 0
    assert nsf("epoch", "start", "e", "--db", path)[0] == 0
    for n, (channel, root) in enumerate((("camera", "cam"), ("camera", "cam"), ("word", "ana"))):
        assert nsf("observe", "e", f"x{n}", channel, "0", "0", root, "--db", path)[0] == 0
    import time
    time.sleep(0.2)
    code, listed = nsf("memory", "consolidated", "--db", path)
    assert code == 0 and sorted(i["id"] for i in listed["items"]) == [1, 2, 3]
    code, out = nsf("forget", "consolidated", "3", "--db", path)
    assert code == 0 and out["status"] == "forgotten" and out["effects"]["consolidated_deleted"] == 1
    code, out = nsf("forget", "camera", "cam", "--db", path)
    assert code == 0 and out["effects"]["consolidated_deleted"] == 2 and out["effects"]["roots_forgotten"] == 1
    code, status = nsf("status", "e", "--db", path)
    assert status["sensors"] == {} and status["partners"] == {}
    with pytest.raises(SystemExit):
        cli.main(["forget", "consolidated", "not-a-number", "--db", path])
    capsys.readouterr()
    with pytest.raises(SystemExit):                     # review F7: bounded like `nsf config --ask-budget`
        cli.main(["init", "--db", str(tmp_path / "bad.sqlite"), "--ask-budget", "-5"])
    capsys.readouterr()
    assert not (tmp_path / "bad.sqlite").exists()


# ---- second-pass review: scope forgets replayed, first-build files, damaged settings, bounds -----------------------

def _same_named_camera_and_partner(db, path, tmp_path):
    """desk: a camera and a partner both called "ana", another camera, references; a confirmed change of camera ana
    and one of partner ana. Returns (archive of raw events, archive of readings)."""
    evidence.start_epoch(db, "desk", T0)
    n = 0
    for k in range(6):
        for channel, root, corrected in (("reference", f"ref-{k % 3}", 0), ("camera", "ana", 1), ("word", "ana", 0),
                                         ("camera", "c9", 0)):
            n += 1
            obs(db, f"e{n}", "desk", channel, root, corrected, T0 + n)
    with store.write(db):
        db.executemany("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                       "VALUES('desk',?,?,NULL,?,1)", [("camera:ana", 14, T0), ("partner:ana", 15, T0)])
        store.bump_state_version(db)
    raw_archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 100)
    governance.sweep(db, T0 + 8 * DAY)
    return raw_archive, checkpoints.create(db, path, tmp_path / "cps", T0 + 8 * DAY + 1)


@pytest.mark.parametrize("forget, scope, gone, stays", [
    ("forget_camera", "camera:ana", "sensors", "partners"), ("forget_partner", "partner:ana", "partners", "sensors")])
def test_a_forgotten_camera_or_partner_is_no_phantom_in_a_restored_copy(instance, tmp_path, forget, scope, gone, stays):
    """Second-pass review S2. A camera and a partner share a name, so the name stays held when one of them is
    forgotten and no root is scrubbed. The live file dropped the forgotten scope's changepoints; a restored copy kept
    them: the forgotten camera was listed as a sensor with n 0 and its old segment, counted by the executive, and a
    new camera of that name landed in the old segment. forget_partner had the same residue (in the live file too,
    since 0.7.0). The restore now replays the forget by the digest label of the deletion log, through a second
    generation."""
    db, path = instance
    archives = _same_named_camera_and_partner(db, path, tmp_path)
    assert set(beliefs.compute(db, "desk")["segments"]) == {"camera:ana", "partner:ana"}
    out = getattr(governance, forget)(db, "ana", T0 + 8 * DAY + 2)
    assert out["effects"]["consolidated_deleted"] == 6 and out["effects"]["roots_forgotten"] == 0
    assert out["effects"]["derived_deleted"] == {"changepoints": 1}
    live = beliefs.compute(db, "desk")
    kept_scope = "partner:ana" if scope == "camera:ana" else "camera:ana"
    assert "ana" not in live[gone] and "ana" in live[stays] and set(live["segments"]) == {kept_scope}
    assert "ana" not in db.execute("SELECT target_id FROM deletion_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    for n, archive in enumerate(archives):
        report, first = _restored(db, archive, tmp_path / f"r{n}.sqlite", T0 + 8 * DAY + 10 + n)
        with closing(first):
            assert report["scope_changes_removed"] == 1
            assert first.execute("SELECT scope FROM changepoints").fetchall() == [(kept_scope,)]
            copy = beliefs.compute(first, "desk")
            assert {k: copy[k] for k in BELIEF} == {k: live[k] for k in BELIEF}
            assert executive.decide(first, "desk", T0 + 9 * DAY)["reason"] == \
                executive.decide(db, "desk", T0 + 9 * DAY)["reason"]
            # a camera (or partner) of that name seen again starts from nothing, not in the forgotten segment
            channel = "camera" if scope == "camera:ana" else "word"
            obs(first, "again", "desk", channel, "ana", 1, T0 + 9 * DAY)
            again = beliefs.compute(first, "desk")[gone]["ana"]
            assert again["n"] == 1 and "segment" not in again
            if n == 0:
                continue
            # second generation: the copy took the record over; the older archive restored from it is clean too
            log = first.execute("SELECT effects FROM deletion_log WHERE target_kind=?", (scope.split(":")[0],)).fetchall()
            assert len(log) == 1 and json.loads(log[0][0])["replayed"] is True
            _, second = _restored(first, archives[0], tmp_path / "r-second.sqlite", T0 + 9 * DAY + 1)
            with closing(second):
                assert second.execute("SELECT scope FROM changepoints").fetchall() == [(kept_scope,)]
                assert "ana" not in beliefs.compute(second, "desk")[gone]


def test_a_scope_of_the_same_name_created_after_the_forget_keeps_its_changes_in_a_later_archive(instance, tmp_path):
    """The replay touches only a copy older than the forget: a camera named like a forgotten one, taught afterwards
    with a confirmed change of its own, is in an archive written after the forget and stays in its restore."""
    db, path = instance
    _same_named_camera_and_partner(db, path, tmp_path)
    governance.forget_camera(db, "ana", T0 + 8 * DAY + 2)
    seqs = [obs(db, f"new-{k}", "desk", "camera", "ana", 1, T0 + 8 * DAY + 3 + k) for k in range(4)]
    with store.write(db):
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('desk','camera:ana',?,NULL,?,1)", (seqs[2], T0))
        store.bump_state_version(db)
    later = checkpoints.create(db, path, tmp_path / "cps", T0 + 8 * DAY + 10)
    report, copy = _restored(db, later, tmp_path / "later.sqlite", T0 + 8 * DAY + 11)
    with closing(copy):
        assert report["scope_changes_removed"] == 0
        assert ("camera:ana", seqs[2]) in copy.execute("SELECT scope, since_seq FROM changepoints").fetchall()
        assert beliefs.compute(copy, "desk")["sensors"]["ana"]["segment"] == f"ana@{seqs[2]}"


def _first_build_shape(path):
    """Make a current file look as the first build of schema 14 left one: the old sample index (no seq, no value),
    tombstones by seq alone, no index by root."""
    with closing(sqlite3.connect(path, isolation_level=None)) as raw:
        raw.executescript(
            "BEGIN; DROP INDEX evidence_consolidated_copies; DROP INDEX evidence_consolidated_root; "
            "DROP INDEX tombstones_epoch_seq; "
            "CREATE INDEX evidence_consolidated_sample ON evidence_consolidated(epoch, channel, root, sample_id) "
            "WHERE sample_id IS NOT NULL; CREATE INDEX tombstones_seq ON tombstones(seq); COMMIT;")
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION


def _indexes(db):
    return {name for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('evidence_consolidated','tombstones') "
        "AND name NOT LIKE 'sqlite_%'")}


def test_a_file_migrated_by_the_first_build_of_version_14_gets_the_current_indexes_when_opened(tmp_path):
    """Second-pass review S3. Version 14 was amended in place, and a file already at 14 is never migrated again: on
    one made by the first build every web query failed with "no such index: evidence_consolidated_root". Opening
    brings the indexes to the current set; nothing else changes, and a current file is opened without a write."""
    path = tmp_path / "first-build.sqlite"
    db = store.create(path, T0)
    teach(db)
    obs(db, "s-a", "desk", "word", "ana", 0, T0 + 50, sample="s1")
    obs(db, "s-b", "desk", "word", "ana", 0, T0 + 51, sample="s1")
    governance.sweep(db, T0 + 8 * DAY)
    believed, rows, current = held(db, "desk"), folded(db), _indexes(db)
    db.close()
    _first_build_shape(path)
    with closing(sqlite3.connect(path)) as raw:      # the reviewer's reproduction: the hinted index is not there
        assert "evidence_consolidated_root" not in _indexes(raw) and "tombstones_seq" in _indexes(raw)
    db = store.open_existing(path)
    try:
        assert _indexes(db) == current == {"evidence_consolidated_epoch", "evidence_consolidated_tally",
                                           "evidence_consolidated_copies", "evidence_consolidated_root",
                                           "tombstones_epoch_seq"}
        assert governance.held_roots(db) == ["ana", "cam-a"]
        from neuroscifi.webcortex import orchestrator
        from neuroscifi.webcortex.config import WebConfig
        assert orchestrator.private_terms(db, WebConfig()) == ["ana", "cam-a"]
        assert held(db, "desk") == believed and folded(db) == rows
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert schema.ensure_indexes(db) == []                       # and now there is nothing to do
        before = db.total_changes
        cookie = db.execute("PRAGMA schema_version").fetchone()[0]
    finally:
        db.close()
    db = store.open_existing(path)
    try:
        assert db.total_changes == 0 and db.execute("PRAGMA schema_version").fetchone()[0] == cookie
        assert before >= 0
    finally:
        db.close()


def test_held_roots_names_no_index_a_version_14_file_may_lack(instance):
    """Even on a connection that never ran the open-time check, listing the held roots must not fail."""
    db, path = instance
    teach(db)
    governance.sweep(db, T0 + 8 * DAY)
    with store.write(db):
        db.execute("DROP INDEX evidence_consolidated_root")
    assert governance.held_roots(db) == ["ana", "cam-a"] and governance.root_held(db, "cam-a")


def _damage_settings(db, change):
    raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
    with store.write(db):
        db.execute("UPDATE meta SET value=? WHERE key='config'",
                   (change if isinstance(change, str) else json.dumps(raw | change),))


@pytest.mark.parametrize("saved", ["no", "false", 0, 1, "yes", None, []])
def test_consolidation_is_never_on_by_truthiness(instance, saved):
    """Second-pass review S4. A hand-edited `"consolidation": "no"` is a truthy string and counted as on. A saved
    value that is not a JSON boolean now reads as off (nothing is kept that an owner may have opted out of), every
    sweep says so, and `nsf config` repairs it."""
    db, _ = instance
    teach(db, rounds=2)
    _damage_settings(db, {"consolidation": saved})
    assert store.config(db).consolidation is False
    problems = store.setting_problems(db)
    assert len(problems) == 1 and "consolidation" in problems[0] and "treated as off" in problems[0]
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["expired"] == 6 and "consolidated" not in out and out["warnings"] == problems
    assert count(db, "evidence_consolidated") == 0
    assert store.set_config(db, {"consolidation": False}, T0 + 9 * DAY)["changed"] == ["consolidation"]   # repaired
    assert store.setting_problems(db) == [] and "warnings" not in governance.sweep(db, T0 + 9 * DAY)
    assert json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])["consolidation"] is False


def test_a_restore_validates_the_settings_it_carries(instance, tmp_path):
    """Second-pass review S4: `"consolidation": "no"` was carried as "no" (and counted as on in the copy), a string
    ask budget as a string. A value set_config would refuse is not carried as it stands: consolidation goes over as
    what the live file acts on (off), the others stay as the archive has them, and the result says which."""
    db, path = instance
    teach(db)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)
    _damage_settings(db, {"consolidation": "no", "ask_budget_per_epoch": "3", "ask_budget_window_seconds": -5})
    report, copy = _restored(db, archive, tmp_path / "r.sqlite", T0 + 70)
    with closing(copy):
        assert report["settings_carried"] == {"consolidation": {"from": True, "to": False}}
        warnings = " | ".join(report["settings_warnings"])
        assert len(report["settings_warnings"]) == 3 and "consolidation ('no')" in warnings
        assert "ask_budget_per_epoch ('3')" in warnings and "ask_budget_window_seconds (-5)" in warnings
        saved = json.loads(copy.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        assert saved["consolidation"] is False and saved["ask_budget_per_epoch"] == 3
        assert saved["ask_budget_window_seconds"] == 7 * 86400 and store.setting_problems(copy) == []
        assert "consolidated" not in governance.sweep(copy, T0 + 8 * DAY)
        assert executive.decide(copy, "desk", T0 + 8 * DAY + 1)["ask_budget"] == 3


@pytest.mark.parametrize("row", ["{not json", "[1, 2]", ""])
def test_a_restore_survives_a_live_settings_row_that_cannot_be_read(instance, tmp_path, row):
    """Second-pass review S4. Restore is the recovery tool: before it carried settings it never read the row, and it
    must not start failing on a damaged one. Nothing is carried, the result says so, and the forgets still apply."""
    db, path = instance
    ids = teach(db)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)
    governance.forget_evidence(db, ids[0], T0 + 61)
    _damage_settings(db, row)
    report, copy = _restored(db, archive, tmp_path / "r.sqlite", T0 + 70)
    with closing(copy):
        assert report["settings_carried"] == {} and len(report["settings_warnings"]) == 1
        assert "could not be read" in report["settings_warnings"][0]
        assert report["evidence_removed"] == 1 and count(copy, "evidence") == 17
        assert store.config(copy).consolidation is True          # the archive's own, sound settings


def test_the_replay_survives_a_copy_whose_settings_row_cannot_be_read(instance, tmp_path):
    """The archive's own settings row may be the damaged one (a checkpoint cannot be made from such a file, but an
    archive can be damaged later): the replay writes no setting into the copy, says so, and applies the forgets."""
    db, path = instance
    ids = teach(db)
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 60)
    checkpoints.restore(db, archive["id"], tmp_path / "copy.sqlite", T0 + 61, apply_tombstones=False)
    governance.forget_evidence(db, ids[0], T0 + 62)
    store.set_config(db, {"consolidation": False}, T0 + 63)
    with closing(sqlite3.connect(tmp_path / "copy.sqlite", isolation_level=None)) as copy:
        copy.execute("UPDATE meta SET value='{not json' WHERE key='config'")
        report = checkpoints._replay(copy, checkpoints._forgets(db, T0 + 64), T0 + 64)
        assert report["settings_carried"] == {}
        assert "archive's settings could not be read" in report["settings_warnings"][0]
        assert report["evidence_removed"] == 1
        assert copy.execute("SELECT count(*) FROM evidence").fetchone()[0] == 17


@pytest.mark.parametrize("seq", [2 ** 63, 2 ** 64, 10 ** 30])
def test_a_seq_or_dataset_id_beyond_sqlite_is_refused_not_an_overflow(instance, seq):
    """Second-pass review S5: forget_consolidated(2**63) raised OverflowError (the console answered 500)."""
    from neuroscifi.instance.errors import NotFound, ValidationError
    db, _ = instance
    for forget in (governance.forget_consolidated, governance.forget_dataset):
        with pytest.raises(ValidationError):
            forget(db, seq, T0)
    assert governance.forget_consolidated(db, 2 ** 63 - 1, T0)["status"] == "absent"
    with pytest.raises(NotFound):
        governance.forget_dataset(db, 2 ** 63 - 1, T0)
    with pytest.raises(ValidationError):
        governance.forget_dataset(db, True, T0)


def test_cli_config_reports_a_damaged_setting(tmp_path, capsys):
    from neuroscifi import cli
    path = tmp_path / "cli.sqlite"
    db = store.create(path, T0)
    _damage_settings(db, {"consolidation": "no"})
    db.close()
    assert cli.main(["config", "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["config"]["consolidation"] is False and "treated as off" in out["warnings"][0]
    assert cli.main(["config", "--db", str(path), "--no-consolidation"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["changed"] == ["consolidation"] and "warnings" not in out
