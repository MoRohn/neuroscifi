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
# Two optional parts of a definition, both about which file `learner.pack` chooses. A rule without them (gate-v1 and
# every rule registered before they existed) keeps its meaning and its sha256: the smallest file cut from the dense
# fit whose validation error is within the tolerance of the dense FP32 graph.
#   pack_candidates.learned_sparse: true   the graph the edge-exchange learner fitted is a candidate too
#   pack_reference: "best_learner"         the tolerance is measured against the better of the two learners' own
#                                          graphs instead of the dense one (needs learned_sparse)
# With both, the rule can promote the sparse learner's graph, the only one of the two that still fits when there are
# many populations (docs/architecture/API_CONTRACTS.md, "Which graph is packed"; ADR-045). store.BEST_LEARNER_V1
# is gate-v1 with both: a new individual is born with it after gate-v1, and an individual born earlier gets it only by
# its owner's `nsf graph rules add --builtin best-learner-v1 --owner-approved`.
PACK_REFERENCES = ("dense", "best_learner")
OPTIONAL = ("pack_reference",)


def canonical(definition):
    return json.dumps(definition, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate(definition):
    if not isinstance(definition, dict) or not set(REQUIRED) <= set(definition) <= set(REQUIRED) | set(OPTIONAL):
        raise ValidationError("Rule must define exactly: " + ", ".join(sorted(REQUIRED))
                              + " (optional: " + ", ".join(OPTIONAL) + ")")
    for key, kind in REQUIRED.items():
        value = definition[key]
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise ValidationError(f"Rule field {key} must be {kind.__name__}")
    pack = definition["pack_candidates"]
    if not {"degrees", "bits"} <= set(pack) <= {"degrees", "bits", "learned_sparse"} or not pack["degrees"] \
            or not set(pack["bits"]) <= {4, 8} or not all(isinstance(d, int) and d > 0 for d in pack["degrees"]):
        raise ValidationError("pack_candidates needs positive degrees and bits from {4, 8}")
    learned = pack.get("learned_sparse", False)
    if not isinstance(learned, bool):
        raise ValidationError("pack_candidates.learned_sparse must be true or false")
    if learned and not pack["bits"]:
        raise ValidationError("pack_candidates.learned_sparse needs at least one bit width")
    reference = definition.get("pack_reference", PACK_REFERENCES[0])
    if reference not in PACK_REFERENCES:
        raise ValidationError("pack_reference must be one of: " + ", ".join(PACK_REFERENCES))
    if reference == "best_learner" and not learned:
        raise ValidationError("pack_reference best_learner needs pack_candidates.learned_sparse: true")
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
