"""Exact belief inference, change detection, epoch matching and explanations for one calibration epoch.

Hidden bits: the mechanism w, one alignment bit s_r per camera root r and one convention bit t_p
per word partner p, all with uniform priors. A camera reading predicts (bit XOR action) == w XOR s_r
and a word reading predicts (bit XOR action) == w XOR t_p, each with error .10. Reference readings
are grouped by calibration root and scored with `lab.source_groups.group_log_likelihood`, which
integrates out the shared fixture polarity of that root.

Confirmed changes (scope-limited relearning). When the owner confirms a change of scope X since
evidence #S (`executive.resolve(..., confirm=True)`), X gets a NEW hidden bit with a uniform prior for
its events with seq >= S: camera root r becomes the segments r@0 and r@S, and every other factor keeps
pooling all of its evidence, so a partner's meaning and the mechanism carry over. A confirmed mechanism
change makes w piecewise (w_0 before S, w_1 from S on). The 2^M mechanism-segment assignments are
enumerated exactly (M <= 6); inside each assignment the camera and partner factors stay independent.
Reference roots keep one fixture polarity across mechanism segments. With no confirmed change the model
is the original one and the arithmetic is the same, operation for operation.

Continuing an earlier setup. When the owner answers "continue there" to an epoch question
(`executive.resolve(..., confirm=True)` on scope 'epoch:<E>'), epoch_links records which segment of each of
E's factors the new epoch continues. compute() then pools E's learning events in those segments (camera and
word events of the linked camera/partner segments and reference events, all inside the linked mechanism
segment) with the new epoch's own evidence, as if they were the new epoch's earliest events. The pool is
read on every call, so forgetting source evidence changes the linked beliefs at once.

Inference reads SQL aggregates (counts per channel, root, corrected reading and segment, with copies of
one sample counted once), so its cost does not grow with the Python work per event (ADR-006: beliefs
are still computed on read; `belief_revisions` is only an audit trail of semantic changes).
"""
from bisect import bisect_right
import functools
import itertools
import json
import math

import numpy as np

from ..lab.core import CAMERA, SPEAKER, STATES, probabilities
from ..lab.source_groups import group_log_likelihood
from . import store
from .errors import NotFound, ValidationError

READING_ERROR = .10
REFERENCE_FLIP = .15
REFERENCE_ERROR = .10
IDENTIFIABLE_MARGIN = .37
CHANGE_WINDOW = 64
CHANGE_THRESHOLD = math.log(19)
AMBIGUITY_MARGIN = 1.0
MIN_AFTER_CHANGE = 3
EXPLAIN_EVENTS = 50
MAX_MECHANISM_SEGMENTS = 6
MATCH_EVENTS = 12
# Declared before measurement: name an earlier setup only when it is at least 3:1 more likely than a new setup
# and every other earlier configuration combined.
MATCH_POSTERIOR = .75
LEGACY_REFERENCE_TEXT = "try a known mechanical reference from a distinct root"
LEGACY_ACT_TEXT = "test a new action and check its observed consequence"

_LOG_HIT = math.log1p(-READING_ERROR)
_LOG_MISS = math.log(READING_ERROR)
_LOG_HALF = math.log(.5)
_LOG_KEEP = math.log1p(-REFERENCE_FLIP)
_LOG_FLIP = math.log(REFERENCE_FLIP)
# Rows of the legacy 8-state table whose mechanism bit is 0 and 1 (group_log_likelihood depends on w only).
_W_ROW = (int(np.argmax(STATES[:, 0] == 0)), int(np.argmax(STATES[:, 0] == 1)))
_PREFIX = {"camera": "camera", "word": "partner"}
_CHANNEL = {"camera": "camera", "partner": "word"}

EVENT_COLUMNS = ("seq", "id", "epoch", "channel", "action", "bit", "root", "sample_id", "actor", "consent",
                 "provenance", "acquired_at", "recorded_at", "retention", "expires_at")

# A learning event: consent 'learn', and the first learning copy of its (channel, root, sample_id).
_LEARNING = ("consent='learn' AND (sample_id IS NULL OR seq=(SELECT min(p.seq) FROM evidence p{hint} "
             "WHERE p.epoch=evidence.epoch AND p.consent='learn' AND p.sample_id=evidence.sample_id "
             "AND p.channel=evidence.channel AND p.root=evidence.root))")

# Owned by this module and created by schema migration 3 (same statements; tests/instance/test_migrations.py
# checks they stay identical). Kept here so ensure_tables can run on a connection that has not migrated.
DDL = (
    "CREATE TABLE IF NOT EXISTS changepoints(id INTEGER PRIMARY KEY, epoch TEXT NOT NULL, scope TEXT NOT NULL, "
    "since_seq INTEGER NOT NULL, pending_id INTEGER, created_at REAL NOT NULL, confirmed INTEGER NOT NULL)",
    # Covering index for the per-epoch tallies (learning rows, sample dedupe and memory-only counts).
    "CREATE INDEX IF NOT EXISTS evidence_tally ON evidence(epoch, consent, sample_id, channel, root, bit, action)",
)


