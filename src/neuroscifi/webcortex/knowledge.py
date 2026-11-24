"""Web knowledge: versioned, temporal statements in the individual's semantic memory (ADR-026).

A row is a *version* of what is known about one claim key. New values supersede, never overwrite: the older row
keeps its validity interval and points at its successor (`superseded_by`). Every promotion to 'accepted' goes
through the owner: `review()` here, or `nsf resolve ID TEXT --confirm|--reject` on the question with scope
`web:knowledge:<id>` (executive.resolve calls `apply_review` inside its own transaction).

Retrieval is lexical (BM25 over statement and key, ADR-009/022). Staleness is computed on read from the freshness
class (temporal.is_stale); status 'stale' is set only when a forget removed part of an item's support. With the
database at hand staleness also reads later observations: a release-class item is stale once a claim about the
same key (or a near-identical one) carrying a higher version was recorded after the item's last verification
(newer_release_seen); an item resting on an arXiv record is stale once a later read of that record showed a
higher arXiv version (revision_seen, from web_sources.metadata.version).
"""
from __future__ import annotations

from array import array
from collections import OrderedDict
import math
import re
import threading

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError
from . import state

COLUMNS = ("id", "key", "version", "statement", "status", "confidence", "uncertainty", "freshness_class",
           "first_verified_at", "last_verified_at", "valid_from", "valid_until", "superseded_by", "supporting",
           "contradicting", "sources", "claim_values", "polarity", "note", "pending_id", "task_id", "created_at",
           "updated_at", "provenance", "verification", "published_at", "event_at", "retrieved_at")
_JSON = ("supporting", "contradicting", "sources", "claim_values")
PROVENANCE = ("verified_conclusion", "user_asserted")     # migration 8 (ADR-038)
MAX_QUESTION_STATEMENT = 300
REVIEWABLE = ("provisional", "accepted", "stale")


def row(db, knowledge_id) -> dict:
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int):
        raise ValidationError("knowledge id must be an integer")
    found = db.execute(f"SELECT {','.join(COLUMNS)} FROM web_knowledge WHERE id=?", (knowledge_id,)).fetchone()
    if found is None:
        raise NotFound(f"No web knowledge item {knowledge_id}")
    return _decode(found)


def _decode(values) -> dict:
    out = dict(zip(COLUMNS, values))
    for key in _JSON:
        out[key] = state.loads(out[key], [])
    out["verification"] = state.loads(out["verification"], {}) or {}
    return out


def rows(db, where="1", params=()) -> list[dict]:
    return [_decode(r) for r in db.execute(f"SELECT {','.join(COLUMNS)} FROM web_knowledge WHERE {where} "
                                           "ORDER BY id", params)]


def versions(db, key) -> list[dict]:
    return [{"id": r["id"], "version": r["version"], "statement": r["statement"], "status": r["status"],
             "valid_from": r["valid_from"], "valid_until": r["valid_until"], "superseded_by": r["superseded_by"],
             "confidence": r["confidence"]} for r in rows(db, "key=?", (key,))]


def staleness(item: dict, now, cfg=None, db=None) -> tuple[bool, str]:
    """(stale, reason) for a knowledge row. With `db`, later observations (a newer release, a new arXiv version of
    a source) make it stale before its TTL (module docstring)."""
    if item["status"] == "stale":
        return True, item.get("note") or "marked stale: part of its support was removed"
    from . import temporal
    newer = revised = False
    if db is not None:
        newer = item["freshness_class"] == "release" and newer_release_seen(db, item)
        revised = revision_seen(db, item)
    stale, reason = temporal.is_stale(item["last_verified_at"], item["freshness_class"], now,
                                      ttl_overrides=(cfg.freshness_ttl if cfg is not None else None) or None,
                                      newer_release_seen=newer, revision_seen=revised)
    return bool(stale), reason


def _version(text):
    """Comparable tuple of a version string ('v2.3.5', '2.10', 'v3'), or None."""
    import re
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _versions(values):
    from . import text
    out = []
    for v in values or []:
        if isinstance(v, str) and text.value_kind(v)[0] == "version":
            parsed = _version(v)
            if parsed is not None:
                out.append(parsed)
    return out


def newer_release_seen(db, item) -> bool:
    """A claim about the same key (or a near-identical key) with a higher version than every version the item
    states, recorded after the item was last verified."""
    mine = _versions(item["claim_values"])
    if not mine:
        return False
    from .consolidate import SIMILAR_KEY, _similar
    top = max(mine)
    # A range scan of web_claims_created (migration 7): only claims recorded after the last verification are read.
    for key, values in db.execute("SELECT key, claim_values FROM web_claims INDEXED BY web_claims_created "
                                  "WHERE created_at>? AND claim_values LIKE '%v%'", (item["last_verified_at"] or 0.0,)):
        if key != item["key"] and _similar(key, item["key"]) < SIMILAR_KEY:
            continue
        if any(v > top for v in _versions(state.loads(values, []))):
            return True
    return False


