"""Exact belief inference, change detection and explanations for one calibration epoch.

Hidden bits: the mechanism w, one alignment bit s_r per camera root r and one convention bit t_p
per word partner p, all with uniform priors. A camera reading predicts (bit XOR action) == w XOR s_r
and a word reading predicts (bit XOR action) == w XOR t_p, each with error .10. Reference readings
are grouped by calibration root and scored with `lab.source_groups.group_log_likelihood`, which
integrates out the shared fixture polarity of that root.

Given w the factors are independent, so inference is exact and costs O(events):
log L(w) = sum over camera roots and partners of log sum_h 1/2 * prod lik + sum over reference roots
of the group log-likelihood at w. Beliefs are computed on read from active evidence (ADR-006);
`belief_revisions` is only an audit trail of semantic changes.
"""
import json
import math

import numpy as np

from ..lab.core import CAMERA, SPEAKER, STATES, probabilities, update
from ..lab.source_groups import group_log_likelihood
from . import store
from .errors import NotFound

READING_ERROR = .10
REFERENCE_FLIP = .15
REFERENCE_ERROR = .10
IDENTIFIABLE_MARGIN = .37
CHANGE_WINDOW = 64
CHANGE_THRESHOLD = math.log(19)
AMBIGUITY_MARGIN = 1.0
MIN_AFTER_CHANGE = 3
EXPLAIN_EVENTS = 50
LEGACY_REFERENCE_TEXT = "try a known mechanical reference from a distinct root"
LEGACY_ACT_TEXT = "test a new action and check its observed consequence"

_LOG_HIT = math.log1p(-READING_ERROR)
_LOG_MISS = math.log(READING_ERROR)
_LOG_HALF = math.log(.5)
# Rows of the legacy 8-state table whose mechanism bit is 0 and 1 (group_log_likelihood depends on w only).
_W_ROW = (int(np.argmax(STATES[:, 0] == 0)), int(np.argmax(STATES[:, 0] == 1)))
_PREFIX = {"camera": "camera", "word": "partner"}

EVENT_COLUMNS = ("seq", "id", "epoch", "channel", "action", "bit", "root", "sample_id", "actor", "consent",
                 "provenance", "acquired_at", "recorded_at", "retention", "expires_at")


def event_dict(row):
    """Map an evidence row selected with EVENT_COLUMNS to a dict."""
    return dict(zip(EVENT_COLUMNS, row))


def require_epoch(db, epoch):
    """Raise NotFound unless the epoch exists."""
    if not isinstance(epoch, str) or db.execute("SELECT 1 FROM epochs WHERE name=?", (epoch,)).fetchone() is None:
        raise NotFound(f"Unknown epoch {epoch!r}; start it with `nsf epoch start`")


def _lse(a, b):
    high = max(a, b)
    return high + math.log1p(math.exp(-abs(a - b)))


def _logistic(x):
    """1 / (1 + exp(-x)) without overflow; exactly 0.5 at x == 0."""
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _bernoulli(n0, n1, predicted):
    matches = n1 if predicted else n0
    return matches * _LOG_HIT + (n0 + n1 - matches) * _LOG_MISS


def _load(db, epoch):
    """All rows of the epoch, and the learning events: consent='learn', copies of one sample counted once."""
    rows = [event_dict(r) for r in db.execute(
        f"SELECT {','.join(EVENT_COLUMNS)} FROM evidence WHERE epoch=? ORDER BY seq", (epoch,))]
    seen, learning, duplicates, memory_only = set(), [], 0, 0
    for event in rows:
        if event["consent"] != "learn":
            memory_only += 1
            continue
        if event["sample_id"] is not None:
            key = (event["channel"], event["root"], event["sample_id"])
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
        learning.append(event)
    counts = {"total": len(rows), "learning": len(learning), "memory_only": memory_only,
              "duplicates_ignored": duplicates}
    return counts, learning


def _readings(events):
    return [(e["channel"], e["root"], e["bit"] ^ e["action"]) for e in events]


