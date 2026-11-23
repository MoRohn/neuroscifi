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


def obs(db, event_id, epoch, channel, root, corrected, now, *, action=1, sample=None, consent="learn"):
    record = {"id": event_id, "epoch": epoch, "channel": channel, "action": action, "bit": corrected ^ action,
              "root": root, "consent": consent}
    if sample is not None:
        record["sample_id"] = sample
    return evidence.observe(db, record, now)["seq"]


def held(db, epoch):
    result = beliefs.compute(db, epoch)
    return {key: result[key] for key in BELIEF}


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
    promoted_learning = count(db, "evidence", beliefs.learning_sql(db))
    assert count(db, "evidence_consolidated") + promoted_learning == learning
    assert count(db, "evidence_consolidated") > 60
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


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
    assert out["expired"] == 4 and out["consolidated"] == 2
    assert [(r[3], r[4], r[5]) for r in folded(db)] == [("cam-a", 1, "frame-1"), ("cam-a", 1, "frame-2")]
    assert beliefs.compute(db, "desk")["sensors"] == before["sensors"]
    # a copy of a consolidated sample arriving later is a duplicate, not a second reading
    obs(db, "third", "desk", "camera", "cam-a", 1, T0 + 9 * DAY, sample="frame-1")
    obs(db, "other-root", "desk", "camera", "cam-b", 1, T0 + 9 * DAY, sample="frame-1")  # another root: its own
    now = beliefs.compute(db, "desk")
    assert now["sensors"]["cam-a"] == before["sensors"]["cam-a"] and now["sensors"]["cam-b"]["n"] == 1
    assert now["evidence"] == {"total": 4, "learning": 3, "memory_only": 0, "duplicates_ignored": 1,
                               "consolidated": 2}
    assert executive._new_events(db, "desk", "camera:cam-a", 0) == 2
    again = governance.sweep(db, T0 + 20 * DAY)
    assert again["expired"] == 2 and again["consolidated"] == 1                          # the copy leaves nothing
    assert count(db, "evidence_consolidated") == 3
    assert beliefs.compute(db, "desk")["sensors"] == now["sensors"]


def test_a_promoted_first_copy_keeps_the_reading_and_a_later_copy_expires_to_nothing(instance):
    db, _ = instance
    evidence.start_epoch(db, "desk", T0)
    obs(db, "kept", "desk", "word", "ana", 0, T0, sample="w1")
    governance.promote(db, "kept", T0 + 1)
    obs(db, "copy", "desk", "word", "ana", 0, T0 + 2, sample="w1")
    out = governance.sweep(db, T0 + 8 * DAY)
    assert out["expired"] == 1 and "consolidated" not in out and count(db, "evidence_consolidated") == 0
    assert beliefs.compute(db, "desk")["partners"]["ana"]["n"] == 1


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
    assert governance.held_roots(db) == ["cam-new", "cam-a", "ana"]
    assert orchestrator.private_terms(db, WebConfig(private_terms=["mine"])) == ["mine", "cam-new", "cam-a", "ana"]


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
    raw.executescript("BEGIN; DROP TABLE evidence_consolidated; DROP INDEX tombstones_seq; "
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
            {"evidence_consolidated_epoch", "evidence_consolidated_sample", "evidence_consolidated_tally"}
        assert db.execute("SELECT tbl_name FROM sqlite_master WHERE name='tombstones_seq'").fetchone() == ("tombstones",)
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