def revision_seen(db, item) -> bool:
    """A source of the item is an arXiv record whose version, read after the item was last verified, is higher
    than any version of it read up to then."""
    urls = [s.get("url") for s in item["sources"] if isinstance(s, dict) and s.get("url")]
    if not urls:
        return False
    verified = item["last_verified_at"] or 0.0
    before, after = {}, {}
    rows = db.execute(f"SELECT canonical_url, retrieved_at, json_extract(metadata, '$.version') FROM web_sources "
                      f"WHERE canonical_url IN ({','.join('?' * len(urls))}) AND "
                      f"json_extract(metadata, '$.arxiv_id') IS NOT NULL", urls).fetchall()
    for url, retrieved, version in rows:
        parsed = _version(version)
        if parsed is None:
            continue
        side = before if (retrieved or 0.0) <= verified else after
        side[url] = max(side.get(url, parsed), parsed)
    return any(url in before and v > before[url] for url, v in after.items())


def describe(db, item: dict, now, cfg=None) -> dict:
    """The public view of a knowledge row: status, confidence, freshness, staleness, provenance, versions."""
    stale, reason = staleness(item, now, cfg, db)
    return {
        "id": item["id"], "key": item["key"], "version": item["version"], "statement": item["statement"],
        "status": item["status"], "confidence": state.finite(item["confidence"], 0.0),
        "uncertainty": state.finite(item["uncertainty"], 0.0), "freshness_class": item["freshness_class"],
        "first_verified_at": item["first_verified_at"], "last_verified_at": item["last_verified_at"],
        "valid_from": item["valid_from"], "valid_until": item["valid_until"], "stale": stale, "stale_reason": reason,
        "provisional": item["status"] == "provisional", "contested": bool(item["contradicting"]),
        "contradicting": item["contradicting"], "supporting": item["supporting"],
        "sources": [{k: s.get(k) for k in ("url", "title", "domain", "published_at", "retrieved_at")}
                    for s in item["sources"]],
        "values": item["claim_values"], "polarity": item["polarity"], "note": item["note"],
        "pending_id": item["pending_id"], "superseded_by": item["superseded_by"],
        "versions": versions(db, item["key"]),
        # ADR-038: provenance kind (verified_conclusion | user_asserted), the ADR-035 verification record and the
        # separate times of this version (effective = valid_from, verification = last_verified_at).
        "provenance": item["provenance"], "verification": item["verification"],
        "times": {"published_at": item["published_at"], "event_at": item["event_at"],
                  "retrieved_at": item["retrieved_at"], "verified_at": item["last_verified_at"],
                  "effective_at": item["valid_from"]},
    }


# ---- the lexical index of the live statements: this process only --------------------------------------------------
#
# query() used to tokenise every live statement and build BM25 on every call (measured: 212 ms at 20,000
# statements, 1.2 s at 100,000). The index is now kept per individual file for the life of the process and is valid
# only while the live statements are exactly the ones it was built from: each query reads (id, statement, key) of
# the live rows and compares them with what the index holds, value by value. That is the whole invalidation rule,
# so a write, a forget, a status change or another process's commit can never be answered from an older index.
# What a query saves is the tokenising and the index build, not the read (26 ms of a 30 ms repeated query at
# 20,000 statements). Rows are decoded only for the items a query returns, and they are read at that moment, so
# nothing a query returns is older than its own read.
# Bounds: at most INDEX_FILES individuals and INDEX_MAX_STATEMENTS statements in all are kept by one process (the
# least recently asked individual goes first; one individual above the bound is indexed per call, as before). An
# index holds the live statements and their tokens: 1.4 to 1.7 KB a statement where every statement brings a word of
# its own (scripts/bench_knowledge.py index), so at most about 200 MB. forget() drops this process's indexes at
# once, like the vectors of semantic.py; another long-running process keeps a forgotten statement's text in its
# index until its own next query or its exit.

INDEX_FILES = 4
INDEX_MAX_STATEMENTS = 120_000


