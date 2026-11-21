"""temporal.py: freshness classes, date parsing, staleness, windows, supersession, validity."""
from datetime import datetime, timezone

import pytest

from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import temporal as TM

NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc).timestamp()   # a Friday
H, D = 3600.0, 86400.0


def ts(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


# ---- classes -----------------------------------------------------------------------------------------------

def test_freshness_policy_table():
    assert list(TM.FRESHNESS) == ["breaking", "volatile", "release", "scientific", "stable"]
    assert [TM.FRESHNESS[c]["ttl"] for c in TM.FRESHNESS] == [3 * H, 2 * D, 7 * D, 30 * D, 365 * D]
    assert all(TM.FRESHNESS[c]["description"] for c in TM.FRESHNESS)


@pytest.mark.parametrize("question, cls", [
    ("breaking: earthquake today in Lisbon", "breaking"),
    ("is GitHub down right now", "breaking"),
    ("what is the latest numpy version", "release"),
    ("CVE-2026-1234 advisory for openssl", "release"),
    ("does exercise increase hippocampal neurogenesis? recent studies", "scientific"),
    ("current price of copper", "volatile"),
    ("who is the CEO of Acme", "volatile"),
    ("history of the Roman aqueducts", "stable"),
])
def test_classify_by_cues(question, cls):
    assert TM.classify(question) == cls


def test_classify_prefers_assessment_and_ignores_invalid():
    assert TM.classify("history of rome", {"freshness_class": "breaking"}) == "breaking"
    assert TM.classify("history of rome", {"freshness_class": "bogus"}) == "stable"


def test_ttl_overrides_and_unknown_class():
    assert TM.ttl("release") == 7 * D
    assert TM.ttl("release", {"release": 3600}) == 3600.0
    with pytest.raises(ValidationError):
        TM.ttl("weekly")


# ---- parsing -----------------------------------------------------------------------------------------------

DATE_TABLE = [
    ("2026-11-03", ts(2026, 11, 3), "day"),
    ("2026-11-03T10:00:00Z", ts(2026, 11, 3, 10), "time"),
    ("2026-11-03T10:00:00+02:00", ts(2026, 11, 3, 8), "time"),
    ("2026-11-03T10:00:00.250-05:00", ts(2026, 11, 3, 15) + 0.25, "time"),
    ("2026-11-03 10:00", ts(2026, 11, 3, 10), "time"),
    ("Tue, 03 Nov 2026 10:00:00 GMT", ts(2026, 11, 3, 10), "time"),
    ("Tue, 03 Nov 2026 10:00:00 +0100", ts(2026, 11, 3, 9), "time"),
    ("3 November 2026", ts(2026, 11, 3), "day"),
    ("3rd of November 2026", ts(2026, 11, 3), "day"),
    ("Tuesday, 3 Nov 2026", ts(2026, 11, 3), "day"),
    ("Nov 3, 2026", ts(2026, 11, 3), "day"),
    ("Nov. 3, 2026", ts(2026, 11, 3), "day"),
    ("November 3 2026", ts(2026, 11, 3), "day"),
    ("2026/11/03", ts(2026, 11, 3), "day"),
    ("Published: 3 Nov 2026", ts(2026, 11, 3), "day"),
    ("November 2026", ts(2026, 11, 1), "month"),
    ("2026-11", ts(2026, 11, 1), "month"),
    ("yesterday", ts(2026, 11, 19), "day"),
    ("today", ts(2026, 11, 20), "day"),
    ("2 days ago", ts(2026, 11, 18), "day"),
    ("3 hours ago", NOW - 3 * H, "time"),
    ("an hour ago", NOW - H, "time"),
    ("last week", ts(2026, 11, 13), "day"),
    ("this week", ts(2026, 11, 16), "day"),
    ("last Tuesday", ts(2026, 11, 17), "day"),
    ("a month ago", ts(2026, 10, 20), "day"),
    ("last month", ts(2026, 10, 1), "month"),
]


@pytest.mark.parametrize("text, expected, precision", DATE_TABLE)
def test_parse_date_table(text, expected, precision):
    assert TM.parse_date(text, now=NOW) == pytest.approx(expected)
    assert TM.parse_date_precision(text, now=NOW) == (pytest.approx(expected), precision)


@pytest.mark.parametrize("text", ["31 February 2026", "2026-02-30", "2026-13-01", "Feb 29, 2025", "nonsense",
                                  "", "32 May 2026", "2026-11-03T25:00:00Z"])
def test_parse_date_rejects_impossible(text):
    assert TM.parse_date(text, now=NOW) is None


def test_bare_year_only_via_precision_and_relative_needs_now():
    assert TM.parse_date("2026") is None
    assert TM.parse_date_precision("2026") == (ts(2026, 1, 1), "year")
    assert TM.parse_date("yesterday") is None and TM.parse_date("2 days ago") is None
    assert TM.parse_date("Feb 29, 2028") == ts(2028, 2, 29)      # leap year


def test_event_dates_absolute_partial_and_relative():
    ref = ts(2026, 11, 20)
    s = "On Nov 3 the team said that 2 days ago, on 1 November 2026, it shipped; data may 3 follow in May 2026."
    assert TM.event_dates(s, reference=ref) == [ts(2026, 11, 3), ts(2026, 11, 18), ts(2026, 11, 1), ts(2026, 5, 1)]
    # without a reference only absolute dates survive
    assert TM.event_dates(s, reference=None) == [ts(2026, 11, 1), ts(2026, 5, 1)]
    # "Dec 30" mentioned in January refers to the previous year
    assert TM.event_dates("Released on Dec 30 to users.", reference=ts(2027, 1, 5)) == [ts(2026, 12, 30)]
    assert TM.event_dates("Revenue in 2024 was flat.", reference=ref) == []


# ---- staleness and windows ---------------------------------------------------------------------------------

@pytest.mark.parametrize("cls", list(TM.FRESHNESS))
def test_is_stale_per_class_boundaries(cls):
    limit = TM.FRESHNESS[cls]["ttl"]
    fresh, why = TM.is_stale(NOW - limit + 60, cls, NOW)
    assert fresh is False and why.startswith("fresh")
    stale, why = TM.is_stale(NOW - limit - 60, cls, NOW)
    assert stale is True and cls in why


def test_is_stale_overrides_and_events():
    assert TM.is_stale(NOW - 4 * H, "breaking", NOW)[0] is True
    assert TM.is_stale(NOW - 4 * H, "breaking", NOW, ttl_overrides={"breaking": 5 * H})[0] is False
    stale, why = TM.is_stale(NOW - 60, "release", NOW, newer_release_seen=True)
    assert stale and "newer release" in why
    stale, why = TM.is_stale(NOW - 60, "scientific", NOW, revision_seen=True)
    assert stale and "erratum" in why
    assert TM.is_stale(None, "stable", NOW) == (True, "stale: never verified")


def test_window_ok():
    assert TM.window_ok(None, 7 * D, NOW) is None
    assert TM.window_ok(NOW - 6 * D, 7 * D, NOW) is True
    assert TM.window_ok(NOW - 8 * D, 7 * D, NOW) is False
    assert TM.window_ok(NOW - 800 * D, None, NOW) is True
    assert TM.window_ok(NOW + 3 * D, 7 * D, NOW) is False       # implausible future date


# ---- supersession ------------------------------------------------------------------------------------------

def claim(values=("v2.3",), published=None, event=None, retrieved=NOW, polarity=1, key="numpy latest version"):
    return {"key": key, "values": list(values), "published_at": published, "event_at": event,
            "retrieved_at": retrieved, "polarity": polarity}


def test_supersedes_newer_conflicting_value():
    old = claim(("v2.3",), published=ts(2026, 6, 1))
    new = claim(("v2.4",), published=ts(2026, 11, 3))
    assert TM.supersedes(new, old) is True and TM.supersedes(old, new) is False


def test_supersedes_event_time_wins_over_publication():
    old = claim(("v2.3",), published=ts(2026, 11, 10), event=ts(2026, 6, 1))
    new = claim(("v2.4",), published=ts(2026, 11, 5), event=ts(2026, 11, 3))
    assert TM.supersedes(new, old) is True


def test_supersedes_requires_conflict_same_key_and_strictly_later():
    old = claim(("v2.3",), published=ts(2026, 6, 1))
    assert TM.supersedes(claim(("v2.3",), published=ts(2026, 11, 3)), old) is False          # same value
    assert TM.supersedes(claim(("v2.4",), published=ts(2026, 11, 3), key="pandas"), old) is False
    assert TM.supersedes(claim(("v2.4",), published=ts(2026, 6, 1)), old) is False            # same time
    assert TM.supersedes(claim(("v2.3",), published=ts(2026, 11, 3), polarity=-1), old) is True


def test_supersedes_never_from_undated_new_and_bounds_undated_old_by_retrieval():
    old = claim(("v2.3",), published=ts(2026, 6, 1))
    assert TM.supersedes(claim(("v2.4",), published=None, event=None), old) is False
    undated_old = claim(("v2.3",), published=None, retrieved=ts(2026, 10, 1))
    assert TM.supersedes(claim(("v2.4",), published=ts(2026, 11, 3)), undated_old) is True
    assert TM.supersedes(claim(("v2.4",), published=ts(2026, 9, 1)), undated_old) is False


# ---- descriptions and validity -----------------------------------------------------------------------------

@pytest.mark.parametrize("age, text", [
    (10, "just now"), (60, "1 minute ago"), (5 * 60, "5 minutes ago"), (H, "1 hour ago"), (3 * H, "3 hours ago"),
    (D, "1 day ago"), (2 * D + 5, "2 days ago"), (30 * D, "on 2026-10-21"),
])
def test_describe_age(age, text):
    assert TM.describe_age(NOW - age, NOW) == text


def test_describe_age_undated_and_future():
    assert TM.describe_age(None, NOW) == "undated"
    assert TM.describe_age(NOW + 5 * D, NOW).startswith("dated in the future")


def test_validity():
    v = TM.validity({"event_at": ts(2026, 11, 3), "published_at": ts(2026, 11, 4)}, now=NOW)
    assert v == {"valid_from": ts(2026, 11, 3), "valid_until": None, "basis": "event date stated in the source"}
    v = TM.validity({"published_at": ts(2026, 11, 4), "superseded_at": ts(2026, 11, 10)}, now=NOW)
    assert v["valid_from"] == ts(2026, 11, 4) and v["valid_until"] == ts(2026, 11, 10)
    assert "superseded" in v["basis"]
    v = TM.validity({"retrieved_at": NOW}, now=NOW)
    assert v["valid_from"] == NOW and "undated" in v["basis"]
    assert TM.validity({}, now=NOW)["valid_from"] is None


# ---- review remediation (V1) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", ["Humans settled here 12000 years ago.", "It was 100000000 days ago.",
                                      "The event happened 12,900 years ago.", "Roughly 4.5 billion years ago.",
                                      "About 1.000 years ago."])
