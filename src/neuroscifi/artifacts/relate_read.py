"""The careful, bounded reader of what an artifact's own words STATE (lane R; ADR-077): numbers with their units and
nearby words, sizes, ranges, counts, ordered steps and timestamped events. Used by `artifacts/relate.py`.

Everything read here is UNTRUSTED text: a statement keeps its sentence only as provenance, for the owner to see, and
the sentence is never interpreted beyond the patterns below. The vocabularies (units, aggregate words, sequence
words, plural nouns) are general English and SI, written once; none is a list per benchmark case.

Number formats: plain `1234.5`; grouped `1,234.5`, `1.234,5`, `1 234,5` (no-break or thin space) and `1'234.5`;
decimal comma `12,5`. A number that one format alone cannot settle (`1,234`) is read with the text's own convention
(the unambiguous numbers elsewhere in the same text), and both readings are kept when the text has none. Dates,
times, versions (`1.2.3`) and numbers glued to words (`v2`, `x86`) are not quantities. Ranges: `10 to 70`, `10-70`,
`between 10 and 70`. Sizes: `120 x 40 x 60 mm`, `640×480`. "About", "roughly" etc. widen the tolerance; "more than",
"under" etc. make a bound.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_TEXT = 256 << 10          # characters of an artifact's words read
MAX_SENTENCES = 4000
MAX_NUMBERS = 64              # per sentence
MAX_STATEMENTS = 2000
MAX_EVENTS = 20_000
MAX_ITEMS = 2000
SENTENCE_KEEP = 240           # characters of a sentence kept as provenance

# ---- words ---------------------------------------------------------------------------------------------------------

STOP = frozenset("""a an the and or but nor of to in on at by for from with without into onto over under is are was
were be been being am do does did done has have had having this that these those it its it's they them their there
here as so than then very also just only not no yes all any each every some such which who whom whose what when where
why how i we you he she me us my our your his her one per via up down out off about above below again further once
s will would can could shall should may might must please get got let""".split())
# words that tell one step from another ("link up" / "link down", "power on" / "power off", "not")
STEP_KEEP = frozenset("up down on off in out over under above below not no".split())
SEQUENCE_WORDS = frozenset("""first firstly then next finally afterwards afterward later subsequently lastly last
second secondly third thirdly fourth fifth meanwhile eventually before after soon""".split())

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def singular(word: str) -> str:
    """A plain English plural folded to its singular (rows -> row, entries -> entry, vertices -> vertex)."""
    w = word.lower()
    if len(w) <= 3 or not w.endswith("s") or w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("ices") and len(w) > 5:
        return w[:-4] + "ex"
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "xes", "sses", "zes")):
        return w[:-2]
    return w[:-1]


def tokens(text: str) -> list[str]:
    """Lower-case word and digit tokens, in order, bounded."""
    return _TOKEN.findall((text or "")[:MAX_TEXT].lower())


def content_words(text: str, *, drop=frozenset()) -> tuple[str, ...]:
    """Distinct singular content words of a text (no function words, no bare numbers), in order of appearance."""
    out: dict[str, None] = {}
    for t in tokens(text):
        if t in STOP or t in drop or t.isdigit():
            continue
        out[singular(t)] = None
    return tuple(out)


def name_words(name: str) -> tuple[str, ...]:
    """The words of a name (a column name, a series name, a file label): split on case changes, digits and
    punctuation; singular; function words dropped."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name or "")
    spaced = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", spaced.strip())      # a file name's extension is not a word of it
    return content_words(spaced.replace("_", " "))