def ensure_tables(db):
    """Caller owns the transaction. Create the changepoints table and the tally index if missing.

    Every file opened through store.connect + migrate is at schema 3 and already has both, so on those this
    is an idempotent no-op (IF NOT EXISTS).
    """
    for statement in DDL:
        db.execute(statement)


def learning_sql(db):
    """SQL predicate on `evidence` selecting learning events.

    With the tally index present the first-copy lookup is pinned to it; otherwise SQLite prefers walking
    evidence_epoch in seq order, which is O(epoch) per copied sample.
    """
    indexed = db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='evidence_tally'").fetchone()
    return _LEARNING.format(hint=" INDEXED BY evidence_tally" if indexed else "")


def event_dict(row):
    """Map an evidence row selected with EVENT_COLUMNS to a dict."""
    return dict(zip(EVENT_COLUMNS, row))


def require_epoch(db, epoch):
    """Raise NotFound unless the epoch exists."""
    if not isinstance(epoch, str) or db.execute("SELECT 1 FROM epochs WHERE name=?", (epoch,)).fetchone() is None:
        raise NotFound(f"Unknown epoch {epoch!r}; start it with `nsf epoch start`")


def scope_of(channel, root):
    return f"{_PREFIX[channel]}:{root}"


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


@functools.lru_cache(maxsize=8192)
def _group_values(n0, n1):
    """group_log_likelihood of one reference root with n1 ones and n0 zeros, at w=0 and w=1."""
    group = np.zeros((n0 + n1, 1), dtype=np.int64)
    group[:n1] = 1
    values = group_log_likelihood(group, REFERENCE_FLIP, REFERENCE_ERROR)[0]
    return float(values[_W_ROW[0]]), float(values[_W_ROW[1]])


# ---- changepoints -------------------------------------------------------------------------------------

def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _has_changepoints(db):
    return _has_table(db, "changepoints")


def changes(db, epoch):
    """Owner answers about changes in an epoch.

    Returns {"confirmed": {scope: [since_seq, ...]}, "rejected": {scope: horizon_seq}, "records": [...]}.
    A rejection row's since_seq is the epoch's last evidence seq when the owner said "no change" (the
    horizon); it applies only while no later confirmation of that scope exists.
    """
    confirmed, rejected, records = {}, {}, []
    if _has_changepoints(db):
        for cid, scope, since, pending_id, created, ok in db.execute(
                "SELECT id,scope,since_seq,pending_id,created_at,confirmed FROM changepoints WHERE epoch=? ORDER BY id",
                (epoch,)):
            records.append({"id": cid, "scope": scope, "since_seq": since, "pending_id": pending_id,
                            "created_at": created, "confirmed": bool(ok)})
            if ok:
                confirmed.setdefault(scope, set()).add(since)
                rejected.pop(scope, None)
            else:
                rejected[scope] = since
    confirmed = {scope: sorted(seqs) for scope, seqs in confirmed.items()}
    if len(confirmed.get("mechanism", ())) >= MAX_MECHANISM_SEGMENTS:
        raise ValidationError(f"Epoch {epoch} has more than {MAX_MECHANISM_SEGMENTS} mechanism segments")
    return {"confirmed": confirmed, "rejected": rejected, "records": records}


# ---- links to an earlier setup ------------------------------------------------------------------------

def link(db, epoch):
    """The epoch's continuation of an earlier setup, or None.

    Returns {"source_epoch", "segments": {factor: segment label}, "pending_id", "created_at"}. Factors are
    'mechanism', 'camera:<root>' and 'partner:<root>'; a label is '<root or mechanism>@<since_seq>'.
    """
    if not _has_table(db, "epoch_links"):
        return None
    row = db.execute("SELECT source_epoch, segments, pending_id, created_at FROM epoch_links WHERE epoch=?",
                     (epoch,)).fetchone()
    if row is None:
        return None
    return {"source_epoch": row[0], "segments": json.loads(row[1]), "pending_id": row[2], "created_at": row[3]}


def _segment_range(confirmed, scope, label):
    """[start, end) of the segment named by label; end is None for a scope's latest segment."""
    start = int(label.rsplit("@", 1)[1])
    later = [seq for seq in confirmed.get(scope, []) if seq > start]
    return start, (later[0] if later else None)


