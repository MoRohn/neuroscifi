"""Versioned executable graphs: learn, evaluate, promote under an immutable rule, archive, recall, lesion.

Lifecycle of a revision: 'candidate' (learn) -> 'active' or 'rejected' (promote/reject). Promoting a
candidate archives the incumbent of its context, so at most one revision per context is active and older
graphs stay available for rollback and recall. Deleting a dataset turns every revision trained or
validated on it into 'invalidated' (invalidate_dependents) and erases its weights and metrics (erase_learned_from).

Single writer: learn, promote and rollback hold the context lease (graph_leases) for their duration and
re-check it inside the committing transaction. Fitting and scoring run under the lease but outside any
write transaction; the short committing transaction re-checks what they read (datasets not deleted,
incumbent and lineage unchanged) and raises Gone or Conflict instead of committing a stale result.

The scoring rule is copied from rules.latest(db) when a candidate is created and is immutable afterwards
(schema trigger). Nothing in this module registers rules; only birth and the owner command do.

One check of the gate belongs to no rule: a snapshot that predicts no better than a graph with no edges is never
promoted (learn records the comparison in metrics.beats_no_graph, promote rejects on it; ADR-045).
"""
from contextlib import contextmanager
import hashlib
import json
import os
import threading

import numpy as np

from ..graph_store import HEADER, decode
from . import datasets, learner, rules, store
from .errors import Conflict, Gone, LeaseHeld, NotFound, ValidationError

COLUMNS = ("id", "context", "parent_id", "status", "bits", "degree", "rows", "cols", "byte_size", "sha256",
           "rule_version", "origin", "metrics", "decision", "created_at", "decided_at")
# The gate's checks in the order a decision lists them. The last one is not part of any rule: a snapshot that is not
# better than a graph with no edges is never promoted, whatever the rule says. It is judged from what learn() recorded
# (metrics.beats_no_graph), so a candidate learned before that was recorded has five checks, as it always had.
CHECKS = ("independent_heldout", "heldout_gain", "retention", "byte_budget", "lineage_intact", "beats_no_graph")
NEW_CONTEXT_ADVICE = "register as a new context; the incumbent still solves older data"
REPLACED_BY_ROLLBACK = "replaced_by_rollback"
# Set by the graph-rule migration (graph_migration.py) on an active graph that fails check (f): it was archived
# because it predicts worse than no graph.
DEMOTED = "demote"
# Archived revisions the deletion cascade never re-activates on its own: one the owner rolled back from, and one
# demoted for failing check (f). The owner can still re-activate either with rollback.
NOT_REVIVED = (REPLACED_BY_ROLLBACK, DEMOTED)
LESION_BLOCK = 32          # time steps per block for the lesion standard error
COACTIVATION_LAGS = (1, 2, 3)


# ---- helpers ------------------------------------------------------------------------------------------

def _row(db, revision_id):
    row = db.execute(f"SELECT {','.join(COLUMNS)} FROM graph_revisions WHERE id=?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Unknown graph revision {revision_id}")
    rev = dict(zip(COLUMNS, row))
    rev["metrics"] = json.loads(rev["metrics"]) if rev["metrics"] else {}
    rev["decision"] = json.loads(rev["decision"]) if rev["decision"] else None
    return rev


ERASED = b""      # the blob of a revision whose weights went with a forgotten dataset (schema 15)


def _erased_text(revision_id):
    return (f"Graph revision {revision_id} was erased: it learned from a dataset the owner forgot, so its weights "
            "and metrics are gone")


def _matrix(db, revision_id):
    row = db.execute("SELECT blob FROM graph_revisions WHERE id=?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Unknown graph revision {revision_id}")
    if bytes(row[0]) == ERASED:
        raise Gone(_erased_text(revision_id))
    return decode(row[0])


def _active(db, context):
    row = db.execute("SELECT id FROM graph_revisions WHERE context=? AND status='active'", (context,)).fetchone()
    return row[0] if row else None


def _lineage(db, revision_id):
    return db.execute("SELECT l.dataset_id, l.role, d.sha256, d.deleted_at FROM graph_lineage l "
                      "JOIN datasets d ON d.id=l.dataset_id WHERE l.revision_id=? ORDER BY l.dataset_id",
                      (revision_id,)).fetchall()


def _lineage_intact(db, revision_id):
    return all(deleted is None for _, _, _, deleted in _lineage(db, revision_id))