class _Index:
    """BM25 over the live statements with a posting list per term. scores() adds, for each statement, the same
    terms in the same order as text.BM25.scores, so the two give the same floats
    (tests/webcortex/test_knowledge_index.py)."""

    def __init__(self, live, tokens_of):
        from . import text
        self.live = live                                   # [(id, statement, key)] in id order, as read
        self.k1, self.b = text.BM25_K1, text.BM25_B
        self.toks = []                                     # the tokens of live[n], reused by the next build
        self._rows = None
        words: dict[str, str] = {}                         # one string object per distinct token
        where: dict[str, array] = {}                       # term -> positions of the statements that hold it
        often: dict[str, array] = {}                       # term -> how often, in the same order
        lengths = []
        for n, row in enumerate(live):
            toks = tuple(words.setdefault(t, t) for t in tokens_of(row))
            self.toks.append(toks)
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            lengths.append(len(toks))
            for t, f in counts.items():
                if t not in where:
                    where[t], often[t] = array("L"), array("L")
                where[t].append(n)
                often[t].append(f)
        self.n = len(live)
        avgdl = (sum(lengths) / self.n) if self.n else 0.0
        self.norm = [self.k1 * (1 - self.b + self.b * length / avgdl) if avgdl else self.k1 for length in lengths]
        self.where, self.often = where, often
        self.idf = {t: math.log(1.0 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in where.items()}

    def scores(self, query_tokens) -> list[float]:
        out = [0.0] * self.n
        for t in dict.fromkeys(query_tokens):              # each query term counts once
            positions = self.where.get(t)
            if positions:
                idf, gain, norm = self.idf[t], self.k1 + 1, self.norm
                for n, f in zip(positions, self.often[t]):
                    out[n] += idf * f * gain / (f + norm[n])
        return out

    @property
    def rows(self) -> list[dict]:
        """The live statements as the rows semantic.rank reads ({"id", "statement", "key"}); made on first use, so
        an individual without semantic retrieval never holds them."""
        if self._rows is None:
            self._rows = [{"id": i, "statement": s, "key": k} for i, s, k in self.live]
        return self._rows


_INDEXES: OrderedDict[str, _Index] = OrderedDict()
_INDEX_LOCK = threading.Lock()


def _doc_tokens(statement, key):
    from . import text
    return text.content_tokens(statement + " " + key.replace("<", " ").replace(">", " "))


def _index(db, *, cached=True) -> _Index:
    """The index of the individual's live statements as they are now (see the comment above). `cached=False`
    builds it from nothing and keeps nothing: the reference the kept index is tested against."""
    live = db.execute(f"SELECT id, statement, key FROM web_knowledge WHERE status IN "
                      f"({','.join('?' * len(state.LIVE_KNOWLEDGE))}) ORDER BY id", state.LIVE_KNOWLEDGE).fetchall()
    path = store.database_path(db) if cached else None
    if not path or len(live) > INDEX_MAX_STATEMENTS:
        return _Index(live, lambda row: _doc_tokens(row[1], row[2]))
    with _INDEX_LOCK:
        held = _INDEXES.get(path)
        if held is not None:
            _INDEXES.move_to_end(path)
    if held is not None and held.live == live:
        return held
    known = dict(zip(held.live, held.toks)) if held is not None else {}     # unchanged rows keep their tokens

    def tokens_of(row):
        toks = known.get(row)
        return _doc_tokens(row[1], row[2]) if toks is None else toks
    built = _Index(live, tokens_of)
    with _INDEX_LOCK:
        _INDEXES[path] = built
        _INDEXES.move_to_end(path)
        while len(_INDEXES) > INDEX_FILES or (len(_INDEXES) > 1 and
                                              sum(i.n for i in _INDEXES.values()) > INDEX_MAX_STATEMENTS):
            _INDEXES.popitem(last=False)
    return built


def clear_index() -> None:
    """Drop every lexical index this process holds (forget(); tests)."""
    with _INDEX_LOCK:
        _INDEXES.clear()


def query(db, topic, now, *, limit=10, cached=True) -> dict:
    """What is known about `topic`: live items (provisional, accepted, stale) ranked by BM25 over statement and key.
    With semantic retrieval on and reachable (semantic.py) they are ranked by meaning instead, and a statement the
    reranker scores under the floor is not returned; `retrieval` says which ranking produced the items and, when
    the semantic one was asked for and could not be used, why. The BM25 index is kept between calls (_index);
    `cached=False` rebuilds it for this call, with the same result."""
    from . import semantic, text
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 2000:
        raise ValidationError("topic must be a non-blank string of at most 2000 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a positive integer")
    cfg = state.get_config(db)
    terms = text.content_tokens(topic)
    index = _index(db, cached=cached)
    items, retrieval = [], {"mode": "lexical"}
    if index.n:
        scores = index.scores(terms) if terms else [0.0] * index.n
        ranked = None
        if semantic.get_config(db).enabled:     # rank() answers (None, lexical) at once when it is off: not asked
            found, retrieval = semantic.rank(db, topic, index.rows, scores, limit=min(100, limit))
            if found is not None:
                ranked = [(score, item["id"]) for score, item in found]
        if ranked is None:          # off, not configured or unreachable: the lexical ranking, as before
            live = index.live
            ranked = sorted(((s, live[n][0]) for n, s in enumerate(scores) if s > 0),
                            key=lambda x: (-x[0], -x[1]))[:min(100, limit)]
        # Only what is returned is decoded, and it is read now: an item that stopped being live since the index
        # was read (forgotten, superseded, archived by another connection) is left out.
        ids = [kid for _, kid in ranked]
        full = {}
        for start in range(0, len(ids), 500):
            part = ids[start:start + 500]
            full.update((r["id"], r) for r in rows(
                db, f"id IN ({','.join('?' * len(part))}) AND status IN "
                    f"({','.join('?' * len(state.LIVE_KNOWLEDGE))})", (*part, *state.LIVE_KNOWLEDGE)))
        for score, kid in ranked:
            if kid in full:
                items.append(describe(db, full[kid], now, cfg) | {"score": round(score, 6)})
    return {"topic": topic, "as_of": now, "items": items, "matched": len(items), "retrieval": retrieval,
            "known": sum(1 for i in items if i["status"] == "accepted" and not i["stale"] and not i["contested"])}


FEEDBACK_FIELDS = ("id", "knowledge_id", "key", "effect", "before_confidence", "after_confidence", "info_gain",
                   "task_id", "created_at")


def feedback(db, subject, *, limit=5) -> list[dict]:
    """The newest `limit` (1..100) feedback rows about a subject, newest first: `subject` is a knowledge id (the
    history of its key, across versions) or a claim key. Each row: id, knowledge_id, key, effect (strengthened,
    weakened, unchanged, reversed, new), before/after confidence, info_gain (bits), task_id, created_at."""
    if isinstance(subject, bool) or not isinstance(subject, (int, str)) or (isinstance(subject, str) and
                                                                            not subject.strip()):
        raise ValidationError("subject must be a knowledge id or a non-empty key")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValidationError("limit must be an integer between 1 and 100")
    key = row(db, subject)["key"] if isinstance(subject, int) else subject
    return [{k: (state.finite(v) if k in ("before_confidence", "after_confidence", "info_gain") else v)
             for k, v in zip(FEEDBACK_FIELDS, found)}
            for found in db.execute(f"SELECT {','.join(FEEDBACK_FIELDS)} FROM web_feedback WHERE key=? "
                                    "ORDER BY id DESC LIMIT ?", (key, limit))]


# ---- questions -----------------------------------------------------------------------------------------------

MAX_QUESTION = 2000


def open_question(db, scope, question, now) -> int:
    """Open the web question of `scope`, or add `question` as an update to the one already open (one open question
    per subject). Caller owns the transaction."""
    found = db.execute("SELECT id, question FROM pending WHERE scope=? AND resolved_at IS NULL ORDER BY id LIMIT 1",
                       (scope,)).fetchone()
    if found:
        if question not in found[1] and len(found[1]) + len(question) + 9 <= MAX_QUESTION:
            db.execute("UPDATE pending SET question=? WHERE id=?", (f"{found[1]}\nUpdate: {question}", found[0]))
        return found[0]
    return db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(NULL,'ask',?,?,?)",
                      (scope, question, now)).lastrowid


