"""Lexical primitives for the evidence stage: tokens, sentences, BM25, shingles, value extraction, claim keys.

Deterministic and stdlib-only (ADR-022: there is no model). Everything here operates on untrusted text and only
measures it; nothing is executed or interpreted as an instruction.

Constants
---------
BM25_K1, BM25_B        Okapi BM25 parameters (Robertson/Zaragoza defaults: k1 = 1.5, b = 0.75).
NUMERIC_TOLERANCE      Two numbers of the same kind conflict only when they differ by more than 1 % relative.
DATE_TOLERANCE         Two calendar dates conflict only when they differ by more than one day.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re

BM25_K1 = 1.5
BM25_B = 0.75
NUMERIC_TOLERANCE = 0.01
DATE_TOLERANCE = 86400.0

STOPWORDS = frozenset("""
a about above after again against all also am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each else
etc ever every few for from further get got had hadn't has hasn't have haven't having he he'd he'll he's her here
here's hers herself him himself his how how's however i i'd i'll i'm i've if in into is isn't it it's its itself
just let's like many may me more most much must mustn't my myself near neither nor of off on once one only onto or
other ought our ours ourselves out over own per quite rather really s same said says shall shan't she she'd she'll
she's should shouldn't since so some still such t than that that's the their theirs them themselves then there
there's therefore these they they'd they'll they're they've this those though through thus to too toward towards
under until up upon us very via was wasn't we we'd we'll we're we've were weren't what what's when when's where
where's whether which while who who's whom whose why why's will with within without won't would wouldn't yet you
you'd you'll you're you've your yours yourself yourselves
""".split())

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
MONTH_RE = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
            r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")

# ---- tokens -------------------------------------------------------------------------------------------------

_VERSION = r"v?\d+(?:\.\d+)+(?:[-.]?(?:rc|a|b|alpha|beta|dev|post)\d*)?"
_TOKEN_RE = re.compile(
    rf"(?<![\w.])(?:{_VERSION})(?![\w])|\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|[^\W_]+", re.UNICODE | re.IGNORECASE)
_VERSION_TOKEN = re.compile(r"^(?:v\d+(?:\.\d+)+.*|\d+(?:\.\d+){2,}.*|\d+(?:\.\d+)+[-.]?(?:rc|a|b|alpha|beta|dev|post)\d*)$")
_NUMBER_TOKEN = re.compile(r"^\d+(?:\.\d+)?$")


def tokens(s: str, *, keep_case: bool = False) -> list[str]:
    """Lower-case, Unicode-aware word tokens. Version-like tokens ("2.3.5", "v1.2rc1") and alphanumerics
    ("cuda13") stay whole; thousands separators are removed ("1,200" -> "1200"). keep_case=True keeps the
    original case (used to spot entity-like tokens)."""
    out = []
    for m in _TOKEN_RE.finditer(s or ""):
        tok = m.group(0) if keep_case else m.group(0).lower()
        if "," in tok:
            tok = tok.replace(",", "")
        out.append(tok)
    return out


def content_tokens(s: str) -> list[str]:
    """tokens() without stopwords."""
    return [t for t in tokens(s) if t not in STOPWORDS]


def is_version(tok: str) -> bool:
    return bool(_VERSION_TOKEN.match(tok))


def is_number(tok: str) -> bool:
    return bool(_NUMBER_TOKEN.match(tok))


# ---- sentences ----------------------------------------------------------------------------------------------

# Never end a sentence after these (titles, Latin abbreviations, months: "Nov. 3").
_ABBREV_NEVER = frozenset("""
mr mrs ms dr prof sr jr st mt gen gov sen rep col lt sgt capt cmdr adm hon rev vs cf e.g i.e et al approx ca
viz resp esp dept univ assn bros jan feb mar apr jun jul aug sep sept oct nov dec u.s u.k u.n
""".split())
# Abbreviations that only continue the sentence when a number follows ("No. 5", "Fig. 3", "Eq. 2").
_ABBREV_NUMERIC = frozenset("no nos fig figs eq eqs vol vols pp p ch sec ref refs art tab".split())
_BOUNDARY = re.compile(r"[.!?]+[\"'”’)\]]*\s+")
_OPENER = re.compile(r"[\"'“‘(\[]*([A-ZÀ-ɏͰ-ϿЀ-ӿ\d])")
_LIST_OR_HEADING = re.compile(r"^\s*(?:[-*•#>]|\d+[.)]\s)")
_TERMINAL = re.compile(r"[.!?:;\"'”)\]]\s*$")


def _is_abbreviation(word: str, following: str) -> bool:
    w = word.rstrip(".").lower().lstrip("(\"'“[")
    if not w:
        return False
    if w in _ABBREV_NEVER:
        return True
    if w in _ABBREV_NUMERIC and re.match(r"\s*\d", following):
        return True
    if re.fullmatch(r"(?:[a-z]\.)+[a-z]", w):            # U.S, e.g style dotted initials
        return True
    if len(w) == 1 and word[0].isupper() and w.isalpha():  # initials: "J. Smith"
        return True
    return False


def _line_blocks(s: str, hard_newlines: bool = False) -> list[tuple[int, int]]:
    """Spans of text that may contain several sentences. Blank lines, list items, headings and lines that end
    with terminal punctuation are hard boundaries; a soft-wrapped line is joined with the next. With
    `hard_newlines` every line is its own block (text whose line breaks are block boundaries, e.g. HTML)."""
    spans: list[tuple[int, int]] = []
    start = None
    prev_line = ""
    pos = 0
    for line in s.splitlines(keepends=True):
        stripped = line.strip()
        line_start, pos = pos, pos + len(line)
        if not stripped:
            if start is not None:
                spans.append((start, line_start))
                start = None
            prev_line = ""
            continue
        hard = hard_newlines or start is None or _LIST_OR_HEADING.match(line) or _TERMINAL.search(prev_line)
        if not hard and len(prev_line.split()) <= 10 and stripped[:1].isupper():
            hard = True                                    # short unterminated line above = heading
        if hard:
            if start is not None:
                spans.append((start, line_start))
            start = line_start
        prev_line = stripped
    if start is not None:
        spans.append((start, len(s)))
    return spans


def sentence_spans(s: str, *, hard_newlines: bool = False) -> list[tuple[int, int]]:
    """(start, end) character spans of sentences in `s` (stripped of surrounding whitespace). `hard_newlines`:
    no sentence spans a line break (for text whose lines are block elements; see _line_blocks)."""
    s = s or ""
    out: list[tuple[int, int]] = []
    for b_start, b_end in _line_blocks(s, hard_newlines):
        block = s[b_start:b_end]
        cut = 0
        for m in _BOUNDARY.finditer(block):
            following = block[m.end():]
            if not _OPENER.match(following):
                continue                                   # next word is lower-case: not a boundary
            before = block[cut:m.start() + 1]
            word = before.split()[-1] if before.split() else ""
            if block[m.start()] == "." and _is_abbreviation(word, following):
                continue
            out.append((b_start + cut, b_start + m.end()))
            cut = m.end()
        out.append((b_start + cut, b_end))
    spans = []
    for a, b in out:
        while a < b and s[a].isspace():
            a += 1
        while b > a and s[b - 1].isspace():
            b -= 1
        if b > a:
            spans.append((a, b))
    return spans


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def sentences(s: str, *, hard_newlines: bool = False) -> list[str]:
    """Abbreviation-aware sentence split; whitespace inside a sentence is collapsed to single spaces.
    Decimals, versions, URLs, "e.g.", "et al.", "Dr.", "No. 5" and initials do not end a sentence.
    `hard_newlines=True` for text whose line breaks are block boundaries (HTML pages, feeds, API records)."""
    return [collapse(s[a:b]) for a, b in sentence_spans(s, hard_newlines=hard_newlines)]


# A heading or title is a line that does not end a sentence, names no finite verb, and is short or in Title Case.
HEADING_MAX_WORDS = 12
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*$")
_FINITE_VERB = re.compile(
    r"\b(?:is|are|was|were|am|be|been|has|have|had|will|would|can|could|may|might|shall|should|must|does|do|did|"
    r"isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|won't|can't|doesn't|don't|didn't|says|said|shows|ships|"
    r"runs|uses|makes|gets|adds|brings|supports|reports|reaches|opens|launches|offers|includes|contains|provides|"
    r"requires|remains|becomes|takes|gives|lists|drops|rises|falls|grows|holds|leads|wins|loses|lies|flows|"
    r"drains|listens|needs|means|stands|sits|costs|weighs|measures|[a-z]{2,}ed)\b", re.IGNORECASE)


def heading_like(line: str, title: str = "") -> bool:
    """True for a heading or a page title rather than a sentence: equal to `title` (case and whitespace
    insensitive), or without sentence-final punctuation, without a finite-verb cue (an auxiliary, a common
    present-tense verb or an -ed form) and either at most HEADING_MAX_WORDS words or in Title Case."""
    s = collapse(line)
    if not s:
        return False
    if title and s.rstrip(".:!?").lower() == collapse(title).rstrip(".:!?").lower():
        return True
    if _SENTENCE_END.search(s) or _FINITE_VERB.search(s):
        return False
    words = [w for w in s.split() if any(c.isalpha() for c in w)]
    content = [w for w in words if w.lower() not in STOPWORDS]
    title_case = len(content) >= 2 and sum(w[0].isupper() for w in content) >= 0.7 * len(content)
    return len(s.split()) <= HEADING_MAX_WORDS or title_case


# ---- BM25 ---------------------------------------------------------------------------------------------------

class BM25:
    """Okapi BM25 over pre-tokenised documents. idf = ln(1 + (N - df + 0.5) / (df + 0.5)) (never negative)."""

    def __init__(self, docs: list[list[str]], *, k1: float = BM25_K1, b: float = BM25_B):
        self.k1, self.b = k1, b
        self.tf = []
        df: dict[str, int] = {}
        for doc in docs:
            counts: dict[str, int] = {}
            for t in doc:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        self.lengths = [len(d) for d in docs]
        self.n = len(docs)
        self.avgdl = (sum(self.lengths) / self.n) if self.n else 0.0
        self.idf = {t: math.log(1.0 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query_tokens) -> list[float]:
        terms = list(dict.fromkeys(query_tokens))          # each query term counts once
        out = []
        for counts, length in zip(self.tf, self.lengths):
            norm = self.k1 * (1 - self.b + self.b * length / self.avgdl) if self.avgdl else self.k1
            total = 0.0
            for t in terms:
                f = counts.get(t)
                if f:
                    total += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(total)
        return out


# ---- similarity ---------------------------------------------------------------------------------------------

def shingles(s: str, k: int = 5) -> set:
    """Contiguous k-word shingles of tokens(s) (stopwords kept: copying preserves them). A text shorter than k
    words yields one shingle of the whole text."""
    toks = tokens(s)
    if not toks:
        return set()
    if len(toks) < k:
        return {" ".join(toks)}
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def jaccard(a, b) -> float:
    """|a ∩ b| / |a ∪ b|; 0.0 when either side is empty (no evidence of similarity)."""
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---- values -------------------------------------------------------------------------------------------------

_MAGNITUDE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9,
              "b": 1e9, "trillion": 1e12, "tn": 1e12}
_CURRENCY_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCY_WORD = {"usd": "USD", "dollars": "USD", "dollar": "USD", "eur": "EUR", "euros": "EUR", "euro": "EUR",
                  "gbp": "GBP", "pounds": "GBP", "jpy": "JPY", "yen": "JPY", "chf": "CHF"}
_UNITS = ("percentage points", "per cent", "percent", "pp", "%", "mg/kg", "x", "fold", "participants", "patients",
          "subjects", "volunteers", "people", "neurons", "cells", "mice", "rats", "monkeys", "animals", "slices",
          "samples", "users", "downloads", "stars", "times", "points", "sites", "studies", "trials", "cases",
          "deaths", "employees")
# Physical units: spelling (lower case) -> canonical unit. Every spelling of one unit yields the same canonical
# value string ("412 kilometres" and "412 km" are both "412 km").
_UNIT_SPELLINGS = {
    "km": ("km", "kilometre", "kilometres", "kilometer", "kilometers"),
    "m": ("m", "metre", "metres", "meter", "meters"),
    "cm": ("cm", "centimetre", "centimetres", "centimeter", "centimeters"),
    "mm": ("mm", "millimetre", "millimetres", "millimeter", "millimeters"),
    "µm": ("µm", "um", "micrometre", "micrometres", "micrometer", "micrometers", "micron", "microns"),
    "nm": ("nm", "nanometre", "nanometres", "nanometer", "nanometers"),
    "mi": ("mi", "mile", "miles"),
    "ft": ("ft", "foot", "feet"),
    "km²": ("km²", "km2", "sq km", "square kilometre", "square kilometres", "square kilometer", "square kilometers"),
    "m²": ("m²", "m2", "sq m", "square metre", "square metres", "square meter", "square meters"),
    "mi²": ("mi²", "sq mi", "square mile", "square miles"),
    "ha": ("ha", "hectare", "hectares"),
    "m³/s": ("m³/s", "m3/s", "cubic metres per second", "cubic meters per second", "cubic metre per second",
             "cubic meter per second"),
    "m³": ("m³", "m3", "cubic metre", "cubic metres", "cubic meter", "cubic meters"),
    "l": ("l", "litre", "litres", "liter", "liters"),
    "ml": ("ml", "millilitre", "millilitres", "milliliter", "milliliters"),
    "µl": ("µl", "ul", "microlitre", "microlitres", "microliter", "microliters"),
    "t": ("tonne", "tonnes", "metric ton", "metric tons"),
    "kg": ("kg", "kilogram", "kilograms", "kilogramme", "kilogrammes"),
    "g": ("g", "gram", "grams", "gramme", "grammes"),
    "mg": ("mg", "milligram", "milligrams"),
    "µg": ("µg", "ug", "microgram", "micrograms"),
    "ns": ("ns", "nanosecond", "nanoseconds"),
    "µs": ("µs", "us", "microsecond", "microseconds"),
    "ms": ("ms", "millisecond", "milliseconds"),
    "s": ("s", "sec", "secs", "second", "seconds"),
    "min": ("min", "mins", "minute", "minutes"),
    "h": ("h", "hr", "hrs", "hour", "hours"),
    "days": ("day", "days"),
    "weeks": ("week", "weeks"),
    "months": ("month", "months"),
    "years": ("year", "years"),
    "kb": ("kb", "kilobyte", "kilobytes"),
    "mb": ("mb", "megabyte", "megabytes"),
    "gb": ("gb", "gigabyte", "gigabytes"),
    "tb": ("tb", "terabyte", "terabytes"),
    "hz": ("hz", "hertz"),
    "khz": ("khz", "kilohertz"),
    "mhz": ("mhz", "megahertz"),
    "ghz": ("ghz", "gigahertz"),
    "w": ("w", "watt", "watts"),
    "kw": ("kw", "kilowatt", "kilowatts"),
    "mw": ("mw", "megawatt", "megawatts"),
    "gw": ("gw", "gigawatt", "gigawatts"),
    "v": ("v", "volt", "volts"),
    "kv": ("kv", "kilovolt", "kilovolts"),
    "mv": ("mv", "millivolt", "millivolts"),
    "°c": ("°c",), "°f": ("°f",), "k": ("k",),
}
_UNIT_CANONICAL = {spelling: unit for unit, spellings in _UNIT_SPELLINGS.items() for spelling in spellings}
# Canonical unit -> (dimension, factor to the dimension's base unit). Quantities compare within one dimension,
# converted to the base unit ("1.2 km" and "1200 m" agree); temperatures and other units compare only with
# themselves.
_DIMENSION = {
    "km": ("length", 1e3), "m": ("length", 1.0), "cm": ("length", 1e-2), "mm": ("length", 1e-3),
    "µm": ("length", 1e-6), "nm": ("length", 1e-9), "mi": ("length", 1609.344), "ft": ("length", 0.3048),
    "km²": ("area", 1e6), "m²": ("area", 1.0), "mi²": ("area", 2_589_988.110336), "ha": ("area", 1e4),
    "m³/s": ("flow", 1.0), "m³": ("volume", 1.0), "l": ("volume", 1e-3), "ml": ("volume", 1e-6),
    "µl": ("volume", 1e-9), "t": ("mass", 1e3), "kg": ("mass", 1.0), "g": ("mass", 1e-3), "mg": ("mass", 1e-6),
    "µg": ("mass", 1e-9), "ns": ("time", 1e-9), "µs": ("time", 1e-6), "ms": ("time", 1e-3), "s": ("time", 1.0),
    "min": ("time", 60.0), "h": ("time", 3600.0), "days": ("time", 86400.0), "weeks": ("time", 604800.0),
    "months": ("time", 2_629_800.0), "years": ("time", 31_557_600.0), "kb": ("data", 1e3), "mb": ("data", 1e6),
    "gb": ("data", 1e9), "tb": ("data", 1e12), "hz": ("frequency", 1.0), "khz": ("frequency", 1e3),
    "mhz": ("frequency", 1e6), "ghz": ("frequency", 1e9), "w": ("power", 1.0), "kw": ("power", 1e3),
    "mw": ("power", 1e6), "gw": ("power", 1e9), "v": ("voltage", 1.0), "kv": ("voltage", 1e3),
    "mv": ("voltage", 1e-3),
}
_NUM = r"[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT_RE = "|".join(re.escape(u).replace(r"\ ", r"\s+")
                    for u in sorted(set(_UNITS) | set(_UNIT_CANONICAL), key=len, reverse=True))
# A bare number coordinated with a following quantity shares its unit: "between 400 and 520 km", "400–520 km",
# "5 to 10 mg", "3 or 4 days". "and" only after "between" (else "Nimbus-OS 5 and 512 MB" would become 5 MB).
_RANGE_JOIN = re.compile(r"\s*(?:–|—|to|or)\s*|\s+and\s+", re.IGNORECASE)
_BETWEEN = re.compile(r"\bbetween\s+$", re.IGNORECASE)
_MAG_RE = r"(?:thousand|million|billion|trillion|bn|mn|tn|k|m|b)"

_DATE_ISO = re.compile(r"(?<![\d.])(\d{4})-(\d{2})-(\d{2})(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?(?![\d])")
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_RE})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_DATE_MDY = re.compile(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_DECADE = re.compile(r"\b\d{3}0['’]?s\b")
_CITATION = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]")
_VALUE_RE = re.compile(
    rf"(?P<money1>(?P<cur>[$€£¥])\s?(?P<m1>{_NUM})(?:\s?(?P<mag1>{_MAG_RE})\b)?)"
    rf"|(?P<version>(?<![\w.])(?:(?:version|release|v)\s?)(?P<ver>\d+(?:\.\d+)+(?:[-.]?(?:rc|a|b|alpha|beta|dev|post)\d*)?)(?![\w.]*\d)"
    rf"|(?<![\w.])(?P<ver2>\d+(?:\.\d+){{2,}}(?:[-.]?(?:rc|a|b|alpha|beta|dev|post)\d*)?|\d+\.\d+[-.]?(?:rc|a|b|alpha|beta|dev|post)\d*)(?![\w.]))"
    rf"|(?<![\w.\-/])(?P<num>{_NUM})(?:\s?(?P<mag>thousand|million|billion|trillion|bn|mn|tn)\b)?"
    rf"(?:\s?(?P<curw>usd|eur|gbp|jpy|chf|dollars?|euros?|pounds|yen)\b"
    rf"|\s?(?P<unit>{_UNIT_RE})(?![\w%]))?(?!\w)",
    re.IGNORECASE)


def _fmt(x: float) -> str:
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.6g}"


def _num(raw: str) -> float:
    return float(raw.replace(",", "").replace("−", "-"))


def _date_values(s: str) -> list[tuple[int, str]]:
    out = []
    for rx, order in ((_DATE_ISO, "ymd"), (_DATE_DMY, "dmy"), (_DATE_MDY, "mdy")):
        for m in rx.finditer(s):
            if order == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif order == "dmy":
                d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower().rstrip(".")], int(m.group(3))
            else:
                mo, d, y = MONTHS[m.group(1).lower().rstrip(".")], int(m.group(2)), int(m.group(3))
            if order != "ymd" and m.group(0)[:3].lower() == "may" and not m.group(0).startswith("May"):
                continue
            try:
                datetime(y, mo, d)
            except ValueError:
                continue
            out.append((m.start(), m.end(), f"{y:04d}-{mo:02d}-{d:02d}"))
    out.sort()
    kept, last_end = [], -1
    for a, b, v in out:
        if a >= last_end:
            kept.append((a, b, v))
            last_end = b
    return kept


def values(s: str) -> list[str]:
    """Values mentioned in `s`, in order, deduplicated, as canonical strings:

    - calendar dates "2026-11-03" (ISO, "3 November 2026", "Nov 3, 2026");
    - versions "v2.3.5" (two-dot numbers, pre-release suffixes, or a number after "version"/"release"/"v");
    - money "USD 1200000"; percentages "42%"; percentage points "3 pp";
    - quantities with a unit "24 mg", "24 participants", with the unit spelling normalised ("412 kilometres" ->
      "412 km", "9,800 square kilometres" -> "9800 km²"); years "2026"; plain numbers "3.5".

    A bare number coordinated with a following quantity takes its unit ("between 400 and 520 km" -> "400 km",
    "520 km"; joiners: en/em dash, "to", "or", "and" after "between"). Numbers glued to letters (cuda13, H100,
    COVID-19), ordinals and bracketed citation markers are ignored.
    """
    s = s or ""
    found: list[tuple[int, str]] = []
    numbers: list[list] = []                # [start, end, x, unit or None, index in found] for plain numbers
    masked = list(s)
    for a, b, v in _date_values(s):
        found.append((a, v))
        masked[a:b] = " " * (b - a)
    for m in _DECADE.finditer(s):
        masked[m.start():m.end()] = " " * (m.end() - m.start())
    for m in _CITATION.finditer(s):
        masked[m.start():m.end()] = " " * (m.end() - m.start())
    text = "".join(masked)
    for m in _VALUE_RE.finditer(text):
        g = m.groupdict()
        if g["money1"]:
            amount = _num(g["m1"]) * _MAGNITUDE.get((g["mag1"] or "").lower(), 1.0)
            if not math.isfinite(amount):
                continue                                       # a 309+ digit run is not a value
            found.append((m.start(), f"{_CURRENCY_SYMBOL[g['cur']]} {_fmt(amount)}"))
        elif g["ver"] or g["ver2"]:
            found.append((m.start(), "v" + (g["ver"] or g["ver2"]).lower()))
        elif g["num"]:
            raw = g["num"]
            after = text[m.end():m.end() + 3]
            if re.match(r"(?:st|nd|rd|th)\b", after, re.IGNORECASE):
                continue
            x = _num(raw)
            if g["mag"]:
                x *= _MAGNITUDE[g["mag"].lower()]
            if not math.isfinite(x):
                continue
            if g["curw"]:
                found.append((m.start(), f"{_CURRENCY_WORD[g['curw'].lower()]} {_fmt(x)}"))
                continue
            unit = _unit(g["unit"] or "")
            if unit:
                numbers.append([m.start(), m.end(), x, unit, len(found)])
                found.append((m.start(), _with_unit(x, unit)))
            elif re.fullmatch(r"\d{4}", raw) and 1000 <= x <= 2999:
                found.append((m.start(), raw))                 # a year
            else:
                numbers.append([m.start(), m.end(), x, None, len(found)])
                found.append((m.start(), _fmt(x)))
    for here, nxt in reversed(list(zip(numbers, numbers[1:]))):
        join = _RANGE_JOIN.fullmatch(text[here[1]:nxt[0]])
        if here[3] is None and nxt[3] and join and \
                ("and" not in join.group(0).lower() or _BETWEEN.search(text[:here[0]])):
            here[3] = nxt[3]
            found[here[4]] = (here[0], _with_unit(here[2], nxt[3]))
    found.sort(key=lambda p: p[0])
    return list(dict.fromkeys(v for _, v in found))


def _unit(raw: str) -> str:
    """Canonical unit of a unit spelling: '%', 'pp', a physical unit ('km', 'km²', ...) or a count noun."""
    u = re.sub(r"\s+", " ", raw.strip().lower())
    if u in ("%", "percent", "per cent"):
        return "%"
    if u in ("percentage points", "pp"):
        return "pp"
    return _UNIT_CANONICAL.get(u, u)


def _with_unit(x: float, unit: str) -> str:
    return f"{_fmt(x)}%" if unit == "%" else f"{_fmt(x)} {unit}"


def value_kind(v: str) -> tuple[str, str, object]:
    """Classify a canonical value string -> (kind, comparison group, parsed value).

    kind in: date, year, version, money, percent, points, quantity, number. Values are comparable only within
    the same comparison group: money per currency (money:USD), quantities per physical dimension converted to
    its base unit (quantity:length, "412 km" -> 412000.0; any spelling of a unit is accepted) or per count noun
    (quantity:mice), plain numbers as integers (number:int) or decimals (number:dec)."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        ts = datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        return "date", "date", ts
    if re.fullmatch(r"\d{4}", v):
        return "year", "year", int(v)
    if v.startswith("v") and re.match(r"v\d", v):
        return "version", "version", v[1:]
    m = re.fullmatch(r"([A-Z]{3}) (-?[\d.e+]+)", v)
    if m:
        return "money", "money:" + m.group(1), float(m.group(2))
    if v.endswith("%"):
        return "percent", "percent", float(v[:-1])
    m = re.fullmatch(r"(-?[\d.e+]+) (.+)", v)
    if m:
        unit = _unit(m.group(2))
        if unit == "pp":
            return "points", "points", float(m.group(1))
        if unit in _DIMENSION:
            dimension, factor = _DIMENSION[unit]
            return "quantity", "quantity:" + dimension, float(m.group(1)) * factor
        return "quantity", "quantity:" + unit, float(m.group(1))
    try:
        x = float(v)
    except ValueError:
        return "text", "text:" + v, v
    return "number", "number:dec" if "." in v else "number:int", x


