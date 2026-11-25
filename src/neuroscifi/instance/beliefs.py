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

Consolidated readings (ADR-044, schema 14). When the lifetime of an event with consent 'learn' ends,
governance.sweep moves what it taught to `evidence_consolidated`: its seq, epoch, channel, root, corrected
reading and sample id. Every belief and likelihood here reads the journal's events and those rows together, as
one set ordered by seq: the tallies, a linked epoch's pool, the change window, epoch matching and the legacy
status. A consolidated reading sits in the segment its seq falls in, exactly as the event did. Copies of one
sample count once, and the copy that counts is the earliest one among the journal's learning copies and the
consolidated readings of that sample, whichever of them have expired (`_not_counted` counts the consolidated
readings that do not count, `_EARLIEST` tests one; `learning_sql` leaves out a journal copy with an earlier
consolidated one). So a
posterior is the same number before and after its events expire, and forgetting the first copy of a sample lets
the next one count, as in a journal where nothing expired. Listings of episodes (ids, actors, timestamps) show
raw events only; counts say how many consolidated readings stand beside them.
"""
from bisect import bisect_right
import functools
import itertools
import json
import math

import numpy as np

from ..graph_store import decode
from ..lab.core import CAMERA, SPEAKER, STATES, probabilities
from ..lab.source_groups import group_log_likelihood
from . import datasets, store
from .errors import NotFound, ValidationError
from .schema import has_consolidated

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
_LEARNING = ("consent='learn' AND (sample_id IS NULL OR (seq=(SELECT min(p.seq) FROM evidence p{hint} "
             "WHERE p.epoch=evidence.epoch AND p.consent='learn' AND p.sample_id=evidence.sample_id "
             "AND p.channel=evidence.channel AND p.root=evidence.root){earlier}))")
# The earliest consolidated reading of a row's sample (ADR-044), NULL when it has none; {t} is the row's table or
# alias. Pinned to the copies index (schema.ensure_indexes guarantees it on every file opened through store): left
# to itself the planner walks the tally index, every reading of the root.
_FIRST_READING = ("(SELECT min(c.seq) FROM evidence_consolidated c INDEXED BY evidence_consolidated_copies "
                  "WHERE c.epoch={t}.epoch AND c.channel={t}.channel AND c.root={t}.root "
                  "AND c.sample_id={t}.sample_id)")
# A consolidated reading `c` that is the earliest reading of its sample, or has no sample id. (A journal copy may
# still precede it: _preceded.)
_EARLIEST = ("(c.sample_id IS NULL OR NOT EXISTS (SELECT 1 FROM evidence_consolidated d "
             "INDEXED BY evidence_consolidated_copies WHERE d.epoch=c.epoch AND d.channel=c.channel "
             "AND d.root=c.root AND d.sample_id=c.sample_id AND d.seq<c.seq))")
# Up to this many samples with several readings are looked up one by one (the usual case: none or a handful); beyond
# it, one grouped pass over the copies index counts them all.
_FEW_COPIED_SAMPLES = 256

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
    present = {name for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('evidence_tally','evidence_consolidated')")}
    # With consolidated readings the first copy of a sample may be one of them: a journal copy counts only when no
    # consolidated reading of its sample is earlier.
    earlier = f" AND coalesce({_FIRST_READING.format(t='evidence')}>seq, 1)" \
        if "evidence_consolidated" in present else ""
    return _LEARNING.format(hint=" INDEXED BY evidence_tally" if "evidence_tally" in present else "", earlier=earlier)


def _preceded(db, epoch):
    """Seqs of consolidated readings that are the first reading of their sample but have an earlier learning copy
    still in the journal (a promoted event, or a copy whose lifetime is longer): the journal's copy counts, where it
    stands, and the reading does not. One seek per sampled event of the journal, so bounded by the journal."""
    if not has_consolidated(db) or db.execute(
            "SELECT 1 FROM evidence_consolidated INDEXED BY evidence_consolidated_copies WHERE epoch=? "
            "AND sample_id IS NOT NULL LIMIT 1", (epoch,)).fetchone() is None:
        return []           # no consolidated reading of the epoch has a sample id
    return [seq for (seq,) in db.execute(
        f"SELECT reading FROM (SELECT min(seq) AS first, {_FIRST_READING.format(t='e')} AS reading FROM evidence e "
        "WHERE epoch=? AND consent='learn' AND sample_id IS NOT NULL GROUP BY sample_id, channel, root) "
        "WHERE reading>first", (epoch,))]


def _chunks(values, size=400):
    return [values[start:start + size] for start in range(0, len(values), size)]


def _has_copied_index(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='evidence_consolidated_copied'"
                      ).fetchone() is not None


def _copied_samples(db, epoch):
    """The epoch's samples with more than one consolidated reading, up to _FEW_COPIED_SAMPLES + 1 of them, as rows
    (channel, root, sample_id, first seq, readings).

    Of any two readings of a sample at least one carries copied=1 (schema 16), so every such sample is among the
    flagged ones: when they are few, each is counted by key and the samples a stale flag names (one reading left) are
    dropped. When they are many, or the file has no flags (a connection that has not migrated), the grouped pass over
    every reading of the epoch decides, as before. Either way the rows are those of the grouped pass."""
    if _has_copied_index(db):
        flagged = db.execute(
            "SELECT DISTINCT channel, root, sample_id FROM evidence_consolidated INDEXED BY evidence_consolidated_copied "
            "WHERE epoch=? AND copied=1 LIMIT ?", (epoch, _FEW_COPIED_SAMPLES + 1)).fetchall()
        if len(flagged) <= _FEW_COPIED_SAMPLES:
            rows = []
            for channel, root, sample in flagged:
                first, n = db.execute(
                    "SELECT min(seq), count(*) FROM evidence_consolidated INDEXED BY evidence_consolidated_copies "
                    "WHERE epoch=? AND channel=? AND root=? AND sample_id=?", (epoch, channel, root, sample)).fetchone()
                if n > 1:
                    rows.append((channel, root, sample, first, n))
            return rows
    return db.execute(
        "SELECT channel, root, sample_id, min(seq), count(*) FROM evidence_consolidated "
        "INDEXED BY evidence_consolidated_copies WHERE epoch=? AND sample_id IS NOT NULL "
        "GROUP BY channel, root, sample_id HAVING count(*)>1 LIMIT ?", (epoch, _FEW_COPIED_SAMPLES + 1)).fetchall()


def _not_counted(db, epoch, predicates, preceded):
    """How many of the epoch's consolidated readings do NOT count as learning readings, per root, corrected reading
    and predicate: {(channel, root): [[n0, n1] for each predicate]}.

    Every expired copy of a sample left its own reading (ADR-044) and one copy of a sample counts: the earliest among
    the journal's learning copies and the readings. So a reading does not count when an earlier reading of its sample
    exists (a "later copy"), or when it is in `preceded` (_preceded). `predicates`: [(template, params)], SQL
    conditions on a reading's channel, root and seq, the seq written `{s}`.

    Nothing here grows with Python work per reading. _copied_samples finds the samples with several readings, from
    the flagged readings when they are few (no pass over the epoch's readings at all when none is flagged). Few of
    them (the usual case): their later copies are read by key and counted in one statement.
    Many (every sample recorded by two devices): one grouped pass counts, per sample, all its readings minus its
    first, and sums that per root, so the cost is that of a pass over the index however many samples are copied.
    """
    out = {}

    def add(rows):
        for channel, root, *counts in rows:
            entry = out.setdefault((channel, root), [[0, 0] for _ in predicates])
            for i in range(len(predicates)):
                entry[i][0] += counts[2 * i] or 0
                entry[i][1] += counts[2 * i + 1] or 0

    def by_seq(seqs):
        sums = "".join(f", sum(value=0 AND ({t.format(s='seq')})), sum(value=1 AND ({t.format(s='seq')}))"
                       for t, _ in predicates)
        params = [x for _, values in predicates for x in (*values, *values)]
        for chunk in _chunks(seqs):
            add(db.execute(f"SELECT channel, root{sums} FROM evidence_consolidated WHERE seq IN "
                           f"({','.join('?' * len(chunk))}) GROUP BY channel, root", (*params, *chunk)))

    copied = _copied_samples(db, epoch)
    # (few samples, and few readings of them: one sample recorded thousands of times goes to the grouped pass too)
    if len(copied) <= _FEW_COPIED_SAMPLES and sum(row[4] for row in copied) <= 16 * _FEW_COPIED_SAMPLES:
        later = [seq for channel, root, sample, first, _ in copied for (seq,) in db.execute(
            "SELECT seq FROM evidence_consolidated INDEXED BY evidence_consolidated_copies WHERE epoch=? "
            "AND channel=? AND root=? AND sample_id=? AND seq>?", (epoch, channel, root, sample, first))]
        by_seq(later + list(preceded))
        return out
    # Per sample with several readings: its readings under each predicate, and its first reading (seq and value packed
    # into one minimum); per root: the readings minus the first ones.
    inner = "".join(f", sum(value=0 AND ({t.format(s='seq')})) AS a{i}, sum(value=1 AND ({t.format(s='seq')})) AS b{i}"
                    for i, (t, _) in enumerate(predicates))
    outer = "".join(f", sum(a{i}) - sum(first%2=0 AND ({t.format(s='first/2')})), "
                    f"sum(b{i}) - sum(first%2=1 AND ({t.format(s='first/2')}))" for i, (t, _) in enumerate(predicates))
    inner_params = [x for _, values in predicates for x in (*values, *values)]
    add(db.execute(
        f"SELECT channel, root{outer} FROM (SELECT channel, root, min(seq*2+value) AS first{inner} "
        "FROM evidence_consolidated INDEXED BY evidence_consolidated_copies WHERE epoch=? AND sample_id IS NOT NULL "
        "GROUP BY channel, root, sample_id HAVING count(*)>1) GROUP BY channel, root",
        (*inner_params, *inner_params, epoch)))
    by_seq(list(preceded))
    return out


def last_seq(db, epoch):
    """The highest seq the epoch ever held: of an event in the journal, a consolidated reading or a tombstone; 0 when
    it never held any. It does not fall when events expire or are forgotten, so "evidence through #N" in a question
    means the same before and after a sweep."""
    last = db.execute("SELECT coalesce(max(seq), 0) FROM evidence WHERE epoch=?", (epoch,)).fetchone()[0]
    last = max(last, db.execute("SELECT coalesce(max(seq), 0) FROM tombstones WHERE epoch=?", (epoch,)).fetchone()[0])
    if has_consolidated(db):
        last = max(last, db.execute("SELECT coalesce(max(seq), 0) FROM evidence_consolidated WHERE epoch=?",
                                    (epoch,)).fetchone()[0])
    return last


def learning_since(db, epoch, after, channel=None, root=None):
    """Learning readings of the epoch with seq > after, raw and consolidated, optionally of one channel and root."""
    where, params = "", []
    if channel is not None:
        where, params = " AND channel=? AND root=?", [channel, root]
    n = db.execute(f"SELECT count(*) FROM evidence WHERE epoch=? AND seq>? AND {learning_sql(db)}{where}",
                   [epoch, after, *params]).fetchone()[0]
    if has_consolidated(db):
        # Readings after `after` that are the earliest of their sample: one seek per reading in that range, which a
        # question's horizon keeps short. Less those with an earlier copy still in the journal.
        scope = where.replace("channel=?", "c.channel=?").replace("root=?", "c.root=?")
        # (by seq for the whole epoch; within one root by the tally index, whose rows of that root are few)
        index = "evidence_consolidated_epoch" if channel is None else "evidence_consolidated_tally"
        n += db.execute(f"SELECT count(*) FROM evidence_consolidated c INDEXED BY {index} "
                        f"WHERE c.epoch=? AND c.seq>?{scope} AND {_EARLIEST}", [epoch, after, *params]).fetchone()[0]
        for chunk in _chunks(_preceded(db, epoch)):
            n -= db.execute(f"SELECT count(*) FROM evidence_consolidated WHERE seq>?{where} AND seq IN "
                            f"({','.join('?' * len(chunk))})", [after, *params, *chunk]).fetchone()[0]
    return n


def has_reference(db, epoch):
    """Whether the epoch holds any reference evidence, raw or consolidated."""
    if db.execute("SELECT 1 FROM evidence WHERE epoch=? AND channel='reference' LIMIT 1", (epoch,)).fetchone():
        return True
    return has_consolidated(db) and db.execute(
        "SELECT 1 FROM evidence_consolidated WHERE epoch=? AND channel='reference' LIMIT 1",
        (epoch,)).fetchone() is not None


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
        return ("{s}>=?" + (" AND {s}<?" if end is not None else ""),
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
    scope = f"{where} AND ({' OR '.join(alternatives)})"       # a template: the seq column is written {s}
    rows = db.execute(
        f"SELECT channel, root, bit, action, count(*), min(seq) FROM evidence WHERE epoch=? AND {learning_sql(db)} "
        f"AND {scope.format(s='seq')} GROUP BY channel, root, bit, action", [source, *params]).fetchall()
    if has_consolidated(db):
        # The source's consolidated readings lie in its segments by their seq, as its events did; those that do not
        # count (a later copy of a sample) are left out, so that the first seq of a root is a counted one.
        rows += [(channel, root, value, 0, n, first) for channel, root, value, _, _, n, first in
                 _consolidated_tallies(db, source, [], scope=(scope, params))[0]]
    for channel, root, bit, action, n, first in rows:
        entry = tallies.setdefault((channel, root), [0, 0, first])
        entry[bit ^ action] += n
        entry[2] = min(entry[2], first)
    return tallies


# ---- tallies and the model ----------------------------------------------------------------------------

def _consolidated_tallies(db, epoch, bounds, preceded=None, scope=None):
    """Rows (channel, root, value, 0, bucket, n, first_seq) of the epoch's consolidated readings that count, bucket =
    number of `bounds` <= seq, as _aggregate's other statements return them; and how many readings do not count.

    Consolidated readings never expire, so an old epoch can hold many more of them than the journal ever held
    events. One pass over the covering index in its own order, without a sort: per (channel, root, value) the
    number of readings at or after each bound and the first seq there; a bucket's count is the difference of two
    neighbours, and its first seq is the first seq at or after its bound. The readings that do not count
    (_not_counted: later copies of a sample) are taken out per root, value and bucket; a bucket that lost any has
    its first counted seq read again (factors are ordered by their first reading).
    `preceded`: _preceded(db, epoch), read here when not given. `scope`: (template, params), a further condition on
    channel, root and seq (written {s}) that every reading must meet (a linked epoch's segments).
    """
    preceded = _preceded(db, epoch) if preceded is None else preceded
    scope_sql, scope_params = scope if scope is not None else ("1", [])
    per_bound = "".join(", sum(seq>=?), min(CASE WHEN seq>=? THEN seq END)" for _ in bounds)
    tallies = {}
    for channel, root, value, n, first, *rest in db.execute(
            f"SELECT channel, root, value, count(*), min(seq){per_bound} FROM evidence_consolidated "
            f"INDEXED BY evidence_consolidated_tally WHERE epoch=? AND ({scope_sql.format(s='seq')}) "
            "GROUP BY channel, root, value", (*(b for bound in bounds for b in (bound, bound)), epoch, *scope_params)):
        at_or_after = [n, *rest[0::2], 0]
        firsts = [first, *rest[1::2]]
        for b in range(len(bounds) + 1):
            if at_or_after[b] - at_or_after[b + 1]:
                tallies[(channel, root, value, b)] = [at_or_after[b] - at_or_after[b + 1], firsts[b]]
    if not tallies:
        return [], 0
    predicates = [(scope_sql, list(scope_params))] + [(f"({scope_sql}) AND {{s}}>=?", [*scope_params, bound])
                                                      for bound in bounds]
    uncounted = 0
    skip = f" AND c.seq NOT IN ({','.join('?' * len(preceded))})" if preceded else ""
    for (channel, root), cumulative in _not_counted(db, epoch, predicates, preceded).items():
        cumulative = cumulative + [[0, 0]]
        for value in (0, 1):
            for b in range(len(bounds) + 1):
                lost = cumulative[b][value] - cumulative[b + 1][value]
                entry = tallies.get((channel, root, value, b))
                if not lost or entry is None:   # None: a writer removed the readings between the two reads
                    continue
                uncounted += lost
                entry[0] -= lost
                if entry[0] <= 0:
                    del tallies[(channel, root, value, b)]
                    continue
                low, high = (bounds[b - 1] if b else 0), (bounds[b] if b < len(bounds) else None)
                first = db.execute(
                    "SELECT c.seq FROM evidence_consolidated c INDEXED BY evidence_consolidated_tally "
                    "WHERE c.epoch=? AND c.channel=? AND c.root=? AND c.value=? AND c.seq>=?"
                    + (" AND c.seq<?" if high is not None else "") + f" AND ({scope_sql.format(s='c.seq')}) "
                    f"AND {_EARLIEST}{skip} ORDER BY c.seq LIMIT 1",
                    (epoch, channel, root, value, low, *([high] if high is not None else []), *scope_params,
                     *preceded)).fetchone()
                if first is not None:
                    entry[1] = first[0]
    return [(channel, root, value, 0, b, n, first) for (channel, root, value, b), (n, first) in tallies.items()], \
        uncounted


def _aggregate(db, epoch, bounds, preceded=None):
    """Learning-event tallies from SQL: {(channel, root, bucket): [n0, n1, first_seq]} and the evidence counts.

    bucket = number of `bounds` <= seq. Copies of one (channel, root, sample_id) count once: the earliest copy
    among the journal's and the consolidated readings (`preceded`: _preceded(db, epoch), read here when not given).
    """
    bucket = "+".join(["(seq>=?)"] * len(bounds)) or "0"
    group = ", b" if bounds else ""
    raw = {}
    consolidated, later_copies = _consolidated_tallies(db, epoch, bounds, preceded) if has_consolidated(db) \
        else ([], 0)
    nonsample = db.execute(
        f"SELECT channel, root, bit, action, {bucket} AS b, count(*), min(seq) FROM evidence "
        f"WHERE epoch=? AND consent='learn' AND sample_id IS NULL GROUP BY channel, root, bit, action{group}",
        (*bounds, epoch)).fetchall()
    # A sample with an earlier consolidated reading ("covered") is counted there; its journal copies are duplicates.
    # An epoch without consolidated readings runs the statement used before schema 14, with a constant 0.
    covered = f"coalesce({_FIRST_READING.format(t='e')}<min(seq), 0)" if consolidated or later_copies else "0"
    sampled = db.execute(
        f"SELECT channel, root, bit, action, {bucket} AS b, count(*), sum(copies), covered, min(seq) FROM ("
        f"SELECT channel, root, bit, action, min(seq) AS seq, count(*) AS copies, {covered} AS covered "
        "FROM evidence e WHERE epoch=? AND consent='learn' AND sample_id IS NOT NULL "
        f"GROUP BY sample_id, channel, root) GROUP BY channel, root, bit, action, covered{group}",
        (*bounds, epoch)).fetchall()
    memory_only = db.execute("SELECT count(*) FROM evidence WHERE epoch=? AND consent='memory_only'",
                             (epoch,)).fetchone()[0]
    learning = copies = folded = 0
    for rows, kind in ((nonsample, "plain"), (sampled, "sampled"), (consolidated, "consolidated")):
        for row in rows:
            channel, root, bit, action, b, n = row[:6]
            first = row[-1]
            if kind == "sampled":
                copies += row[6]
                if row[7]:
                    continue
            elif kind == "plain":
                copies += n
            else:
                folded += n
            entry = raw.setdefault((channel, root, b), [0, 0, first])
            entry[bit ^ action] += n
            entry[2] = min(entry[2], first)
            learning += n
    counts = {"total": copies + memory_only + folded + later_copies, "learning": learning,
              "memory_only": memory_only, "duplicates_ignored": copies + folded + later_copies - learning}
    if folded:      # absent while zero: an epoch without consolidated readings reports what it always did
        counts["consolidated"] = folded
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
    if has_consolidated(db):
        # Consolidated readings have no id to order by: they come first, in seq order. The prototype never
        # consolidated, so with them the sums can differ from legacy_live's in the last bits (not in the readings).
        skip = set(_preceded(db, epoch))
        records = [row[:4] for row in db.execute(
            "SELECT c.channel, c.root, c.value, 0, c.seq FROM evidence_consolidated c "
            f"INDEXED BY evidence_consolidated_epoch WHERE c.epoch=? AND {_EARLIEST} ORDER BY c.seq", (epoch,))
            if row[4] not in skip] + records
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
    preceded = _preceded(db, epoch)         # read once for the tallies and the change window
    counts, raw = _aggregate(db, epoch, bounds, preceded)
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
        change = _detect(db, epoch, known, preceded)
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

def _window(db, epoch, limit, preceded=None):
    """The epoch's latest `limit` learning readings in seq order. A raw event carries EVENT_COLUMNS; a consolidated
    reading carries seq, channel, root, bit (its corrected reading), action 0 and "consolidated": True. Consolidated
    readings that do not count are left out: later copies of a sample by the statement, those with an earlier copy
    in the journal by `preceded` (_preceded(db, epoch), read here when not given)."""
    rows = db.execute(f"SELECT {','.join(EVENT_COLUMNS)} FROM evidence INDEXED BY evidence_epoch "
                      f"WHERE epoch=? AND {learning_sql(db)} ORDER BY seq DESC LIMIT ?", (epoch, limit)).fetchall()
    events = [event_dict(row) for row in rows]
    if has_consolidated(db):
        skip = set(_preceded(db, epoch) if preceded is None else preceded)
        kept = [{"seq": seq, "channel": channel, "root": root, "bit": value, "action": 0, "consolidated": True}
                for seq, channel, root, value in db.execute(
                    "SELECT c.seq, c.channel, c.root, c.value FROM evidence_consolidated c INDEXED BY "
                    f"evidence_consolidated_epoch WHERE c.epoch=? AND {_EARLIEST} ORDER BY c.seq DESC LIMIT ?",
                    (epoch, limit + len(skip)))
                if seq not in skip]
        events = sorted(events + kept, key=lambda e: -e["seq"])[:limit]
    return events[::-1]


def _detect(db, epoch, known, preceded=None):
    confirmed, rejected = known["confirmed"], known["rejected"]
    window = _window(db, epoch, CHANGE_WINDOW, preceded)
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


# ---- panel dynamics (decision D-1, docs/quality/PREREGISTRATION_D1.md) -----------------------------------

ANSWER_COUNTERS = {True: "epoch_answers_continue", False: "epoch_answers_new"}


def epoch_context(db, epoch):
    """The graph context declared for the epoch's panel, or None."""
    if not _has_table(db, "epoch_contexts"):
        return None
    row = db.execute("SELECT context FROM epoch_contexts WHERE epoch=?", (epoch,)).fetchone()
    return row[0] if row else None


def latest_stream(db, epoch):
    """The epoch's most recently attached population stream: {"id", "rows", "cols", "provenance", "created_at",
    "expires_at", "states", "targets"}, or None."""
    if not _has_table(db, "epoch_streams"):
        return None
    row = db.execute("SELECT id, rows, cols, sha256, blob, provenance, created_at, expires_at FROM epoch_streams "
                     "WHERE epoch=? ORDER BY id DESC LIMIT 1", (epoch,)).fetchone()
    if row is None:
        return None
    sid, rows, cols, digest, blob, provenance, created, expires = row
    states, targets = datasets.decode(blob, digest, rows, cols, f"Stream {sid} of epoch {epoch}")
    return {"id": sid, "rows": rows, "cols": cols, "provenance": provenance, "created_at": created,
            "expires_at": expires, "states": states, "targets": targets}


def gaussian_log_likelihood(residuals, variance):
    """Sum over steps and populations of log N(residual; 0, variance): an isotropic Gaussian residual model."""
    residuals = np.asarray(residuals, dtype=np.float64)
    return float(-.5 * residuals.size * math.log(2 * math.pi * variance)
                 - np.sum(residuals * residuals) / (2 * variance))


def new_setup_dynamics(targets):
    """(log p(stream | new panel), variance): the zero predictor with variance = the stream's mean squared target,
    i.e. a panel whose dynamics are unknown. None when that variance is not positive (all-zero targets)."""
    variance = float(np.mean(np.asarray(targets, dtype=np.float64) ** 2))
    if not variance > 0 or not math.isfinite(variance):
        return None
    return gaussian_log_likelihood(targets, variance), variance


def _graph_dynamics(db, context, states, targets):
    """(log p(stream | G), detail) under the ACTIVE graph of `context` with variance = its stored validation MSE, or
    (None, why) when the context has no active graph, no validation MSE, or a population count that differs."""
    if context is None:
        return None, "no graph context declared for this epoch"
    row = db.execute("SELECT id, rows, cols, blob, metrics FROM graph_revisions WHERE context=? AND status='active'",
                     (context,)).fetchone()
    if row is None:
        return None, f"context {context} has no active graph"
    revision, rows, cols, blob, metrics = row
    variance = json.loads(metrics or "{}").get("validation_mse")
    if not isinstance(variance, (int, float)) or isinstance(variance, bool) or not variance > 0 \
            or not math.isfinite(variance):
        return None, f"graph revision {revision} of context {context} has no validation MSE"
    if rows != cols or cols != states.shape[1]:
        return None, f"graph revision {revision} has {cols} populations, the stream {states.shape[1]}"
    residuals = targets - states @ decode(blob).T
    return gaussian_log_likelihood(residuals, float(variance)), {"revision_id": revision, "variance": float(variance)}


def answer_prior(db):
    """P(new setup) = (n_new + 1) / (n_new + n_continue + 2) from the owner's answers to epoch questions.

    n_continue / n_new count resolve(..., confirm=True / False) on 'epoch:<E>' questions in this instance
    (counters epoch_answers_continue / epoch_answers_new), so the first decision uses 1/2.
    """
    n_new = store.counter(db, ANSWER_COUNTERS[False])
    n_continue = store.counter(db, ANSWER_COUNTERS[True])
    return {"new_setup": (n_new + 1) / (n_new + n_continue + 2), "n_new": n_new, "n_continue": n_continue}


# ---- epoch matching (continued) -----------------------------------------------------------------------

def match_epochs(db, epoch, *, last=MATCH_EVENTS):
    """Is this epoch's recent evidence an earlier setup returning, or a new setup?

    Bits: scores the last `last` learning events of `epoch` under each OTHER epoch with learning evidence, taking
    the best configuration of that epoch's segments (see _candidate; it includes the log(#segments)
    multiple-comparison penalty), against a NEW setup scored exactly by new_setup_log_likelihood. Without references
    in the window, a candidate can beat a new setup by at most a factor 2 per shared camera root or partner (a fresh
    bit matches it half the time), however many readings arrive.

    Dynamics (decision D-1): when `epoch` has an attached population stream, its most recent one is scored too.
    A candidate whose epoch declares a graph context with an ACTIVE graph that has a validation MSE scores
    log p(stream | G) with an isotropic Gaussian residual of that variance; the new setup scores the zero predictor
    with variance = the stream's mean squared target. A candidate without such a graph (no context, no active graph,
    no validation MSE, other population count) is scored with the new setup's dynamics term, so the stream neither
    favours nor penalises it. Without a stream (or with all-zero targets) there is no dynamics term.

    Prior: P(new setup) = answer_prior(db) (1/2 before the owner answered any epoch question); the rest is split
    evenly across the candidate epochs.

    Returns {"epoch", "events", "ranking": [{"epoch", "segments", "log_likelihood", "bits_log_likelihood",
    "dynamics_log_likelihood", "dynamics", "penalty", "posterior", "events"}], "best", "best_posterior",
    "new_setup_posterior", "new_setup_log_likelihood", "new_setup_bits_log_likelihood",
    "new_setup_dynamics_log_likelihood", "stream", "prior", "ask_posterior"}. log_likelihood = bits + dynamics
    (bits already net of the penalty); "dynamics" is None without a stream, else {"source": "graph" or "new_setup",
    "context", "revision_id", "variance", "why"}. "best" is the top epoch when its posterior >= MATCH_POSTERIOR (.75).
    """
    require_epoch(db, epoch)
    if isinstance(last, bool) or not isinstance(last, int) or not 1 <= last <= 1000:
        raise ValidationError("last must be an integer between 1 and 1000")
    events = _window(db, epoch, last)
    tallies = _window_tallies(events)
    new_bits = new_setup_log_likelihood(tallies)
    observed = latest_stream(db, epoch)
    new_dynamics = new_setup_dynamics(observed["targets"]) if observed is not None else None
    ranking = []
    for (other,) in db.execute("SELECT name FROM epochs WHERE name!=? ORDER BY created_at, name", (epoch,)).fetchall():
        candidate = _candidate(db, other, tallies)
        if candidate is None:
            continue
        bits = candidate.pop("log_likelihood")
        candidate.update(bits_log_likelihood=bits, dynamics_log_likelihood=None, dynamics=None)
        if new_dynamics is not None:
            context = epoch_context(db, other)
            value, detail = _graph_dynamics(db, context, observed["states"], observed["targets"])
            if value is None:
                candidate["dynamics"] = {"source": "new_setup", "context": context, "revision_id": None,
                                         "variance": new_dynamics[1], "why": detail}
                value = new_dynamics[0]
            else:
                candidate["dynamics"] = {"source": "graph", "context": context, "why": None, **detail}
            candidate["dynamics_log_likelihood"] = value
        candidate["log_likelihood"] = bits + (candidate["dynamics_log_likelihood"] or 0.0)
        ranking.append(candidate)
    new_setup = new_bits + (new_dynamics[0] if new_dynamics is not None else 0.0)
    prior = answer_prior(db)
    log_new = math.log(prior["new_setup"])
    log_each = math.log((1 - prior["new_setup"]) / len(ranking)) if ranking else None
    total = log_new + new_setup
    for r in ranking:
        total = _lse(total, log_each + r["log_likelihood"])
    for r in ranking:
        r["posterior"] = math.exp(log_each + r["log_likelihood"] - total)
    ranking.sort(key=lambda r: (-r["log_likelihood"], r["epoch"]))
    best = ranking[0]["epoch"] if ranking and ranking[0]["posterior"] >= MATCH_POSTERIOR else None
    stream = None if observed is None else {key: observed[key] for key in ("id", "rows", "cols", "provenance",
                                                                           "created_at", "expires_at")}
    return {"epoch": epoch, "events": len(events), "ranking": ranking, "best": best,
            "best_posterior": ranking[0]["posterior"] if best is not None else None,
            "new_setup_posterior": math.exp(log_new + new_setup - total),
            "new_setup_log_likelihood": new_setup, "new_setup_bits_log_likelihood": new_bits,
            "new_setup_dynamics_log_likelihood": new_dynamics[0] if new_dynamics is not None else None,
            "stream": stream, "prior": prior, "ask_posterior": MATCH_POSTERIOR}


# ---- explanations -------------------------------------------------------------------------------------

def _load(db, epoch):
    """The epoch's learning events in seq order (consent='learn'; copies of one sample counted once). Episodes of
    the journal only: consolidated readings (ADR-044) are counted by explain(), never listed."""
    return [event_dict(r) for r in db.execute(
        f"SELECT {','.join(EVENT_COLUMNS)} FROM evidence WHERE epoch=? AND {learning_sql(db)} ORDER BY seq",
        (epoch,))]


def _split(events, predicted):
    agree = [e for e in events if predicted is not None and e["bit"] ^ e["action"] == predicted]
    disagree = [e for e in events if predicted is not None and e["bit"] ^ e["action"] != predicted]
    return agree, disagree


def _factor(scope, p, events, predicted, uncertainty, consolidated=(0, 0)):
    """`events`: the factor's raw events, which the lists hold (an episode has an id and a time). `consolidated`:
    (n0, n1), its consolidated readings by corrected reading: counted in the totals and in *_consolidated (absent
    while zero), never listed. As with events, neither side counts anything while there is no MAP reading."""
    supporting, counter = _split(events, predicted)
    kept = (0, 0) if predicted is None else (consolidated[predicted], consolidated[1 - predicted])
    out = {"scope": scope, "p_inverted": p, "supporting": supporting[-EXPLAIN_EVENTS:],
           "counterevidence": counter[-EXPLAIN_EVENTS:], "supporting_total": len(supporting) + kept[0],
           "counterevidence_total": len(counter) + kept[1], "uncertainty": uncertainty}
    for name, n in (("supporting_consolidated", kept[0]), ("counterevidence_consolidated", kept[1])):
        if n:
            out[name] = n
    return out


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
    # One pass under all the epoch's bounds (the buckets compute() used); a scope's own segment of a bucket follows
    # from where the bucket begins, as in _model.
    every = sorted({seq for seqs in confirmed.values() for seq in seqs})
    kept = _consolidated_tallies(db, epoch, every)[0] if has_consolidated(db) else []

    def consolidated(channel, bounds, segment, root=None):
        """(n0, n1): consolidated readings of a channel (and root) in `segment` of the scope's own bounds."""
        counts = [0, 0]
        for row_channel, row_root, value, _, b, n, _ in kept:
            if row_channel == channel and root in (None, row_root) \
                    and _segment(bounds, every[b - 1] if b else -1) == segment:
                counts[value] += n
        return counts

    current_mechanism = len(mechanism_bounds)
    references = [e for e in learning if e["channel"] == "reference"
                  and _segment(mechanism_bounds, e["seq"]) == current_mechanism]
    kept_references = consolidated("reference", mechanism_bounds, current_mechanism)
    if not result["evidence"]["learning"]:
        why = "no learning evidence yet; act and record camera/word reports"
    elif not references and not sum(kept_references):
        why = ("only camera and word evidence: the joint flip symmetry makes the mechanism unidentifiable "
               "without an independent reference")
    elif identifiable:
        why = (f"{len(references) + sum(kept_references)} reference reading(s) from {result['reference_roots']} "
               f"root(s) pin the mechanism at p_inverted={p_w:.3f}")
    else:
        why = _mechanism_why(p_w, result["reference_roots"], result["evidence"]["learning"])
    factors = [_factor("mechanism", p_w, references, _map(p_w), why, kept_references)]
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
        factors.append(_factor(name, p, events, _map(relation), why, consolidated(channel, seqs, fseg, root)))
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