def _linked_tallies(db, linked):
    """Learning tallies {(channel, root): [n0, n1, first_seq]} of the source epoch inside the linked segments.

    Every pooled event lies in the linked mechanism segment; camera and word events also lie in their root's
    linked segment. Roots that are not linked are not pooled.
    """
    source, segments = linked["source_epoch"], linked["segments"]
    confirmed = changes(db, source)["confirmed"]

    def within(scope, label):
        start, end = _segment_range(confirmed, scope, label)
        return ("seq>=?" + (" AND seq<?" if end is not None else ""),
                [start] + ([end] if end is not None else []))

    where, params = within("mechanism", segments.get("mechanism", "mechanism@0"))
    alternatives = ["channel='reference'"]
    for scope, label in sorted(segments.items()):
        if scope == "mechanism":
            continue
        prefix, root = scope.split(":", 1)
        clause, values = within(scope, label)
        alternatives.append(f"(channel=? AND root=? AND {clause})")
        params += [_CHANNEL[prefix], root, *values]
    tallies = {}
    for channel, root, bit, action, n, first in db.execute(
            f"SELECT channel, root, bit, action, count(*), min(seq) FROM evidence WHERE epoch=? AND {learning_sql(db)} "
            f"AND {where} AND ({' OR '.join(alternatives)}) GROUP BY channel, root, bit, action", [source, *params]):
        entry = tallies.setdefault((channel, root), [0, 0, first])
        entry[bit ^ action] += n
        entry[2] = min(entry[2], first)
    return tallies


# ---- tallies and the model ----------------------------------------------------------------------------

def _aggregate(db, epoch, bounds):
    """Learning-event tallies from SQL: {(channel, root, bucket): [n0, n1, first_seq]} and the evidence counts.

    bucket = number of `bounds` <= seq. Copies of one (channel, root, sample_id) count once (first copy).
    """
    bucket = "+".join(["(seq>=?)"] * len(bounds)) or "0"
    group = ", b" if bounds else ""
    raw = {}
    nonsample = db.execute(
        f"SELECT channel, root, bit, action, {bucket} AS b, count(*), min(seq) FROM evidence "
        f"WHERE epoch=? AND consent='learn' AND sample_id IS NULL GROUP BY channel, root, bit, action{group}",
        (*bounds, epoch)).fetchall()
    sampled = db.execute(
        f"SELECT channel, root, bit, action, {bucket} AS b, count(*), sum(copies), min(seq) FROM ("
        "SELECT channel, root, bit, action, min(seq) AS seq, count(*) AS copies FROM evidence "
        "WHERE epoch=? AND consent='learn' AND sample_id IS NOT NULL GROUP BY sample_id, channel, root) "
        f"GROUP BY channel, root, bit, action{group}", (*bounds, epoch)).fetchall()
    memory_only = db.execute("SELECT count(*) FROM evidence WHERE epoch=? AND consent='memory_only'",
                             (epoch,)).fetchone()[0]
    learning = copies = 0
    for rows, sample in ((nonsample, False), (sampled, True)):
        for row in rows:
            channel, root, bit, action, b, n = row[:6]
            first = row[-1]
            entry = raw.setdefault((channel, root, b), [0, 0, first])
            entry[bit ^ action] += n
            entry[2] = min(entry[2], first)
            learning += n
            copies += row[6] if sample else n
    counts = {"total": copies + memory_only, "learning": learning, "memory_only": memory_only,
              "duplicates_ignored": copies - learning}
    return counts, raw


def _segment(bounds, seq):
    return bisect_right(bounds, seq)


def _model(raw, confirmed, bounds):
    """Ordered factors [((channel, root, factor_segment), {mechanism_segment: (n0, n1)})] and the segment count.

    Factors are ordered by their first learning event, which with no confirmed change is the order in
    which the per-event code first met each (channel, root).
    """
    mechanism = confirmed.get("mechanism", [])
    factors = {}
    for (channel, root, b), (n0, n1, first) in raw.items():
        rep = bounds[b - 1] if b else -1      # every event of bucket b has seq >= rep and the same segments
        fseg = 0 if channel == "reference" else _segment(confirmed.get(scope_of(channel, root), []), rep)
        entry = factors.setdefault((channel, root, fseg), [first, {}])
        entry[0] = min(entry[0], first)
        counts = entry[1].setdefault(_segment(mechanism, rep), [0, 0])
        counts[0] += n0
        counts[1] += n1
    for scope, seqs in confirmed.items():
        if scope != "mechanism":
            prefix, root = scope.split(":", 1)
            factors.setdefault((_CHANNEL[prefix], root, len(seqs)), [seqs[-1], {}])  # current segment, maybe empty
    ordered = sorted(factors.items(), key=lambda item: item[1][0])
    return [(key, {m: tuple(c) for m, c in sorted(entry[1].items())}) for key, entry in ordered], len(mechanism) + 1


def _reference_ll(counts, assignment):
    """Fixture-polarity-integrated log-likelihood of one reference root whose readings span mechanism segments."""
    good = inverted = 0.0
    for m, (n0, n1) in counts.items():
        matches = n1 if assignment[m] else n0
        good += matches * _LOG_HIT + (n0 + n1 - matches) * _LOG_MISS
        inverted += matches * _LOG_MISS + (n0 + n1 - matches) * _LOG_HIT
    return _lse(_LOG_KEEP + good, _LOG_FLIP + inverted)


