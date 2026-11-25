"""Lexical primitives for the evidence stage: tokens, sentences, BM25, shingles, value extraction, claim keys.

Deterministic and stdlib-only (ADR-022: there is no model). Everything here operates on untrusted text and only
measures it; nothing is executed or interpreted as an instruction.

Constants
---------
BM25_K1, BM25_B        Okapi BM25 parameters (Robertson/Zaragoza defaults: k1 = 1.5, b = 0.75).
NUMERIC_TOLERANCE      Two numbers of the same kind conflict only when they differ by more than 1 % relative.
DATE_TOLERANCE         Two calendar dates conflict only when they differ by more than one day.
QUOTE_SPAN             A sentence boundary is ignored inside a double quotation opened at most this many characters
                       earlier (a stray unbalanced quote further back does not glue a whole block together).
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import unicodedata

BM25_K1 = 1.5
BM25_B = 0.75
NUMERIC_TOLERANCE = 0.01
DATE_TOLERANCE = 86400.0
QUOTE_SPAN = 300

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

# ---- characters a reader does not see -----------------------------------------------------------------------
# Fetched text is turned into claims, and a claim is shown to the owner and compared with what a model wrote. Both
# go wrong when the text holds characters nobody sees: a right-to-left override shows "72" as "27", a zero-width
# space inside "100" or a zero-width non-joiner inside "NumPy" makes two tokens of one word, a soft hyphen hides
# in the middle of a name. strip_unseen() removes them where text is extracted (webcortex/extract.py), and the
# answer guard refuses a sentence that cites a claim still holding one (reasoning.UNSEEN_IN_CLAIM).
#
# Removed (code points, so that this file holds none of the characters themselves):
#   - the format characters that draw nothing: soft hyphen, Arabic letter mark, Mongolian vowel separator,
#     zero-width space and joiners, direction marks, embeddings, overrides and isolates, word joiner and the
#     invisible operators, the deprecated format controls, byte order mark, interlinear annotation controls,
#     shorthand and musical format controls, tag characters;
#   - what Unicode files under other categories and still draws nothing: combining grapheme joiner, Khmer inherent
#     vowels, Mongolian free variation selectors, variation selectors and their supplement;
#   - control characters other than tab, line feed and carriage return.
# Turned into a space: the fillers that draw a blank (Hangul fillers, the Braille blank) and the controls Python
# counts as white space. Turned into a line break: the line and paragraph separators.
# Kept on purpose: the format characters that are drawn (category Cf all the same): Arabic number signs and end of
# ayah, the Syriac abbreviation mark, Kaithi number signs, Egyptian hieroglyph joiners. Removing them would change
# what a reader of those scripts sees.
_UNSEEN_DELETE = ((0x00, 0x08), (0x0E, 0x1B), (0x7F, 0x84), (0x86, 0x9F), (0xAD, 0xAD), (0x34F, 0x34F),
                  (0x61C, 0x61C), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
                  (0x2060, 0x206F), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x1BCA0, 0x1BCA3),
                  (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
_UNSEEN_BLANK = ((0x0B, 0x0C), (0x1C, 0x1F), (0x85, 0x85), (0x115F, 0x1160), (0x2800, 0x2800), (0x3164, 0x3164),
                 (0xFFA0, 0xFFA0))
_UNSEEN_BREAK = ((0x2028, 0x2029),)


def _char_class(ranges) -> str:
    return "[" + "".join(chr(a) if a == z else f"{chr(a)}-{chr(z)}" for a, z in ranges) + "]"


_UNSEEN_DELETE_RE = re.compile(_char_class(_UNSEEN_DELETE))
_UNSEEN_BLANK_RE = re.compile(_char_class(_UNSEEN_BLANK))
_UNSEEN_BREAK_RE = re.compile(_char_class(_UNSEEN_BREAK))


# Direction controls are a case of their own: they draw nothing and change the order in which the characters around
# them are shown. The two overrides (U+202D, U+202E) reverse what follows ("<U+202E>72" is shown as "27"); the
# marks, embeddings and isolates (U+200E, U+200F, U+061C, U+202A-U+202C, U+2066-U+2069) reorder neighbouring
# digits and punctuation ("Affected:<U+200F> 3 4" is shown as "4 3"). Removing one would leave the order nobody was
# shown, so a line (as_seen) or a paragraph (extract.py) that holds one is not read at all: what a page shows in
# an order other than its own cannot be quoted faithfully. One exception, for right-to-left writing (Hebrew,
# Arabic and the other scripts of bidirectional class R and AL), where marks, embeddings and isolates are how text
# is written: a line that holds one such letter keeps every direction control in it except the overrides (marks,
# embeddings and their pop, isolates), the claims made of it carry them, and the answer guard does not cite those
# claims (reasoning.UNSEEN_IN_CLAIM). Other consumers of claims (the extractive report, the knowledge store, memory
# answers, Pocket) show a claim's text as stored, controls included, so as the page showed it; none of them
# compares it with a model's words. An override is never kept.
_OVERRIDE_RE = re.compile(f"[{chr(0x202D)}{chr(0x202E)}]")
_DIRECTION = (0x200E, 0x200F, 0x061C, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069)
_DIRECTION_RE = re.compile("[" + "".join(map(chr, _DIRECTION)) + "]")
_UNSEEN_DELETE_BUT_DIRECTION_RE = re.compile("(?!" + _DIRECTION_RE.pattern + ")" + _char_class(_UNSEEN_DELETE))


def right_to_left(s) -> bool:
    """Whether `s` holds a letter of a right-to-left script (bidirectional class R or AL; a letter: the right-to-left
    and Arabic letter marks are of those classes too and count for nothing)."""
    return any(unicodedata.bidirectional(ch) in ("R", "AL") and unicodedata.category(ch).startswith("L")
               for ch in str(s or ""))


def reorders(s) -> bool:
    """Whether `s` holds a direction control that makes what a reader sees differ from its characters' order: an
    override anywhere, or any other direction control in text without right-to-left letters."""
    s = str(s or "")
    return bool(_OVERRIDE_RE.search(s)) or (bool(_DIRECTION_RE.search(s)) and not right_to_left(s))


def unseen_characters(s) -> list[str]:
    """The characters of `s` that draw nothing (the removed kind above), as sorted code point names ("U+202E")."""
    return sorted({f"U+{ord(ch):04X}" for ch in _UNSEEN_DELETE_RE.findall(str(s or ""))})


def strip_unseen(s) -> tuple[str, int]:
    """`s` without the characters that draw nothing, and how many were removed or replaced: those are removed,
    blank fillers become a space, line and paragraph separators a line break. Nothing else changes: no
    normalisation, no change of white space or case. Use as_seen() for fetched text: it first drops the lines a
    direction override reorders."""
    s = str(s or "")
    s, removed = _UNSEEN_DELETE_RE.subn("", s)
    s, blanked = _UNSEEN_BLANK_RE.subn(" ", s)
    s, broken = _UNSEEN_BREAK_RE.subn("\n", s)
    return s, removed + blanked + broken


def as_seen(s) -> tuple[str, int, int]:
    """Fetched text as a reader sees it -> (text, characters removed or replaced, lines dropped). A line that
    reorders what is shown (reorders: an override, or another direction control without right-to-left letters) is
    dropped whole; a line with right-to-left letters keeps its direction marks and isolates; from the rest,
    strip_unseen() takes the characters that draw nothing."""
    s = str(s or "")
    dropped = 0
    if _DIRECTION_RE.search(s):
        lines = _UNSEEN_BREAK_RE.sub("\n", s).split("\n")
        kept = [line for line in lines if not reorders(line)]
        dropped = len(lines) - len(kept)
        removed = 0
        out = []
        for line in kept:
            if _DIRECTION_RE.search(line):          # right-to-left text: its marks stay, the rest goes
                line, n = _UNSEEN_DELETE_BUT_DIRECTION_RE.subn("", line)
                line, m = _UNSEEN_BLANK_RE.subn(" ", line)
                removed += n + m
            else:
                line, n = strip_unseen(line)
                removed += n
            out.append(line)
        return "\n".join(out), removed, dropped
    s, removed = strip_unseen(s)
    return s, removed, dropped


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


def _open_quote(block: str, start: int, end: int) -> bool:
    """True when a double quotation opened in block[start:end] (at most QUOTE_SPAN characters before `end`) is
    still open at `end`: a boundary there would start the next sentence inside the quotation."""
    part = block[start:end]
    straight = [i for i, ch in enumerate(part) if ch == '"']
    if len(straight) % 2:
        return len(part) - straight[-1] <= QUOTE_SPAN
    depth, opened = 0, -1
    for i, ch in enumerate(part):
        if ch in "“„":
            depth, opened = depth + 1, i
        elif ch == "”" and depth:
            depth -= 1
    return depth > 0 and len(part) - opened <= QUOTE_SPAN


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
            if _open_quote(block, cut, m.end()):
                continue                                   # inside a quotation: the sentence goes on
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
_SECTION_HEADING = re.compile(
    r"^\s*(?i:abstract|summary|simple\s+summary|graphical\s+abstract|background|introduction|objectives?|aims?|"
    r"purpose|context|methods?|materials\s+and\s+methods|results?|findings|conclusions?|discussion|"
    r"significance(?:\s+statement)?|highlights|key\s+points)\s*[:.\u2014\u2013-]?\s+(?=[\"“‘(\[]?[A-Z0-9])")


def strip_heading(sentence: str) -> str:
    """`sentence` without a section heading glued to its start ("Abstract A key challenge ..." -> "A key challenge
    ...", "Background: ..."), as academic abstracts arrive from Crossref/OpenAlex. A heading word that is the
    sentence's subject ("Results show ...") is kept: the next word must start a new sentence (capital or digit)."""
    m = _SECTION_HEADING.match(sentence or "")
    if not m or len(sentence[m.end():].split()) < 3:
        return sentence
    return sentence[m.end():]


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
    "nmi": ("nmi", "nautical mile", "nautical miles"),
    "ft": ("ft", "foot", "feet"),
    "yd": ("yd", "yard", "yards"),
    "inch": ("inch", "inches"),
    "km²": ("km²", "km2", "sq km", "square kilometre", "square kilometres", "square kilometer", "square kilometers"),
    "m²": ("m²", "m2", "sq m", "square metre", "square metres", "square meter", "square meters"),
    "mi²": ("mi²", "sq mi", "square mile", "square miles"),
    "ha": ("ha", "hectare", "hectares"),
    "acre": ("acre", "acres"),
    "ft²": ("ft²", "ft2", "sq ft", "square foot", "square feet"),
    "m³/s": ("m³/s", "m3/s", "cubic metres per second", "cubic meters per second", "cubic metre per second",
             "cubic meter per second"),
    "m³": ("m³", "m3", "cubic metre", "cubic metres", "cubic meter", "cubic meters"),
    "ft³": ("ft³", "ft3", "cubic foot", "cubic feet"),
    "l": ("l", "litre", "litres", "liter", "liters"),
    "ml": ("ml", "millilitre", "millilitres", "milliliter", "milliliters"),
    "µl": ("µl", "ul", "microlitre", "microlitres", "microliter", "microliters"),
    "t": ("tonne", "tonnes", "metric ton", "metric tons"),
    "kg": ("kg", "kilogram", "kilograms", "kilogramme", "kilogrammes"),
    "g": ("g", "gram", "grams", "gramme", "grammes"),
    "mg": ("mg", "milligram", "milligrams"),
    "µg": ("µg", "ug", "microgram", "micrograms"),
    "lb": ("lb", "lbs"),
    "km/h": ("km/h", "kph", "km/hr", "kilometre per hour", "kilometres per hour", "kilometer per hour",
             "kilometers per hour"),
    "mph": ("mph", "mile per hour", "miles per hour"),
    "m/s": ("m/s", "metre per second", "metres per second", "meter per second", "meters per second"),
    "kn": ("kn", "knot", "knots"),
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
    "pb": ("pb", "petabyte", "petabytes"),
    "kib": ("kib", "kibibyte", "kibibytes"),
    "mib": ("mib", "mebibyte", "mebibytes"),
    "gib": ("gib", "gibibyte", "gibibytes"),
    "tib": ("tib", "tebibyte", "tebibytes"),
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
    "°c": ("°c", "ºc", "℃", "degrees celsius", "degree celsius"),
    "°f": ("°f", "ºf", "℉", "degrees fahrenheit", "degree fahrenheit"),
    "kelvin": ("kelvin", "kelvins"), "k": ("k",),
}
_UNIT_CANONICAL = {spelling: unit for unit, spellings in _UNIT_SPELLINGS.items() for spelling in spellings}
# Canonical unit -> (dimension, factor to the dimension's base unit). Quantities compare within one dimension,
# converted to the base unit ("1.2 km" and "1200 m" agree). Every factor is exact by definition (the international
# foot, mile, yard, inch, pound, acre and nautical mile). Units whose size depends on a country or a convention
# (gallons, pints, fluid ounces, short and long tons, "pounds", which is also a currency) are not in the table, so
# they compare only with themselves. Data sizes in binary prefixes (KiB, MiB, ...) are a dimension of their own:
# "8 GiB" is never compared with "8 GB" (8 GiB is 7.4 % more). Temperatures are converted by _AFFINE; a bare "K"
# is not taken for kelvin ("5k" is a count) and compares only with itself.
_DIMENSION = {
    "km": ("length", 1e3), "m": ("length", 1.0), "cm": ("length", 1e-2), "mm": ("length", 1e-3),
    "µm": ("length", 1e-6), "nm": ("length", 1e-9), "mi": ("length", 1609.344), "ft": ("length", 0.3048),
    "nmi": ("length", 1852.0), "yd": ("length", 0.9144), "inch": ("length", 0.0254),
    "km²": ("area", 1e6), "m²": ("area", 1.0), "mi²": ("area", 2_589_988.110336), "ha": ("area", 1e4),
    "acre": ("area", 4046.8564224), "ft²": ("area", 0.09290304),
    "m³/s": ("flow", 1.0), "m³": ("volume", 1.0), "l": ("volume", 1e-3), "ml": ("volume", 1e-6),
    "µl": ("volume", 1e-9), "ft³": ("volume", 0.028316846592), "t": ("mass", 1e3), "kg": ("mass", 1.0),
    "g": ("mass", 1e-3), "mg": ("mass", 1e-6), "µg": ("mass", 1e-9), "lb": ("mass", 0.45359237),
    "km/h": ("speed", 1 / 3.6), "mph": ("speed", 0.44704), "m/s": ("speed", 1.0), "kn": ("speed", 1852 / 3600),
    "ns": ("time", 1e-9), "µs": ("time", 1e-6), "ms": ("time", 1e-3), "s": ("time", 1.0),
    "min": ("time", 60.0), "h": ("time", 3600.0), "days": ("time", 86400.0), "weeks": ("time", 604800.0),
    "months": ("time", 2_629_800.0), "years": ("time", 31_557_600.0), "kb": ("data", 1e3), "mb": ("data", 1e6),
    "gb": ("data", 1e9), "tb": ("data", 1e12), "pb": ("data", 1e15), "kib": ("binary data", 2 ** 10),
    "mib": ("binary data", 2 ** 20), "gib": ("binary data", 2 ** 30), "tib": ("binary data", 2 ** 40),
    "hz": ("frequency", 1.0), "khz": ("frequency", 1e3),
    "mhz": ("frequency", 1e6), "ghz": ("frequency", 1e9), "w": ("power", 1.0), "kw": ("power", 1e3),
    "mw": ("power", 1e6), "gw": ("power", 1e9), "v": ("voltage", 1.0), "kv": ("voltage", 1e3),
    "mv": ("voltage", 1e-3),
}
# Temperatures: canonical unit -> (scale, offset) to kelvin. Compared by _same_temperature, never by a relative
# tolerance on the absolute scale (72 °C and 73 °C differ by 0.3 % in kelvin).
_AFFINE = {"°c": (1.0, 273.15), "°f": (5 / 9, 273.15 - 32 * 5 / 9), "kelvin": (1.0, 0.0)}
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
    return [v for v, _ in _scan_values(s)]


