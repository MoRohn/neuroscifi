"""Exact factorised inference, legacy parity, identifiability, change detection and explanations."""
import json
import math
import random

import numpy as np
import pytest

from neuroscifi import legacy_live
from neuroscifi.instance import beliefs, evidence, store
from neuroscifi.instance.errors import NotFound
from neuroscifi.lab.source_groups import group_log_likelihood

from conftest import T0


class Journal:
    """Feed corrected readings into one epoch; returns the seq of each event."""

    def __init__(self, db, epoch="desk"):
        self.db, self.epoch, self.count = db, epoch, 0
        evidence.start_epoch(db, epoch, T0)

    def add(self, channel, root, corrected, **extra):
        self.count += 1
        action = self.count % 2
        obs = {"id": f"{self.epoch}-{self.count:04d}", "epoch": self.epoch, "channel": channel,
               "action": action, "bit": corrected ^ action, "root": root, **extra}
        return evidence.observe(self.db, obs, T0 + self.count)["seq"]


def assert_close(actual, expected, tol=1e-12):
    if isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key in expected:
            assert_close(actual[key], expected[key], tol)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected):
            assert_close(a, b, tol)
    elif isinstance(expected, float):
        assert abs(actual - expected) <= tol, (actual, expected)
    else:
        assert actual == expected


def test_legacy_parity_on_randomised_sequences(instance, tmp_path):
    """compute()["legacy"] equals legacy_live.status to 1e-12, and the factorised margins equal the 8-state ones."""
    db, _ = instance
    rng = random.Random(20261120)
    proto = legacy_live.open_store(tmp_path / "proto.sqlite")
    for n in range(220):
        epoch = f"e{n}"
        evidence.start_epoch(db, epoch, T0)
        with proto:
            proto.execute("INSERT INTO epochs VALUES (?)", (epoch,))
        camera, partner = f"cam{rng.randrange(3)}", f"person{rng.randrange(3)}"
        refs = [f"ref{k}" for k in range(rng.randrange(1, 5))]
        bias = {name: rng.random() for name in ("camera", "word", "reference")}
        for i in range(rng.randrange(0, 40)):
            channel = rng.choice(["camera", "word", "reference", "reference"])
            root = {"camera": camera, "word": partner}.get(channel) or rng.choice(refs)
            action, bit = rng.randrange(2), int(rng.random() < bias[channel])
            event_id = f"{rng.getrandbits(40):010x}-{i}"  # random ids: id order differs from insertion order
            legacy_live.add_event(proto, epoch, event_id, channel, action, bit, root)
            evidence.observe(db, {"id": event_id, "epoch": epoch, "channel": channel, "action": action,
                                  "bit": bit, "root": root}, T0)
        result = beliefs.compute(db, epoch)
        expected = legacy_live.status(proto, epoch)
        assert_close(result["legacy"], expected)
        margins = expected["probability_inverted"]
        assert abs(result["mechanism"]["p_inverted"] - margins["mechanism"]) < 1e-12
        for name, key in (("sensors", "camera"), ("partners", "speaker_convention")):
            for value in result[name].values():
                assert abs(value["p_inverted"] - margins[key]) < 1e-12
    proto.close()


def test_group_likelihood_rows_are_mechanism_states():
    """The per-w values taken from group_log_likelihood are its w=0 and w=1 columns (it ignores s and t)."""
    readings = np.array([[1], [0], [1], [1]])
    values = group_log_likelihood(readings, .15, .10)[0]
    assert np.allclose(values[:4], values[0]) and np.allclose(values[4:], values[4])
    good, bad = 3 * math.log(.9) + math.log(.1), 3 * math.log(.1) + math.log(.9)
    assert values[4] == pytest.approx(math.log(.85 * math.exp(good) + .15 * math.exp(bad)), abs=1e-12)


def test_camera_and_word_only_leave_mechanism_unidentifiable_until_references(instance):
    db, _ = instance
    j = Journal(db)
    for _ in range(10):
        j.add("camera", "cam-a", 1)
        j.add("word", "ana", 1)
    result = beliefs.compute(db, "desk")
    assert result["mechanism"] == {"p_inverted": 0.5, "identifiable": False}
    assert result["reference_roots"] == 0
    scopes = {u["scope"]: u["why"] for u in result["unidentifiable"]}
    assert "joint flip symmetry" in scopes["mechanism"]
    assert {"mechanism", "camera:cam-a", "partner:ana"} <= set(scopes)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    result = beliefs.compute(db, "desk")
    assert result["reference_roots"] == 3 and result["mechanism"]["identifiable"]
    assert result["mechanism"]["p_inverted"] < .05
    assert result["sensors"]["cam-a"]["p_inverted"] > .95 and result["partners"]["ana"]["p_inverted"] > .95
    assert result["unidentifiable"] == []


