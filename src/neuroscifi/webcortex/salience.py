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
salience_record=false`.

ADR-056 (Wave 2 lane L), off by default: with salience_attention on, order() decides which of the candidates
autonomy.admission already admitted (idle and timer paths) is taken first. Admission itself is unchanged: the same
candidates are admitted and refused, with the same reasons, and the caps, budgets, cooldown and quiet are read as
before; nothing raises its own priority, budget or frequency. The weights stay the fixed ones above (nothing is
learned or rewarded)."""
from __future__ import annotations

import math

from . import state

WEIGHTS = {"novelty": 0.15, "contradiction": 0.20, "staleness": 0.15, "confidence_shortfall": 0.15,
           "owner_importance": 0.15, "potential_consequence": 0.10, "prediction_discrepancy": 0.10}
NOVELTY_WINDOW = 7 * 86400.0
ERROR_WINDOW = 30 * 86400.0
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def enabled(db) -> bool:
    return getattr(state.get_config(db), "salience_record", True) is True


def attention(db) -> bool:
    """The owner's salience_attention setting (ADR-056): the salience record orders the candidates admission already
    admitted. It needs the record itself (salience_record)."""
    cfg = state.get_config(db)
    return getattr(cfg, "salience_attention", False) is True and getattr(cfg, "salience_record", True) is True


def order(chosen: list) -> tuple[list, dict]:
    """(the admitted candidates in the order they are taken, the record of that order) with salience_attention on:
    the owner's own records first, as before; then the higher salience score first, then the higher priority, then
    the key. Never adds or removes a candidate. Each candidate keeps `priority_rank`, its place in admission's own
    (priority) order, so the decision event says whether the priority order would have taken another first; the
    other candidate is named by its place, never by its subject (a forgotten subject leaves nothing in another
    task's record).
    The priority order stands, recorded as such, unless EVERY candidate carries a finite numeric score (Wave 2 lane
    Y, M2): a record that could not be made ({"error": ...}), a missing, string, boolean or NaN score is never ordered
    "by salience"."""
    if not chosen or any(_score(c) is None for c in chosen):
        return chosen, {"by": "priority", "why": "a candidate carries no finite salience score"}
    ranked = [c | {"priority_rank": i} for i, c in enumerate(chosen)]
    ranked.sort(key=lambda c: (not c.get("owner"), -_score(c), -float(c.get("priority") or 0.0), str(c.get("key"))))
    return ranked, {"by": "salience", "priority_rank_of_first": ranked[0]["priority_rank"]}


def _score(candidate) -> float | None:
    """The candidate's salience score when it is a finite number, else None."""
    record = candidate.get("salience")
    value = record.get("score") if isinstance(record, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


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


__all__ = ["ERROR_WINDOW", "NOVELTY_WINDOW", "WEIGHTS", "attention", "enabled", "order", "score"]