def _infer(readings):
    """Exact posterior from (channel, root, corrected reading) triples.

    Returns log_marginal (log evidence under the uniform priors), p_w = P(w=1 | data), and for each
    camera/word factor (channel, root) -> ([P(h=1 | w=0, data), P(h=1 | w=1, data)], n).
    """
    tallies = {}
    for channel, root, reading in readings:
        tallies.setdefault((channel, root), [0, 0])[reading] += 1
    log_w = [0.0, 0.0]
    conditional = {}
    for (channel, root), (n0, n1) in tallies.items():
        if channel == "reference":
            group = np.array([[1]] * n1 + [[0]] * n0)
            values = group_log_likelihood(group, REFERENCE_FLIP, REFERENCE_ERROR)[0]
            for w in (0, 1):
                log_w[w] += float(values[_W_ROW[w]])
            continue
        given_w = []
        for w in (0, 1):
            ll = [_bernoulli(n0, n1, w ^ h) for h in (0, 1)]
            log_w[w] += _LOG_HALF + _lse(ll[0], ll[1])
            given_w.append(_logistic(ll[1] - ll[0]))
        conditional[(channel, root)] = (given_w, n0 + n1)
    return {"log_marginal": _LOG_HALF + _lse(log_w[0], log_w[1]), "p_w": _logistic(log_w[1] - log_w[0]),
            "conditional": conditional}


def _factor_p(p_w, given_w):
    """P(h=1) = sum_w P(w) P(h=1 | w, data)."""
    return (1 - p_w) * given_w[0] + p_w * given_w[1]


def _relation_p(p_w, given_w):
    """P(w XOR h = 1): the quantity camera or word readings observe directly."""
    return (1 - p_w) * given_w[0] + p_w * (1 - given_w[1])


def _identifiable(p):
    return abs(p - .5) >= IDENTIFIABLE_MARGIN


def _legacy(epoch, learning):
    """The prototype's 8-state status, computed exactly as legacy_live.status (records ordered by id)."""
    scores = np.zeros((1, 8))
    references = {}
    records = sorted(learning, key=lambda e: e["id"])
    for event in records:
        corrected = np.array([event["bit"] ^ event["action"]])
        if event["channel"] == "reference":
            references.setdefault(event["root"], []).append(corrected)
        else:
            update(scores, CAMERA if event["channel"] == "camera" else SPEAKER, corrected, READING_ERROR)
    for group in references.values():
        scores += group_log_likelihood(np.array(group), REFERENCE_FLIP, REFERENCE_ERROR)
    states = probabilities(scores)[0]
    margins = states @ STATES
    return {"epoch": epoch, "stored_observations": len(records), "reference_roots": len(references),
            "probability_inverted": dict(zip(["mechanism", "camera", "speaker_convention"], margins.tolist())),
            "state_probabilities_in_binary_order": states.tolist(), "top_state_probability": float(states.max()),
            "suggested_next_observation": LEGACY_REFERENCE_TEXT if abs(margins[0] - .5) < IDENTIFIABLE_MARGIN
            else LEGACY_ACT_TEXT}


def _mechanism_why(p, reference_roots, learning):
    if not learning:
        return "no learning evidence yet"
    if reference_roots == 0:
        return ("no reference evidence: camera and word readings only constrain mechanism XOR alignment and "
                "mechanism XOR convention, so the joint flip symmetry (every hidden bit flipped together) explains "
                "them equally well; mechanism is unidentifiable without an independent reference")
    return (f"reference evidence from {reference_roots} root(s) leaves p_inverted={p:.3f}, within "
            f"{IDENTIFIABLE_MARGIN} of 0.5; " + LEGACY_REFERENCE_TEXT)


def _factor_why(p, relation, n, mechanism_identifiable):
    if not mechanism_identifiable and abs(relation - .5) >= IDENTIFIABLE_MARGIN:
        return (f"{n} reading(s) fix mechanism XOR this bit (P={relation:.3f}), but the bit inherits the "
                f"mechanism's joint flip ambiguity")
    return f"{n} reading(s) leave p_inverted={p:.3f}, within {IDENTIFIABLE_MARGIN} of 0.5 (conflicting or too few)"