def clean(text: str, limit: int = SENTENCE_KEEP) -> str:
    """An artifact's words made safe to print to the owner: control and bidirectional-override characters removed,
    white space folded, cut. Still untrusted (never given to a model by this module)."""
    text = re.sub(r"[\x00-\x1f\x7f‎‏‪-‮⁦-⁩]", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---- units ---------------------------------------------------------------------------------------------------------
# unit word -> (dimension, factor to the dimension's base unit, canonical symbol). Bases: mm, s, Hz, px, byte, g,
# ml, mm2, mm3, degree, percent; money is one dimension per currency (no exchange rates).

def _units() -> dict:
    table = {}

    def add(dim, factor, symbol, *names):
        for n in names:
            table[n] = (dim, factor, symbol)
    add("length", 1.0, "mm", "mm", "millimetre", "millimetres", "millimeter", "millimeters")
    add("length", 10.0, "cm", "cm", "centimetre", "centimetres", "centimeter", "centimeters")
    add("length", 1000.0, "m", "m", "metre", "metres", "meter", "meters")
    add("length", 1e6, "km", "km", "kilometre", "kilometres", "kilometer", "kilometers")
    add("length", 0.001, "um", "um", "µm", "micron", "microns", "micrometre", "micrometres")
    add("length", 25.4, "in", "in", "inch", "inches")
    add("length", 304.8, "ft", "ft", "foot", "feet")
    add("duration", 1.0, "s", "s", "sec", "secs", "second", "seconds")
    add("duration", 0.001, "ms", "ms", "millisecond", "milliseconds")
    add("duration", 60.0, "min", "min", "mins", "minute", "minutes")
    add("duration", 3600.0, "h", "h", "hr", "hrs", "hour", "hours")
    add("duration", 86400.0, "day", "day", "days")
    add("duration", 604800.0, "week", "week", "weeks")
    add("frequency", 1.0, "Hz", "hz", "hertz")
    add("frequency", 1000.0, "kHz", "khz", "kilohertz")
    add("pixels", 1.0, "px", "px", "pixel", "pixels")
    add("data", 1.0, "B", "byte", "bytes")
    add("data", 1000.0, "kB", "kb")
    add("data", 1024.0, "KiB", "kib")
    add("data", 1e6, "MB", "mb")
    add("data", 1024.0 ** 2, "MiB", "mib")
    add("data", 1e9, "GB", "gb")
    add("data", 1024.0 ** 3, "GiB", "gib")
    add("mass", 1.0, "g", "g", "gram", "grams")
    add("mass", 1000.0, "kg", "kg", "kilogram", "kilograms")
    add("mass", 0.001, "mg", "mg", "milligram", "milligrams")
    add("mass", 1e6, "t", "tonne", "tonnes")
    add("mass", 453.59237, "lb", "lb", "lbs")
    add("volume", 1.0, "ml", "ml", "millilitre", "millilitres", "milliliter", "milliliters")
    add("volume", 1000.0, "l", "l", "litre", "litres", "liter", "liters")
    add("percent", 1.0, "%", "%", "percent", "pct")
    add("angle", 1.0, "deg", "deg", "degree", "degrees", "°")
    add("temperature", 1.0, "degC", "°c", "celsius", "degc")
    add("temperature_f", 1.0, "degF", "°f", "fahrenheit", "degf")
    add("money:EUR", 1.0, "EUR", "eur", "euro", "euros", "€")
    add("money:USD", 1.0, "USD", "usd", "dollar", "dollars", "$", "us$")
    add("money:GBP", 1.0, "GBP", "gbp", "£", "sterling")
    add("money:JPY", 1.0, "JPY", "jpy", "yen", "¥")
    add("money:CHF", 1.0, "CHF", "chf", "franc", "francs")
    add("area", 1.0, "mm2", "mm2", "mm²")
    add("area", 100.0, "cm2", "cm2", "cm²")
    add("area", 1e6, "m2", "m2", "m²")
    add("volume3", 1.0, "mm3", "mm3", "mm³")
    add("volume3", 1000.0, "cm3", "cm3", "cm³", "cc")
    add("volume3", 1e9, "m3", "m3", "m³")
    return table


UNITS = _units()
# single letters that are units only right after a number ("5 m", "3 s", "2 h", "12 g", "1.5 l", "8 B")
_LETTER_UNITS = frozenset({"m", "s", "h", "g", "l"})


@dataclass(frozen=True)
class Unit:
    dim: str
    factor: float
    symbol: str

    def to_base(self, value: float) -> float:
        return value * self.factor


def unit_of(word: str | None) -> Unit | None:
    """The unit a word or symbol names (case-insensitive; `degrees Celsius` and `°C` both name degC)."""
    if not word:
        return None
    w = word.strip().lower().replace("^2", "2").replace("^3", "3")
    w = re.sub(r"^(?:degrees?|deg)\s*(celsius|fahrenheit|c|f)$", lambda m: "°" + m.group(1)[0], w)
    w = re.sub(r"^per\s*cent$", "percent", w)
    if w in UNITS:
        d, f, s = UNITS[w]
        return Unit(d, f, s)
    return None


def compatible(a: Unit | None, b: Unit | None) -> bool:
    """Two units measure the same dimension (or one side states no unit)."""
    return a is None or b is None or a.dim == b.dim


def convert(value: float, frm: Unit | None, to: Unit | None) -> float:
    if frm is None or to is None or frm.dim != to.dim:
        return value
    return value * frm.factor / to.factor


# ---- aggregate words -----------------------------------------------------------------------------------------------

AGGREGATE_WORDS = {
    "sum": ("total", "totals", "totalled", "totaled", "totalling", "totaling", "sum", "sums", "altogether",
            "combined", "aggregate", "add", "adds", "added"),
    "mean": ("average", "averages", "averaged", "mean", "means"),
    "median": ("median",),
    "max": ("largest", "maximum", "max", "highest", "biggest", "greatest", "peak", "peaked", "most", "top",
            "longest", "tallest", "widest", "heaviest", "hottest", "warmest", "fastest", "busiest", "oldest",
            "deepest", "loudest", "costliest"),
    "min": ("smallest", "minimum", "min", "lowest", "least", "fewest", "shortest", "lightest", "coldest", "coolest",
            "slowest", "quietest", "youngest", "shallowest", "cheapest", "narrowest"),
    "first": ("first", "initial", "starting", "began", "started", "opening"),
    "last": ("last", "final", "latest", "closing", "ended", "ending"),
}
_AGG_OF = {w: agg for agg, words in AGGREGATE_WORDS.items() for w in words}
APPROX_WORDS = ("about", "around", "approximately", "approx", "roughly", "nearly", "almost", "circa", "some", "~",
                "close")
LOWER_BOUND = ("more than", "over", "above", "at least", "exceeding", "upwards of", "greater than", "no less than")
UPPER_BOUND = ("less than", "under", "below", "at most", "up to", "fewer than", "no more than", "within")

DIMENSION_WORDS = {"width": "width", "wide": "width", "depth": "depth", "deep": "depth", "height": "height",
                   "high": "height", "tall": "height", "length": "length", "long": "length"}


# words with which a question asks about a count ("as many", "one per row", "for each", "the number of")
COUNT_CUES = frozenset("number numbers many count counts each every per how".split())


def aggregate_named(text: str) -> tuple[str, ...]:
    """The aggregates a text names (the question's words: "add up", "the total", "the average")."""
    found: dict[str, None] = {}
    words = tokens(text)
    for i, w in enumerate(words):
        agg = _AGG_OF.get(w)
        if agg in ("first", "last"):
            continue                       # a question's "first"/"last" names order far more often than a value
        if agg == "sum" and w in ("add", "adds", "added") and (i + 1 >= len(words) or words[i + 1] != "up"):
            continue
        if agg:
            found[agg] = None
    return tuple(found)


# ---- numbers -------------------------------------------------------------------------------------------------------

_SEP = ",.  '"
_NUMBER = re.compile(
    r"(?<![\w.,/:\-])"                                      # not glued to a word, a date, a time or another number
    r"(?P<sign>[-−+])?"
    r"(?P<body>\d{1,3}(?:[,.  ']\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![\w]|[.,:/]\d|-\d)")                               # not followed by more digits, a date part or a time part
_UNAMBIG_EURO = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+|(?<![\d.,])\d+,(?:\d{1,2}|\d{4,})(?![\d.,])|"
                           r"(?<![\d.,])\d{1,3}(?:\.\d{3}){2,}(?![\d,])")