_TEMPORAL_KINDS = ("date", "year")
_PRECISE_KINDS = ("number", "percent", "points", "quantity")
_BARE = ("number:int", "number:dec")


def _decimals(v: str) -> int:
    m = re.match(r"-?\d+\.(\d+)(?![\d.e])", v)
    return len(m.group(1)) if m else 0


def _same(kind: str, a, b, sa: str = "", sb: str = "") -> bool:
    """Equal within tolerance. Two numbers written with the same number of decimal places (>= 1) must be equal:
    the relative tolerance absorbs rounding ("42%" vs "42.2%"), and "6.12" vs "6.14" is not a rounding."""
    if kind == "date":
        return abs(a - b) <= DATE_TOLERANCE
    if kind in ("version", "year") or kind.startswith("text"):
        return a == b
    if kind in _PRECISE_KINDS and _decimals(sa) and _decimals(sa) == _decimals(sb):
        return math.isclose(a, b, rel_tol=1e-9)
    scale = max(abs(a), abs(b))
    return scale == 0 or abs(a - b) / scale <= NUMERIC_TOLERANCE


def _magnitude(v: str) -> int | None:
    m = re.match(r"-?[\d.]+", v)
    x = abs(float(m.group(0))) if m else 0.0
    return math.floor(math.log10(x)) if x else None


