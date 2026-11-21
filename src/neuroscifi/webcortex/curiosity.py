"""Curiosity: which investigation would teach the individual the most per unit of cost. It never runs research.

Utility = expected information gain (bits) x importance / estimated cost.

Information gain, not entropy (research/scripts/curiosity_control.py: an entropy-seeking policy keeps choosing an
irreducibly noisy observation, whose outcome is uncertain but uninformative; information gain does not). One more
investigation is modelled as a report of reliability r = 0.8 about a binary variable X with P(X) = p:

    IG(p) = H(p r + (1 - p)(1 - r)) - H(r)            (mutual information between X and the report)

- uncontested knowledge: X = "the stored version is still current"; the chance it changed grows with age towards
  even odds, p = 1 - (1 - exp(-age / ttl)) / 2 for its freshness class (status 'stale' -> p = 0.5). Freshly
  verified knowledge has p ~ 1 and IG ~ 0; knowledge within its TTL is not a candidate at all ("avoid unnecessary
  searches"), and the reason is stated;
- contested knowledge: the competing hypotheses H = {A holds, B holds, both held at different times, different
  scope} with prior from the support of each side; IG = H(Y) - H(Y | H) for a symmetric k-ary report of
  reliability r. Each contested candidate carries those hypotheses and a distinguishing query for each;
- predictions due and held single-source claims (knowledge gaps): the item's own posterior p.

Irreducible noise: a key whose disagreement persists however often it is read (balanced contradictory sources)
has a high *expected* gain on paper. Its realised gains are recorded in web_feedback: when the last
FUTILE_STREAK investigations of a key changed nothing (effect 'unchanged', |info_gain| < FUTILE_GAIN) the
expected gain is multiplied by FUTILITY_FACTOR per further futile run, so it stops dominating.

Importance: accepted 1.0, provisional 0.7, held 0.4, knowledge from an owner's monitor 1.2; a recent prediction
error x1.5. Cost: max_requests of the mode budget the investigation would use, relative to fact_verification.
"""
from __future__ import annotations

import math
import re

from . import config as wconfig
from . import state

RELIABILITY = 0.8
MIN_GAIN = 0.02
FUTILE_STREAK = 2
FUTILE_GAIN = 0.01
FUTILITY_FACTOR = 0.25
GAP_WINDOW = 7 * 86400
ERROR_WINDOW = 30 * 86400
IMPORTANCE = {"accepted": 1.0, "provisional": 0.7, "stale": 0.7, "held": 0.4, "monitor": 1.2}
REFERENCE_COST = wconfig.MODE_BUDGETS["fact_verification"].max_requests


def entropy(p) -> float:
    """Binary entropy in bits."""
    p = min(1.0, max(0.0, float(p)))
    if p in (0.0, 1.0):
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def info_gain(p, reliability=RELIABILITY) -> float:
    """Mutual information (bits) between a binary truth with P = p and one report of the given reliability
    (claims.info_gain over the Beta mean p = alpha / (alpha + beta))."""
    from .claims import info_gain as beta_info_gain
    p = min(1.0, max(0.0, float(p)))
    return beta_info_gain(p, 1.0 - p, reliability)


def categorical_gain(prior, reliability=RELIABILITY) -> float:
    """MI (bits) between k hypotheses with `prior` and a symmetric k-ary report of the given reliability."""
    total = sum(prior)
    if total <= 0 or len(prior) < 2:
        return 0.0
    prior = [p / total for p in prior]
    k = len(prior)
    miss = (1 - reliability) / (k - 1)
    y = [reliability * p + miss * (1 - p) for p in prior]
    h = lambda dist: -sum(q * math.log2(q) for q in dist if q > 0)  # noqa: E731
    return max(0.0, h(y) - h([reliability] + [miss] * (k - 1)))


def cost(mode) -> float:
    return wconfig.MODE_BUDGETS.get(mode, wconfig.MODE_BUDGETS["fact_verification"]).max_requests / REFERENCE_COST


def _ttl(cfg, cls):
    from . import temporal
    return float(temporal.ttl(cls, cfg.freshness_ttl or None))


