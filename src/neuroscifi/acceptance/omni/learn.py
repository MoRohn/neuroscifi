"""OMNIMIND benchmark, family `learn`: does feedback improve later form choices? (lane ZF; docs/quality/
OMNIMIND_BENCHMARK_FORMS.md has the design and what it cannot show.)

SIMULATED people (learn_people.py) with hidden, stable preferences make a stream of requests; for each, a chooser picks
a form from the candidates the content kind and device allow, and the person reacts as a real person could: kept,
dismissed, or replaced by the form they wanted. This measures ADAPTATION TO SIMULATED PREFERENCES, not benefit to a
real person.

The learner interface (a fresh learner per person and seed; `make()` returns one):
    choose(case) -> form          case = {"person": id, "index", "cycle", "kind", "device", "candidates": [...],
                                          "content": ContentShape dict}
    feedback(case, form, outcome) outcome = {"outcome": "kept" | "dismissed" | "replaced", "by": form | None}
The person's preference is never in `case`. Standard library only; no product code.
"""
from __future__ import annotations

from collections import defaultdict
import math
import random
import statistics

from . import learn_people

SPLITS = ("development", "held_out")
LABEL = ("OMNIMIND benchmark, family learn: SIMULATED people with hidden, stable preferences; it measures adaptation "
         "to simulated preferences from simulated feedback, NOT benefit to a real person; offline; no model")

_SCREEN = {
    "scalar": ("prose", "table", "bar_chart"),
    "series": ("prose", "table", "line_chart", "heatmap", "sonification", "explorable"),
    "categories": ("prose", "table", "bar_chart"),
    "distribution": ("prose", "table", "histogram", "sonification"),
    "relation": ("prose", "table", "scatter_chart"),
    "hierarchy": ("prose", "table", "node_link"),
    "network": ("prose", "table", "node_link"),
    "records": ("prose", "table", "data_file"),
    "process": ("prose", "line_chart", "explorable", "step_through"),
    "timeline": ("prose", "table", "timeline"),
    "sound": ("prose", "waveform_spectrum", "sonification"),
}
_HEARD = ("series", "distribution", "sound")
DEFAULT = {"scalar": "prose", "series": "line_chart", "categories": "bar_chart", "distribution": "histogram",
           "relation": "scatter_chart", "hierarchy": "node_link", "network": "node_link", "records": "table",
           "process": "explorable", "timeline": "timeline", "sound": "waveform_spectrum"}
_SIZES = {"scalar": (1, 1), "series": (30, 3000), "categories": (3, 30), "distribution": (100, 5000),
          "relation": (20, 500), "hierarchy": (10, 400), "network": (10, 300), "records": (10, 5000),
          "process": (20, 400), "timeline": (5, 60), "sound": (1, 1)}


def candidates(kind: str, device: str) -> tuple[str, ...]:
    if device == "voice":
        return ("spoken", "prose") + (("sonification",) if kind in _HEARD else ())
    return _SCREEN[kind]


def preference(person: dict, kind: str, device: str) -> tuple[str, str | None]:
    """(the form the person wants, their acceptable second) for one key; hidden from every chooser but the oracle."""
    cands = [c for c in candidates(kind, device) if c not in person["never"]]
    order = person["by_kind"].get(kind) or person["by_device"].get(device) or person["order"]
    ranked = [f for f in order if f in cands]
    if not ranked:
        default = DEFAULT[kind] if device != "voice" else "spoken"
        ranked = [default] if default in cands else cands[:1]
    rest = [f for f in ranked[1:]]
    return ranked[0], (rest[0] if rest else None)


def profile(person: dict) -> dict:
    """The full preference map over every key: what the held-out test compares with development."""
    return {f"{k}/{d}": preference(person, k, d)[0] for k in _SCREEN for d in ("console", "pocket", "voice")}