def _dump(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _holder(operation):
    return f"graphs.{operation}:{os.getpid()}:{threading.get_ident()}"


def _require_lease(db, context, holder):
    row = db.execute("SELECT holder FROM graph_leases WHERE context=?", (context,)).fetchone()
    if row is None or row[0] != holder:
        raise LeaseHeld(f"Lease on context {context} was lost before commit")


def _heldout(db, rev):
    """Held-out datasets of the revision's context that are independent of its lineage.

    Excluded: datasets in the lineage, datasets whose content (sha256) equals a lineage dataset, deleted
    datasets and datasets with a different population count. 'older' are those created before the
    revision's training dataset (lower id); 'current' are the newer ones, or all eligible ones when
    nothing newer exists. Founding revisions have no training dataset, so every eligible set is current.
    """
    lineage = _lineage(db, rev["id"])
    ids = {r[0] for r in lineage}
    shas = {r[2] for r in lineage}
    train = [r[0] for r in lineage if r[1] == "train"]
    eligible, excluded = [], []
    for dataset_id, sha, cols in db.execute(
            "SELECT id, sha256, cols FROM datasets WHERE context=? AND split='heldout' AND deleted_at IS NULL "
            "AND blob IS NOT NULL ORDER BY id", (rev["context"],)):
        if dataset_id in ids or sha in shas:
            excluded.append({"dataset_id": dataset_id, "why": "same data as the revision's lineage"})
        elif cols != rev["cols"] or rev["rows"] != rev["cols"]:
            excluded.append({"dataset_id": dataset_id,
                             "why": f"{cols} populations, graph is {rev['rows']}x{rev['cols']}"})
        else:
            eligible.append(dataset_id)
    older = [d for d in eligible if train and d < min(train)]
    current = [d for d in eligible if d not in older] or list(eligible)
    return eligible, current, older, excluded


def _scores(db, matrix, dataset_ids):
    out = {}
    for dataset_id in dataset_ids:
        states, targets = datasets.load(db, dataset_id)
        out[dataset_id] = learner.mse(matrix, states, targets)
    return out


def _insert_revision(db, *, context, parent_id, status, blob, rule_version, origin, metrics, now):
    _, bits, rows, cols, degree = HEADER.unpack(blob[:HEADER.size])
    cur = db.execute(
        "INSERT INTO graph_revisions(context,parent_id,status,bits,degree,rows,cols,byte_size,sha256,blob,"
        "rule_version,origin,metrics,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (context, parent_id, status, bits, degree, rows, cols, len(blob), hashlib.sha256(blob).hexdigest(),
         blob, rule_version, origin, _dump(metrics), now))
    return cur.lastrowid


# ---- lease --------------------------------------------------------------------------------------------

@contextmanager
def lease(db, context, holder, now, ttl=600):
    """Own the mutable graph of `context` while the block runs.

    LeaseHeld if another holder has an unexpired lease. An expired lease is taken over. The same holder
    renews its own lease (and then leaves it in place on exit); a fresh lease is released on exit.
    Leases are coordination state, so they do not bump state_version.
    """
    datasets._check_context(context)
    if not isinstance(holder, str) or not holder.strip() or ttl <= 0:
        raise ValidationError("A lease needs a holder name and a positive ttl")
    with store.write(db):
        row = db.execute("SELECT holder, expires_at FROM graph_leases WHERE context=?", (context,)).fetchone()
        if row and row[0] != holder and row[1] > now:
            raise LeaseHeld(f"Context {context} is leased by {row[0]} until {row[1]}")
        renewed = bool(row and row[0] == holder and row[1] > now)
        db.execute("INSERT INTO graph_leases(context,holder,expires_at) VALUES(?,?,?) ON CONFLICT(context) "
                   "DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at",
                   (context, holder, now + ttl))
    try:
        yield {"context": context, "holder": holder, "expires_at": now + ttl}
    finally:
        if not renewed:
            with store.write(db):
                db.execute("DELETE FROM graph_leases WHERE context=? AND holder=?", (context, holder))


# ---- creation -----------------------------------------------------------------------------------------

def register_founding(db, nsg_blob, now):
    """Register an NSG1 snapshot as the active graph of context 'founding'. Opens its own transaction."""
    blob = bytes(nsg_blob)
    try:
        matrix = decode(blob)
    except ValueError as error:
        raise ValidationError(f"Founding graph is not a valid NSG1 snapshot: {error}") from None
    if not np.isfinite(matrix).all():
        raise ValidationError("Founding graph must be finite")
    with store.write(db):
        if _active(db, "founding") is not None:
            raise Conflict("This individual already has an active founding graph")
        revision = _insert_revision(db, context="founding", parent_id=None, status="active", blob=blob,
                                    rule_version=rules.latest(db), origin="founding", metrics={}, now=now)
        db.execute("UPDATE graph_revisions SET decided_at=?, decision=? WHERE id=?",
                   (now, _dump({"action": "founding", "decided_at": now}), revision))
        store.bump_state_version(db)
    return revision


