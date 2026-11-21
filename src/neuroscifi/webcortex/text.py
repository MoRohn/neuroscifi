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


def _line_blocks(s: str) -> list[tuple[int, int]]:
    """Spans of text that may contain several sentences. Blank lines, list items, headings and lines that end
    with terminal punctuation are hard boundaries; a soft-wrapped line is joined with the next."""
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
        hard = start is None or _LIST_OR_HEADING.match(line) or _TERMINAL.search(prev_line)
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


def sentence_spans(s: str) -> list[tuple[int, int]]:
    """(start, end) character spans of sentences in `s` (stripped of surrounding whitespace)."""
    s = s or ""
    out: list[tuple[int, int]] = []
    for b_start, b_end in _line_blocks(s):
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


def sentences(s: str) -> list[str]:
    """Abbreviation-aware sentence split; whitespace inside a sentence is collapsed to single spaces.
    Decimals, versions, URLs, "e.g.", "et al.", "Dr.", "No. 5" and initials do not end a sentence."""
    return [collapse(s[a:b]) for a, b in sentence_spans(s)]


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
_UNITS = ("percentage points", "per cent", "percent", "pp", "%", "ms", "µs", "us", "ns", "seconds", "second",
          "sec", "s", "minutes", "minute", "min", "hours", "hour", "h", "days", "day", "weeks", "week", "months",
          "month", "years", "year", "mg/kg", "mg", "µg", "ug", "kg", "g", "ml", "µl", "ul", "l", "nm", "µm", "um",
          "mm", "cm", "km", "m", "ghz", "mhz", "khz", "hz", "tb", "gb", "mb", "kb", "gw", "mw", "kw", "w", "mv",
          "kv", "v", "°c", "°f", "k", "x", "fold", "participants", "patients", "subjects", "volunteers", "people",
          "neurons", "cells", "mice", "rats", "monkeys", "animals", "slices", "samples", "users", "downloads",
          "stars", "times", "points", "sites", "studies", "trials", "cases", "deaths", "employees")
_NUM = r"[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT_RE = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
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
    - quantities with a unit "24 mg", "24 participants"; years "2026"; plain numbers "3.5".

    Numbers glued to letters (cuda13, H100, COVID-19), ordinals and bracketed citation markers are ignored.
    """
    s = s or ""
    found: list[tuple[int, str]] = []
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
            if g["curw"]:
                found.append((m.start(), f"{_CURRENCY_WORD[g['curw'].lower()]} {_fmt(x)}"))
                continue
            unit = (g["unit"] or "").lower()
            if unit in ("%", "percent", "per cent"):
                found.append((m.start(), f"{_fmt(x)}%"))
            elif unit in ("percentage points", "pp"):
                found.append((m.start(), f"{_fmt(x)} pp"))
            elif unit:
                found.append((m.start(), f"{_fmt(x)} {unit}"))
            elif re.fullmatch(r"\d{4}", raw) and 1000 <= x <= 2999:
                found.append((m.start(), raw))                 # a year
            else:
                found.append((m.start(), _fmt(x)))
    found.sort(key=lambda p: p[0])
    return list(dict.fromkeys(v for _, v in found))


def value_kind(v: str) -> tuple[str, str, object]:
    """Classify a canonical value string -> (kind, comparison group, parsed value).

    kind in: date, year, version, money, percent, points, quantity, number. Values are comparable only within
    the same comparison group (e.g. quantity:mg vs quantity:mg, money:USD vs money:USD)."""
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
        if m.group(2) == "pp":
            return "points", "points", float(m.group(1))
        return "quantity", "quantity:" + m.group(2), float(m.group(1))
    try:
        return "number", "number", float(v)
    except ValueError:
        return "text", "text:" + v, v


_TEMPORAL_KINDS = ("date", "year")


def _same(kind: str, a, b) -> bool:
    if kind == "date":
        return abs(a - b) <= DATE_TOLERANCE
    if kind in ("version", "year") or kind.startswith("text"):
        return a == b
    scale = max(abs(a), abs(b))
    return scale == 0 or abs(a - b) / scale <= NUMERIC_TOLERANCE


def _group_conflict(va: list[str], vb: list[str], temporal: bool) -> str | None:
    ga: dict[str, list] = {}
    gb: dict[str, list] = {}
    for v in va:
        kind, group, parsed = value_kind(v)
        if (kind in _TEMPORAL_KINDS) == temporal:
            ga.setdefault(group, []).append((v, parsed, kind))
    for v in vb:
        kind, group, parsed = value_kind(v)
        if (kind in _TEMPORAL_KINDS) == temporal:
            gb.setdefault(group, []).append((v, parsed, kind))
    for group in sorted(set(ga) & set(gb)):
        overlap = any(_same(ka, pa, pb) for _, pa, ka in ga[group] for _, pb, _ in gb[group])
        if not overlap:
            left = ", ".join(v for v, _, _ in ga[group])
            right = ", ".join(v for v, _, _ in gb[group])
            label = {"version": "different versions", "date": "different dates",
                     "year": "different years"}.get(ga[group][0][2], "different values")
            return f"{label}: {left} vs {right}"
    return None


def values_conflict(va: list[str], vb: list[str]) -> str | None:
    """Reason string when two value lists disagree, else None.

    Values are compared only within one comparison group (value_kind). A group conflicts when no pair is equal
    within tolerance (numbers > 1 % relative, versions exact, dates > 1 day). When both the temporal values
    (dates/years) and the other values differ, the statements describe different reference periods (a series,
    e.g. revenue in 2024 vs 2025), which is not a contradiction."""
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
