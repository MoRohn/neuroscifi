"""Imagination on the individual's beliefs (Cognitive Genesis lane M, ADR-052): exact, checkable answers to "what
if", computed on a sandbox and never written to the individual.

sandbox(db)       A private in-memory copy of the individual's file (sqlite backup, the pattern the acceptance
                  scenarios and the cognitive benchmark already use). Everything below runs on the copy; the live
                  connection is only read. Bounded: a file larger than InstanceConfig.imagine_max_bytes is not copied,
                  and work on the copy stops after imagine_max_seconds (an sqlite progress handler).
what_if           The exact posterior had some readings been different (flip), absent (drop), or had more readings
                  arrived (add: "four more readings of partner p that agree with what it believes"). It is the
                  belief model's own exact inference (beliefs.compute) on the altered copy, so the answer is the model's
                  posterior, with the assumptions it rests on stated; its uncertainty is that posterior.
question_values   For each reading the individual could take next (one more reading of a camera root, a partner or a
                  reference root): the probability of each outcome under the current beliefs, the posterior after it,
                  the expected change and the mutual information with each hidden bit (bits). An input to an
                  information-gain score; it decides nothing and changes no executive rule.
retrospect        Which earlier reading or owner answer most changed a belief: exact leave-one-out on the copy.

The recorded actions do not support an action-conditioned model of the beliefs: an action enters the model only as
the declared inversion of the reading (bit XOR action, the birth record's action semantics), and nothing recorded
says what an action did to the world. A question about the consequence of an action on the beliefs is therefore
declined (ADR-052); forecast.py answers pushes on a graph where the recorded trajectories identify them.

Nothing here writes to the individual or counts as evidence. keep() stores an answer in the imagined store (kind
'simulation', derived from every epoch, camera, partner and reading it names, so a forget of any removes it) when the
owner asks for it; nowhere else.
"""
from contextlib import contextmanager
import math
import secrets
import sqlite3
import time

from .. import provenance
from . import beliefs, store
from .errors import InstanceError, NotFound, ValidationError

VERSION = "1"
MAX_CHANGES = 1000            # readings flipped, dropped or added in one what-if
MAX_RETROSPECT = 2000         # readings and answers tried by one retrospection
MODEL = ("the belief model declared at birth: hidden bits with uniform priors (the mechanism, one alignment bit per "
         f"camera root, one convention bit per partner); a camera or word reading equals mechanism XOR its bit with "
         f"error {beliefs.READING_ERROR}; a reference root's fixture polarity flips with probability "
         f"{beliefs.REFERENCE_FLIP} and its readings err with probability {beliefs.REFERENCE_ERROR}")
KEPT = ("every reading the question does not name stays as recorded (journal and consolidated readings, copies of one "
        "sample counted once), and so do the owner's confirmed changes and the epoch's link to an earlier setup")
EXACT = ("exact under the model: these are the model's own posterior probabilities, with no sampling or approximation "
         "error; they are only as right as the model's assumptions")


class Refused(InstanceError):
    """A question imagination does not answer: its switch is off, the copy would be too large or took too long, or
    the question is outside what the model can answer."""
    http_status = 409


def _require(db, setting):
    if not getattr(store.config(db), setting):
        raise Refused(f"{setting} is off (`nsf config --{setting.replace('_', '-')}` turns it on)")


class Box:
    """A sandbox: `db` is the private copy; `cost()` says what it took."""

    def __init__(self, copy, size, started):
        self.db, self.bytes, self._started = copy, size, started

    def cost(self):
        return {"copied_bytes": self.bytes, "seconds": round(time.perf_counter() - self._started, 6)}