def value_nouns(s: str) -> dict[str, str]:
    """For each plain number (no unit, currency or date) among values(s): the stem of the word right after it
    ("55 countries" -> "countri"), or "" when a stopword, punctuation or the end follows. Two plain numbers are
    the same kind of quantity only when these nouns agree (values_conflict with `nouns`)."""
    out: dict[str, str] = {}
    for v, noun in _scan_values(s):
        if noun is not None:
            out.setdefault(v, noun)
    return out


def _scan_values(s: str) -> list[tuple[str, str | None]]:
    """[(canonical value, noun after it for a plain number else None)] in order, deduplicated (see values)."""
    s = s or ""
    found: list[tuple[int, str]] = []
    numbers: list[list] = []                # [start, end, x, unit or None, index in found, magnitude] per number
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
            mag = _MAGNITUDE[g["mag"].lower()] if g["mag"] else 1.0
            x *= mag
            if not math.isfinite(x):
                continue
            if g["curw"]:
                found.append((m.start(), f"{_CURRENCY_WORD[g['curw'].lower()]} {_fmt(x)}"))
                continue
            unit = _unit(g["unit"] or "")
            if unit:
                numbers.append([m.start(), m.end(), x, unit, len(found), mag])
                found.append((m.start(), _with_unit(x, unit)))
            elif re.fullmatch(r"\d{4}", raw) and 1000 <= x <= 2999:
                found.append((m.start(), raw))                 # a year
            else:
                numbers.append([m.start(), m.end(), x, None, len(found), mag])
                found.append((m.start(), _fmt(x)))
    for here, nxt in reversed(list(zip(numbers, numbers[1:]))):
        join = _RANGE_JOIN.fullmatch(text[here[1]:nxt[0]])
        if not join or ("and" in join.group(0).lower() and not _BETWEEN.search(text[:here[0]])):
            continue
        if here[5] == 1.0 and nxt[5] != 1.0 and here[3] is None and math.isfinite(here[2] * nxt[5]):
            here[2], here[5] = here[2] * nxt[5], nxt[5]       # "14–16 billion": both ends in billions
            found[here[4]] = (here[0], _fmt(here[2]))
        if here[3] is None and nxt[3]:
            here[3] = nxt[3]
            found[here[4]] = (here[0], _with_unit(here[2], nxt[3]))
    plain = {}
    for start, end, _x, unit, index, _mag in numbers:
        if unit is None and index < len(found) and found[index][0] == start:
            m = _NEXT_WORD.match(text, end)
            word = m.group(1).lower() if m else ""
            plain[start] = "" if not word or word in STOPWORDS else stem(word)
    found.sort(key=lambda p: p[0])
    out: dict[str, str | None] = {}
    for start, v in found:
        if v not in out:
            out[v] = plain.get(start)
    return list(out.items())


