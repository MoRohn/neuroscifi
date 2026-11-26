"""Monitoring of a research answer: calibrated confidence, evidence sufficiency, selective answering and the depth
record (Project Cognitive Genesis lane C, ADR-054; docs/web-cortex/MONITORING.md).

One record per research task, report["monitoring"], written by the orchestrator after the answer (and the model's
guarded summary) exist and before the result is stored. It reads only signals the pipeline already produced and
never changes a verification state, a posterior, what the reasoning guard accepts or what consolidation keeps. No
model is called. The model never grades itself here: its self-critique marks are one weak input with a declared
weight, never a gate.

Signals (signals()): the verification state of the answer's statement and the posterior the claims module gave it,
the number of independent source groups for and against, how independent those groups really are
(claims.dependence: one family of sites, sources citing each other), the reasoning guard's outcome (sentences it
refused, the model abstaining), the self-critique's marks, agreement between the web and what memory already held
(consolidation contested or strengthened the statement), staleness (the question's freshness window not met) and
the open contradictions on the subject.

Score (components()): a sum in log-odds units, each term named, its weight in WEIGHTS: the claims module's
posterior as log-odds, plus or minus a fixed amount per signal. Nothing is fitted here.

Claim probability: the UNFITTED score, the logistic of the sum (Wave 1 review B5: the map fitted for claims scored
worse than the unfitted score on the development split, Brier 0.034 against 0.016, and on held-out as well).

Answer confidence (calibrate()): a monotone map per stratum ("verdict": P(the verification state given is right);
"answer": P(the top quoted answer is right)), fitted by fit(): isotonic regression (pool adjacent violators) whose
blocks each carry a Beta(1, 1) prior (the claims module's prior), so a block of 4 agreeing outcomes reads 5/6, never
1, interpolated linearly between block centres and flat beyond the ends. The maps in data/monitoring_calibration.json
were fitted on the DEVELOPMENT split of the cognitive benchmark's selective family only (scripts/bench_monitoring.py
fit); the held-out split was never used to fit or choose anything. A map is APPLIED only when it was fitted on at
least MIN_ITEMS items in at least MIN_BLOCKS blocks (applied()); otherwise the record shows no number and says
NOT_ENOUGH_DATA (Wave 1 review A6: the fact-check map was one block of 15 items, so every fact check, an
"insufficient" verdict too, showed the same 0.941). The shipped maps are all below that minimum today, so the record
carries no answer confidence. They are fitted on a fictional offline corpus: on the open web they would be a
starting point, not a guarantee.

Selective answering (WebConfig.selective_answering, off by default): for a fact check, and for any answer whose map is
not applied, the decision reads sufficiency() (are there enough independent sources, fresh enough, from different
families of sites); only an applied map with the threshold chosen on the development split for TARGET_RISK would
decide by number. A weak answer opens with a plain "not enough evidence" line that says why; the quotes stay (they
are what the sources say). Verification escalation (WebConfig.verification_escalation, off by default;
orchestrator._Run.escalate_verification) reads one more independent source for the weakest statement first, inside
the task's own budget.

The depth record (depth()) says how many pages, queries and requests the task used and how many statements had
two independent groups after each page, so the gain of each extra page can be read later
(WebConfig.adaptive_depth uses the same count while reading to stop early, off by default).
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path

VERSION = 1
CALIBRATION_FILE = Path(__file__).resolve().parent / "data" / "monitoring_calibration.json"
TARGET_RISK = 0.10              # the selective threshold keeps development risk at or below this
MIN_GROUPS = 2                  # independent groups for a statement to count as verified (claims CATEGORY_POLICY)
EPSILON = 1e-4                  # probabilities are clipped to [EPSILON, 1 - EPSILON] before log-odds
MIN_ITEMS = 30                  # a fitted map is applied only with this many development items ...
MIN_BLOCKS = 3                  # ... in this many blocks (Wave 1 review A6)
NOT_ENOUGH_DATA = "not enough development data"

# Log-odds weight of each signal, fixed before the development split was scored and never fitted; the ablation in
# docs/quality/evidence/genesis_monitoring.json switches each off. "critique" is the self-critique's share of
# marked sentences: a weak input, and 0.0 because on the two recorded live-model runs the marks fell on 41 of 94 and
# 46 of 90 kept sentences while 3 sentences stated a value the corpus marks false (precision 0.07, recall 1.0;
# random marks would do as well with probability 0.08 and 0.13: genesis_monitoring.json "critique_marks"). It
# stays a named, displayed signal. The guard's refusals and the model abstaining are 0.0 as well, shown only (Wave 1
# review A5): the development data has no model calls, so nothing measured what they say about an answer, and with
# a weight the confidence would move with the model's wording while the evidence stayed the same.
WEIGHTS = {
    "state": {"supported": 1.0, "disputed": -1.0},  # other states: 0 (the posterior already says it)
    "per_contradiction": -0.25,                      # each open contradiction on the subject, at most 2
    "stale": -0.25,                                  # the question's freshness window is not met
    "per_dependent_group": -0.5,                     # a supporting group joined to another (family or citation)
    "guard_refused_share": 0.0,                      # share of the model's sentences the guard refused (shown only)
    "model_abstained": 0.0,                          # the model wrote nothing the guard could keep (shown only)
    "critique": 0.0,                                 # share of kept sentences the self-critique marked
    "memory_agrees": 0.25,                           # memory held the statement and the web strengthened it
    "memory_disagrees": -0.25,                       # memory or another source contests it on record
}
COMPONENTS = ("evidence", "state", "contradictions", "stale", "independence", "guard", "critique", "agreement")


def _clip(p: float) -> float:
    return min(1.0 - EPSILON, max(EPSILON, float(p)))


def logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


# ---- signals ---------------------------------------------------------------------------------------------------

def _top_quote(report) -> dict | None:
    return next((a for a in report.get("answer") or [] if a.get("type") == "quote"), None)


def _cluster_of(clusters, *, ids=(), text=None):
    ids = set(ids or ())
    for cl in clusters or ():
        members = {c.id for c in cl.claims}
        if ids and ids & members:
            return cl
        if text and (cl.statement == text or any(c.text == text for c in cl.claims)):
            return cl
    return None


def signals(report: dict, *, documents=(), clusters=(), consolidation=None) -> dict:
    """The signals of the answer's statement, from the report (and, when the run has them, its documents, clusters
    and consolidation result). kind: "verdict" (a fact check: the report verdict), "answer" (the top quoted
    statement) or "none"."""
    from . import claims as C
    verdict = report.get("verdict") or {}
    state = C.normalise_state(verdict.get("state")) if verdict.get("state") else None
    quote = _top_quote(report)
    out = {"kind": "none", "state": None, "posterior": None, "supporting_groups": 0, "contradicting_groups": 0,
           "statement": None}
    if state:
        groups = verdict.get("independent_groups") or {}
        out |= {"kind": "verdict", "state": state, "posterior": verdict.get("confidence"),
                "supporting_groups": int(groups.get("supporting") or 0),
                "contradicting_groups": int(groups.get("contradicting") or 0), "statement": verdict.get("statement")}
        cluster = _cluster_of(clusters, ids=verdict.get("supporting") or ())
    elif quote is not None:
        v = quote.get("verification") or {}
        out |= {"kind": "answer", "state": C.normalise_state(v.get("state")) if v.get("state") else None,
                "posterior": quote.get("confidence"), "supporting_groups": int(quote.get("independent_sources") or 0),
                "contradicting_groups": 1 if quote.get("status") == "contested" else 0, "statement": quote.get("text")}
        cluster = _cluster_of(clusters, text=quote.get("text"))
    else:
        cluster = None
    out["contradictions"] = len(report.get("contradictions") or [])
    fresh = (report.get("freshness") or {}).get("satisfied")
    out["stale"] = fresh is False
    out["answered_from_memory"] = bool((report.get("memory") or {}).get("answered_from_memory"))
    reasoning = report.get("reasoning") or {}
    rejected = reasoning.get("rejected") or []
    by_critique = sum(1 for r in rejected if any(str(x).startswith("self-critique") for x in r.get("reasons") or []))
    kept = len(reasoning.get("answer") or [])
    critique = reasoning.get("critique") or {}
    out["guard"] = {"status": reasoning.get("status"), "kept": kept, "refused": len(rejected) - by_critique,
                    "abstained": bool(reasoning.get("abstained"))}
    out["critique"] = {"status": critique.get("status"), "flagged": int(critique.get("flagged") or 0),
                       "removed": int(critique.get("removed") or 0), "kept": kept}
    out["independence"] = None
    if cluster is not None:
        support = set(cluster.supporting)
        members = [c for c in cluster.claims if c.id in support and not C.is_adversarial(c)]
        out["independence"] = C.dependence(members, documents)
    out["agreement"] = _agreement(consolidation, cluster)
    return out


def _agreement(consolidation, cluster) -> dict | None:
    """Memory against the web for the answer's statement: consolidation's record of the cluster's key (strengthened:
    memory held it and the web agrees; contested: a stored item or another source disagrees on record)."""
    if not consolidation or cluster is None or not cluster.key:
        return None
    def keyed(entries):
        return sum(1 for e in entries or () if isinstance(e, dict) and e.get("key") == cluster.key)
    return {"agrees": keyed(consolidation.get("strengthened")), "disagrees": keyed(consolidation.get("contested"))}


# ---- score -----------------------------------------------------------------------------------------------------

def components(sig: dict) -> dict:
    """The score's named terms in log-odds units (see WEIGHTS)."""
    w = WEIGHTS
    post = sig.get("posterior")
    out = {"evidence": logit(0.5 if post is None else post),
           "state": float(w["state"].get(sig.get("state"), 0.0)),
           "contradictions": w["per_contradiction"] * min(int(sig.get("contradictions") or 0), 2),
           "stale": w["stale"] if sig.get("stale") else 0.0}
    dep = sig.get("independence") or {}
    joined = max(0, int(dep.get("groups") or 0) - int(dep.get("effective_groups") or 0)) if dep else 0
    out["independence"] = w["per_dependent_group"] * joined if sig.get("state") == "supported" or \
        sig.get("kind") == "answer" else 0.0
    guard = sig.get("guard") or {}
    written = int(guard.get("kept") or 0) + int(guard.get("refused") or 0)
    out["guard"] = (w["guard_refused_share"] * (int(guard.get("refused") or 0) / written) if written else 0.0) + \
        (w["model_abstained"] if guard.get("abstained") else 0.0)
    crit = sig.get("critique") or {}
    out["critique"] = w["critique"] * (crit["flagged"] / crit["kept"]) if crit.get("kept") else 0.0
    agree = sig.get("agreement") or {}
    out["agreement"] = (w["memory_agrees"] if agree.get("agrees") else 0.0) + \
        (w["memory_disagrees"] if agree.get("disagrees") else 0.0)
    return out