def _infer(factors, segments=1):
    """Exact posterior from ordered factors (see _model).

    Returns log_marginal (log evidence under the uniform priors), the posterior over mechanism-segment
    assignments, p_segments[m] = P(w_m = 1 | data), and for each camera/word factor segment
    key -> ([P(h=1 | assignment, data) for each assignment], n).
    """
    assignments = list(itertools.product((0, 1), repeat=segments))
    log_w = [0.0] * len(assignments)
    conditional = {}
    for key, counts in factors:
        if key[0] == "reference":
            if segments == 1:
                values = _group_values(*counts[0])
                for i, assignment in enumerate(assignments):
                    log_w[i] += values[assignment[0]]
            else:
                for i, assignment in enumerate(assignments):
                    log_w[i] += _reference_ll(counts, assignment)
            continue
        given = []
        for i, assignment in enumerate(assignments):
            ll = [sum(_bernoulli(n0, n1, assignment[m] ^ h) for m, (n0, n1) in counts.items()) for h in (0, 1)]
            log_w[i] += _LOG_HALF + _lse(ll[0], ll[1])
            given.append(_logistic(ll[1] - ll[0]))
        conditional[key] = (given, sum(n0 + n1 for n0, n1 in counts.values()))
    total = log_w[0]
    for value in log_w[1:]:
        total = _lse(total, value)
    if segments == 1:
        p = _logistic(log_w[1] - log_w[0])
        posterior = [1 - p, p]
    else:
        posterior = [math.exp(value - total) for value in log_w]
    p_segments = [sum(p for p, a in zip(posterior, assignments) if a[m]) for m in range(segments)]
    if segments == 1:
        p_segments = [posterior[1]]
    return {"log_marginal": segments * _LOG_HALF + total, "posterior": posterior, "assignments": assignments,
            "p_segments": p_segments, "conditional": conditional}


def _factor_p(inference, given):
    """P(h=1) = sum over assignments of P(assignment) P(h=1 | assignment, data)."""
    return sum(p * g for p, g in zip(inference["posterior"], given))


def _relation_p(inference, given):
    """P(w_current XOR h = 1): the quantity camera or word readings observe directly."""
    return sum(p * ((1 - g) if a[-1] else g)
               for p, g, a in zip(inference["posterior"], given, inference["assignments"]))


def _identifiable(p):
    return abs(p - .5) >= IDENTIFIABLE_MARGIN


def _legacy(db, epoch):
    """The prototype's 8-state status, computed exactly as legacy_live.status (records ordered by id).

    legacy_live adds one log-likelihood row per event in id order; np.cumsum adds in that same sequential
    order, so the floats equal the prototype's bit for bit. O(events): built only for compute(), explain()
    and stored revisions, never on the executive's path.
    """
    records = db.execute(f"SELECT channel, root, bit, action FROM evidence WHERE epoch=? AND {learning_sql(db)} "
                         "ORDER BY id", (epoch,)).fetchall()
    references = {}
    readouts, observed = [], []
    for channel, root, bit, action in records:
        if channel == "reference":
            references.setdefault(root, [0, 0])[bit ^ action] += 1
        else:
            readouts.append(CAMERA if channel == "camera" else SPEAKER)
            observed.append(bit ^ action)
    scores = np.zeros((1, 8))
    if readouts:
        rows = np.where(np.array(readouts) == np.array(observed)[:, None],
                        np.log1p(-READING_ERROR), np.log(READING_ERROR))
        scores += np.cumsum(rows, axis=0)[-1]
    for n0, n1 in references.values():
        group = np.zeros((n0 + n1, 1), dtype=np.int64)
        group[:n1] = 1
        scores += group_log_likelihood(group, REFERENCE_FLIP, REFERENCE_ERROR)
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


def _segment_start(seqs, index):
    return seqs[index - 1] if index else 0