def _evaluate(db, epoch):
    require_epoch(db, epoch)
    counts, learning = _load(db, epoch)
    inference = _infer(_readings(learning))
    p_w = inference["p_w"]
    identifiable = _identifiable(p_w)
    reference_roots = sorted({e["root"] for e in learning if e["channel"] == "reference"})
    groups = {"camera": {}, "word": {}}
    unidentifiable = []
    if not identifiable:
        unidentifiable.append({"scope": "mechanism", "why": _mechanism_why(p_w, len(reference_roots), learning)})
    for (channel, root), (given_w, n) in sorted(inference["conditional"].items()):
        p = _factor_p(p_w, given_w)
        groups[channel][root] = {"p_inverted": p, "n": n}
        if not _identifiable(p):
            scope = f"{_PREFIX[channel]}:{root}"
            unidentifiable.append({"scope": scope, "why": _factor_why(p, _relation_p(p_w, given_w), n, identifiable)})
    simple = len(groups["camera"]) <= 1 and len(groups["word"]) <= 1
    result = {
        "epoch": epoch, "state_version": store.state_version(db), "evidence": counts,
        "mechanism": {"p_inverted": p_w, "identifiable": identifiable},
        "sensors": groups["camera"], "partners": groups["word"], "reference_roots": len(reference_roots),
        "unidentifiable": unidentifiable, "legacy": _legacy(epoch, learning) if simple else None,
    }
    return result, learning, inference


def compute(db, epoch):
    """Exact posterior over the mechanism, each camera root's alignment and each partner's convention.

    Uses active evidence with consent='learn'; copies sharing (channel, root, sample_id) count once.
    With at most one camera root and one partner, "legacy" reproduces legacy_live.status().
    """
    return _evaluate(db, epoch)[0]


def _change_scopes(window):
    scopes = {"mechanism": list(range(len(window)))}
    for i, event in enumerate(window):
        if event["channel"] in _PREFIX:
            scopes.setdefault(f"{_PREFIX[event['channel']]}:{event['root']}", []).append(i)
    return scopes


def detect_change(db, epoch):
    """Single-factor changepoint test over the last 64 learning events.

    A change in scope X at tau flips that hidden bit for X's events from tau on, which is the same as
    inverting those readings. Score = log marginal-likelihood gain over no change - log(#changepoints
    tried, across all scopes). Each candidate needs >= 1 affected event before tau and >= 3 from tau on.
    """
    require_epoch(db, epoch)
    window = _load(db, epoch)[1][-CHANGE_WINDOW:]
    readings = _readings(window)
    base = _infer(readings)["log_marginal"]
    best, tried = {}, 0
    for scope, affected in _change_scopes(window).items():
        for k in range(1, len(affected) - MIN_AFTER_CHANGE + 1):
            flipped = set(affected[k:])
            modified = [(c, r, x ^ (i in flipped)) for i, (c, r, x) in enumerate(readings)]
            gain = _infer(modified)["log_marginal"] - base
            tried += 1
            if scope not in best or gain > best[scope][0]:
                best[scope] = (gain, window[affected[k]]["seq"])
    result = {"suspected": False, "scope": None, "since_seq": None, "llr": 0.0, "alternatives": [],
              "ambiguous": False, "window": len(window), "tried": tried}
    if not best:
        return result
    penalty = math.log(tried)
    order = {"mechanism": 0, "camera": 1, "partner": 2}
    ranked = sorted(best.items(), key=lambda item: (-item[1][0], order[item[0].split(":")[0]], item[0]))
    top_scope, (top_gain, since) = ranked[0]
    llr = top_gain - penalty
    suspected = llr > CHANGE_THRESHOLD
    result.update(llr=llr, alternatives=[{"scope": s, "llr": g - penalty} for s, (g, _) in ranked[1:]])
    if suspected:
        runner_up = ranked[1][1][0] - penalty if len(ranked) > 1 else None
        result.update(suspected=True, scope=top_scope, since_seq=since,
                      ambiguous=runner_up is not None and llr - runner_up < AMBIGUITY_MARGIN)
    return result


def _split(events, predicted):
    agree = [e for e in events if predicted is not None and e["bit"] ^ e["action"] == predicted]
    disagree = [e for e in events if predicted is not None and e["bit"] ^ e["action"] != predicted]
    return agree, disagree


