"""ADR-051 (lane I): curiosity by learning progress (expected against realised gain per subject) and the salience
record of autonomy candidates."""
import pytest

from neuroscifi.webcortex import autonomy, curiosity, salience, state
from test_consolidate import DAY, T0, claim, cluster, consolidated, open_db
from test_curiosity import accepted


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def noise(db, key, at, n=1):
    """n looks at a subject whose sources disagree at random for ever (the noisy TV)."""
    for i in range(n):
        a = claim(f"http://n{i}a.example/{key}", f"The {key} stands at {41 + i} points today.", key=key)
        b = claim(f"http://n{i}b.example/{key}", f"The {key} stands at {77 - i} points today.", key=key)
        consolidated(db, [cluster([a, b], status="contested", confidence=0.5,
                                  contradicting=[{"a": a.id, "b": b.id, "reason": "different values"}])], at + i)


def lp_on(db, on=True):
    state.set_config(db, {"curiosity_learning_progress": on}, T0)


def test_a_subject_never_looked_at_keeps_its_gain(db):
    factor, note, record = curiosity.progress(db, "never seen", 0.9)
    assert factor == 1.0 and note == "" and record["looks"] == 0


def test_looks_that_change_nothing_down_weight_the_subject(db):
    noise(db, "coin index", T0 + DAY, n=3)
    factor, note, record = curiosity.progress(db, "coin index", 0.9)
    p, c = curiosity.PRIOR_RESOLVABLE, curiosity.PROGRESS_CHANCE
    expected = p * (1 - c) ** 3 / (p * (1 - c) ** 3 + 1 - p) / p           # the two-kind posterior, by hand
    assert record["without_progress"] == 3 and factor == pytest.approx(max(curiosity.LP_FLOOR, expected))
    assert record["realised_bits"] == 0.0 and "learning progress" in note


def test_a_look_that_changed_what_is_held_is_progress(db):
    accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"), T0)
    factor, _, record = curiosity.progress(db, "zlib latest version", 0.3)
    assert record["looks"] >= 1 and record["without_progress"] == 0 and factor == 1.0


def test_off_by_default_and_rank_is_as_before_when_off(db):
    """review1 CM1: re-measured after the rework it lowered the timer path's bits per budget on the held-out worlds'
    wider seeds, so it ships off; on, the stronger of it and the futility down-weight applies."""
    accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"), T0)
    noise(db, "coin index", T0 + DAY, n=2)
    assert curiosity.learning_progress_on(db) is False
    off = curiosity.rank(db, T0 + 2 * DAY)
    assert all("learning_progress" not in c for c in off)
    lp_on(db)
    on = curiosity.rank(db, T0 + 2 * DAY)
    coin_off = next(c for c in off if c["target"].get("key") == "coin index")
    coin_on = next(c for c in on if c["target"].get("key") == "coin index")
    assert coin_on["info_gain"] < coin_off["info_gain"] and coin_on["learning_progress"]["without_progress"] == 2
    zlib_off = next(c for c in off if c["target"].get("key") == "zlib latest version")
    zlib_on = next(c for c in on if c["target"].get("key") == "zlib latest version")
    assert zlib_on["info_gain"] == zlib_off["info_gain"]           # the stale but important subject keeps its gain


def test_a_confirming_refresh_is_progress_on_what_it_was_asked_to_settle(db):
    """review1 CM1 (a): re-verifying a stale item that is still current settles P(current); it is not a failure."""
    from test_consolidate import two_sources
    _, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    key = db.execute("SELECT key FROM web_knowledge WHERE id=?", (first["provisional"][0],)).fetchone()[0]
    for i in range(3):
        consolidated(db, [two_sources(published=T0 - 5 * DAY)], T0 + (i + 1) * DAY)
    effects = [e for (e,) in db.execute("SELECT effect FROM web_feedback WHERE key=? ORDER BY id", (key,))]
    assert effects[0] == "new" and len(effects) >= 3 and set(effects[1:]) == {"unchanged"}
    for expected in (0.1, 0.5, 1.0):
        factor, note, record = curiosity.progress(db, key, expected, T0 + 4 * DAY)
        assert factor == 1.0 and record["without_progress"] == 0 and note == ""
    # and the futility streak, which reads the same rows, does not count them either while it is on (off: Wave 0)
    assert curiosity._futility(db, key)[0] == 0.25
    assert curiosity._futility(db, key, refresh_is_progress=True)[0] == 1.0
    later = T0 + 400 * DAY
    lp_on(db, False)
    off = next(c for c in curiosity.rank(db, later) if c["target"].get("key") == key)
    lp_on(db)
    on = next(c for c in curiosity.rank(db, later) if c["target"].get("key") == key)
    assert on["info_gain"] == pytest.approx(4 * off["info_gain"], abs=1e-5) and "irreducible" not in on["reason"]


