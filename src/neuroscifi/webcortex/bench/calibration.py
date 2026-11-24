"""Does mapping a forecast through its own settled history help? A simulation, because the individual has no
history of its own to learn the answer from (ADR-046; evolution.fit_recalibration).

A stream is one kind of forecaster: it states a forecast p for each prediction, and the prediction comes true with
a probability t that the forecaster does not know. t is tied to p by a rule the stream fixes (the forecaster is
right, too sure, not sure enough, off by a constant, says the same thing every time, knows nothing, has it
backwards) or by two rules, one after the other (its habits drift). A prediction's outcome becomes known `lag`
predictions later. Each forecast is mapped with the outcomes known when it is made, then scored: so every score
here is on a prediction the mapping had not seen (prequential), and nothing is fitted to what it is scored on.

Two scores: the Brier score against the outcome (what the individual could compute itself) and the mean squared
distance to t (what only the simulation knows; it has no coin-toss noise in it).

The settings of the mapping (how many settlements before it acts, how strongly it is held to "the forecast is
right", how far back it looks) were chosen on the DEVELOPMENT streams and are reported on the HELD-OUT streams,
which use other rules, other forecast values, another lag and other seeds.
"""
from __future__ import annotations

import math
import random

from .. import evolution

KNOWLEDGE = (0.72, 0.75, 0.78, 0.82, 0.86, 0.90, 0.93)          # confidences of provisional and accepted items
HYPOTHESIS = (0.20, 0.30, 0.40, 0.50, 0.60, 0.70)               # posteriors of research opportunities
BROAD = tuple(round(0.05 * i, 2) for i in range(1, 20))
HIGH = (0.80, 0.85, 0.90, 0.95)
LOW = (0.10, 0.15, 0.25, 0.35, 0.45)
PREFIXES = (25, 50, 100)                                         # "few samples": the first N predictions of a stream


def _rule(shift=0.0, slope=1.0, const=None):
    return {"shift": shift, "slope": slope, "const": const}


# name -> (forecast values, rule or [rule before the midpoint, rule after it])
DEVELOPMENT = {
    "right": (BROAD, _rule()),
    "knowledge that nearly always holds": (KNOWLEDGE, _rule(const=0.97)),
    "hypotheses that rarely come true": (HYPOTHESIS, _rule(shift=-1.5)),
    "too sure (slope 0.5)": (BROAD, _rule(slope=0.5)),
    "not sure enough (slope 1.8)": (BROAD, _rule(slope=1.8)),
    "hypotheses that come true more often": (HYPOTHESIS, _rule(shift=1.0)),
    "one forecast every time (0.5, true 0.2)": ((0.5,), _rule(const=0.2)),
    "knows nothing (always 0.5 true)": (BROAD, _rule(const=0.5)),
    "backwards (slope -1)": (BROAD, _rule(slope=-1.0)),
    "drift: rarely true, then often": (HYPOTHESIS, [_rule(shift=-1.5), _rule(shift=1.0)]),
    "drift: too high, then right": (KNOWLEDGE, [_rule(shift=-1.5), _rule()]),
}
HELD_OUT = {
    "right (hypothesis values)": (HYPOTHESIS, _rule()),
    "right (knowledge values)": (KNOWLEDGE, _rule()),
    "knowledge that holds 9 times in 10": (HIGH, _rule(const=0.90)),
    "knowledge that holds less often (shift -1.2)": (KNOWLEDGE, _rule(shift=-1.2)),
    "hypotheses that seldom come true (shift -1.0)": (LOW, _rule(shift=-1.0)),
    "too sure (slope 0.65)": (BROAD, _rule(slope=0.65)),
    "not sure enough (slope 1.5)": (BROAD, _rule(slope=1.5)),
    "hypotheses that come true more often (shift 0.7)": (HYPOTHESIS, _rule(shift=0.7)),
    "one forecast every time (0.7, true 0.45)": ((0.7,), _rule(const=0.45)),
    "knows nothing (always 0.3 true)": (BROAD, _rule(const=0.3)),
    "backwards (slope -0.5)": (BROAD, _rule(slope=-0.5)),
    "drift: often true, then rarely": (HYPOTHESIS, [_rule(shift=1.2), _rule(shift=-1.0)]),
    "drift: right, then too high": (KNOWLEDGE, [_rule(), _rule(shift=-1.2)]),
}
# Not used to choose anything: outcomes that arrive in bursts. In the individual one refresh can settle several
# predictions about one source, so settlements are not independent draws and count for fewer than their number.
BURSTS = {
    "right, outcomes in bursts of 5": (BROAD, _rule()),
    "right (knowledge values), outcomes in bursts of 5": (KNOWLEDGE, _rule()),
    "hypotheses that rarely come true, in bursts of 5": (HYPOTHESIS, _rule(shift=-1.5)),
    "knowledge that nearly always holds, in bursts of 5": (KNOWLEDGE, _rule(const=0.97)),
}
SPLITS = {"development": {"streams": DEVELOPMENT, "seeds": tuple(range(6)), "lag": 5, "n": 240, "burst": 1},
          "held_out": {"streams": HELD_OUT, "seeds": tuple(range(100, 112)), "lag": 10, "n": 240, "burst": 1},
          "bursts": {"streams": BURSTS, "seeds": tuple(range(200, 212)), "lag": 10, "n": 240, "burst": 5}}