def _assess(db, epoch, *, detect=True, legacy=True, pool=True):
    """compute() plus the internals other functions reuse: (result, inference, factors, changes).

    detect=False skips change detection; legacy=False leaves "legacy" as None (the O(events) replay);
    pool=False ignores the epoch's link (its own evidence only).
    """
    require_epoch(db, epoch)
    known = changes(db, epoch)
    confirmed = known["confirmed"]
    bounds = sorted({seq for seqs in confirmed.values() for seq in seqs})
    counts, raw = _aggregate(db, epoch, bounds)
    linked = link(db, epoch) if pool else None
    linked_from = None
    if linked is not None:
        # Pooled events count as the epoch's earliest: bucket 0, i.e. the first segment of every factor.
        pooled = 0
        for (channel, root), (n0, n1, first) in _linked_tallies(db, linked).items():
            entry = raw.setdefault((channel, root, 0), [0, 0, first])
            entry[0] += n0
            entry[1] += n1
            entry[2] = min(entry[2], first)
            pooled += n0 + n1
        linked_from = {"epoch": linked["source_epoch"], "segments": linked["segments"],
                       "pending_id": linked["pending_id"], "created_at": linked["created_at"], "learning": pooled}
    factors, segments = _model(raw, confirmed, bounds)
    inference = _infer(factors, segments)
    p_w = inference["p_segments"][-1]
    identifiable = _identifiable(p_w)
    reference_roots = sum(1 for (channel, _, _), _ in factors if channel == "reference")
    current = {}
    for key, value in inference["conditional"].items():
        channel, root, fseg = key
        if (channel, root) not in current or fseg > current[(channel, root)][0]:
            current[(channel, root)] = (fseg, value)
    groups = {"camera": {}, "word": {}}
    unidentifiable = []
    if not identifiable:
        unidentifiable.append({"scope": "mechanism", "why": _mechanism_why(p_w, reference_roots, counts["learning"])})
    for (channel, root), (fseg, (given, n)) in sorted(current.items()):
        p = _factor_p(inference, given)
        scope = scope_of(channel, root)
        entry = {"p_inverted": p, "n": n}
        if scope in confirmed:
            entry["segment"] = f"{root}@{_segment_start(confirmed[scope], fseg)}"
        groups[channel][root] = entry
        if not _identifiable(p):
            unidentifiable.append({"scope": scope, "why": _factor_why(p, _relation_p(inference, given), n,
                                                                       identifiable)})
    mechanism = {"p_inverted": p_w, "identifiable": identifiable}
    segments_detail = {}
    if "mechanism" in confirmed:
        seqs = confirmed["mechanism"]
        mechanism["segment"] = f"mechanism@{seqs[-1]}"
        sizes = [0] * segments
        for _, counts_by_m in factors:
            for m, (n0, n1) in counts_by_m.items():
                sizes[m] += n0 + n1
        segments_detail["mechanism"] = [
            {"segment": f"mechanism@{_segment_start(seqs, m)}", "since_seq": _segment_start(seqs, m),
             "p_inverted": inference["p_segments"][m], "n": sizes[m]} for m in range(segments)]
    for scope, seqs in sorted(confirmed.items()):
        if scope == "mechanism":
            continue
        prefix, root = scope.split(":", 1)
        rows = []
        for fseg in range(len(seqs) + 1):
            given, n = inference["conditional"].get((_CHANNEL[prefix], root, fseg), (None, 0))
            rows.append({"segment": f"{root}@{_segment_start(seqs, fseg)}", "since_seq": _segment_start(seqs, fseg),
                         "p_inverted": _factor_p(inference, given) if given else .5, "n": n})
        segments_detail[scope] = rows
    simple = len(groups["camera"]) <= 1 and len(groups["word"]) <= 1 and not confirmed and linked_from is None
    result = {
        "epoch": epoch, "state_version": store.state_version(db), "evidence": counts,
        "mechanism": mechanism, "sensors": groups["camera"], "partners": groups["word"],
        "reference_roots": reference_roots, "unidentifiable": unidentifiable,
        "legacy": _legacy(db, epoch) if simple and legacy else None,
        "segments": segments_detail,
        "confirmed_changes": [{key: r[key] for key in ("scope", "since_seq", "pending_id", "created_at")}
                              for r in known["records"] if r["confirmed"]],
        "linked_from": linked_from,
    }
    if detect:
        change = _detect(db, epoch, known)
        result["change"] = change
        if change["suspected"]:
            # A change is suspected and the owner has neither confirmed nor rejected it: the confident
            # reading of that factor may be stale.
            scope = change["scope"]
            if scope == "mechanism":
                target = mechanism
            else:
                prefix, root = scope.split(":", 1)
                target = result["sensors" if prefix == "camera" else "partners"].get(root)
            if target is not None:
                target.update(suspected_change=True, llr=change["llr"], suspected_since_seq=change["since_seq"],
                              ambiguous=change["ambiguous"])
    return result, inference, factors, known


def compute(db, epoch):
    """Exact posterior over the mechanism, each camera root's alignment and each partner's convention.

    Uses active evidence with consent='learn'; copies sharing (channel, root, sample_id) count once.
    With at most one camera root, one partner and no confirmed change, "legacy" reproduces
    legacy_live.status(). Factors report their current segment; "segments" details every segment of a
    scope with confirmed changes, "confirmed_changes" lists them, and "change" is detect_change(). A
    factor (or the mechanism) whose change is suspected but not yet confirmed or rejected carries
    "suspected_change": true with its llr and suspected_since_seq. "linked_from" is None, or the earlier
    epoch and segments this epoch continues ({"epoch", "segments", "pending_id", "created_at", "learning"}
    with "learning" = pooled source events); factor "n" then includes pooled events, "evidence" does not.
    """
    return _assess(db, epoch)[0]


# ---- change detection ---------------------------------------------------------------------------------