_UNAMBIG_DOT = re.compile(r"\d{1,3}(?:,\d{3})+\.\d+|(?<![\d.,])\d+\.(?:\d{1,2}|\d{4,})(?![\d.,])|"
                          r"(?<![\d.,])\d{1,3}(?:,\d{3}){2,}(?![\d.])")


def convention(text: str) -> str | None:
    """'dot' (1,234.5) or 'comma' (1.234,5): the decimal mark this text uses where a number settles it; None when
    the text settles neither, or both."""
    euro, dot = bool(_UNAMBIG_EURO.search(text)), bool(_UNAMBIG_DOT.search(text))
    if euro == dot:
        return None
    return "comma" if euro else "dot"


def number_readings(body: str, conv: str | None) -> tuple[float, ...]:
    """The value(s) a written number can have: one when its format settles it, else the convention's reading
    first (or both when there is no convention). Also returns nothing for a malformed grouping."""
    seps = [c for c in body if c in _SEP]
    if not seps:
        return (float(body),)
    marks = set(seps)
    if len(marks) == 1 and len(seps) == 1 and body[-1] not in _SEP:
        mark = seps[0]
        head, tail = body.split(mark)
        if mark in (" ", " ", "'"):
            return (float(head + tail),) if len(tail) == 3 else ()
        if len(tail) != 3 or len(head) > 3:                 # 12,5 / 1234,56 / 3.14159: the mark is decimal
            return (float(head + "." + tail),)
        as_group, as_decimal = float(head + tail), float(head + "." + tail)
        if conv == ("comma" if mark == "," else "dot"):
            return (as_decimal,)
        if conv is not None:
            return (as_group,)
        return (as_group, as_decimal)
    # two or more marks: the last distinct mark is the decimal one if it occurs once at the end
    last = seps[-1]
    group_marks = set(seps[:-1])
    if len(group_marks) == 1 and last not in group_marks and seps.count(last) == 1:
        group = group_marks.pop()
        head, _, frac = body.rpartition(last)
        parts = head.split(group)
        if all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            return (float("".join(parts) + "." + frac),)
        return ()
    if len(marks) == 1:                                    # 1,234,567 or 1.234.567: grouping only
        parts = body.split(seps[0])
        if all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            return (float("".join(parts)),)
    return ()