def _attach_bare(x: dict, y: dict) -> None:
    """A plain number on side x stands for a quantity on side y when it is x's only plain number of the same
    order of magnitude (floor log10 of the written number) as y's values of that quantity, x states no value of
    that quantity, and no other quantity on y has that magnitude ("412" vs "520 km"; not "24" vs "520 km", not
    "412, 450" vs "520 km"). It is then compared in that unit, in addition to its own group."""
    bare = [e for g in (*_BARE, "number") for e in x.get(g, [])]
    if not bare:
        return
    quantities = {g: {_magnitude(e[0]) for e in y[g]} for g in y if g.startswith("quantity:") and g not in x}
    for group, classes in quantities.items():
        rivals = set().union(*(c for g, c in quantities.items() if g != group)) if len(quantities) > 1 else set()
        for cls in classes - rivals - {None}:
            hits = [e for e in bare if _magnitude(e[0]) == cls]
            if len(hits) != 1:
                continue
            unit = y[group][0][0].split(" ", 1)[1]
            factor = _DIMENSION.get(_unit(unit), ("", 1.0))[1]
            x.setdefault(group, []).append((hits[0][0], hits[0][1] * factor, "quantity"))


def _group_conflict(va: list[str], vb: list[str], temporal: bool) -> str | None:
    """Like-for-like comparison: every comparison group present on both sides is compared on its own, and the
    first group with no equal pair is a conflict, so a value shared in one group ("Nimbus-OS 5") cannot mask a
    disagreement in another ("kernel 6.14" vs "6.12"). Integers and decimals are separate groups, merged only
    when the two sides share neither; a plain number may stand for a quantity (_attach_bare)."""
    ga: dict[str, list] = {}
    gb: dict[str, list] = {}
    for values_, groups in ((va, ga), (vb, gb)):
        for v in values_:
            kind, group, parsed = value_kind(v)
            if (kind in _TEMPORAL_KINDS) == temporal:
                groups.setdefault(group, []).append((v, parsed, kind))
    if not temporal:
        if not set(_BARE) & set(ga) & set(gb):
            for groups in (ga, gb):
                merged = [e for g in _BARE for e in groups.pop(g, [])]
                if merged:
                    groups["number"] = merged
        _attach_bare(ga, gb)
        _attach_bare(gb, ga)
    for group in sorted(set(ga) & set(gb)):
        overlap = any(_same(ka, pa, pb, sa, sb) for sa, pa, ka in ga[group] for sb, pb, _ in gb[group])
        if not overlap:
            left = ", ".join(v for v, _, _ in ga[group])
            right = ", ".join(v for v, _, _ in gb[group])
            label = {"version": "different versions", "date": "different dates",
                     "year": "different years"}.get(ga[group][0][2], "different values")
            return f"{label}: {left} vs {right}"
    return None


