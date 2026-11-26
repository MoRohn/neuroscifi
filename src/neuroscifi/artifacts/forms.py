"""The form planner (DESIGN §5, ADR-073): decide what form a result should take BEFORE anything is made.

`plan(request, content, context, adjustments=None) -> FormPlan` is a pure, deterministic function. It reads the
content's shape (`ContentShape`: strong evidence), the request's words (weak evidence), an explicit form if one was
named (always honoured when feasible), and the context (device, accessibility, budget, preferences, the generators
`make.catalog()` lists at call time). It returns every candidate with every criterion score, the chosen form (or two
forms combined), which evidence decided (`decided_by`: "explicit", "content", "words" or "context") and one sentence
a non-technical person can read.

How a choice is made, in order:
1. Read the request without a model (`read_request`): an explicit form named in strict phrases ("as a table", "plot
   ...", "read it aloud"), forms refused in words ("no chart"), purposes by cue words (weak: weight 0.5 each; 1.5
   for hearing, which names a sense), wishes for a short, a full or an exact answer, and the size of the answer from
   the question's grammar (`answer_class`: a yes-or-no question, an extreme, a count, one time/person/place, one
   measurement, a typical value: a few values picked out of the content). Purposes the content cannot serve are
   dropped (a question "how did it change" about content with no order has no change to show).
2. The content's own purposes (strong: weight 1.0 shared among its kinds), e.g. a series in time -> change over time.
3. Every registered form is a candidate; so is every combination of a non-text form with a second form that may
   follow one. Hard constraints remove candidates first: the device must show the form's media; accessibility needs
   (voice only, a screen reader needs the text that goes with a picture, reduced motion); feasibility (no generator
   here, or the form cannot carry this content at all); budget (estimated seconds and bytes; a chart or a table over
   the byte budget is cut down to fit instead, carrying less); forms refused in words or by a preference of -1; the
   pre-production check (`foresee`) rejecting a form when the form it would switch to is here; a data file only when
   keeping a copy is a purpose, the device is an agent, or it is named; data heard as tones only when hearing is.
4. Each remaining candidate scores in [0, 1] on CRITERIA by the rules stated beside each function below; the total is
   the weighted mean (WEIGHTS, optionally shifted within clamps by `adjustments`), plus a stated preference. A
   problem the pre-production check predicts is listed with its fix and costs no score: what it loses is already
   counted in carried information (a scoring penalty that grew with the content could move the choice back to words).
5. Rules over the totals, in order: an explicit form wins when feasible (else the substitute with the best total
   less NEAREST_WEIGHT x its difference in kind, explained); a sentence's worth (at most 4 items and 6 values; never
   a picture, sound, solid, surface or process) stays words (except hearing a value, which words cannot do); a wish
   for a short answer, or a question picking a few values out of the content, is words; a stated preference is
   followed; when hearing is the main purpose, the best form that is heard; words that cover less than half of a
   kind of the content are not chosen while another form is admitted; the simplest form within SIMPLE_MARGIN of the
   best wins; a non-text form is chosen unasked only when the best beats words by MATERIAL (MATERIAL_RICH after "show
   me"); a picture, sound or model chosen unasked comes with a sentence on what it shows (the caption). A
   combination exists only when its second form adds what the first lacks (words: carried information on kinds the
   first shows poorly; rows: precision), the first carries at least half on its own, and adds something to the
   second; a screen reader's companion is any text form.
6. `decided_by` compares three runs: content alone, content and words, everything. The first evidence that changed
   the choice decided it.

The planner decides FORM only. It never decides or checks truth, and what it returns is a plan for an output, not
evidence: it must never be cited in a claim or stored on the evidence side (ADR-049, ADR-071). It knows nothing of
taste, of the person's history beyond the preferences handed to it, or of whether the content is right.
Every estimate `foresee` returns is a prediction from counts, labelled as such; Wave O-1 compares them with what
validators measure.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .types import SHAPE_KINDS, ContentShape, Malformed

# ---------------------------------------------------------------------------------------------------------------
# Vocabularies

DEVICES = ("console", "pocket", "voice", "cli", "agent")
MEDIA = ("text", "speech", "picture", "sound", "interactive", "motion", "file", "3d")
ACCESSIBILITY = ("voice_only", "screen_reader", "reduced_motion", "no_sound")
PURPOSES = ("look_up", "compare", "change_over_time", "distribution", "relation", "follow_process", "what_if",
            "locate", "hear", "keep", "learn", "look_at", "decide", "understand")
CRITERIA = ("clarity", "carried", "precision", "interactivity", "effort", "feasibility", "accessibility", "cost")
DECIDED_BY = ("explicit", "content", "words", "context")

# The weights table. Feasibility is a hard constraint (an infeasible form is removed), so its weight is 0: every
# admitted candidate scores 1 on it and it cannot tell two of them apart.
WEIGHTS = MappingProxyType({"clarity": 0.30, "carried": 0.26, "precision": 0.10, "interactivity": 0.08,
                            "effort": 0.12, "feasibility": 0.0, "accessibility": 0.06, "cost": 0.08})

SIMPLE_MARGIN = 0.03        # the simplest form within this of the best total wins
MATERIAL = 0.08             # a non-text form unasked must beat words by at least this
MATERIAL_RICH = 0.03        # ... when the person asked to be shown ("show me", "in detail")
COMBO_CARRIED_GAIN = 0.10   # a second form must add at least this share of carried information ...
COMBO_PRECISION_NEED = 0.5  # ... or, where precise values are needed at least this much,
COMBO_PRECISION_GAIN = 0.3  # ... add at least this much precision
ATTENTION_S = 30.0          # the attention bound for a sound or an animation, in seconds
SMALL_VALUES = 6            # at most this many values ...
SMALL_ITEMS = 4             # ... of at most this many items (rows, steps, nodes, events) is a sentence's worth
MEASUREMENTS_ONLY = 0.3     # how much of a picture, sound, solid or surface words or rows carry: its measurements
SMALL_CHARS = 600           # at most this many characters of text is a sentence's worth (a short paragraph)
KIND_DIFFERENCE = 0.4       # forms at least this far apart (`difference`) differ in kind
MAX_ADJUST = 0.10           # the most an adjustment can move one weight
MAX_ADJUSTMENTS = 256       # entries an adjustments table may hold
HEARD_CARRIED = 0.3         # a form that is heard must carry at least this much to serve "hear"
PRECISE_NEED = 0.8          # the precision need when the words ask for exact values
COMBO_PRIMARY_CARRIED = 0.5  # the first form of a combination must carry at least this much on its own
PREFERENCE_WEIGHT = 0.05    # a preference in [-1, 1] moves a total by at most this; -1 removes the form

# A "specific" kind says what the content is; "records" only says it has rows. When a shape has a specific kind,
# records adds nothing to what a form must carry.
GENERIC_KINDS = ("records",)
# The kind that names the content first (for sentences and for the adjustments key).
KIND_PRIORITY = ("process", "network", "hierarchy", "solid", "surface", "map", "image", "sound", "timeline",
                 "sequence", "relation", "distribution", "series", "categories", "records", "code", "prose",
                 "scalar")
VALUE_KINDS = ("scalar", "series", "categories", "distribution", "relation", "records")
UNIT_OF_KIND = {"scalar": "values", "series": "values", "categories": "values", "relation": "values",
                "records": "values", "distribution": "dist", "network": "items", "hierarchy": "items",
                "sequence": "items", "timeline": "items", "process": "items", "map": "items", "surface": "samples",
                "solid": "samples", "image": "samples", "sound": "samples", "prose": "chars", "code": "chars"}
DIST_UNITS = 40             # a distribution's information saturates at about 40 bins' worth


# ---------------------------------------------------------------------------------------------------------------
# Inputs and outputs


@dataclass(frozen=True)
class Request:
    """What was asked. `explicit_form`: a form name from FORMS or a plain alias ("chart", "table", "sound", ...)
    when the caller already knows the person named one; otherwise the words are read for one. `purpose`: one of
    PURPOSES when the caller knows it (it counts as words, stronger than a cue)."""

    text: str = ""
    explicit_form: str | None = None
    purpose: str | None = None

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise Malformed("the request's text must be a string")
        if len(self.text) > 20_000:
            object.__setattr__(self, "text", self.text[:20_000])
        if self.purpose is not None and self.purpose not in PURPOSES:
            raise Malformed(f"unknown purpose {self.purpose!r}")
        if self.explicit_form is not None and _explicit_set(self.explicit_form) is None:
            raise Malformed(f"unknown form {self.explicit_form!r}")


@dataclass(frozen=True)
class Context:
    """Where and for whom. `shows`: the media this device can present (None: the device's defaults, DEVICE_SHOWS).
    `preferences`: form name, family or medium -> value in [-1, 1] (-1: never). `generators`: names of available
    generators (None: read `make.catalog()` when `plan` is called)."""

    device: str = "console"
    shows: tuple[str, ...] | None = None
    width_px: int | None = None
    accessibility: tuple[str, ...] = ()
    preferences: Mapping[str, float] = field(default_factory=dict)
    budget_s: float = 60.0
    budget_bytes: int = 16 << 20
    generators: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.device not in DEVICES:
            raise Malformed(f"unknown device {self.device!r}")
        if self.shows is not None:
            object.__setattr__(self, "shows", tuple(self.shows))
            bad = [m for m in self.shows if m not in MEDIA]
            if bad:
                raise Malformed(f"unknown media {bad!r}")
        object.__setattr__(self, "accessibility", tuple(self.accessibility))
        bad = [a for a in self.accessibility if a not in ACCESSIBILITY]
        if bad:
            raise Malformed(f"unknown accessibility need(s) {bad!r}")
        prefs = {}
        for key, value in dict(self.preferences).items():
            if not isinstance(key, str) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise Malformed("a preference is a name and a finite number")
            prefs[key] = max(-1.0, min(1.0, float(value)))
        object.__setattr__(self, "preferences", MappingProxyType(dict(sorted(prefs.items()))))
        if not (math.isfinite(self.budget_s) and self.budget_s > 0) or self.budget_bytes <= 0:
            raise Malformed("the budget must be positive")
        if self.width_px is not None and not 120 <= int(self.width_px) <= 10_000:
            raise Malformed("width_px must be between 120 and 10000")
        if self.generators is not None:
            object.__setattr__(self, "generators", tuple(sorted(set(map(str, self.generators)))))


@dataclass(frozen=True)
class Display:
    """A device resolved to numbers: what it shows and how much fits."""

    device: str
    shows: frozenset
    width_px: int           # usable width for a picture
    screen_rows: int        # table rows on one screen
    screen_cols: int        # table columns on one screen

    @property
    def plot_px(self) -> int:
        return max(60, int(self.width_px * 0.85))


DEVICE_SHOWS = {
    "console": ("text", "speech", "picture", "sound", "interactive", "motion", "file", "3d"),
    "pocket": ("text", "speech", "picture", "sound", "interactive", "motion", "file", "3d"),
    "voice": ("speech", "sound"),
    "cli": ("text", "file"),
    "agent": ("text", "file"),
}
_DEVICE_SIZE = {"console": (960, 30, 8), "pocket": (360, 15, 4), "voice": (360, 0, 0), "cli": (640, 40, 6),
                "agent": (960, 200, 50)}


@dataclass(frozen=True)
class Problem:
    """A predicted problem of a form for this content (an estimate, not a measurement)."""

    code: str               # "too_many_series", "points_per_pixel", "sound_too_long", ...
    text: str               # a plain sentence
    fix: str | None         # "downsample", "facet", "aggregate", "page", "shorten", "label_subset", "switch:<form>"
    severity: str = "adapt"  # "adapt": the form is made with the fix; "reject": the form is dropped for the switch


@dataclass(frozen=True)
class Foresight:
    problems: tuple[Problem, ...]
    estimates: Mapping[str, float]      # seconds, bytes and form-specific counts; all predictions from counts
    label: str = "estimate"


@dataclass(frozen=True)
class Candidate:
    forms: tuple[str, ...]              # one form, or two combined (the first leads)
    scores: Mapping[str, float]         # criterion -> [0, 1], every criterion
    total: float
    excluded: str | None = None         # why a hard constraint removed it (plain words); None when admitted
    adaptations: tuple[str, ...] = ()   # fixes the pre-production check would apply
    problems: tuple[str, ...] = ()      # predicted problems, plain sentences
    estimates: Mapping[str, float] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return self.excluded is None

    def to_dict(self) -> dict:
        return {"forms": list(self.forms), "scores": dict(self.scores), "total": self.total,
                "excluded": self.excluded, "adaptations": list(self.adaptations), "problems": list(self.problems),
                "estimates": dict(self.estimates)}


@dataclass(frozen=True)
class FormPlan:
    chosen: tuple[str, ...]
    candidates: tuple[Candidate, ...]   # all of them: admitted ones by total, then the excluded
    decided_by: str
    reason: str                         # one sentence in plain words
    purpose: str                        # the main purpose
    purposes: Mapping[str, float]       # the purpose mix that scored clarity
    evidence: tuple[str, ...]           # what was read, in plain words (cue names, never the request's text)
    explicit: tuple[str, ...] | None    # the forms the person named, if any
    explicit_honoured: bool | None
    weights: Mapping[str, float]
    notes: tuple[str, ...] = ()
    label: str = "plan: made by the system, not evidence"

    def candidate(self, forms) -> Candidate | None:
        forms = (forms,) if isinstance(forms, str) else tuple(forms)
        return next((c for c in self.candidates if c.forms == forms), None)

    def to_dict(self) -> dict:
        return {"chosen": list(self.chosen), "decided_by": self.decided_by, "reason": self.reason,
                "purpose": self.purpose, "purposes": dict(self.purposes), "evidence": list(self.evidence),
                "explicit": list(self.explicit) if self.explicit else None,
                "explicit_honoured": self.explicit_honoured, "weights": dict(self.weights),
                "notes": list(self.notes), "label": self.label,
                "candidates": [c.to_dict() for c in self.candidates]}


# ---------------------------------------------------------------------------------------------------------------
# Forms


@dataclass(frozen=True)
class Form:
    """One form a result can take. `kinds`: content kind -> how much of that kind's structure the form can show
    at all (0..1). `capacity`: unit ("values", "dist", "items", "samples", "chars") -> how many units it shows
    without loss; beyond that it shows `floor` of the information plus the share that fits. `cover`, when given,
    replaces that generic rule for one kind (charts count points per series and series per style). `rule` states
    the carried rule in words. `generator`: the name `make.catalog()` must list (None: needs no generator)."""

    name: str
    label: str                          # "a line chart"
    family: str                         # forms of one family do not differ in kind
    encoding: str                       # what carries the values: "words", "position", "length", "links", ...
    media: tuple[str, ...]              # every medium the device must show
    kinds: Mapping[str, float]
    capacity: Mapping[str, float]
    floor: float
    rule: str
    generator: str | None
    precision: float                    # how exactly a value can be read off it
    interactivity: float                # 0 static, 0.5 navigable, 1 manipulable
    effort: float                       # base effort for the person
    complexity: int                     # for "the simplest form within a margin"
    cost_class: str                     # "none", "low", "medium", "high"
    fallback: str | None                # the accessible form that comes with it
    bytes_per_unit: float = 0.0
    base_bytes: float = 0.0
    secondary: bool = False             # may be the second form of a combination
    text_like: bool = False             # counts as words/text for the "non-text unasked" rule
    cover: Callable | None = None       # (kind, shape, display) -> coverage in [0, 1], or None for the generic rule
    check: Callable | None = None       # (shape, display) -> list[Problem]
    min_items: int = 0                  # fewer items than this: the form carries nothing
    second_for: tuple[str, ...] = ()    # only when one of these purposes is present (alone or second), unless
    #                                     named (a data file is also always admitted for an agent: a program reads it)

    @property
    def devices(self) -> tuple[str, ...]:
        """The devices whose default media show this form."""
        return tuple(d for d in DEVICES if set(self.media) <= set(DEVICE_SHOWS[d]))

    def carried_kinds(self, shape: ContentShape, display: Display | None = None) -> dict:
        """Per content kind: fit x coverage. Records is ignored when the shape has a more specific kind."""
        display = display or resolve_display(Context())
        out = {}
        for kind in _specific_kinds(shape):
            fit = float(self.kinds.get(kind, 0.0))
            if fit <= 0 or shape.n_items < self.min_items:
                out[kind] = 0.0
                continue
            coverage = self.cover(kind, shape, display) if self.cover else None
            if coverage is None:
                coverage = _generic_coverage(self, kind, shape)
            out[kind] = max(0.0, min(1.0, fit * coverage))
        return out

    def carried(self, shape: ContentShape, display: Display | None = None) -> float:
        """The share of the content's information this form presents without loss (mean over its kinds)."""
        per = self.carried_kinds(shape, display)
        return sum(per.values()) / len(per) if per else 0.0


FORMS: dict[str, Form] = {}


def register_form(form: Form) -> Form:
    """Add a form (a new form is one registration). Names are unique; vocabularies are checked."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", form.name or ""):
        raise Malformed("a form needs a lower-case name")
    if form.name in FORMS:
        raise Malformed(f"a form named {form.name!r} is already registered")
    bad = [k for k in form.kinds if k not in SHAPE_KINDS] + [m for m in form.media if m not in MEDIA]
    if bad:
        raise Malformed(f"unknown kind or medium {bad!r}")
    if form.cost_class not in _COST_SECONDS:
        raise Malformed(f"unknown cost class {form.cost_class!r}")
    for value in (form.precision, form.interactivity, form.effort, form.floor, *form.kinds.values()):
        if not 0.0 <= value <= 1.0:
            raise Malformed("fits, precision, interactivity, effort and floor are in [0, 1]")
    FORMS[form.name] = form
    return form


def unregister_form(name: str) -> None:
    FORMS.pop(name, None)


_COST_SECONDS = {"none": 0.0, "low": 0.05, "medium": 0.5, "high": 2.0}
_BYTES_PER_SECOND = 20e6            # an estimated generation throughput


def _specific_kinds(shape: ContentShape) -> tuple[str, ...]:
    kinds = tuple(dict.fromkeys(shape.kinds))
    specific = tuple(k for k in kinds if k not in GENERIC_KINDS)
    return specific or kinds


def _units(kind: str, shape: ContentShape) -> float:
    unit = UNIT_OF_KIND[kind]
    if kind == "scalar":
        return 1.0
    if unit == "values":
        return max(1, shape.n_items) * max(1, shape.n_series)
    if unit == "dist":
        return min(max(1, shape.n_items), DIST_UNITS) * max(1, shape.n_series)
    if unit == "chars":     # words or code: their characters, or their items when no characters are known
        return max(1, shape.text_chars) if shape.text_chars else max(1, shape.n_items)
    return max(1, shape.n_items)


def _within(units: float, cap: float, floor: float) -> float:
    """1 up to the capacity; beyond it the floor plus the share that fits."""
    if units <= cap:
        return 1.0
    return floor + (1.0 - floor) * cap / units


def _generic_coverage(form: Form, kind: str, shape: ContentShape) -> float:
    unit = UNIT_OF_KIND[kind]
    cap = form.capacity.get(unit)
    if cap is None:
        return 1.0      # the form shows this kind as a whole
    if cap == 0:
        return MEASUREMENTS_ONLY    # it can state measurements of the thing, never show the thing
    floor = form.floor
    if unit == "chars" and form.text_like:
        floor = max(floor, 0.3)     # words keep their own words' gist when they run long
    return _within(_units(kind, shape), cap, floor)


def _values(shape: ContentShape) -> int:
    return max(1, shape.n_items) * max(1, shape.n_series)


# Chart coverage rules (stated): series are told apart by line style (6), bar pattern (4), sonification voice (2).
LINE_STYLES = 6
BAR_PATTERNS = 4
SOUND_VOICES = 2
MIN_BAR_PX = 12
MAX_PANELS = 24


def _count(kind: str, shape: ContentShape) -> int:
    """Items a chart draws for one kind: a distribution's information saturates at DIST_UNITS bins."""
    n = max(1, shape.n_items)
    return min(n, DIST_UNITS) if kind == "distribution" else n


def _cover_line(kind, shape, display):
    if kind == "scalar":
        return 0.0
    styles = min(1.0, LINE_STYLES / max(1, shape.n_series))
    return styles * _within(_count(kind, shape), 2 * display.plot_px, 0.6)


def _cover_bar(kind, shape, display):
    patterns = min(1.0, BAR_PATTERNS / max(1, shape.n_series))
    return patterns * _within(_count(kind, shape), max(4, display.plot_px // MIN_BAR_PX), 0.2)


def _cover_scatter(kind, shape, display):
    if kind != "map" and shape.n_series < 2:
        return 0.0      # a scatter needs two numeric measures (or coordinates)
    return _within(_count(kind, shape), 5000, 0.5)


def _cover_histogram(kind, shape, display):
    return 1.0 if shape.n_items >= 10 else 0.0


def _cover_heatmap(kind, shape, display):
    if kind in ("series", "categories", "records") and shape.n_series < 3:
        return 0.0      # a matrix needs at least three columns
    cells = _count(kind, shape) * (max(1, shape.n_series) if kind in VALUE_KINDS else 1)
    return _within(cells, (display.plot_px // 3) ** 2, 0.6)


def _cover_multiples(kind, shape, display):
    if shape.n_series < 2:
        return 0.0      # one series has no multiples
    panels = min(1.0, MAX_PANELS / shape.n_series)
    return panels * _within(_count(kind, shape), 2 * display.plot_px // 4, 0.5)


def _cover_timeline(kind, shape, display):
    if kind != "timeline" and not shape.has_time:
        return 0.0
    return _within(_count(kind, shape), 500, 0.3)


def _cover_explorable(kind, shape, display):
    return 1.0 if shape.has_parameters else 0.0


def _cover_sonify(kind, shape, display):
    if "sound" in shape.kinds:
        return 0.0      # a recording is already a sound: it is not turned into tones
    voices = min(1.0, SOUND_VOICES / max(1, shape.n_series))
    return voices * _within(_count(kind, shape), ATTENTION_S * 4, 0.3)


def _cover_document(kind, shape, display):
    if kind == "hierarchy" and "prose" not in shape.kinds:
        return 0.5 * _within(max(1, shape.n_items), 2000, 0.5)    # an outline of names, not headings over text
    return None


def _cover_map(kind, shape, display):
    if shape.has_space < 2 and kind != "map":
        return 0.0      # values without places are not drawn on a map
    return None


def _cover_stepping(kind, shape, display):
    if kind == "code" and not {"sequence", "process"} & set(shape.kinds):
        return 0.2      # code that is not run in steps is read, not stepped through
    return None


def _cover_motion(kind, shape, display):
    moving = 1.0 if shape.has_time and shape.has_space else 0.7     # motion shows best what moves in space
    return moving * _within(_count(kind, shape), 360, 0.4)


def _cover_image(kind, shape, display):
    if kind in ("map", "surface") and shape.has_space < 2:
        return 0.0
    if kind == "image" and "sequence" in shape.kinds:
        return _within(max(1, shape.n_items), 1, 0.3)     # one picture of a sequence of pictures
    return _within(_count(kind, shape), 16_000_000, 0.5)


def _cover_frames(kind, shape, display):
    if kind == "image" and "sequence" not in shape.kinds and not shape.has_time:
        return 0.0      # one picture is not a sequence
    return _within(_count(kind, shape), 600, 0.5)


def _p(code, text, fix, severity="adapt"):
    return Problem(code, text, fix, severity)


def _check_words(cap_values, cap_items, cap_chars, name):
    def check(shape, display):
        problems = []
        kinds = _specific_kinds(shape)
        if any(UNIT_OF_KIND[k] in ("values", "dist") for k in kinds) and _values(shape) > cap_values:
            problems.append(_p("summary_only", f"{name} can state about {cap_values} of the {_values(shape)} values: "
                               "it would give a summary (first, last, largest, smallest)", "summarise"))
        if any(UNIT_OF_KIND[k] == "items" for k in kinds) and shape.n_items > cap_items:
            problems.append(_p("summary_only", f"{name} can follow about {cap_items} of the {shape.n_items} items",
                               "summarise"))
        if any(UNIT_OF_KIND[k] == "chars" for k in kinds) and shape.text_chars > cap_chars:
            problems.append(_p("too_long", f"the text has {shape.text_chars} characters; {name} holds about "
                               f"{cap_chars}", "shorten"))
        return problems
    return check


def _rows(shape: ContentShape) -> int:
    """Rows a table of this content has: its items, unless every kind is a single value or words."""
    if all(k in ("scalar", "prose", "code") for k in _specific_kinds(shape)):
        return 1
    return max(1, shape.n_items)


def _check_table(shape, display):
    problems = []
    rows, cols = _rows(shape), max(1, shape.n_series)
    cells = rows * cols
    if display.screen_rows and rows > display.screen_rows:
        problems.append(_p("table_beyond_screen", f"{rows} rows are more than one screen of {display.screen_rows}; "
                           f"about {math.ceil(rows / display.screen_rows)} pages", "page"))
    if display.screen_cols and cols > display.screen_cols:
        problems.append(_p("table_too_wide", f"{cols} columns are more than the {display.screen_cols} that fit",
                           "scroll"))
    if cells > 20_000:
        problems.append(_p("table_too_large", f"{cells} cells are more than a person reads in a table",
                           "switch:data_file", "reject"))
    return problems


def _label_px(shape, n):
    """Typical label width in pixels: the content's own characters per item when known, else 6 characters."""
    chars = shape.text_chars / n if shape.text_chars and n else 6
    return 7.0 * min(40.0, max(3.0, chars))


def _check_line(shape, display):
    problems = []
    if shape.n_series > LINE_STYLES:
        severity = "reject" if shape.n_series > 3 * LINE_STYLES else "adapt"
        problems.append(_p("too_many_series", f"{shape.n_series} series are more than the {LINE_STYLES} line "
                           "styles that tell them apart", "switch:small_multiples", severity))
    ppx = shape.n_items / display.plot_px
    if ppx > 2:
        problems.append(_p("points_per_pixel", f"about {ppx:.0f} points per pixel: the line would be drawn from "
                           "an envelope of minima and maxima", "downsample"))
    return problems


def _check_bar(shape, display):
    problems = []
    bars = max(1, shape.n_items) * max(1, shape.n_series)
    fit = max(4, display.plot_px // MIN_BAR_PX)
    if bars > fit:
        severity = "reject" if bars > 4 * fit else "adapt"
        problems.append(_p("too_many_categories", f"{bars} bars are more than the {fit} that fit the width",
                           "switch:table", severity))
    elif bars and _label_px(shape, shape.n_items) > display.plot_px / bars:
        problems.append(_p("label_overlap", "the category labels would overlap at this width", "label_subset"))
    if shape.n_series > BAR_PATTERNS:
        problems.append(_p("too_many_series", f"{shape.n_series} series are more than the {BAR_PATTERNS} bar "
                           "patterns", "facet"))
    return problems


def _check_scatter(shape, display):
    if shape.n_items > 5000:
        return [_p("overplotting", f"{shape.n_items} points would cover each other", "aggregate")]
    return []


def _check_histogram(shape, display):
    if shape.n_items < 20:
        return [_p("too_few_values", f"{shape.n_items} values make a sparse histogram", "switch:table")]
    return []


def _check_heatmap(shape, display):
    cells = max(1, shape.n_items) * max(1, shape.n_series)
    side = display.plot_px / math.sqrt(cells)
    if side < 3:
        return [_p("cells_too_small", f"cells about {side:.1f} pixels wide", "aggregate")]
    return []


def _check_multiples(shape, display):
    if shape.n_series > MAX_PANELS:
        return [_p("too_many_panels", f"{shape.n_series} panels are more than {MAX_PANELS} on one screen", "page")]
    return []


def _check_timeline(shape, display):
    problems = []
    if shape.n_items > 60:
        problems.append(_p("events_beyond_screen", f"{shape.n_items} events are more than one screen holds",
                           "scroll"))
    if shape.n_items and _label_px(shape, shape.n_items) * min(shape.n_items, 60) > display.plot_px * 6:
        problems.append(_p("label_overlap", "event labels would overlap", "label_subset"))
    return problems


def _check_nodelink(shape, display):
    problems = []
    if shape.n_items > 150:
        severity = "reject" if shape.n_items > 2000 else "adapt"
        problems.append(_p("too_many_nodes", f"{shape.n_items} nodes are more than about 150 that stay readable",
                           "aggregate" if severity == "adapt" else "switch:table", severity))
    area = display.plot_px * display.plot_px * 0.75
    if shape.n_items and shape.n_items * _label_px(shape, shape.n_items) * 14 > 0.3 * area:
        problems.append(_p("label_overlap", "node labels would overlap; only the most connected would be labelled",
                           "label_subset"))
    return problems


def _check_tree(shape, display):
    if shape.n_items > 300:
        return [_p("too_many_nodes", f"{shape.n_items} nodes: deeper levels would start folded", "collapse")]
    return []


def _check_flow(shape, display):
    if shape.n_items > 20:
        severity = "reject" if shape.n_items > 60 else "adapt"
        return [_p("too_many_steps", f"{shape.n_items} steps are more than a flow on one screen holds",
                   "switch:step_through", severity)]
    return []


def _check_steps(shape, display):
    if shape.n_items > 500:
        return [_p("too_many_steps", f"{shape.n_items} steps: every {math.ceil(shape.n_items / 500)}th is kept",
                   "downsample")]
    return []


def _check_animation(shape, display):
    seconds = max(shape.duration_s, shape.n_items / 12.0)
    if seconds > ATTENTION_S:
        return [_p("animation_too_long", f"about {seconds:.0f} seconds is longer than the {ATTENTION_S:.0f}-second "
                   "attention bound", "shorten")]
    return []


def _check_sonify(shape, display):
    problems = []
    seconds = shape.n_items * 0.25
    if seconds > ATTENTION_S:
        problems.append(_p("sound_too_long", f"one tone per value would last about {seconds:.0f} seconds, longer "
                           f"than the {ATTENTION_S:.0f}-second attention bound", "downsample"))
    if shape.n_series > SOUND_VOICES:
        problems.append(_p("too_many_voices", f"{shape.n_series} series are more than {SOUND_VOICES} voices can "
                           "tell apart", "facet"))
    return problems


def _check_tone(shape, display):
    if _values(shape) > 12:
        return [_p("sound_too_long", f"{_values(shape)} tones are more than a short sound holds", "shorten")]
    return []


def _check_raster(shape, display):
    if shape.n_items > (2 * display.width_px) ** 2:
        return [_p("downscaled", "the picture is larger than the screen; it would be shown smaller", "downsample")]
    return []


def _check_solid(shape, display):
    if shape.n_items > 2_000_000:
        return [_p("too_many_vertices", f"{shape.n_items} vertices would be simplified for viewing", "downsample")]
    return []


def _check_document(shape, display):
    if shape.text_chars > 200_000:
        return [_p("too_long", "the document would be split into parts", "page")]
    return []


def _form(name, label, family, encoding, media, kinds, *, capacity=None, floor=0.0, rule, generator, precision,
          interactivity, effort, complexity, cost_class, fallback, bytes_per_unit=0.0, base_bytes=0.0,
          secondary=False, text_like=False, cover=None, check=None, min_items=0, second_for=()):
    return register_form(Form(name, label, family, encoding, tuple(media), MappingProxyType(dict(kinds)),
                              MappingProxyType(dict(capacity or {})), floor, rule, generator, precision,
                              interactivity, effort, complexity, cost_class, fallback, bytes_per_unit, base_bytes,
                              secondary, text_like, cover, check, min_items, tuple(second_for)))


_form("prose", "a sentence or two", "words", "words", ("text",),
      {"scalar": 1, "series": 1, "categories": 1, "distribution": 1, "relation": 0.8, "records": 1,
       "hierarchy": 0.7, "network": 0.6, "sequence": 1, "timeline": 1, "process": 0.6, "surface": 0.3, "solid": 0.3,
       "map": 0.5, "image": 0.3, "sound": 0.3, "prose": 1, "code": 0.8},
      capacity={"values": 7, "dist": 7, "items": 8, "chars": 3000, "samples": 0}, floor=0.0,
      rule="about 7 values, 8 steps or items, or 3,000 characters; of a picture, sound or solid about 7 of its "
           "measurements",
      generator=None, precision=0.9, interactivity=0.0, effort=0.05, complexity=0, cost_class="none",
      fallback=None, base_bytes=600, secondary=True, text_like=True,
      check=_check_words(7, 8, 3000, "a paragraph"))
_form("spoken", "a spoken summary", "words", "words", ("speech",),
      {"scalar": 1, "series": 1, "categories": 1, "distribution": 1, "relation": 0.7, "records": 1,
       "hierarchy": 0.5, "network": 0.4, "sequence": 1, "timeline": 1, "process": 0.5, "surface": 0.2, "solid": 0.2,
       "map": 0.4, "image": 0.2, "sound": 0.3, "prose": 1, "code": 0.2},
      capacity={"values": 3, "dist": 3, "items": 4, "chars": 900, "samples": 0}, floor=0.0,
      rule="about 3 values, 4 steps or 900 characters (a minute of speech)",
      generator=None, precision=0.7, interactivity=0.0, effort=0.1, complexity=0, cost_class="none",
      fallback=None, base_bytes=300, secondary=True, text_like=True,
      check=_check_words(3, 4, 900, "a spoken summary"))
_form("table", "a table", "table", "grid", ("text",),
      {"scalar": 1, "series": 1, "categories": 1, "distribution": 1, "relation": 0.8, "records": 1,
       "hierarchy": 0.5, "network": 0.6, "sequence": 0.8, "timeline": 0.9, "process": 0.4, "surface": 0.6,
       "solid": 0.2, "map": 0.4, "image": 0.1, "sound": 0.1, "prose": 0.2, "code": 0.1},
      capacity={"values": 20_000, "dist": 20_000, "items": 2000, "chars": 20_000, "samples": 0}, floor=0.3,
      rule="every cell up to 20,000 (in pages of one screen); beyond that 0.3 plus the share that fits",
      generator="table.html", precision=1.0, interactivity=0.0, effort=0.1, complexity=1, cost_class="low",
      fallback=None, bytes_per_unit=24, base_bytes=2000, secondary=True, text_like=True, check=_check_table)
_form("document", "a structured document", "document", "words", ("text",),
      {"prose": 1, "hierarchy": 1, "code": 1, "records": 0.8, "sequence": 0.8, "scalar": 1, "series": 0.6,
       "categories": 0.8, "distribution": 0.5, "relation": 0.5, "network": 0.4, "timeline": 0.8, "process": 0.4,
       "map": 0.3, "image": 0.2, "sound": 0.1, "surface": 0.2, "solid": 0.1},
      capacity={"values": 5000, "dist": 5000, "items": 2000, "chars": 500_000, "samples": 0}, floor=0.5,
      rule="headings, paragraphs and tables: 500,000 characters, 5,000 table cells, 2,000 items",
      generator="document.html", precision=0.9, interactivity=0.0, effort=0.2, complexity=2, cost_class="low",
      fallback=None, bytes_per_unit=1.2, base_bytes=4000, text_like=True, check=_check_document,
      cover=_cover_document)
_form("data_file", "a data file", "file", "file", ("file",),
      {"scalar": 1, "series": 1, "categories": 1, "distribution": 1, "relation": 1, "records": 1, "surface": 1,
       "timeline": 1, "network": 0.9, "hierarchy": 0.6, "sequence": 0.8, "map": 0.9, "process": 0.3},
      capacity={"values": 5_000_000, "dist": 5_000_000, "items": 1_000_000, "samples": 5_000_000}, floor=0.5,
      rule="every value up to 5,000,000 (to open elsewhere: nothing is shown)",
      generator="data.csv", precision=1.0, interactivity=0.0, effort=0.6, complexity=2, cost_class="low",
      fallback=None, bytes_per_unit=10, base_bytes=200, secondary=True, text_like=True, second_for=("keep",))
_form("line_chart", "a line chart", "chart", "position", ("picture",),
      {"series": 1, "timeline": 0.4, "distribution": 0.3, "process": 0.5, "relation": 0.6, "records": 0.6,
       "categories": 0.3, "sound": 0.5},
      rule="every point of up to 6 series (line styles), up to 2 points per pixel; beyond that an envelope "
           "carrying 0.6",
      generator="chart.line", precision=0.45, interactivity=0.0, effort=0.15, complexity=2, cost_class="low",
      fallback="table", bytes_per_unit=10, base_bytes=4000, cover=_cover_line, check=_check_line, min_items=2)
_form("bar_chart", "a bar chart", "chart", "length", ("picture",),
      {"categories": 1, "series": 0.6, "distribution": 0.6, "records": 0.7, "relation": 0.2, "timeline": 0.2},
      rule="one bar per value up to the width (12 px a bar) and 4 patterned series; beyond that 0.2 plus the "
           "share that fits",
      generator="chart.bar", precision=0.55, interactivity=0.0, effort=0.15, complexity=2, cost_class="low",
      fallback="table", bytes_per_unit=120, base_bytes=4000, cover=_cover_bar, check=_check_bar, min_items=2)
_form("scatter_chart", "a scatter chart", "chart", "position", ("picture",),
      {"relation": 1, "records": 0.7, "map": 0.8, "distribution": 0.4, "series": 0.5, "categories": 0.2},
      rule="every point up to 5,000 when there are two measures (or coordinates); beyond that 0.5 plus the share",
      generator="chart.scatter", precision=0.4, interactivity=0.0, effort=0.15, complexity=2, cost_class="low",
      fallback="table", bytes_per_unit=40, base_bytes=4000, cover=_cover_scatter, check=_check_scatter,
      min_items=3)
_form("histogram", "a histogram", "chart", "length", ("picture",),
      {"distribution": 1, "series": 0.4, "records": 0.4},
      rule="the whole distribution (about 40 bins' worth) of 10 or more values",
      generator="chart.histogram", precision=0.4, interactivity=0.0, effort=0.15, complexity=2, cost_class="low",
      fallback="table", base_bytes=8000, cover=_cover_histogram, check=_check_histogram, min_items=10)
_form("heatmap", "a heat map", "chart", "shade", ("picture",),
      {"surface": 1, "records": 0.6, "relation": 0.7, "categories": 0.5, "series": 0.5, "network": 0.5,
       "image": 0.5, "map": 0.4},
      rule="every cell of a matrix of 3 or more columns while a cell is 3 px or wider; beyond that 0.6 plus share",
      generator="chart.heatmap", precision=0.3, interactivity=0.0, effort=0.25, complexity=3, cost_class="low",
      fallback="table", bytes_per_unit=60, base_bytes=4000, cover=_cover_heatmap, check=_check_heatmap,
      min_items=2)
_form("small_multiples", "small multiples (one small chart per series)", "chart", "position", ("picture",),
      {"series": 1, "categories": 0.6, "distribution": 0.6, "records": 0.6, "relation": 0.5, "timeline": 0.3},
      rule="up to 24 panels, each with up to 2 points per pixel of its quarter width",
      generator="chart.small_multiples", precision=0.4, interactivity=0.0, effort=0.25, complexity=3,
      cost_class="low", fallback="table", bytes_per_unit=10, base_bytes=8000, cover=_cover_multiples,
      check=_check_multiples, min_items=2)
_form("timeline", "a timeline", "diagram", "position", ("picture",),
      {"timeline": 1, "sequence": 0.8, "series": 0.3, "records": 0.4, "process": 0.2},
      rule="every dated event up to 500 (scrolling); beyond that 0.3 plus the share",
      generator="timeline", precision=0.7, interactivity=0.0, effort=0.2, complexity=2, cost_class="low",
      fallback="table", bytes_per_unit=200, base_bytes=3000, cover=_cover_timeline, check=_check_timeline,
      min_items=2)
_form("node_link", "a node-link diagram", "diagram", "links", ("picture",),
      {"network": 1, "relation": 0.8, "hierarchy": 0.8, "sequence": 0.3, "process": 0.4, "records": 0.1, "code": 0.5},
      capacity={"items": 150, "values": 150}, floor=0.3,
      rule="every node and link up to about 150 nodes; beyond that 0.3 plus the share",
      generator="graph.nodelink", precision=0.6, interactivity=0.0, effort=0.3, complexity=3, cost_class="medium",
      fallback="table", bytes_per_unit=250, base_bytes=3000, check=_check_nodelink, min_items=2)
_form("tree_diagram", "a tree diagram", "diagram", "links", ("picture",),
      {"hierarchy": 1, "network": 0.3, "relation": 0.3, "records": 0.1, "code": 0.3},
      capacity={"items": 300, "values": 300, "chars": 20_000}, floor=0.5,
      rule="every node up to 300 (deeper levels folded beyond); beyond that 0.5 plus the share",
      generator="graph.tree", precision=0.7, interactivity=0.0, effort=0.2, complexity=2, cost_class="low",
      fallback="document", bytes_per_unit=120, base_bytes=3000, check=_check_tree, min_items=2)
_form("flow_steps", "a flow of steps", "diagram", "order", ("picture",),
      {"sequence": 1, "process": 0.6, "hierarchy": 0.2, "timeline": 0.4, "network": 0.3, "code": 0.6},
      capacity={"items": 20}, floor=0.3,
      rule="every step up to 20; beyond that 0.3 plus the share",
      generator="flow.steps", precision=0.8, interactivity=0.0, effort=0.15, complexity=2, cost_class="low",
      fallback="prose", bytes_per_unit=300, base_bytes=3000, check=_check_flow, min_items=2)
_form("step_through", "a step-through", "stepper", "order", ("picture", "interactive"),
      {"sequence": 0.9, "process": 0.9, "series": 0.4, "timeline": 0.5, "network": 0.4, "hierarchy": 0.4,
       "records": 0.3, "code": 0.9},
      capacity={"items": 500, "values": 500, "chars": 50_000}, floor=0.5,
      rule="one state per step up to 500 steps; beyond that 0.5 plus the share",
      generator="explorable.steps", precision=0.7, interactivity=0.6, effort=0.35, complexity=4,
      cost_class="medium", fallback="table", bytes_per_unit=400, base_bytes=60_000, check=_check_steps,
      cover=_cover_stepping,
      min_items=2)
_form("explorable", "an explorable model with sliders", "model", "manipulation", ("picture", "interactive"),
      {"process": 1, "relation": 0.6, "series": 0.5, "surface": 0.6},
      rule="the whole model when its inputs can be varied; nothing otherwise",
      generator="explorable.model", precision=0.5, interactivity=1.0, effort=0.4, complexity=5,
      cost_class="medium", fallback="table", base_bytes=80_000, cover=_cover_explorable)
_form("animation", "an animation", "motion", "motion", ("picture", "motion"),
      {"process": 0.9, "sequence": 0.6, "series": 0.4, "timeline": 0.5, "network": 0.3, "solid": 0.6,
       "surface": 0.6},
      rule="one frame per state up to 360 (30 s at 12 frames a second); beyond that 0.4 plus the share; 0.7 of "
           "that unless the content moves in space over time",
      generator="animation.svg", precision=0.3, interactivity=0.2, effort=0.25, complexity=4,
      cost_class="medium", fallback="flow_steps", bytes_per_unit=2000, base_bytes=5000, check=_check_animation,
      cover=_cover_motion, min_items=2)
_form("sonification", "a sonification (the values as pitch)", "sound", "pitch", ("sound",),
      {"series": 1, "distribution": 0.3, "records": 0.3, "timeline": 0.2},
      rule="one tone per value of up to 2 series for 30 s (120 tones); beyond that 0.3 plus the share",
      generator="sound.sonify", precision=0.15, interactivity=0.2, effort=0.35, complexity=3, cost_class="medium",
      fallback="table", cover=_cover_sonify, check=_check_sonify, min_items=2, secondary=True,
      second_for=("hear",))
_form("tone", "a short tone", "sound", "pitch", ("sound",),
      {"scalar": 1},
      rule="one value heard as a tone of that pitch (a series is a sonification)",
      generator="sound.tone", precision=0.4, interactivity=0.2, effort=0.15, complexity=2, cost_class="low",
      fallback="prose", base_bytes=44_144, check=_check_tone, secondary=True, second_for=("hear",))
_form("sound_view", "a waveform and spectrum view", "chart", "position", ("picture",),
      {"sound": 1, "series": 0.3},
      rule="the whole sound as a waveform envelope and its spectrum",
      generator="chart.line", precision=0.4, interactivity=0.2, effort=0.3, complexity=3, cost_class="medium",
      fallback="table", base_bytes=16_000)
_form("solid_view", "a 3D viewer", "spatial", "space", ("3d", "interactive"),
      {"solid": 1, "surface": 0.8, "map": 0.3},
      capacity={"samples": 2_000_000, "items": 2_000_000}, floor=0.5,
      rule="every vertex up to 2,000,000; beyond that simplified (0.5 plus the share)",
      generator="solid.view", precision=0.4, interactivity=0.6, effort=0.35, complexity=5, cost_class="medium",
      fallback="table", bytes_per_unit=50, base_bytes=60_000, check=_check_solid)
_form("surface_view", "a surface view", "spatial", "space", ("3d",),
      {"surface": 1, "solid": 0.5, "records": 0.3, "map": 0.4},
      capacity={"samples": 1_000_000, "values": 1_000_000, "items": 1_000_000}, floor=0.5,
      rule="every grid point up to 1,000,000; beyond that 0.5 plus the share",
      generator="surface.grid", precision=0.35, interactivity=0.6, effort=0.3, complexity=4, cost_class="medium",
      fallback="table", bytes_per_unit=30, base_bytes=8000, check=_check_solid)
_form("image", "a picture", "raster", "pixels", ("picture",),
      {"image": 1, "map": 0.6, "surface": 0.6, "solid": 0.5},
      rule="every pixel up to 16 million when the content has two spatial dimensions",
      generator="image.raster", precision=0.3, interactivity=0.0, effort=0.1, complexity=2, cost_class="medium",
      fallback="prose", bytes_per_unit=1.0, base_bytes=1000, cover=_cover_image, check=_check_raster)
_form("map", "a map", "map", "place", ("picture",),
      {"map": 1, "categories": 0.6, "records": 0.5, "relation": 0.3, "surface": 0.4},
      capacity={"items": 5000, "values": 5000}, floor=0.5,
      rule="every place up to 5,000 (values shown by shade or size) when the content has two spatial dimensions; "
           "beyond that clustered, 0.5 plus the share",
      generator="map.points", precision=0.4, interactivity=0.0, effort=0.2, complexity=3, cost_class="medium",
      fallback="table", bytes_per_unit=60, base_bytes=6000, cover=_cover_map, min_items=1)
_form("image_sequence", "an animated picture", "motion", "pixels", ("picture", "motion"),
      {"image": 1, "sequence": 0.9, "process": 0.4},
      rule="every frame up to 600 when the pictures come in order",
      generator="image.apng", precision=0.25, interactivity=0.2, effort=0.25, complexity=4, cost_class="high",
      fallback="prose", bytes_per_unit=20_000, base_bytes=1000, cover=_cover_frames, check=_check_animation,
      min_items=2)

# Purpose -> form -> how directly the form answers that purpose (clarity). Unlisted forms score DEFAULT_FIT.
DEFAULT_FIT = 0.15
PURPOSE_FIT = {
    "look_up": {"prose": 1.0, "spoken": 0.9, "table": 0.85, "document": 0.75, "data_file": 0.2, "line_chart": 0.35,
                "bar_chart": 0.45, "scatter_chart": 0.25, "histogram": 0.2, "heatmap": 0.3, "small_multiples": 0.25,
                "timeline": 0.5, "node_link": 0.35, "tree_diagram": 0.45, "flow_steps": 0.4, "step_through": 0.3,
                "explorable": 0.2, "animation": 0.1, "sonification": 0.1, "tone": 0.3, "sound_view": 0.3,
                "solid_view": 0.4, "surface_view": 0.3, "image": 0.5, "image_sequence": 0.3},
    "compare": {"bar_chart": 1.0, "table": 0.85, "small_multiples": 0.8, "line_chart": 0.7, "heatmap": 0.6, "map": 0.6,
                "prose": 0.55, "scatter_chart": 0.5, "spoken": 0.4, "histogram": 0.5, "explorable": 0.5,
                "document": 0.4, "timeline": 0.3, "node_link": 0.3, "tree_diagram": 0.3, "sound_view": 0.4,
                "solid_view": 0.3, "surface_view": 0.3, "image": 0.3, "animation": 0.3},
    "change_over_time": {"line_chart": 1.0, "small_multiples": 0.85, "timeline": 0.75, "animation": 0.6,
                         "bar_chart": 0.6, "heatmap": 0.5, "sonification": 0.35, "step_through": 0.5,
                         "image_sequence": 0.5, "table": 0.45, "prose": 0.4, "scatter_chart": 0.4, "spoken": 0.3,
                         "explorable": 0.4, "flow_steps": 0.3, "document": 0.3, "sound_view": 0.4,
                         "surface_view": 0.3},
    "distribution": {"histogram": 1.0, "small_multiples": 0.6, "bar_chart": 0.6, "heatmap": 0.5, "map": 0.6,
                     "scatter_chart": 0.4, "line_chart": 0.3, "table": 0.4, "prose": 0.45, "spoken": 0.35,
                     "sonification": 0.3},
    "relation": {"scatter_chart": 1.0, "node_link": 0.95, "heatmap": 0.75, "tree_diagram": 0.8, "line_chart": 0.5,
                 "table": 0.45, "prose": 0.4, "small_multiples": 0.5, "explorable": 0.6, "flow_steps": 0.3,
                 "bar_chart": 0.4, "spoken": 0.3, "document": 0.3},
    "follow_process": {"flow_steps": 1.0, "step_through": 0.95, "animation": 0.8, "prose": 0.8, "explorable": 0.6,
                       "timeline": 0.6, "document": 0.6, "spoken": 0.55, "tree_diagram": 0.4, "node_link": 0.4,
                       "table": 0.35, "line_chart": 0.2, "image_sequence": 0.5},
    "what_if": {"explorable": 1.0, "step_through": 0.6, "animation": 0.5, "line_chart": 0.5,
                "small_multiples": 0.5, "table": 0.4, "prose": 0.35, "scatter_chart": 0.4, "spoken": 0.25,
                "surface_view": 0.5, "solid_view": 0.3, "heatmap": 0.4},
    "locate": {"map": 1.0, "image": 0.9, "scatter_chart": 0.8, "surface_view": 0.5, "solid_view": 0.6, "table": 0.5,
               "prose": 0.6, "spoken": 0.5, "node_link": 0.3, "tree_diagram": 0.4, "document": 0.3, "heatmap": 0.5},
    "hear": {"tone": 1.0, "sonification": 0.95, "sound_view": 0.6, "spoken": 0.6, "prose": 0.3, "line_chart": 0.3,
             "table": 0.2},
    "keep": {"data_file": 1.0, "table": 0.7, "document": 0.7, "prose": 0.3, "spoken": 0.1},
    "learn": {"step_through": 0.9, "explorable": 0.85, "flow_steps": 0.85, "animation": 0.8, "node_link": 0.75,
              "tree_diagram": 0.75, "line_chart": 0.65, "document": 0.75, "prose": 0.7, "timeline": 0.7,
              "small_multiples": 0.6, "bar_chart": 0.6, "scatter_chart": 0.6, "histogram": 0.6, "heatmap": 0.5,
              "table": 0.5, "spoken": 0.6, "sonification": 0.4, "sound_view": 0.6, "tone": 0.5, "solid_view": 0.7,
              "surface_view": 0.6, "image": 0.6, "image_sequence": 0.6, "data_file": 0.1},
    "look_at": {"image": 1.0, "solid_view": 1.0, "surface_view": 1.0, "sound_view": 0.9, "image_sequence": 0.8, "map": 0.8,
                "heatmap": 0.6, "animation": 0.8, "scatter_chart": 0.4, "prose": 0.35, "spoken": 0.3, "table": 0.2,
                "line_chart": 0.3},
}
# How much each purpose needs exact values (precision) and manipulation (interactivity).
# "understand" asks for the same as "learn" (an explanation that can be followed); "decide" asks to weigh options:
# side by side, with exact values, and with the inputs to try where the content has them.
PURPOSE_FIT["understand"] = dict(PURPOSE_FIT["learn"])
PURPOSE_FIT["decide"] = {"explorable": 0.9, "table": 0.85, "bar_chart": 0.85, "small_multiples": 0.7,
                         "line_chart": 0.7, "prose": 0.75, "heatmap": 0.6, "scatter_chart": 0.5, "spoken": 0.5,
                         "document": 0.5, "step_through": 0.4, "map": 0.5, "data_file": 0.2}
PRECISION_NEED = {"decide": 0.6, "understand": 0.3, "look_up": 1.0, "keep": 1.0, "compare": 0.6, "locate": 0.5, "follow_process": 0.4,
                  "change_over_time": 0.3, "distribution": 0.3, "relation": 0.3, "learn": 0.3, "what_if": 0.3,
                  "look_at": 0.2, "hear": 0.1}
INTERACTION_NEED = {"what_if": 1.0, "follow_process": 0.4, "learn": 0.4, "understand": 0.4, "look_at": 0.3,
                    "decide": 0.4}


# ---------------------------------------------------------------------------------------------------------------
# Reading the request (no model): explicit forms, refusals, purposes, wishes


CHARTS = ("line_chart", "bar_chart", "scatter_chart", "histogram", "heatmap", "small_multiples")
DIAGRAMS = ("node_link", "tree_diagram", "flow_steps", "timeline")
SOUNDS = ("sonification", "tone", "sound_view")

# Alias -> forms (for Request.explicit_form and for the words). Families resolve to their best feasible member.
ALIASES = {
    "chart": CHARTS, "plot": CHARTS, "graph": CHARTS + ("node_link", "tree_diagram"), "diagram": DIAGRAMS,
    "picture": None, "visual": None, "table": ("table",), "spreadsheet": ("data_file", "table"),
    "file": ("data_file",), "csv": ("data_file",), "sound": SOUNDS, "audio": SOUNDS, "3d": ("solid_view",
                                                                                              "surface_view"),
    "map": ("map", "image", "scatter_chart"), "animation": ("animation", "image_sequence"), "video": ("animation",
                                                                                               "image_sequence"),
    "simulation": ("explorable",), "interactive": ("explorable", "step_through"), "steps": ("step_through",
                                                                                            "flow_steps"),
    "document": ("document",), "report": ("document",), "words": ("prose",), "text": ("prose",),
    "sentence": ("prose",), "spoken": ("spoken",), "speech": ("spoken",),
}

# Form nouns in the words, most specific first. Each pattern is matched on lower-cased text.
_NOUNS = (
    (r"line (?:chart|graph|plot)s?", ("line_chart",)),
    (r"(?:bar|column) (?:chart|graph|plot)s?", ("bar_chart",)),
    (r"scatter ?(?:plot|chart|graph|diagram)s?|scatterplots?", ("scatter_chart",)),
    (r"histograms?", ("histogram",)),
    (r"heat ?maps?", ("heatmap",)),
    (r"small multiples", ("small_multiples",)),
    (r"time ?lines?", ("timeline",)),
    (r"flow ?charts?|flow diagrams?|process diagrams?", ("flow_steps",)),
    (r"network (?:diagram|graph|map|view)s?|node[- ]link(?: diagram)?s?|knowledge graphs?|graph of (?:who|the "
     r"connections|the links|the relations)", ("node_link",)),
    (r"tree (?:diagram|view|chart)s?|hierarchy diagrams?|org(?:ani[sz]ation(?:al)?)? charts?", ("tree_diagram",)),
    (r"step[- ]by[- ]step (?:walk ?through|view|animation|demo)|walk ?throughs?|steppers?", ("step_through",)),
    (r"simulat(?:ion|or)s?|sliders?|explorables?|interactive (?:model|demo|simulation|chart|diagram|view)s?|"
     r"playgrounds?", ("explorable",)),
    (r"animat(?:ion|ed (?:chart|diagram|picture|gif|image))s?|movies?|videos?|gifs?", ("animation",
                                                                                         "image_sequence")),
    (r"sonifi(?:cation|ed)", ("sonification",)),
    (r"tones?|beeps?", ("tone",)),
    (r"waveforms?|spectr(?:um|ogram)s?", ("sound_view",)),
    (r"sound|audio(?: clip)?", SOUNDS),
    (r"3d(?: (?:model|view|viewer|object|render|print))?s?|three[- ]dimensional (?:model|view)s?", ("solid_view",
                                                                                                    "surface_view")),
    (r"surface (?:plot|view)s?", ("surface_view",)),
    (r"maps?", ("map", "image", "scatter_chart")),
    (r"(?:pictures?|images?|visuals?|visuali[sz]ations?|graphics?|infographics?|illustrations?|drawings?)", None),
    (r"diagrams?", DIAGRAMS),
    (r"charts?|plots?", CHARTS),
    (r"graphs?", CHARTS + ("node_link", "tree_diagram")),
    (r"tables?|tabular form", ("table",)),
    (r"spreadsheets?|csv(?: file)?s?|tsv|data files?|excel(?: file)?", ("data_file",)),
    (r"(?:a |the )?file", ("data_file",)),
    (r"reports?|documents?|write-?ups?", ("document",)),
    (r"(?:one|a|single|two|few) (?:sentence|line)s?|plain words|words|prose|plain text|paragraphs?", ("prose",)),
)
_NOUN_RE = [(re.compile(r"\b(?:" + pattern + r")\b"), forms) for pattern, forms in _NOUNS]
# A noun names the output when a production cue comes just before it. Prepositions need an indefinite article or
# none ("as a table", "in a chart"; not "in the table", which names content). Verbs take any determiner.
_DETERMINER = r"(?:the|this|that|these|those|its|his|her|their|my|your|our|his)"
# Free words between a cue and a noun are adjectives ("as a simple table"), never a determiner or possessive: "to its
# file" or "in their table" names content.
_FREE = r"(?:(?!" + _DETERMINER + r"\s)[a-z0-9-]+\s+){0,2}"
_PREP_CUE = re.compile(r"\b(?:as|in|into|with|via|using)\s+(?:an?\s+|one\s+|some\s+)?" + _FREE + "$")
_VERB_CUE = re.compile(r"\b(?:make|draw|plot|show|give|create|generate|build|render|want|need|produce|export|put|"
                       r"turn|convert|prefer|sketch|use|get|send|save|do|try|prepare|display|sonify|play|write|"
                       r"see|like|would like|rather have)\s+(?:(?:me|us|it|this|that|them|these|those)\s+)?"
                       r"(?:(?:an?|the|one|some|your|my)\s+)?(?:[a-z0-9-]+\s+){0,2}$")
_DEFINITE_BEFORE = re.compile(r"\b(?:in|on|from|of|at|inside|within)\s+(?:the|this|that|my|your|our|these|those)\s+"
                              r"(?:[a-z0-9-]+\s+){0,2}$")
_FORM_VERBS = (
    (r"plot|chart|graph", CHARTS),
    (r"tabulate|tabulise|tabularise", ("table",)),
    (r"map out", DIAGRAMS),
    (r"map", ("map", "image", "scatter_chart")),
    (r"diagram", DIAGRAMS),
    (r"animate", ("animation", "image_sequence")),
    (r"sonify", ("sonification",)),
    (r"visuali[sz]e|illustrate|draw|sketch", None),
    (r"simulate", ("explorable",)),
    (r"export|download", ("data_file",)),
    (r"read (?:it|this|that|them|the \w+) (?:out|aloud)|read (?:out|aloud)|say it|tell me out loud", ("spoken",)),
)
_CLAUSE_START = r"(?:^|[.;:!?,]\s*|\b(?:please|can you|could you|would you|will you|and|then|now|also|just)\s+)"
_VERB_RE = [(re.compile(_CLAUSE_START + r"(?:" + pattern + r")\b"), forms) for pattern, forms in _FORM_VERBS]
_ALOUD = re.compile(r"\b(?:out loud|aloud)\b")
_INDEFINITE_BEFORE = re.compile(r"\b(?:an?|some)\s+(?:[a-z0-9-]+\s+){0,2}$")
_NEGATION = re.compile(r"\b(?:no|not|without|don't|do not|never|skip|avoid|rather than|instead of|nothing like)\s+"
                       r"(?:[a-z0-9'-]+\s+){0,3}$")

_SIMPLE = re.compile(r"\b(?:just (?:tell|say|give)|briefly|in brief|in (?:a|one) (?:word|sentence|line)|"
                     r"short answer|quick(?:ly)? (?:answer|question)|tl;?dr|keep it (?:short|simple)|simply put|"
                     r"the gist|bottom line|in short|one-liner|in a nutshell)\b")
_PRECISE = re.compile(r"\b(?:exact(?:ly)?|precise(?:ly)?|to the (?:cent|digit|decimal)|the (?:actual|exact) "
                      r"(?:figures|numbers|values)|decimal places)\b")
_RICH = re.compile(r"\b(?:in detail|detailed|show me|let me (?:see|explore|play|try)|walk me through|explore|"
                   r"everything|all (?:the|of the) (?:values|data|numbers|points|rows)|in full|full picture|"
                   r"deep dive|visuali[sz]e|interactive)\b")

PURPOSE_CUES = {
    "look_up": r"\b(?:what (?:is|was|are|were)|how (?:many|much|tall|long|big|old|far|high|deep)|who|"
               r"when (?:did|was|is|will)|value of|look up|find|which one)\b",
    "compare": r"\b(?:compar\w*|versus|vs\.?|differ\w*|bigger|smaller|larger|higher|lower|better|worse|rank\w*|"
               r"more than|less than|against each other|side by side)\b",
    "change_over_time": r"\b(?:chang\w*|trend\w*|over time|over the (?:years?|months?|weeks?|days?|decades?)|grow\w*|"
                        r"grew|ris(?:e|en|ing)|rose|f[ae]ll(?:ing)?|declin\w*|increas\w*|decreas\w*|evolv\w*|since|"
                        r"history|per (?:day|week|month|year)|daily|weekly|monthly|yearly|annual\w*|each year)\b",
    "distribution": r"\b(?:distribut\w*|spread|how often|frequenc\w*|typical|outliers?|range of|variab\w*|"
                    r"vary|skew\w*)\b",
    "relation": r"\b(?:relat\w*|correlat\w*|depend\w*|connect\w*|links?|linked|associat\w*|influenc\w*|network|"
                r"who knows|structure|organi[sz]ed|hierarch\w*|parts? of)\b",
    "follow_process": r"\b(?:how (?:does|do|did|is) .{0,40}\b(?:work|happen|made|done)|steps?|process|procedure|"
                      r"how (?:do|can|should) (?:i|we)|algorithm|stages?|in order|walk me through|step by step|"
                      r"instructions?|recipe)\b",
    "what_if": r"\b(?:what if|what happens (?:if|when)|suppose|imagine if|if (?:i|we) (?:change|increase|decrease|"
               r"double|halve|raise|lower)|try (?:different|other)|scenarios?|sensitiv\w*|play with)\b",
    "locate": r"\b(?:where|locat\w*|positions?|nearest|how far|coordinates?)\b",
    "hear": r"\b(?:hear|listen|sounds? like|audible|pitch)\b",
    "keep": r"\b(?:export|download|save|keep a copy|csv|spreadsheet|excel|reuse|import (?:it )?into|for my records)\b",
    "learn": r"\b(?:explain\w*|teach|learn|why|intuition|what does .{0,40} mean)\b",
    "understand": r"\b(?:understand\w*|make sense of|get my head around|grasp)\b",
    "decide": r"\b(?:decide|decision|choose|should (?:i|we)|which (?:option|one) should|worth it|go with)\b",
    "look_at": r"\b(?:looks? like|see (?:it|the|what)|view|shape of|appearance)\b",
}
_PURPOSE_RE = {purpose: re.compile(pattern) for purpose, pattern in PURPOSE_CUES.items()}
WORD_WEIGHT = 0.5           # a cue word's weight for its purpose (weak) ...
# ... except cues that name a sense or an outcome rather than a topic: hearing, keeping a copy, varying inputs.
WORD_WEIGHTS = {"hear": 1.5, "keep": 1.0, "what_if": 0.75}
GENERIC_PURPOSE_WEIGHT = 0.5   # a scalar, records or text say little about purpose: their look-up counts half
DECLARED_WEIGHT = 1.5       # Request.purpose (the caller knows it)
CONTENT_WEIGHT = 1.0        # the content's own purposes (strong), shared among its kinds


@dataclass(frozen=True)
class Reading:
    """What the words say, as cue names (never the request's text)."""

    explicit: tuple[str, ...] | None
    explicit_cue: str | None
    refused: tuple[str, ...]
    purposes: Mapping[str, float]
    simple: bool
    rich: bool
    evidence: tuple[str, ...]
    precise: bool = False
    selective: str | None = None     # the question asks for a few values picked out of the content (its class)


def _explicit_set(name: str) -> tuple[str, ...] | None:
    key = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    if key in FORMS:
        return (key,)
    if key.replace("_", "") in ALIASES:
        key = key.replace("_", "")
    if key in ALIASES:
        forms = ALIASES[key]
        return _picture_forms() if forms is None else tuple(forms)
    return None


def _picture_forms() -> tuple[str, ...]:
    return tuple(name for name, form in FORMS.items() if "picture" in form.media or "3d" in form.media)


def read_request(request: Request) -> Reading:
    """Read the words without a model. Cue words are weak evidence; the content decides most of the plan."""
    low = " ".join(request.text.lower().split())
    evidence = []
    explicit, explicit_cue = None, None
    refused: list[str] = []
    taken: list[tuple[int, int]] = []
    if request.explicit_form:
        explicit, explicit_cue = _explicit_set(request.explicit_form), "named by the caller"
    for pattern, forms in _NOUN_RE:
        for match in pattern.finditer(low):
            span = match.span()
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            before = low[max(0, span[0] - 60):span[0]]
            forms_ = _picture_forms() if forms is None else forms
            if _NEGATION.search(before):
                taken.append(span)
                generic = forms is None or set(forms_) >= set(CHARTS)
                refused.extend(_picture_forms() if generic else forms_)
                evidence.append(f"refused in words: {match.group(0)}")
                continue
            if _DEFINITE_BEFORE.search(before) or low[span[1]:span[1] + 12].startswith(" of contents"):
                taken.append(span)
                continue        # names content ("in the table"), not an output
            if forms is None and not _INDEFINITE_BEFORE.search(before):
                taken.append(span)
                continue        # "the full picture", "the big picture": a general word for a picture names one
                #                 only with "a" ("draw me a picture")
            if _PREP_CUE.search(before) or _VERB_CUE.search(before):
                taken.append(span)
                if explicit is None:
                    explicit, explicit_cue = tuple(forms_), match.group(0)
    if explicit is None:
        for pattern, forms in _VERB_RE:
            match = pattern.search(low)
            if match:
                explicit = _picture_forms() if forms is None else tuple(forms)
                explicit_cue = match.group(0).strip(" .;:!?,")
                break
    if explicit is None and _ALOUD.search(low):
        explicit, explicit_cue = ("spoken",), "aloud"
    if explicit is not None:
        evidence.append(f"form named: {explicit_cue}")
    refused = [f for f in dict.fromkeys(refused) if not explicit or f not in explicit]
    purposes: dict[str, float] = {}
    for purpose, pattern in _PURPOSE_RE.items():
        match = pattern.search(low)
        if match:
            purposes[purpose] = WORD_WEIGHTS.get(purpose, WORD_WEIGHT)
            evidence.append(f"purpose from words: {purpose} ({match.group(0)[:30]})")
    if request.purpose:
        purposes[request.purpose] = DECLARED_WEIGHT
        evidence.append(f"purpose given: {request.purpose}")
    simple, rich = bool(_SIMPLE.search(low)), bool(_RICH.search(low))
    if simple:
        evidence.append("asked for a short answer")
    if rich:
        evidence.append("asked to be shown in full")
    precise = bool(_PRECISE.search(low))
    if precise:
        evidence.append("asked for exact values")
    selective = answer_class(low)
    if selective:
        evidence.append(f"the question picks out {SELECTIVE_PHRASE[selective]}")
    return Reading(explicit, explicit_cue, tuple(refused), MappingProxyType(purposes), simple and not rich,
                   rich and not simple, tuple(evidence), precise, selective)


# The size of the answer, read from the question's grammar (not from topic words): a yes-or-no question, an extreme
# ("the warmest", "the most"), a count ("how many"), one time, person or place ("when did", "who won", "where is"),
# one measure ("how long is"), a typical value. Such a question is answered by a few values picked out of the
# content, however large the content is, unless the words ask for the whole ("each day", "over time", "show me").
_WHOLE = re.compile(r"\b(?:each|every|all (?:the|of)|throughout|over time|over the (?:years?|months?|weeks?|days?|"
                    r"course)|across|trends?|patterns?|course of|day by day|month by month|year by year|show me|"
                    r"walk me through|explain|list (?:all|every|the)|overview|full picture|in detail|"
                    r"how (?:did|does|do|has|have) .{0,40}\b(?:change|go|move|develop|evolve|vary))\b")
_POLAR = re.compile(r"(?:^|[.!?;]\s+)(?:so,?\s+|and\s+|but\s+)?(?!(?:can|could|will|would) you\b)"
                    r"(?:is|are|was|were|do|does|did|has|have|had|can|could|will|would|should|any)\b")
_NOT_SUPERLATIVE = frozenset("interest test rest west forest honest request contest suggest invest harvest protest "
                             "digest chest guest nest modest manifest arrest quest conquest priest crest vest pest zest "
                             "attest detest inquest unrest midwest southwest northwest northeast southeast earnest "
                             "biggest".split()) - {"biggest"}
_SUPERLATIVE = re.compile(r"\b(?:most|least|top|peak|maximum|minimum|best|worst|first|last time|[a-z]{3,}est)\b")
_COUNT = re.compile(r"\bhow (?:many|much)\b")
_ONE_WH = re.compile(r"(?:^|[.!?;,]\s*|\b(?:tell me|show me|know|find out|say|ask)\s+)(?:when|what time|what date|"
                     r"who|whose|where (?:is|was|did|does))\b")
_ONE_MEASURE = re.compile(r"\bhow (?:long|loud|high|tall|big|heavy|far|old|deep|wide|fast|large|small|warm|cold|hot|"
                          r"strong|bright|sharp) (?:is|was|did|does)\b")
_TYPICAL = re.compile(r"\b(?:typical|average|median|usual)\b")
SELECTIVE_PHRASE = {"yes_or_no": "a yes or a no", "extreme": "the largest or smallest", "count": "a count",
                    "one_of": "one time, person or place", "measure": "one measurement", "typical": "a typical value"}


def answer_class(low: str) -> str | None:
    """The class of a question whose answer is a few values picked out of the content, or None."""
    if not low or _WHOLE.search(low):
        return None
    if _POLAR.search(low):
        return "yes_or_no"
    if any(m.group(0) not in _NOT_SUPERLATIVE for m in _SUPERLATIVE.finditer(low)):
        return "extreme"
    if _COUNT.search(low):
        return "count"
    if _ONE_MEASURE.search(low):
        return "measure"
    if _ONE_WH.search(low):
        return "one_of"
    if _TYPICAL.search(low):
        return "typical"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Content: merging shapes, its purposes, which purposes it can serve


def merge(content: tuple[ContentShape, ...]) -> ContentShape | None:
    """Several pieces of content as one shape to plan for: kinds united, items the largest, series added."""
    content = tuple(content)
    if not content:
        return None
    if len(content) == 1:
        return content[0]
    kinds = tuple(dict.fromkeys(k for s in content for k in s.kinds))
    return ContentShape(kinds, max(s.n_items for s in content), sum(max(1, s.n_series) for s in content),
                        any(s.has_time for s in content), max(s.has_space for s in content),
                        any(s.has_parameters for s in content), max(s.duration_s for s in content),
                        sum(s.text_chars for s in content))


def primary_kind(shape: ContentShape | None) -> str:
    if shape is None or not shape.kinds:
        return "prose"
    return next(k for k in KIND_PRIORITY if k in shape.kinds)


def _content_purposes(shape: ContentShape | None, n_pieces: int) -> dict:
    if shape is None or not shape.kinds:
        return {"look_up": GENERIC_PURPOSE_WEIGHT}
    found: dict[str, float] = {}
    kinds = _specific_kinds(shape)
    for kind in kinds:
        if kind == "process":
            options = ("what_if",) if shape.has_parameters else ("follow_process", "look_at") if (
                shape.has_time and shape.has_space) else ("follow_process",)
        elif kind == "sound":
            options = ("hear", "look_at")
        elif kind in ("solid", "surface", "image"):
            options = ("look_at",)
        elif kind == "map":
            options = ("locate",)
        elif kind in ("network", "relation", "hierarchy"):
            options = ("relation",)
        elif kind == "timeline" or (kind == "sequence" and shape.has_time):
            options = ("change_over_time",)
        elif kind == "sequence":
            options = ("follow_process",)
        elif kind == "series":
            options = ("change_over_time",)
        elif kind == "distribution":
            options = ("distribution",)
        elif kind == "categories":
            options = ("compare",)
        elif kind == "code":
            options = ("learn",)    # code is read to see how it works
        else:   # scalar, records, prose
            options = ("look_up",)
        weight = GENERIC_PURPOSE_WEIGHT if kind in ("scalar", "records", "prose") else CONTENT_WEIGHT
        for purpose in options:
            found[purpose] = found.get(purpose, 0.0) + weight / len(kinds) / len(options)
    if n_pieces >= 2 and any(k in VALUE_KINDS for k in kinds):
        found["compare"] = found.get("compare", 0.0) + CONTENT_WEIGHT / 2
    return found


def serves(purpose: str, shape: ContentShape | None, n_pieces: int = 1) -> bool:
    """Whether the content has what the purpose needs (a change needs an order, a relation two measures ...). No rule
    here counts items: a purpose that switched on as content grew could move the choice back to words (content of
    one or two values is words anyway)."""
    if purpose in ("look_up", "learn", "understand", "decide"):
        return True
    if shape is None or not shape.kinds:
        return False
    kinds = set(shape.kinds)
    if purpose == "compare":
        return bool(kinds & {"categories", "relation"}) or shape.n_series >= 2 or n_pieces >= 2 or \
            bool(kinds & {"records", "series"})
    if purpose == "change_over_time":
        return bool(kinds & {"series", "timeline", "sequence", "process"} or shape.has_time)
    if purpose == "distribution":
        return "distribution" in kinds or bool(kinds & {"series", "records", "categories"})
    if purpose == "relation":
        return bool(kinds & {"relation", "network", "hierarchy"}) or shape.n_series >= 2
    if purpose == "follow_process":
        return bool(kinds & {"sequence", "process", "timeline", "code"})
    if purpose == "what_if":
        return shape.has_parameters
    if purpose == "locate":
        return "map" in kinds or shape.has_space >= 2
    if purpose == "hear":
        return bool(kinds & {"sound", "scalar", "series"})
    if purpose == "keep":
        return bool(kinds - {"prose", "scalar"})
    if purpose == "look_at":
        return bool(kinds & {"image", "solid", "surface", "map", "sound"}) or shape.has_space > 0
    return False


def _sentence_worth(shape: ContentShape | None) -> bool:
    """A scalar or a sentence's worth: at most SMALL_ITEMS items and SMALL_VALUES values, or a short text. Never a
    picture, a sound, a solid or a surface (words cannot show them), nor a process (states that change are seen
    changing), whatever their counts."""
    if shape is None or not shape.kinds:
        return True
    kinds = set(_specific_kinds(shape))
    if kinds & {"image", "sound", "solid", "surface", "process"}:
        return False
    if kinds <= {"prose"}:
        return shape.text_chars <= SMALL_CHARS
    if "scalar" in kinds and kinds <= {"scalar", "prose"}:
        return shape.text_chars <= SMALL_CHARS
    return shape.n_items <= SMALL_ITEMS and _values(shape) <= SMALL_VALUES and shape.text_chars <= SMALL_CHARS


# ---------------------------------------------------------------------------------------------------------------
# Context: the display, feasibility


def resolve_display(context: Context) -> Display:
    shows = set(context.shows if context.shows is not None else DEVICE_SHOWS[context.device])
    if "voice_only" in context.accessibility:
        shows &= {"speech", "sound"}
    if "no_sound" in context.accessibility:     # the person cannot or will not listen: nothing heard, no speech
        shows -= {"speech", "sound"}
    width, rows, cols = _DEVICE_SIZE[context.device]
    return Display(context.device, frozenset(shows), int(context.width_px or width), rows, cols)


def available_generators(context: Context) -> frozenset:
    """The generator names usable here: the context's list, else `make.catalog()` read now (available rows only;
    a row counts by its generator name and by its form, with dots and underscores both accepted)."""
    if context.generators is not None:
        return frozenset(context.generators)
    from . import make
    names = set()
    for row in make.catalog():
        if row.get("available"):
            for value in (row.get("generator"), row.get("form")):
                if isinstance(value, str) and value:
                    names.update({value, value.replace("_", "."), value.replace(".", "_")})
    return frozenset(names)


def feasible_here(form: Form, generators: frozenset) -> bool:
    if form.generator is None:
        return True
    return form.generator in generators or form.generator.replace(".", "_") in generators


# ---------------------------------------------------------------------------------------------------------------
# Pre-production check ("imagination", directive §9): cheap arithmetic before anything is made


def foresee(form: str | Form, shape: ContentShape, context: Context | None = None) -> Foresight:
    """Predicted problems and estimated cost of making `form` for `shape` on this context's device. Every number
    is an estimate from counts (labelled so); nothing is made."""
    form = FORMS[form] if isinstance(form, str) else form
    context = context or Context()
    display = resolve_display(context)
    problems = list(form.check(shape, display)) if form.check else []
    units = _values(shape) if any(k in VALUE_KINDS for k in shape.kinds) else max(1, shape.n_items)
    if form.name in ("sonification",):
        tones = min(shape.n_items, int(ATTENTION_S * 4)) * min(max(1, shape.n_series), SOUND_VOICES)
        seconds_long = tones * 0.25
        size = 44 + seconds_long * 22_050 * 2
    elif form.name in ("tone",):
        seconds_long = min(_values(shape), 12) * 0.5
        size = 44 + seconds_long * 22_050 * 2
    elif form.name == "image" or form.name == "image_sequence":
        frames = max(1, shape.n_items) if form.name == "image_sequence" else 1
        pixels = min(shape.n_items, (2 * display.width_px) ** 2) if form.name == "image" else 0
        size = form.base_bytes + (pixels if pixels else frames * form.bytes_per_unit)
        seconds_long = 0.0
    else:
        shown = units
        if form.name == "line_chart":
            shown = min(max(1, shape.n_items), 2 * display.plot_px) * min(max(1, shape.n_series), 3 * LINE_STYLES)
        elif form.name in ("sound_view",):
            shown = 2 * display.plot_px
        size = form.base_bytes + form.bytes_per_unit * shown
        seconds_long = 0.0
    seconds = _COST_SECONDS[form.cost_class] + size / _BYTES_PER_SECOND
    estimates = {"seconds": round(seconds, 4), "bytes": float(round(size)), "units": float(units)}
    if form.name in ("sonification", "tone"):
        estimates["duration_s"] = round(seconds_long, 2)
    if form.name in ("line_chart", "scatter_chart"):
        estimates["points_per_px"] = round(max(1, shape.n_items) / display.plot_px, 3)
    if form.name == "table" and display.screen_rows:
        estimates["screens"] = float(math.ceil(max(1, shape.n_items) / display.screen_rows))
    return Foresight(tuple(problems), MappingProxyType(estimates))


# ---------------------------------------------------------------------------------------------------------------
# Criteria (each into [0, 1], by the rule in its docstring)


def _clarity(forms: tuple[str, ...], purposes: Mapping[str, float]) -> float:
    """The purpose mix's weighted fit (PURPOSE_FIT); a combination serves each purpose with its better form."""
    total = sum(purposes.values()) or 1.0
    score = 0.0
    for purpose, weight in purposes.items():
        table = PURPOSE_FIT[purpose]
        score += weight / total * max(table.get(f, DEFAULT_FIT) for f in forms)
    return score


def _precision(forms, need, carried=None) -> float:
    """1 - need x (1 - the form's precision). In a combination, values are read off the second form where it
    carries them and off the first elsewhere: precision = p2 x carried2 + p1 x (1 - carried2) when p2 > p1."""
    first = FORMS[forms[0]].precision
    best = first
    if len(forms) == 2:
        second = FORMS[forms[1]].precision
        share = 1.0 if carried is None else carried
        best = max(first, second * share + first * (1.0 - share))
    return 1.0 - need * (1.0 - best)


def _interactivity(forms, need) -> float:
    """1 - shortfall - a quarter of the surplus: a static form for a what-if falls short; sliders for a lookup
    are a little in the way."""
    level = FORMS[forms[0]].interactivity
    return max(0.0, 1.0 - max(0.0, need - level) - 0.25 * max(0.0, level - need))


def _effort_of(form: Form, shape: ContentShape | None, display: Display) -> float:
    """Base effort plus reading time: a table's pages, a document's length, prose's length, a sound's duration. A
    file costs a person the trouble of opening it elsewhere; it costs an agent (a program) nothing."""
    effort = form.effort
    if form.name == "data_file" and display.device == "agent":
        effort = 0.0
    if shape is None:
        return effort
    if form.name == "table" and display.screen_rows:
        pages = math.ceil(_rows(shape) / display.screen_rows) * math.ceil(
            max(1, shape.n_series) / max(1, display.screen_cols))
        effort += 0.5 * min(1.0, pages / 20)
    elif form.name == "document":
        effort += 0.4 * min(1.0, shape.text_chars / 30_000)
    elif form.name in ("prose", "spoken"):
        cap = form.capacity.get("chars", 3000)
        effort += 0.4 * min(1.0, min(shape.text_chars, cap) / 3000)
        # Every value or step said in words is one more to hold in mind (a picture shows them at once).
        kinds = _specific_kinds(shape)
        said = 0.0
        if any(UNIT_OF_KIND[k] in ("values", "dist") and k != "scalar" for k in kinds):
            said = max(said, min(_values(shape), form.capacity["values"]) / form.capacity["values"])
        effort += WORDS_EFFORT_PER_FULL * said
        if any(UNIT_OF_KIND[k] in ("items", "samples") for k in kinds):
            # steps are followed one after another in words as in a diagram: a smaller load per step
            effort += WORDS_EFFORT_PER_STEPS * min(max(1, shape.n_items), form.capacity["items"]) / \
                form.capacity["items"]
    elif form.name == "node_link":
        effort += 0.3 * min(1.0, shape.n_items / 150)
    elif form.name == "sonification":
        effort += 0.3 * min(1.0, shape.n_items * 0.25 / ATTENTION_S)
    return min(1.0, effort)


def _accessibility(forms, needs, shape=None, display=None) -> float:
    """1 without stated needs. With a screen reader: words 1; a table 1 within one screen of rows, 0.3 beyond (it is
    read cell by cell); a picture 0.3 alone (it is only admitted with its text), sound 0.6, interactive 0.5. With
    reduced motion: moving forms 0 (they are removed anyway). A combination takes its better part."""
    if not needs:
        return 1.0
    best = 0.0
    for name in forms:
        form = FORMS[name]
        score = 1.0
        if "screen_reader" in needs:
            if name in ("table", "data_file") and shape is not None and display is not None and \
                    _rows(shape) > max(display.screen_rows, 1):
                score = min(score, 0.3)
            elif form.text_like or "speech" in form.media:
                score = min(score, 1.0)
            elif "interactive" in form.media:
                score = min(score, 0.5)
            elif "sound" in form.media:
                score = min(score, 0.6)
            else:
                score = min(score, 0.3)
        if "reduced_motion" in needs and "motion" in form.media:
            score = 0.0
        best = max(best, score)
    return best


def _cost(seconds, size, context) -> float:
    """1 - half the share of the time budget used - half the share of the byte budget used."""
    return max(0.0, 1.0 - 0.5 * min(1.0, seconds / context.budget_s) - 0.5 * min(1.0, size / context.budget_bytes))


# ---------------------------------------------------------------------------------------------------------------
# The plan


def _weights(adjustments, kind, purpose, device) -> dict:
    weights = dict(WEIGHTS)
    if adjustments is None:
        return weights
    if len(adjustments) > MAX_ADJUSTMENTS:
        raise Malformed(f"an adjustments table holds at most {MAX_ADJUSTMENTS} entries")
    shift = dict.fromkeys(CRITERIA, 0.0)
    for key, table in adjustments.items():
        if not (isinstance(key, tuple) and len(key) == 3):
            raise Malformed("an adjustment's key is (content kind, purpose, device)")
        k, p, d = key
        if (k != "*" and k not in SHAPE_KINDS) or (p != "*" and p not in PURPOSES) or (d != "*" and d not in DEVICES):
            raise Malformed(f"unknown adjustment key {key!r}")
        for criterion, value in dict(table).items():
            if criterion not in CRITERIA or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise Malformed(f"unknown or non-numeric adjustment {criterion!r}")
            if k in ("*", kind) and p in ("*", purpose) and d in ("*", device):
                shift[criterion] += float(value)
    for criterion in CRITERIA:
        if criterion == "feasibility":
            continue        # a hard constraint is not a weight to learn
        weights[criterion] = max(0.0, weights[criterion] + max(-MAX_ADJUST, min(MAX_ADJUST, shift[criterion])))
    total = sum(weights.values()) or 1.0
    return {c: weights[c] / total for c in CRITERIA}


def _preference(form_names, context) -> float:
    value = 0.0
    for name in form_names:
        form = FORMS[name]
        for key in (form.name, form.family, *form.media):
            if key in context.preferences:
                value = context.preferences[key]
                break
    return value


@dataclass
class _Run:
    """One scoring run (content alone; content and words; everything)."""

    chosen: tuple[str, ...]
    candidates: list
    explicit_ok: bool | None
    rule: str           # which rule picked the choice: "explicit", "nearest", "small", "best", "simplest", "words"


def _score_run(shape, n_pieces, reading: Reading, context: Context, display: Display, generators, purposes,
               weights, use_words: bool, relaxed: bool) -> _Run:
    if not relaxed and display.device == "agent" and shape is not None and serves("keep", shape, n_pieces):
        # an agent is a program: it keeps and reads data, so a copy it can parse is one of its purposes
        purposes = dict(purposes)
        purposes["keep"] = purposes.get("keep", 0.0) + AGENT_KEEP
        total_p = sum(purposes.values())
        purposes = {p: w / total_p for p, w in purposes.items()}
    need_precision = sum(PRECISION_NEED[p] * w for p, w in purposes.items()) / (sum(purposes.values()) or 1)
    if use_words and reading.precise:
        need_precision = max(need_precision, PRECISE_NEED)
    need_interaction = sum(INTERACTION_NEED.get(p, 0.0) * w for p, w in purposes.items()) / (
        sum(purposes.values()) or 1)
    needs = () if relaxed else context.accessibility
    if needs:   # with stated needs, how well a form serves them counts three times as much
        weights = dict(weights)
        weights["accessibility"] *= ACCESS_FACTOR
        total_w = sum(weights.values())
        weights = {c: w / total_w for c, w in weights.items()}
    refused = set(reading.refused) if use_words else set()
    explicit = reading.explicit if use_words else None
    words_form = "prose" if "text" in display.shows else "spoken"
    shape_ = shape or ContentShape((), 0, 0, False, 0, False, 0.0, 0)
    singles: dict[str, Candidate] = {}
    carried_kinds: dict[str, dict] = {}
    for name, form in FORMS.items():
        per = form.carried_kinds(shape_, display) if shape is not None else {}
        carried_kinds[name] = per
        carried = sum(per.values()) / len(per) if per else (1.0 if form.text_like and name != "data_file"
                                                             else 0.0)
        seen = foresee(form, shape_, Context(device=display.device, width_px=display.width_px))
        excluded, budget_fit, est = None, False, dict(seen.estimates)
        missing = [m for m in form.media if m not in display.shows]
        if missing:
            excluded = _why_media(form, missing, context, relaxed)
        elif not relaxed and "reduced_motion" in needs and "motion" in form.media:
            excluded = "moving pictures are off for reduced motion"
        elif not relaxed and not feasible_here(form, generators):
            excluded = f"nothing here can make {form.label} yet"
        elif carried <= 0.0 and not (explicit and name in explicit and _sentence_worth(shape) and
                                     any(k in form.kinds for k in VALUE_KINDS)):
            # a chart of one or a few values, named by the person, is their call ("one bar")
            excluded = f"{form.label} cannot carry this content"
        elif name in refused:
            excluded = f"you said you did not want {form.label}"
        elif not relaxed and _preference((name,), context) <= -1.0:
            excluded = f"you said you never want {form.label}"
        elif form.second_for and not any(p in purposes for p in form.second_for) and \
                not (name == "data_file" and display.device == "agent") and not (
                explicit and name in explicit):
            excluded = f"{form.label} is made only when it is asked for"
        elif not relaxed and (seen.estimates["seconds"] > context.budget_s or
                              seen.estimates["bytes"] > context.budget_bytes):
            fit = _units_within_budget(form, context)
            if seen.estimates["seconds"] <= context.budget_s and fit is not None:
                # a chart or a table is made smaller to fit (fewer points, fewer rows), keeping what DOWNSAMPLED says
                shown = max(1.0, (seen.estimates["bytes"] - form.base_bytes) / form.bytes_per_unit)
                keep = DOWNSAMPLED[name]
                carried *= keep + (1.0 - keep) * min(1.0, fit / shown)
                budget_fit = True
                est["bytes"] = float(min(seen.estimates["bytes"], int(form.base_bytes + fit * form.bytes_per_unit)))
            else:
                excluded = f"{form.label} would take more than the budget allows"
        scores = {
            "clarity": _clarity((name,), purposes),
            "carried": carried,
            "precision": _precision((name,), need_precision),
            "interactivity": _interactivity((name,), need_interaction),
            "effort": 1.0 - _effort_of(form, shape, display),
            "feasibility": 1.0 if relaxed or feasible_here(form, generators) else 0.0,
            "accessibility": _accessibility((name,), needs, shape_, display),
            "cost": _cost(seen.estimates["seconds"], seen.estimates["bytes"], context if not relaxed else
                          Context(budget_s=1e9, budget_bytes=1 << 60)),
        }
        fixes = tuple(p.fix for p in seen.problems if p.fix) + (("downsample to fit the budget",) if budget_fit else ())
        singles[name] = Candidate((name,), {c: round(min(1.0, max(0.0, v)), 4) for c, v in scores.items()}, 0.0, excluded,
                                  fixes,
                                  tuple(p.text for p in seen.problems), MappingProxyType(est))
    # The pre-production check rejects a form when the form it would switch to is admitted here.
    for name, cand in list(singles.items()):
        if cand.excluded:
            continue
        seen = foresee(FORMS[name], shape_, Context(device=display.device, width_px=display.width_px))
        for problem in seen.problems:
            if problem.severity == "reject" and problem.fix and problem.fix.startswith("switch:"):
                target = problem.fix.split(":", 1)[1]
                if target in singles and singles[target].excluded is None:
                    singles[name] = _replace(cand, excluded=f"{problem.text}; {FORMS[target].label} fits better")
                    break
    # A screen reader needs the text that goes with a picture: pictures are admitted only with their fallback.
    if "screen_reader" in needs:
        for name, cand in list(singles.items()):
            form = FORMS[name]
            if cand.excluded is None and not form.text_like and "speech" not in form.media and \
                    "sound" not in form.media:
                singles[name] = _replace(cand, excluded="a screen reader needs the text that goes with "
                                                        f"{form.label}")
    # Totals of singles.
    pref_on = not relaxed
    for name, cand in singles.items():
        singles[name] = _replace(cand, total=_total(cand.scores, weights, (name,), context if pref_on else None))
    # Combinations of a non-text form and a secondary (text-like or sound) form.
    combos: list[Candidate] = []
    for name, cand in singles.items():
        form = FORMS[name]
        if form.text_like:
            continue
        seconds = [s for s in FORMS if FORMS[s].secondary and s != name and FORMS[s].family != form.family]
        for second in seconds:
            if FORMS[second].second_for and not any(p in purposes for p in FORMS[second].second_for):
                continue
            other = singles[second]
            pair = (name, second)
            per_p, per_s = carried_kinds[name], carried_kinds[second]
            kinds = set(per_p) | set(per_s)
            carried = sum(max(per_p.get(k, 0.0), per_s.get(k, 0.0)) for k in kinds) / len(kinds) if kinds else 0.0
            # a screen reader needs words or rows beside a picture: any text form may be that companion
            forced = "screen_reader" in needs and FORMS[second].text_like and second != "data_file" and \
                not form.text_like and "sound" not in form.media
            excluded = None
            primary_excl = cand.excluded if not (cand.excluded or "").startswith("a screen reader") or not forced \
                else None
            if primary_excl:
                excluded = primary_excl
            elif other.excluded:
                excluded = other.excluded
            else:
                # what the second adds on the kinds the first shows poorly (below half): a diagram's missing words,
                # not a share of a kind the first already shows
                # Only words add what a picture lacks this way ("a diagram plus two sentences"); rows add precision.
                gain_carried = sum(max(0.0, per_s.get(k, 0.0) - per_p.get(k, 0.0)) for k in kinds
                                   if per_p.get(k, 0.0) < COMBO_PRIMARY_CARRIED) / len(kinds) \
                    if kinds and second in WORDS else 0.0
                gain_precision = _precision(pair, 1.0, other.scores["carried"]) - _precision((name,), 1.0)
                if not forced and cand.scores["carried"] < COMBO_PRIMARY_CARRIED:
                    excluded = f"{form.label} carries too little of this content to lead"
                elif not forced and carried - other.scores["carried"] < COMBO_CARRIED_GAIN and \
                        _clarity(pair, purposes) - _clarity((second,), purposes) < COMBO_CARRIED_GAIN:
                    excluded = f"{form.label} would add nothing to {FORMS[second].label}"
                elif not forced and not (gain_carried >= COMBO_CARRIED_GAIN or
                                         (need_precision >= COMBO_PRECISION_NEED and
                                          gain_precision >= COMBO_PRECISION_GAIN)):
                    excluded = f"{FORMS[second].label} would add nothing {form.label} lacks"
            seconds_ = cand.estimates.get("seconds", 0.0) + other.estimates.get("seconds", 0.0)
            bytes_ = cand.estimates.get("bytes", 0.0) + other.estimates.get("bytes", 0.0)
            if excluded is None and not relaxed and (seconds_ > context.budget_s or bytes_ > context.budget_bytes):
                excluded = "the two together would take more than the budget allows"
            effort = min(1.0, _effort_of(form, shape, display) + 0.5 * _effort_of(FORMS[second], shape, display))
            scores = {
                "clarity": _clarity(pair, purposes),
                "carried": carried,
                "precision": _precision(pair, need_precision, other.scores["carried"]),
                "interactivity": _interactivity(pair, need_interaction),
                "effort": 1.0 - effort,
                "feasibility": min(cand.scores["feasibility"], other.scores["feasibility"]),
                "accessibility": _accessibility(pair, needs, shape_, display),
                "cost": _cost(seconds_, bytes_, context if not relaxed else Context(budget_s=1e9,
                                                                                    budget_bytes=1 << 60)),
            }
            scores = {c: round(min(1.0, max(0.0, v)), 4) for c, v in scores.items()}
            combos.append(Candidate(pair, scores, _total(scores, weights, pair, context if pref_on else None),
                                    excluded, cand.adaptations, cand.problems,
                                    MappingProxyType({"seconds": round(seconds_, 4), "bytes": bytes_})))
    candidates = list(singles.values()) + combos
    admitted = [c for c in candidates if c.excluded is None]
    by_total = sorted(admitted, key=lambda c: (-c.total, _complexity(c.forms), c.forms))

    def ranked():
        return sorted(candidates, key=lambda c: (c.excluded is not None, -c.total, _complexity(c.forms), c.forms))

    # Rule 1: an explicit form wins when feasible (its best-scoring member, alone or with its accessible fallback).
    if explicit:
        named = [c for c in by_total if c.forms[0] in explicit and (len(c.forms) == 1 or "screen_reader" in needs
                                                                    or c.forms[1] in explicit)]
        if named:
            return _Run(named[0].forms, ranked(), True, "explicit")
        nearest = _nearest(explicit, by_total)
        if nearest is not None:
            return _Run(nearest.forms, ranked(), False, "nearest")
    if not by_total:
        return _Run((), ranked(), None if not explicit else False, "none")
    words_cands = [c for c in by_total if c.forms == (words_form,)]
    words_total = words_cands[0].total if words_cands else None
    # Rule 2: a scalar or a sentence's worth stays words (hearing a value is the one thing words cannot do).
    heard = [c for c in by_total if "sound" in FORMS[c.forms[0]].media and c.scores["carried"] >= HEARD_CARRIED]
    hear = max(purposes, key=lambda p: (purposes[p], p)) == "hear" and bool(heard)
    if _sentence_worth(shape) and words_cands and not hear:
        return _Run((words_form,), ranked(), None, "small")
    # A stated wish for a short answer is honoured with words whenever words can be given; so is a question whose
    # answer is a few values picked out of the content (a yes or no, an extreme, a count, one time or name).
    if use_words and (reading.simple or reading.selective) and words_cands:
        return _Run((words_form,), ranked(), None, "words")
    # A stated preference is followed: the best admitted form the person prefers (words included: a person who prefers
    # the simplest answer gets it). Not in the content-only and words-only runs, so it is "context" that decided.
    if not relaxed:
        preferred = [c for c in by_total if len(c.forms) == 1 and _preference(c.forms, context) > 0]
        if preferred:
            top = max(preferred, key=lambda c: (_preference(c.forms, context), c.total))
            return _Run(top.forms, ranked(), None, "preference")
    # Rule 3: to hear is to hear: when hearing is the main purpose, the best admitted form that is heard wins.
    if hear:
        return _Run(heard[0].forms, ranked(), None, "hear")
    # Rule 4: words that lose most of a kind of the content (cover less than WORDS_SHARE of it) are not chosen by
    # score while any other form is admitted: paragraphs where a picture or a table carries what words cannot is the
    # planner's "too poor" failure.
    lost = _words_lost(words_form, carried_kinds, by_total)
    pool = [c for c in by_total if not (lost and c.forms[0] in WORDS)] or by_total
    best = pool[0]
    # Rule 5: the simplest form within a small margin of the best.
    close = [c for c in pool if c.total >= best.total - SIMPLE_MARGIN]
    choice = min(close, key=lambda c: (_complexity(c.forms), -c.total, c.forms))
    rule = "best" if choice is best else "simplest"
    # Rule 6: a non-text form unasked only when its margin over words is material.
    material = MATERIAL_RICH if (use_words and reading.rich) else MATERIAL
    if not lost and not _is_text(choice.forms[0]) and words_total is not None and \
            best.total - words_total < material:
        choice, rule = next(c for c in by_total if _is_text(c.forms[0])), "words"
    # Rule 7: a picture, sound or model chosen unasked comes with a sentence that says what it shows (the directive's
    # "a diagram plus two sentences"; text supports the experience): the pair, when words are admitted and the two
    # together fit the budget.
    if len(choice.forms) == 1 and not _is_text(choice.forms[0]) and words_cands:
        captioned = next((c for c in candidates if c.forms == (choice.forms[0], words_form)), None)
        if captioned is not None and (captioned.excluded is None or
                                      any(w in captioned.excluded for w in CAPTION_EXEMPT)):
            if captioned.excluded is not None:
                captioned = _replace(captioned, excluded=None)
                candidates = [captioned if c.forms == captioned.forms else c for c in candidates]
            return _Run(captioned.forms, ranked(), None, "caption")
    return _Run(choice.forms, ranked(), None, rule)


WORDS = ("prose", "spoken")
WORDS_EFFORT_PER_FULL = 0.4  # effort words add when they state as many values as they can hold
WORDS_EFFORT_PER_STEPS = 0.1  # ... and as many steps or items as they can hold
WORDS_SHARE = 0.5           # words that cover less than this share of a kind of the content have lost it


def _is_text(name: str) -> bool:
    return FORMS[name].text_like or "speech" in FORMS[name].media


def _words_lost(words_form: str, carried_kinds: Mapping[str, dict], admitted) -> tuple[str, ...]:
    """The kinds words cover less than WORDS_SHARE of, when any form that is not words is admitted. (Not "when
    another form carries that kind better": whether one does changes as content grows, and the rule must not.)"""
    if not any(c.forms[0] not in WORDS for c in admitted):
        return ()
    per = carried_kinds.get(words_form, {})
    fits = FORMS[words_form].kinds
    lost = []
    for kind, carried in per.items():
        fit = float(fits.get(kind, 0.0))
        if fit and carried / fit < WORDS_SHARE:
            lost.append(kind)
    return tuple(lost)


def _fallback_text(form: Form) -> str:
    fallback = form.fallback or "table"
    return fallback if FORMS.get(fallback) is not None and FORMS[fallback].text_like else "table"


def _why_media(form, missing, context, relaxed) -> str:
    if "voice_only" in context.accessibility and not relaxed:
        return f"only speech and sound are wanted, and {form.label} is not heard"
    plain = {"text": "text", "speech": "speech", "picture": "pictures", "sound": "sound", "interactive":
             "interactive views", "motion": "moving pictures", "file": "files", "3d": "3D views"}
    return f"this device cannot show {plain[missing[0]]}"


def _replace(cand: Candidate, **changes) -> Candidate:
    values = {"forms": cand.forms, "scores": cand.scores, "total": cand.total, "excluded": cand.excluded,
              "adaptations": cand.adaptations, "problems": cand.problems, "estimates": cand.estimates}
    values.update(changes)
    return Candidate(**values)


def _total(scores, weights, forms, context) -> float:
    total = sum(weights[c] * scores[c] for c in CRITERIA)
    if context is not None:
        total += PREFERENCE_WEIGHT * _preference(forms, context)
    return round(total, 4)


def _complexity(forms) -> int:
    return max(FORMS[f].complexity for f in forms) + (len(forms) - 1)


def difference(a: str, b: str) -> float:
    """How different two forms are in kind, in [0, 1]: media (Jaccard distance) 0.35, family 0.35, encoding 0.15,
    interactivity 0.15. Two forms of one family and one medium differ by at most 0.3, and a family alone by 0.35,
    both below KIND_DIFFERENCE (0.4): two charts never count as different kinds, nor a timeline and a line chart."""
    fa, fb = FORMS[a], FORMS[b]
    ma, mb = set(fa.media), set(fb.media)
    media = len(ma ^ mb) / len(ma | mb)
    return round(0.35 * media + 0.35 * (fa.family != fb.family) + 0.15 * (fa.encoding != fb.encoding) +
                 0.15 * abs(fa.interactivity - fb.interactivity), 4)


MIN_FIT_UNITS = 50          # a downsampled form must still show at least this many points, bars or cells


# Forms that can be made smaller to fit a byte budget, and what they still carry when cut down: a chart drawn from an
# envelope of fewer points keeps the shape (0.6); a table cut to the rows that fit keeps a summary (0.3).
DOWNSAMPLED = {"line_chart": 0.6, "scatter_chart": 0.6, "heatmap": 0.6, "small_multiples": 0.6,
               "sonification": 0.6, "table": 0.3}


def _units_within_budget(form: Form, context: Context) -> float | None:
    """How many points or cells of a form that can be cut down fit the byte budget, or None if too few."""
    if form.name not in DOWNSAMPLED or form.bytes_per_unit <= 0:
        return None
    fit = (context.budget_bytes - form.base_bytes) / form.bytes_per_unit
    return fit if fit >= MIN_FIT_UNITS else None


# Exclusions of a (picture, words) pair that the caption rule overrides: the gain rules judge words as carriers;
# as a caption they state what the picture shows.
CAPTION_EXEMPT = ("would add nothing", "carries too little of this content to lead")

AGENT_KEEP = 0.5            # the weight an agent device adds to keeping a copy (before renormalising)
ACCESS_FACTOR = 3.0         # the accessibility weight's multiple when the person has stated needs
NEAREST_WEIGHT = 0.3        # how much difference in kind from the named form counts against a substitute's total


def _nearest(explicit, by_total) -> Candidate | None:
    """The admitted single form that best balances its total against its difference in kind from the forms named
    (total - NEAREST_WEIGHT x difference): close in kind, and good for this content."""
    singles = [c for c in by_total if len(c.forms) == 1]
    if not singles:
        return None
    return min(singles, key=lambda c: (NEAREST_WEIGHT * min(difference(c.forms[0], e) for e in explicit) - c.total,
                                       c.forms))


def _purpose_mix(shape, n_pieces, reading: Reading | None) -> tuple[dict, list]:
    mix = _content_purposes(shape, n_pieces)
    dropped = []
    if reading is not None:
        for purpose, weight in reading.purposes.items():
            if serves(purpose, shape, n_pieces):
                mix[purpose] = mix.get(purpose, 0.0) + weight
            else:
                dropped.append(purpose)
    total = sum(mix.values()) or 1.0
    return {p: round(w / total, 4) for p, w in sorted(mix.items())}, dropped


def plan(request: Request, content: tuple[ContentShape, ...] = (), context: Context | None = None, *,
         adjustments: Mapping | None = None) -> FormPlan:
    """Decide the form of a result. Pure and deterministic (feasibility read from `make.catalog()` at call time
    when the context does not list generators)."""
    if isinstance(content, ContentShape):
        content = (content,)
    content = tuple(content)
    if not all(isinstance(s, ContentShape) for s in content):
        raise Malformed("content is a tuple of ContentShape")
    context = context or Context()
    shape = merge(content)
    reading = read_request(request)
    display = resolve_display(context)
    generators = available_generators(context)
    relaxed_display = Display(display.device, frozenset(MEDIA), display.width_px, display.screen_rows or 30,
                              display.screen_cols or 8)
    mix_content, _ = _purpose_mix(shape, len(content), None)
    mix_words, dropped = _purpose_mix(shape, len(content), reading)
    main = max(mix_words, key=lambda p: (mix_words[p], p))
    weights = _weights(adjustments, primary_kind(shape), main, context.device)
    no_words = Reading(None, None, (), MappingProxyType({}), False, False, ())
    run0 = _score_run(shape, len(content), no_words, context, relaxed_display, generators, mix_content, weights,
                      use_words=False, relaxed=True)
    run1 = _score_run(shape, len(content), reading, context, relaxed_display, generators, mix_words, weights,
                      use_words=True, relaxed=True)
    run2 = _score_run(shape, len(content), reading, context, display, generators, mix_words, weights,
                      use_words=True, relaxed=False)
    if reading.explicit:
        decided_by = "explicit"
    elif run2.chosen != run1.chosen:
        decided_by = "context"
    elif run1.chosen != run0.chosen:
        decided_by = "words"
    else:
        decided_by = "content"
    notes = []
    for purpose in dropped:
        notes.append(f"the words suggest {PURPOSE_PHRASE[purpose]}, but the content has nothing of that kind")
    chosen_cand = next(c for c in run2.candidates if c.forms == run2.chosen) if any(
        c.forms == run2.chosen for c in run2.candidates) else None
    if chosen_cand is not None and chosen_cand.adaptations:
        notes.append("it would be made with: " + ", ".join(dict.fromkeys(chosen_cand.adaptations)))
    reason = _reason(run2, run1, run0, decided_by, reading, shape, main, display, context)
    return FormPlan(run2.chosen, tuple(run2.candidates), decided_by, reason, main, MappingProxyType(mix_words),
                    reading.evidence, reading.explicit, run2.explicit_ok, MappingProxyType(dict(weights)),
                    tuple(notes))


# ---------------------------------------------------------------------------------------------------------------
# The sentence


PURPOSE_PHRASE = {"look_up": "a value to look up", "compare": "how they compare", "change_over_time":
                  "how it changed", "distribution": "how the values are spread", "relation": "how things are related",
                  "follow_process": "how to follow it step by step", "what_if": "what happens when the inputs change",
                  "locate": "where things are", "hear": "how it sounds", "keep": "a copy to keep",
                  "learn": "an explanation", "look_at": "what it looks like", "understand": "to understand it",
                  "decide": "what to choose"}


def label_of(forms) -> str:
    if not forms:
        return "no form"
    if len(forms) == 1:
        return FORMS[forms[0]].label
    first, second = FORMS[forms[0]].label, FORMS[forms[1]]
    if second.name == "prose":
        return f"{first} with a sentence on what it shows"
    if second.name == "spoken":
        return f"{first} with a spoken sentence on what it shows"
    return f"{first} with {second.label}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def content_phrase(shape: ContentShape | None) -> str:
    if shape is None or not shape.kinds:
        return "there is nothing to show beyond words"
    kind, n, k = primary_kind(shape), shape.n_items, shape.n_series
    series = f" in {k} series" if k > 1 else ""
    phrases = {
        "scalar": "the answer is a single value",
        "series": f"there are {n * max(1, k)} values in {'time order' if shape.has_time else 'order'}{series}",
        "categories": f"there are {n} categories to compare" + (f" on {k} measures" if k > 1 else ""),
        "distribution": f"there are {n} values whose spread matters",
        "relation": f"there are {n} pairs of related values",
        "network": f"the content is a network of {n} nodes",
        "hierarchy": f"the content is a hierarchy of {n} items",
        "sequence": f"there are {n} steps in order",
        "timeline": f"there are {n} dated events",
        "process": "the content is a model with inputs you can vary" if shape.has_parameters else
                   f"the content is a process of {n} states",
        "surface": f"the content is a surface of {n} points",
        "solid": f"the content is a solid object of {n} vertices",
        "map": f"the content is {n} places",
        "image": "the content is a picture",
        "sound": f"the content is a sound of {shape.duration_s:.0f} seconds",
        "prose": f"the content is {shape.text_chars} characters of text",
        "code": "the content is code",
        "records": f"there are {n} records" + (f" of {k} columns" if k > 1 else ""),
    }
    if _sentence_worth(shape) and kind in VALUE_KINDS and _values(shape) > 1:
        return f"the answer is only {_values(shape)} values"
    if _sentence_worth(shape) and kind == "prose":
        return "the answer fits in a few sentences"
    return phrases[kind]


def _reason(run2: _Run, run1: _Run, run0: _Run, decided_by, reading: Reading, shape, main, display, context) -> str:
    words_form = "prose" if "text" in display.shows else "spoken"
    if not run2.chosen:
        words = next((c for c in run2.candidates if c.forms == (words_form,)), None)
        why = words.excluded if words is not None and words.excluded else "nothing can be presented here"
        return f"No form can be given, because {why}."
    label = _cap(label_of(run2.chosen))
    if decided_by == "explicit":
        if run2.explicit_ok:
            return f"{label}, because you asked for one."
        if len(reading.explicit) == 1:
            named = FORMS[reading.explicit[0]].label
        elif reading.explicit_cue and reading.explicit_cue != "named by the caller":
            named = f"the {reading.explicit_cue.split()[-1]}"
        else:
            named = "the form you named"
        why = _why_not(run2, reading.explicit, named)
        return f"{label} instead of {named} you asked for, because {why}; it is the nearest form that can be made here."
    if decided_by == "context":
        blocked = next((c for c in run2.candidates if c.forms == run1.chosen), None)
        why = blocked.excluded if blocked is not None and blocked.excluded else \
            _context_phrase(context, display, run1.chosen, run2.chosen)
        return f"{label}, because {why}; it is the best form this situation allows."
    if decided_by == "words":
        if reading.simple:
            other = label_of(run0.chosen)
            return f"{label}, because you asked for a short answer; {other} could show more of it."
        if reading.selective and run2.chosen and run2.chosen[0] in WORDS:
            return f"{label}, because you asked for {SELECTIVE_PHRASE[reading.selective]}, which a sentence gives."
        if run0.chosen[0] in reading.refused:
            return f"{label}, because you said you did not want {label_of(run0.chosen[:1])}; {content_phrase(shape)}."
        if reading.precise and len(run2.chosen) == 2:
            return f"{label}, because {content_phrase(shape)} and you asked for the exact values as well."
        if reading.rich:
            return f"{label}, because {content_phrase(shape)} and you asked to be shown in full."
        return f"{label}, because {content_phrase(shape)} and you asked about {PURPOSE_PHRASE[main]}."
    # decided by the content
    phrase = content_phrase(shape)
    if run2.chosen == (words_form,):
        if run2.rule == "small":
            return f"{label}, because {phrase}; nothing more is needed."
        return f"{label}, because {phrase}; no other form would add enough to be worth it."
    words = FORMS[words_form]
    cap = int(words.capacity.get("values", 7))
    extra = ""
    if shape is not None and any(UNIT_OF_KIND[k] in ("values", "dist") for k in _specific_kinds(shape)) and \
            _values(shape) > cap:
        extra = f"; a sentence could carry only about {cap} of them"
    elif shape is not None and any(UNIT_OF_KIND[k] == "items" for k in _specific_kinds(shape)) and \
            shape.n_items > int(words.capacity.get("items", 8)):
        extra = f"; a sentence could follow only about {int(words.capacity.get('items', 8))} of them"
    purpose = "what happened when" if primary_kind(shape) == "timeline" and main == "change_over_time" \
        else PURPOSE_PHRASE[main]
    return f"{label}, because {phrase} and the question is {purpose}{extra}."


def _why_not(run: _Run, explicit, named: str) -> str:
    reasons = [c.excluded for c in run.candidates if len(c.forms) == 1 and c.forms[0] in explicit and c.excluded]
    if len(explicit) > 1 and reasons and all("cannot carry this content" in r for r in reasons):
        return f"{named} cannot carry this content"
    if len(explicit) > 1 and reasons and all(r.startswith("nothing here can make") for r in reasons):
        return f"nothing here can make {named} yet"
    if len(explicit) > 1 and reasons and all(r.startswith("this device cannot show") for r in reasons):
        return reasons[0]
    return reasons[0] if reasons else "it cannot be made here"


def _context_phrase(context: Context, display: Display, unconstrained, chosen) -> str:
    """Why the context moved the choice when it excluded nothing: a narrower screen, a preference, a need."""
    if any(_preference(forms, context) for forms in (unconstrained, chosen) if forms):
        return "of the preferences you have stated"
    if context.accessibility:
        return "of the accessibility needs stated"
    if display.width_px < _DEVICE_SIZE["console"][0]:
        return f"this device's screen is {display.width_px} pixels wide, where {label_of(unconstrained)} fits less"
    return "of what this device can show"


def alternatives(result: FormPlan, n: int = 3) -> tuple[Candidate, ...]:
    """Up to n admitted candidates that differ in kind (`difference` >= KIND_DIFFERENCE from every one already
    taken), best first, starting with the chosen one. A combination counts as its first form."""
    if n <= 0:
        return ()
    admitted = [c for c in result.candidates if c.excluded is None]
    chosen = next((c for c in admitted if c.forms == result.chosen), None)
    ordered = ([chosen] if chosen else []) + [c for c in admitted if c is not chosen]
    taken: list[Candidate] = []
    for cand in ordered:
        if all(difference(cand.forms[0], t.forms[0]) >= KIND_DIFFERENCE for t in taken):
            taken.append(cand)
        if len(taken) == n:
            break
    return tuple(taken)


def describe(result: FormPlan, limit: int = 6) -> str:
    """The plan in plain words, for a person."""
    lines = [f"Form: {_cap(label_of(result.chosen))}", f"Why: {result.reason}",
             f"Decided by: {_DECIDED_WORDS[result.decided_by]}"]
    lines.extend(f"Note: {note}" for note in result.notes)
    others = [c for c in result.candidates if c.excluded is None and c.forms != result.chosen][:limit]
    if others:
        lines.append("Other forms considered, best first:")
        lines.extend(f"  {label_of(c.forms)} ({c.total:.2f})" for c in others)
    lines.append("This is a plan made by the system: it decides the form only, never whether the content is true.")
    return "\n".join(lines)


_DECIDED_WORDS = {"explicit": "the form you asked for", "content": "the content itself",
                  "words": "the words of your request", "context": "the device, needs or what is available here"}

__all__ = ["ACCESSIBILITY", "ALIASES", "CRITERIA", "Candidate", "Context", "DECIDED_BY", "DEVICES", "Display", "FORMS",
           "Foresight", "Form", "FormPlan", "KIND_DIFFERENCE", "MEDIA", "PURPOSES", "Problem", "Reading", "Request",
           "WEIGHTS", "alternatives", "available_generators", "content_phrase", "describe", "difference", "foresee", "label_of",
           "merge", "plan", "read_request", "register_form", "resolve_display", "serves", "unregister_form"]