def test_progress_and_futility_take_the_stronger_never_the_product(db):
    """review1 CM1 (b): both read the same web_feedback rows."""
    noise(db, "coin index", T0 + DAY, n=3)
    now = T0 + DAY + 3
    lp_on(db)
    coin = next(c for c in curiosity.rank(db, now) if c["target"].get("key") == "coin index")
    gain = curiosity.categorical_gain([0.35, 0.35, 0.15, 0.15])
    futility, _ = curiosity._futility(db, "coin index")
    lp, _, _ = curiosity.progress(db, "coin index", gain, now)
    assert futility == 0.25 and lp < futility
    assert coin["info_gain"] == pytest.approx(gain * min(futility, lp), abs=1e-6)
    assert coin["info_gain"] > gain * futility * lp + 1e-3


def test_a_down_weighted_subject_recovers_with_time_and_has_a_floor(db):
    """review1 CM1 (c): half of the down-weight is gone LP_HALF_LIFE after the newest look; never below LP_FLOOR."""
    noise(db, "coin index", T0, n=2)
    last = T0 + 1
    at_once, _, _ = curiosity.progress(db, "coin index", 0.9, last)
    half, _, record = curiosity.progress(db, "coin index", 0.9, last + curiosity.LP_HALF_LIFE)
    late, _, _ = curiosity.progress(db, "coin index", 0.9, last + 20 * curiosity.LP_HALF_LIFE)
    assert half == pytest.approx(1 - (1 - at_once) / 2) and record["days_since_last_look"] == pytest.approx(7.0)
    assert at_once < half < late and late > 0.99
    noise(db, "coin index", T0 + 10, n=4)                       # six looks without progress: far below the floor
    floor, _, record = curiosity.progress(db, "coin index", 0.9, T0 + 13)
    assert record["without_progress"] == 6 and floor == curiosity.LP_FLOOR


def test_the_noisy_subject_falls_behind_the_stale_one_sooner(db):
    stale = accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"),
                     T0)
    noise(db, "coin index", T0 + 19 * DAY, n=1)
    now = T0 + 20 * DAY
    lp_on(db, False)
    assert [c for c in curiosity.rank(db, now) if not c["excluded"]][0]["target"].get("key") == "coin index"
    lp_on(db)
    top = [c for c in curiosity.rank(db, now) if not c["excluded"]][0]
    assert top["target"].get("knowledge_id") == stale


def test_the_owners_hypotheses_are_never_down_weighted(db):
    from neuroscifi.instance import store
    from neuroscifi.webcortex import evolution
    noise(db, "coin index", T0 + DAY, n=3)
    lp_on(db)
    with store.write(db):
        hid, _ = evolution.open_hypothesis(db, key="coin index", statement="The coin index stands at 9 points today.",
                                           origin="owner", verification={"confidence": 0.5}, claims=[],
                                           values=["9"], polarity=1, now=T0 + 4 * DAY)
    ranked = autonomy.candidates(db, T0 + 20 * DAY)
    rows = ranked["chosen"] + ranked["refused"]
    owner = next(c for c in rows if c["key"] == "coin index")        # one candidate per subject
    assert hid and owner["owner"] is True
    assert owner["info_gain"] == pytest.approx(curiosity.info_gain(0.5), abs=1e-6)     # not down-weighted


def test_every_candidate_carries_an_explained_salience_that_changes_no_order(db):
    accepted(db, "zlib latest version", "zlib 1.3.1 is the latest zlib release.", ("a.example", "b.example"), T0)
    noise(db, "coin index", T0 + DAY, n=1)
    now = T0 + 20 * DAY
    shown = autonomy.candidates(db, now)
    for c in shown["chosen"] + shown["refused"]:
        s = c["salience"]
        assert set(s["signals"]) == set(salience.WEIGHTS) and 0.0 <= s["score"] <= 1.0
        assert s["score"] == pytest.approx(sum(salience.WEIGHTS[k] * v for k, v in s["signals"].items()), abs=1e-5)
        assert s["explanation"].startswith(f"salience {s['score']:.3f}")
    coin = next(c for c in shown["chosen"] + shown["refused"] if c["key"] == "coin index")
    assert coin["salience"]["signals"]["contradiction"] == 1.0
    state.set_config(db, {"salience_record": False}, now)
    plain = autonomy.candidates(db, now)
    assert [(c["key"], c["priority"]) for c in plain["chosen"]] == [(c["key"], c["priority"]) for c in shown["chosen"]]
    assert all("salience" not in c for c in plain["chosen"] + plain["refused"])


def test_the_decision_event_records_salience_and_progress(db):
    pick = {"kind": "stale", "key": "k", "mode": "fact_verification", "info_gain": 0.3, "cost": 1.0, "priority": 0.3,
            "signals": {}, "reason": "r", "salience": {"score": 0.2}, "learning_progress": {"factor": 1.0}}
    inputs = autonomy.decision_inputs(pick, {"rule": "unified", "path": "idle"})
    assert inputs["salience"] == {"score": 0.2} and inputs["learning_progress"] == {"factor": 1.0}
