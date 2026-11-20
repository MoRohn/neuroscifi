"""Immutable, versioned scoring rules for graph promotion.

A candidate records the rule version that existed when it was created. Rules can only be
added (by birth or an owner command), never edited or removed; SQLite triggers enforce it.
The learner pipeline has no code path that registers rules, so it cannot change its own exam.
"""
import hashlib
import json

from .errors import Conflict, NotFound, ValidationError

REQUIRED = {"version": str, "validation_tolerance": float, "pack_candidates": dict,
            "min_heldout_gain": float, "retention_tolerance": float, "byte_budget": int,
            "unknown_context_ratio": float, "recall_history": int}


def canonical(definition):
    return json.dumps(definition, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate(definition):
    if not isinstance(definition, dict) or set(definition) != set(REQUIRED):
        raise ValidationError("Rule must define exactly: " + ", ".join(sorted(REQUIRED)))
    for key, kind in REQUIRED.items():
        value = definition[key]
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise ValidationError(f"Rule field {key} must be {kind.__name__}")
    pack = definition["pack_candidates"]
    if set(pack) != {"degrees", "bits"} or not pack["degrees"] or not set(pack["bits"]) <= {4, 8} \
            or not all(isinstance(d, int) and d > 0 for d in pack["degrees"]):
        raise ValidationError("pack_candidates needs positive degrees and bits from {4, 8}")
    if not (0 <= definition["validation_tolerance"] <= 1 and 0 <= definition["retention_tolerance"] <= 1
            and 0 <= definition["min_heldout_gain"] < 1 and definition["byte_budget"] > 0
            and definition["unknown_context_ratio"] > 1 and definition["recall_history"] >= 1):
        raise ValidationError("Rule thresholds are out of range")
    if not definition["version"].strip() or len(definition["version"]) > 64:
        raise ValidationError("Rule version must be a short nonempty name")
    return definition


def register(db, definition, now, *, created_by):
    """Add a rule version. Caller owns the transaction. Same content twice is idempotent."""
    validate(definition)
    text = canonical(definition)
    digest = hashlib.sha256(text.encode()).hexdigest()
    row = db.execute("SELECT sha256 FROM scoring_rules WHERE version=?", (definition["version"],)).fetchone()
    if row:
        if row[0] != digest:
            raise Conflict(f"Rule {definition['version']} already exists with different content")
        return definition["version"]
    db.execute("INSERT INTO scoring_rules VALUES(?,?,?,?,?)",
               (definition["version"], text, digest, now, created_by))
    return definition["version"]


def get(db, version):
    row = db.execute("SELECT definition, sha256 FROM scoring_rules WHERE version=?", (version,)).fetchone()
    if not row:
        raise NotFound(f"Unknown scoring rule {version}")
    if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        raise Conflict(f"Scoring rule {version} failed its integrity check")
    return json.loads(row[0])


def latest(db):
    row = db.execute("SELECT version FROM scoring_rules ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        raise NotFound("No scoring rule registered")
    return row[0]


def listing(db):
    return [{"version": v, "sha256": h, "created_at": t, "created_by": b, "definition": json.loads(d)}
            for v, d, h, t, b in db.execute(
                "SELECT version, definition, sha256, created_at, created_by FROM scoring_rules ORDER BY rowid")]
