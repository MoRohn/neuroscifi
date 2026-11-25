"""The graph-rule migration (ADR-047): bring an individual made by older software under the rules of today, once.

Two changes, both made at the first open of the file by software that has this module, both recorded, both
reversible with `nsf graph migration undo --owner-approved`:

1. Birth rule. An individual whose newest scoring rule is still `gate-v1` (born before `best-learner-v1` existed,
   and never told otherwise by its owner) gets `best-learner-v1` registered as its newest rule, so candidates learned
   from now on are packed as a new individual's are (ADR-045). Rules are immutable and nothing is removed: every
   revision keeps the rule it was decided under, and gate-v1 stays on record.
2. Active graphs. An active graph that predicts worse than a graph with no edges (check (f) of the gate, which no
   rule waives, ADR-045) is archived with a decision record saying why. It keeps its snapshot, metrics and lineage;
   the owner can re-activate it with `nsf graph rollback`, and the deletion cascade never revives it on its own.
   A graph that cannot be judged (founding, imported, or its validation data gone) stays active and is listed.

Not applied: to an individual born by this software (its rules are the ones it was born with: settle_at_birth),
while the owner's setting `graph_rule_migration` is false, to a file this process cannot write (a read-only open:
retried at the next writable open), while any context's lease is held (a graph job is running: retried at the next
open). An owner who had already chosen a rule (best-learner-v1 registered, or a rule of their own newest) keeps it.

The record is one meta row, `graph_rule_migration`: {"status": "settled_at_birth" | "applied" | "reversed", ...}.
"""
import json
import sqlite3

from .. import __version__
from . import graphs, rules, store
from .errors import Conflict, InstanceError, LeaseHeld

MARKER = "graph_rule_migration"
CREATED_BY = "migration"
FROM_RULE = "gate-v1"


