"""Uncertainty shown to the owner (Wave 4 lane D, ADR-065): where an answer, a knowledge item or a contradiction
question rests on sources the system cannot show to be independent, say so in plain words.

DISPLAY ONLY. Nothing in the product reads what this module returns to decide anything: no verification state,
confidence, answer, knowledge item, question, budget or research step depends on it, and it writes nothing. It is
computed when a view is drawn, from facts the records already hold, and only the views import it (console, CLI,
Pocket, voice; tests/acceptance/test_wave4_d.py checks that no other module does). Today's rule for what counts as a
source is not changed; it is read (claims.independence_groups over the statement's supporting claims, as stored).

The facts, per statement:
  pages      the distinct pages whose claims support it
  groups     how many independent sources those pages count as under today's rule
  joined     why some pages were counted as one source: "same_site", "near_duplicate" (nearly the same wording),
             "named_origin" (a page names where the story came from)
  own_answer whether a supporting page repeats an earlier answer of this individual's own (its value before any source
             it read stated it, or its wording: consolidate._Echoes, read only, within OWN_SECONDS). A flag computed for
             display, never a rule; None when not checked (time, or a view that may not see the owner's answers).

When a caution is shown (written before it was measured; ADR-065): when the statement rests on at most one source that
can be told apart (groups <= 1), when some of its pages were counted together as copies (pages > groups: copying is
present in this story, and reworded copies are not caught), or when a supporting page repeats an earlier answer of
the individual's own. The words say what is known and what is not; they never say the statement is wrong.
"""
from __future__ import annotations

from . import claims as C
from . import state
from . import text as T

OWN_SECONDS = 0.5       # the own-answer check's budget per view (consolidate._Echoes' deadline); unchecked: None
MAX_CLAIMS = 40         # supporting claims read per statement (the rest are not looked at)

_JOINED_WORDS = {"same_site": "some are on the same site", "near_duplicate": "some have nearly the same wording",
                 "named_origin": "some name the same origin"}


def facts(db, claim_rows, *, own_check: bool = True, own=None) -> dict | None:
    """The facts of one statement from its supporting claims' rows (state.CLAIM_COLUMNS dicts); None without any.
    `own`: an _Own made for several statements at once (one time budget per view)."""
    claims = [state.claim_from_row(r) for r in claim_rows[:MAX_CLAIMS]]
    if not claims:
        return None
    groups = C.independence_groups(claims)
    joined = set()
    by_id = {c.id: c for c in claims}
    for ids in groups:
        members = [by_id[i] for i in ids if i in by_id]
        if len({m.source_url for m in members}) < 2:
            continue
        for i, a in enumerate(members):
            if (a.flags or {}).get("origin"):
                joined.add("named_origin")
            for b in members[i + 1:]:
                if a.source_url == b.source_url:
                    continue
                if C._domain(a) == C._domain(b):
                    joined.add("same_site")
                elif T.jaccard(T.shingles(a.text, C.SHINGLE_K), T.shingles(b.text, C.SHINGLE_K)) >= \
                        C.DUPLICATE_SHINGLE:
                    joined.add("near_duplicate")
    if own_check:
        own = own or _Own(db, claims)
        found = own.answer(claims)
    else:
        found = None
    return {"pages": len({c.source_url for c in claims}), "groups": len(groups), "joined": sorted(joined),
            "own_answer": found}


class _Own:
    """Whether supporting pages repeat an earlier answer of the individual's own: consolidate._Echoes over the claims
    of one view, read only, within OWN_SECONDS (display only; nothing decides on it)."""

    def __init__(self, db, claims):
        from . import consolidate
        try:
            self.echoes = consolidate._Echoes(db, claims, seconds=OWN_SECONDS)
        except Exception:        # noqa: BLE001 - a display fact: not shown when it cannot be computed
            self.echoes = None

    def answer(self, claims) -> bool | None:
        """True when one of `claims` repeats one; None when not all could be checked (or the check failed)."""
        if self.echoes is None:
            return None
        if any(self.echoes.of(c) in ("value", "wording") for c in claims):
            return True
        return None if set(self.echoes.skipped) & {c.id for c in claims} else False


def shown(f: dict | None) -> bool:
    """Whether a caution is shown for these facts (the rule in the module docstring)."""
    return bool(f) and (f["groups"] <= 1 or f["pages"] > f["groups"] or f.get("own_answer") is True)


def sentence(f: dict | None) -> str | None:
    """The caution in plain words for a non-technical owner, or None when none is shown."""
    if not shown(f):
        return None
    pages, groups = f["pages"], f["groups"]
    why = "; ".join(_JOINED_WORDS[j] for j in f["joined"] if j in _JOINED_WORDS)
    if pages <= 1:
        out = "Only one source says this."
    elif groups <= 1:
        out = f"{pages} pages say this, but they look like copies of one source" + (f" ({why})." if why else ".")
    elif pages > groups:
        out = (f"{pages} pages say this; some look like copies" + (f" ({why})" if why else "") +
               f", so they count as {groups} sources.")
    else:
        out = f"{pages} pages from {groups} sources say this."
    if pages > 1:
        out += " A reworded copy cannot be told from an independent report, so there may be fewer."
    if f.get("own_answer") is True:
        out += " One of these pages repeats something this system said earlier, so it may not be independent."
    return out