def _futility(db, key):
    """(factor, note) from the key's realised feedback history."""
    effects = db.execute("SELECT effect, info_gain FROM web_feedback WHERE key=? ORDER BY id DESC LIMIT 6",
                         (key,)).fetchall()
    streak = 0
    for effect, gain in effects:
        if effect == "unchanged" and abs(gain or 0.0) < FUTILE_GAIN:
            streak += 1
        else:
            break
    if streak < FUTILE_STREAK:
        return 1.0, ""
    factor = FUTILITY_FACTOR ** (streak - FUTILE_STREAK + 1)
    return factor, (f"; the last {streak} investigations of this key changed nothing (disagreement looks "
                    f"irreducible), so the gain is down-weighted x{factor:g}")


def _monitor_keys(db):
    """Task ids launched by owner-authorised monitors (knowledge from them is owner-flagged)."""
    owners = {f"monitor:{mid}" for (mid,) in db.execute("SELECT id FROM web_monitors WHERE authorized_by='owner'")}
    if not owners:
        return set()
    return {tid for tid, origin in db.execute("SELECT id, origin FROM web_tasks") if origin in owners}


def _terms(key):
    return " ".join(t for t in re.findall(r"[^\W_]+", key or "") if t not in ("num", "ver"))[:200]


def hypotheses(item, now) -> list[dict]:
    """Competing hypotheses for a contested knowledge item and a query that would distinguish each."""
    from datetime import datetime, timezone
    terms = _terms(item["key"])
    year = datetime.fromtimestamp(now, timezone.utc).year
    first = item["contradicting"][0] if item["contradicting"] else {}
    return [
        {"label": "A holds", "statement": knowledge_quote(item["statement"]),
         "query": f"{terms} official source"},
        {"label": "B holds", "statement": knowledge_quote(first.get("text", "")), "query": f"{terms} {year}"},
        {"label": "both held at different times", "query": f"{terms} changed {year}"},
        {"label": "different scope", "query": f"{terms} specification primary source"},
    ]


def knowledge_quote(text):
    from .knowledge import quote
    return quote(text)


def _entry(kind, target, gain, mode, importance, reason, *, extra=None, factor=1.0):
    c = cost(mode)
    utility = gain * factor * importance / c if c else 0.0
    out = {"target": target, "kind": kind, "mode": mode, "info_gain": round(gain * factor, 6),
           "importance": importance, "cost": round(c, 6), "utility": round(utility, 6), "reason": reason,
           "excluded": False}
    return out | (extra or {})