def values_conflict(va: list[str], vb: list[str]) -> str | None:
    """Reason string when two value lists disagree, else None.

    Values are compared like for like within one comparison group (value_kind; _group_conflict). A group
    conflicts when no pair is equal within tolerance (numbers > 1 % relative unless written to the same
    decimal places, versions exact, dates > 1 day). A plain number is compared with a quantity only by the
    magnitude rule of _attach_bare. When both the temporal values (dates/years) and the other values differ,
    the statements describe different reference periods (a series, e.g. revenue in 2024 vs 2025), which is
    not a contradiction."""
    if any(value_kind(v)[0] == "version" for v in list(va) + list(vb)):
        # "is 2.3" next to "version 2.4": a bare dotted decimal is compared as a version
        va = ["v" + v if re.fullmatch(r"\d+\.\d+", v) else v for v in va]
        vb = ["v" + v if re.fullmatch(r"\d+\.\d+", v) else v for v in vb]
    other = _group_conflict(va, vb, temporal=False)
    when = _group_conflict(va, vb, temporal=True)
    if other and when:
        return None
    return other or when


# ---- claim keys ---------------------------------------------------------------------------------------------

def normalise(s: str) -> str:
    """Claim normal form: lower-case tokens in their original order, punctuation, stopwords and single letters removed,
    versions masked as <ver>, other numbers as <num>, whitespace collapsed."""
    out = []
    for t in tokens(s):
        if is_version(t):
            out.append("<ver>")
        elif is_number(t):
            out.append("<num>")
        elif t not in STOPWORDS and not (len(t) == 1 and t.isalpha()):
            out.append(t)
    return " ".join(out)


def stem(t: str) -> str:
    """A deliberately tiny suffix stripper so "released"/"release"/"releases" and "study"/"studies" meet."""
    if len(t) <= 4 or not t.isalpha():
        return t
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("ly", ""), ("s", "")):
        if t.endswith(suffix) and not t.endswith("ss") and len(t) - len(suffix) >= 3:
            t = t[: len(t) - len(suffix)] + repl
            break
    if len(t) > 4 and t.endswith("e"):
        t = t[:-1]
    return t


__all__ = ["BM25", "BM25_B", "BM25_K1", "DATE_TOLERANCE", "MONTHS", "MONTH_RE", "NUMERIC_TOLERANCE", "STOPWORDS",
           "collapse", "content_tokens", "is_number", "is_version", "jaccard", "normalise", "sentence_spans",
           "sentences", "shingles", "stem", "tokens", "value_kind", "values", "values_conflict"]