def test_relative_dates_out_of_calendar_range_are_skipped(sentence):
    assert TM.event_dates(sentence, reference=NOW) == []


def test_in_range_relative_years_still_resolve():
    assert TM.event_dates("Built 900 years ago.", reference=NOW) == [ts(1126, 11, 20)]


def test_future_effective_dates_never_supersede():
    old = {"key": "k", "values": ["v2.3.5"], "published_at": NOW - D, "retrieved_at": NOW}
    plan = {"key": "k", "values": ["v3.0"], "published_at": NOW - 5 * D, "event_at": ts(2027, 6, 1),
            "retrieved_at": NOW}
    assert TM.supersedes(plan, old, now=NOW) is False
    assert TM.supersedes(plan, old) is False                 # its own retrieval time bounds "now"
    assert TM.supersedes(plan, old, now=ts(2027, 6, 2)) is True
    soon = dict(plan, event_at=NOW + 0.5 * D)
    assert TM.supersedes(soon, old, now=NOW) is True         # within FUTURE_SKEW (time zones)


def test_validity_never_starts_in_the_future():
    out = TM.validity({"published_at": NOW + 100 * 365 * D}, now=NOW)
    assert out["valid_from"] is None and "future" in out["basis"]
    assert TM.validity({"published_at": NOW + 0.5 * D}, now=NOW)["valid_from"] == NOW + 0.5 * D