def rank(db, now, *, limit=10) -> list[dict]:
    """Candidates sorted by utility (desc), then the excluded ones (excluded=True) with the reason."""
    from . import knowledge, temporal
    cfg = state.get_config(db)
    monitor_tasks = _monitor_keys(db)
    errors = {kid for (kid,) in db.execute("SELECT knowledge_id FROM web_predictions WHERE outcome='error' AND "
                                           "checked_at>=?", (now - ERROR_WINDOW,))}
    due = {kid: pid for pid, kid in db.execute("SELECT id, knowledge_id FROM web_predictions WHERE outcome IS NULL "
                                               "AND check_after<=? ORDER BY id", (now,))}
    chosen, excluded = [], []
    live = knowledge.rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
    for item in live:
        importance = IMPORTANCE["monitor"] if item["task_id"] in monitor_tasks else IMPORTANCE[item["status"]]
        boost = ""
        if item["id"] in errors:
            importance *= 1.5
            boost = "; a recent prediction about it failed (x1.5)"
        factor, futile = _futility(db, item["key"])
        target = {"knowledge_id": item["id"], "key": item["key"], "statement": knowledge.quote(item["statement"])}
        if item["contradicting"]:
            support = max(1, len(item["supporting"]))
            against = max(1, len({c.get("claim") for c in item["contradicting"] if isinstance(c, dict)}))
            share = 0.7 / (support + against)
            gain = categorical_gain([support * share, against * share, 0.15, 0.15])
            chosen.append(_entry(
                "contested", target, gain, "fact_verification", importance,
                f"contested: {support} supporting vs {against} contradicting claim(s); distinguishing 4 hypotheses "
                f"gains {gain:.3f} bits{boost}{futile}", factor=factor,
                extra={"hypotheses": hypotheses(item, now)}))
            continue
        stale, why = knowledge.staleness(item, now, cfg, db)
        if item["status"] == "stale" or (stale and why.startswith(("stale: a newer", "stale: a new version"))):
            p = 0.5         # marked stale, or a newer release / source revision was observed: even odds
        else:
            elapsed = max(0.0, now - item["last_verified_at"])
            p = 1.0 - (1.0 - math.exp(-elapsed / max(1.0, _ttl(cfg, item["freshness_class"])))) / 2.0
        gain = info_gain(p)
        age = temporal.describe_age(item["last_verified_at"], now)
        if item["id"] in due:
            chosen.append(_entry("prediction", target | {"prediction_id": due[item["id"]]}, gain,
                                 "fact_verification", importance,
                                 f"prediction due: '{item['statement'][:60]}' was expected to still hold; "
                                 f"P(current)={p:.2f}, gain {gain:.3f} bits{boost}{futile}", factor=factor))
        elif stale and gain * factor >= MIN_GAIN:
            chosen.append(_entry("stale", target, gain, "fact_verification", importance,
                                 f"stale ({why}; verified {age}); P(current)={p:.2f}, gain {gain:.3f} bits"
                                 f"{boost}{futile}", factor=factor))
        else:
            why_not = (f"expected gain {gain * factor:.3f} bits < {MIN_GAIN}" if gain * factor < MIN_GAIN else
                       f"not due: {why}")
            excluded.append(_entry("stale" if stale else "fresh", target, gain, "fact_verification", importance,
                                   f"excluded: {why_not} (verified {age}, well supported){futile}; avoid unnecessary "
                                   "searches", factor=factor) | {"excluded": True, "utility": 0.0})
    # Contested subjects that never became knowledge (open questions from consolidation).
    seen_keys = {i["key"] for i in live}
    for (key,) in db.execute("SELECT DISTINCT key FROM web_feedback WHERE knowledge_id IS NULL "
                             "AND json_extract(detail,'$.contested')=1 ORDER BY key").fetchall():
        if key in seen_keys:
            continue
        factor, futile = _futility(db, key)
        gain = categorical_gain([0.35, 0.35, 0.15, 0.15])
        entry = _entry("hypothesis", {"key": key}, gain, "gap_resolution", IMPORTANCE["held"],
                       f"sources disagree and nothing is known yet; distinguishing hypotheses gains {gain:.3f} bits"
                       f"{futile}", factor=factor,
                       extra={"hypotheses": [{"label": "A holds", "query": f"{_terms(key)} official source"},
                                             {"label": "B holds", "query": f"{_terms(key)} latest"}]})
        (chosen if gain * factor >= MIN_GAIN else excluded).append(entry)
    # Knowledge gaps: single-source claims of recent tasks that no knowledge row covers.
    gaps = db.execute("SELECT key, max(confidence), min(text) FROM web_claims WHERE cluster_status='single_source' "
                      "AND created_at>=? GROUP BY key ORDER BY key LIMIT 200", (now - GAP_WINDOW,)).fetchall()
    for key, conf, sample in gaps:
        if key in seen_keys:
            continue
        factor, futile = _futility(db, key)
        p = state.finite(conf, 0.5)
        gain = info_gain(p)
        entry = _entry("gap", {"key": key, "statement": knowledge.quote(sample)}, gain, "gap_resolution",
                       IMPORTANCE["held"], f"held single-source claim (confidence {p:.2f}); a second independent "
                       f"source would gain {gain:.3f} bits{futile}", factor=factor)
        (chosen if gain * factor >= MIN_GAIN else excluded).append(
            entry if gain * factor >= MIN_GAIN else entry | {"excluded": True, "utility": 0.0})
    chosen.sort(key=lambda e: (-e["utility"], str(e["target"])))
    excluded.sort(key=lambda e: str(e["target"]))
    limit = max(1, int(limit))
    return chosen[:limit] + [e | {"excluded": True, "utility": 0.0} for e in excluded[:limit]]


def propose(db, candidate: dict, now) -> dict:
    """Turn a candidate into a research *proposal* the owner authorises (pending scope web:research:<task id>).

    The task is created queued with stage 'proposed'; the worker skips it until the owner confirms."""
    from ..instance import store
    from . import orchestrator
    target = candidate.get("target") or {}
    objective = target.get("statement") or _terms(target.get("key", ""))
    if candidate.get("hypotheses"):
        objective = f"{objective} ({candidate['hypotheses'][0]['query']})"
    task = orchestrator.submit(db, objective, now, mode=candidate.get("mode", "fact_verification"),
                               origin="curiosity", proposed=True)
    with store.write(db):
        from .knowledge import open_question, quote
        pid = open_question(db, f"web:research:{task['id']}",
                            f"Curiosity proposes researching «{quote(objective)}» ({candidate.get('reason', '')}). "
                            "Run it?", now)
        store.bump_state_version(db)
    return task | {"pending_id": pid}