def open_web_questions(db) -> int:
    """Open Web Cortex questions (scope web:...), across tasks."""
    return db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:%'").fetchone()[0]


def _stale_before(now, cfg) -> float | None:
    """The time before which an open web question is stale, or None when nothing is (no `now` given, or
    config.web_question_stale_days is 0: every open question counts, the rule before this setting)."""
    days = state.finite(getattr(cfg, "web_question_stale_days", 0), 0.0)
    if now is None or state.finite(now) is None or not days or days <= 0:
        return None
    return now - days * 86400.0


def web_question_load(db, now=None, cfg=None) -> dict:
    """The owner's web-question load: {open, stale, counted, cap, stale_after_days, room}. A question left open
    for more than config.web_question_stale_days is *stale*: it stays open, listed and answerable, and no longer
    counts toward config.max_open_web_questions, so ten questions the owner never answers do not stop new ones for
    ever. counted = open - stale; room = counted < cap. A question updated later (open_question appends to it)
    keeps the age of its first asking."""
    cfg = cfg or state.get_config(db)
    total = open_web_questions(db)
    before = _stale_before(now, cfg)
    stale = 0 if before is None else db.execute(
        "SELECT count(*) FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:%' AND created_at<?",
        (before,)).fetchone()[0]
    counted = total - stale
    return {"open": total, "stale": stale, "counted": counted, "cap": cfg.max_open_web_questions,
            "stale_after_days": state.finite(getattr(cfg, "web_question_stale_days", 0), 0.0) or 0,
            "room": counted < cfg.max_open_web_questions}


def stale_web_questions(db, now, cfg=None) -> list[dict]:
    """The open web questions that are stale at `now` (web_question_load), oldest first: {id, scope, question,
    created_at, age_days}. Each is still answered with `nsf resolve ID ...`."""
    cfg = cfg or state.get_config(db)
    before = _stale_before(now, cfg)
    if before is None:
        return []
    return [{"id": pid, "scope": scope, "question": question, "created_at": created,
             "age_days": round((now - created) / 86400.0, 1)}
            for pid, scope, question, created in db.execute(
                "SELECT id, scope, question, created_at FROM pending WHERE resolved_at IS NULL AND scope LIKE "
                "'web:%' AND created_at<? ORDER BY created_at, id", (before,))]


def question_room(db, cfg=None, now=None) -> bool:
    """Whether a NEW web question fits the owner's interruption budget (config.max_open_web_questions). With `now`
    the stale questions (web_question_load) are left out of the count; without it every open question counts."""
    return web_question_load(db, now, cfg)["room"]


def ask(db, scope, question, now, cfg=None):
    """open_question within the web question budget: an open question of the same scope is updated (no new
    interruption); a new one is opened only while fewer than config.max_open_web_questions are open and not stale
    (web_question_load). Returns the pending id, or None when the budget is spent (the subject stays reviewable in
    the console / `nsf web knowledge`). Caller owns the transaction."""
    found = db.execute("SELECT 1 FROM pending WHERE scope=? AND resolved_at IS NULL LIMIT 1", (scope,)).fetchone()
    if found is None and not question_room(db, cfg, now):
        return None
    return open_question(db, scope, question, now)