@contextmanager
def sandbox(db, *, max_bytes=None, max_seconds=None):
    """A private in-memory copy of the individual's file for trials that must not touch it.

    The live connection is only read (sqlite backup). The copy is an in-memory database: nothing done to it reaches
    the file, and it is gone when the block ends. Refused when the file is larger than max_bytes
    (InstanceConfig.imagine_max_bytes), or when the copy and the work on it take longer than max_seconds
    (imagine_max_seconds; the copy's statements are interrupted)."""
    cfg = store.config(db)
    max_bytes = cfg.imagine_max_bytes if max_bytes is None else max_bytes
    max_seconds = cfg.imagine_max_seconds if max_seconds is None else max_seconds
    size = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
    if size > max_bytes:
        raise Refused(f"the individual's file is {size / 2**20:.1f} MiB, above the {max_bytes / 2**20:.0f} MiB a "
                      "sandbox may copy (`nsf config --imagine-max-mib M`)")
    started = time.perf_counter()
    deadline = started + max_seconds

    def late(*_):
        if time.perf_counter() > deadline:
            raise Refused(f"the sandbox ran out of time ({max_seconds:g} s; `nsf config --imagine-max-seconds S`)")

    copy = sqlite3.connect(":memory:", isolation_level=None)
    try:
        # One step, one read snapshot (review1 Q2): a paged copy restarts whenever another connection commits, and
        # under a writer committing every 2 ms a 257 MiB file never finished in 10 s (a single step: 0.07 s; the
        # numbers are in lane fix1b's report). A WAL reader does not hold writers back, and the size is bounded above.
        db.backup(copy, pages=-1, progress=late)
        if store.database_path(copy) is not None:      # by construction; checked so no change can make it a file
            raise RuntimeError("a sandbox must be an in-memory database")
        copy.execute("PRAGMA foreign_keys=ON")
        copy.execute("PRAGMA recursive_triggers=ON")
        copy.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 2000)
        try:
            yield Box(copy, size, started)
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                raise Refused(f"the sandbox ran out of time ({max_seconds:g} s; "
                              "`nsf config --imagine-max-seconds S`)") from None
            raise
    finally:
        copy.close()


@contextmanager
def _trial(copy):
    """Changes made inside the block are undone at its end (a savepoint of the copy)."""
    copy.execute("SAVEPOINT trial")
    try:
        yield copy
    finally:
        copy.execute("ROLLBACK TO trial")
        copy.execute("RELEASE trial")


# ---- naming readings and factors -------------------------------------------------------------------------------------

_SCOPES = {"camera": "camera", "partner": "word", "reference": "reference"}


def _scope(text):
    """'camera:r' | 'partner:p' | 'reference:r' -> (channel, root)."""
    if not isinstance(text, str) or ":" not in text:
        raise ValidationError(f"a factor is camera:ROOT, partner:ROOT or reference:ROOT, got {text!r}")
    prefix, root = text.split(":", 1)
    if prefix not in _SCOPES or not root.strip() or len(root) > 128:
        raise ValidationError(f"a factor is camera:ROOT, partner:ROOT or reference:ROOT, got {text!r}")
    return _SCOPES[prefix], root


def _reading(copy, epoch, ref):
    """A learning reading of the epoch named by its evidence id, or '#SEQ' for a consolidated reading:
    (table, seq, channel, root, corrected reading)."""
    if isinstance(ref, str) and ref.startswith("#") and ref[1:].isdigit():
        row = copy.execute("SELECT seq, channel, root, value FROM evidence_consolidated WHERE seq=? AND epoch=?",
                           (int(ref[1:]), epoch)).fetchone() if beliefs.has_consolidated(copy) else None
        if row is None:
            raise NotFound(f"No consolidated reading {ref} in epoch {epoch!r}")
        return ("evidence_consolidated", *row)
    row = copy.execute("SELECT seq, channel, root, (bit<>action), consent FROM evidence WHERE id=? AND epoch=?",
                       (ref, epoch)).fetchone()
    if row is None:
        raise NotFound(f"No reading {ref!r} in epoch {epoch!r} (a consolidated reading is named #SEQ)")
    if row[4] != "learn":
        raise ValidationError(f"reading {ref!r} is kept for memory only (consent memory_only): it changes no belief")
    return ("evidence", *row[:4])


def _label(channel, root):
    return "reference:" + root if channel == "reference" else beliefs.scope_of(channel, root)


def _flip(copy, table, seq):
    column = "value=1-value" if table == "evidence_consolidated" else "bit=1-bit"
    copy.execute(f"UPDATE {table} SET {column} WHERE seq=?", (seq,))


def _drop(copy, table, seq):
    copy.execute(f"DELETE FROM {table} WHERE seq=?", (seq,))


def _add(copy, epoch, channel, root, value, count, now):
    """`count` more learning readings of (channel, root) with corrected reading `value`, after every recorded one."""
    start = secrets.token_hex(6)
    copy.executemany(
        "INSERT INTO evidence(id,epoch,channel,action,bit,root,actor,consent,provenance,recorded_at,retention,"
        "expires_at) VALUES(?,?,?,0,?,?,'imagination','learn','simulated',?,'standard',NULL)",
        [(f"what-if:{start}:{i}", epoch, channel, value, root, now) for i in range(count)])