CHOSEN = {"minimum": evolution.RECALIBRATION_MINIMUM, "window": evolution.RECALIBRATION_WINDOW,
          "prior_shift": evolution.RECALIBRATION_PRIOR_SHIFT, "prior_tilt": evolution.RECALIBRATION_PRIOR_TILT}
GRID = {"minimum": (10, 20, 40), "window": (100, 200, 0), "prior_shift": (1.0, 4.0, 16.0),
        "prior_tilt": (None, 4.0, 16.0, 64.0)}


def truth(p: float, rule: dict) -> float:
    if rule["const"] is not None:
        return rule["const"]
    return evolution._sigmoid(rule["shift"] + rule["slope"] * math.log(p / (1.0 - p)))


def stream(values, rules, seed: int, n: int, burst: int = 1) -> list[tuple[float, float, float]]:
    """[(forecast, outcome, true probability)] in the order the predictions are made. With burst > 1 every
    `burst` consecutive predictions share one draw: they come true together or fail together as far as their
    probabilities allow."""
    rng = random.Random(seed)
    rules = rules if isinstance(rules, list) else [rules]
    out, draw = [], 0.0
    for i in range(n):
        p = rng.choice(values)
        t = truth(p, rules[min(len(rules) - 1, i * len(rules) // n)])
        fresh = rng.random()
        if i % burst == 0:
            draw = fresh
        out.append((p, 1.0 if draw < t else 0.0, t))
    return out


def mapped(points, lag: int, options: dict) -> list[float]:
    """Each forecast as the mapping reads it, from the outcomes that are known when it is made."""
    out, known = [], []
    for i, (p, _, _) in enumerate(points):
        if i - lag - 1 >= 0:
            known.append(points[i - lag - 1][:2])
        out.append(evolution.recalibrate(p, evolution.fit_recalibration(known, **options)))
    return out


def _score(points, forecasts, upto=None) -> dict:
    rows = list(zip(points, forecasts))[:upto]
    n = len(rows)
    return {"brier": sum((q - y) ** 2 for (_, y, _), q in rows) / n,
            "distance": sum((q - t) ** 2 for (_, _, t), q in rows) / n}


def evaluate(split: str, options: dict) -> dict:
    """Per stream of the split, averaged over its seeds: the two scores of the stated and of the mapped forecast,
    over the whole stream and over its first PREFIXES predictions."""
    spec = SPLITS[split]
    out = {}
    for name, (values, rules) in spec["streams"].items():
        sums: dict = {}
        for seed in spec["seeds"]:
            points = stream(values, rules, seed, spec["n"], spec["burst"])
            for label, forecasts in (("stated", [p for p, _, _ in points]),
                                     ("mapped", mapped(points, spec["lag"], options))):
                for upto in (*PREFIXES, None):
                    for key, value in _score(points, forecasts, upto).items():
                        cell = (upto or "all", label, key)
                        sums[cell] = sums.get(cell, 0.0) + value
        k = len(spec["seeds"])
        out[name] = {str(upto): {label: {key: round(sums[(upto, label, key)] / k, 6) for key in ("brier", "distance")}
                                 for label in ("stated", "mapped")} for upto in (*PREFIXES, "all")}
    return out


def after_a_change(split: str, options: dict, span: int = 50) -> dict:
    """Where the mapping is expected to hurt: the `span` predictions that follow the moment a forecaster's rule
    changes, when everything the mapping has learned describes the old rule. Per drifting stream of the split,
    the mean squared distance to the true probability of the stated and of the mapped forecast in that span."""
    spec = SPLITS[split]
    out = {}
    for name, (values, rules) in spec["streams"].items():
        if not isinstance(rules, list):
            continue
        stated = moved = 0.0
        for seed in spec["seeds"]:
            points = stream(values, rules, seed, spec["n"], spec["burst"])
            forecasts = mapped(points, spec["lag"], options)
            cut = slice(spec["n"] // 2, spec["n"] // 2 + span)
            stated += _score(points[cut], [p for p, _, _ in points[cut]])["distance"]
            moved += _score(points[cut], forecasts[cut])["distance"]
        k = len(spec["seeds"])
        out[name] = {"stated": round(stated / k, 6), "mapped": round(moved / k, 6)}
    return out


def summary(result: dict) -> dict:
    """One line for a whole split: the mean scores over its streams, and the worst change the mapping made to any
    stream at any prefix (positive = worse than the stated forecast)."""
    names = list(result)
    mean = {label: {key: round(sum(result[n]["all"][label][key] for n in names) / len(names), 6)
                    for key in ("brier", "distance")} for label in ("stated", "mapped")}
    worst = {key: max(((round(result[n][upto]["mapped"][key] - result[n][upto]["stated"][key], 6), n, upto)
                       for n in names for upto in result[n]), key=lambda item: item[0])
             for key in ("brier", "distance")}
    return {"mean": mean, "worst_change": {key: {"change": w[0], "stream": w[1], "first": w[2]}
                                           for key, w in worst.items()}}


def select() -> list[dict]:
    """Every setting of GRID on the development streams, best first: lowest mean distance to the true probability
    among those that never make a stream worse by more than 0.002 at any prefix; the others follow, marked."""
    rows = []
    for minimum in GRID["minimum"]:
        for window in GRID["window"]:
            for prior_shift in GRID["prior_shift"]:
                for prior_tilt in GRID["prior_tilt"]:
                    options = {"minimum": minimum, "window": window, "prior_shift": prior_shift,
                               "prior_tilt": prior_tilt}
                    s = summary(evaluate("development", options))
                    rows.append({"options": options, "mean_distance": s["mean"]["mapped"]["distance"],
                                 "mean_brier": s["mean"]["mapped"]["brier"],
                                 "worst_distance_change": s["worst_change"]["distance"],
                                 "harmless": s["worst_change"]["distance"]["change"] <= 0.002})
    rows.sort(key=lambda r: (not r["harmless"], r["mean_distance"], str(r["options"])))
    return rows


def report(options: dict | None = None) -> dict:
    options = options or CHOSEN
    out = {"options": options}
    for split in SPLITS:
        result = evaluate(split, options)
        out[split] = {"lag": SPLITS[split]["lag"], "predictions_per_stream": SPLITS[split]["n"],
                      "seeds": len(SPLITS[split]["seeds"]), "burst": SPLITS[split]["burst"],
                      "summary": summary(result),
                      "the_50_predictions_after_a_change": after_a_change(split, options), "streams": result}
    earlier = options | {"minimum": 10}
    out["bursts_with_a_minimum_of_10"] = {"options": earlier, "summary": summary(evaluate("bursts", earlier))}
    return out