@dataclass(frozen=True)
class Num:
    start: int
    end: int
    values: tuple[float, ...]       # the reading(s); the first is the one used
    decimals: int
    unit: Unit | None
    unit_text: str
    approx: bool
    bound: str | None               # "lower" | "upper" | None
    currency_before: bool = False

    @property
    def value(self) -> float:
        return self.values[0]

    def tolerance(self) -> float:
        """Half a unit of the last digit written; "about" widens it to the larger of that at the last non-zero
        digit and 5 %."""
        half = 0.5 * 10.0 ** (-self.decimals)
        if not self.approx:
            return half
        v = abs(self.value)
        if self.decimals == 0 and v >= 10:
            text = f"{int(round(v))}"
            zeros = len(text) - len(text.rstrip("0"))
            half = max(half, 0.5 * 10.0 ** zeros)
        return max(half, 0.05 * v)


_CURRENCY_BEFORE = {"$": "$", "€": "€", "£": "£", "¥": "¥"}
_UNIT_AFTER = re.compile(r"\s?(°\s?[cfCF]\b|°|%|[$€£¥]|[A-Za-zµ]{1,12}[²³23]?)(?:\s+((?i:celsius|fahrenheit|cent)))?")


def _unit_after(text: str, end: int) -> tuple[Unit | None, str, int]:
    m = _UNIT_AFTER.match(text, end)
    if not m:
        return None, "", end
    word = m.group(1)
    extra = m.group(2)
    if extra:
        u = unit_of(word + " " + extra)
        if u:
            return u, m.group(0).strip(), m.end()
    if len(word) == 1 and word.lower() in _LETTER_UNITS:
        # a single letter is a unit only when it stands alone ("5 m", not "5 more")
        if m.end(1) < len(text) and text[m.end(1)].isalpha():
            return None, "", end
    after = _PREPOSITION_IN.match(text, m.end(1)) if word.lower() == "in" else None
    if after and after.group(1).lower() not in DIMENSION_WORDS:
        # "in" is a unit only when it is adjacent to its number and not the preposition before another word
        # ("520 in total", "5 in the box"); "5 in", "5 in.", "5 in x 3 in", "5 in by 3 in" and "5 in wide" (a
        # dimension word) stay inches
        return None, "", end
    u = unit_of(word)
    if u is None:
        return None, "", end
    return u, word, m.end(1)


_PREPOSITION_IN = re.compile(r"[ \t]+(?!(?:x|by)[ \t]*[\d.]|x\b)([^\W\d_]+)")


def _before(text: str, start: int, words: tuple[str, ...]) -> bool:
    head = text[max(0, start - 24):start].lower()
    return any(re.search(rf"(?<![a-z]){re.escape(w)}\s*$", head) for w in words)


