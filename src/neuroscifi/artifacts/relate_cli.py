"""`nsf artifact relate ID ID [--question "…"]` (lane R; ADR-077): relate two held artifacts by measurement and keep
what was found as links.

Both artifacts are decoded again from their verified bytes (store.decoded_held), compared by `artifacts.relate`, and
the result is printed in plain words. Links are recorded in lane A's `artifact_links` (store.link): `consistent_with`
or `contradicts` when there is an overall answer, `describes` when one names the other's file, `same_subject` (weak)
when a name occurs in both. `how` is the machine-readable basis (which quantity, which statement, the tolerance),
never text from an artifact. Every such link is the SYSTEM'S CLAIM about its artifacts, made by measuring both: it
is labelled so wherever it is shown, is never evidence and never enters a knowledge table. Forgetting either end
removes it (store._remove deletes links from and to a forgotten artifact). A shared name that is one of the owner's
forgotten roots is never used. Nothing runs unless the owner types the command.
"""
from __future__ import annotations

import re

CLAIM_RELATIONS = ("consistent_with", "contradicts", "describes", "same_subject")
CLAIM_LABEL = "the system's claim from measuring both artifacts; not evidence"
CONFIDENCE = {"consistent_with": 0.8, "contradicts": 0.8, "describes": 0.6, "same_subject": 0.3}


def _forgotten(db, name: str) -> bool:
    """Whether a name (or one of its words) is a root the owner has forgotten (governance.root_digest)."""
    from ..instance import governance, store
    instance_id = store.birth(db)["instance_id"]
    candidates = {name, name.lower(), name.strip()} | set(re.findall(r"[\w'-]+", name)) | \
        {w.lower() for w in re.findall(r"[\w'-]+", name)}
    for root in candidates:
        if db.execute("SELECT 1 FROM forgotten_roots WHERE digest=?",
                      (governance.root_digest(instance_id, root),)).fetchone():
            return True
    return False


def relate(db, first: str, second: str, *, question: str | None = None, now=None, record: bool = True) -> dict:
    from . import relate as R
    from . import store
    if first == second:
        from .types import Malformed
        raise Malformed("give two different artifacts")
    sides, records, unread = [], [], []
    for artifact_id in (first, second):
        rec, data, decoded, error = store.decoded_held(db, artifact_id)
        records.append(rec)
        if decoded is None:
            unread.append({"id": rec.id, "reason": (error or {}).get("message", "not decoded")})
            continue
        sides.append(R.side(decoded, name=rec.label if rec.origin == "given" else None, data=data))
    out = {"first": first, "second": second, "question": question, "label": CLAIM_LABEL, "links": [],
           "made": [r.id for r in records if r.origin == "made"]}
    if unread:
        out |= {"answer": None, "because": ["cannot tell: " + "; ".join(f"{u['id']} could not be read here "
                                                                        f"({u['reason']})" for u in unread)],
                "relation": None}
        return out
    rel = R.compare(sides[0], sides[1], question)
    shared = [s for s in rel.shared if not _forgotten(db, s["name"])]
    rel.shared = shared
    out |= {"answer": rel.answer, "because": rel.because(), "relation": rel.to_dict()}
    if not record:
        return out
    out["links"] = record_links(db, first, second, rel, now=now)
    return out


def record_links(db, first: str, second: str, rel, *, now=None) -> list[dict]:
    """The links a Relation of two held artifacts supports, recorded in `artifact_links` (the module doc's rules;
    the production pipeline records the relations of its sources through this too)."""
    from . import store
    shared = rel.shared
    links = []
    # lane FX4 (R3): only an answer decided under a question that names what is compared and where becomes a
    # durable link; an answer from "everything the two artifacts let me compare" is shown, never recorded
    if rel.answer is True and getattr(rel, "specific", False):
        links.append(("consistent_with", first, second, rel.basis()))
    elif rel.answer is False and getattr(rel, "specific", False):
        links.append(("contradicts", first, second, rel.basis()))
    for m in rel.describes:
        src, dst = (first, second) if m["describes"] == "first" else (second, first)
        links.append(("describes", src, dst, "mentions=file_name"))
    if shared:
        links.append(("same_subject", first, second, f"shared_names={len(shared)}"))
    out = []
    for relation, src, dst, how in links:
        result = store.link(db, src, relation, "artifact", dst, how, CONFIDENCE[relation], now=now)
        out.append({"relation": relation, "src": src, "dst": dst, "how": how, "status": result["status"]})
    return out


# Lane FX4 (R9): which comparison decided a recorded consistent_with / contradicts link is in its `how` (the
# Relation's basis: "<first ref>/<second ref>;tol=..." for each deciding comparison, `relate.Relation.basis`). The
# three comparisons lane FX3 added are told apart by their refs, so their links can be listed and removed without a
# new column (no migration). A rollback of those comparisons removes these links with `remove_links_decided_by`.
DECIDED_BY_REFS = {"members": frozenset({"bundle.members.listing", "document.listing"}),
                   "links": frozenset({"drawing.links", "document.links"}),
                   "times": frozenset({"signal.tone_starts", "document.times"})}


def _refs_of(how: str) -> set:
    out = set()
    for part in str(how or "").split(";"):
        if "/" in part and not part.startswith("tol="):
            out.update(x for x in part.split("/") if x)
    return out


def links_decided_by(db, comparison: str) -> list[dict]:
    """The consistent_with / contradicts links whose deciding comparisons include `comparison` (one of
    DECIDED_BY_REFS)."""
    if comparison not in DECIDED_BY_REFS:
        raise ValueError(f"comparison must be one of {', '.join(sorted(DECIDED_BY_REFS))}")
    refs = DECIDED_BY_REFS[comparison]
    rows = db.execute("SELECT src, relation, dst_kind, dst_id, how FROM artifact_links WHERE relation IN "
                      "('consistent_with', 'contradicts') ORDER BY created_at, src").fetchall()
    return [dict(zip(("src", "relation", "dst_kind", "dst_id", "how"), r)) for r in rows
            if refs <= _refs_of(r[4])]


def remove_links_decided_by(db, comparison: str) -> list[dict]:
    """Remove those links (the rollback of a comparison); returns what was removed. The artifacts, their events and
    every other link stay. No event is written: artifact_events' actions are fixed by the schema (no migration)."""
    from ..instance import store
    found = links_decided_by(db, comparison)
    if not found:
        return []
    with store.write(db):
        for x in found:
            db.execute("DELETE FROM artifact_links WHERE src=? AND relation=? AND dst_kind=? AND dst_id=? AND how=?",
                       (x["src"], x["relation"], x["dst_kind"], x["dst_id"], x["how"]))
        store.bump_state_version(db)
    return found


def plain(value: dict) -> str:
    verdict = {True: "they agree", False: "they DISAGREE", None: "cannot tell"}[value["answer"]]
    lines = [f"Relating {value['first']} and {value['second']}: {verdict}."]
    if value.get("question"):
        lines.append(f"  asked: {value['question'][:200]}")
    for line in value.get("because", [])[:14]:
        lines.append(f"  - {line}")
    if value.get("made"):
        lines.append("  (" + ", ".join(value["made"]) + " was made by the system: it is not evidence)")
    for link in value.get("links", []):
        lines.append(f"  recorded: {link['src']} {link['relation']} {link['dst']} ({link['how']}; {CLAIM_LABEL})")
    if not value.get("links"):
        lines.append("  nothing recorded")
    return "\n".join(lines)


__all__ = ["CLAIM_LABEL", "CLAIM_RELATIONS", "plain", "record_links", "relate"]
