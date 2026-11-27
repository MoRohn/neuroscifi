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
    links = []
    if rel.answer is True:
        links.append(("consistent_with", first, second, rel.basis()))
    elif rel.answer is False:
        links.append(("contradicts", first, second, rel.basis()))
    for m in rel.describes:
        src, dst = (first, second) if m["describes"] == "first" else (second, first)
        links.append(("describes", src, dst, "mentions=file_name"))
    if shared:
        links.append(("same_subject", first, second, f"shared_names={len(shared)}"))
    for relation, src, dst, how in links:
        result = store.link(db, src, relation, "artifact", dst, how, CONFIDENCE[relation], now=now)
        out["links"].append({"relation": relation, "src": src, "dst": dst, "how": how, "status": result["status"]})
    return out


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


__all__ = ["CLAIM_LABEL", "CLAIM_RELATIONS", "plain", "relate"]