def score(parts: dict, drop=()) -> float:
    return sum(v for k, v in parts.items() if k not in drop)


def stratum(sig: dict) -> str:
    return "verdict" if sig.get("kind") == "verdict" else "answer"


def answer_score(sig: dict, s: float) -> float:
    """The score the answer-confidence map reads: for a fact check, how decisively the evidence points one way (a
    verdict far from even is a verdict less likely to flip); for a quoted answer, the statement's own score."""
    return abs(s) if stratum(sig) == "verdict" else s


# ---- calibration ----------------------------------------------------------------------------------------------

def fit(scores, outcomes) -> dict:
    """Monotone (non-decreasing) map from score to probability: pool adjacent violators over the outcomes sorted by
    score (tied scores pooled first), each block read as its Beta(1, 1) posterior mean (k + 1) / (n + 2).
    {"x": block mean scores, "y": block probabilities, "n": block sizes}. Empty input: the identity at 0.5."""
    pairs = sorted((float(s), int(bool(o))) for s, o in zip(scores, outcomes))
    blocks = []                                   # [sum score, sum outcome, n]
    for s, o in pairs:
        if blocks and blocks[-1][0] / blocks[-1][2] == s:
            blocks[-1] = [blocks[-1][0] + s, blocks[-1][1] + o, blocks[-1][2] + 1]
        else:
            blocks.append([s, o, 1])
    merged = []
    for b in blocks:
        merged.append(b)
        while len(merged) > 1 and (merged[-2][1] + 1) / (merged[-2][2] + 2) >= (merged[-1][1] + 1) / (merged[-1][2] + 2):
            last = merged.pop()
            merged[-1] = [merged[-1][0] + last[0], merged[-1][1] + last[1], merged[-1][2] + last[2]]
    return {"x": [round(b[0] / b[2], 6) for b in merged], "y": [round((b[1] + 1) / (b[2] + 2), 6) for b in merged],
            "n": [b[2] for b in merged]}


