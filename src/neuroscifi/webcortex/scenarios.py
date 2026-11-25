"""Scenario records for the Web Cortex's predictions (Cognitive Genesis lane M, ADR-052): imagine -> predict -> act ->
observe -> compare -> learn, kept entirely in the imagined store.

For each open prediction (a hypothesis's testable prediction or a knowledge item's "still holds at the next
refresh") a template writes ONE record of kind 'simulation' to the imagined store (instance/imagined.py), derived from
the prediction and the hypothesis or knowledge item it concerns. It holds three candidate futures with stated
probabilities that sum to 1:

  confirmed     decisive evidence arrives in time and agrees            P = d * q
  error         decisive evidence arrives in time and disagrees         P = d * (1 - q)
  inconclusive  no decisive evidence by the deadline (or the refresh finds nothing)   P = 1 - d

q is the prediction's own forecast (as the ADR-046 mapping reads it while `calibrated_forecasts` is on), and d the
share of earlier settled predictions of the same kind that were decisive (Laplace: (decisive + 1) / (settled + 2)).
The assumptions (independence of arrival and truth, the base rate, the forecast) are written with the record. No model
writes anything here: the template is deterministic. A model-written pilot was not built (research.md grades scenario
narration preliminary, and no offline ablation could show a gain; ADR-052).

When the prediction settles, settle() fills the record's outcome slot once with what was observed (naming the
prediction as the evidence) and the comparison: the three-way Brier and log score of the futures, and the plain
forecast's Brier on a decisive outcome. errors() reports them per mechanism, computed on read. Nothing here is ever
read by knowledge, claims, verification, calibration or retrieval (the imagined store's quarantine, ADR-049): the
records inform the owner and measure the template, nothing else.

Behind InstanceConfig.imagine_scenarios (off by default). tick() is the timer's entry point and is held while the
owner's quiet is on (background.py, activity 'tick.scenarios').
"""
from __future__ import annotations

import json
import math

from .. import provenance
from ..instance import imagined, store
from ..instance.errors import InstanceError, NotFound, ValidationError
from . import evolution, state

MECHANISM = "scenarios.template"
VERSION = "1"
FUTURES = ("confirmed", "error", "inconclusive")
PER_TICK = 20               # at most this many new records per timer pass
LOG_FLOOR = 1e-12


def enabled(db) -> bool:
    try:
        return bool(store.config(db).imagine_scenarios)
    except (InstanceError, ValueError):
        return False


def _kind(db, prediction_id) -> str | None:
    return evolution.prediction_kind(db, prediction_id)


def decisive_rate(db, kind) -> dict:
    """{"decisive", "settled", "rate"}: how often an earlier settled prediction of this kind was decisive (confirmed
    or error rather than inconclusive), Laplace-smoothed. A forgotten task's settlement does not count."""
    row = db.execute(f"SELECT count(*), coalesce(sum(p.outcome IN ('confirmed','error')), 0) FROM web_predictions p "
                     f"LEFT JOIN web_hypotheses h ON h.id=p.hypothesis_id WHERE p.outcome IS NOT NULL AND "
                     f"{evolution._KIND_SQL}=? AND {evolution._counted('p.')}", (kind,)).fetchone()
    settled, decisive = int(row[0]), int(row[1])
    return {"decisive": decisive, "settled": settled, "rate": (decisive + 1) / (settled + 2)}


def _existing(db, prediction_id) -> str | None:
    row = db.execute("SELECT i.id FROM imagined i JOIN imagined_refs r ON r.record_id=i.id WHERE r.ref_kind="
                     "'prediction' AND r.ref_id=? AND i.mechanism=? ORDER BY i.created_at LIMIT 1",
                     (str(prediction_id), MECHANISM)).fetchone()
    return row[0] if row else None