def brief(f: dict | None) -> str | None:
    """One short spoken sentence (voice says it once)."""
    if not shown(f):
        return None
    if f.get("own_answer") is True:
        return "Careful: a source may just repeat something I said before."
    if f["pages"] <= 1:
        return "Careful: only one source says this."
    return "Careful: these sources may be copies of each other."


def view(db, claim_rows, *, own_check: bool = True, own=None) -> dict | None:
    """{"pages", "groups", "joined", "own_answer", "text", "brief"} when a caution is shown, else None."""
    f = facts(db, claim_rows, own_check=own_check, own=own)
    text = sentence(f)
    return None if text is None else f | {"text": text, "brief": brief(f)}


def _rows(db, where, params) -> list[dict]:
    return [dict(zip(state.CLAIM_COLUMNS, r)) for r in db.execute(
        f"SELECT {','.join(state.CLAIM_COLUMNS)} FROM web_claims WHERE {where} ORDER BY rowid LIMIT ?",
        (*params, MAX_CLAIMS))]


def for_report(db, task_id, report: dict, *, own_check: bool = True) -> dict:
    """{"answer": [caution or None per report["answer"] item], "verdict": caution or None} for one research report.
    A quoted statement's supporting claims are its finding's claims (synthesis) from the pages it cites or that
    corroborate it."""
    report = report if isinstance(report, dict) else {}
    urls = {s.get("n"): s.get("url") for s in report.get("sources") or () if isinstance(s, dict)}
    findings = {f.get("statement"): f for f in report.get("findings") or () if isinstance(f, dict)}

    def supporting(numbers, ids):
        wanted = {urls[n] for n in numbers or () if n in urls}
        ids = [i for i in ids or () if isinstance(i, str)][:200]
        if not wanted or not ids:
            return []
        rows = _rows(db, f"task_id=? AND id IN ({','.join('?' * len(ids))})", (str(task_id), *ids))
        return [r for r in rows if r["source_url"] in wanted or r["canonical_url"] in wanted]

    statements = []
    for a in report.get("answer") or ():
        if not isinstance(a, dict) or a.get("type") != "quote":
            statements.append(None)
            continue
        f = findings.get(a.get("text")) or {}
        statements.append(supporting([*(a.get("citations") or ()), *(a.get("corroborating") or ())], f.get("claims")))
    verdict_rows = None
    v = report.get("verdict")
    if isinstance(v, dict) and v.get("supporting"):
        ids = [i for i in v["supporting"] if isinstance(i, str)][:200]
        if ids:
            verdict_rows = _rows(db, f"task_id=? AND id IN ({','.join('?' * len(ids))})", (str(task_id), *ids))
    own = None
    if own_check:           # one time budget for the whole view
        every = {r["id"]: r for rows in [*statements, verdict_rows] if rows for r in rows[:MAX_CLAIMS]}
        own = _Own(db, [state.claim_from_row(r) for r in every.values()]) if every else None
    return {"answer": [None if rows is None else view(db, rows, own_check=own_check, own=own) for rows in statements],
            "verdict": None if verdict_rows is None else view(db, verdict_rows, own_check=own_check, own=own)}


def for_contested(db, task_id, pending_id, *, own_check: bool = True) -> list | None:
    """The caution of each side of a contradiction question (scope web:contested:<task>:<n>): [{"value", "caution"}]
    for the sides that carry one, from the task's claims about the question's subject stating that value; None when
    none does (or the question's record is gone)."""
    import json
    found = db.execute("SELECT key, detail FROM web_feedback WHERE task_id=? AND json_extract(detail, '$.pending_id')=? "
                       "ORDER BY rowid DESC LIMIT 1", (str(task_id), int(pending_id))).fetchone()
    if found is None:
        return None
    key, detail = found[0], state.loads(found[1], {}) or {}
    values = [v for v in (detail.get("values") or []) if isinstance(v, str)][:4]
    sides = [(v, _rows(db, "task_id=? AND key=? AND instr(claim_values, ?) > 0", (str(task_id), key, json.dumps(v))))
             for v in values]
    own = None
    if own_check:
        every = {r["id"]: r for _, rows in sides for r in rows}
        own = _Own(db, [state.claim_from_row(r) for r in every.values()]) if every else None
    out = [{"value": v, "caution": view(db, rows, own_check=own_check, own=own)} for v, rows in sides if rows]
    return [x for x in out if x["caution"]] or None


def for_knowledge(db, items, *, own_check: bool = True) -> list:
    """The caution of each knowledge item (its supporting claims, as stored with it), one time budget for them all."""
    rows = []
    for item in items:
        ids = [i for i in ((item or {}).get("supporting") or ()) if isinstance(i, str)][:200]
        rows.append(_rows(db, f"id IN ({','.join('?' * len(ids))})", tuple(ids)) if ids else [])
    own = None
    if own_check:
        every = {r["id"]: r for found in rows for r in found}
        own = _Own(db, [state.claim_from_row(r) for r in every.values()]) if every else None
    return [view(db, found, own_check=own_check, own=own) if found else None for found in rows]
