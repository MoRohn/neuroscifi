"""Scoring functions of the cognitive benchmark. Pure, deterministic, standard library only; each is tested against
hand-computed cases in tests/acceptance/test_cognition_bench.py. No family scores itself: every score here compares
an output with a ground truth the family's generator wrote before the system under test ran.
"""
from __future__ import annotations

import math
import statistics


# ---- information -----------------------------------------------------------------------------------------------------

def entropy(p: float) -> float:
    """Binary entropy in bits."""
    p = min(1.0, max(0.0, float(p)))
    if p in (0.0, 1.0):
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def report_gain(p: float, reliability: float) -> float:
    """Mutual information (bits) between a binary truth with P(x=1) = p and one report that equals x with
    probability `reliability`: H(p r + (1-p)(1-r)) - H(r). A report of reliability 0.5 teaches nothing."""
    q = p * reliability + (1 - p) * (1 - reliability)
    return max(0.0, entropy(q) - entropy(reliability))


def report_posterior(p: float, reliability: float, report: int) -> float:
    """P(x=1 | report) for the channel of report_gain."""
    like1 = reliability if report == 1 else 1 - reliability
    like0 = 1 - reliability if report == 1 else reliability
    z = p * like1 + (1 - p) * like0
    return p if z == 0 else p * like1 / z


def readings_gain(p: float, error: float, k: int) -> float:
    """Expected information gain (bits) about a binary x with P(x=1) = p from k independent readings that each
    equal x with probability 1 - error (the belief model's reading channel): H(p) - E[H(p | readings)]."""
    if k <= 0:
        return 0.0
    out = entropy(p)
    for ones in range(k + 1):
        ways = math.comb(k, ones)
        l1 = (1 - error) ** ones * error ** (k - ones)
        l0 = error ** ones * (1 - error) ** (k - ones)
        z = p * l1 + (1 - p) * l0
        if z > 0:
            out -= ways * z * entropy(p * l1 / z)
    return max(0.0, out)


def log_score_gain(before: float, after: float, truth: int) -> float:
    """Realised information about the truth (bits): log2 P_after(truth) - log2 P_before(truth), probabilities clipped
    to [1e-9, 1 - 1e-9]. Positive when the belief moved towards what is true; its expectation is the information
    gain only when the reports really carry information (for pure noise it is zero or negative on average)."""
    clip = lambda q: min(1 - 1e-9, max(1e-9, q))  # noqa: E731
    pb = clip(before if truth == 1 else 1 - before)
    pa = clip(after if truth == 1 else 1 - after)
    return math.log2(pa) - math.log2(pb)


# ---- threshold breaches: episodes, lead time, false alarms ----------------------------------------------------------

def episodes(flags) -> list[tuple[int, int]]:
    """Runs of True as [start, end) tick intervals."""
    out, start = [], None
    for t, flag in enumerate(flags):
        if flag and start is None:
            start = t
        elif not flag and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def breach_report(reference, regulated, holds, *, horizon: int) -> dict:
    """Compare the breaches of one channel without regulation (`reference`, a list of booleans per tick) with those
    under a regulator (`regulated`), given the ticks at which the regulator held background work (`holds`).

    - avoided: reference episodes with no regulated breach tick inside them (the breach did not happen at all)
    - lead_ticks: for each reference episode starting at s, s - (first hold in [s - horizon, s]); episodes with no
      hold in that window have no lead (None) and count as "not anticipated"
    - false_alarm_ticks: hold ticks with no reference episode starting within the next `horizon` ticks and none in
      progress; false_alarm_share = those / all hold ticks (None without holds)"""
    ref = episodes(reference)
    hold_ticks = [t for t, h in enumerate(holds) if h]
    avoided, leads = 0, []
    for s, e in ref:
        if not any(regulated[s:e]):
            avoided += 1
        early = [t for t in hold_ticks if s - horizon <= t <= s]
        leads.append(s - early[0] if early else None)
    warned = set()
    for s, e in ref:
        warned.update(range(max(0, s - horizon), e))
    false_alarms = sum(1 for t in hold_ticks if t not in warned)
    known = [x for x in leads if x is not None]
    return {"reference_episodes": len(ref), "reference_ticks": sum(map(bool, reference)),
            "episodes": len(episodes(regulated)), "ticks": sum(map(bool, regulated)), "avoided": avoided,
            "avoided_share": avoided / len(ref) if ref else None,
            "anticipated": len(known), "mean_lead_ticks": statistics.fmean(known) if known else None,
            "hold_ticks": len(hold_ticks), "false_alarm_ticks": false_alarms,
            "false_alarm_share": false_alarms / len(hold_ticks) if hold_ticks else None}