def _state(copy, epoch, *, detect=False):
    """(beliefs summary, log marginal likelihood) of the copy."""
    result, inference, _, _ = beliefs._assess(copy, epoch, detect=detect, legacy=False)
    out = {"mechanism": {"p_inverted": result["mechanism"]["p_inverted"],
                         "identifiable": result["mechanism"]["identifiable"]}}
    for key, group in (("camera", "sensors"), ("partner", "partners")):
        for root, entry in result[group].items():
            out[f"{key}:{root}"] = {"p_inverted": entry["p_inverted"],
                                    "identifiable": beliefs._identifiable(entry["p_inverted"]), "n": entry["n"]}
    if detect:
        change = result["change"]
        out["change"] = {"suspected": change["suspected"], "scope": change["scope"], "llr": change["llr"]}
    return out, inference["log_marginal"]


def _p(summary, factor):
    """P(bit = 1) of a factor in a summary; 0.5 (the uniform prior) for a factor the readings no longer name."""
    entry = summary.get(factor)
    return 0.5 if entry is None else entry["p_inverted"]


def _predictive(copy, epoch, channel, root, now):
    """P(the next corrected reading of (channel, root) is 1) under the current beliefs, exactly: the ratio of the
    marginal likelihoods with that reading added (P(x) = Z(data, x) / Z(data))."""
    lm = []
    for value in (0, 1):
        with _trial(copy):
            _add(copy, epoch, channel, root, value, 1, now)
            lm.append(_state(copy, epoch)[1])
    return 1.0 / (1.0 + math.exp(lm[0] - lm[1]))


def _entropy(p):
    return 0.0 if p <= 0.0 or p >= 1.0 else -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _factors(summary):
    return [k for k in summary if k != "change"]


# ---- what if ---------------------------------------------------------------------------------------------------------

def _parse_add(item):
    """{"factor": "partner:p", "reading": 0 | 1 | "agree" | "disagree", "count": n}."""
    if not isinstance(item, dict) or set(item) - {"factor", "reading", "count"}:
        raise ValidationError('an added reading is {"factor": "partner:p", "reading": 0|1|"agree"|"disagree", '
                              '"count": n}')
    channel, root = _scope(item.get("factor"))
    reading = item.get("reading", "agree")
    if reading not in (0, 1, "agree", "disagree") or isinstance(reading, bool):
        raise ValidationError("reading must be 0, 1, 'agree' or 'disagree'")
    count = item.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_CHANGES:
        raise ValidationError(f"count must be an integer from 1 to {MAX_CHANGES}")
    return channel, root, reading, count