def numbers(text: str, conv: str | None = None) -> list[Num]:
    """The quantities written in a text, in order (bounded)."""
    out = []
    for m in _NUMBER.finditer(text):
        if len(out) >= MAX_NUMBERS * 4:
            break
        body = m.group("body")
        readings = number_readings(body, conv)
        if not readings:
            continue
        sign = -1.0 if m.group("sign") in ("-", "−") else 1.0
        if m.group("sign") == "-" and m.start() > 0 and text[m.start() - 1].isalnum():
            sign = 1.0
        values = tuple(sign * v for v in readings)
        start, end = m.start(), m.end()
        before = text[start - 1] if start > 0 else ""
        unit, unit_text, end2 = _unit_after(text, end)
        currency_before = False
        if before in _CURRENCY_BEFORE or (start >= 3 and text[start - 3:start].upper() in ("USD", "EUR", "GBP",
                                                                                          "CHF", "JPY")):
            symbol = before if before in _CURRENCY_BEFORE else text[start - 3:start]
            unit, unit_text, currency_before = unit_of(symbol), symbol, True
        if unit is None:
            m2 = re.match(r"\s?(USD|EUR|GBP|CHF|JPY)\b", text[end:end + 5])
            if m2:
                unit, unit_text = unit_of(m2.group(1)), m2.group(1)
        approx = _before(text, start - (1 if currency_before else 0), APPROX_WORDS)
        bound = "lower" if _before(text, start - (1 if currency_before else 0), LOWER_BOUND) else \
            "upper" if _before(text, start - (1 if currency_before else 0), UPPER_BOUND) else None
        digits = re.sub(r"\D", "", body)
        last = max(body.rfind(c) for c in _SEP)
        decimals = len(body) - last - 1 if last >= 0 and readings[0] != float(digits) else 0
        out.append(Num(start, max(end, end2) if unit else end, values, decimals, unit, unit_text, approx, bound,
                       currency_before))
    return out


# ---- sentences -----------------------------------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")


def sentences(text: str) -> list[tuple[int, str]]:
    """(index, sentence) of a text: split at line breaks and at a full stop, question or exclamation mark followed
    by space and a capital or a digit. Bounded."""
    out = []
    for part in _SENTENCE_END.split((text or "")[:MAX_TEXT]):
        part = part.strip()
        if part:
            out.append((len(out), part))
            if len(out) >= MAX_SENTENCES:
                break
    return out


# ---- statements ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Said:
    """One thing a text states. kind: 'number' (value with unit, aggregate, words), 'count' (an integer and the noun
    it counts), 'range' (lo, hi), 'extent' (a size as 2 or 3 lengths, axes not stated), 'box' (per-axis ranges
    x/y/z), 'unit' (a declaration of the unit the artifact's own numbers are in). `where` is the sentence index."""
    kind: str
    value: object
    unit: Unit | None
    words: tuple[str, ...]
    aggregate: str | None
    noun: str | None
    sentence: str
    where: int
    tolerance: float
    approx: bool = False
    bound: str | None = None
    alternatives: tuple = ()
    axes: tuple = ()


_AXIS_RANGE = re.compile(r"(?<![A-Za-z])([xyzXYZ])\s*[:=]?\s*$")
_RANGE_WORD = re.compile(r"^\s*(?:to|until|through|–|—|-|and)\s*$")
_SIZE_SEP = re.compile(r"^\s*(?:x|×|by|\*)\s*$", re.I)
_UNIT_DECLARED = re.compile(
    r"(?:\bunits?\s*[:=]?\s*(?:are|is|in)?\s*|\((?:in\s+)?|\ball\s+(?:dimensions|sizes|values|measurements)\s+"
    r"(?:are\s+)?in\s+|\bin\s+)([A-Za-zµ°²³]{1,14}(?:\s+(?:celsius|fahrenheit))?)\)?", re.I)


def declared_unit(sentence: str) -> Unit | None:
    """A unit the sentence declares for its numbers without a unit of their own: "(mm)", "units: centimetres",
    "all dimensions in inches". Only when exactly one distinct unit is declared."""
    found = {}
    for m in _UNIT_DECLARED.finditer(sentence):
        u = unit_of(m.group(1))
        if u is not None and u.dim not in ("percent",):
            found[u.symbol] = u
    return next(iter(found.values())) if len(found) == 1 else None


def _aggregate_for(words_before: list[str], words_after: list[str]) -> str | None:
    if words_before[-2:] in (["in", "all"], ["all", "told"]):
        return "sum"
    for w in reversed(words_before[-8:]):
        if w in _AGG_OF:
            return _AGG_OF[w]
    if len(words_after) >= 2 and words_after[0] == "in" and words_after[1] in ("total", "all"):
        return "sum"
    if words_after[:1] and words_after[0] in ("total", "altogether", "combined", "overall"):
        return "sum"
    return None


