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

import logging
import math
import re

from . import config as wconfig
from . import state

log = logging.getLogger(__name__)

RELIABILITY = 0.8
MIN_GAIN = 0.02
FUTILE_STREAK = 2
FUTILE_GAIN = 0.01
FUTILITY_FACTOR = 0.25
GAP_WINDOW = 7 * 86400
ERROR_WINDOW = 30 * 86400
IMPORTANCE = {"accepted": 1.0, "provisional": 0.7, "stale": 0.7, "held": 0.4, "monitor": 1.2}
# Learning progress (ADR-051; progress(); WebConfig.curiosity_learning_progress). Declared before measurement.
LP_WINDOW = 6                 # a subject's newest looks considered
PRIOR_RESOLVABLE = 0.5        # prior that a subject further looks can resolve
PROGRESS_CHANCE = 0.8         # chance that a look at a resolvable subject makes progress
PROGRESS_SHARE = 0.5          # a look made progress when it realised at least this share of the expected gain
CHANGED_EFFECTS = ("new", "reversed", "strengthened", "weakened")
# review1 CM1, chosen on the development split only (lane fix1b's report has the grid): a subject's down-weight
# halves every LP_HALF_LIFE seconds after its newest look and the factor never goes below LP_FLOOR.
LP_HALF_LIFE = 7 * 86400.0
LP_FLOOR = 0.05
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


def _futility(db, key, *, refresh_is_progress=False):
    """(factor, note) from the key's realised feedback history. With refresh_is_progress (learning progress on), a
    confirming re-verification (consolidation's refresh, not a disagreement) ends the streak as progress() credits
    it; off, every 'unchanged' look counts, as before ADR-051."""
    effects = db.execute("SELECT effect, info_gain, json_extract(detail,'$.new_claims') IS NOT NULL AND "
                         "coalesce(json_extract(detail,'$.contested'),0)=0 FROM web_feedback WHERE key=? "
                         "ORDER BY id DESC LIMIT 6", (key,)).fetchall()
    streak = 0
    for effect, gain, refresh in effects:
        if effect == "unchanged" and abs(gain or 0.0) < FUTILE_GAIN and not (refresh_is_progress and refresh):
            streak += 1
        else:
            break
    if streak < FUTILE_STREAK:
        return 1.0, ""
    factor = FUTILITY_FACTOR ** (streak - FUTILE_STREAK + 1)
    return factor, (f"; the last {streak} investigations of this key changed nothing (disagreement looks "
                    f"irreducible), so the gain is down-weighted x{factor:g}")


def learning_progress_on(db) -> bool:
    """The owner's curiosity_learning_progress setting (WebConfig; ADR-051)."""
    return getattr(state.get_config(db), "curiosity_learning_progress", False) is True