def _window(db, epoch, limit):
    rows = db.execute(f"SELECT {','.join(EVENT_COLUMNS)} FROM evidence INDEXED BY evidence_epoch "
                      f"WHERE epoch=? AND {learning_sql(db)} ORDER BY seq DESC LIMIT ?", (epoch, limit)).fetchall()
    return [event_dict(row) for row in reversed(rows)]


def _detect(db, epoch, known):
    confirmed, rejected = known["confirmed"], known["rejected"]
    window = _window(db, epoch, CHANGE_WINDOW)
    mechanism = confirmed.get("mechanism", [])
    present = sorted({_segment(mechanism, e["seq"]) for e in window})
    remap = {m: i for i, m in enumerate(present)}
    keys, msegs, readings = [], [], []
    for e in window:
        channel, root = e["channel"], e["root"]
        fseg = 0 if channel == "reference" else _segment(confirmed.get(scope_of(channel, root), []), e["seq"])
        keys.append((channel, root, fseg))
        msegs.append(remap[_segment(mechanism, e["seq"])])
        readings.append(e["bit"] ^ e["action"])
    tallies = {}
    for key, m, x in zip(keys, msegs, readings):
        tallies.setdefault(key, {}).setdefault(m, [0, 0])[x] += 1
    segments = len(present) or 1

    def factors(delta=None):
        out = []
        for key, by_m in tallies.items():
            counts = {}
            for m, (n0, n1) in sorted(by_m.items()):
                d0, d1 = delta.get((key, m), (0, 0)) if delta else (0, 0)
                counts[m] = (n0 - d0 + d1, n1 - d1 + d0)
            out.append((key, counts))
        return out

    base = _infer(factors(), segments)["log_marginal"]
    scopes = {"mechanism": []}
    for i, e in enumerate(window):
        scopes["mechanism"].append(i)
        if e["channel"] in _PREFIX:
            scopes.setdefault(scope_of(e["channel"], e["root"]), []).append(i)
    best, tried, suppressed = {}, 0, []
    for scope, indices in scopes.items():
        since = confirmed.get(scope, [None])[-1]
        affected = [i for i in indices if since is None or window[i]["seq"] >= since]
        if scope in rejected and sum(window[i]["seq"] > rejected[scope] for i in affected) < MIN_AFTER_CHANGE:
            suppressed.append(scope)
            continue
        delta = {}
        found = []
        # Walk the changepoint backwards, accumulating the flipped suffix; keep the earliest best on ties.
        for k in range(len(affected) - 1, 0, -1):
            i = affected[k]
            entry = delta.setdefault((keys[i], msegs[i]), [0, 0])
            entry[readings[i]] += 1
            if len(affected) - k >= MIN_AFTER_CHANGE:
                found.append((_infer(factors(delta), segments)["log_marginal"] - base, window[i]["seq"]))
                tried += 1
        for gain, seq in found:
            if scope not in best or gain >= best[scope][0]:
                best[scope] = (gain, seq)
    result = {"suspected": False, "scope": None, "since_seq": None, "llr": 0.0, "alternatives": [],
              "ambiguous": False, "window": len(window), "tried": tried, "suppressed": suppressed}
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


def detect_change(db, epoch):
    """Single-factor changepoint test over the last 64 learning events.

    A change in scope X at tau flips that hidden bit for X's events from tau on, which is the same as
    inverting those readings. Score = log marginal-likelihood gain over no change - log(#changepoints
    tried, across all scopes). Each candidate needs >= 1 affected event before tau and >= 3 from tau on.
    Confirmed changes are part of the model: a scope is only tested on its events after its latest
    confirmed changepoint. A scope the owner rejected is not tested ("suppressed") until at least 3 of
    its events arrived after the rejection.
    """
    require_epoch(db, epoch)
    return _detect(db, epoch, changes(db, epoch))


# ---- epoch matching -----------------------------------------------------------------------------------

def _map(p):
    """MAP value of a binary factor; None for a tie (within rounding of exact symmetry)."""
    return None if abs(p - .5) < 1e-9 else int(p > .5)


def _relation_at(inference, given, m):
    """P(w_m XOR h = 1): the relation a camera/word segment observes under mechanism segment m."""
    return sum(p * ((1 - g) if a[m] else g)
               for p, g, a in zip(inference["posterior"], given, inference["assignments"]))


def _fresh(n0, n1):
    """Log marginal likelihood of one camera/word root's readings under a fresh, uniform bit."""
    return _LOG_HALF + _lse(_bernoulli(n0, n1, 0), _bernoulli(n0, n1, 1))


def _window_tallies(events):
    tallies = {}
    for e in events:
        tallies.setdefault((e["channel"], e["root"]), [0, 0])[e["bit"] ^ e["action"]] += 1
    return {key: tuple(value) for key, value in tallies.items()}


def new_setup_log_likelihood(tallies):
    """Exact log marginal likelihood of readings {(channel, root): (n0, n1)} under a NEW setup.

    A new setup has a fresh, uniform mechanism bit shared by every reading, a fresh uniform alignment bit per
    camera root, a fresh uniform convention bit per partner and a fixture polarity per reference root, with the
    reading-error model of compute(): this is compute()'s own evidence on these readings alone.
    """
    return _infer([((channel, root, 0), {0: counts}) for (channel, root), counts in sorted(tallies.items())])[
        "log_marginal"]


