"""The salience record: one logged, explainable score per autonomy candidate, with the value of every signal it was
made of (Project Cognitive Genesis, lane I; ADR-051).

    salience = sum over signals of WEIGHTS[signal] x value        (each value in [0, 1]; the weights sum to 1)

Signals, each read from the records (never from a model):
  novelty                 1 when the subject was first seen within NOVELTY_WINDOW, or an owner monitor found it
                          new or changed (autonomy's source_change signal); else 0
  contradiction           1 when its sources disagree (a contested item or an open disagreement); else 0
  staleness               1 when the stored version is stale (curiosity's stale candidates); else 0
  confidence_shortfall    1 - the confidence the individual holds in it (knowledge confidence, or the stated forecast
                          of a hypothesis; 0.5 when nothing is held)
  owner_importance        1 for the owner's own records; else the overlap of its words with the owner's goals
                          (autonomy's goal signal)
  potential_consequence   what depends on it: 1 for knowledge the owner accepted or an owner monitor produced, 0.5
                          for provisional knowledge, 0 for anything not held as knowledge
  prediction_discrepancy  1 when a prediction about it failed within ERROR_WINDOW; else the shortfall of its recent
                          looks against the gain expected of them (1 - the learning-progress factor, when known)

Display and record only: autonomy.candidates attaches it to every candidate and the decision event of a queued
investigation stores it (autonomy.decision_inputs). It never changes what is admitted or the order: the owner's
records, permissions, budgets, `quiet` and min_gain decide exactly as before. Off: `nsf web config --set
salience_record=false`."""
from __future__ import annotations

from . import state

WEIGHTS = {"novelty": 0.15, "contradiction": 0.20, "staleness": 0.15, "confidence_shortfall": 0.15,
           "owner_importance": 0.15, "potential_consequence": 0.10, "prediction_discrepancy": 0.10}
NOVELTY_WINDOW = 7 * 86400.0
ERROR_WINDOW = 30 * 86400.0
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def enabled(db) -> bool:
    return getattr(state.get_config(db), "salience_record", True) is True


def _clip(value) -> float:
    return round(min(1.0, max(0.0, float(value))), 6)


def score(db, item: dict, now: float) -> dict:
    """{"score", "signals": {name: value}, "weights", "explanation"} for one autonomy candidate (see the module doc)."""
    signals = item.get("signals") or {}
    key = item.get("key")
    first = db.execute("SELECT min(created_at) FROM web_claims WHERE key=?", (key,)).fetchone()[0]
    novelty = 1.0 if signals.get("source_change") or (first is not None and now - first <= NOVELTY_WINDOW) else 0.0
    status = confidence = None
    if item.get("knowledge_id") is not None:
        row = db.execute("SELECT status, confidence FROM web_knowledge WHERE id=?", (item["knowledge_id"],)).fetchone()
        if row:
            status, confidence = row
    if confidence is None and item.get("forecast") is not None:
        confidence = item["forecast"]
    consequence = (1.0 if status == "accepted" or item.get("owner") else 0.5 if status == "provisional" else 0.0)
    failed = db.execute("SELECT 1 FROM web_predictions p LEFT JOIN web_knowledge k ON k.id=p.knowledge_id LEFT JOIN "
                        "web_hypotheses h ON h.id=p.hypothesis_id WHERE p.outcome='error' AND p.checked_at>=? AND "
                        "(k.key=? OR h.key=?) LIMIT 1", (now - ERROR_WINDOW, key, key)).fetchone() is not None
    lp = (item.get("learning_progress") or {}).get("factor")
    values = {"novelty": novelty, "contradiction": signals.get("contradiction", 0.0),
              "staleness": signals.get("stale", 0.0),
              "confidence_shortfall": 1.0 - state.finite(confidence, 0.5),
              "owner_importance": 1.0 if item.get("owner") else signals.get("goal", 0.0),
              "potential_consequence": consequence,
              "prediction_discrepancy": 1.0 if failed else (1.0 - lp if lp is not None else 0.0)}
    values = {k: _clip(v) for k, v in values.items()}
    total = round(sum(WEIGHTS[k] * v for k, v in values.items()), 6)
    parts = sorted(((WEIGHTS[k] * v, k) for k, v in values.items() if v), reverse=True)
    explanation = ("salience {:.3f} = ".format(total) +
                   (" + ".join(f"{WEIGHTS[k]:g} x {k} {values[k]:.2f}" for _, k in parts) if parts else "no signal"))
    return {"score": total, "signals": values, "weights": dict(WEIGHTS), "explanation": explanation}


__all__ = ["ERROR_WINDOW", "NOVELTY_WINDOW", "WEIGHTS", "enabled", "score"]