def progress(db, key, expected, now=None) -> tuple[float, str, dict]:
    """(factor, note, record): learning progress of a subject (ADR-051), from its last LP_WINDOW looks in
    web_feedback (every research outcome about the key, newest first) compared with the gain expected of a look now.

    A look's realised gain is the entropy it removed (web_feedback.info_gain, never below 0); a look that changed what
    is held (effect new, reversed, strengthened or weakened, and not an unresolved disagreement) answered the question
    a look asks and is credited with the expected gain, and so is a re-verification that found the stored item still
    current (a refresh: effect unchanged, written by consolidation's refresh, not a disagreement): it settled what a
    look at a stale or due item is asked, whether the item is still current. A look made progress when it realised at
    least PROGRESS_SHARE of the expected gain. Two kinds of subject are told apart from the looks without progress
    since the last one that made some (n): a resolvable one makes progress on a look with chance PROGRESS_CHANCE, an
    irreducible one (noise that never resolves, or a subject that keeps changing nothing) never does; with the prior
    PRIOR_RESOLVABLE, P(resolvable | n) = p (1 - c)^n / (p (1 - c)^n + 1 - p). The factor is that posterior over the
    prior (1 at n = 0), so the expected gain is down-weighted only, never raised. The down-weight then fades with the
    time since the subject's newest look (half of it is gone after LP_HALF_LIFE; `now`, default the newest look's
    time, i.e. no fading) and never goes below LP_FLOOR, so a subject is never shut out for good. The caller compares
    the down-weighted gain with every admission floor (curiosity.MIN_GAIN, autonomy's min_gain)."""
    rows = db.execute("SELECT effect, info_gain, json_extract(detail,'$.contested'), "
                      "json_extract(detail,'$.new_claims') IS NOT NULL, created_at FROM web_feedback WHERE key=? "
                      "ORDER BY id DESC LIMIT ?", (key, LP_WINDOW)).fetchall()
    expected = max(0.0, float(expected or 0.0))
    looks = []
    for effect, gain, contested, refresh, _ in rows:
        realised = max(0.0, state.finite(gain, 0.0))
        if not contested and (effect in CHANGED_EFFECTS or (effect == "unchanged" and refresh)):
            realised = max(realised, expected)
        looks.append((realised, realised > 0 and realised >= PROGRESS_SHARE * expected))
    streak = 0
    for _, made in looks:
        if made:
            break
        streak += 1
    miss = (1.0 - PROGRESS_CHANCE) ** streak
    posterior = PRIOR_RESOLVABLE * miss / (PRIOR_RESOLVABLE * miss + 1.0 - PRIOR_RESOLVABLE)
    evidence_factor = posterior / PRIOR_RESOLVABLE
    since = 0.0 if not rows or now is None else max(0.0, float(now) - state.finite(rows[0][4], float(now)))
    faded = 1.0 - (1.0 - evidence_factor) * 0.5 ** (since / LP_HALF_LIFE)
    factor = 1.0 if not streak else max(LP_FLOOR, faded)
    record = {"looks": len(looks), "without_progress": streak, "expected_bits_per_look": round(expected, 6),
              "realised_bits": round(sum(r for r, _ in looks), 6), "p_resolvable": round(posterior, 6),
              "days_since_last_look": round(since / 86400.0, 3), "factor": round(factor, 6)}
    note = ("" if not streak else
            f"; learning progress: the last {streak} look(s) realised {sum(r for r, _ in looks[:streak]):.3f} bits "
            f"against {expected:.3f} expected each, so P(resolvable)={posterior:.2f}; "
            f"{since / 86400.0:.1f} day(s) since the last look, the gain is down-weighted x{factor:.3g}")
    return factor, note, record


def _monitor_keys(db):
    """Task ids launched by owner-authorised monitors (knowledge from them is owner-flagged)."""
    owners = {f"monitor:{mid}" for (mid,) in db.execute("SELECT id FROM web_monitors WHERE authorized_by='owner'")}
    if not owners:
        return set()
    return {tid for tid, origin in db.execute("SELECT id, origin FROM web_tasks") if origin in owners}


def _terms(key):
    return " ".join(t for t in re.findall(r"[^\W_]+", key or "") if t not in ("num", "ver"))[:200]


# A research opportunity must be a statement that can be checked on its own (T1-17): a sentence lifted from an
# abstract or release notes that leans on its context ("It has been employed ...", "We present ...", "for this
# release", "these procedures") names nothing a later search could verify.
_CONTEXT_START = re.compile(r"^(?:it|its|this|these|those|they|their|them|such|he|she|his|her|here|both|thus|hence|"
                            r"therefore|moreover|furthermore|however|additionally|also|in addition|as a result|"
                            r"as such)\b", re.I)
_AUTHORIAL = re.compile(r"\b(?:we|our|ours|us|you|your|yours)\b|^(?:We|Our|You|Your)\b")
_DEICTIC = re.compile(r"\b(?:this|these|those)\b", re.I)


def claim_shaped(statement) -> str | None:
    """None when `statement` is a self-contained, checkable claim; else why it is not (a structured record's
    template, a question, a fragment, too few words, or wording that depends on the text it was taken from)."""
    from . import claims
    s = " ".join(str(statement or "").split())
    if claims.record_template(s) is not None:
        return "a structured record's template, not a claim"
    if not s or s.endswith("?"):
        return "not a statement"
    if claims.fragment(s) or len(re.findall(r"[^\W\d_]{2,}", s)) < 3:
        return "a fragment, not a self-contained claim"
    if _CONTEXT_START.match(s) or _AUTHORIAL.search(s) or _DEICTIC.search(s):
        return "depends on the text it was taken from (a pronoun, 'we', 'you' or 'this ...'), not a self-contained claim"
    return None