def learn(db, context, now, *, train, validation, holder):
    """Fit a candidate revision from a train and a validation dataset of `context`, holding its lease. WritesPaused
    while instance regulation pauses writes (store.require_write_allowed, free space of the instance's own file)."""
    datasets._check_context(context)
    store.require_write_allowed(db)
    with lease(db, context, holder, now):
        meta = {}
        for dataset_id, split in ((train, "train"), (validation, "validation")):
            row = db.execute("SELECT context, split, cols FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            if row is None:
                raise NotFound(f"Unknown dataset {dataset_id}")
            if row[0] != context or row[1] != split:
                raise ValidationError(f"Dataset {dataset_id} is {row[1]} data of context {row[0]}, "
                                      f"expected {split} data of {context}")
            meta[split] = row[2]
        if meta["train"] != meta["validation"]:
            raise ValidationError("Train and validation datasets have different population counts")
        rule_version = rules.latest(db)
        rule = rules.get(db, rule_version)
        x, y = datasets.load(db, train)
        val = datasets.load(db, validation)
        fitted = learner.fit(x, y, seed=train)
        packed = learner.pack(fitted["dense"], val, rule, sparse=fitted["sparse"])
        sparse_support = fitted["support"]
        packed_support = decode(packed["blob"]) != 0
        edges = int(sparse_support.sum())
        # The error of a graph with no edges on the same validation data: a snapshot that does not beat it has
        # learned nothing (the dense learner stops fitting when there are many populations; the sparse one does not).
        no_graph = float(np.mean(val[1] ** 2))
        metrics = {
            "train_dataset": train, "validation_dataset": validation, "train_steps": int(len(x)),
            "learner": {"window": learner.WINDOW, "learning_rate": learner.LEARNING_RATE, "k": 3,
                        "initial_support_seed": train},
            "validation_mse": packed["validation_mse"],
            "dense_validation_mse": packed["dense_validation_mse"],
            "no_graph_validation_mse": no_graph,
            "beats_no_graph": bool(packed["validation_mse"] < no_graph),
            "pack": {key: packed[key] for key in ("bits", "degree", "byte_size", "validation_mse",
                                                  "dense_validation_mse", "candidates", "learner", "reference",
                                                  "reference_validation_mse") if key in packed},
            "sparse": {"swaps": fitted["swaps"], "edges": edges,
                       "validation_mse": learner.mse(fitted["sparse"], *val),
                       "support_agreement_with_snapshot":
                           float((sparse_support & packed_support).sum() / edges) if edges else None},
        }
        with store.write(db):
            _require_lease(db, context, holder)
            for dataset_id in (train, validation):
                if db.execute("SELECT deleted_at IS NOT NULL OR blob IS NULL FROM datasets WHERE id=?",
                              (dataset_id,)).fetchone()[0]:
                    raise Gone(f"Dataset {dataset_id} was deleted while the candidate was being learned")
            revision = _insert_revision(db, context=context, parent_id=_active(db, context), status="candidate",
                                        blob=packed["blob"], rule_version=rule_version, origin="learner",
                                        metrics=metrics, now=now)
            db.executemany("INSERT INTO graph_lineage(revision_id,dataset_id,role) VALUES(?,?,?)",
                           [(revision, train, "train"), (revision, validation, "validation")])
            store.bump(db, "replay_updates")
            store.bump_state_version(db)
    return revision


# ---- scoring and promotion ----------------------------------------------------------------------------

def evaluate(db, revision_id, now):
    """Score a revision on independent held-out data of its context and store the result in metrics."""
    rev = _row(db, revision_id)
    eligible, current, older, excluded = _heldout(db, rev)
    scores = _scores(db, _matrix(db, revision_id), eligible)
    heldout_mse = float(np.mean(list(scores.values()))) if scores else None
    evaluation = {"heldout": [{"dataset_id": d, "mse": scores[d]} for d in eligible],
                  "current": current, "older": older, "excluded": excluded,
                  "heldout_mse": heldout_mse, "evaluated_at": now}
    with store.write(db):
        metrics = _row(db, revision_id)["metrics"]
        metrics["evaluation"] = evaluation
        metrics["heldout_mse"] = heldout_mse
        db.execute("UPDATE graph_revisions SET metrics=? WHERE id=?", (_dump(metrics), revision_id))
        store.bump(db, "replay_updates")
        store.bump_state_version(db)
    return {"revision_id": revision_id, "context": rev["context"], "status": rev["status"], **evaluation}


def _gate(db, cand, rule, now):
    """Apply checks (a)-(e) of the candidate's own rule and (f), which no rule can waive, and return the full
    decision record."""
    incumbent = _active(db, cand["context"])
    eligible, current, older, excluded = _heldout(db, cand)
    cand_matrix = _matrix(db, cand["id"])
    inc_matrix = _matrix(db, incumbent) if incumbent is not None else None
    checks = {}
    # (a) independent held-out data
    checks["independent_heldout"] = {"passed": bool(eligible), "datasets": eligible, "excluded": excluded}
    # (b) the candidate beats the incumbent on current held-out data by min_heldout_gain
    gain = float(rule["min_heldout_gain"])
    if not eligible:
        checks["heldout_gain"] = {"passed": None, "why": "not evaluated: no independent held-out data"}
    else:
        cand_scores = _scores(db, cand_matrix, current)
        cand_mse = float(np.mean(list(cand_scores.values())))
        check = {"datasets": current, "candidate_mse": cand_mse, "min_heldout_gain": gain,
                 "candidate_per_dataset": [{"dataset_id": d, "mse": cand_scores[d]} for d in current]}
        if inc_matrix is None:
            check.update(passed=True, incumbent_mse=None, threshold=None, why="no incumbent")
        elif inc_matrix.shape != cand_matrix.shape:
            check.update(passed=False, incumbent_mse=None, threshold=None,
                         why=f"incumbent shape {list(inc_matrix.shape)} differs from the candidate")
        else:
            inc_scores = _scores(db, inc_matrix, current)
            inc_mse = float(np.mean(list(inc_scores.values())))
            threshold = inc_mse * (1 - gain)
            check.update(passed=cand_mse < threshold, incumbent_mse=inc_mse, threshold=threshold,
                         incumbent_per_dataset=[{"dataset_id": d, "mse": inc_scores[d]} for d in current])
        checks["heldout_gain"] = check
    # (c) retention on every older held-out dataset
    tolerance = float(rule["retention_tolerance"])
    if not eligible:
        checks["retention"] = {"passed": None, "why": "not evaluated: no independent held-out data"}
    elif inc_matrix is None or inc_matrix.shape != cand_matrix.shape or not older:
        checks["retention"] = {"passed": True, "retention_tolerance": tolerance, "datasets": [],
                               "why": "no incumbent" if inc_matrix is None else
                               "no comparable incumbent" if inc_matrix.shape != cand_matrix.shape else
                               "no older held-out data"}
    else:
        cand_old = _scores(db, cand_matrix, older)
        inc_old = _scores(db, inc_matrix, older)
        rows = [{"dataset_id": d, "candidate_mse": cand_old[d], "incumbent_mse": inc_old[d],
                 "limit": inc_old[d] * (1 + tolerance), "passed": cand_old[d] <= inc_old[d] * (1 + tolerance)}
                for d in older]
        checks["retention"] = {"passed": all(r["passed"] for r in rows), "retention_tolerance": tolerance,
                               "datasets": rows}
    # (d) resource budget
    checks["byte_budget"] = {"passed": cand["byte_size"] <= int(rule["byte_budget"]),
                             "byte_size": cand["byte_size"], "byte_budget": int(rule["byte_budget"])}
    # (e) integrity: nothing the candidate was learned from has been deleted
    lineage = _lineage(db, cand["id"])
    deleted = [dataset_id for dataset_id, _, _, deleted_at in lineage if deleted_at is not None]
    checks["lineage_intact"] = {"passed": not deleted, "datasets": [row[0] for row in lineage], "deleted": deleted}
    # (f) the snapshot is better than no graph. A graph that predicts worse than one with no edges misleads everything
    # that reads it (recall, the panel match of beliefs), so no rule can let it through. The dense learner produces
    # such a graph when there are many populations (learner.py).
    recorded = cand["metrics"]
    if isinstance(recorded.get("beats_no_graph"), bool):
        ours, none = recorded.get("validation_mse"), recorded.get("no_graph_validation_mse")
        check = {"passed": recorded["beats_no_graph"], "validation_mse": ours, "no_graph_validation_mse": none}
        if not check["passed"]:
            shown = [f"{value:.6g}" if isinstance(value, float) else "(not recorded)" for value in (ours, none)]
            check["why"] = (f"the snapshot's validation error {shown[0]} is not below {shown[1]}, the error of a graph "
                            "with no edges on the same validation data: it has learned nothing that helps")
        checks["beats_no_graph"] = check
    order = [name for name in CHECKS if name in checks]
    promoted = all(checks[name]["passed"] is True for name in order)
    decision = {
        "action": "promote" if promoted else "reject", "promoted": promoted, "decided_at": now,
        "context": cand["context"], "candidate": cand["id"], "incumbent": incumbent,
        "rule_version": cand["rule_version"], "rule": rule,
        "thresholds": {"min_heldout_gain": gain, "retention_tolerance": tolerance,
                       "byte_budget": int(rule["byte_budget"]),
                       "validation_tolerance": float(rule["validation_tolerance"])},
        "heldout": {"current": current, "older": older},
        "order": order, "checks": checks,
        "passed": [name for name in order if checks[name]["passed"] is True],
        "failed": [name for name in order if checks[name]["passed"] is not True],
    }
    if checks["heldout_gain"]["passed"] is True and checks["retention"]["passed"] is False:
        decision["recommendation"] = NEW_CONTEXT_ADVICE
    return decision


def no_graph_check(db, rev):
    """Check (f) for a revision already decided: {"passed": True|False|None, ...}. What learn() recorded when it did
    (metrics.beats_no_graph); otherwise, for a learned revision whose validation dataset is still stored, the same
    comparison computed now from its snapshot (`computed`: true); otherwise None with `why` (a founding or imported
    graph, or a revision whose validation data is gone, cannot be judged). Reads only."""
    metrics = rev["metrics"]
    if isinstance(metrics.get("beats_no_graph"), bool):
        return {"passed": metrics["beats_no_graph"], "validation_mse": metrics.get("validation_mse"),
                "no_graph_validation_mse": metrics.get("no_graph_validation_mse"), "computed": False}
    if rev["origin"] != "learner":
        return {"passed": None, "why": f"a {rev['origin']} graph has no validation data of its own"}
    dataset_id = metrics.get("validation_dataset")
    row = db.execute("SELECT cols, deleted_at IS NULL AND blob IS NOT NULL FROM datasets WHERE id=?",
                     (dataset_id,)).fetchone() if isinstance(dataset_id, int) else None
    if row is None or not row[1]:
        return {"passed": None, "why": "its validation dataset is no longer stored"}
    if row[0] != rev["cols"] or rev["rows"] != rev["cols"]:
        return {"passed": None, "why": f"its validation dataset has {row[0]} populations, the graph is "
                                       f"{rev['rows']}x{rev['cols']}"}
    states, targets = datasets.load(db, dataset_id)
    ours = float(learner.mse(_matrix(db, rev["id"]), states, targets))
    none = float(np.mean(targets ** 2))
    return {"passed": bool(ours < none), "validation_mse": ours, "no_graph_validation_mse": none,
            "validation_dataset": dataset_id, "computed": True}


def promote(db, revision_id, now):
    """Run the gate of the candidate's recorded rule; promote on a full pass, otherwise reject.

    Scoring runs under the context lease but outside the write transaction, so readers and evidence
    writers are not blocked while held-out data is scored. The committing transaction re-checks that the
    candidate is still a candidate and that the incumbent and the lineage are what was scored; otherwise
    Conflict (nothing is written, the candidate can be promoted again).
    """
    rev = _row(db, revision_id)
    if rev["status"] != "candidate":
        raise Conflict(f"Revision {revision_id} is {rev['status']}; only candidates can be promoted")
    holder = _holder("promote")
    with lease(db, rev["context"], holder, now):
        cand = _row(db, revision_id)
        if cand["status"] != "candidate":
            raise Conflict(f"Revision {revision_id} is {cand['status']}; only candidates can be promoted")
        decision = _gate(db, cand, rules.get(db, cand["rule_version"]), now)
        with store.write(db):
            _require_lease(db, rev["context"], holder)
            status = db.execute("SELECT status FROM graph_revisions WHERE id=?", (revision_id,)).fetchone()[0]
            if status != "candidate":
                raise Conflict(f"Revision {revision_id} became {status} while it was scored")
            if _active(db, cand["context"]) != decision["incumbent"]:
                raise Conflict(f"The active graph of {cand['context']} changed while revision {revision_id} was "
                               "scored; promote again")
            lineage = [[d, deleted is not None] for d, _, _, deleted in _lineage(db, revision_id)]
            scored = [[d, d in decision["checks"]["lineage_intact"]["deleted"]]
                      for d in decision["checks"]["lineage_intact"]["datasets"]]
            if lineage != scored:
                raise Conflict(f"The lineage of revision {revision_id} changed while it was scored; promote again")
            if decision["promoted"]:
                if decision["incumbent"] is not None:
                    db.execute("UPDATE graph_revisions SET status='archived' WHERE id=?", (decision["incumbent"],))
                status = "active"
            else:
                status = "rejected"
            db.execute("UPDATE graph_revisions SET status=?, decision=?, decided_at=? WHERE id=?",
                       (status, _dump(decision), now, revision_id))
            store.bump(db, "replay_updates")
            store.bump_state_version(db)
    return {"promoted": decision["promoted"], "decision": decision}


def reject(db, revision_id, now, reason):
    """Owner rejection of a candidate."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("A rejection needs a reason")
    with store.write(db):
        rev = _row(db, revision_id)
        if rev["status"] != "candidate":
            raise Conflict(f"Revision {revision_id} is {rev['status']}; only candidates can be rejected")
        decision = {"action": "reject", "promoted": False, "by": "owner", "reason": reason, "decided_at": now,
                    "rule_version": rev["rule_version"]}
        db.execute("UPDATE graph_revisions SET status='rejected', decision=?, decided_at=? WHERE id=?",
                   (_dump(decision), now, revision_id))
        store.bump_state_version(db)
    return {"revision_id": revision_id, "status": "rejected", "decision": decision}


def rollback(db, context, revision_id, now):
    """Re-activate an archived, non-invalidated revision whose lineage is intact; archive the active one."""
    datasets._check_context(context)
    holder = _holder("rollback")
    with lease(db, context, holder, now):
        with store.write(db):
            _require_lease(db, context, holder)
            rev = _row(db, revision_id)
            if rev["context"] != context:
                raise ValidationError(f"Revision {revision_id} belongs to context {rev['context']}, not {context}")
            if rev["status"] != "archived":
                raise Conflict(f"Revision {revision_id} is {rev['status']}; only archived revisions can be "
                               "re-activated")
            if not _lineage_intact(db, revision_id):
                raise Conflict(f"Revision {revision_id} was learned from a deleted dataset")
            previous = _active(db, context)
            if previous is not None:
                # The owner rolled back FROM this revision: record it so a later cascade does not revive it.
                db.execute("UPDATE graph_revisions SET status='archived', decision=?, decided_at=? WHERE id=?",
                           (_dump({"action": REPLACED_BY_ROLLBACK, "by": revision_id, "decided_at": now,
                                   "prior": _row(db, previous)["decision"]}), now, previous))
            decision = {"action": "rollback", "decided_at": now, "replaced": previous, "prior": rev["decision"]}
            db.execute("UPDATE graph_revisions SET status='active', decision=?, decided_at=? WHERE id=?",
                       (_dump(decision), now, revision_id))
            store.bump_state_version(db)
    return {"context": context, "active": revision_id, "archived": previous}


def invalidate_dependents(db, dataset_id, now):
    """Invalidate revisions trained or validated on a deleted dataset. Caller owns the transaction.

    Does not bump state_version: the caller's deletion is the one logical change. When the active revision
    of a context is invalidated, the newest archived revision of that context whose lineage has no deleted
    dataset and that the owner did not roll back from (its latest decision is not 'replaced_by_rollback')
    is re-activated; if there is none, `failure` names the context.
    """
    revs = db.execute("SELECT DISTINCT r.id, r.context, r.status, r.decision FROM graph_lineage l "
                      "JOIN graph_revisions r ON r.id=l.revision_id WHERE l.dataset_id=? "
                      "AND l.role IN ('train','validation') AND r.status!='invalidated' ORDER BY r.id",
                      (dataset_id,)).fetchall()
    invalidated, contexts = [], []
    for revision, context, status, decision in revs:
        prior = json.loads(decision) if decision else None
        db.execute("UPDATE graph_revisions SET status='invalidated', decision=?, decided_at=? WHERE id=?",
                   (_dump({"action": "invalidate", "dataset": dataset_id, "was": status, "decided_at": now,
                           "prior": prior}), now, revision))
        invalidated.append(revision)
        if status == "active":
            contexts.append(context)
    reactivated, failures = None, []
    for context in contexts:
        candidates = [r[0] for r in db.execute(
            "SELECT id, decision FROM graph_revisions WHERE context=? AND status='archived' "
            "ORDER BY created_at DESC, id DESC", (context,))
            if (json.loads(r[1]) if r[1] else {}).get("action") not in NOT_REVIVED]
        chosen = next((r for r in candidates if _lineage_intact(db, r)), None)
        if chosen is None:
            failures.append(f"no eligible snapshot for context {context}")
            continue
        prior = _row(db, chosen)["decision"]
        db.execute("UPDATE graph_revisions SET status='active', decision=?, decided_at=? WHERE id=?",
                   (_dump({"action": "reactivate", "after_deleting_dataset": dataset_id, "decided_at": now,
                           "prior": prior}), now, chosen))
        reactivated = chosen
    return {"invalidated": invalidated, "reactivated": reactivated,
            "failure": "; ".join(failures) if failures else None}


def erase_learned_from(db, dataset_id, now):
    """Erase the weights and metrics of every invalidated revision trained or validated on a deleted dataset (also
    one invalidated earlier by another forget), and the graph effects measured on those revisions: what was learned
    from data the owner forgot goes with it (ADR-048). Caller owns the transaction; call after invalidate_dependents.

    The revision stays listed: id, context, status 'invalidated', shape, sha256, lineage and decisions (the audit
    of what was promoted and why). metrics becomes {"erased": {"dataset": id, "at": now}}. The schema trigger allows
    exactly this update of an invalidated revision. Returns {"erased": [ids], "effects_deleted": n}."""
    ids = [r for (r,) in db.execute(
        "SELECT DISTINCT r.id FROM graph_lineage l JOIN graph_revisions r ON r.id=l.revision_id "
        "WHERE l.dataset_id=? AND l.role IN ('train','validation') AND r.status='invalidated' AND r.blob!=? "
        "ORDER BY r.id", (dataset_id, ERASED))]
    effects = 0
    for revision in ids:
        db.execute("UPDATE graph_revisions SET blob=?, metrics=? WHERE id=?",
                   (ERASED, _dump({"erased": {"dataset": dataset_id, "at": now}}), revision))
        effects += db.execute("DELETE FROM graph_effects WHERE revision_id=?", (revision,)).rowcount
    return {"erased": ids, "effects_deleted": effects}


# ---- inspection ---------------------------------------------------------------------------------------

def _block_se(per_step, block=LESION_BLOCK):
    """Standard error of the mean of a per-step series from non-overlapping time blocks (autocorrelation-robust)."""
    blocks = len(per_step) // block
    if blocks < 2:
        return None
    means = per_step[:blocks * block].reshape(blocks, block).mean(axis=1)
    return float(means.std(ddof=1) / np.sqrt(blocks))


def _dataset_context(db, dataset_id):
    row = db.execute("SELECT context FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if row is None:
        raise NotFound(f"Unknown dataset {dataset_id}")
    return row[0]


def lesion(db, revision_id, dataset_id, now):
    """Zero each retained edge in turn and measure the change in error on one dataset.

    Each edge also gets a time-block standard error ("se"): the per-step error change is averaged over
    non-overlapping blocks of 32 steps and se = sd(block means) / sqrt(#blocks), which respects the
    autocorrelation of a trajectory (None with fewer than two blocks). "task" is the dataset's context.
    Also scores a permuted control that keeps each receiver's weights but scrambles their sources
    (as lab.graph_bench does), seeded by (revision_id, dataset_id). Rows go to graph_effects.
    """
    rev = _row(db, revision_id)
    matrix = _matrix(db, revision_id).astype(np.float64)
    states, targets = datasets.load(db, dataset_id)
    task = _dataset_context(db, dataset_id)
    if matrix.shape != (states.shape[1],) * 2:
        raise ValidationError(f"Dataset {dataset_id} has {states.shape[1]} populations, graph is "
                              f"{matrix.shape[0]}x{matrix.shape[1]}")
    base = learner.mse(matrix, states, targets)
    residual = states @ matrix.T - targets
    n = matrix.shape[1]
    edges = []
    for dst, src in zip(*np.nonzero(matrix)):
        weight = matrix[dst, src]
        # Removing the edge shifts receiver dst's error by -weight * x_src at every step and leaves every other
        # receiver unchanged, so the change in MSE is exactly the mean of per_step (no full recomputation per edge).
        per_step = ((residual[:, dst] - weight * states[:, src]) ** 2 - residual[:, dst] ** 2) / n
        edges.append({"src": int(src), "dst": int(dst), "weight": float(weight),
                      "delta_mse": float(per_step.mean()), "se": _block_se(per_step)})
    edges.sort(key=lambda e: -e["delta_mse"])
    rng = np.random.default_rng([revision_id, dataset_id])
    shuffled = np.zeros_like(matrix)
    for j in range(n):
        pool = np.delete(np.arange(n), j)
        shuffled[j, pool] = rng.permutation(matrix[j, pool])
    control = learner.mse(shuffled, states, targets)
    with store.write(db):
        db.executemany("INSERT INTO graph_effects(revision_id,dataset_id,kind,src,dst,effect,detail,created_at) "
                       "VALUES(?,?,'lesion',?,?,?,?,?)",
                       [(revision_id, dataset_id, e["src"], e["dst"], e["delta_mse"],
                         _dump({"weight": e["weight"], "base_mse": base, "se": e["se"], "block": LESION_BLOCK,
                                "task": task}), now) for e in edges])
        db.execute("INSERT INTO graph_effects(revision_id,dataset_id,kind,src,dst,effect,detail,created_at) "
                   "VALUES(?,?,'lesion_control',NULL,NULL,?,?,?)",
                   (revision_id, dataset_id, control,
                    _dump({"base_mse": base, "control": "permuted sources", "task": task}), now))
        store.bump(db, "replay_updates")
        store.bump_state_version(db)
    return {"revision_id": revision_id, "dataset_id": dataset_id, "context": rev["context"], "task": task,
            "edges": edges, "permuted_control_mse": control, "base_mse": base, "se_block_steps": LESION_BLOCK,
            "se_blocks": len(states) // LESION_BLOCK}


def functional_map(db, dataset_id, now, *, lags=COACTIVATION_LAGS, top=20):
    """Log which populations coactivate, in what context and with what lag, and propose routes from it.

    For each lag L the Pearson correlation of population i at t with population j at t+L is computed over
    the dataset's states (n = T - L pairs). Each ordered pair (i, j), i != j, is stored once in
    graph_effects (kind 'coactivation', src=i, dst=j, effect = the correlation at its strongest lag, detail
    {lag, context, n, by_lag}), attributed to the context's active revision. The result proposes the `top`
    pairs by |correlation| that are NOT edges of that active graph.

    Coactivation is not connection: a common stimulus or a shared upstream population can make two
    disconnected populations coactivate, so a proposed route is a hypothesis for learn/lesion to test.
    Raises NotFound when the dataset's context has no active graph.
    """
    if isinstance(top, bool) or not isinstance(top, int) or top < 1:
        raise ValidationError("top must be a positive integer")
    lags = tuple(lags)
    if not lags or any(isinstance(lag, bool) or not isinstance(lag, int) or lag < 1 for lag in lags):
        raise ValidationError("lags must be positive integers")
    states, _ = datasets.load(db, dataset_id)
    context = _dataset_context(db, dataset_id)
    revision = _active(db, context)
    if revision is None:
        raise NotFound(f"Context {context} has no active graph to attribute a functional map to")
    matrix = _matrix(db, revision)
    t, n = states.shape
    if matrix.shape != (n, n):
        raise ValidationError(f"Dataset {dataset_id} has {n} populations, the active graph of {context} is "
                              f"{matrix.shape[0]}x{matrix.shape[1]}")
    if max(lags) > t - 3:
        raise ValidationError(f"lags must leave at least 3 time steps (dataset has {t})")
    by_lag = {}
    for lag in lags:
        x, y = states[:-lag], states[lag:]
        x, y = x - x.mean(axis=0), y - y.mean(axis=0)
        scale = np.outer(np.sqrt((x * x).sum(axis=0)), np.sqrt((y * y).sum(axis=0)))
        corr = (x.T @ y) / np.where(scale > 0, scale, 1)     # [i, j]; a constant population correlates 0
        corr[scale == 0] = 0
        by_lag[lag] = corr
    pairs = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            values = {lag: float(by_lag[lag][i, j]) for lag in lags}
            lag = max(lags, key=lambda k: (abs(values[k]), -k))
            pairs.append({"src": i, "dst": j, "lag": lag, "correlation": values[lag], "n": t - lag,
                          "by_lag": {str(k): v for k, v in values.items()}, "in_graph": bool(matrix[j, i] != 0)})
    with store.write(db):
        db.executemany("INSERT INTO graph_effects(revision_id,dataset_id,kind,src,dst,effect,detail,created_at) "
                       "VALUES(?,?,'coactivation',?,?,?,?,?)",
                       [(revision, dataset_id, p["src"], p["dst"], p["correlation"],
                         _dump({"lag": p["lag"], "context": context, "n": p["n"], "by_lag": p["by_lag"]}), now)
                        for p in pairs])
        store.bump(db, "replay_updates")
        store.bump_state_version(db)
    proposed = sorted((p for p in pairs if not p["in_graph"]), key=lambda p: (-abs(p["correlation"]), p["src"], p["dst"]))
    return {"dataset_id": dataset_id, "context": context, "revision_id": revision, "lags": list(lags),
            "pairs": len(pairs), "edges_in_graph": int((matrix != 0).sum()),
            "routes": [{key: p[key] for key in ("src", "dst", "lag", "correlation", "n")} for p in proposed[:top]],
            "caveat": "coactivation can come from a common stimulus; test a route with learn and lesion"}


def _public(rev):
    return {key: rev[key] for key in COLUMNS}


def listing(db, context=None):
    sql = f"SELECT id FROM graph_revisions{' WHERE context=?' if context is not None else ''} ORDER BY id"
    return [_public(_row(db, r[0])) for r in db.execute(sql, (context,) if context is not None else ())]


def show(db, revision_id):
    """Revision record, lineage, the decoded matrix (rounded to 6 decimals) and its support mask. An erased revision
    (erase_learned_from) has no matrix: `erased` says why, and matrix and support are empty."""
    rev = _public(_row(db, revision_id))
    rev["lineage"] = [{"dataset_id": d, "role": role, "deleted": deleted is not None}
                      for d, role, _, deleted in _lineage(db, revision_id)]
    try:
        matrix = _matrix(db, revision_id).astype(np.float64)
    except Gone as gone:
        return rev | {"erased": str(gone), "matrix": [], "support": [], "edges": 0}
    rev["matrix"] = np.round(matrix, 6).tolist()
    rev["support"] = (matrix != 0).tolist()
    rev["edges"] = int((matrix != 0).sum())
    return rev


def export(db, revision_id, out_path):
    """Write the revision's NSG1 bytes to a new file (mode 0600). Refuses to overwrite or to follow a symlink."""
    rev = _row(db, revision_id)
    blob = db.execute("SELECT blob FROM graph_revisions WHERE id=?", (revision_id,)).fetchone()[0]
    if bytes(blob) == ERASED:
        raise Gone(_erased_text(revision_id))
    try:
        fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Conflict(f"{out_path} already exists; export never overwrites") from None
    except OSError as error:
        raise ValidationError(f"Cannot create {out_path}: {error.strerror}") from None
    with os.fdopen(fd, "wb") as out:
        out.write(blob)
    return {"revision_id": revision_id, "path": str(out_path), "bytes": len(blob), "sha256": rev["sha256"]}