_NEXT_WORD = re.compile(r"[ \t]+([^\W\d_][\w'’-]*)")


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
        if unit in _AFFINE:
            return "quantity", "quantity:temperature", _kelvin(float(m.group(1)), unit)
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


def _kelvin(x: float, unit: str) -> float:
    scale, offset = _AFFINE[unit]
    return x * scale + offset


def _temperature_unit(v: str) -> str | None:
    m = re.fullmatch(r"-?[\d.e+]+ (.+)", v or "")
    unit = _unit(m.group(1)) if m else None
    return unit if unit in _AFFINE else None


TEMPERATURE_TOLERANCE = 0.3     # kelvin: a temperature converted and rounded to whole degrees Fahrenheit is within it


def _same_temperature(a: float, b: float, sa: str, sb: str) -> bool:
    """Two temperatures (kelvin). Written in the same unit they compare as before the conversion existed: the
    written numbers, by _same's rule for numbers. In different units: equal within the larger of
    TEMPERATURE_TOLERANCE and 1 % of the larger value in degrees Celsius ("161.6 °F" and "72 °C" agree; "140 °F",
    60 °C, does not)."""
    ua, ub = _temperature_unit(sa), _temperature_unit(sb)
    if ua == ub:
        xa, xb = float(sa.split(" ", 1)[0]), float(sb.split(" ", 1)[0])
        return _same_number("quantity", xa, xb, sa, sb)
    ca, cb = a - 273.15, b - 273.15
    return abs(a - b) <= max(TEMPERATURE_TOLERANCE, 0.01 * max(abs(ca), abs(cb)))