# ---- probabilistic forecasts: Brier with Murphy's decomposition, ECE ------------------------------------------------

def brier(forecasts, outcomes) -> float | None:
    pairs = list(zip(forecasts, outcomes))
    return sum((f - o) ** 2 for f, o in pairs) / len(pairs) if pairs else None


def murphy(forecasts, outcomes) -> dict | None:
    """Brier = reliability - resolution + uncertainty, grouping forecasts by their distinct value (rounded to 1e-6),
    so the identity is exact. Lower reliability is better calibration; higher resolution is better discrimination."""
    pairs = list(zip(forecasts, outcomes))
    if not pairs:
        return None
    n = len(pairs)
    base = sum(o for _, o in pairs) / n
    groups: dict = {}
    for f, o in pairs:
        groups.setdefault(round(f, 6), []).append(o)
    rel = sum(len(os) * (f - sum(os) / len(os)) ** 2 for f, os in groups.items()) / n
    res = sum(len(os) * (sum(os) / len(os) - base) ** 2 for os in groups.values()) / n
    unc = base * (1 - base)
    return {"brier": brier(forecasts, outcomes), "reliability": rel, "resolution": res, "uncertainty": unc,
            "groups": len(groups), "n": n, "base_rate": base}


def ece(forecasts, outcomes, *, bins: int = 10, mass: bool = False) -> float | None:
    """Expected calibration error: sum over bins of (n_b / N) |mean outcome - mean forecast|. Equal-width bins over
    [0, 1] by default (a forecast of exactly 1 falls in the last bin); with mass=True, `bins` bins of (nearly)
    equal count in forecast order. Never report it without Brier: a constant forecast at the base rate has ECE 0."""
    pairs = sorted(zip(forecasts, outcomes))
    n = len(pairs)
    if not n:
        return None
    if mass:
        size = max(1, math.ceil(n / bins))
        groups = [pairs[i:i + size] for i in range(0, n, size)]
    else:
        buckets: dict = {}
        for f, o in pairs:
            buckets.setdefault(min(bins - 1, int(f * bins)), []).append((f, o))
        groups = list(buckets.values())
    return sum(len(g) / n * abs(sum(o for _, o in g) / len(g) - sum(f for f, _ in g) / len(g)) for g in groups)


# ---- selective prediction --------------------------------------------------------------------------------------------

def risk_coverage(confidences, correct) -> dict:
    """The risk-coverage curve of answering only the most confident items: items in decreasing confidence, tied
    confidences entered together (so the curve does not depend on the order of ties). Points
    {coverage, risk, threshold}; AURC = mean over k = 1..N of the risk when the k most confident are answered
    (items of a tie group take the risk at the group's end); coverage at risk <= 0.05 and <= 0.10."""
    items = sorted(zip(confidences, correct), key=lambda x: -x[0])
    n = len(items)
    if not n:
        return {"points": [], "aurc": None, "n": 0}
    points, per_item, errors, i = [], [], 0, 0
    while i < n:
        j = i
        while j < n and items[j][0] == items[i][0]:
            errors += 0 if items[j][1] else 1
            j += 1
        risk = errors / j
        points.append({"coverage": j / n, "risk": risk, "threshold": items[i][0]})
        per_item += [risk] * (j - i)
        i = j

    def at(limit):
        ok = [p["coverage"] for p in points if p["risk"] <= limit + 1e-12]
        return max(ok) if ok else 0.0
    return {"points": points, "aurc": sum(per_item) / n, "coverage_at_risk_0.05": at(0.05),
            "coverage_at_risk_0.10": at(0.10), "full_coverage_risk": points[-1]["risk"], "n": n}


