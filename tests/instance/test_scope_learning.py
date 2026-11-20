"""Scope-limited relearning after a confirmed change, rejected changes, stale status, epoch matching and speed.

The brief's design question: a person says 'left' as a camera is remounted. The learner must name the
changed mapping, keep the person's meaning, and still operate the original setup later.
"""
import itertools
import json
import math
import random
import statistics
import time

import numpy as np
import pytest

from neuroscifi import cli
from neuroscifi.instance import beliefs, evidence, executive, governance, store
from neuroscifi.instance.beliefs import _bernoulli, _lse, _logistic
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.lab.source_groups import group_log_likelihood

from conftest import T0


class Journal:
    def __init__(self, db, epoch="desk"):
        self.db, self.epoch, self.count = db, epoch, 0
        evidence.start_epoch(db, epoch, T0)

    def add(self, channel, root, corrected, **extra):
        self.count += 1
        action = self.count % 2
        obs = {"id": f"{self.epoch}-{self.count:04d}", "epoch": self.epoch, "channel": channel,
               "action": action, "bit": corrected ^ action, "root": root, **extra}
        return evidence.observe(self.db, obs, T0 + self.count)["seq"]


def baseline(j, rounds=12, camera=0, partner=0, mechanism=0):
    for k in range(rounds):
        j.add("camera", "cam-a", mechanism ^ camera)
        j.add("word", "ana", mechanism ^ partner)
        j.add("reference", f"ref-{k % 3}", mechanism)


def remount(j, rounds=6):
    """The camera is remounted: its readings flip; Ana and the references are unchanged."""
    first = None
    for k in range(rounds):
        seq = j.add("camera", "cam-a", 1)
        first = first or seq
        j.add("word", "ana", 0)
        j.add("reference", f"ref-{k % 3}", 0)
    return first


# ---- 1. the remount loop ------------------------------------------------------------------------------