def review_question(db, task_id, deferred, now, cfg=None) -> int | None:
    """The ONE aggregate question of a task whose candidates exceeded the question budget: "N candidates from task
    X await review". When the global budget is spent the newest open aggregate question is updated instead of
    opening another. Caller owns the transaction."""
    if deferred <= 0:
        return None
    text = (f"{deferred} candidate(s) from research task {task_id} await review in the console (Research) or with "
            "`nsf web knowledge --status provisional` / `nsf web review ID --accept|--reject`; no separate question "
            "was opened for each (web question budget).")
    scope = f"web:review:{task_id}"
    if db.execute("SELECT 1 FROM pending WHERE scope=? AND resolved_at IS NULL", (scope,)).fetchone() is None \
            and not question_room(db, cfg, now):
        latest = db.execute("SELECT scope FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:review:%' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        if latest is not None:
            scope = latest[0]
    return open_question(db, scope, text, now)


def quote(statement) -> str:
    statement = " ".join(str(statement).split())
    return statement if len(statement) <= MAX_QUESTION_STATEMENT else statement[:MAX_QUESTION_STATEMENT - 1] + "…"


def close_questions(db, scope, resolution, now) -> list[int]:
    """Close the open questions of `scope` with a system resolution. Caller owns the transaction."""
    return _close_questions(db, scope, resolution, now)


def _close_questions(db, scope, resolution, now) -> list[int]:
    ids = [r[0] for r in db.execute("SELECT id FROM pending WHERE scope=? AND resolved_at IS NULL", (scope,))]
    for pid in ids:
        db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, resolution, pid))
    return ids


# ---- review --------------------------------------------------------------------------------------------------

DECIDABLE = ("provisional", "accepted", "stale", "rejected")   # the owner may accept or reject these
MAX_RESOLUTION_HISTORY = 4000


def decide(db, item, accept: bool, now, *, note="", via="review", question_id=None) -> dict:
    """The owner's accept/reject of a knowledge item: the ONE transition shared by review() and `nsf resolve`
    (apply_review). Caller owns the transaction.

    provisional/stale/accepted/rejected -> accepted or rejected (archived and superseded rows: Conflict). An owner
    decision may be revised: rejecting an accepted item, or re-accepting a rejected one, records the revision in
    the item's note ("re-accepted after an earlier rejection" / "rejected after an earlier acceptance") and appends
    it to the resolution of every already-resolved question of scope web:knowledge:<id>, so the question's record
    never contradicts the item. Open questions of that scope are closed (except `question_id`, the question the
    caller is resolving itself). Returns {id, previous_status, status, pending_closed, decisions_revised}."""
    verdict = "accepted" if accept else "rejected"
    before = item["status"]
    if before not in DECIDABLE:
        raise Conflict(f"Knowledge item {item['id']} is {before}; only provisional, accepted, stale or rejected "
                       "items can be accepted or rejected")
    revised = before in ("accepted", "rejected") and before != verdict
    text = f"owner {verdict}"
    if revised:
        text = "owner re-accepted it after an earlier rejection" if accept else \
            "owner rejected it after an earlier acceptance"
    text += f" ({via})" if via else ""
    if note:
        text += f": {note}"
    if before != verdict:
        db.execute("UPDATE web_knowledge SET status=?, note=?, updated_at=? WHERE id=?",
                   (verdict, text, now, item["id"]))
    scope = f"web:knowledge:{item['id']}"
    closed = [pid for pid in (r[0] for r in db.execute("SELECT id FROM pending WHERE scope=? AND resolved_at IS NULL",
                                                        (scope,)).fetchall()) if pid != question_id]
    for pid in closed:
        db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, note or verdict, pid))
    history = []
    if revised:
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        for pid, resolution in db.execute("SELECT id, resolution FROM pending WHERE scope=? AND resolved_at IS NOT "
                                          "NULL", (scope,)).fetchall():
            if pid == question_id or pid in closed:
                continue
            entry = f"[{day}] {text}"
            updated = f"{resolution or ''}\n{entry}".strip()[-MAX_RESOLUTION_HISTORY:]
            db.execute("UPDATE pending SET resolution=? WHERE id=?", (updated, pid))
            history.append(pid)
    return {"id": item["id"], "previous_status": before, "status": verdict, "pending_closed": closed,
            "decisions_revised": history}


# ---- the owner's decision on a disagreement (RT4-05) -------------------------------------------------------------
#
# A contested subject never becomes knowledge, so the owner's answer to "web:contested:..." ("412 km holds") used to
# be recorded only on that question and the next task with the same disagreement asked again. The answer that names
# one of the disagreeing values is now a decision: a web_feedback row keyed by the conflict's key with detail
# {owner_choice, values, pending_id}. consolidate asks no new question for a disagreement the owner decided (same
# key, the chosen value among the disagreeing ones) and notes the decision instead; open contested questions about
# the same disagreement from other tasks are closed as answered by it.