def what_if(db, epoch, *, flip=(), drop=(), add=(), now=None, detect=True):
    """The exact beliefs of `epoch` had the readings in `flip` read the other way, the readings in `drop` never
    arrived, and the readings described by `add` arrived after every recorded one. Readings are named by evidence id,
    or '#SEQ' for a consolidated reading; an added reading's value is its corrected reading (bit XOR action), or
    'agree' / 'disagree' with the factor's most probable next reading under the current beliefs.

    Returns {"label", "kind", "question", "assumptions", "uncertainty", "before", "after", "difference", "changes",
    "cost", ...}: "before" is today's beliefs (computed on the copy, equal to the live ones), "after" the beliefs
    under the stated changes. Nothing is written to the individual."""
    _require(db, "imagine_what_if")
    beliefs.require_epoch(db, epoch)
    flip, drop, add = list(flip), list(drop), [_parse_add(a) for a in add]
    if not (flip or drop or add):
        raise ValidationError("name at least one reading to flip or drop, or readings to add")
    if len(flip) + len(drop) + sum(a[3] for a in add) > MAX_CHANGES:
        raise ValidationError(f"at most {MAX_CHANGES} readings may be changed in one what-if")
    if set(flip) & set(drop):
        raise ValidationError("a reading cannot be both flipped and dropped")
    now = time.time() if now is None else now
    with sandbox(db) as box:
        copy = box.db
        before, _ = _state(copy, epoch, detect=detect)
        assumptions, changes, plans = [MODEL, KEPT], [], []
        for ref in flip:
            table, seq, channel, root, value = _reading(copy, epoch, ref)
            plans.append(("flip", table, seq))
            changes.append({"change": "flip", "reading": ref, "factor": _label(channel, root), "recorded": value,
                            "imagined": 1 - value})
            assumptions.append(f"reading {ref} ({_label(channel, root)}) read {1 - value} instead of {value}")
        for ref in drop:
            table, seq, channel, root, value = _reading(copy, epoch, ref)
            plans.append(("drop", table, seq))
            changes.append({"change": "drop", "reading": ref, "factor": _label(channel, root), "recorded": value})
            assumptions.append(f"reading {ref} ({_label(channel, root)}) never arrived")
        for channel, root, reading, count in add:
            if reading in ("agree", "disagree"):
                likely = int(_predictive(copy, epoch, channel, root, now) >= 0.5)
                value = likely if reading == "agree" else 1 - likely
            else:
                value = reading
            plans.append(("add", channel, root, value, count))
            changes.append({"change": "add", "factor": _label(channel, root), "reading": value, "count": count,
                            "as_asked": reading})
            assumptions.append(f"{count} more reading(s) of {_label(channel, root)} read {value} (corrected reading: "
                               "bit XOR action), after every recorded one and in the current segment of each factor")
        for plan in plans:
            if plan[0] == "flip":
                _flip(copy, plan[1], plan[2])
            elif plan[0] == "drop":
                _drop(copy, plan[1], plan[2])
            else:
                _add(copy, epoch, plan[1], plan[2], plan[3], plan[4], now)
        after, _ = _state(copy, epoch, detect=detect)
        cost = box.cost()
    factors = sorted(set(_factors(before)) | set(_factors(after)))
    return {"label": provenance.NOT_EVIDENCE, "kind": provenance.SIMULATION, "mechanism": "imagine.what_if",
            "mechanism_version": VERSION, "epoch": epoch, "exact": True,
            "question": "what would the beliefs be if " + "; ".join(assumptions[2:]),
            "assumptions": assumptions, "uncertainty": EXACT, "changes": changes, "before": before, "after": after,
            "difference": {f: _p(after, f) - _p(before, f) for f in factors}, "cost": cost}


# ---- comparing possible next readings -------------------------------------------------------------------------------

def _reference_roots(copy, epoch):
    roots = {r for (r,) in copy.execute("SELECT DISTINCT root FROM evidence WHERE epoch=? AND channel='reference' "
                                        "AND consent='learn'", (epoch,))}
    if beliefs.has_consolidated(copy):
        roots |= {r for (r,) in copy.execute("SELECT DISTINCT root FROM evidence_consolidated WHERE epoch=? AND "
                                             "channel='reference'", (epoch,))}
    return sorted(roots)


def question_values(db, epoch, *, now=None):
    """For each reading the individual could take next, the candidate futures it opens, computed exactly on a sandbox:
    [{"factor", "p_reading": {0, 1}, "after": {0: beliefs, 1: beliefs}, "expected_change": {factor: E|change|},
    "information_bits": {factor: mutual information of the reading with that hidden bit}, "information_bits_total"}],
    sorted by information_bits_total, highest first. "information_bits_total" sums the per-bit mutual information
    (not the joint information of all bits); "information_bits_joint" is the joint one for a camera or partner, taken
    from lane I's definition (information.gains with one reading: H(Y) - H(Y | hidden bits)), which equals
    h(p_reading) - h(reading error) from this copy's own predictive probability (tests/instance/test_imagine.py).
    A reading of an existing reference root informs the mechanism; a reading
    at a new reference root informs nothing (its polarity is unknown), so it is not listed. Decides nothing."""
    _require(db, "imagine_what_if")
    beliefs.require_epoch(db, epoch)
    now = time.time() if now is None else now
    with sandbox(db) as box:
        copy = box.db
        before, base = _state(copy, epoch)
        from . import information          # lane I's exact information gain (ADR-051): one definition of it
        joint = {c["subject"]: c["eig_bits"] for c in information.gains(copy, epoch, k=1)["candidates"]}
        candidates = [_scope(f) for f in _factors(before) if f != "mechanism"]
        candidates += [("reference", r) for r in _reference_roots(copy, epoch)]
        out = []
        for channel, root in candidates:
            lm, after = [], {}
            for value in (0, 1):
                with _trial(copy):
                    _add(copy, epoch, channel, root, value, 1, now)
                    after[value], m = _state(copy, epoch)
                    lm.append(m)
            p1 = 1.0 / (1.0 + math.exp(lm[0] - lm[1]))
            p = {0: 1.0 - p1, 1: p1}
            factors = sorted(set(_factors(before)) | set(_factors(after[0])) | set(_factors(after[1])))
            info = {f: _entropy(_p(before, f)) - sum(p[x] * _entropy(_p(after[x], f)) for x in (0, 1))
                    for f in factors}
            out.append({"factor": _label(channel, root), "p_reading": p, "after": after,
                        "expected_change": {f: sum(p[x] * abs(_p(after[x], f) - _p(before, f)) for x in (0, 1))
                                            for f in factors},
                        "information_bits": info, "information_bits_total": sum(info.values()),
                        "information_bits_joint": None if channel == "reference" else
                        joint.get(_label(channel, root))})
        cost = box.cost()
    out.sort(key=lambda c: (-c["information_bits_total"], c["factor"]))
    return {"label": provenance.NOT_EVIDENCE, "kind": provenance.SIMULATION, "mechanism": "imagine.question_values",
            "mechanism_version": VERSION, "epoch": epoch, "exact": True, "before": before, "candidates": out,
            "assumptions": [MODEL, KEPT, "each candidate is one more reading of that factor, after every recorded one"],
            "uncertainty": EXACT, "cost": cost}