_COUNT_ADJECTIVES = frozenset("distinct different separate unique individual more other new active further "
                              "additional extra".split())


def _noun_after(text: str, end: int) -> str | None:
    """The noun a whole number counts: the word right after it ("9 entries"), or after one counting adjective
    ("5 distinct customers"). Nothing else is skipped ("17718 was paid" counts nothing)."""
    words = re.findall(r"[A-Za-z][A-Za-z-]*", text[end:end + 48])[:2]
    if not words or not re.match(r"\s", text[end:end + 1] or "x"):
        return None
    word = words[0].lower()
    if word in _COUNT_ADJECTIVES and len(words) > 1:
        word = words[1].lower()
    if word in STOP or unit_of(word) is not None or word in _AGG_OF or word in SEQUENCE_WORDS or len(word) < 3:
        return None
    return singular(word)


def statements_of_text(text: str, *, base: int = 0, default_unit: Unit | None = None) -> list[Said]:
    """Everything the text states, sentence by sentence (bounded)."""
    text = (text or "")[:MAX_TEXT]
    conv = convention(text)
    out: list[Said] = []
    for index, sentence in sentences(text):
        if len(out) >= MAX_STATEMENTS:
            break
        nums = numbers(sentence, conv)[:MAX_NUMBERS]
        declared = declared_unit(sentence)
        if declared is not None and not nums:
            out.append(Said("unit", declared.symbol, declared, content_words(sentence), None, None, clean(sentence),
                            base + index, 0.0))
            continue
        if not nums:
            continue
        here_default = declared or default_unit
        used: set[int] = set()
        words = content_words(sentence, drop=frozenset(_AGG_OF) | frozenset(APPROX_WORDS))
        kept = clean(sentence)
        # sizes: a x b (x c) [unit]
        i = 0
        while i < len(nums):
            j = i
            while j + 1 < len(nums) and _SIZE_SEP.match(sentence[nums[j].end:nums[j + 1].start]) \
                    and not nums[j].unit and not nums[j + 1].currency_before:
                j += 1
            if j > i and j - i <= 2:
                group = nums[i:j + 1]
                unit = group[-1].unit or here_default
                values = tuple(convert(n.value, unit, unit) for n in group)
                tol = max(n.tolerance() for n in group)
                out.append(Said("extent", values, unit, words, None, None, kept, base + index, tol,
                                approx=any(n.approx for n in group)))
                used.update(range(i, j + 1))
                i = j + 1
                continue
            i += 1
        # per-axis ranges ("x 10 to 70") and plain ranges ("between 5 and 90")
        box = {}
        for k in range(len(nums) - 1):
            if k in used or k + 1 in used:
                continue
            a, b = nums[k], nums[k + 1]
            if not _RANGE_WORD.match(sentence[a.end:b.start]):
                continue
            unit = b.unit or a.unit or here_default
            axis = _AXIS_RANGE.search(sentence[max(0, a.start - 12):a.start])
            tol = max(a.tolerance(), b.tolerance())
            if axis:
                box[axis.group(1).lower()] = (a.value, b.value, tol)
            else:
                before = tokens(sentence[:a.start])
                agg = "range" if any(w in ("from", "between", "range", "ranged", "ranges", "ranging", "varied",
                                           "varies", "spanned", "spans") for w in before[-4:]) else None
                if agg:
                    out.append(Said("range", (a.value, b.value), unit, words, agg, None, kept, base + index, tol))
            used.update((k, k + 1))
        if len(box) >= 2:
            unit = next((n.unit for n in nums if n.unit), None) or here_default
            out.append(Said("box", {k: (v[0], v[1]) for k, v in box.items()}, unit, words, None, None, kept,
                            base + index, max(v[2] for v in box.values()), axes=tuple(sorted(box))))
        # "200 mm wide, 150 mm deep and 3 mm tall": lengths each tagged with a different outer dimension
        tagged = {}
        for k, n in enumerate(nums):
            if k in used:
                continue
            unit = n.unit or here_default
            if unit is None or unit.dim != "length":
                continue
            after = tokens(sentence[n.end:n.end + 16])[:1]
            before = tokens(sentence[max(0, n.start - 24):n.start])[-2:]
            tag = DIMENSION_WORDS.get(after[0]) if after else None
            if tag is None:
                tag = next((DIMENSION_WORDS[w] for w in reversed(before) if w in DIMENSION_WORDS), None)
            if tag and tag not in tagged:
                tagged[tag] = (k, convert(n.value, unit, unit), unit, n.tolerance())
        if len(tagged) >= 2:
            unit = next(iter(tagged.values()))[2]
            values = tuple(convert(v[1], v[2], unit) for v in tagged.values())
            out.append(Said("extent", values, unit, words, None, None, kept, base + index,
                            max(v[3] for v in tagged.values()), axes=tuple(tagged)))
            used.update(v[0] for v in tagged.values())
        # the rest: single numbers
        for k, n in enumerate(nums):
            if k in used:
                continue
            pre = tokens(sentence[:n.start])
            post = tokens(sentence[n.end:])
            agg = _aggregate_for(pre, post)
            unit = n.unit or (here_default if n.unit is None and not n.currency_before else None)
            noun = None
            if n.unit is None and float(n.value).is_integer() and n.decimals == 0 and n.value >= 0:
                noun = _noun_after(sentence, n.end)
                if noun is None:
                    m = re.search(r"number\s+of\s+([a-z]+)\s+(?:is|was|are|were|:)?\s*$", sentence[:n.start].lower())
                    if m:
                        noun = singular(m.group(1))
            if noun is not None:
                out.append(Said("count", int(n.value), None, words, "count", noun, kept, base + index, 0.0,
                                approx=n.approx, bound=n.bound))
                continue
            out.append(Said("number", n.value, unit, words, agg, None, kept, base + index, n.tolerance(),
                            approx=n.approx, bound=n.bound, alternatives=n.values[1:]))
    return out