def _value(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def contested_decision(db, key, values) -> dict | None:
    """The owner's latest decision on the disagreement about `key` whose sides include the chosen value, as
    {chosen, decided_at, pending_id}; None when the owner never decided it."""
    sides = {_value(v) for v in values or ()}
    for detail, at in db.execute("SELECT detail, created_at FROM web_feedback WHERE key=? AND "
                                 "json_extract(detail,'$.owner_choice') IS NOT NULL ORDER BY id DESC", (key,)):
        found = state.loads(detail, {})
        if _value(found.get("owner_choice")) in sides:
            return {"chosen": found["owner_choice"], "decided_at": at, "pending_id": found.get("pending_id")}
    return None


def _asked(db, pending_id):
    """(key, values, task_id) the contested question `pending_id` was asked about, from its consolidation row."""
    row = db.execute("SELECT key, detail, task_id FROM web_feedback WHERE json_extract(detail,'$.contested')=1 AND "
                     "json_extract(detail,'$.pending_id')=? ORDER BY id DESC LIMIT 1", (pending_id,)).fetchone()
    if row is None:
        return None
    return row[0], [str(v) for v in state.loads(row[1], {}).get("values") or []], row[2]


def answer_contested(db, current: dict, resolution: str, now) -> dict | None:
    """The owner answered a web:contested question without confirm. When the answer names one of the disagreeing
    values ("412 km" or "412 km holds"), record the decision and close the other open contested questions about the
    same key whose sides include it. None for any other question or answer. Caller owns the transaction."""
    try:
        kind, _ = _parse_scope(current["scope"])
    except ValidationError:
        return None
    asked = _asked(db, current["id"]) if kind == "contested" else None
    if asked is None:
        return None
    key, values, task_id = asked
    said = _value(re.sub(r"\s+holds\W*$", "", str(resolution or "").strip(), flags=re.IGNORECASE))
    chosen = next((v for v in values if _value(v) == said), None)
    if chosen is None:
        return None
    db.execute("INSERT INTO web_feedback(task_id,knowledge_id,key,effect,before_confidence,after_confidence,info_gain,"
               "detail,created_at) VALUES(?,NULL,?,'unchanged',NULL,NULL,0,?,?)",
               (task_id, key, state.dumps({"owner_choice": chosen, "values": values, "pending_id": current["id"]}),
                now))
    closed = []
    for (pid,) in db.execute("SELECT id FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:contested:%' AND "
                             "id<>? ORDER BY id", (current["id"],)).fetchall():
        other = _asked(db, pid)
        if other is not None and other[0] == key and _value(chosen) in {_value(v) for v in other[1]}:
            db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?",
                       (now, f"{chosen} (the owner's answer to question #{current['id']})", pid))
            closed.append(pid)
    return {"kind": "contested", "key": key, "chosen": chosen, "pending_closed": closed}


def _parse_scope(scope):
    parts = scope.split(":", 2)
    if len(parts) != 3 or parts[0] != "web":
        raise ValidationError(f"not a web question scope: {scope}")
    return parts[1], parts[2]


def apply_review(db, current: dict, confirm: bool, now) -> dict:
    """The owner's confirm/reject of a web question. Caller (executive.resolve) owns the transaction.

    web:knowledge:<id>  confirm=True -> accepted, False -> rejected
    web:monitor:<id>    confirm=True -> the proposed monitor becomes active (due now), False -> stopped
    web:research:<id>   confirm=True -> the proposed task is queued for the worker, False -> cancelled
    Other web questions (web:contested:..., web:review:<task> aggregates, web:finding:<monitor>:<task> monitor
    findings) are informational: answer them without confirm.
    """
    kind, ident = _parse_scope(current["scope"])
    if kind == "knowledge":
        done = decide(db, row(db, _int(ident)), bool(confirm), now, via=f"question #{current['id']}",
                      question_id=current["id"])
        out = {"kind": "knowledge", "id": done["id"], "previous_status": done["previous_status"],
               "status": done["status"]}
        return out | ({"decisions_revised": done["decisions_revised"]} if done["decisions_revised"] else {})
    if kind == "monitor":
        mid = _int(ident)
        found = db.execute("SELECT status FROM web_monitors WHERE id=?", (mid,)).fetchone()
        if found is None:
            raise NotFound(f"No monitor {mid}")
        if found[0] != "proposed":
            raise Conflict(f"Monitor {mid} is {found[0]}, not a proposal")
        status = "active" if confirm else "stopped"
        db.execute("UPDATE web_monitors SET status=?, next_run_at=?, updated_at=? WHERE id=?",
                   (status, now if confirm else None, now, mid))
        return {"kind": "monitor", "id": mid, "status": status}
    if kind == "research":
        found = db.execute("SELECT status, stage FROM web_tasks WHERE id=?", (ident,)).fetchone()
        if found is None:
            raise NotFound(f"No research task {ident}")
        if found != ("queued", "proposed"):
            raise Conflict(f"Research task {ident} is not an open proposal")
        if confirm:
            clamped = _within_allowance(db, ident, now)
            state.update_task(db, ident, now, stage=None)
            state.log_event(db, ident, "decision", {"decision": "proposal authorised by the owner"} |
                            ({"clamped_to_daily_allowance": clamped} if clamped else {}), now)
            return {"kind": "research", "id": ident, "status": "queued"}
        state.update_task(db, ident, now, status="cancelled", stage=None, finished_at=now,
                          error="proposal declined by the owner")
        state.log_event(db, ident, "cancelled", {"reason": "proposal declined by the owner"}, now)
        return {"kind": "research", "id": ident, "status": "cancelled"}
    raise ValidationError(f"Question {current['id']} ({current['scope']}) is informational; answer it without "
                          "confirm")