def _record(db):
    row = db.execute("SELECT value FROM meta WHERE key=?", (MARKER,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return {"status": "unreadable"}
    return value if isinstance(value, dict) else {"status": "unreadable"}


def _save(db, record):
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (MARKER, json.dumps(record, sort_keys=True)))


def settle_at_birth(db, now):
    """Mark a new individual as never needing the migration. Caller owns the transaction."""
    _save(db, {"status": "settled_at_birth", "at": now, "software_version": __version__})


def _rule_change(db):
    """(change, note): change = {"from", "to"} when best-learner-v1 is to be adopted, else None with the reason."""
    versions = [r["version"] for r in rules.listing(db)]
    latest = versions[-1] if versions else None
    target = store.BEST_LEARNER_V1["version"]
    if latest != FROM_RULE:
        return None, (f"the newest rule is already {target}" if latest == target
                      else f"the newest rule is {latest}, not {FROM_RULE}: it is kept")
    if target in versions:
        return None, f"{target} is registered and the owner chose {FROM_RULE} after it: kept"
    try:
        if rules.get(db, FROM_RULE) != store.GATE_V1:
            return None, f"this file's {FROM_RULE} is not the built-in one: kept"
    except InstanceError as error:
        return None, f"this file's {FROM_RULE} cannot be read ({error}): kept"
    return {"from": FROM_RULE, "to": target}, None


def plan(db):
    """What apply() would do now, without writing: {"rule", "rule_note", "demote", "keep", "not_judged"}."""
    change, note = _rule_change(db)
    demote, keep, not_judged = [], [], []
    for (revision_id,) in db.execute("SELECT id FROM graph_revisions WHERE status='active' ORDER BY id").fetchall():
        rev = graphs._row(db, revision_id)
        try:
            check = graphs.no_graph_check(db, rev)
        except (InstanceError, ValueError) as error:    # a damaged snapshot or dataset: judged by nobody here
            check = {"passed": None, "why": f"it could not be judged: {error}"}
        entry = {"revision": revision_id, "context": rev["context"], "check": check}
        (demote if check["passed"] is False else keep if check["passed"] else not_judged).append(entry)
    return {"rule": change, "rule_note": note, "demote": demote, "keep": keep, "not_judged": not_judged}


def _leased(db, now):
    return [c for (c,) in db.execute("SELECT context FROM graph_leases WHERE expires_at>? ORDER BY context", (now,))]


def apply(db, now):
    """Run the migration once if it is due; returns the record written, or None when nothing was done (not due,
    opted out, read-only, busy or a lease held: the next open tries again). Never raises for a file it cannot write."""
    if _record(db) is not None or not store.config(db).graph_rule_migration:
        return None
    try:
        with store.write(db):
            if _record(db) is not None:          # another process did it while this one waited for the lock
                return None
            if _leased(db, now):
                return None
            todo = plan(db)
            if todo["rule"]:
                rules.register(db, store.BEST_LEARNER_V1, now, created_by=CREATED_BY)
            for entry in todo["demote"]:
                rev = graphs._row(db, entry["revision"])
                check = entry["check"] | {"why": "it predicts worse than a graph with no edges on its validation "
                                                 "data, which the promotion gate never lets through (ADR-045)"}
                decision = {"action": graphs.DEMOTED, "by": CREATED_BY, "decided_at": now, "check": check,
                            "prior": rev["decision"]}
                db.execute("UPDATE graph_revisions SET status='archived', decision=?, decided_at=? WHERE id=?",
                           (graphs._dump(decision), now, entry["revision"]))
            changed = bool(todo["rule"] or todo["demote"])
            record = {"status": "applied", "at": now, "software_version": __version__, **todo,
                      "state_version": store.bump_state_version(db) if changed else store.state_version(db)}
            _save(db, record)
    except sqlite3.OperationalError as error:
        text = str(error)
        if "readonly" in text or "read-only" in text or "locked" in text or "busy" in text:
            return None
        raise
    return record


def status(db):
    """The record, or {"status": "pending", "plan": ...} for a file the migration has not reached yet."""
    record = _record(db)
    if record is None:
        cfg = store.config(db)
        return {"status": "pending" if cfg.graph_rule_migration else "opted_out", "plan": plan(db)}
    return record


def undo(db, now):
    """Reverse an applied migration (owner command). The owner's setting turns off, so it never runs again, not even
    in a copy restored from an older archive (the setting is carried by a restore). Gate-v1 is registered again as
    the newest rule under a new name if best-learner-v1, added by the migration, is still the newest; each demoted
    graph is re-activated if it is still archived as demoted, its data is intact and its context has no active graph.
    What cannot be reversed is listed with the reason. Returns the new record."""
    with store.write(db):
        record = _record(db)
        if not record or record.get("status") != "applied":
            raise Conflict("Nothing to undo: the graph-rule migration has not been applied to this individual "
                           f"(status {record.get('status') if record else 'pending'})")
        held = _leased(db, now)
        if held:
            raise LeaseHeld(f"A graph job holds the lease of {', '.join(held)}; undo when it has finished")
        done, left = [], []
        if record.get("rule"):
            newest = rules.latest(db)
            if newest != record["rule"]["to"]:
                left.append({"rule": record["rule"]["from"],
                             "why": f"the newest rule is now {newest}, chosen after the migration: kept"})
            else:
                versions = {r["version"] for r in rules.listing(db)}
                name, n = f"{record['rule']['from']}-restored", 1
                while name in versions:
                    n += 1
                    name = f"{record['rule']['from']}-restored-{n}"
                definition = dict(rules.get(db, record["rule"]["from"]), version=name)
                rules.register(db, definition, now, created_by="owner")
                done.append({"rule": name, "same_as": record["rule"]["from"]})
        for entry in record.get("demote", []):
            revision_id, context = entry["revision"], entry["context"]
            row = db.execute("SELECT status, decision FROM graph_revisions WHERE id=?", (revision_id,)).fetchone()
            decision = json.loads(row[1]) if row and row[1] else {}
            active = graphs._active(db, context)
            if row is None or row[0] != "archived" or decision.get("action") != graphs.DEMOTED:
                left.append({"revision": revision_id, "context": context,
                             "why": f"it is {row[0] if row else 'gone'} and was changed after the migration"})
            elif not graphs._lineage_intact(db, revision_id):
                left.append({"revision": revision_id, "context": context, "why": "it was learned from a deleted dataset"})
            elif active is not None:
                left.append({"revision": revision_id, "context": context,
                             "why": f"revision {active} is active in {context} now; `nsf graph rollback {context} "
                                    f"{revision_id}` re-activates it instead"})
            else:
                db.execute("UPDATE graph_revisions SET status='active', decision=?, decided_at=? WHERE id=?",
                           (graphs._dump({"action": "undo_demote", "by": "owner", "decided_at": now,
                                          "prior": decision}), now, revision_id))
                done.append({"revision": revision_id, "context": context, "reactivated": True})
        raw = json.loads(store._meta(db, "config"))
        if raw.get("graph_rule_migration") is not False:
            before = raw.get("graph_rule_migration", True)
            raw["graph_rule_migration"] = False
            db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw, sort_keys=True),))
        else:
            before = None
        version = store.bump_state_version(db)
        if before is not None:
            db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                       (store.SETTING_KIND, "graph_rule_migration", now,
                        json.dumps({"from": before, "to": False, "state_version": version,
                                    "by": "nsf graph migration undo"}, sort_keys=True)))
        new = {**record, "status": "reversed", "reversed_at": now, "undone": done, "not_undone": left,
               "state_version": version}
        _save(db, new)
    return new


def shown(db):
    """The record for `nsf birth` and the console's overview: what the migration did, or undid; None for an
    individual it never applied to (settled at birth, pending or opted out)."""
    record = _record(db)
    if not record or record.get("status") not in ("applied", "reversed"):
        return None
    return {key: record[key] for key in ("status", "at", "software_version", "rule", "rule_note", "demote",
                                         "not_judged", "reversed_at", "undone", "not_undone") if key in record}
