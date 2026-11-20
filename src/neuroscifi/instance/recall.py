"""History-based recall: pick which context's active graph explains a stored trajectory.

Every step, each competing graph predicts the next target before the outcome is revealed
(lab.context_recall.GraphRecall); the graph with the lowest summed error over the last
rule.recall_history steps is the choice. No context label enters the selector. A step is 'unknown'
when the chosen graph's rolling mean error exceeds rule.unknown_context_ratio times its validation MSE;
when more than half the steps are unknown the verdict is 'unknown_context'. A chosen graph without a
validation MSE (a founding or imported graph) cannot vouch for a step, so that step counts as unknown.
Recall never creates a graph: an unknown verdict only records an open question for the owner.
"""
import json

import numpy as np

from ..lab.context_recall import GraphRecall
from . import datasets, rules, store
from .errors import NotFound, ValidationError

UNKNOWN_MAJORITY = 0.5


def run(db, dataset_id, now, *, contexts=None, record=True):
    """Replay a dataset through the active graphs. record=False is read-only (no counters, no pending)."""
    states, targets = datasets.load(db, dataset_id)
    n = states.shape[1]
    rule_version = rules.latest(db)
    rule = rules.get(db, rule_version)
    rows = db.execute("SELECT id, context, rows, cols, blob, metrics FROM graph_revisions "
                      "WHERE status='active' ORDER BY created_at, id").fetchall()
    if contexts is not None:
        contexts = list(contexts)
        missing = sorted(set(contexts) - {r[1] for r in rows})
        if missing:
            raise NotFound(f"No active graph for context {', '.join(missing)}")
        rows = [r for r in rows if r[1] in contexts]
    rows = [r for r in rows if r[2] == r[3] == n]
    if not rows:
        raise ValidationError(f"No active graph has {n} populations to compete on dataset {dataset_id}")
    names = [r[1] for r in rows]
    validation = [json.loads(r[5] or "{}").get("validation_mse") for r in rows]
    ratio = float(rule["unknown_context_ratio"])
    selector = GraphRecall([r[4] for r in rows], history=int(rule["recall_history"]))
    selected = latest = 0.0
    unknown = 0
    choices = []
    for t, (x, y) in enumerate(zip(states, targets)):
        chosen, prediction = selector.predict(x)          # committed before the outcome is seen
        error = np.mean((prediction - y) ** 2, axis=1)
        selected += error[chosen]
        latest += error[-1]
        selector.observe(prediction, y)
        rolling = float(np.mean(np.asarray(selector.errors)[:, chosen]))
        if validation[chosen] is None or rolling > ratio * validation[chosen]:
            unknown += 1
        if choices and choices[-1]["context"] == names[chosen]:
            choices[-1]["to"] = t + 1
        else:
            choices.append({"context": names[chosen], "revision_id": rows[chosen][0], "from": t, "to": t + 1})
    steps = len(states)
    unknown_fraction = unknown / steps
    share = {name: 0 for name in names}
    for run_ in choices:
        share[run_["context"]] += run_["to"] - run_["from"]
    if unknown_fraction > UNKNOWN_MAJORITY:
        verdict = "unknown_context"
    else:
        verdict = "known:" + max(names, key=lambda name: share[name])
    result = {
        "dataset_id": dataset_id, "steps": steps, "rule_version": rule_version,
        "history": int(rule["recall_history"]), "unknown_context_ratio": ratio,
        "selected_mse": float(selected / steps), "latest_only_mse": float(latest / steps),
        "choices": choices, "context_share": {name: share[name] / steps for name in names},
        "competitors": [{"context": r[1], "revision_id": r[0], "validation_mse": v} for r, v in zip(rows, validation)],
        "unknown_fraction": unknown_fraction, "verdict": verdict, "pending_id": None,
    }
    if record:
        with store.write(db):
            if verdict == "unknown_context":
                context = db.execute("SELECT context FROM datasets WHERE id=?", (dataset_id,)).fetchone()[0]
                question = (f"Dataset {dataset_id} ({steps} steps) matches no known context: on "
                            f"{unknown_fraction:.0%} of steps the best active graph's rolling error exceeded "
                            f"{ratio:g}x its validation error (selected MSE {result['selected_mse']:.4g}; "
                            f"competitors {', '.join(names)}). Register it as a new context?")
                existing = db.execute("SELECT id FROM pending WHERE kind='new_context' AND scope='context:unknown' "
                                      "AND resolved_at IS NULL AND question=?", (question,)).fetchone()
                if existing:
                    result["pending_id"] = existing[0]
                else:
                    result["pending_id"] = db.execute(
                        "INSERT INTO pending(epoch,context,kind,scope,question,created_at) "
                        "VALUES(NULL,?,'new_context','context:unknown',?,?)", (context, question, now)).lastrowid
            store.bump(db, "replay_updates")
            store.bump_state_version(db)
    return result