def _within_allowance(db, task_id, now) -> dict:
    """W-12: confirming an autonomous proposal commits it to the daily autonomous allowance. Its request/byte
    budget is clamped to what is left now (other autonomous tasks may have run since it was proposed); with
    nothing left -> Conflict and the proposal stays open. Returns the clamped fields. Caller owns the txn."""
    from dataclasses import replace
    from . import orchestrator, scheduler
    from .types import Budget
    task = state.task(db, task_id)
    if not orchestrator.is_autonomous(task["origin"]):
        return {}
    cfg = state.get_config(db)
    left = scheduler.autonomous_remaining(db, now, cfg, exclude=task_id)
    for key in ("requests", "bytes"):
        if left[key] < 1:
            raise Conflict(f"the daily autonomous allowance is spent (no {key} left of "
                           f"{cfg.daily_autonomous.get(key, 0)}); the proposal stays open, confirm it later")
    budget = Budget.from_json(task["budget"])
    clamped = {f"max_{k}": left[k] for k in ("requests", "bytes") if getattr(budget, f"max_{k}") > left[k]}
    if clamped:
        state.update_task(db, task_id, now, budget=replace(budget, **clamped).to_json())
    return clamped


def review_outcome(db, current: dict):
    """True/False when the question's subject shows the owner's confirm/reject, else None (for idempotent retries)."""
    try:
        kind, ident = _parse_scope(current["scope"])
    except ValidationError:
        return None
    if kind == "knowledge":
        found = db.execute("SELECT status FROM web_knowledge WHERE id=?", (_maybe_int(ident),)).fetchone()
        return {"accepted": True, "rejected": False}.get(found[0]) if found else None
    if kind == "monitor":
        found = db.execute("SELECT status FROM web_monitors WHERE id=?", (_maybe_int(ident),)).fetchone()
        return {"active": True, "paused": True, "stopped": False}.get(found[0]) if found else None
    if kind == "research":
        found = db.execute("SELECT status, stage FROM web_tasks WHERE id=?", (ident,)).fetchone()
        if found is None or found[1] == "proposed":
            return None
        return found[0] != "cancelled"
    return None


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"invalid id in web question scope: {text}") from None


def _maybe_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def review(db, knowledge_id, accept: bool, now, *, note="") -> dict:
    """Accept or reject an item (decide(): provisional/stale/rejected -> accepted, provisional/stale/accepted ->
    rejected); closes its open question in the same transaction and counts as a teacher intervention, exactly like
    answering it with `nsf resolve`. Revising an earlier decision is recorded on the item and on the question's
    resolution (decisions_revised lists those questions)."""
    if not isinstance(accept, bool):
        raise ValidationError("accept must be true or false")
    if not isinstance(note, str) or len(note) > 1024:
        raise ValidationError("note must be a string of at most 1024 characters")
    with store.write(db):
        item = row(db, knowledge_id)
        done = decide(db, item, accept, now, note=note, via="review")
        store.bump(db, "teacher_interventions")
        version = store.bump_state_version(db)
    return done | {"state_version": version}


def _owner_status(db, knowledge_id, status, now, note) -> dict:
    with store.write(db):
        item = row(db, knowledge_id)
        before = item["status"]
        if before not in REVIEWABLE and before != status:
            raise Conflict(f"Knowledge item {item['id']} is {before}; it cannot become {status}")
        db.execute("UPDATE web_knowledge SET status=?, note=?, updated_at=? WHERE id=?",
                   (status, f"owner {status}" + (f": {note}" if note else ""), now, item["id"]))
        closed = _close_questions(db, f"web:knowledge:{item['id']}", f"{status} by the owner", now)
        version = store.bump_state_version(db)
    return {"id": item["id"], "previous_status": before, "status": status, "pending_closed": closed,
            "state_version": version}


def archive(db, knowledge_id, now, *, note="") -> dict:
    """Keep the item for history but stop treating it as current knowledge."""
    return _owner_status(db, knowledge_id, "archived", now, note)


def reject(db, knowledge_id, now, *, note="") -> dict:
    return _owner_status(db, knowledge_id, "rejected", now, note)