def _candidate(db, other, tallies):
    """The best-scoring configuration of `other` for the window tallies, or None when it has no learning events.

    A configuration picks one mechanism segment m and, for each camera root and partner the window and
    `other` share, one of that factor's segments. Each picked segment predicts with its MAP value: a camera
    or word root predicts MAP(w_m XOR bit) with reading error .10, references are scored with MAP w_m and
    their fixture polarity integrated out. Roots `other` never saw, ties, and the references when w_m is a
    tie are fresh (marginalised), exactly as under a new setup. For each m the segment of each factor is
    chosen independently (the factors are independent given m), then the best m is kept.

    Multiple comparisons: picking the best of k segments of a factor would let an epoch with more segments
    fit better by chance. Each picked factor with k > 1 segments (and the mechanism with M > 1 segments,
    when the window depends on it) is charged log(k): its share of a uniform prior over that factor's
    segments, so the score is a lower bound on the configuration-averaged likelihood.
    """
    result, inference, _, known = _assess(db, other, detect=False, legacy=False, pool=False)
    if not result["evidence"]["learning"]:
        return None
    confirmed = known["confirmed"]
    mechanism = confirmed.get("mechanism", [])
    counts = {}
    for channel, root, _ in inference["conditional"]:
        counts[(channel, root)] = len(confirmed.get(scope_of(channel, root), [])) + 1
    references = [c for (channel, _), c in tallies.items() if channel == "reference"]
    best = None
    for m in range(len(mechanism) + 1):
        w = _map(inference["p_segments"][m])
        ll, penalty, informative, chosen = 0.0, 0.0, 0, {}
        for (channel, root), (n0, n1) in sorted(tallies.items()):
            if channel == "reference":
                continue
            count = counts.get((channel, root))
            if count is None:
                ll += _fresh(n0, n1)
                continue
            options = []
            for fseg in range(count):
                given = inference["conditional"].get((channel, root, fseg))
                relation = _map(_relation_at(inference, given[0], m)) if given else None
                options.append((_fresh(n0, n1) if relation is None else _bernoulli(n0, n1, relation), fseg, relation))
            score, fseg, relation = max(options, key=lambda option: option[:2])   # ties -> the later segment
            ll += score
            penalty += math.log(count)
            chosen[scope_of(channel, root)] = fseg
            informative += 0 if relation is None else n0 + n1
        if references:
            values = [_group_values(n0, n1) for n0, n1 in references]
            if w is None:
                ll += _LOG_HALF + _lse(sum(v[0] for v in values), sum(v[1] for v in values))
            else:
                ll += sum(v[w] for v in values)
                informative += sum(n0 + n1 for n0, n1 in references)
        if mechanism and (references or chosen):
            penalty += math.log(len(mechanism) + 1)
        if best is None or ll - penalty >= best[0]:   # ties -> the later mechanism segment
            best = (ll - penalty, m, penalty, informative, chosen)
    score, m, penalty, informative, chosen = best
    segments = {"mechanism": f"mechanism@{_segment_start(mechanism, m)}"}
    for (channel, root), count in sorted(counts.items()):
        scope = scope_of(channel, root)
        segments[scope] = f"{root}@{_segment_start(confirmed.get(scope, []), chosen.get(scope, count - 1))}"
    return {"epoch": other, "segments": segments, "log_likelihood": score, "penalty": penalty,
            "events": informative}


def match_epochs(db, epoch, *, last=MATCH_EVENTS):
    """Is this epoch's recent evidence an earlier setup returning, or a new setup?

    Scores the last `last` learning events of `epoch` under each OTHER epoch with learning evidence, taking
    the best configuration of that epoch's segments (see _candidate; its log_likelihood includes the
    log(#segments) multiple-comparison penalty), against a NEW setup scored exactly by
    new_setup_log_likelihood. Prior: 1/2 on a new setup, 1/2 on "some earlier configuration" split evenly
    across the candidate epochs. Without references in the window, a candidate can beat a new setup by at most
    a factor 2 per shared camera root or partner (a fresh bit matches it half the time), however many
    readings arrive.

    Returns {"epoch", "events", "ranking": [{"epoch", "segments", "log_likelihood", "penalty", "posterior",
    "events"}], "best", "best_posterior", "new_setup_posterior", "new_setup_log_likelihood", "ask_posterior"}.
    "best" is the top epoch when its posterior >= MATCH_POSTERIOR (.75), else None.
    """
    require_epoch(db, epoch)
    if isinstance(last, bool) or not isinstance(last, int) or not 1 <= last <= 1000:
        raise ValidationError("last must be an integer between 1 and 1000")
    events = _window(db, epoch, last)
    tallies = _window_tallies(events)
    new_setup = new_setup_log_likelihood(tallies)
    ranking = []
    for (other,) in db.execute("SELECT name FROM epochs WHERE name!=? ORDER BY created_at, name", (epoch,)).fetchall():
        candidate = _candidate(db, other, tallies)
        if candidate is not None:
            ranking.append(candidate)
    prior = math.log(.5 / len(ranking)) if ranking else None
    total = _LOG_HALF + new_setup
    for r in ranking:
        total = _lse(total, prior + r["log_likelihood"])
    for r in ranking:
        r["posterior"] = math.exp(prior + r["log_likelihood"] - total)
    ranking.sort(key=lambda r: (-r["log_likelihood"], r["epoch"]))
    best = ranking[0]["epoch"] if ranking and ranking[0]["posterior"] >= MATCH_POSTERIOR else None
    return {"epoch": epoch, "events": len(events), "ranking": ranking, "best": best,
            "best_posterior": ranking[0]["posterior"] if best is not None else None,
            "new_setup_posterior": math.exp(_LOG_HALF + new_setup - total),
            "new_setup_log_likelihood": new_setup, "ask_posterior": MATCH_POSTERIOR}