def futures(db, prediction_id) -> dict:
    """The template's three futures for one prediction, without storing them: {"futures": {name: {"p", "means"}},
    "q", "decisive", "assumptions", "uncertainty", "derived_from"}. NotFound for no such prediction."""
    pred = evolution.prediction(db, prediction_id)
    kind = _kind(db, prediction_id)
    stated = state.finite(pred["probability"])
    if stated is None:
        raise ValidationError(f"prediction {prediction_id} has no forecast probability")
    fit = evolution.recalibrations(db).get(kind) or {}
    q = evolution.recalibrate(stated, fit)
    rate = decisive_rate(db, kind)
    d = rate["rate"]
    out = {"confirmed": {"p": d * q, "means": "decisive evidence arrives in time and agrees with the prediction"},
           "error": {"p": d * (1 - q), "means": "decisive evidence arrives in time and contradicts it"},
           "inconclusive": {"p": 1 - d, "means": "no decisive evidence by the deadline"
                            if pred["hypothesis_id"] else "the refresh finds no evidence either way"}}
    refs = [("prediction", prediction_id)]
    if pred["hypothesis_id"]:
        refs.append(("hypothesis", pred["hypothesis_id"]))
    if pred["knowledge_id"]:
        refs.append(("knowledge", pred["knowledge_id"]))
    mapped = "" if q == stated else f", read by the calibration mapping as {q:.3f}"
    assumptions = [
        f"the statement holds with the prediction's own forecast probability {stated:.3f}{mapped}",
        f"decisive evidence arrives as often as it did for earlier {kind} predictions: {rate['decisive']} of "
        f"{rate['settled']} settled ones were decisive (estimate {d:.3f}, Laplace)",
        "whether decisive evidence arrives does not depend on whether the statement holds",
        "the expectation and its criteria stay as recorded until the prediction settles"]
    uncertainty = (f"the futures' probabilities are estimates: the arrival rate rests on {rate['settled']} earlier "
                   "settlement(s) and the forecast on its own calibration; they describe this template, not the world")
    return {"prediction_id": prediction_id, "kind": kind, "q": q, "stated": stated, "decisive": rate,
            "futures": out, "assumptions": assumptions, "uncertainty": uncertainty, "derived_from": refs,
            "expectation": pred["expectation"]}


def imagine(db, prediction_id, now) -> str:
    """Store the template's futures for one open prediction (kind simulation) and return the record id; the existing
    record when there is one. Refused while imagine_scenarios is off or once the prediction has settled."""
    if not enabled(db):
        raise ValidationError("imagine_scenarios is off (`nsf config --imagine-scenarios` turns it on)")
    pred = evolution.prediction(db, prediction_id)
    if pred["outcome"] is not None:
        raise ValidationError(f"prediction {prediction_id} has settled: there is nothing left to imagine")
    found = _existing(db, prediction_id)
    if found:
        return found
    f = futures(db, prediction_id)
    end = state.finite(pred.get("deadline")) or state.finite(pred.get("check_after"))
    lifetime = None if end is None or end <= now else max(60.0, end - now + 30 * 86400)
    payload = {"prediction_id": prediction_id, "kind": f["kind"], "expectation": f["expectation"],
               "forecast": {"stated": f["stated"], "used": f["q"]}, "decisive": f["decisive"],
               "futures": f["futures"], "template": "three futures: decisive and right, decisive and wrong, none"}
    return imagined.record(db, provenance.SIMULATION, payload=payload, assumptions=f["assumptions"],
                           uncertainty=f["uncertainty"], mechanism=MECHANISM, mechanism_version=VERSION,
                           question=f"what may happen to prediction {prediction_id}: {f['expectation']}"[:2000],
                           now=now, derived_from=f["derived_from"], lifetime=lifetime)


def imagine_open(db, now, *, limit=PER_TICK) -> list[str]:
    """Records for open predictions that have none yet, oldest first, at most `limit`."""
    made = []
    for (pid,) in db.execute("SELECT id FROM web_predictions WHERE outcome IS NULL AND probability IS NOT NULL "
                             "ORDER BY id").fetchall():
        if len(made) >= limit:
            break
        if _existing(db, pid):
            continue
        try:
            made.append(imagine(db, pid, now))
        except (ValidationError, NotFound):
            continue
    return made