def _map(p):
    """MAP value of a binary factor; None for a tie (within rounding of exact symmetry)."""
    return None if abs(p - .5) < 1e-9 else int(p > .5)


def _factor(scope, p, events, predicted, uncertainty):
    supporting, counter = _split(events, predicted)
    return {"scope": scope, "p_inverted": p, "supporting": supporting[-EXPLAIN_EVENTS:],
            "counterevidence": counter[-EXPLAIN_EVENTS:], "supporting_total": len(supporting),
            "counterevidence_total": len(counter), "uncertainty": uncertainty}


def explain(db, epoch):
    """Beliefs with, per factor, the events that support or contradict its MAP reading and why it is uncertain.

    Mechanism uses reference events against MAP w. Camera and word factors use their own events against
    the MAP of the relation they observe (w XOR bit), which stays defined when w is not identifiable.
    Event lists keep the latest 50; *_total gives the full counts.
    """
    result, learning, inference = _evaluate(db, epoch)
    p_w = inference["p_w"]
    identifiable = result["mechanism"]["identifiable"]
    references = [e for e in learning if e["channel"] == "reference"]
    if not learning:
        why = "no learning evidence yet; act and record camera/word reports"
    elif not references:
        why = ("only camera and word evidence: the joint flip symmetry makes the mechanism unidentifiable "
               "without an independent reference")
    elif identifiable:
        why = (f"{len(references)} reference reading(s) from {result['reference_roots']} root(s) pin the "
               f"mechanism at p_inverted={p_w:.3f}")
    else:
        why = _mechanism_why(p_w, result["reference_roots"], learning)
    factors = [_factor("mechanism", p_w, references, _map(p_w), why)]
    for (channel, root), (given_w, n) in sorted(inference["conditional"].items()):
        scope = f"{_PREFIX[channel]}:{root}"
        p = _factor_p(p_w, given_w)
        relation = _relation_p(p_w, given_w)
        if _identifiable(p):
            why = f"{n} reading(s) give p_inverted={p:.3f}"
        else:
            why = _factor_why(p, relation, n, identifiable)
        events = [e for e in learning if e["channel"] == channel and e["root"] == root]
        factors.append(_factor(scope, p, events, _map(relation), why))
    rows = db.execute("SELECT id,state_version,created_at,cause,summary FROM belief_revisions WHERE epoch=? "
                      "ORDER BY id DESC LIMIT 20", (epoch,)).fetchall()
    revisions = [{"id": i, "state_version": v, "created_at": t, "cause": c, "map": _signature_text(s)}
                 for i, v, t, c, s in reversed(rows)]
    return {"beliefs": result, "factors": factors, "revisions": revisions, "change": detect_change(db, epoch)}


def _signature(result):
    return {"mechanism": _map(result["mechanism"]["p_inverted"]),
            "identifiable": result["mechanism"]["identifiable"],
            "sensors": {r: _map(v["p_inverted"]) for r, v in result["sensors"].items()},
            "partners": {r: _map(v["p_inverted"]) for r, v in result["partners"].items()}}


_EMPTY_SIGNATURE = {"mechanism": None, "identifiable": False, "sensors": {}, "partners": {}}


def _signature_text(summary):
    """MAP signature stored in a revision summary, or None when the summary is not a compute() result."""
    try:
        return _signature(json.loads(summary))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def record_revision(db, epoch, now, cause):
    """Caller owns the transaction. Insert belief_revisions(cause) when the MAP assignment of the mechanism,
    any sensor or any partner, or the mechanism's identifiability, differs from the epoch's previous revision.
    Returns the new revision id, or None when nothing changed.
    """
    result = compute(db, epoch)
    row = db.execute("SELECT summary FROM belief_revisions WHERE epoch=? ORDER BY id DESC LIMIT 1",
                     (epoch,)).fetchone()
    previous = _signature_text(row[0]) if row else _EMPTY_SIGNATURE
    if previous == _signature(result):
        return None
    summary = json.dumps(result, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES(?,?,?,?,?)",
                      (epoch, result["state_version"], now, cause, summary)).lastrowid
