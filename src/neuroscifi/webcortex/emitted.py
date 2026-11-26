"""ADR-067 (Wave 4 lane E): what the individual itself emitted, recorded at emission as claim fingerprints, and the
rule that a page repeating a value only the individual had stated is no evidence for it. Setting
`own_text_provenance` (WebConfig; OFF by default: with it off nothing here runs and nothing is recorded).

EMISSION. A model-written answer stored with a task (orchestrator: the report the console shows, Pocket lists, voice
speaks and MCP returns; the benchmark harnesses store theirs the same way) is the individual's own output. Its
sentences (reasoning["text"] and every reasoning["answer"] sentence; a template, extractive answer quotes sources and
is not read) are read for the pairs (subject name, value): a subject name as consolidation's subject check reads it
(consolidate._name_set) and every value of the sentence, normalised (value_key: a version or dotted release number by
its numeric parts with trailing zero parts dropped, as PEP 440 compares release segments, "2.4" = "v2.4.0"; a date as
its day; money, percentages and quantities by the existing normaliser's group and base-unit value, "41 m" = "4100 cm";
a year or a whole number below 100 is not specific enough and is not read). For each pair, at most MAX_PER_ANSWER per
answer, the emission records: its time, and whether a claim the individual had read at or before that time (this task's
first, then earlier ones naming the subject, at most SUPPORT_ROWS, within SUPPORT_SECONDS) states that value about that
subject: 1 sourced, 0 not, 2 not known in time (counted as sourced: never an echo). No text is recorded, no source's
name or URL: only salted digests (the instance's id is the salt), the time, the flag and a count of the distinct
sources (domains) found stating it (`n`, at most MAX_GROUPS; a count, so a forgotten source leaves nothing of itself).

STORAGE (no schema change): rows of web_strategies (procedural memory; primary-key lookups only; no reader of that
table lists names, and no Pocket, family, MCP or agent surface reads it):
  "emitted:<d>"       d = digest of (subject name, value key); {"e": [[at, sourced, n, task digest], ...]}: the
                      earliest MAX_EMISSIONS emissions, oldest first
  "emitted-task:<t>"  t = digest of the task id; {"k": [d, ...]}: the fingerprints whose emission list holds this task
At most MAX_FINGERPRINTS "emitted:" rows: a full store records nothing more (logged with counts only; the rule then
sees no later emission). The count is read before the write transaction (an index range), as is everything else.

THE RULE (consolidate.run, with the setting on). A page's claim (not a structured record, not a primary record) is
an echo of the individual's own output when, for one of its subject names and one of its values, the EARLIEST recorded
emission of that pair was not sourced (0), was made before the claim was read, and the page's stated publication is
not before it (or the page states none). An echo is never evidence FOR its value: it leaves the supporting side of its
cluster (consolidate._without_echoes, as Wave 3's echo_recognition); an echo that only disagrees stays where it is and
counts against a held value only as today. A value the individual emitted with source support is not an echo: the
sources carry it. The checks are point lookups by digest made before the write transaction: at most MAX_LOOKUPS per
consolidation and MAX_KEYS_PER_CLAIM per claim, shared out claim by claim; a claim whose pairs were not all looked up is
not an echo and is named in `echo_skipped` and logged (ids only).

WHAT IT CANNOT SEE (ADR-067): a value the individual only imagined and never emitted (consolidation never reads the
imagined store, ADR-049); anything it emitted while the setting was off; a page that states a publication date before
the emission (dates are the page's own claim); another system's output; a value without a subject name this reader
finds (a lower-case package name) or one not specific enough (a year, a whole number below 100); a page published
after the emission that reports the value independently (the false rejection: such a page is discarded too, unless
the individual's emission was sourced; ADR-067 measures it); an emission past the caps.

FORGET AND RESTORE. Forgetting a task (research, the refresh tasks of a forgotten item, a voice session's research,
and every checkpoint replay of them: governance._forget_research_core) removes its emissions (forget_task); forgetting
a knowledge item removes the fingerprints of its subject names and values (forget_knowledge; also on replay). A
forgotten source changes nothing here: an emission it supported stays sourced (security review M3) and nothing of the
source was kept.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

from . import text as T

PREFIX = "emitted:"
TASK_PREFIX = "emitted-task:"
MAX_PER_ANSWER = 16
MAX_EMISSIONS = 3
MAX_GROUPS = 8
MAX_FINGERPRINTS = 50_000
MAX_LOOKUPS = 512
MAX_KEYS_PER_CLAIM = 32
SUPPORT_ROWS = 2000
SUPPORT_SECONDS = 1.0
SOURCED, UNSOURCED, UNKNOWN = 1, 0, 2
_DOTTED = re.compile(r"\d+\.\d+")
_log = logging.getLogger(__name__)


# ---- fingerprints ------------------------------------------------------------------------------------------------------

def value_key(value) -> str | None:
    """A value as the fingerprints compare it (see the module docstring), or None when it is not read."""
    if not isinstance(value, str) or not value:
        return None
    kind, group, parsed = T.value_kind(value)
    if kind in ("year", "text"):
        return None
    if kind == "number" and group == "number:int" and abs(parsed) < 100:
        return None
    if kind == "version" or (group == "number:dec" and _DOTTED.fullmatch(value)):
        key = list(T.version_key(value))
        if not key:
            return None
        nums, tail = key[:-2], key[-2:]
        while len(nums) > 1 and nums[-1] == 0:
            nums.pop()
        return "version:" + ".".join(map(str, nums)) + ("" if tail == [4, 0] else "-{}.{}".format(*tail))
    if kind == "date":
        return "date:" + value
    return f"{group}:{float(parsed):.6g}"


def names(sentence) -> set:
    from .consolidate import _name_set
    return {n for n in _name_set(sentence or "") if n}


def pairs(subject_names, values) -> list[tuple[str, str]]:
    """[(name, value key)] in a stable order, without repeats."""
    keys = [k for k in dict.fromkeys(value_key(v) for v in values or ()) if k]
    return [(n, k) for n in sorted(subject_names) for k in keys]


def _digest(salt, kind, value) -> str:
    return hashlib.sha256(f"{salt}:emitted:{kind}:{value}".encode()).hexdigest()[:32]


def _salt(db) -> str:
    from ..instance import store
    return store.birth(db)["instance_id"]


def fingerprint(salt, name, key) -> str:
    return PREFIX + _digest(salt, "claim", f"{name}\x1f{key}")


def task_row(salt, task_id) -> str:
    return TASK_PREFIX + _digest(salt, "task", task_id)


def own_sentences(reasoning) -> list[str]:
    """Every sentence of a stored model-written answer (its free text and its answer sentences); [] for a template."""
    if not isinstance(reasoning, dict) or not str(reasoning.get("generated_by") or "").startswith("model:"):
        return []
    out = []
    if isinstance(reasoning.get("text"), str):
        out += T.sentences(reasoning["text"])
    for a in reasoning.get("answer") or ():
        if isinstance(a, dict) and isinstance(a.get("text"), str):
            out += T.sentences(a["text"])
    return [" ".join(s.split()) for s in out]


def _load(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _rows(db, row_names) -> dict:
    """{name: state} of the web_strategies rows named (primary-key lookups)."""
    out, row_names = {}, sorted(set(row_names))
    for start in range(0, len(row_names), 300):
        part = row_names[start:start + 300]
        for name, raw in db.execute(f"SELECT name, state FROM web_strategies WHERE name IN ({','.join('?' * len(part))})",
                                    part):
            out[name] = _load(raw)
    return out


def stored(db) -> int:
    """The number of fingerprint rows (an index range of the primary key)."""
    return db.execute("SELECT count(*) FROM web_strategies WHERE name >= ? AND name < ?",
                      (PREFIX, PREFIX[:-1] + chr(ord(PREFIX[-1]) + 1))).fetchone()[0]


# ---- emission ----------------------------------------------------------------------------------------------------------

def _support(db, task_id, name, key, at, deadline, clock) -> tuple[int, int]:
    """(SOURCED / UNSOURCED / UNKNOWN, the number of distinct domains found stating it) for one pair at `at`."""
    import sqlite3
    found = set()
    queries = (("SELECT claim_values, text, domain FROM web_claims WHERE task_id=? AND retrieved_at <= ?",
                (task_id, at)),
               ("SELECT claim_values, text, domain FROM web_claims WHERE retrieved_at <= ? AND instr(lower(text), ?) > 0 "
                "LIMIT ?", (at, name, SUPPORT_ROWS)))
    for n, (sql, params) in enumerate(queries):
        if clock() > deadline:
            return UNKNOWN, len(found)
        try:
            rows = db.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "interrupt" not in str(exc).lower():
                raise
            return UNKNOWN, len(found)
        for values, text, domain in rows:
            try:
                listed = json.loads(values or "[]")
            except ValueError:
                continue
            if name in names(text) and key in {value_key(v) for v in listed if isinstance(v, str)}:
                found.add(str(domain or "").lower())
                if len(found) >= MAX_GROUPS:
                    break
        if found:
            return SOURCED, len(found)
        if n == 1 and len(rows) >= SUPPORT_ROWS:
            return UNKNOWN, 0
    return UNSOURCED, 0


def prepare(db, task_id, report, now, *, cfg=None, clock=None, seconds=None) -> dict | None:
    """What one stored answer emits (see the module docstring): read only, BEFORE the write transaction that stores the
    answer. None when the setting is off, the answer is not model-written or states no specific value of a subject."""
    from . import state
    cfg = cfg or state.get_config(db)
    if not getattr(cfg, "own_text_provenance", False) or not isinstance(report, dict):
        return None
    found = []
    for sentence in own_sentences(report.get("reasoning")):
        found += pairs(names(sentence), T.values(sentence))
    found = list(dict.fromkeys(found))[:MAX_PER_ANSWER]
    if not found:
        return None
    clock = clock or time.monotonic
    deadline = clock() + (SUPPORT_SECONDS if seconds is None else seconds)
    salt = _salt(db)
    out = {"task": task_row(salt, task_id), "task_digest": _digest(salt, "task", task_id), "at": float(now),
           "full": stored(db) >= MAX_FINGERPRINTS, "rows": []}
    db.set_progress_handler(lambda: 1 if clock() > deadline else 0, 10_000)
    try:
        for name, key in found:
            sourced, n = _support(db, task_id, name, key, now, deadline, clock)
            out["rows"].append((fingerprint(salt, name, key), sourced, n))
    finally:
        db.set_progress_handler(None, 0)
    unknown = sum(1 for _, s, _ in out["rows"] if s == UNKNOWN)
    if unknown:
        _log.warning("own-text provenance: %d of %d emitted value(s) of task %s not checked for a source in time; "
                     "they count as sourced (never echoes)", unknown, len(out["rows"]), task_id)
    return out


def record(db, prepared) -> int:
    """Write what prepare() found. Caller owns the write transaction; primary-key reads and writes only. Returns the
    number of fingerprints whose emission list took this answer."""
    if not prepared:
        return 0
    if prepared["full"]:
        _log.warning("own-text provenance: the store holds its maximum of %d fingerprints; nothing more is recorded",
                     MAX_FINGERPRINTS)
        return 0
    at, task = prepared["at"], prepared["task_digest"]
    held = _rows(db, [d for d, _, _ in prepared["rows"]] + [prepared["task"]])
    kept = list((held.get(prepared["task"]) or {}).get("k") or [])
    taken = 0
    for d, sourced, n in prepared["rows"]:
        emissions = [e for e in (held.get(d) or {}).get("e") or [] if isinstance(e, list) and len(e) == 4]
        if any(e[3] == task for e in emissions):
            continue
        emissions = sorted(emissions + [[at, sourced, n, task]], key=lambda e: (e[0], e[3]))
        dropped = emissions[MAX_EMISSIONS:]
        emissions = emissions[:MAX_EMISSIONS]
        db.execute("INSERT INTO web_strategies(name,state,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET "
                   "state=excluded.state, updated_at=excluded.updated_at", (d, _dump({"e": emissions}), at))
        for e in dropped:                      # an emission no longer kept: its task's list forgets the fingerprint
            if e[3] != task:
                _unlist(db, TASK_PREFIX + e[3], d)
        if any(e[3] == task for e in emissions):
            kept.append(d)
            taken += 1
    if kept:
        db.execute("INSERT INTO web_strategies(name,state,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET "
                   "state=excluded.state, updated_at=excluded.updated_at",
                   (prepared["task"], _dump({"k": sorted(set(kept))}), at))
    return taken


def _unlist(db, task_name, d):
    row = _rows(db, [task_name]).get(task_name)
    if row is None:
        return
    keys = [k for k in row.get("k") or [] if k != d]
    if keys:
        db.execute("UPDATE web_strategies SET state=? WHERE name=?", (_dump({"k": keys}), task_name))
    else:
        db.execute("DELETE FROM web_strategies WHERE name=?", (task_name,))


# ---- forget ------------------------------------------------------------------------------------------------------------

def forget_task(db, task_id) -> int:
    """Remove a task's emissions (caller owns the transaction; primary-key lookups only). Returns fingerprints changed."""
    salt = _salt(db)
    name, task = task_row(salt, task_id), _digest(salt, "task", task_id)
    row = _rows(db, [name]).get(name)
    if row is None:
        return 0
    changed = 0
    held = _rows(db, row.get("k") or [])
    for d, state in held.items():
        emissions = [e for e in state.get("e") or [] if isinstance(e, list) and len(e) == 4 and e[3] != task]
        if emissions:
            db.execute("UPDATE web_strategies SET state=? WHERE name=?", (_dump({"e": emissions}), d))
        else:
            db.execute("DELETE FROM web_strategies WHERE name=?", (d,))
        changed += 1
    db.execute("DELETE FROM web_strategies WHERE name=?", (name,))
    return changed