def applied(table: dict | None) -> bool:
    """Is a fitted map used? Only with at least MIN_ITEMS development items in at least MIN_BLOCKS blocks."""
    return bool(table and table.get("x")) and sum(table.get("n") or ()) >= MIN_ITEMS and \
        len(table["x"]) >= MIN_BLOCKS


def calibrate(s: float, table: dict | None) -> float:
    """The probability of score s under a fitted map (linear between block centres, flat beyond the ends)."""
    if not table or not table.get("x"):
        return 1.0 / (1.0 + math.exp(-s))
    xs, ys = table["x"], table["y"]
    if s <= xs[0]:
        return ys[0]
    if s >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if s <= xs[i]:
            t = (s - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


@lru_cache(maxsize=1)
def calibration() -> dict:
    """The shipped calibration (data/monitoring_calibration.json); {} when the file is missing or unreadable, which
    makes every map the logistic of the raw score and selective answering never fire (no threshold)."""
    try:
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def threshold(curve_conf, curve_correct, target_risk=TARGET_RISK) -> float | None:
    """The lowest confidence threshold whose selective risk (wrong share among the answered, ties answered
    together) is at most target_risk; None when no threshold reaches it."""
    pairs = sorted(zip(curve_conf, curve_correct), key=lambda p: -p[0])
    best, answered, wrong, i = None, 0, 0, 0
    while i < len(pairs):
        t = pairs[i][0]
        while i < len(pairs) and pairs[i][0] == t:
            answered += 1
            wrong += 0 if pairs[i][1] else 1
            i += 1
        if wrong / answered <= target_risk:
            best = t
    return best


# ---- the record --------------------------------------------------------------------------------------------------

_PLAIN_STATE = {"disputed": "the sources contradict it", "conflicting": "the sources disagree",
                "insufficient": "too few independent sources state it",
                "temporally_unresolved": "the sources may be out of date for what is asked",
                "not_evaluated": "it is a judgement the sources cannot settle"}


def sufficiency(sig: dict) -> dict:
    """Is the evidence enough, in plain words: {"enough": bool, "why": [reasons], "shown": [notes]}. Only the
    sources decide; what the model did (it found nothing it could say) is a note shown beside them, never a reason
    (Wave 1 review A5: the development data has no model calls)."""
    why = []
    state = sig.get("state")
    if sig.get("kind") == "none":
        why.append("no source stated an answer")
    elif state in _PLAIN_STATE and not (sig.get("kind") == "verdict" and state == "disputed"):
        why.append(_PLAIN_STATE[state])
    if sig.get("kind") != "none" and sig.get("supporting_groups", 0) < MIN_GROUPS and state != "disputed":
        why.append("only one independent source states it" if sig.get("supporting_groups") == 1
                   else "no independent source states it")
    dep = sig.get("independence") or {}
    if dep.get("one_family"):
        why.append("the agreeing sources are one family of sites (" +
                   ", ".join(h for _, hosts in dep.get("families") or [] for h in hosts) + ")")
    if dep.get("circular"):
        why.append("one agreeing source cites another (" +
                   "; ".join(f"{a} links to {b}" for a, b in (dep.get("citations") or [])[:3]) + ")")
    if sig.get("stale") and "the sources may be out of date for what is asked" not in why:
        why.append("no source is recent enough for what is asked")
    guard = sig.get("guard") or {}
    shown = ["the model found nothing it could say from the sources"] if guard.get("abstained") else []
    return {"enough": not why, "why": list(dict.fromkeys(why)), "shown": shown}


def assess(report: dict, *, documents=(), clusters=(), consolidation=None, table: dict | None = None,
           selective: bool = False) -> dict:
    """The monitoring record of one report (see the module docstring). `table` defaults to the shipped calibration;
    `selective` (WebConfig.selective_answering) makes `decision` "not_enough_evidence" when the answer is weak:
    for a fact check, or without an applied map, when sufficiency() says the evidence is not enough; with an applied
    map and the development threshold (both strata applied), below the threshold. `below_threshold` is that
    weakness, whichever basis (`selective_basis`) decided it."""
    table = calibration() if table is None else table
    sig = signals(report, documents=documents, clusters=clusters, consolidation=consolidation)
    parts = components(sig)
    s = score(parts)
    strata = table.get("strata") or {}
    kind = stratum(sig)
    claim_p = calibrate(s, None) if sig["kind"] != "none" else None              # unfitted (module doc)
    mapped = applied(strata.get(kind))
    confidence = (calibrate(answer_score(sig, s), strata.get(kind)) if sig["kind"] != "none" and mapped
                  else None)
    cut = (table.get("threshold") or {}).get("confidence")
    if not all(applied(strata.get(k)) for k in ("verdict", "answer")):
        cut = None                                   # the threshold was chosen on confidences no map now gives
    enough = sufficiency(sig)
    if kind == "answer" and cut is not None and sig["kind"] != "none":
        basis, weak = "threshold", confidence < cut
    else:
        basis, weak = "sufficiency", not enough["enough"]
    decision = "not_enough_evidence" if selective and weak else "answer"
    return {"version": VERSION, "kind": sig["kind"], "statement": sig.get("statement"),
            "confidence": None if confidence is None else round(confidence, 6),
            "confidence_note": NOT_ENOUGH_DATA if sig["kind"] != "none" and not mapped else None,
            "claim_probability": None if claim_p is None else round(claim_p, 6),
            "claim_probability_basis": "the unfitted score (logistic)",
            "score": round(s, 6), "components": {k: round(v, 6) for k, v in parts.items()},
            "signals": {k: v for k, v in sig.items() if k not in ("statement",)},
            "sufficiency": enough, "threshold": cut, "below_threshold": bool(weak), "selective_basis": basis,
            "target_risk": (table.get("threshold") or {}).get("target_risk"),
            "calibration": {"stratum": kind, "applied": mapped,
                            "fitted_on": table.get("fitted_on") or "none (logistic)"},
            "decision": decision, "note": note(enough) if decision == "not_enough_evidence" else None}


def note(enough: dict) -> str:
    """The owner-facing line for an answer below the threshold."""
    why = "; ".join(enough.get("why") or []) or "the evidence found is weak"
    return f"Not enough evidence for a confident answer: {why}. What the sources say is below, as they say it."


def apply_note(report: dict, record: dict) -> None:
    """Selective answering: lead the answer with the plain "not enough evidence" line (a template entry; the quotes
    stay as they are). Does nothing unless record["decision"] is "not_enough_evidence"."""
    if record.get("decision") != "not_enough_evidence" or not record.get("note"):
        return
    report["answer"] = [{"type": "template", "status": "not_enough_evidence", "text": record["note"],
                         "citations": [], "confidence": None}] + list(report.get("answer") or [])


# ---- escalation and depth -----------------------------------------------------------------------------------------

ESCALATION_MIN_SECONDS = 15.0   # time left in the task's budget below which no escalation starts


def escalation(record: dict, *, usage, budget, elapsed: float) -> dict | None:
    """Whether to read one more independent source before answering: below the threshold, a statement to check,
    and the task's own budget still has a page, a query, two requests and ESCALATION_MIN_SECONDS. None otherwise.
    {"statement", "confidence", "threshold", "why"}."""
    if not record.get("below_threshold") or not record.get("statement"):
        return None
    if usage.pages + 1 > budget.max_pages or usage.queries + 1 > budget.max_queries or \
            usage.requests + 2 > budget.max_requests or budget.max_seconds - elapsed < ESCALATION_MIN_SECONDS:
        return None
    return {"statement": record["statement"], "confidence": record.get("confidence"),
            "threshold": record.get("threshold"), "why": (record.get("sufficiency") or {}).get("why") or []}


def verified_count(clusters) -> int:
    """Statements with at least MIN_GROUPS independent supporting groups (cluster status 'corroborated')."""
    return sum(1 for cl in clusters or () if cl.status == "corroborated")


def verified_curve(documents, clusters) -> list[int]:
    """After each page in reading order, how many of the final corroborated statements already had MIN_GROUPS
    independent groups (a group counts from its first page)."""
    from . import claims as C
    order = {}
    for i, d in enumerate(documents or ()):
        for u in (d.canonical_url, d.final_url, d.url):
            if u:
                order.setdefault(u, i)
    reached = []
    for cl in clusters or ():
        if cl.status != "corroborated":
            continue
        support = set(cl.supporting)
        members = [c for c in cl.claims if c.id in support]
        firsts = sorted(min((order.get(c.source_url, len(order)) for c in members if c.id in g), default=len(order))
                        for g in [set(ids) for ids in C.independence_groups(members)])
        if len(firsts) >= MIN_GROUPS:
            reached.append(firsts[MIN_GROUPS - 1])
    return [sum(1 for r in reached if r <= k) for k in range(len(documents or ()))]


def depth(documents, clusters, usage, budget, *, stopped_early=None, escalated=None, guard=None) -> dict:
    """The depth record of a task: what it used of its budget and the verified statements after each page."""
    curve = verified_curve(documents, clusters)
    gains = [curve[0]] + [b - a for a, b in zip(curve, curve[1:])] if curve else []
    tail = 0
    for g in reversed(gains):
        if g:
            break
        tail += 1
    return {"pages": usage.pages, "queries": usage.queries, "requests": usage.requests,
            "max_pages": budget.max_pages, "verified_after_page": curve, "verified_gain_per_page": gains,
            "pages_after_last_gain": tail if curve and curve[-1] else None,
            "guard_refused": (guard or {}).get("refused"), "stopped_early": stopped_early, "escalated": escalated}