def _same(kind: str, a, b, sa: str = "", sb: str = "") -> bool:
    """Equal within tolerance. Two numbers written with the same number of decimal places (>= 1) must be equal:
    the relative tolerance absorbs rounding ("42%" vs "42.2%"), and "6.12" vs "6.14" is not a rounding.
    Temperatures: _same_temperature."""
    if kind == "quantity" and (_temperature_unit(sa) or _temperature_unit(sb)):
        return _same_temperature(a, b, sa, sb)
    return _same_number(kind, a, b, sa, sb)


def _same_number(kind: str, a, b, sa: str = "", sb: str = "") -> bool:
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
            if _unit(unit) in _AFFINE:
                # a plain number read as a temperature in y's unit, and written so (_same_temperature)
                x.setdefault(group, []).append((f"{hits[0][0]} {_unit(unit)}", _kelvin(hits[0][1], _unit(unit)),
                                                "quantity"))
                continue
            factor = _DIMENSION.get(_unit(unit), ("", 1.0))[1]
            x.setdefault(group, []).append((hits[0][0], hits[0][1] * factor, "quantity"))


def _group_conflict(va: list[str], vb: list[str], temporal: bool, nouns=None) -> str | None:
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
    if temporal:
        # granularity: a year on one side and only full dates on the other are compared by year ("2008" agrees
        # with "December 3, 2008"; "2007" does not), never as two unrelated groups (T1-01)
        for x, y in ((ga, gb), (gb, ga)):
            if "year" in x and "date" not in x and "date" in y and "year" not in y:
                y["year"] = [(v, datetime.fromtimestamp(p, timezone.utc).year, "year") for v, p, _ in y.pop("date")]
                break
    for group in sorted(set(ga) & set(gb)):
        if nouns is not None and group in (*_BARE, "number"):
            na, nb = nouns
            pairs = [(x, y) for x in ga[group] for y in gb[group] if _same_count(x, y, na, nb)]
            if not pairs:
                continue                                   # plain numbers counting different things
            if any(_same(x[2], x[1], y[1], x[0], y[0]) for x, y in pairs):
                continue
            left = ", ".join(dict.fromkeys(x[0] for x, _ in pairs))
            right = ", ".join(dict.fromkeys(y[0] for _, y in pairs))
            return f"different values: {left} vs {right}"
        overlap = any(_same(ka, pa, pb, sa, sb) for sa, pa, ka in ga[group] for sb, pb, _ in gb[group])
        if not overlap:
            left = ", ".join(v for v, _, _ in ga[group])
            right = ", ".join(v for v, _, _ in gb[group])
            label = {"version": "different versions", "date": "different dates",
                     "year": "different years"}.get(ga[group][0][2], "different values")
            return f"{label}: {left} vs {right}"
    return None