def compare(payload, outcome) -> dict:
    """The futures against what happened: three-way Brier and log score, and the plain forecast's Brier when the
    outcome was decisive."""
    probs = {k: float(payload["futures"][k]["p"]) for k in FUTURES}
    brier = sum((probs[k] - (1.0 if k == outcome else 0.0)) ** 2 for k in FUTURES)
    out = {"outcome": outcome, "p_of_outcome": probs[outcome], "brier_3way": brier,
           "log_loss": -math.log(max(probs[outcome], LOG_FLOOR)),
           "decisive_predicted": probs["confirmed"] + probs["error"],
           "decisive_observed": outcome != "inconclusive", "forecast_brier": None}
    if outcome != "inconclusive":
        out["forecast_brier"] = (float(payload["forecast"]["used"]) - (outcome == "confirmed")) ** 2
    return out


def settle(db, now) -> list[dict]:
    """Fill the outcome slot of every scenario record whose prediction has settled (once): the observed outcome,
    the prediction named as the evidence, and compare()."""
    done = []
    rows = db.execute("SELECT i.id, i.payload, p.id, p.outcome, p.checked_at FROM imagined i JOIN imagined_refs r ON "
                      "r.record_id=i.id AND r.ref_kind='prediction' JOIN web_predictions p ON p.id=CAST(r.ref_id AS "
                      "INTEGER) WHERE i.mechanism=? AND i.outcome IS NULL AND p.outcome IS NOT NULL ORDER BY i.created_at",
                      (MECHANISM,)).fetchall()
    for record_id, payload, pid, outcome, checked_at in rows:
        if outcome not in FUTURES:
            continue
        comparison = compare(json.loads(payload), outcome)
        imagined.observe(db, record_id, observation={"prediction_id": pid, "outcome": outcome,
                                                     "settled_at": checked_at, "comparison": comparison},
                         evidence=[("prediction", pid)], now=now)
        done.append({"record_id": record_id, "prediction_id": pid, "outcome": outcome,
                     "brier_3way": comparison["brier_3way"]})
    return done


def errors(db) -> dict:
    """The prediction error of each scenario mechanism, from the filled outcome slots (computed on read): n, mean
    three-way Brier and log loss, predicted against observed share of decisive outcomes, and the plain forecast's
    Brier on the decisive ones. For reference, three equal futures score a Brier of 2/3 and a log loss of ln 3."""
    out = {}
    for mechanism, version, text in db.execute(
            "SELECT mechanism, mechanism_version, outcome FROM imagined WHERE mechanism LIKE 'scenarios.%' AND "
            "outcome IS NOT NULL ORDER BY created_at"):
        c = json.loads(text)["observed"]["comparison"]
        entry = out.setdefault(f"{mechanism} v{version}", {"n": 0, "brier": 0.0, "log_loss": 0.0,
                                                           "decisive_predicted": 0.0, "decisive_observed": 0,
                                                           "forecast_brier": [], })
        entry["n"] += 1
        entry["brier"] += c["brier_3way"]
        entry["log_loss"] += c["log_loss"]
        entry["decisive_predicted"] += c["decisive_predicted"]
        entry["decisive_observed"] += bool(c["decisive_observed"])
        if c["forecast_brier"] is not None:
            entry["forecast_brier"].append(c["forecast_brier"])
    report = {}
    for name, e in out.items():
        n, fb = e["n"], e["forecast_brier"]
        report[name] = {"n": n, "brier_3way": e["brier"] / n, "log_loss": e["log_loss"] / n,
                        "decisive_predicted": e["decisive_predicted"] / n, "decisive_observed": e["decisive_observed"] / n,
                        "forecast_brier_on_decisive": sum(fb) / len(fb) if fb else None, "decisive_n": len(fb)}
    return {"label": provenance.NOT_EVIDENCE, "mechanisms": report,
            "reference": {"brier_3way_equal_futures": 2 / 3, "log_loss_equal_futures": math.log(3)}}


def tick(db, now) -> dict | None:
    """The timer's pass: settle what can be compared, then imagine for new open predictions (at most PER_TICK).
    None while imagine_scenarios is off (the default); held while the owner's quiet is on."""
    if not enabled(db) or not imagined.present(db):
        return None
    from .. import background
    allowed, reason = background.allowed(db, "tick.scenarios")
    if not allowed:
        return {"held": reason}
    try:
        settled = settle(db, now)
        made = imagine_open(db, now)
    except InstanceError as error:          # writes paused, the store full at 0 rows: the next pass tries again
        return {"skipped": str(error)[:300]}
    return {"settled": len(settled), "imagined": len(made)}