# ---- ordered steps -------------------------------------------------------------------------------------------------

_LIST_MARK = re.compile(r"^\s*(?:[-*•+]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|step\s+\d{1,3}[:.)]?)\s+", re.I)


def step_words(text: str) -> tuple[str, ...]:
    """The words a step is matched by: its content words and digits (so 'port 3' differs from 'port 7'), without
    sequence markers ('first', 'then', 'finally')."""
    out: dict[str, None] = {}
    text = _LIST_MARK.sub("", text or "", count=1)
    for t in tokens(text):
        if (t in STOP and t not in STEP_KEEP) or t in SEQUENCE_WORDS:
            continue
        out[t if t.isdigit() else singular(t)] = None
    return tuple(out)


# ---- timestamps and events -----------------------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                           "nov", "dec"))}
_ISO_TS = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?"
                     r"\s?(Z|[+-]\d{2}:?\d{2})?(?!\d)")
_SYSLOG_TS = re.compile(r"^\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})"
                        r"(?!\d)", re.I)
_CLF_TS = re.compile(r"\[(\d{2})/(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/(\d{4}):(\d{2}):(\d{2}):(\d{2})"
                     r"\s*([+-]\d{4})?\]", re.I)
_CLOCK_TS = re.compile(r"^\s*\[?(\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,6}))?\]?(?!\d)")


def _epoch(y, mo, d, h, mi, s, frac="", offset=None) -> float | None:
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    t = dt.timestamp() + (float("0." + frac) if frac else 0.0)
    if offset and offset != "Z":
        sign = -1 if offset[0] == "-" else 1
        digits = offset[1:].replace(":", "")
        t -= sign * (int(digits[:2]) * 3600 + int(digits[2:4] or 0) * 60)
    return t


@dataclass(frozen=True)
class Stamp:
    seconds: float
    absolute: bool          # False: no year or no date was written (syslog, clock time): only order is meaningful
    start: int
    end: int
    precision: float        # seconds: the smallest step the written time can show


def timestamp(line: str) -> Stamp | None:
    """The first timestamp written in a line (ISO 8601, syslog, common log format, or a clock time at the start)."""
    m = _ISO_TS.search(line)
    if m:
        y, mo, d, h, mi, s, frac, off = m.groups()
        t = _epoch(y, mo, d, h, mi, s, frac or "", off)
        if t is not None:
            return Stamp(t, True, m.start(), m.end(), 10.0 ** -len(frac) if frac else 1.0 if s else 60.0)
    m = _CLF_TS.search(line)
    if m:
        d, mon, y, h, mi, s, off = m.groups()
        t = _epoch(y, _MONTHS[mon.lower()], d, h, mi, s, "", off)
        if t is not None:
            return Stamp(t, True, m.start(), m.end(), 1.0)
    m = _SYSLOG_TS.match(line)
    if m:
        mon, d, h, mi, s = m.groups()
        t = _epoch(2000, _MONTHS[mon.lower()], d, h, mi, s)     # no year written: 2000 stands in (order only)
        if t is not None:
            return Stamp(t, False, m.start(), m.end(), 1.0)
    m = _CLOCK_TS.match(line)
    if m:
        h, mi, s, frac = m.groups()
        if int(h) < 24 and int(mi) < 60 and int(s) < 61:
            return Stamp(int(h) * 3600 + int(mi) * 60 + int(s) + (float("0." + frac) if frac else 0.0), False,
                         m.start(), m.end(), 1.0)
    return None