def react(person: dict, kind: str, device: str, form: str, rng: random.Random) -> dict:
    """A simulated person's reaction. One draw decides inconsistency, one whether they keep a second choice, one
    whether they say what they wanted; the draws come from a per-request stream, the same for every chooser."""
    want, second = preference(person, kind, device)
    flip, keep2, say = rng.random(), rng.random(), rng.random()
    if form not in candidates(kind, device) or form in person["never"]:
        liked = False
    elif form == want:
        liked = True
    elif form == second:
        liked = keep2 < person["accept_second"]
    else:
        liked = False
    if flip < person["noise"] and form not in person["never"]:
        liked = not liked
    if liked:
        return {"outcome": "kept", "by": None}
    if say < person["replace"] and form != want:
        return {"outcome": "replaced", "by": want}
    return {"outcome": "dismissed", "by": None}


def stream(person: dict, seed: int):
    """The person's requests for one seed: CYCLES x PER_CYCLE (index, cycle, kind, device, n_items)."""
    rng = random.Random(f"stream:{seed}:{person['id']}")
    kinds, kw = zip(*sorted(person["kinds"].items()))
    devices, dw = zip(*sorted(person["devices"].items()))
    out = []
    for i in range(learn_people.CYCLES * learn_people.PER_CYCLE):
        kind = rng.choices(kinds, kw)[0]
        device = rng.choices(devices, dw)[0]
        lo, hi = _SIZES[kind]
        out.append((i, i // learn_people.PER_CYCLE, kind, device, rng.randint(lo, hi)))
    return out


# ---- reference choosers -----------------------------------------------------------------------------------------------

class NeverLearns:
    """The content kind's usual form (spoken on a speaker); feedback is ignored."""

    def choose(self, case):
        return "spoken" if case["device"] == "voice" else DEFAULT[case["kind"]]

    def feedback(self, case, form, outcome):
        pass


class Today(NeverLearns):
    """Today's system: sentences, whatever the content."""

    def choose(self, case):
        return "prose"


class KeyMajority(NeverLearns):
    """Reference learner: per (content kind, device), a tally (+1 kept, -1 dismissed or replaced, +1 to the form
    named in a replacement); the highest-tallied candidate wins, ties going to the usual form, then candidate order."""

    def __init__(self):
        self.tally = defaultdict(lambda: defaultdict(int))

    def key(self, case):
        return (case["kind"], case["device"])

    def choose(self, case):
        tally = self.tally[self.key(case)]
        default = super().choose(case)
        order = [default] + [c for c in case["candidates"] if c != default]
        return max(order, key=lambda f: (tally[f], -order.index(f)))

    def feedback(self, case, form, outcome):
        tally = self.tally[self.key(case)]
        tally[form] += 1 if outcome["outcome"] == "kept" else -1
        if outcome["outcome"] == "replaced" and outcome["by"]:
            tally[outcome["by"]] += 1


class PersonMajority(KeyMajority):
    """Weaker reference: one tally per person, whatever the content or device."""

    def key(self, case):
        return "all"


REFERENCES = {
    "today": ("today's system: always prose", Today),
    "never_learns": ("the kind's usual form, never adjusted", NeverLearns),
    "key_majority": ("reference learner: per (kind, device) tally of the person's reactions", KeyMajority),
    "person_majority": ("reference learner: one tally per person, ignoring kind and device", PersonMajority),
    "oracle": ("knows the hidden preference: an upper bound, not a chooser", None),
}


class _Oracle:
    def __init__(self, person):
        self.person = person

    def choose(self, case):
        return preference(self.person, case["kind"], case["device"])[0]

    def feedback(self, case, form, outcome):
        pass


# ---- running and scoring ---------------------------------------------------------------------------------------------

def run_person(make, person: dict, seed: int) -> list[bool]:
    """Kept or not, request by request, for one fresh chooser."""
    chooser = _Oracle(person) if make is None else make()
    kept = []
    for i, cycle, kind, device, n in stream(person, seed):
        case = {"person": person["id"], "index": i, "cycle": cycle, "kind": kind, "device": device,
                "candidates": list(candidates(kind, device)),
                "content": {"kinds": [kind], "n_items": n, "n_series": 1, "has_time": kind == "series",
                            "has_space": 0, "has_parameters": kind == "process", "duration_s": 0.0,
                            "text_chars": 0}}
        form = getattr(chooser, "choose")(dict(case))
        outcome = react(person, kind, device, form, random.Random(f"react:{seed}:{person['id']}:{i}"))
        chooser.feedback(dict(case), form, dict(outcome))
        kept.append(outcome["outcome"] == "kept")
    return kept


def _sd(values):
    return round(statistics.stdev(values), 4) if len(values) > 1 else 0.0


def score(subject, split: str) -> dict:
    """Kept share per cycle (mean over people and seeds, with the sd over seeds), the late share (the second half of
    the cycles) per seed and per person, and the share over all cycles (which credits adapting fast), for one
    chooser. `subject`: a reference name, or a zero-argument factory
    returning a fresh learner."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    make = REFERENCES[subject][1] if isinstance(subject, str) else subject
    people, seeds = learn_people.ALL[split], learn_people.SEEDS[split]
    cycles, per = learn_people.CYCLES, learn_people.PER_CYCLE
    late_from = cycles // 2
    by_seed_cycle = defaultdict(list)          # (seed, cycle) -> kept flags over people
    late_seed, late_person, all_seed = defaultdict(list), defaultdict(list), defaultdict(list)
    for person in people:
        for seed in seeds:
            kept = run_person(make, person, seed)
            for c in range(cycles):
                block = kept[c * per:(c + 1) * per]
                by_seed_cycle[(seed, c)] += block
                all_seed[seed] += block
                if c >= late_from:
                    late_seed[seed] += block
                    late_person[person["id"]] += block
    cycle_means, cycle_sds = [], []
    for c in range(cycles):
        per_seed = [sum(by_seed_cycle[(s, c)]) / len(by_seed_cycle[(s, c)]) for s in seeds]
        cycle_means.append(round(statistics.fmean(per_seed), 4))
        cycle_sds.append(_sd(per_seed))
    late = [sum(late_seed[s]) / len(late_seed[s]) for s in seeds]
    whole = [sum(all_seed[s]) / len(all_seed[s]) for s in seeds]
    return {"label": LABEL, "split": split, "people": [p["id"] for p in people], "seeds": list(seeds),
            "cycles": cycles, "requests_per_cycle": per,
            "denominator_per_cycle": len(people) * len(seeds) * per,
            "kept_by_cycle": cycle_means, "kept_by_cycle_sd_over_seeds": cycle_sds,
            "late_cycles": [late_from + 1, cycles],
            "late_kept": {"mean": round(statistics.fmean(late), 4), "sd_over_seeds": _sd(late),
                          "per_seed": [round(x, 4) for x in late]},
            "all_kept": {"mean": round(statistics.fmean(whole), 4), "sd_over_seeds": _sd(whole),
                         "per_seed": [round(x, 4) for x in whole]},
            "late_kept_by_person": {pid: round(sum(v) / len(v), 4) for pid, v in late_person.items()}}


def baseline() -> dict:
    """Every reference on both splits, the gains over never_learns paired by seed, and the gain a learner must show
    to be visible above the spread over seeds."""
    out = {}
    for split in SPLITS:
        results = {name: {"what": REFERENCES[name][0], **score(name, split)} for name in REFERENCES}
        gains, visible = {}, {"rule": "a difference between two choosers' kept shares is visible when it exceeds "
                                      "2 * sqrt(2) * the larger sd over seeds of the two (twice the noise of a "
                                      "difference of two independent per-seed means)"}
        for measure in ("late_kept", "all_kept"):
            base = results["never_learns"][measure]["per_seed"]
            gains[measure] = {}
            for name, r in results.items():
                diffs = [a - b for a, b in zip(r[measure]["per_seed"], base)]
                gains[measure][name] = {"mean": round(statistics.fmean(diffs), 4), "sd_over_seeds": _sd(diffs)}
            sd_never = results["never_learns"][measure]["sd_over_seeds"]
            sd_major = results["key_majority"][measure]["sd_over_seeds"]
            visible[measure] = {"over_never_learns": round(2 * math.sqrt(2) * sd_never, 4),
                                "over_key_majority": round(2 * math.sqrt(2) * max(sd_never, sd_major), 4)}
        out[split] = {"references": results, "gain_over_never_learns_paired_by_seed": gains,
                      "visible_gain": visible}
    return out
