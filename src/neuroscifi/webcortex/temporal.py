"""Temporal reasoning: freshness classes, date parsing, staleness, windows, supersession and validity.

All times are Unix seconds in UTC. Nothing reads the clock: relative expressions ("yesterday", "2 days ago") are
resolved only against an explicit `now`/`reference`.

FRESHNESS is the policy default per class; an owner may override any TTL (WebConfig.freshness_ttl) and callers pass
those as `ttl_overrides`. Dates are parsed conservatively: impossible dates ("31 February 2026") are rejected, and a
bare year is only returned by `parse_date_precision` (a year is too coarse to stand in for a publication time).
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re

from ..instance.errors import ValidationError
from . import text as _text
from .types import FRESHNESS_CLASSES

HOUR = 3600.0
DAY = 86400.0

FRESHNESS = {
    "breaking": {"ttl": 3 * HOUR,
                 "description": "Developing events (news, outages, incidents); re-verify after 3 hours."},
    "volatile": {"ttl": 2 * DAY,
                 "description": "Fast-changing state (prices, status, current office holders); re-verify after 2 days."},
    "release": {"ttl": 7 * DAY,
                "description": "Software versions, releases and advisories; re-verify after 7 days or as soon as a "
                               "newer release is observed."},
    "scientific": {"ttl": 30 * DAY,
                   "description": "Research findings; re-verify after 30 days or when a new preprint version, "
                                  "erratum or retraction is observed."},
    "stable": {"ttl": 365 * DAY,
               "description": "Slow-changing reference knowledge (definitions, history); re-verify yearly."},
}
assert tuple(FRESHNESS) == FRESHNESS_CLASSES

FUTURE_SKEW = DAY   # a publication date up to one day ahead of `now` is tolerated (time zones, scheduled posts)

# Question cues per class, checked in this order (the first class with a cue wins; default 'stable').
_CUES = (
    ("breaking", r"\b(?:breaking|right now|just happened|happening now|live updates?|latest news|today|tonight|"
                 r"this (?:morning|afternoon|evening)|ongoing|outage|is .* down|earthquake|hurricane|wildfire|"
                 r"election results?|developing)\b"),
    ("release", r"\b(?:latest (?:version|release|stable)|new(?:est)? (?:version|release)|released?|changelog|"
                r"release notes|cve-\d+|cve|vulnerabilit(?:y|ies)|advisor(?:y|ies)|security patch|patch(?:ed)?|"
                r"upgrade|deprecat\w*|pypi|npm|crates?\.io|version|v\d+(?:\.\d+)+)\b"),
    ("scientific", r"\b(?:stud(?:y|ies)|paper|papers|preprint|arxiv|biorxiv|medrxiv|pubmed|meta-analysis|"
                   r"systematic review|randomi[sz]ed|clinical trial|trial|peer[- ]reviewed|journal|evidence for|"
                   r"in vivo|in vitro|neurons?|hypothesis|experiment\w*|research)\b"),
    ("volatile", r"\b(?:current(?:ly)?|now|latest|recent(?:ly)?|this (?:week|month|year)|price|prices|stock|"
                 r"exchange rate|weather|forecast|score|status|who is the|ceo of|president of|ranking|trending)\b"),
)


def classify(question: str, assessment: dict | None = None) -> str:
    """Freshness class for a question: assessment['freshness_class'] when given and valid, else cue-based."""
    if assessment and assessment.get("freshness_class") in FRESHNESS:
        return assessment["freshness_class"]
    q = (question or "").lower()
    for cls, rx in _CUES:
        if re.search(rx, q):
            return cls
    return "stable"


# ---- claim categories (ADR-035, docs/knowledge/CLAIM_VERIFICATION.md) ------------------------------------------
#
# A freshness class says how long a *verification* stays fresh; a claim category says what kind of evidence can
# verify a *statement*. Each category maps onto one freshness class (CATEGORY_FRESHNESS), so the TTL table above
# stays the single source of time limits. claim_category() is deterministic: statement text (+ the question's
# freshness class, whether the statement is a package-registry statement, and the kinds of sources read).
CLAIM_CATEGORIES = ("stable", "historical", "time_sensitive", "scientific", "numerical", "comparative", "registry",
                    "interpretive")
CATEGORY_FRESHNESS = {"stable": "stable", "historical": "stable", "time_sensitive": "volatile",
                      "scientific": "scientific", "numerical": "stable", "comparative": "stable",
                      "registry": "release", "interpretive": "stable"}
assert set(CATEGORY_FRESHNESS) == set(CLAIM_CATEGORIES) and set(CATEGORY_FRESHNESS.values()) <= set(FRESHNESS)

# Lower-case judgement words (case-sensitive on purpose: "Great Wall", "Good Hope" are names, not judgements).
_INTERPRETIVE = re.compile(
    r"\b(?:best|worst|better|worse|should|ought|overrated|underrated|beautiful|ugly|worth(?:while|\s+it)?|"
    r"deserves?|ideal|recommended|good|bad|great|terrible|awful|excellent|important|unimportant|fair|unfair|"
    r"ethical|unethical|immoral|morally|pointless|useless|superior|inferior|wrong\s+to|right\s+to)\b")
_TIME_SENSITIVE = re.compile(
    r"\b(?:currently|current|now|nowadays|today|tonight|at\s+present|presently|still|no\s+longer|anymore|as\s+of|"
    r"latest|newest|most\s+recent|recent(?:ly)?|this\s+(?:week|month|year|morning|afternoon|evening|season)|"
    r"ongoing|right\s+now|so\s+far|yet|prices?|costs?|stock|exchange\s+rate|weather|forecast|status|outage|"
    r"(?:is|are)\s+(?:down|offline|online|unavailable|available|open|closed|operational)|"
    r"(?:ceo|chief\s+executive|president|prime\s+minister|chancellor|mayor|governor|chair(?:man|woman|person)?|"
    r"leader|director)\s+of)\b", re.IGNORECASE)
# Tense of the first finite verb (a heuristic: auxiliaries, an -ed or irregular past form, or a lower-case
# third-person -s form). Reading a past statement as present only costs it the "historical" category (the safer
# direction); the reverse would drop a discount, so an -s form ("carries", "ships") counts as present.
_PAST_WORD = re.compile(r"^(?:was|were|did|had|[a-z]{3,}ed|built|born|won|began|wrote|made|became|ran|held|sold|"
                        r"took|died|lost|left|came|went)$")
_PRESENT_WORD = re.compile(r"^(?:is|are|am|has|have|does|do|will|shall|can|may|must|remains?|stands?|"
                           r"[a-z]{2,}[^su\W]s)$")
_WORD = re.compile(r"[A-Za-z][\w'’-]*")


def _tense(statement: str) -> str | None:
    """"past", "present" or None: the first word (after the first) that reads as a finite verb decides."""
    for w in _WORD.findall(statement)[1:]:
        if _PAST_WORD.match(w):
            return "past"
        if _PRESENT_WORD.match(w):
            return "present"
    return None
_SCIENTIFIC = re.compile(dict(_CUES)["scientific"] + r"|\b(?:species|genes?|proteins?|cells?|cortex|synap\w*|"
                         r"molecul\w*|bacteri\w*|vir(?:us|al)|patients?|efficacy|dose|placebo)\b", re.IGNORECASE)
_BOUND = re.compile(r"\b(?:more|less|fewer|greater|other|rather)\s+than\b(?:\s+(?:about\s+)?[~≈]?\d)?",
                    re.IGNORECASE)
_COMPARATIVE = re.compile(
    r"\b(?:than|compared\s+(?:to|with)|versus|vs\.?|outperforms?|beats?|ranks?|ranked|"
    r"largest|tallest|longest|biggest|highest|fastest|oldest|youngest|smallest|shortest|deepest|widest|"
    r"heaviest|lightest|slowest|lowest|cheapest|richest|busiest|most\s+[a-z]+|least\s+[a-z]+)\b", re.IGNORECASE)
_NUMERIC_KINDS = ("number", "percent", "points", "quantity", "money", "version")


_YEAR_CONTEXT = re.compile(r"\b(?:in|on|since|from|until|till|by|during|before|after|circa|around|early|mid|late|"
                           r"of|year)\s+(?:the\s+)?(?:year\s+)?(\d{4})\b(?!\s*(?:[a-z%]|\.\d))", re.IGNORECASE)


def _past_values(statement: str, values, now: float | None) -> bool | None:
    """True when every date or year of the statement lies in the past (before `now`; a year before now's year),
    False when one does not, None when it states none. A four-digit number is a year only after a temporal
    preposition ("in 1987", "since 2019"): "port 9000" is not a year."""
    dated = [(k, p) for k, _, p in (_text.value_kind(v) for v in values) if k == "date"]
    dated += [("year", int(m.group(1))) for m in _YEAR_CONTEXT.finditer(statement)]
    if not dated:
        return None
    if now is None:
        return True
    year = datetime.fromtimestamp(now, timezone.utc).year
    return all((k == "date" and p <= now) or (k == "year" and p < year) for k, p in dated)


def claim_category(statement: str, *, now: float | None = None, freshness_class: str | None = None,
                   registry: bool = False, source_kinds=()) -> tuple[str, str]:
    """(category, reason) of a checked statement; rules in this order (the first that applies wins):

    interpretive     a lower-case judgement word ("best", "should", "overrated", "recommended", ...)
    registry         `registry`: a package-registry statement (synthesis.release_statement)
    historical       past tense (_tense: the first finite verb is was/were/did/had or a past form) with every date
                     in the past,
                     and no present-state cue ("still", "currently")
    time_sensitive   a present-state cue ("currently", "latest", "price", "CEO of", "is open", ...) or the
                     question's freshness class is breaking/volatile
    historical       past tense without a date ("X was created by Y")
    scientific       a research cue (study, trial, neurons, species, ...) or an academic source was read
    comparative      a comparison ("than", "versus", a superlative); "more than 40" is a bound, not a comparison
    numerical        a number, percentage, quantity, amount or version in the statement
    stable           everything else (definitions, locations, identities)
    """
    s = _text.collapse(statement or "")
    values = _text.values(s)
    present_cue = _TIME_SENSITIVE.search(s)
    past_tense = _tense(s) == "past"
    past = _past_values(s, values, now)
    if _INTERPRETIVE.search(s):
        return "interpretive", f"a judgement ('{_INTERPRETIVE.search(s).group(0)}') is not a checkable fact"
    if registry:
        return "registry", "a statement about a package's own registry record"
    if past_tense and past is True and not (present_cue and present_cue.group(0).lower() in
                                                            ("still", "currently", "now", "no longer", "anymore")):
        return "historical", "a completed, dated event: whether it happened then does not change with time"
    if present_cue:
        return "time_sensitive", f"a statement about the current state ('{present_cue.group(0)}')"
    if past is False:
        return "time_sensitive", "a statement about a future or current date"
    if freshness_class in ("breaking", "volatile"):
        return "time_sensitive", f"the question asks about the current state ({freshness_class} class)"
    if past_tense:
        return "historical", "a past-tense statement about a completed event"
    if _SCIENTIFIC.search(s) or "academic" in set(source_kinds or ()):
        return "scientific", "a research finding or measurement"
    if _COMPARATIVE.search(_BOUND.sub(" ", s)):
        return "comparative", "a comparison or ranking"
    years = {m.group(1) for m in _YEAR_CONTEXT.finditer(s)}
    if any(_text.value_kind(v)[0] in _NUMERIC_KINDS or (_text.value_kind(v)[0] == "year" and v not in years)
           for v in values):
        return "numerical", "a stated number or quantity"
    return "stable", "a definition, identity or location that does not depend on when it was written"


def ttl(freshness_class: str, ttl_overrides: dict | None = None) -> float:
    if freshness_class not in FRESHNESS:
        raise ValidationError(f"unknown freshness class: {freshness_class!r}")
    if ttl_overrides and ttl_overrides.get(freshness_class) is not None:
        return float(ttl_overrides[freshness_class])
    return float(FRESHNESS[freshness_class]["ttl"])


# ---- parsing ------------------------------------------------------------------------------------------------

_UNITS = {"second": 1.0, "sec": 1.0, "minute": 60.0, "min": 60.0, "hour": HOUR, "hr": HOUR, "day": DAY,
          "week": 7 * DAY}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a few": 3}
_M = _text.MONTH_RE
_ORD = r"(?:st|nd|rd|th)?"
_WEEKDAY_PREFIX = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"


def _utc(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _day(y: int, m: int, d: int) -> float | None:
    try:
        return datetime(y, m, d, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _month(name: str) -> int:
    return _text.MONTHS[name.lower().rstrip(".")]


def _midnight(ts: float) -> float:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp()


def _shift_months(ts: float, months: int) -> float:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    total = dt.year * 12 + (dt.month - 1) + months
    y, m = divmod(total, 12)
    d = min(dt.day, calendar.monthrange(y, m + 1)[1])
    return dt.replace(year=y, month=m + 1, day=d).timestamp()


def _relative(s: str, now: float) -> tuple[float, str] | None:
    s = s.lower().strip()
    if s in ("now", "just now"):
        return now, "time"
    if s in ("today", "this morning", "this afternoon", "this evening", "tonight", "earlier today"):
        return _midnight(now), "day"
    if s in ("yesterday", "last night"):
        return _midnight(now) - DAY, "day"
    if s == "this week":
        dt = datetime.fromtimestamp(now, timezone.utc)
        return _midnight(now) - dt.weekday() * DAY, "day"
    if s == "last week":
        return _midnight(now) - 7 * DAY, "day"
    if s == "this month":
        dt = datetime.fromtimestamp(now, timezone.utc)
        return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc).timestamp(), "month"
    if s == "last month":
        dt = datetime.fromtimestamp(_shift_months(now, -1), timezone.utc)
        return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc).timestamp(), "month"
    if s == "this year":
        return datetime(datetime.fromtimestamp(now, timezone.utc).year, 1, 1, tzinfo=timezone.utc).timestamp(), "year"
    if s == "last year":
        y = datetime.fromtimestamp(now, timezone.utc).year - 1
        return datetime(y, 1, 1, tzinfo=timezone.utc).timestamp(), "year"
    m = re.fullmatch(r"(\d+|a couple of|a few|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
                     r"\s+(second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago", s)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMWORDS[m.group(1)]
        unit = m.group(2)
        try:   # "12000 years ago" lies before year 1: not a calendar date
            if unit == "month":
                return _midnight(_shift_months(now, -n)), "day"
            if unit == "year":
                return _midnight(_shift_months(now, -12 * n)), "day"
            precision = "time" if unit in ("second", "sec", "minute", "min", "hour", "hr") else "day"
            ts = now - n * _UNITS[unit]
            return (ts if precision == "time" else _midnight(ts)), precision
        except (ValueError, OverflowError, OSError):
            return None
    m = re.fullmatch(r"(?:last|on|this past)\s+(" + "|".join(_WEEKDAYS) + ")", s)
    if m:
        target = _WEEKDAYS.index(m.group(1))
        today = datetime.fromtimestamp(now, timezone.utc).weekday()
        back = (today - target) % 7 or 7
        return _midnight(now) - back * DAY, "day"
    return None


def parse_date_precision(text: str, *, now: float | None = None) -> tuple[float, str] | None:
    """(timestamp, precision) for one date expression, precision in 'time' | 'day' | 'month' | 'year'.

    Accepts ISO 8601 dates and datetimes (Z or offsets; naive = UTC), "2026-11", "2026/11/03", RFC 2822,
    "3 November 2026", "Tuesday, 3 Nov 2026", "Nov 3, 2026", "November 2026", a bare year "2026", and — only
    when `now` is given — relative expressions ("yesterday", "2 days ago", "last week", "this week",
    "last Tuesday"). Month and year precision resolve to the first instant of the period. Impossible dates
    return None."""
    s = (text or "").strip().strip(".,;")
    if not s:
        return None
    s = re.sub(r"^(?:published|updated|posted|last updated|modified|date|on)\s*:?\s+", "", s, flags=re.IGNORECASE)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = map(int, s.split("-"))
        ts = _day(y, m, d)
        return (ts, "day") if ts is not None else None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?", s,
                    re.IGNORECASE):
        try:
            return _utc(datetime.fromisoformat(s.replace("z", "Z"))), "time"
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        ts = _day(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (ts, "day") if ts is not None else None
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        ts = _day(int(m.group(1)), int(m.group(2)), 1)
        return (ts, "month") if ts is not None else None
    m = re.fullmatch(rf"{_WEEKDAY_PREFIX}(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_M})\.?,?\s+(\d{{4}})", s, re.IGNORECASE)
    if m:
        ts = _day(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
        return (ts, "day") if ts is not None else None
    m = re.fullmatch(rf"{_WEEKDAY_PREFIX}({_M})\.?\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})", s, re.IGNORECASE)
    if m:
        ts = _day(int(m.group(3)), _month(m.group(1)), int(m.group(2)))
        return (ts, "day") if ts is not None else None
    m = re.fullmatch(rf"({_M})\.?,?\s+(\d{{4}})", s, re.IGNORECASE)
    if m:
        ts = _day(int(m.group(2)), _month(m.group(1)), 1)
        return (ts, "month") if ts is not None else None
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        return (_day(y, 1, 1), "year") if 1000 <= y <= 2999 else None
    if re.search(r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{1,2}:\d{2}", s):
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            dt = None
        if dt is not None:
            return _utc(dt), "time"
        return None
    if now is not None:
        return _relative(s, now)
    return None


def parse_date(text: str, *, now: float | None = None) -> float | None:
    """Timestamp of a date expression (see parse_date_precision); a bare year returns None."""
    got = parse_date_precision(text, now=now)
    if got is None or got[1] == "year":
        return None
    return got[0]


# ---- dates inside sentences ---------------------------------------------------------------------------------

_EVENT_PATTERNS = (
    re.compile(r"(?<![\d.])\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?![\d])"),
    re.compile(rf"\b\d{{1,2}}{_ORD}\s+(?:of\s+)?{_M}\.?,?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b{_M}\.?\s+\d{{1,2}}{_ORD},?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b{_M}\.?,?\s+\d{{4}}\b", re.IGNORECASE),
)
_EVENT_PARTIAL = re.compile(rf"\b(?:on\s+)?({_M})\.?\s+(\d{{1,2}}){_ORD}\b(?!,?\s*\d{{4}})|"
                            rf"\b(?:on\s+)?(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_M})\b(?!\.?,?\s*\d{{4}})", re.IGNORECASE)
_EVENT_RELATIVE = re.compile(      # (?<![\d,.]): "12,900 years ago" is not "900 years ago"
    r"(?<![\d,.])\b(?:yesterday|today|this (?:morning|afternoon|evening|week|month|year)|tonight|last (?:night|week|month|"
    r"year)|(?:last|on|this past) (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:\d+|a couple of|a few|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago)\b", re.IGNORECASE)


def _is_may_modal(match_text: str) -> bool:
    return match_text.lstrip().lower().startswith(("may", "on may")) and "May" not in match_text


def event_dates(sentence: str, *, reference: float | None = None) -> list[float]:
    """Dates mentioned in a sentence, in order of appearance, deduplicated.

    Absolute dates (full or month-year) are always returned; "Nov 3" without a year and relative expressions
    ("yesterday", "2 days ago", "last Tuesday") need `reference` (normally the publication date) and are skipped
    without one. Bare years are not event dates (too coarse); text.values() reports them as values."""
    s = sentence or ""
    found: list[tuple[int, int, float]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y, _ in found)

    for rx in _EVENT_PATTERNS:
        for m in rx.finditer(s):
            if not free(m.start(), m.end()) or _is_may_modal(m.group(0)):
                continue
            got = parse_date_precision(m.group(0))
            if got is not None:
                found.append((m.start(), m.end(), got[0]))
    if reference is not None:
        ref = datetime.fromtimestamp(reference, timezone.utc)
        for m in _EVENT_PARTIAL.finditer(s):
            if not free(m.start(), m.end()) or _is_may_modal(m.group(0)):
                continue
            month, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            ts = _day(ref.year, _month(month), int(day))
            if ts is not None and ts > reference + FUTURE_SKEW:
                ts = _day(ref.year - 1, _month(month), int(day))   # "Nov 3" said in January means last year
            if ts is not None:
                found.append((m.start(), m.end(), ts))
        for m in _EVENT_RELATIVE.finditer(s):
            if not free(m.start(), m.end()):
                continue
            got = _relative(m.group(0), reference)
            if got is not None:
                found.append((m.start(), m.end(), got[0]))
    found.sort()
    return list(dict.fromkeys(ts for _, _, ts in found))


# ---- staleness and windows ----------------------------------------------------------------------------------

def describe_age(ts: float | None, now: float) -> str:
    """'just now', '5 minutes ago', '3 hours ago', '2 days ago', or 'on 2026-11-03' (>= 7 days); 'undated'."""
    if ts is None:
        return "undated"
    delta = now - ts
    day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    if delta < -60:
        return f"dated in the future ({day})"
    if delta < 60:
        return "just now"
    for size, unit in ((7 * DAY, None), (DAY, "day"), (HOUR, "hour"), (60.0, "minute")):
        if unit is None:
            if delta >= size:
                return f"on {day}"
            continue
        if delta >= size:
            n = int(delta // size)
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def _span(seconds: float) -> str:
    for size, unit in ((365 * DAY, "year"), (DAY, "day"), (HOUR, "hour"), (60.0, "minute")):
        if seconds >= size:
            n = round(seconds / size, 1)
            n = int(n) if n == int(n) else n
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return f"{int(seconds)} seconds"


def is_stale(last_verified_at: float | None, freshness_class: str, now: float, *, ttl_overrides: dict | None = None,
             newer_release_seen: bool = False, revision_seen: bool = False) -> tuple[bool, str]:
    """(stale, reason). Stale when never verified, older than the class TTL (owner overrides win), or when a
    newer release (any class; the release class exists for this) or a new paper revision/erratum was observed."""
    limit = ttl(freshness_class, ttl_overrides)
    if newer_release_seen:
        return True, "stale: a newer release was observed after the last verification"
    if revision_seen:
        return True, "stale: a new version, erratum or retraction of the source was observed"
    if last_verified_at is None:
        return True, "stale: never verified"
    age = now - last_verified_at
    if age < -60:
        return False, f"fresh: verification time is in the future ({describe_age(last_verified_at, now)}); clock skew?"
    if age > limit:
        return True, (f"stale: last verified {describe_age(last_verified_at, now)}; the {freshness_class} class "
                      f"allows {_span(limit)}")
    return False, (f"fresh: verified {describe_age(last_verified_at, now)}, within the {freshness_class} class "
                   f"limit of {_span(limit)}")


def window_ok(published_at: float | None, window: float | None, now: float) -> bool | None:
    """True when published within `window` seconds before `now` (no window = True); None when undated; False when
    older, or dated more than FUTURE_SKEW in the future (a misparsed or scheduled date is not evidence of
    freshness)."""
    if published_at is None:
        return None
    if published_at > now + FUTURE_SKEW:
        return False
    if window is None:
        return True
    return published_at >= now - window


# ---- supersession and validity ------------------------------------------------------------------------------

def effective_time(claim: dict) -> float | None:
    """When the statement applies: its event date, else its publication date."""
    return claim.get("event_at") if claim.get("event_at") is not None else claim.get("published_at")


def supersedes(new: dict, old: dict, *, now: float | None = None) -> bool:
    """True when `new` replaces `old`: same key, conflicting values or polarity, and `new`'s effective time
    (event_at, else published_at) is strictly later than `old`'s. An undated `new` never supersedes. An undated
    `old` is bounded by its retrieval time (it was published no later than it was retrieved). A `new` dated more
    than FUTURE_SKEW after `now` (default: `new`'s retrieval time) never supersedes: a planned release or a
    misparsed date is not a newer state of the world."""
    if not new.get("key") or new.get("key") != old.get("key"):
        return False
    t_new = effective_time(new)
    if t_new is None:
        return False
    reference = now if now is not None else new.get("retrieved_at")
    if reference is not None and t_new > reference + FUTURE_SKEW:
        return False
    t_old = effective_time(old)
    if t_old is None:
        t_old = old.get("retrieved_at")
    if t_old is None or not t_new > t_old:
        return False
    if new.get("polarity", 1) != old.get("polarity", 1):
        return True
    return _text.values_conflict(list(new.get("values") or []), list(old.get("values") or [])) is not None


def validity(claim: dict, *, now: float) -> dict:
    """{valid_from, valid_until, basis}: a statement holds from its event date (else publication date, else
    retrieval time as an upper bound) until it is superseded (claim['superseded_at']) — open-ended otherwise. A
    start more than FUTURE_SKEW after `now` is not used: valid_from is None and the basis says why."""
    if claim.get("event_at") is not None:
        start, basis = claim["event_at"], "event date stated in the source"
    elif claim.get("published_at") is not None:
        start, basis = claim["published_at"], "publication date of the source"
    elif claim.get("retrieved_at") is not None:
        start, basis = claim["retrieved_at"], "retrieval time (undated source; true start is unknown and earlier)"
    else:
        start, basis = None, "no date information"
    until = claim.get("superseded_at")
    if until is not None:
        basis += "; ends when superseded by a newer conflicting statement"
    if start is not None and start > now + FUTURE_SKEW:
        start, basis = None, basis + "; the stated date lies in the future, so no start is asserted"
    return {"valid_from": start, "valid_until": until, "basis": basis}


__all__ = ["DAY", "FRESHNESS", "FUTURE_SKEW", "HOUR", "classify", "describe_age", "effective_time", "event_dates",
           "is_stale", "parse_date", "parse_date_precision", "supersedes", "ttl", "validity", "window_ok"]