def test_one_reference_root_cannot_resolve_alone(instance):
    """Readings from a single fixture share its polarity: many copies saturate below the .87 bar."""
    db, _ = instance
    j = Journal(db)
    for _ in range(30):
        j.add("reference", "ref-1", 1)
    result = beliefs.compute(db, "desk")
    assert result["mechanism"]["p_inverted"] == pytest.approx(.85, abs=1e-9)
    assert not result["mechanism"]["identifiable"]


def test_two_partners_with_opposite_conventions_are_both_retained(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
        j.add("reference", root, 0)
    for _ in range(6):
        j.add("word", "ana", 0)   # Ana's "left" means action 0
        j.add("word", "ben", 1)   # Ben uses the opposite word convention
    result = beliefs.compute(db, "desk")
    assert result["mechanism"]["identifiable"]
    assert result["partners"]["ana"]["p_inverted"] < .05
    assert result["partners"]["ben"]["p_inverted"] > .95
    assert result["legacy"] is None  # two partners: the prototype could not represent this
    # Lifelong correction: more of Ben does not overwrite Ana's meaning.
    for _ in range(20):
        j.add("word", "ben", 1)
    after = beliefs.compute(db, "desk")
    assert after["partners"]["ana"]["p_inverted"] < .05 and after["partners"]["ben"]["p_inverted"] > .99


def test_camera_roots_have_independent_alignment(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    for _ in range(5):
        j.add("camera", "cam-left", 0)
        j.add("camera", "cam-right", 1)
    result = beliefs.compute(db, "desk")
    assert result["sensors"]["cam-left"]["p_inverted"] < .05
    assert result["sensors"]["cam-right"]["p_inverted"] > .95
    assert result["sensors"]["cam-left"]["n"] == result["sensors"]["cam-right"]["n"] == 5
    j.add("camera", "cam-right", 0)
    j.add("camera", "cam-right", 0)
    later = beliefs.compute(db, "desk")
    assert later["sensors"]["cam-left"]["n"] == 5
    assert later["sensors"]["cam-left"]["p_inverted"] == pytest.approx(
        result["sensors"]["cam-left"]["p_inverted"], abs=1e-12)  # another root's evidence leaves it unchanged


def test_memory_only_has_no_effect_and_sample_copies_count_once(instance):
    db, _ = instance
    j = Journal(db)
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 0)
    j.add("camera", "cam-a", 1)
    before = beliefs.compute(db, "desk")
    for _ in range(10):
        j.add("camera", "cam-a", 0, consent="memory_only")
    after = beliefs.compute(db, "desk")
    assert after["evidence"] == {"total": 14, "learning": 4, "memory_only": 10, "duplicates_ignored": 0}
    for key in ("mechanism", "sensors", "partners", "legacy", "unidentifiable"):
        assert after[key] == before[key]
    for _ in range(5):
        j.add("camera", "cam-a", 0, sample_id="frame-7")  # five copies of one camera frame
    copies = beliefs.compute(db, "desk")
    assert copies["evidence"]["duplicates_ignored"] == 4 and copies["sensors"]["cam-a"]["n"] == 2
    single = Journal(db, "single")
    for root in ("ref-1", "ref-2", "ref-3"):
        single.add("reference", root, 0)
    single.add("camera", "cam-a", 1)
    single.add("camera", "cam-a", 0)
    assert beliefs.compute(db, "single")["sensors"] == copies["sensors"]


def test_unknown_epoch_is_not_found(instance):
    db, _ = instance
    for call in (beliefs.compute, beliefs.detect_change, beliefs.explain):
        with pytest.raises(NotFound):
            call(db, "nowhere")


def baseline(j, rounds=12, references=True):
    for k in range(rounds):
        j.add("camera", "cam-a", 0)
        j.add("word", "ana", 0)
        if references:
            j.add("reference", f"ref-{k % 3}", 0)


def test_change_in_one_camera_root(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    first = j.add("camera", "cam-a", 1)
    j.add("word", "ana", 0)
    j.add("reference", "ref-0", 0)
    for k in range(5):
        j.add("camera", "cam-a", 1)
        j.add("word", "ana", 0)
        j.add("reference", f"ref-{k % 3}", 0)
    change = beliefs.detect_change(db, "desk")
    assert change["suspected"] and not change["ambiguous"]
    assert change["scope"] == "camera:cam-a" and change["since_seq"] == first
    assert change["llr"] > math.log(19)
    assert change["alternatives"][0]["llr"] < change["llr"] - 1


def test_change_in_one_partner(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    seqs = []
    for k in range(6):
        j.add("camera", "cam-a", 0)
        seqs.append(j.add("word", "ana", 1))
        j.add("reference", f"ref-{k % 3}", 0)
    change = beliefs.detect_change(db, "desk")
    assert change["suspected"] and not change["ambiguous"]
    assert change["scope"] == "partner:ana" and change["since_seq"] == seqs[0]


def test_everything_flipped_including_references_is_a_mechanism_change(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    first = j.add("camera", "cam-a", 1)
    for k in range(6):
        if k:
            j.add("camera", "cam-a", 1)
        j.add("word", "ana", 1)
        j.add("reference", f"ref-{k % 3}", 1)
    change = beliefs.detect_change(db, "desk")
    assert change["suspected"] and not change["ambiguous"]
    assert change["scope"] == "mechanism" and change["since_seq"] == first


def test_too_little_post_change_evidence_is_not_suspected(instance):
    db, _ = instance
    j = Journal(db)
    baseline(j)
    for _ in range(2):
        j.add("camera", "cam-a", 1)
        j.add("word", "ana", 0)
    change = beliefs.detect_change(db, "desk")
    assert not change["suspected"] and change["scope"] is None and change["since_seq"] is None
    assert not change["ambiguous"] and math.isfinite(change["llr"])


def test_symmetric_change_is_ambiguous(instance):
    """After the change only the camera was observed: a camera remount and a mechanism change fit equally."""
    db, _ = instance
    j = Journal(db)
    baseline(j)
    first = j.add("camera", "cam-a", 1)
    for _ in range(5):
        j.add("camera", "cam-a", 1)
    change = beliefs.detect_change(db, "desk")
    assert change["suspected"] and change["ambiguous"]
    assert {change["scope"], change["alternatives"][0]["scope"]} == {"mechanism", "camera:cam-a"}
    assert change["alternatives"][0]["llr"] == pytest.approx(change["llr"], abs=1e-9)
    assert change["since_seq"] == first


def test_change_detection_uses_last_64_learning_events(instance):
    db, _ = instance
    j = Journal(db)
    for _ in range(10):
        j.add("camera", "cam-a", 1)  # an old regime, outside the window
    baseline(j, rounds=22)          # 66 consistent events
    change = beliefs.detect_change(db, "desk")
    assert change["window"] == 64 and not change["suspected"]


def test_explain_lists_support_counterevidence_and_uncertainty(instance):
    db, _ = instance
    j = Journal(db)
    seqs = [j.add("camera", "cam-a", 1) for _ in range(4)]
    odd = j.add("camera", "cam-a", 0)
    j.add("word", "ana", 1)
    report = beliefs.explain(db, "desk")
    factors = {f["scope"]: f for f in report["factors"]}
    mechanism = factors["mechanism"]
    assert "joint flip symmetry" in mechanism["uncertainty"] and "reference" in mechanism["uncertainty"]
    assert mechanism["supporting"] == mechanism["counterevidence"] == []
    camera = factors["camera:cam-a"]
    assert [e["seq"] for e in camera["supporting"]] == seqs
    assert [e["seq"] for e in camera["counterevidence"]] == [odd]
    assert "joint flip" in camera["uncertainty"]
    assert report["beliefs"]["mechanism"]["identifiable"] is False
    assert report["revisions"] and report["revisions"][0]["cause"].startswith("evidence:")
    assert set(report["change"]) >= {"suspected", "scope", "since_seq", "llr", "alternatives", "ambiguous"}
    for root in ("ref-1", "ref-2", "ref-3"):
        j.add("reference", root, 1)
    j.add("reference", "ref-1", 0)
    factors = {f["scope"]: f for f in beliefs.explain(db, "desk")["factors"]}
    assert factors["mechanism"]["supporting_total"] == 3 and factors["mechanism"]["counterevidence_total"] == 1
    assert "pin the mechanism" in factors["mechanism"]["uncertainty"]


def test_results_are_strict_json(instance):
    db, _ = instance
    j = Journal(db)
    for _ in range(200):
        j.add("reference", "ref-1", 1)
        j.add("camera", "cam-a", 1)
    json.dumps(beliefs.explain(db, "desk"), allow_nan=False)
    assert store.state_version(db) == beliefs.compute(db, "desk")["state_version"]