# ---- retrospection ---------------------------------------------------------------------------------------------------

def retrospect(db, epoch, factor="mechanism", *, limit=200, top=10):
    """Which earlier reading or owner answer most changed `factor`'s belief ('mechanism', 'camera:r' or 'partner:p'):
    for each of the epoch's latest `limit` learning readings (journal and consolidated) and each of the owner's
    answers that shape it (confirmed or rejected changes, the link to an earlier setup), the exact belief without it,
    on a sandbox. Readings that are interchangeable (same factor, segment and reading) have the same influence and
    are grouped. Returns the `top` groups by |influence|, influence = belief now - belief without it."""
    _require(db, "imagine_retrospect")
    beliefs.require_epoch(db, epoch)
    if factor != "mechanism":
        channel, root = _scope(factor)
        if channel == "reference":
            raise ValidationError("a reference root has no belief of its own: ask about the mechanism")
        factor = beliefs.scope_of(channel, root)
    for name, value, high in (("limit", limit, MAX_RETROSPECT), ("top", top, 100)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
            raise ValidationError(f"{name} must be an integer from 1 to {high}")
    with sandbox(db) as box:
        copy = box.db
        now_summary, _ = _state(copy, epoch)
        if factor not in now_summary:
            raise NotFound(f"epoch {epoch!r} has no belief about {factor}")
        p_now = _p(now_summary, factor)
        items = [("evidence", seq, eid, channel, root, value) for seq, eid, channel, root, value in copy.execute(
            "SELECT seq, id, channel, root, (bit<>action) FROM evidence WHERE epoch=? AND consent='learn' "
            "ORDER BY seq DESC LIMIT ?", (epoch, limit))]
        if beliefs.has_consolidated(copy):
            items += [("evidence_consolidated", seq, f"#{seq}", channel, root, value)
                      for seq, channel, root, value in copy.execute(
                          "SELECT seq, channel, root, value FROM evidence_consolidated WHERE epoch=? ORDER BY seq DESC "
                          "LIMIT ?", (epoch, limit))]
        items = sorted(items, key=lambda i: -i[1])[:limit]
        # An owner answer is named by structured fields (the scope it is about, the earlier setup), never by a
        # sentence that repeats a root: keep() records each name as a source, so forgetting it removes the record.
        answers = [("changepoints", cid, {"answer": "confirmed change" if ok else "rejected change", "scope": scope,
                                          "since_seq": since})
                   for cid, scope, since, ok in (copy.execute(
                       "SELECT id, scope, since_seq, confirmed FROM changepoints WHERE epoch=? ORDER BY id", (epoch,))
                       if beliefs._has_changepoints(copy) else ())]
        if beliefs._has_table(copy, "epoch_links") and copy.execute(
                "SELECT 1 FROM epoch_links WHERE epoch=?", (epoch,)).fetchone():
            source = copy.execute("SELECT source_epoch FROM epoch_links WHERE epoch=?", (epoch,)).fetchone()[0]
            answers.append(("epoch_links", epoch, {"answer": "continuing an earlier setup", "source_epoch": source}))
        groups = {}
        for table, seq, name, channel, root, value in items:
            with _trial(copy):
                _drop(copy, table, seq)
                influence = p_now - _p(_state(copy, epoch)[0], factor)
            key = (_label(channel, root), value, round(influence, 12))
            groups.setdefault(key, []).append(name)
        found = [{"what": "readings", "factor": label, "reading": value, "influence": influence,
                  "count": len(names), "readings": names[:20]} for (label, value, influence), names in groups.items()]
        for table, key, fields in answers:
            with _trial(copy):
                column = "epoch" if table == "epoch_links" else "id"
                copy.execute(f"DELETE FROM {table} WHERE {column}=?", (key,))
                influence = p_now - _p(_state(copy, epoch)[0], factor)
            found.append({"what": "owner answer", **fields, "influence": influence})
        cost = box.cost()
    found.sort(key=lambda f: (-abs(f["influence"]), f.get("factor", ""), f.get("answer", "")))
    return {"label": provenance.NOT_EVIDENCE, "kind": provenance.SIMULATION, "mechanism": "imagine.retrospect",
            "mechanism_version": VERSION, "epoch": epoch, "factor": factor, "belief_now": p_now, "exact": True,
            "tried": {"readings": len(items), "owner_answers": len(answers)}, "most": found[0] if found else None,
            "ranked": found[:top],
            "assumptions": [MODEL, KEPT, "each trial removes one reading or answer and keeps everything else; the "
                            f"{limit} latest readings are tried"],
            "uncertainty": EXACT, "cost": cost}


# ---- keeping an answer -----------------------------------------------------------------------------------------------

def _reading_ref(name):
    return ("consolidated", name[1:]) if name.startswith("#") else ("evidence", name)


def sources(payload):
    """Every record a kept answer names, as imagined references: its epoch (and an earlier setup it continues),
    each camera and partner root (a key of a beliefs map, a factor, a change's scope) and each reading (flipped,
    dropped, or grouped by a retrospection). A forget of any of them removes the kept answer (imagined.purge_orphans),
    so no name it holds outlives its forget. Reference roots are fixtures, not private names, and are not listed."""
    refs = {("epoch", payload["epoch"])}

    def root(text):
        kind, _, name = text.partition(":") if isinstance(text, str) else ("", "", "")
        if kind in ("camera", "partner") and name:
            refs.add((kind, name))

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                root(key)
                if key in ("factor", "scope"):
                    root(item)
                elif key == "source_epoch" and isinstance(item, str):
                    refs.add(("epoch", item))
                elif key == "readings" and isinstance(item, list):
                    refs.update(_reading_ref(r) for r in item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    for change in payload.get("changes", ()):
        if change["change"] in ("flip", "drop"):
            refs.add(_reading_ref(change["reading"]))
    return sorted(refs)


def keep(db, answer, now, *, lifetime=None):
    """Store a what_if, question_values or retrospect answer in the imagined store (kind 'simulation'), derived from
    every record it names (sources): forgetting any of them removes it. Refused, never truncated, when it names more
    than imagined.MAX_REFS or names a root this individual holds no reading of. The only write imagination makes;
    never evidence."""
    from . import imagined
    payload = {k: answer[k] for k in ("epoch", "changes", "before", "after", "difference", "candidates", "factor",
                                      "belief_now", "most", "ranked") if k in answer}
    refs = sources(payload)
    if len(refs) > imagined.MAX_REFS:
        raise Refused(f"this answer names {len(refs)} readings, cameras, partners and setups; a kept answer may name "
                      f"at most {imagined.MAX_REFS}, so that forgetting any of them removes it: ask a narrower "
                      "question (fewer readings, or a smaller --top) to keep it")
    if imagined._unresolved(db, refs):
        raise Refused("this answer names a camera, partner or reading this individual holds nothing of (an imagined "
                      "one), so a later forget could not find it: it is shown but not kept")
    question = answer.get("question") or f"{answer['mechanism']} on epoch {answer['epoch']}"
    return imagined.record(db, provenance.SIMULATION, payload=payload, assumptions=answer["assumptions"],
                           uncertainty=answer["uncertainty"], mechanism=answer["mechanism"],
                           mechanism_version=answer["mechanism_version"], question=question[:2000], now=now,
                           derived_from=refs, lifetime=lifetime)