def search_terms(statement=None, key=None, db=None) -> str:
    """Search words for a subject: the statement's own words (intent.query_text, never claim-key stems) when the
    statement is known (for a bare key, a stored claim's sentence with that key), else the key's terms."""
    from . import intent
    query_text = getattr(intent, "query_text", None)
    if query_text is not None:
        if not statement and key and db is not None:
            # among claims stored at the same instant, the one stored last (rowid): the index on (key, task_id)
            # would otherwise order them by task id, which is random (Wave 4 lane A)
            found = db.execute("SELECT text FROM web_claims WHERE key=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                               (key,)).fetchone()
            statement = found[0] if found else None
        if statement:
            words = query_text(statement)
            if words:
                return words[:200]
    return _terms(key)


def hypotheses(item, now) -> list[dict]:
    """Competing hypotheses for a contested knowledge item and a query that would distinguish each."""
    from datetime import datetime, timezone
    terms = search_terms(item.get("statement"), item["key"])
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
    from ..instance.errors import ValidationError
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a positive integer")
    cfg = state.get_config(db)
    monitor_tasks = _monitor_keys(db)
    errors = {kid for (kid,) in db.execute("SELECT knowledge_id FROM web_predictions WHERE outcome='error' AND "
                                           "checked_at>=?", (now - ERROR_WINDOW,))}
    due = {kid: pid for pid, kid in db.execute("SELECT id, knowledge_id FROM web_predictions WHERE outcome IS NULL "
                                               "AND check_after<=? ORDER BY id", (now,))}
    chosen, excluded = [], []
    lp_on = learning_progress_on(db)

    def damp(key, gain, factor, futile, kid=None):
        """The futility factor, or learning progress when the owner's setting is on and it down-weights more (both
        read the same web_feedback rows, so the stronger of the two applies, never their product), and the
        record."""
        if not lp_on:
            return factor, futile, {}
        strict = factor, futile           # the rule with the setting off (refreshes that confirm are no progress)
        factor, futile = _futility(db, key, refresh_is_progress=True)
        try:            # Wave 2 lane U: a failing learning-progress read is said and logged; Wave 2 lane X (review2
            lp, note, record = progress(db, key, gain, now)     # L2): it falls back to the strict rule of the
            lp = float(lp)                                      # setting off, never to the gentler one
        except Exception as exc:          # noqa: BLE001 - recorded with the candidate and logged, never silent
            from ..instance.errors import failure_kind      # review2b N5: the item's id, never its key or message
            log.warning("learning progress of the curiosity candidate %s could not be read (%s); the futility rule "
                        "applies as with the setting off", f"knowledge #{kid}" if kid is not None else
                        "without a knowledge item", failure_kind(exc))
            return (*strict, {"learning_progress": {"error": f"could not be read ({type(exc).__name__})"}})
        if lp >= factor:
            return factor, futile, {"learning_progress": record}
        return lp, note, {"learning_progress": record}

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
            factor, futile, lp = damp(item["key"], gain, factor, futile, item["id"])
            chosen.append(_entry(
                "contested", target, gain, "fact_verification", importance,
                f"contested: {support} supporting vs {against} contradicting claim(s); distinguishing 4 hypotheses "
                f"gains {gain:.3f} bits{boost}{futile}", factor=factor,
                extra={"hypotheses": hypotheses(item, now)} | lp))
            continue
        stale, why = knowledge.staleness(item, now, cfg, db)
        if item["status"] == "stale" or (stale and why.startswith(("stale: a newer", "stale: a new version"))):
            p = 0.5         # marked stale, or a newer release / source revision was observed: even odds
        else:
            elapsed = max(0.0, now - item["last_verified_at"])
            p = 1.0 - (1.0 - math.exp(-elapsed / max(1.0, _ttl(cfg, item["freshness_class"])))) / 2.0
        gain = info_gain(p)
        factor, futile, lp = damp(item["key"], gain, factor, futile, item["id"])
        age = temporal.describe_age(item["last_verified_at"], now)
        if item["id"] in due:
            chosen.append(_entry("prediction", target | {"prediction_id": due[item["id"]]}, gain,
                                 "fact_verification", importance,
                                 f"prediction due: '{item['statement'][:60]}' was expected to still hold; "
                                 f"P(current)={p:.2f}, gain {gain:.3f} bits{boost}{futile}", factor=factor,
                                 extra=lp))
        elif stale and gain * factor >= MIN_GAIN:
            chosen.append(_entry("stale", target, gain, "fact_verification", importance,
                                 f"stale ({why}; verified {age}); P(current)={p:.2f}, gain {gain:.3f} bits"
                                 f"{boost}{futile}", factor=factor, extra=lp))
        else:
            why_not = (f"expected gain {gain * factor:.3f} bits < {MIN_GAIN}" if gain * factor < MIN_GAIN else
                       f"not due: {why}")
            excluded.append(_entry("stale" if stale else "fresh", target, gain, "fact_verification", importance,
                                   f"excluded: {why_not} (verified {age}, well supported){futile}; avoid unnecessary "
                                   "searches", factor=factor, extra=lp) | {"excluded": True, "utility": 0.0})
    # Contested subjects that never became knowledge (open questions from consolidation).
    seen_keys = {i["key"] for i in live}
    for (key,) in db.execute("SELECT DISTINCT key FROM web_feedback WHERE knowledge_id IS NULL "
                             "AND json_extract(detail,'$.contested')=1 AND key NOT IN (SELECT key FROM web_feedback "
                             "WHERE json_extract(detail,'$.owner_choice') IS NOT NULL) ORDER BY key").fetchall():
        if key in seen_keys:
            continue
        factor, futile = _futility(db, key)
        gain = categorical_gain([0.35, 0.35, 0.15, 0.15])
        factor, futile, lp = damp(key, gain, factor, futile)
        terms = search_terms(None, key, db)
        said = db.execute("SELECT text FROM web_claims WHERE key=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                          (key,)).fetchone()          # ties: the claim stored last, not the random task id's order
        target = {"key": key} | ({"statement": knowledge.quote(said[0])} if said else {})   # shown, never the key
        entry = _entry("hypothesis", target, gain, "gap_resolution", IMPORTANCE["held"],
                       f"sources disagree and nothing is known yet; distinguishing hypotheses gains {gain:.3f} bits"
                       f"{futile}", factor=factor,
                       extra={"hypotheses": [{"label": "A holds", "query": f"{terms} official source"},
                                             {"label": "B holds", "query": f"{terms} latest"}]} | lp)
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
        factor, futile, lp = damp(key, gain, factor, futile)
        entry = _entry("gap", {"key": key, "statement": knowledge.quote(sample)}, gain, "gap_resolution",
                       IMPORTANCE["held"], f"held single-source claim (confidence {p:.2f}); a second independent "
                       f"source would gain {gain:.3f} bits{futile}", factor=factor, extra=lp)
        (chosen if gain * factor >= MIN_GAIN else excluded).append(
            entry if gain * factor >= MIN_GAIN else entry | {"excluded": True, "utility": 0.0})
    chosen.sort(key=lambda e: (-e["utility"], str(e["target"])))
    excluded.sort(key=lambda e: str(e["target"]))
    return chosen[:limit] + [e | {"excluded": True, "utility": 0.0} for e in excluded[:limit]]


def propose(db, candidate: dict, now) -> dict:
    """Turn a candidate into a research *proposal* the owner authorises (pending scope web:research:<task id>).

    The task is created queued with stage 'proposed'; the worker skips it until the owner confirms. Task and
    question are written in one transaction (one state bump). Conflict when the web question budget
    (config.max_open_web_questions) is spent: a proposal is a question, and the owner is not interrupted further.
    Its request/byte budget is sized to the daily autonomous allowance left now (ValidationError, no proposal, when
    nothing is left); confirming it and starting it check the allowance again (knowledge.apply_review,
    orchestrator._claim)."""
    from ..instance import store
    from ..instance.errors import Conflict
    from . import orchestrator
    from .knowledge import open_question, question_room, quote
    target = candidate.get("target") or {}
    objective = target.get("statement") or search_terms(None, target.get("key", ""), db)
    if candidate.get("kind") == "hypothesis":     # a disagreement: its search words, not one side's sentence
        objective = search_terms(None, target.get("key", ""), db)
    if candidate.get("hypotheses"):
        objective = f"{objective} ({candidate['hypotheses'][0]['query']})"
    prepared = orchestrator.prepare(db, objective, now, mode=candidate.get("mode", "fact_verification"),
                                    origin="curiosity", proposed=True)
    with store.write(db):
        cfg = state.get_config(db)
        if not question_room(db, cfg, now):
            raise Conflict(f"the owner already has {cfg.max_open_web_questions} open web questions; no further "
                           "research proposal is made until some are answered")
        task_id = orchestrator.insert(db, prepared, now)
        pid = open_question(db, f"web:research:{task_id}",
                            f"Curiosity proposes researching «{quote(objective)}» ({candidate.get('reason', '')}). "
                            "Run it?", now)
        store.bump_state_version(db)
    return orchestrator.get(db, task_id) | {"pending_id": pid}