# ---- explanations -------------------------------------------------------------------------------------

def _load(db, epoch):
    """The epoch's learning events in seq order (consent='learn'; copies of one sample counted once)."""
    return [event_dict(r) for r in db.execute(
        f"SELECT {','.join(EVENT_COLUMNS)} FROM evidence WHERE epoch=? AND {learning_sql(db)} ORDER BY seq",
        (epoch,))]


def _split(events, predicted):
    agree = [e for e in events if predicted is not None and e["bit"] ^ e["action"] == predicted]
    disagree = [e for e in events if predicted is not None and e["bit"] ^ e["action"] != predicted]
    return agree, disagree


def _factor(scope, p, events, predicted, uncertainty):
    supporting, counter = _split(events, predicted)
    return {"scope": scope, "p_inverted": p, "supporting": supporting[-EXPLAIN_EVENTS:],
            "counterevidence": counter[-EXPLAIN_EVENTS:], "supporting_total": len(supporting),
            "counterevidence_total": len(counter), "uncertainty": uncertainty}


def explain(db, epoch):
    """Beliefs with, per factor, the events that support or contradict its MAP reading and why it is uncertain.

    Mechanism uses the reference events of its current segment against MAP w. Camera and word factors use
    their own events against the MAP of the relation they observe (w XOR bit), which stays defined when w
    is not identifiable. A scope with confirmed changes lists one factor per segment; older segments are
    named "<scope>@<since_seq>". Event lists keep the latest 50; *_total gives the full counts.
    """
    result, inference, _, known = _assess(db, epoch)
    confirmed = known["confirmed"]
    mechanism_bounds = confirmed.get("mechanism", [])
    p_w = result["mechanism"]["p_inverted"]
    identifiable = result["mechanism"]["identifiable"]
    learning = _load(db, epoch)
    current_mechanism = len(mechanism_bounds)
    references = [e for e in learning if e["channel"] == "reference"
                  and _segment(mechanism_bounds, e["seq"]) == current_mechanism]
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
    for (channel, root, fseg), (given, n) in sorted(inference["conditional"].items()):
        scope = scope_of(channel, root)
        seqs = confirmed.get(scope, [])
        name = scope if fseg == len(seqs) else f"{scope}@{_segment_start(seqs, fseg)}"
        p = _factor_p(inference, given)
        relation = _relation_p(inference, given)
        if _identifiable(p):
            why = f"{n} reading(s) give p_inverted={p:.3f}"
        else:
            why = _factor_why(p, relation, n, identifiable)
        events = [e for e in learning if e["channel"] == channel and e["root"] == root
                  and _segment(seqs, e["seq"]) == fseg]
        factors.append(_factor(name, p, events, _map(relation), why))
    rows = db.execute("SELECT id,state_version,created_at,cause,summary FROM belief_revisions WHERE epoch=? "
                      "ORDER BY id DESC LIMIT 20", (epoch,)).fetchall()
    revisions = [{"id": i, "state_version": v, "created_at": t, "cause": c, "map": _signature_text(s)}
                 for i, v, t, c, s in reversed(rows)]
    return {"beliefs": result, "factors": factors, "revisions": revisions, "change": result["change"]}


# ---- revisions ----------------------------------------------------------------------------------------

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
    result = _assess(db, epoch, legacy=False)[0]
    row = db.execute("SELECT summary FROM belief_revisions WHERE epoch=? ORDER BY id DESC LIMIT 1",
                     (epoch,)).fetchone()
    previous = _signature_text(row[0]) if row else _EMPTY_SIGNATURE
    if previous == _signature(result):
        return None
    result = compute(db, epoch)
    summary = json.dumps(result, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES(?,?,?,?,?)",
                      (epoch, result["state_version"], now, cause, summary)).lastrowid