def auroc(scores, labels) -> float | None:
    """P(score of a random positive > score of a random negative), ties counting 1/2. None without both classes."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


# ---- answers with uncertainty ----------------------------------------------------------------------------------------

# Stated before any answer was scored with them (Wave 1 review B3). An answer is within tolerance when |value - truth|
# <= TOLERANCE x the question's scale (a tenth of the outcome's standard deviation; 0.1 for a probability). The
# interval scored is the central (1 - INTERVAL_ALPHA) interval of a normal with the answer's sd.
TOLERANCE = 0.1
# A second, stricter tolerance, reported always beside TOLERANCE and never chosen per result. Added at the re-review of
# the Wave 1 fix round, after the results at TOLERANCE were known (held-out learned-graph errors reached 0.092 against
# 0.1). Neither tolerance can be shown to predate the results: TOLERANCE arrived in the same commit as them.
STRICT_TOLERANCE = 0.05
INTERVAL_ALPHA = 0.05
_Z = 1.959963984540054          # the normal's 0.975 quantile


def interval_score(value: float, sd: float | None, truth: float, scale: float = 1.0,
                   alpha: float = INTERVAL_ALPHA) -> float:
    """The interval score (Winkler 1972; Gneiting and Raftery 2007, a proper scoring rule) of the central
    (1 - alpha) interval value +- z sd, in units of `scale`: its width, plus 2/alpha times how far the truth lies
    outside it. Lower is better; a uselessly wide interval pays its width, an overconfident one pays the miss. sd None
    is a point answer (width 0)."""
    half = _Z * (sd or 0.0)
    lo, hi = value - half, value + half
    s = (hi - lo) + 2.0 / alpha * max(0.0, lo - truth) + 2.0 / alpha * max(0.0, truth - hi)
    return s / (scale or 1.0)


def answer_scores(rows) -> dict:
    """rows: {"answerable": bool, "truth": float | None, "value": float | None, "sd": float | None, "scale": float,
    optional "identified_by": "front_door"}. value None = the system said it cannot answer.

    Headline (Wave 1 review B3, corrected at its re-review): answered_within_tolerance = share of EVERY answerable
    query, the front-door ones included (answerable_with_front_door of them), answered with |value - truth| <=
    TOLERANCE x scale; a decline, or any non-answer, of an answerable query is not within tolerance, so a system that
    declines an answerable question cannot reach 1.0. answered_within_strict_tolerance is the same at
    STRICT_TOLERANCE, reported always; max_relative_error is the largest |value - truth| / scale over every answered
    query (how close the answers sit to the tolerance's edge). interval_score = the mean interval_score() over the
    answered ones outside the front door (with interval_score_answered, how many).

    Rows marked identified_by="front_door" (a hidden common cause, but a front-door adjustment identifies the effect
    from what the query gives) are also reported on their own: front_door_declined counts the declines (the engine
    does no front-door adjustment), front_door_within_tolerance is the share of all front-door rows answered within
    tolerance. answered_within_tolerance_without_front_door is the figure before the re-review: the share of the
    answerable queries other than the front-door ones (`answerable` of them) answered within tolerance. It is a
    secondary figure, never the headline: it leaves out answerable questions the engine cannot answer.

    Kept for comparison with the recorded files, under names that cannot be mistaken for the headline:
    answered_share (was "coverage": answerable queries answered at all, however wrong), mean_relative_error
    (|value - truth| / scale on answered queries), interval_coverage (share of those with |value - truth| <= 2 sd; sd
    None counts as not covered) and declined_confounded_share (was "abstention_correct": share of the queries NOT
    answerable before the front-door relabelling that were declined; it is not a correctness score)."""
    front = [r for r in rows if r.get("identified_by") == "front_door"]
    main = [r for r in rows if r.get("identified_by") != "front_door"]
    answerable = [r for r in main if r["answerable"]]
    answered = [r for r in answerable if r["value"] is not None]
    declined_before = [r for r in rows if not r["answerable"] or r.get("identified_by") == "front_door"]
    rel = lambda r: abs(r["value"] - r["truth"]) / (r["scale"] or 1.0)  # noqa: E731
    errors = [rel(r) for r in answered]
    covered = [r["sd"] is not None and abs(r["value"] - r["truth"]) <= 2 * r["sd"] for r in answered]
    scores = [interval_score(r["value"], r["sd"], r["truth"], r["scale"]) for r in answered]
    front_answered = [r for r in front if r["value"] is not None]
    every = answerable + [r for r in front if r["answerable"]]            # every answerable query, front door too
    every_errors = [rel(r) for r in every if r["value"] is not None]      # a decline has no error and is not within
    within = lambda tol: (sum(e <= tol + 1e-12 for e in every_errors) / len(every)  # noqa: E731
                          if every else None)
    return {"queries": len(rows), "answerable": len(answerable), "answerable_with_front_door": len(every),
            "tolerance": TOLERANCE, "strict_tolerance": STRICT_TOLERANCE,
            "answered_within_tolerance": within(TOLERANCE),
            "answered_within_strict_tolerance": within(STRICT_TOLERANCE),
            "max_relative_error": max(every_errors) if every_errors else None,
            "answered_within_tolerance_without_front_door": (sum(e <= TOLERANCE + 1e-12 for e in errors)
                                                             / len(answerable) if answerable else None),
            "interval_score": statistics.fmean(scores) if scores else None,
            "interval_score_answered": len(scores),
            "answered_share": len(answered) / len(answerable) if answerable else None,
            "mean_relative_error": statistics.fmean(errors) if errors else None,
            "interval_coverage": sum(covered) / len(covered) if covered else None,
            "front_door": len(front),
            "front_door_declined": len(front) - len(front_answered),
            "front_door_within_tolerance": (sum(rel(r) <= TOLERANCE + 1e-12 for r in front_answered) / len(front)
                                            if front else None),
            "unanswerable": len(rows) - len(answerable) - len(front),
            "declined_confounded_share": (sum(r["value"] is None for r in declined_before) / len(declined_before)
                                          if declined_before else None)}


# ---- across seeds ----------------------------------------------------------------------------------------------------

def spread(values) -> dict:
    """Mean, sample standard deviation, min, max and n of the non-None values (None when there are none)."""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"mean": None, "sd": None, "min": None, "max": None, "n": 0}
    return {"mean": statistics.fmean(xs), "sd": statistics.stdev(xs) if len(xs) > 1 else 0.0, "min": min(xs),
            "max": max(xs), "n": len(xs)}


def rounded(value, digits: int = 6):
    """JSON-friendly copy with floats rounded (so repeated runs compare equal and files stay readable)."""
    if isinstance(value, float):
        return round(value, digits) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: rounded(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(v, digits) for v in value]
    return value


# Wave 4 lane D: an evidence file holds summaries per world and seed by default; the per-cycle histories (Wave 3 added
# about 45 MB of them) only when a harness is asked for them (--full-history). Every pre-registered rule reads the
# summaries (tests/acceptance/test_wave4_d.py checks the committed Wave 3 runs give the same verdicts without them).
HISTORY_KEYS = ("history",          # each subject's newest held version, cycle by cycle, per seed (cycles_s)
                "series_mean")      # the mean over seeds of each metric, cycle by cycle (cycles, its references)


def summary(value):
    """A copy without the per-cycle histories: the HISTORY_KEYS, and the (subject, cycle) list beside the class counts
    of cycles_s.item_changes ("items" next to "classes"; the counts stay)."""
    if isinstance(value, dict):
        return {k: summary(v) for k, v in value.items()
                if k not in HISTORY_KEYS and not (k == "items" and "classes" in value)}
    if isinstance(value, (list, tuple)):
        return [summary(v) for v in value]
    return value


def dumps(value, levels: int = 4) -> str:
    """JSON with the outer `levels` levels one member per line and anything deeper on one line: indent=1 everywhere
    made the Wave 3 files about three times their content. json.loads reads it back unchanged."""
    import json

    def out(v, level):
        if level >= levels or not isinstance(v, (dict, list)) or not v:
            return json.dumps(v, separators=(",", ":"))
        pad = " " * (level + 1)
        if isinstance(v, dict):
            body = ",\n".join(f"{pad}{json.dumps(str(k))}: {out(x, level + 1)}" for k, x in v.items())
            return "{\n" + body + "\n" + " " * level + "}"
        return "[\n" + ",\n".join(pad + out(x, level + 1) for x in v) + "\n" + " " * level + "]"
    return out(value, 0)