def _same_count(x, y, na: dict, nb: dict) -> bool:
    """Two plain numbers count the same thing: the same noun after both, or no noun after one of them and the same
    order of magnitude ("248,000 residents" vs "251,000," but not "1" vs "55")."""
    a, b = na.get(x[0], ""), nb.get(y[0], "")
    if a and b:
        return a == b
    return _magnitude(x[0]) == _magnitude(y[0])


def values_conflict(va: list[str], vb: list[str], *, nouns: tuple[dict, dict] | None = None) -> str | None:
    """Reason string when two value lists disagree, else None.

    `nouns` = (value_nouns(text_a), value_nouns(text_b)): plain numbers are then compared only with plain numbers
    counting the same thing (_same_count: "55 countries" never against "1 winner"); without it all plain numbers
    compare.

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
    other = _group_conflict(va, vb, temporal=False, nouns=nouns)
    when = _group_conflict(va, vb, temporal=True)
    if other and when:
        return None
    return other or when


_RANGE = re.compile(r"\bbetween\s+(\S+)\s+and\s+\S+(?:\s+(?:million|billion|trillion|thousand|bn|mn))?(?:\s+[^\W\d]+)?"
                    r"|(?<![\w.])\d[\d,.]*\s*(?:–|—|-|to)\s*\d[\d,.]*(?:\s+(?:million|billion|trillion|thousand|bn|mn))?"
                    r"(?:\s+[^\W\d]+)?", re.IGNORECASE)


def ranges(s: str) -> list[tuple[str, str]]:
    """Ranges stated in `s` as (low, high) canonical values: "between 73 and 99 bn neurons", "14–16 billion
    neurons", "5 to 7 km" (the bare first number takes the second's unit, as in values())."""
    out = []
    for m in _RANGE.finditer(s or ""):
        found = values(m.group(0))
        if len(found) >= 2:
            lo, hi = found[0], found[1]
            ka, ga, pa = value_kind(lo)
            kb, gb, pb = value_kind(hi)
            if ga == gb and ka not in ("version", "date") and not ka.startswith("text") and pa <= pb:
                out.append((lo, hi))
    return out