@dataclass(frozen=True)
class Event:
    seconds: float
    absolute: bool
    text: str               # the line without its timestamp (untrusted)
    words: tuple[str, ...]
    line: int
    precision: float


def events_of_text(text: str) -> list[Event] | None:
    """The text as a log: every line that carries a timestamp is an event. None unless at least two lines and at
    least half of the non-empty lines carry one (a document that mentions a date is not a log)."""
    lines = [ln for ln in (text or "")[:MAX_TEXT].splitlines()]
    out, nonempty = [], 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        nonempty += 1
        st = timestamp(ln)
        if st is None:
            continue
        rest = (ln[:st.start] + " " + ln[st.end:]).strip()
        out.append(Event(st.seconds, st.absolute, clean(rest), step_words(rest), i, st.precision))
        if len(out) >= MAX_EVENTS:
            break
    if len(out) < 2 or len(out) * 2 < nonempty:
        return None
    return out


def without_format_words(events: list) -> list:
    """Words that occur in at least 80 % of a log's lines (its host, program, process id, level) are the log's
    format, not what happened: left out when the log's events are matched INTO another log's lines (three lines or
    more; the other log keeps all its words, so a source name that is also a word of the events still matches)."""
    if len(events) < 3:
        return events
    seen: dict[str, int] = {}
    for e in events:
        for w in set(e.words):
            seen[w] = seen.get(w, 0) + 1
    common = {w for w, n in seen.items() if n >= 0.8 * len(events)}
    if not common:
        return events
    return [Event(e.seconds, e.absolute, e.text, tuple(w for w in e.words if w not in common) or e.words, e.line,
                  e.precision) for e in events]


@dataclass
class Sequence:
    """Ordered steps a document states (a list, the lines of one paragraph, its sentences)."""
    items: list[str]
    source: str                 # "list" | "lines" | "sentences"
    where: int
    words: list[tuple[str, ...]] = field(default_factory=list)

    def __post_init__(self):
        self.items = self.items[:MAX_ITEMS]
        self.words = [step_words(t) for t in self.items]


def is_number_like(text: str) -> bool:
    return bool(re.fullmatch(r"\s*[-−+]?[\d.,  ' ]+\s*", text or "")) and any(c.isdigit() for c in text)


def column_numbers(values) -> tuple[list[float], str] | None:
    """A text column whose every non-empty cell is one number written in one consistent format (grouping, decimal
    comma, a currency sign or a percent sign around it): the numbers, and the unit symbol the cells carry (or "").
    None otherwise (the column stays text: nothing is guessed)."""
    cells = [str(v).strip() for v in values]
    filled = [c for c in cells if c and c.lower() not in ("na", "n/a", "nan", "null", "-", "—")]
    if not filled or len(filled) > 200_000:
        return None
    joined = " ".join(filled[:5000])
    conv = convention(joined)
    out, units = [], set()
    for c in cells:
        if not c or c.lower() in ("na", "n/a", "nan", "null", "-", "—"):
            out.append(math.nan)
            continue
        ns = numbers(c, conv)
        if len(ns) != 1:
            return None
        n = ns[0]
        rest = (c[:n.start] + c[n.end:]).strip(" $€£¥")
        if rest and not unit_of(rest):
            return None
        if len(n.values) > 1:
            return None
        out.append(n.value)
        units.add(n.unit.symbol if n.unit else "")
    if len(units) > 1:
        return None
    return out, next(iter(units))


__all__ = ["AGGREGATE_WORDS", "Event", "Num", "Said", "Sequence", "Stamp", "UNITS", "Unit", "aggregate_named",
           "clean", "column_numbers", "compatible", "content_words", "convention", "convert", "declared_unit",
           "events_of_text", "is_number_like", "name_words", "number_readings", "numbers", "sentences", "singular",
           "statements_of_text", "step_words", "timestamp", "tokens", "unit_of"]