def forget_knowledge(db, ids) -> int:
    """Remove the fingerprints of the subject names and values of the knowledge rows `ids` (read before they are
    deleted; caller owns the transaction; primary-key lookups only). Returns fingerprints removed."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return 0
    salt, wanted = _salt(db), set()
    for start in range(0, len(ids), 300):
        part = ids[start:start + 300]
        for statement, values in db.execute(f"SELECT statement, claim_values FROM web_knowledge WHERE id IN "
                                            f"({','.join('?' * len(part))})", part):
            try:
                listed = json.loads(values or "[]")
            except ValueError:
                listed = []
            listed = [v for v in listed if isinstance(v, str)] if isinstance(listed, list) else []
            wanted |= {fingerprint(salt, n, k) for n, k in pairs(names(statement), listed + T.values(statement or ""))}
    held = _rows(db, wanted)
    for d, state in held.items():
        for e in state.get("e") or []:
            if isinstance(e, list) and len(e) == 4:
                _unlist(db, TASK_PREFIX + e[3], d)
        db.execute("DELETE FROM web_strategies WHERE name=?", (d,))
    return len(held)


# ---- the rule ------------------------------------------------------------------------------------------------------------

class Echoes:
    """The verdicts of one consolidation's claims (see the module docstring), all made when it is built, before the
    write transaction; of() only looks them up. The hook other rules read: of(claim) is "emitted" for a claim that is
    an echo of the individual's own unsourced output (it counts for nothing), else None."""

    def __init__(self, db, claims):
        from . import claims as _claims, state
        self.seen, self.skipped = {}, []
        claims = [c for c in claims if c is not None]
        salt = _salt(db)
        wanted = {}
        for c in claims:
            if c.id in wanted or c.kind == "metadata" or _claims.is_primary(c):
                continue
            wanted[c.id] = [fingerprint(salt, n, k) for n, k in pairs(names(c.text), c.values)]
        looked, depth = [], 0                  # each claim its share of the cap, first pairs first (review3b N4)
        while len(looked) < MAX_LOOKUPS and any(depth < min(len(k), MAX_KEYS_PER_CLAIM) for k in wanted.values()):
            for keys in wanted.values():
                if depth < min(len(keys), MAX_KEYS_PER_CLAIM) and len(looked) < MAX_LOOKUPS:
                    looked.append(keys[depth])
            depth += 1
        held = _rows(db, looked)
        looked = set(looked)
        for c in claims:
            if c.id in self.seen or c.id not in wanted:
                continue
            keys = wanted[c.id]
            if not set(keys) <= looked:
                self.seen[c.id] = None
                self.skipped.append(c.id)
                continue
            read, published = state.finite(c.retrieved_at), state.finite(c.published_at)
            verdict = None
            for d in keys:
                emissions = [e for e in (held.get(d) or {}).get("e") or [] if isinstance(e, list) and len(e) == 4]
                if not emissions or read is None:
                    continue
                at, sourced = emissions[0][0], emissions[0][1]
                if sourced == UNSOURCED and at < read and (published is None or published >= at):
                    verdict = "emitted"
                    break
            self.seen[c.id] = verdict
        if self.skipped:
            _log.warning("own-text provenance: %d claim(s) not checked (lookup cap); they count as not echoes: %s",
                         len(self.skipped), ",".join(self.skipped[:20]))

    def of(self, claim) -> str | None:
        return self.seen.get(claim.id)


class Either:
    """Two echo checks read as one (Wave 3's echo_recognition and this rule, both on): the first verdict found."""

    def __init__(self, *checks):
        self.checks = [c for c in checks if c is not None]
        self.skipped = [cid for c in self.checks for cid in getattr(c, "skipped", [])]

    def of(self, claim):
        for check in self.checks:
            verdict = check.of(claim)
            if verdict:
                return verdict
        return None