def test_confirmed_remount_relearns_only_the_camera(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    first = remount(j)
    stale = beliefs.compute(db, "desk")
    # P2: before the owner answers, the confident camera reading is flagged as possibly stale.
    assert stale["sensors"]["cam-a"]["suspected_change"] is True
    assert stale["sensors"]["cam-a"]["suspected_since_seq"] == first and stale["sensors"]["cam-a"]["llr"] > 2.94
    assert "suspected_change" not in stale["partners"]["ana"]
    asked = executive.decide(db, "desk", T0 + 100, record=True)
    assert asked["decision"] == "ask" and asked["scope"] == "camera:cam-a"
    question = executive.pending(db, epoch="desk")[0]["question"]
    assert f"since evidence #{first}" in question

    done = executive.resolve(db, asked["pending_id"], "yes, I remounted it", T0 + 101, confirm=True)
    assert done["changepoint"]["scope"] == "camera:cam-a" and done["changepoint"]["since_seq"] == first
    assert done["changepoint"]["confirmed"] is True

    after = beliefs.compute(db, "desk")
    assert after["confirmed_changes"] == [{"scope": "camera:cam-a", "since_seq": first,
                                           "pending_id": asked["pending_id"], "created_at": T0 + 101}]
    camera = after["sensors"]["cam-a"]
    assert camera["segment"] == f"cam-a@{first}" and camera["n"] == 6 and camera["p_inverted"] > .99
    assert "suspected_change" not in camera and not after["change"]["suspected"]
    old, new = after["segments"]["camera:cam-a"]
    assert (old["segment"], old["n"], new["n"]) == ("cam-a@0", 12, 6) and old["p_inverted"] < .01
    # The partner's meaning and the mechanism carry over: they keep pooling all 18 rounds.
    assert after["partners"]["ana"]["n"] == 18 and after["partners"]["ana"]["p_inverted"] < .01
    assert after["mechanism"]["p_inverted"] < .01 and after["mechanism"]["identifiable"]
    assert after["legacy"] is None   # the prototype cannot represent segments
    out = executive.decide(db, "desk", T0 + 102, record=True)
    assert out["decision"] == "act" and out["asks_used"] == 1
    # Explanations name the old segment separately.
    scopes = [f["scope"] for f in beliefs.explain(db, "desk")["factors"]]
    assert scopes == ["mechanism", "camera:cam-a@0", "camera:cam-a", "partner:ana"]
    revision = db.execute("SELECT cause FROM belief_revisions ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert revision == f"resolve:{asked['pending_id']}"


def test_before_the_fix_the_question_repeated_until_the_budget_ran_out(instance):
    """Regression for the reported loop: an answered question is not re-asked for the same events."""
    db, _ = instance
    j = Journal(db)
    baseline(j)
    remount(j)
    first = executive.decide(db, "desk", T0 + 100, record=True)
    executive.resolve(db, first["pending_id"], "hm, maybe", T0 + 101)          # neither yes nor no
    for n in range(5):
        out = executive.decide(db, "desk", T0 + 102 + n, record=True)
        assert out["decision"] == "defer" and out["pending_id"] is None and out["asks_used"] == 1
        assert "answered without confirming or rejecting" in out["reason"]
    status = beliefs.compute(db, "desk")
    assert status["sensors"]["cam-a"]["suspected_change"] is True     # never a confident, stale 'act'
    for _ in range(3):
        j.add("camera", "cam-a", 1)
    again = executive.decide(db, "desk", T0 + 200, record=True)
    assert again["decision"] == "ask" and again["scope"] == "camera:cam-a" and again["asks_used"] == 2


def test_rejected_change_is_quiet_until_three_new_events(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    remount(j)
    asked = executive.decide(db, "desk", T0 + 100, record=True)
    done = executive.resolve(db, asked["pending_id"], "no, nothing moved", T0 + 101, confirm=False)
    horizon = db.execute("SELECT max(seq) FROM evidence").fetchone()[0]
    assert done["changepoint"] == {"id": 1, "epoch": "desk", "scope": "camera:cam-a", "since_seq": horizon,
                                   "created_at": T0 + 101, "confirmed": False}
    change = beliefs.detect_change(db, "desk")
    assert change["suppressed"] == ["camera:cam-a"] and change["scope"] != "camera:cam-a"
    status = beliefs.compute(db, "desk")
    assert "suspected_change" not in status["sensors"]["cam-a"] and status["confirmed_changes"] == []
    assert status["sensors"]["cam-a"]["n"] == 18            # still pooled
    j.add("camera", "cam-a", 1)
    j.add("camera", "cam-a", 1)
    assert beliefs.detect_change(db, "desk")["suppressed"] == ["camera:cam-a"]
    j.add("camera", "cam-a", 1)
    change = beliefs.detect_change(db, "desk")
    assert change["suppressed"] == [] and change["suspected"] and change["scope"] == "camera:cam-a"
    again = executive.decide(db, "desk", T0 + 200, record=True)
    assert again["decision"] == "ask" and again["asks_used"] == 2


def test_confirmed_mechanism_change_is_piecewise_and_capped(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    first = j.add("camera", "cam-a", 1)
    for k in range(6):
        if k:
            j.add("camera", "cam-a", 1)
        j.add("word", "ana", 1)
        j.add("reference", f"ref-{k % 3}", 1)
    asked = executive.decide(db, "desk", T0 + 100, record=True)
    assert asked["scope"] == "mechanism"
    executive.resolve(db, asked["pending_id"], "the gearbox was swapped", T0 + 101, confirm=True)
    after = beliefs.compute(db, "desk")
    assert after["mechanism"]["segment"] == f"mechanism@{first}" and after["mechanism"]["p_inverted"] > .99
    before, now = after["segments"]["mechanism"]
    assert before["p_inverted"] < .01 and (before["n"], now["n"]) == (36, 18)
    # Alignment and convention carry over across the mechanism change.
    assert after["sensors"]["cam-a"]["p_inverted"] < .01 and after["partners"]["ana"]["p_inverted"] < .01
    assert "segment" not in after["sensors"]["cam-a"] and not after["change"]["suspected"]
    assert executive.decide(db, "desk", T0 + 102)["decision"] == "act"
    with store.write(db):
        for since in range(1000, 1004):     # four more mechanism changes: six segments in total
            db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                       "VALUES('desk','mechanism',?,NULL,?,1)", (since, T0))
    assert len(beliefs.compute(db, "desk")["segments"]["mechanism"]) == 6
    j.add("camera", "cam-a", 1)
    with store.write(db):
        pid = db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES('desk','calibrate',"
                         "'mechanism','Did mechanism change since evidence #2000?',?)", (T0,)).lastrowid
    with pytest.raises(ValidationError, match="mechanism segments"):
        executive.resolve(db, pid, "yes", T0 + 300, confirm=True)
    with store.write(db):
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('desk','mechanism',2000,NULL,?,1)", (T0,))
    with pytest.raises(ValidationError, match="more than 6 mechanism segments"):
        beliefs.compute(db, "desk")


def test_resolve_confirm_validation_and_idempotence(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    for corrected in (0, 1, 0, 1):
        j.add("word", "ana", corrected)
    meaning = executive.decide(db, "desk", T0, record=True)          # rule 6: not about a change
    with pytest.raises(ValidationError, match="none is suspected"):
        executive.resolve(db, meaning["pending_id"], "yes", T0, confirm=True)
    with pytest.raises(ValidationError, match="confirm"):
        executive.resolve(db, meaning["pending_id"], "yes", T0, confirm="yes")
    assert executive.pending(db)[0]["resolved_at"] is None          # nothing was written
    baseline(j)
    remount(j)
    asked = executive.decide(db, "desk", T0 + 1, record=True)
    done = executive.resolve(db, asked["pending_id"], "remounted", T0 + 2, confirm=True)
    assert executive.resolve(db, asked["pending_id"], "remounted", T0 + 3, confirm=True) == done
    assert executive.resolve(db, asked["pending_id"], "remounted", T0 + 3) == done
    with pytest.raises(Exception, match="already resolved"):
        executive.resolve(db, asked["pending_id"], "remounted", T0 + 3, confirm=False)
    assert db.execute("SELECT count(*) FROM changepoints").fetchone()[0] == 1


# ---- 2. exactness ---------------------------------------------------------------------------------------

def _old_learning(db, epoch):
    """The per-event loader the aggregates replaced (seq order, first copy of a sample)."""
    rows = [beliefs.event_dict(r) for r in db.execute(
        f"SELECT {','.join(beliefs.EVENT_COLUMNS)} FROM evidence WHERE epoch=? ORDER BY seq", (epoch,))]
    seen, learning = set(), []
    for event in rows:
        if event["consent"] != "learn":
            continue
        key = (event["channel"], event["root"], event["sample_id"])
        if event["sample_id"] is not None and key in seen:
            continue
        seen.add(key)
        learning.append(event)
    return learning


def _old_infer(readings):
    """The per-event inference as it was before the aggregates (kept verbatim as the reference)."""
    tallies = {}
    for channel, root, reading in readings:
        tallies.setdefault((channel, root), [0, 0])[reading] += 1
    log_w = [0.0, 0.0]
    conditional = {}
    for (channel, root), (n0, n1) in tallies.items():
        if channel == "reference":
            group = np.array([[1]] * n1 + [[0]] * n0)
            values = group_log_likelihood(group, .15, .10)[0]
            for w in (0, 1):
                log_w[w] += float(values[beliefs._W_ROW[w]])
            continue
        given_w = []
        for w in (0, 1):
            ll = [_bernoulli(n0, n1, w ^ h) for h in (0, 1)]
            log_w[w] += math.log(.5) + _lse(ll[0], ll[1])
            given_w.append(_logistic(ll[1] - ll[0]))
        conditional[(channel, root)] = (given_w, n0 + n1)
    return {"log_marginal": math.log(.5) + _lse(log_w[0], log_w[1]), "p_w": _logistic(log_w[1] - log_w[0]),
            "conditional": conditional}


def _old_detect(db, epoch):
    window = _old_learning(db, epoch)[-64:]
    readings = [(e["channel"], e["root"], e["bit"] ^ e["action"]) for e in window]
    base = _old_infer(readings)["log_marginal"]
    scopes = {"mechanism": list(range(len(window)))}
    for i, e in enumerate(window):
        if e["channel"] != "reference":
            scopes.setdefault(beliefs.scope_of(e["channel"], e["root"]), []).append(i)
    best, tried = {}, 0
    for scope, affected in scopes.items():
        for k in range(1, len(affected) - 2):
            flipped = set(affected[k:])
            gain = _old_infer([(c, r, x ^ (i in flipped)) for i, (c, r, x) in enumerate(readings)])["log_marginal"] - base
            tried += 1
            if scope not in best or gain > best[scope][0]:
                best[scope] = (gain, window[affected[k]]["seq"])
    return best, tried


def test_without_changepoints_results_equal_the_per_event_code_exactly(instance):
    db, _ = instance
    rng = random.Random(77)
    for n in range(40):
        epoch = f"e{n}"
        evidence.start_epoch(db, epoch, T0)
        bias = {c: rng.random() for c in ("camera", "word", "reference")}
        for i in range(rng.randrange(0, 90)):
            channel = rng.choice(["camera", "word", "reference"])
            root = {"camera": f"cam{rng.randrange(2)}", "word": f"p{rng.randrange(3)}"}.get(channel) \
                or f"ref{rng.randrange(4)}"
            obs = {"id": f"{epoch}-{i}", "epoch": epoch, "channel": channel, "action": rng.randrange(2),
                   "bit": int(rng.random() < bias[channel]), "root": root}
            if rng.random() < .2:
                obs["sample_id"] = f"s{rng.randrange(5)}"
            if rng.random() < .1:
                obs["consent"] = "memory_only"
            evidence.observe(db, obs, T0)
        learning = _old_learning(db, epoch)
        old = _old_infer([(e["channel"], e["root"], e["bit"] ^ e["action"]) for e in learning])
        new = beliefs.compute(db, epoch)
        assert new["mechanism"]["p_inverted"] == old["p_w"]
        assert new["evidence"]["learning"] == len(learning)
        for (channel, root), (given, count) in old["conditional"].items():
            group = new["sensors" if channel == "camera" else "partners"][root]
            assert group["p_inverted"] == (1 - old["p_w"]) * given[0] + old["p_w"] * given[1]
            assert group["n"] == count
        best, tried = _old_detect(db, epoch)
        change = beliefs.detect_change(db, epoch)
        assert change["tried"] == tried
        if best:
            top = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
            assert change["llr"] == top[0][1][0] - math.log(tried)
            assert {a["scope"]: a["llr"] for a in change["alternatives"]} == {
                s: g - math.log(tried) for s, (g, _) in best.items() if s != change["scope"]} or not change["suspected"]


def _brute_force(events, mechanism_bounds, camera_bounds):
    """Enumerate every hidden bit: w per mechanism segment, one bit per camera/partner segment, a fixture
    polarity per reference root. Returns (P(w_current=1), {factor segment: P(bit=1)})."""
    def seg(bounds, seq):
        return sum(seq >= b for b in bounds)
    factors = sorted({(e["channel"], e["root"], seg(camera_bounds.get(e["root"], []) if e["channel"] == "camera"
                                                      else [], e["seq"])) for e in events if e["channel"] != "reference"})
    roots = sorted({e["root"] for e in events if e["channel"] == "reference"})
    m = len(mechanism_bounds) + 1
    total, w_mass, bit_mass = 0.0, 0.0, {f: 0.0 for f in factors}
    for ws in itertools.product((0, 1), repeat=m):
        for hs in itertools.product((0, 1), repeat=len(factors)):
            h = dict(zip(factors, hs))
            for fs in itertools.product((0, 1), repeat=len(roots)):
                polarity = dict(zip(roots, fs))
                p = .5 ** (m + len(factors)) * math.prod(.15 if f else .85 for f in fs)
                for e in events:
                    w = ws[seg(mechanism_bounds, e["seq"])]
                    if e["channel"] == "reference":
                        predicted = w ^ polarity[e["root"]]
                    else:
                        bounds = camera_bounds.get(e["root"], []) if e["channel"] == "camera" else []
                        predicted = w ^ h[(e["channel"], e["root"], seg(bounds, e["seq"]))]
                    p *= .9 if (e["bit"] ^ e["action"]) == predicted else .1
                total += p
                w_mass += p * ws[-1]
                for f in factors:
                    bit_mass[f] += p * h[f]
    return w_mass / total, {f: v / total for f, v in bit_mass.items()}


def test_segmented_inference_equals_brute_force_enumeration(instance):
    db, _ = instance
    rng = random.Random(5)
    j = Journal(db)
    for _ in range(14):
        channel = rng.choice(["camera", "word", "reference"])
        root = {"camera": "cam-a", "word": "ana"}.get(channel) or rng.choice(["ref-1", "ref-2"])
        j.add(channel, root, int(rng.random() < .3))
    with store.write(db):
        beliefs.ensure_tables(db)
        for scope, since in (("mechanism", 6), ("camera:cam-a", 4), ("camera:cam-a", 10)):
            db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                       "VALUES('desk',?,?,NULL,?,1)", (scope, since, T0))
    events = _old_learning(db, "desk")
    p_w, bits = _brute_force(events, [6], {"cam-a": [4, 10]})
    result = beliefs.compute(db, "desk")
    assert result["mechanism"]["p_inverted"] == pytest.approx(p_w, abs=1e-12)
    segments = {row["segment"]: row["p_inverted"] for row in result["segments"]["camera:cam-a"]}
    assert segments == pytest.approx({"cam-a@0": bits[("camera", "cam-a", 0)], "cam-a@4": bits[("camera", "cam-a", 1)],
                                      "cam-a@10": bits[("camera", "cam-a", 2)]}, abs=1e-12)
    assert result["sensors"]["cam-a"]["p_inverted"] == pytest.approx(bits[("camera", "cam-a", 2)], abs=1e-12)
    assert result["partners"]["ana"]["p_inverted"] == pytest.approx(bits[("word", "ana", 0)], abs=1e-12)
    json.dumps(beliefs.explain(db, "desk"), allow_nan=False)


# ---- 3. the original setup returns --------------------------------------------------------------------

def remounted_desk(db):
    """desk: 12 rounds with cam-a relation 0, then a remount (relation 1) the owner confirmed. Returns its seq."""
    j = Journal(db, "desk")
    baseline(j)
    first = remount(j)
    asked = executive.decide(db, "desk", T0 + 100, record=True)
    assert asked["scope"] == "camera:cam-a"
    executive.resolve(db, asked["pending_id"], "yes, I remounted it", T0 + 101, confirm=True)
    return first


def original_readings(j, rounds=4, references=True):
    """The ORIGINAL desk mapping: cam-a relation 0, Ana relation 0, references at w = 0."""
    for k in range(rounds):
        j.add("camera", "cam-a", 0)
        j.add("word", "ana", 0)
        if references:
            j.add("reference", f"ref-{k % 3}", 0)


def _brute_new_setup(events):
    """P(readings) under a new setup: sum over w, one bit per camera root and partner, one polarity per reference root."""
    bits = sorted({(e["channel"], e["root"]) for e in events if e["channel"] != "reference"})
    roots = sorted({e["root"] for e in events if e["channel"] == "reference"})
    total = 0.0
    for w in (0, 1):
        for hs in itertools.product((0, 1), repeat=len(bits)):
            h = dict(zip(bits, hs))
            for fs in itertools.product((0, 1), repeat=len(roots)):
                polarity = dict(zip(roots, fs))
                p = .5 ** (1 + len(bits)) * math.prod(.15 if f else .85 for f in fs)
                for e in events:
                    bit = polarity[e["root"]] if e["channel"] == "reference" else h[(e["channel"], e["root"])]
                    p *= .9 if (e["bit"] ^ e["action"]) == w ^ bit else .1
                total += p
    return total


def test_segment_candidates_recognise_the_setup_before_a_confirmed_remount(instance):
    """After a confirmed remount, desk's CURRENT camera segment is the remounted one; its earlier segment cam-a@0
    must still be a candidate, and the evidence comes from camera, partner and references jointly."""
    db, _ = instance
    first = remounted_desk(db)
    tomorrow = Journal(db, "tomorrow")
    original_readings(tomorrow)
    out = beliefs.match_epochs(db, "tomorrow")
    (top,) = out["ranking"]
    assert out["best"] == "desk" and out["events"] == 12 and top["events"] == 12
    assert top["segments"] == {"mechanism": "mechanism@0", "camera:cam-a": "cam-a@0", "partner:ana": "ana@0"}
    # Each segment predicts with its MAP value: 4 camera and 4 word readings at .9, references at MAP w = 0 with the
    # fixture polarity integrated (ref-0 has two readings: .85 * .9^2 + .15 * .1^2 = .69; one reading: .78),
    # minus log 2 for picking one of cam-a's two segments.
    assert top["penalty"] == pytest.approx(math.log(2), abs=1e-12)
    expected = 8 * math.log(.9) + math.log(.85 * .81 + .15 * .01) + 2 * math.log(.78) - math.log(2)
    assert top["log_likelihood"] == pytest.approx(expected, abs=1e-12)
    # The new-setup alternative is exact: brute-force enumeration of every hidden bit.
    events = _old_learning(db, "tomorrow")
    assert out["new_setup_log_likelihood"] == pytest.approx(math.log(_brute_new_setup(events)), abs=1e-12)
    posterior = .5 * math.exp(expected) / (.5 * math.exp(expected) + .5 * _brute_new_setup(events))
    assert top["posterior"] == pytest.approx(posterior, abs=1e-12) and out["best_posterior"] == top["posterior"]
    assert top["posterior"] >= beliefs.MATCH_POSTERIOR == .75
    assert top["posterior"] + out["new_setup_posterior"] == pytest.approx(1, abs=1e-12)
    # The executive names the segment.
    asked = executive.decide(db, "tomorrow", T0 + 200, record=True)
    assert asked["decision"] == "ask" and asked["scope"] == "epoch:desk"
    (question,) = executive.pending(db, epoch="tomorrow")
    assert question["question"].startswith(
        f"This looks like desk before the camera:cam-a change at #{first}; continue with that calibration?")


def test_without_references_a_candidate_gains_at_most_a_factor_two_per_root(instance):
    """A new setup matches each camera/partner relation half the time, so camera + partner readings alone can never
    reach 3:1 against it once cam-a's two segments cost log 2: here the posterior is (r^2 / 2) / (r^2 / 2 + 1)."""
    db, _ = instance
    remounted_desk(db)
    tomorrow = Journal(db, "tomorrow")
    original_readings(tomorrow, rounds=6, references=False)
    out = beliefs.match_epochs(db, "tomorrow")
    r = .9 ** 6 / (.5 * (.9 ** 6 + .1 ** 6))
    assert out["ranking"][0]["segments"]["camera:cam-a"] == "cam-a@0"
    assert out["ranking"][0]["posterior"] == pytest.approx((r * r / 2) / (r * r / 2 + 1), abs=1e-12)
    assert out["ranking"][0]["posterior"] < 2 / 3 < beliefs.MATCH_POSTERIOR
    assert out["best"] is None
    words = Journal(db, "words-only")
    for _ in range(6):
        words.add("word", "ana", 0)
    assert beliefs.match_epochs(db, "words-only")["best"] is None
    unseen = Journal(db, "elsewhere")
    unseen.add("camera", "cam-z", 0)
    elsewhere = beliefs.match_epochs(db, "elsewhere")   # an unseen root is fresh under every candidate
    assert elsewhere["best"] is None and all(r["events"] == 0 for r in elsewhere["ranking"])
    assert elsewhere["new_setup_posterior"] == pytest.approx(.5, abs=1e-12)
    with pytest.raises(ValidationError):
        beliefs.match_epochs(db, "tomorrow", last=0)


def test_rival_epochs_share_the_prior_mass(instance):
    """'Some earlier configuration' gets 1/2, split evenly: two epochs that both fit share it."""
    db, _ = instance
    baseline(Journal(db, "desk"))
    baseline(Journal(db, "remounted"), camera=1)
    tomorrow = Journal(db, "tomorrow")
    original_readings(tomorrow)
    out = beliefs.match_epochs(db, "tomorrow")
    assert [r["epoch"] for r in out["ranking"]] == ["desk", "remounted"] and out["best"] == "desk"
    assert out["ranking"][1]["segments"]["camera:cam-a"] == "cam-a@0"
    z = .5 * math.exp(out["new_setup_log_likelihood"]) + sum(.25 * math.exp(r["log_likelihood"]) for r in out["ranking"])
    assert out["ranking"][0]["posterior"] == pytest.approx(.25 * math.exp(out["ranking"][0]["log_likelihood"]) / z,
                                                           abs=1e-12)
    twin = Journal(db, "twin")      # the same setup as desk taught again: now two epochs explain tomorrow
    baseline(twin)
    split = beliefs.match_epochs(db, "tomorrow")
    assert split["best"] is None
    assert split["ranking"][0]["posterior"] == pytest.approx(split["ranking"][1]["posterior"], abs=1e-12)


def _linked_union(db, source, first, epoch):
    """desk's learning events in the linked segments (cam-a before the remount, Ana, references) plus epoch's own."""
    linked = [e for e in _old_learning(db, source) if e["channel"] != "camera" or e["seq"] < first]
    return linked + _old_learning(db, epoch)


def test_continue_there_starts_from_the_earlier_calibration(instance):
    db, _ = instance
    first = remounted_desk(db)
    tomorrow = Journal(db, "tomorrow")
    original_readings(tomorrow)
    own = beliefs.compute(db, "tomorrow")
    assert own["linked_from"] is None and own["sensors"]["cam-a"]["n"] == 4
    asked = executive.decide(db, "tomorrow", T0 + 200, record=True)
    done = executive.resolve(db, asked["pending_id"], "yes, back at the desk", T0 + 201, confirm=True)
    segments = {"mechanism": "mechanism@0", "camera:cam-a": "cam-a@0", "partner:ana": "ana@0"}
    assert done["changepoint"] is None
    assert done["link"] == {"epoch": "tomorrow", "source_epoch": "desk", "segments": segments,
                            "pending_id": asked["pending_id"], "created_at": T0 + 201}
    assert executive.resolve(db, asked["pending_id"], "yes, back at the desk", T0 + 202, confirm=True)["link"] == \
        done["link"]
    with pytest.raises(Conflict):
        executive.resolve(db, asked["pending_id"], "yes, back at the desk", T0 + 202, confirm=False)
    after = beliefs.compute(db, "tomorrow")
    assert after["linked_from"] == {"epoch": "desk", "segments": segments, "pending_id": asked["pending_id"],
                                    "created_at": T0 + 201, "learning": 12 + 18 + 18}
    assert after["evidence"]["learning"] == 12 and after["legacy"] is None
    # compute(tomorrow) is exactly inference on the union of its own evidence and desk's linked segments.
    p_w, bits = _brute_force(_linked_union(db, "desk", first, "tomorrow"), [], {})
    assert after["mechanism"]["p_inverted"] == pytest.approx(p_w, abs=1e-12)
    assert after["sensors"]["cam-a"]["p_inverted"] == pytest.approx(bits[("camera", "cam-a", 0)], abs=1e-12)
    assert after["partners"]["ana"]["p_inverted"] == pytest.approx(bits[("word", "ana", 0)], abs=1e-12)
    assert after["sensors"]["cam-a"]["n"] == 12 + 4 and after["sensors"]["cam-a"]["p_inverted"] < .01
    # Linked: rule 4b does not ask again, and the earlier skill is executed (mechanism identified -> act).
    again = executive.decide(db, "tomorrow", T0 + 203, record=True)
    assert again["decision"] == "act" and again["asks_used"] == 1

    # Forgetting source evidence changes the linked beliefs on the next read, and records a revision.
    victims = [e["id"] for e in _old_learning(db, "desk") if e["channel"] == "camera" and e["seq"] < first][:8]
    for k, event_id in enumerate(victims):
        out = governance.forget_evidence(db, event_id, T0 + 300 + k)
        assert out["effects"]["linked_epochs_revised"] == ["tomorrow"]
    reduced = beliefs.compute(db, "tomorrow")
    assert reduced["sensors"]["cam-a"]["n"] == 4 + 4 and reduced["linked_from"]["learning"] == 48 - 8
    assert reduced["sensors"]["cam-a"]["p_inverted"] != after["sensors"]["cam-a"]["p_inverted"]
    p_w, bits = _brute_force(_linked_union(db, "desk", first, "tomorrow"), [], {})
    assert reduced["mechanism"]["p_inverted"] == pytest.approx(p_w, abs=1e-12)
    assert reduced["sensors"]["cam-a"]["p_inverted"] == pytest.approx(bits[("camera", "cam-a", 0)], abs=1e-12)
    # Forgetting the source epoch deletes the link; tomorrow falls back to its own evidence.
    out = governance.forget_epoch(db, "desk", T0 + 400)
    assert out["effects"]["derived_deleted"]["epoch_links"] == 1
    alone = beliefs.compute(db, "tomorrow")
    assert alone["linked_from"] is None and alone["sensors"]["cam-a"]["n"] == 4
    assert {k: alone[k] for k in ("mechanism", "sensors", "partners")} == \
        {k: own[k] for k in ("mechanism", "sensors", "partners")}


def test_forgetting_a_linked_root_deletes_the_link(instance):
    db, _ = instance
    remounted_desk(db)
    tomorrow = Journal(db, "tomorrow")
    original_readings(tomorrow)
    asked = executive.decide(db, "tomorrow", T0 + 200, record=True)
    executive.resolve(db, asked["pending_id"], "yes", T0 + 201, confirm=True)
    governance.forget_partner(db, "ana", T0 + 300)
    assert db.execute("SELECT count(*) FROM epoch_links").fetchone()[0] == 0
    assert beliefs.compute(db, "tomorrow")["linked_from"] is None
    assert not db.execute("SELECT 1 FROM belief_revisions WHERE instr(summary, 'ana@0')").fetchone()


def test_declined_candidate_is_not_asked_again(instance):
    db, _ = instance
    remounted_desk(db)
    # This instance's owner had answered "continue there" twice before (beliefs.answer_prior counts epoch answers),
    # so after the decline below P(new setup) = (1 + 1) / (1 + 2 + 2) and the evidence still names desk.
    with store.write(db):
        store.bump(db, beliefs.ANSWER_COUNTERS[True], 2)
    tomorrow = Journal(db, "tomorrow")
    original_readings(tomorrow, rounds=3)
    asked = executive.decide(db, "tomorrow", T0 + 200, record=True)
    assert asked["decision"] == "ask" and asked["scope"] == "epoch:desk"
    assert executive.decide(db, "tomorrow", T0 + 201, record=True)["pending_id"] == asked["pending_id"]
    done = executive.resolve(db, asked["pending_id"], "no, this is new", T0 + 202, confirm=False)
    assert done["link"] is None and done["changepoint"] is None and beliefs.link(db, "tomorrow") is None
    original_readings(tomorrow, rounds=1)    # still within the 12-event matching window, still matches desk
    match = beliefs.match_epochs(db, "tomorrow")
    assert match["prior"] == {"new_setup": 2 / 5, "n_new": 1, "n_continue": 2} and match["best"] == "desk"
    for k in range(3):
        out = executive.decide(db, "tomorrow", T0 + 203 + k, record=True)
        assert out["scope"] != "epoch:desk" and out["asks_used"] == 1
    assert beliefs.compute(db, "tomorrow")["linked_from"] is None


def _nsf(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured.err


def test_cli_epoch_match_and_resolve_confirm(tmp_path, capsys):
    path = tmp_path / "cli.sqlite"
    db = store.create(path, T0)
    try:
        baseline(Journal(db, "desk"))
        baseline(Journal(db, "remounted"), camera=1)
        original_readings(Journal(db, "tomorrow"))
    finally:
        db.close()
    # Two rounds of camera, word and reference readings reach 3:1; one round does not.
    code, out = _nsf(capsys, "epoch", "match", "tomorrow", "--last", "6", "--db", str(path))
    assert code == 0 and out["best"] == "desk" and out["events"] == 6 and out["best_posterior"] >= .75
    code, out = _nsf(capsys, "epoch", "match", "tomorrow", "--last", "3", "--db", str(path))
    assert code == 0 and out["best"] is None and out["ranking"][0]["posterior"] < .75
    db = store.open_existing(path)
    try:
        j = Journal(db, "today")
        baseline(j)
        remount(j)
    finally:
        db.close()
    code, decided = _nsf(capsys, "decide", "today", "--record", "--db", str(path))
    assert decided["decision"] == "ask" and decided["scope"] == "camera:cam-a"
    with pytest.raises(SystemExit):     # --confirm and --reject are mutually exclusive
        cli.main(["resolve", str(decided["pending_id"]), "yes", "--confirm", "--reject", "--db", str(path)])
    capsys.readouterr()
    code, done = _nsf(capsys, "resolve", str(decided["pending_id"]), "yes", "--confirm", "--db", str(path))
    assert code == 0 and done["changepoint"]["confirmed"] is True
    code, status = _nsf(capsys, "status", "today", "--db", str(path))
    assert code == 0 and status["sensors"]["cam-a"]["segment"].startswith("cam-a@")


# ---- 4. speed -----------------------------------------------------------------------------------------

def test_observe_and_decide_stay_fast_on_a_large_epoch(tmp_path):
    """Inference reads SQL aggregates, so one loop does not replay the epoch (20k events, generous bound)."""
    db = store.create(tmp_path / "big.sqlite", T0, config=store.InstanceConfig(max_evidence_rows=10 ** 6))
    try:
        evidence.start_epoch(db, "big", T0)
        rng = random.Random(3)
        rows = []
        for i in range(20_000):
            channel = ("camera", "word", "reference")[i % 3]
            root = {"camera": "cam-a", "word": "ana"}.get(channel) or f"ref-{i % 5}"
            action, reading = rng.randrange(2), int(rng.random() < .1)
            sample = f"s{i // 2}" if i % 7 == 0 else None
            rows.append((f"b{i}", "big", channel, action, reading ^ action, root, sample, T0, T0 + 10 ** 9))
        with store.write(db):
            db.executemany("INSERT INTO evidence(id,epoch,channel,action,bit,root,sample_id,recorded_at,expires_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        evidence.observe(db, {"id": "warm", "epoch": "big", "channel": "camera", "action": 0, "bit": 0,
                              "root": "cam-a"}, T0)
        loops = []
        for k in range(7):
            start = time.perf_counter()
            evidence.observe(db, {"id": f"n{k}", "epoch": "big", "channel": ("camera", "word", "reference")[k % 3],
                                  "action": 0, "bit": 0, "root": ("cam-a", "ana", "ref-1")[k % 3]}, T0 + k)
            executive.decide(db, "big", T0 + k, record=True)
            loops.append(time.perf_counter() - start)
        assert statistics.median(loops) < 0.2
        assert beliefs.compute(db, "big")["evidence"]["learning"] == len(_old_learning(db, "big"))
    finally:
        db.close()