def assert_fact(db, statement, now, *, note="") -> dict:
    """Record the owner's own assertion as knowledge with provenance 'user_asserted' (ADR-038): status
    'accepted' (the owner said it), verification state 'not_evaluated' (no evidence was examined), confidence 0.5
    (no web evidence either way). It is never mixed with verified conclusions: a live row of the same (or a
    near-identical) key stating the same values is left as it is ("already known"); one stating other values is
    superseded by a NEW version (old row kept with its interval, a knowledge -> knowledge 'supersedes' edge and a
    'reversed' feedback row). Web evidence never supersedes a user-asserted row (consolidate records a
    contradiction and asks the owner instead)."""
    from . import claims, evolution, temporal, text
    from .consolidate import SIMILAR_KEY, _compare, _similar, next_version
    if not isinstance(statement, str) or not statement.strip() or len(statement) > 2000:
        raise ValidationError("statement must be a non-blank string of at most 2000 characters")
    if not isinstance(note, str) or len(note) > 1024:
        raise ValidationError("note must be a string of at most 1024 characters")
    statement = " ".join(statement.split())
    key = claims.claim_key(statement) or statement.casefold()[:200]
    values, polarity = text.values(statement), (-1 if claims.polarity(statement) < 0 else 1)
    category, why = temporal.claim_category(statement, now=now)
    state.require_writable(db)
    with store.write(db):
        live = [r for r in rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
                if r["key"] == key or max(_similar(r["key"], key),
                                          _similar(claims.claim_key(r["statement"]), key)) >= SIMILAR_KEY]
        current = max(live, key=lambda r: (r["key"] == key, r["version"])) if live else None
        if current is not None and _compare(values, polarity, current)[0] == "same":
            return {"id": current["id"], "status": "unchanged", "provenance": current["provenance"],
                    "reason": f"already known as knowledge #{current['id']} with the same values"}
        key = current["key"] if current is not None else key
        verification = {"policy": "ADR-035", "state": "not_evaluated", "basis": "owner_assertion",
                        "category": category, "category_reason": why, "verified_at": None}
        kid = db.execute(
            "INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
            "first_verified_at,last_verified_at,valid_from,supporting,contradicting,sources,claim_values,polarity,"
            "note,created_at,updated_at,provenance,verification) VALUES(?,?,?,'accepted',0.5,0.5,?,?,?,?,'[]','[]',"
            "'[]',?,?,?,?,?,'user_asserted',?)",
            (key, next_version(db, key), statement, temporal.CATEGORY_FRESHNESS[category], now, now, now,
             state.dumps(values), polarity, "asserted by the owner" + (f": {note}" if note else ""), now, now,
             state.dumps(verification))).lastrowid
        superseded = None
        if current is not None:
            superseded = current["id"]
            db.execute("UPDATE web_knowledge SET status='superseded', valid_until=?, superseded_by=?, note=?, "
                       "updated_at=? WHERE id=?", (now, kid, f"superseded by the owner's assertion #{kid}", now,
                                                   current["id"]))
            _close_questions(db, f"web:knowledge:{current['id']}", f"superseded by the owner's assertion #{kid}", now)
            evolution.link(db, None, "knowledge", kid, "supersedes", "knowledge", current["id"],
                           {"reason": "owner assertion"}, now)
            db.execute("INSERT INTO web_feedback(task_id,knowledge_id,key,effect,before_confidence,after_confidence,"
                       "info_gain,detail,created_at) VALUES(NULL,?,?,'reversed',?,0.5,0,?,?)",
                       (kid, key, state.finite(current["confidence"], 0.0),
                        state.dumps({"superseded": current["id"], "owner_assertion": True}), now))
        store.bump(db, "teacher_interventions")
        version = store.bump_state_version(db)
    return {"id": kid, "status": "accepted", "provenance": "user_asserted", "superseded": superseded,
            "state_version": version}


def forget(db, knowledge_id, now) -> dict:
    """Forget the item and every version in its chain (governance.forget_knowledge)."""
    from ..instance import governance
    from . import semantic
    try:
        return governance.forget_knowledge(db, knowledge_id, now)
    finally:
        semantic.clear()      # no vector or text of a forgotten statement stays in this process (semantic.py)
        clear_index()         # ... nor its tokens in the lexical index


OWNER_REFRESH = "refresh-owner:"


def refresh(db, knowledge_id, now, *, origin="cli") -> dict:
    """Submit (not run) a fact-verification task for the item's statement; returns the queued task.

    `origin` names who asked. The owner's paths pass 'cli' or 'console': the task is recorded with origin
    'refresh-owner:<id>' (orchestrator.origin_label shows "Refresh of knowledge #<id>"), an owner task never
    clamped to the autonomous allowance (orchestrator.is_autonomous is False). Only a due prediction passes
    'refresh:<id>', which is autonomous research. Either way the task is tied to the item, so forgetting the item
    forgets its unfinished refreshes (governance.forget_knowledge)."""
    from . import orchestrator
    item = row(db, knowledge_id)
    if item["status"] not in REVIEWABLE:
        raise Conflict(f"Knowledge item {knowledge_id} is {item['status']}; refresh the current version instead")
    if origin in ("cli", "console"):
        origin = f"{OWNER_REFRESH}{item['id']}"
    return orchestrator.submit(db, item["statement"], now, mode="fact_verification", origin=origin,
                               parent=item["task_id"])