def within(v: str, lo: str, hi: str) -> bool | None:
    """None when `v` is not comparable with the range's values, else whether lo <= v <= hi (within tolerance)."""
    kv, gv, pv = value_kind(v)
    kl, gl, pl = value_kind(lo)
    _, gh, ph = value_kind(hi)
    if gv != gl or gl != gh or kv.startswith("text") or kv in ("version", "date"):
        return None
    return pl <= pv <= ph or bool(values_equal(v, lo)) or bool(values_equal(v, hi))


def values_equal(a: str, b: str, *, rel_tol: float | None = None) -> bool | None:
    """None when two canonical values are not comparable (different comparison groups), else whether they are
    equal within tolerance (as in values_conflict; `rel_tol` overrides the relative tolerance of numbers and
    quantities). A year and a date compare by year."""
    ka, ga, pa = value_kind(a)
    kb, gb, pb = value_kind(b)
    if {ka, kb} == {"year", "date"}:
        year = lambda k, p: p if k == "year" else datetime.fromtimestamp(p, timezone.utc).year   # noqa: E731
        return year(ka, pa) == year(kb, pb)
    if ga != gb:
        if {ga, gb} <= {"number:int", "number:dec"}:
            ga = gb
        else:
            return None
    if rel_tol is not None and ka not in ("date", "year", "version") and not ka.startswith("text"):
        scale = max(abs(pa), abs(pb))
        return scale == 0 or abs(pa - pb) / scale <= rel_tol
    return _same(ka, pa, pb, a, b)


def values_agree(va: list[str], vb: list[str]) -> bool:
    """True when the two lists share at least one equal value and do not conflict (values_conflict)."""
    if not va or not vb or values_conflict(va, vb):
        return False
    return any(values_equal(x, y) for x in va for y in vb)


_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3, "": 4, "post": 5}


def version_key(v) -> tuple:
    """Sort key for version strings ("v2.5.5" > "2.5.4" > "2.5.5rc1" > "2.5.5.dev1"; numeric, not lexical);
    () for text without a version number."""
    m = re.search(r"(\d+(?:\.\d+)*)(?:[-.]?(dev|alpha|beta|post|rc|a|b)(\d*))?", str(v or ""), re.IGNORECASE)
    if not m:
        return ()
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * max(0, 4 - len(nums))
    tag = (m.group(2) or "").lower()
    return nums + (_PRE_RANK.get(tag, 4), int(m.group(3) or 0))


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
           "sentences", "shingles", "stem", "strip_heading", "tokens", "value_kind", "value_nouns", "values", "values_agree",
           "values_conflict", "values_equal", "version_key"]
