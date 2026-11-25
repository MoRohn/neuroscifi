"""Knowledge evolution: how what the individual knows changes, and how well it predicts (ADR-038).

docs/knowledge/KNOWLEDGE_EVOLUTION.md and docs/knowledge/CONTINUAL_LEARNING.md describe every rule here.

DISCOVER -> UNDERSTAND -> VERIFY -> CONNECT -> CONSOLIDATE -> PREDICT -> REASSESS -> EVOLVE:
- VERIFY       cluster_verdict: the ADR-035 verification state of one scored claim cluster (category policy,
               syndication-aware independent groups, timeframe for time-sensitive statements, authoritative
               records). consolidate.run admits only "supported" clusters as knowledge candidates.
- CONNECT      link/graph: typed edges in web_claim_links (claim -> source, claim -> knowledge as evidence,
               claim -> knowledge contradiction, claim -> validity interval, entity -> entity, hypothesis ->
               prediction -> observation -> error -> revised hypothesis).
- PREDICT      open_hypothesis/predict: research opportunities (provisional claims that are not verified knowledge)
               become hypotheses with a testable prediction: resolution criteria, deadline, forecast probability.
- REASSESS     observe: when a later task brings decisive evidence about a hypothesis, the prediction is settled
               (confirmed / error); an error gets an error analysis, alternative explanations and a revised
               hypothesis. calibration: Brier score and reliability bins over every settled forecast, persisted.
- EVOLVE       metrics (metacognition from real records), changes ("what has changed in your understanding of X"),
               research_policy (procedural learning: bounded, logged, reversible) and the model-adaptation guard
               (no weight change without owner authorisation, isolated evaluation and a rollback plan; nothing in
               this package changes weights).

Provenance kinds (PROVENANCE_KINDS) are kept apart on every record: raw evidence (web_sources), provisional claims
(web_claims, web_hypotheses), verified conclusions (web_knowledge provenance 'verified_conclusion') and
user-asserted facts (web_knowledge provenance 'user_asserted'). Nothing here touches the binary-belief tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError
from . import state

PROVENANCE_KINDS = ("raw_evidence", "provisional_claim", "verified_conclusion", "user_asserted")
PROVENANCE_BY_TABLE = {"web_sources": "raw_evidence", "web_claims": "provisional_claim",
                       "web_hypotheses": "provisional_claim"}       # web_knowledge: its provenance column
HYPOTHESIS_ORIGINS = ("insufficient", "conflicting", "temporally_unresolved", "disputed", "not_evaluated", "gap",
                      "prediction_error", "owner")
HYPOTHESIS_STATES = ("open", "supported", "refuted", "revised", "abandoned")
RESEARCH_OPPORTUNITY_STATES = ("insufficient", "conflicting", "temporally_unresolved", "not_evaluated")
MAX_HYPOTHESES_PER_TASK = 20          # research opportunities recorded per consolidation (bounded growth)
MAX_HORIZON = 30 * 86400.0            # a hypothesis prediction resolves within at most 30 days (else inconclusive)
MIN_FORECAST, MAX_FORECAST = 0.01, 0.99
CALIBRATION_BINS = 10
MAX_ENTITY_EDGES_PER_CLAIM = 10
MAX_GRAPH_EDGES = 500
MIN_LINKED_COVERAGE = 0.5             # a cluster of the hypothesis' own task must name MORE than this share of its key
_DECISIVE = ("supported", "disputed")
H_COLUMNS = ("id", "key", "statement", "origin", "status", "verification", "claims", "claim_values", "polarity",
             "alternatives", "knowledge_id", "parent_id", "task_id", "note", "created_at", "updated_at",
             "resolved_at")
_H_JSON = {"verification": {}, "claims": [], "claim_values": [], "alternatives": []}
# Lane K (ADR-055): directive 7.2's fields, in web_hypothesis_details (absent for hypotheses recorded before it).
K_COLUMNS = ("assumptions", "counter_evidence", "expected_info_gain", "cost", "revisions")
_K_JSON = {"assumptions": [], "counter_evidence": [], "revisions": []}
_H_SELECT = (",".join("h." + c for c in H_COLUMNS) + "," + ",".join("d." + c for c in K_COLUMNS)
             + " FROM web_hypotheses h LEFT JOIN web_hypothesis_details d ON d.hypothesis_id=h.id")
P_COLUMNS = ("id", "knowledge_id", "hypothesis_id", "expectation", "criteria", "probability", "check_after",
             "deadline", "created_at", "task_id", "checked_at", "outcome", "detail")


def _day(ts) -> str | None:
    ts = state.finite(ts)
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _clip(p) -> float | None:
    p = state.finite(p)
    return None if p is None else round(min(MAX_FORECAST, max(MIN_FORECAST, p)), 6)


# ---- VERIFY: the ADR-035 state of a claim cluster -------------------------------------------------------------

def _support_and_contra(cluster, members):
    ids = {c.id for c in members}
    support = set(cluster.supporting) & ids
    head = next((c for c in members if c.id in support), members[0])
    support.add(head.id)
    contra = set()
    for pair in cluster.contradicting:      # claims.evaluate lists the side agreeing with the representative first
        a, b = pair.get("a"), pair.get("b")
        if a in ids and b in ids:
            contra.add(a if b == head.id else b)
    return head, support - contra, contra - {head.id}


def cluster_verdict(cluster, *, now, freshness_class, ttl_overrides=None, window=None, origins=None) -> dict:
    """The verification state of a scored cluster's representative statement under the ADR-035 policy: the
    cluster's own bearing (its supporting claims and the claims its contradiction pairs set against them) decided
    by synthesis.decide, the same rule as the report verdict and the per-claim states the reasoning guard reads.
    Adversarial claims never count. `origins` (claims.document_origins) joins copies of one document; claims also
    carry their origin keys in flags["origin"] (stamped at extraction), so a caller without the documents
    (consolidation) groups syndicated copies the same way.

    {state, category, category_reason, basis, confidence (posterior mean under the category's undated factor),
     independent_groups {supporting, contradicting}, timeframe, policy 'ADR-035', verified_at}."""
    from . import claims as C, synthesis, temporal
    base = {"policy": "ADR-035", "verified_at": now, "basis": None, "timeframe": None, "confidence": 0.5,
            "independent_groups": {"supporting": 0, "contradicting": 0}}
    members = [c for c in cluster.claims if not C.is_adversarial(c)]
    if not members:
        return base | {"state": "not_evaluated", "category": None, "category_reason": None,
                       "reason": "no claim from a non-adversarial source"}
    head, support, contra = _support_and_contra(cluster, members)
    statement = cluster.statement if cluster.statement in {c.text for c in members} else head.text
    kinds = {"academic"} if any((c.flags or {}).get("record") == "academic" for c in members) else set()
    category, why = temporal.claim_category(statement, now=now, freshness_class=freshness_class,
                                            registry=synthesis.release_statement(statement) is not None,
                                            source_kinds=kinds)
    policy = C.CATEGORY_POLICY[category]
    out = base | {"category": category, "category_reason": why}
    if not policy["evaluated"]:
        return out | {"state": "not_evaluated", "reason": f"not evaluated: {why}"}
    relation = {cid: "support" for cid in support} | {cid: "contradict" for cid in contra}
    registry = {c.id for c in members if c.kind == "metadata" and (c.flags or {}).get("record") in ("package", "release")}
    d = synthesis.decide(statement, members, relation, category=category, now=now, cls=freshness_class,
                         ttl_overrides=ttl_overrides, window=window, origins=origins, registry_ids=registry)
    sup, con, mean = d["supporting_groups"], d["contradicting_groups"], d["confidence"]
    reason = (f"{sup} independent group(s) support, {con} contradict; posterior {mean:.3f} "
              f"({category}: {policy['min_groups']} group(s) needed"
              + (", undated sources do not count" if policy["undated_factor"] is None else "") + ")")
    return out | {"state": d["state"], "basis": d["basis"], "confidence": round(mean, 6),
                  "min_groups": policy["min_groups"], "timeframe": d["timeframe"],
                  "independent_groups": {"supporting": sup, "contradicting": con}, "reason": reason}


# ---- CONNECT: typed knowledge-graph edges ---------------------------------------------------------------------

def link(db, task_id, src_kind, src, kind, dst_kind, dst, detail, now, *, unique=True):
    """One typed edge (state.add_link). With `unique` an identical edge of the same task is not written twice.
    Caller owns the transaction."""
    if unique and db.execute("SELECT 1 FROM web_claim_links WHERE task_id IS ? AND src_kind=? AND src=? AND kind=? "
                             "AND dst_kind=? AND dst=? LIMIT 1",
                             (task_id, src_kind, str(src), kind, dst_kind, str(dst))).fetchone():
        return None
    return state.add_link(db, task_id, src, dst, kind, detail, now, src_kind=src_kind, dst_kind=dst_kind)


def record_knowledge_edges(db, task_id, claims, knowledge_id, times, now, *, head=None):
    """claim -> source (derived_from), claim -> knowledge (evidence_for) for every supporting claim, and the head
    claim -> validity interval of the version (valid_during, with its publication, event, retrieval, verification
    and effective times). Caller owns the transaction."""
    for c in claims:
        link(db, task_id, "claim", c.id, "derived_from", "source", c.source_url,
             {"published_at": state.finite(c.published_at), "retrieved_at": state.finite(c.retrieved_at)}, now)
        link(db, task_id, "claim", c.id, "evidence_for", "knowledge", knowledge_id,
             {"event_at": state.finite(c.event_at), "published_at": state.finite(c.published_at)}, now)
    if head is not None:
        link(db, task_id, "claim", head.id, "valid_during", "interval", knowledge_id, dict(times), now)


def record_entity_edges(db, task_id, claim_id, entity_ids, knowledge_id, now) -> int:
    """entity -> entity (related_to) for entities named in one claim: a co-mention, not an ontology relation.
    At most MAX_ENTITY_EDGES_PER_CLAIM per claim. Caller owns the transaction."""
    out = 0
    ids = list(dict.fromkeys(entity_ids))
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if out >= MAX_ENTITY_EDGES_PER_CLAIM:
                return out
            if link(db, task_id, "entity", a, "related_to", "entity", b,
                    {"claim": claim_id, "knowledge_id": knowledge_id, "relation": "co-mentioned"}, now):
                out += 1
    return out


def graph(db, node_kind, node_id, *, depth=1, limit=MAX_GRAPH_EDGES) -> dict:
    """Edges around one node (both directions), breadth-first to `depth` (1..3), at most `limit` edges."""
    if node_kind not in state.NODE_KINDS:
        raise ValidationError(f"node kind must be one of {', '.join(state.NODE_KINDS)}")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 3:
        raise ValidationError("depth must be 1, 2 or 3")
    seen_nodes, frontier, edges, seen_edges = {(node_kind, str(node_id))}, [(node_kind, str(node_id))], [], set()
    for _ in range(depth):
        nxt = []
        for kind, nid in frontier:
            for row in db.execute("SELECT id,task_id,src_kind,src,kind,dst_kind,dst,detail,created_at FROM "
                                  "web_claim_links WHERE (src_kind=? AND src=?) OR (dst_kind=? AND dst=?) ORDER BY id",
                                  (kind, nid, kind, nid)):
                if row[0] in seen_edges:
                    continue
                seen_edges.add(row[0])
                edges.append({"id": row[0], "task_id": row[1], "src": {"kind": row[2], "id": row[3]}, "kind": row[4],
                              "dst": {"kind": row[5], "id": row[6]}, "detail": state.loads(row[7], {}),
                              "at": row[8]})
                for other in ((row[2], row[3]), (row[5], row[6])):
                    if other not in seen_nodes:
                        seen_nodes.add(other)
                        nxt.append(other)
                if len(edges) >= limit:
                    return {"node": {"kind": node_kind, "id": str(node_id)}, "edges": edges, "truncated": True}
        frontier = nxt
    return {"node": {"kind": node_kind, "id": str(node_id)}, "edges": edges, "truncated": False}


# ---- PREDICT: hypotheses and their predictions ----------------------------------------------------------------

def _decode_h(values) -> dict:
    out = dict(zip(H_COLUMNS + K_COLUMNS, values))
    for key, default in (_H_JSON | _K_JSON).items():
        out[key] = state.loads(out[key], default)
    out["provenance"] = "provisional_claim"
    return out


def hypothesis(db, hypothesis_id) -> dict:
    found = db.execute(f"SELECT {_H_SELECT} WHERE h.id=?", (hypothesis_id,)).fetchone()
    if found is None:
        raise NotFound(f"No hypothesis {hypothesis_id}")
    return _decode_h(found)


def hypotheses(db, *, status=None, key=None, limit=100) -> list[dict]:
    if status is not None and status not in HYPOTHESIS_STATES:
        raise ValidationError(f"status must be one of {', '.join(HYPOTHESIS_STATES)}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a positive integer")
    where, params = [], []
    if status:
        where.append("h.status=?")
        params.append(status)
    if key:
        where.append("h.key=?")
        params.append(key)
    sql = " AND ".join(where) or "1"
    return [_decode_h(r) for r in db.execute(f"SELECT {_H_SELECT} WHERE {sql} ORDER BY h.id DESC LIMIT ?",
                                             (*params, int(limit)))]


def _p_row(values) -> dict:
    out = dict(zip(P_COLUMNS, values))
    out["criteria"] = state.loads(out["criteria"], {})
    out["detail"] = state.loads(out["detail"], {})
    return out


def prediction(db, prediction_id) -> dict:
    found = db.execute(f"SELECT {','.join(P_COLUMNS)} FROM web_predictions WHERE id=?", (prediction_id,)).fetchone()
    if found is None:
        raise NotFound(f"No prediction {prediction_id}")
    return _p_row(found)


def predict(db, hypothesis_id, now, *, expectation=None, criteria=None, deadline=None, probability=None) -> int:
    """A testable prediction about a hypothesis: it resolves 'confirmed' when a later task verifies (ADR-035
    "supported") a statement with the hypothesis' values and polarity, 'error' when it verifies conflicting values
    or disputes them, and 'inconclusive' once `deadline` passes without decisive evidence. `probability` is the
    forecast that the statement holds (default: the hypothesis' posterior), used for calibration. Caller owns the
    transaction."""
    h = hypothesis(db, hypothesis_id)
    deadline = state.finite(deadline, now + MAX_HORIZON)
    if deadline <= now:
        raise ValidationError("deadline must be in the future")
    p = _clip(h["verification"].get("confidence") if probability is None else probability)
    if probability is not None and not (0.0 <= float(probability) <= 1.0):
        raise ValidationError("probability must be between 0 and 1")
    criteria = criteria or {"key": h["key"], "values": h["claim_values"], "polarity": h["polarity"],
                            "confirmed_when": "a later task verifies (supported) the same values and polarity",
                            "error_when": "a later task verifies conflicting values or polarity, or disputes it",
                            "inconclusive_when": f"no decisive evidence by {_day(deadline)}"}
    expectation = expectation or (f"«{_quote(h['statement'])}» will be confirmed by independent evidence by "
                                  f"{_day(deadline)}")
    if not isinstance(expectation, str) or not expectation.strip() or len(expectation) > 500:
        raise ValidationError("expectation must be a non-blank string of at most 500 characters")
    pid = db.execute("INSERT INTO web_predictions(hypothesis_id,expectation,criteria,probability,check_after,"
                     "deadline,created_at) VALUES(?,?,?,?,?,?,?)",
                     (hypothesis_id, expectation[:500], state.dumps(criteria), p, now, deadline, now)).lastrowid
    link(db, h["task_id"], "hypothesis", hypothesis_id, "predicts", "prediction", pid,
         {"probability": p, "deadline": deadline}, now)
    state.log_event(db, None, "prediction", {"prediction_id": pid, "hypothesis_id": hypothesis_id,
                                             "deadline": deadline}, now)
    return pid


def _quote(text) -> str:
    from . import knowledge
    return knowledge.quote(text)


def open_hypothesis(db, *, key, statement, origin, verification, claims, values, polarity, now, task_id=None,
                    knowledge_id=None, parent_id=None, note=None, freshness_class="stable", ttl_overrides=None,
                    with_prediction=True) -> tuple[int, bool]:
    """Record a research opportunity (a provisional claim, never knowledge). An open hypothesis with the same key,
    values and polarity is updated (claims merged, latest verification), never duplicated. A new one gets a
    testable prediction (predict) with a deadline of the class TTL, at most MAX_HORIZON. Returns (id, created).
    Caller owns the transaction."""
    from . import temporal
    if origin not in HYPOTHESIS_ORIGINS:
        raise ValidationError(f"origin must be one of {', '.join(HYPOTHESIS_ORIGINS)}")
    if not isinstance(statement, str) or not statement.strip():
        raise ValidationError("statement must be a non-blank string")
    polarity = -1 if polarity is not None and polarity < 0 else 1
    for h in hypotheses(db, status="open", key=key, limit=50):
        if _relation(values, polarity, h["claim_values"], h["polarity"], h.get("statement")) == "same":
            merged = list(dict.fromkeys(h["claims"] + list(claims)))
            db.execute("UPDATE web_hypotheses SET claims=?, verification=?, updated_at=?, "
                       "knowledge_id=coalesce(knowledge_id, ?) WHERE id=?",
                       (state.dumps(merged), state.dumps(verification), now, knowledge_id, h["id"]))
            return h["id"], False
    hid = db.execute("INSERT INTO web_hypotheses(key,statement,origin,status,verification,claims,claim_values,"
                     "polarity,knowledge_id,parent_id,task_id,note,created_at,updated_at) "
                     "VALUES(?,?,?,'open',?,?,?,?,?,?,?,?,?,?)",
                     (key, statement[:2000], origin, state.dumps(verification), state.dumps(list(claims)),
                      state.dumps(list(values or [])), polarity, knowledge_id, parent_id, task_id, note, now,
                      now)).lastrowid
    for cid in claims:
        link(db, task_id, "claim", cid, "evidence_for", "hypothesis", hid, {"origin": origin}, now)
    _describe(db, hid, origin=origin, verification=verification, claims=claims, knowledge_id=knowledge_id,
              parent_id=parent_id, now=now)
    if with_prediction:
        try:
            horizon = min(temporal.ttl(freshness_class, ttl_overrides), MAX_HORIZON)
        except ValidationError:
            horizon = MAX_HORIZON
        predict(db, hid, now, deadline=now + horizon)
    return hid, True


# ---- directive 7.2: what a hypothesis assumes, what speaks against it, what checking it is worth (lane K) ---------
MAX_REVISIONS = 20                    # status changes kept on a hypothesis (the oldest go first)
MAX_COUNTER_EVIDENCE = 20
COMPETITION_DAMPING = 0.5             # likelihood ratios are raised to this power (shrunk toward 1), research.md 4.5
COMPETITION_RELIABILITY = 0.8         # one independent source group's report reliability (curiosity.RELIABILITY)
_ASSUMPTION = {
    "conflicting": "one side of the disagreement is right: the sources do not describe different scopes or periods",
    "disputed": "one side of the disagreement is right: the sources do not describe different scopes or periods",
    "temporally_unresolved": "the statement and its sources refer to the same reference period",
    "insufficient": "a further independent source would report the statement if it holds",
    "gap": "a further independent source would report the statement if it holds",
    "not_evaluated": "the statement can be checked against sources at all",
    "prediction_error": "the observation that refuted the hypothesis it revises was itself correct",
    "owner": "the owner's statement is what is to be tested, as stated",
}


def fields_enabled(db) -> bool:
    return bool(getattr(state.get_config(db), "hypothesis_fields", True))


def _describe(db, hid, *, origin, verification, claims, knowledge_id, parent_id, now):
    """A new hypothesis' assumptions (template statements read from its own records), counter-evidence (the claims
    of the knowledge item it is an alternative to), expected information gain and cost of one investigation (the
    numbers autonomy.candidates ranks it by), and its premises (premises.record). Caller owns the txn."""
    from . import curiosity, premises
    if fields_enabled(db):
        groups = (verification.get("independent_groups") or {}).get("supporting", 0) if verification else 0
        assumptions = [_ASSUMPTION.get(origin, _ASSUMPTION["insufficient"]),
                       f"the {groups} independent source group(s) behind it are not copies of one another",
                       "claims under the same or a near-identical key are about the same subject"]
        counter = []
        if knowledge_id is not None:
            assumptions.append(f"knowledge #{knowledge_id} may be wrong or out of date")
            row = db.execute("SELECT supporting FROM web_knowledge WHERE id=?", (knowledge_id,)).fetchone()
            counter = list(dict.fromkeys(state.loads(row[0], [])))[:MAX_COUNTER_EVIDENCE] if row else []
        if parent_id is not None:
            assumptions.append(f"hypothesis #{parent_id} failed for the reason its error analysis states")
        p = _clip((verification or {}).get("confidence"))
        db.execute("INSERT OR REPLACE INTO web_hypothesis_details(hypothesis_id,assumptions,counter_evidence,"
                   "expected_info_gain,cost,revisions,created_at) VALUES(?,?,?,?,?,?,?)",
                   (hid, state.dumps(assumptions), state.dumps(counter),
                    round(curiosity.info_gain(0.5 if p is None else p), 6), round(curiosity.cost("fact_verification"), 6),
                    state.dumps([{"at": now, "to": "open", "evidence": {"origin": origin, "claims": list(claims)[:20],
                                                                        "task_id": None}}]), now))
    if premises.enabled(db):
        premises.record(db, "hypothesis", hid, [("claim", c, "supported_by") for c in claims]
                        + ([("knowledge", knowledge_id, "alternative_to")] if knowledge_id is not None else [])
                        + ([("hypothesis", parent_id, "revision_of")] if parent_id is not None else []), now)


def record_revision(db, hid, to, evidence, now):
    """Append one evidence-based status change to a hypothesis' revision record (at most MAX_REVISIONS kept).
    Caller owns the txn; nothing while hypothesis_fields is off."""
    if not fields_enabled(db):
        return
    if db.execute("SELECT 1 FROM web_hypotheses WHERE id=?", (hid,)).fetchone() is None:
        return
    row = db.execute("SELECT revisions FROM web_hypothesis_details WHERE hypothesis_id=?", (hid,)).fetchone()
    revisions = (state.loads(row[0] if row else None, []) + [{"at": now, "to": to,
                                                              "evidence": evidence}])[-MAX_REVISIONS:]
    db.execute("INSERT INTO web_hypothesis_details(hypothesis_id,revisions,created_at) VALUES(?,?,?) "
               "ON CONFLICT(hypothesis_id) DO UPDATE SET revisions=excluded.revisions",
               (hid, state.dumps(revisions), now))


def refresh_counter_evidence(db, hids) -> None:
    """Add the claims that contradict each hypothesis (its 'contradicts' edges) to its counter-evidence. Caller owns
    the txn."""
    if not fields_enabled(db):
        return
    for hid in dict.fromkeys(hids or []):
        row = db.execute("SELECT counter_evidence FROM web_hypothesis_details WHERE hypothesis_id=?",
                         (hid,)).fetchone()
        if row is None:         # recorded before lane K's migration, or with the fields off: nothing to extend
            continue
        found = [s for (s,) in db.execute("SELECT DISTINCT src FROM web_claim_links WHERE kind='contradicts' AND "
                                          "dst_kind='hypothesis' AND dst=? ORDER BY id", (str(hid),))]
        merged = list(dict.fromkeys(state.loads(row[0], []) + found))[:MAX_COUNTER_EVIDENCE]
        if merged != state.loads(row[0], []):
            db.execute("UPDATE web_hypothesis_details SET counter_evidence=? WHERE hypothesis_id=?",
                       (state.dumps(merged), hid))


def abandon_if_unsupported(db, hid, now) -> bool:
    """The evidence rule for 'abandoned' (setting hypothesis_abandon): a derived (not the owner's) open hypothesis
    with no open prediction whose every settled prediction expired 'inconclusive' without a single tracked
    observation - nothing about it was found in the time it gave itself. Recorded with that evidence. The owner's
    hypotheses are never abandoned automatically. Caller owns the txn. Returns whether it was abandoned."""
    if not getattr(state.get_config(db), "hypothesis_abandon", False):
        return False
    row = db.execute("SELECT status, origin FROM web_hypotheses WHERE id=?", (hid,)).fetchone()
    if row is None or row[0] != "open" or row[1] == "owner":
        return False
    preds = [_p_row(r) for r in db.execute(f"SELECT {','.join(P_COLUMNS)} FROM web_predictions WHERE "
                                           "hypothesis_id=? ORDER BY id", (hid,))]
    if not preds or any(p["outcome"] != "inconclusive" for p in preds) or \
            any(p["detail"].get("observations") for p in preds):
        return False
    db.execute("UPDATE web_hypotheses SET status='abandoned', resolved_at=?, updated_at=? WHERE id=?", (now, now, hid))
    record_revision(db, hid, "abandoned", {"rule": "no evidence by the deadline of any of its predictions",
                                           "predictions": [p["id"] for p in preds]}, now)
    state.log_event(db, None, "decision", {"hypothesis_id": hid, "abandoned": [p["id"] for p in preds]}, now)
    return True


def compete(db, key, *, damping=COMPETITION_DAMPING, reliability=COMPETITION_RELIABILITY) -> dict:
    """The open hypotheses of one question (claim key) compared, with "other" (none of them) as a member of the set.
    Each hypothesis' recorded evidence - independent source groups for it and against it (its verification) - gives a
    likelihood ratio r/(1-r) per group, deliberately dampened (raised to `damping`, research.md 4.5: elicited and
    lexical likelihoods are over-confident); the prior is uniform. Read-only and display-only: it ranks nothing and
    changes no hypothesis. {key, members: [{hypothesis_id|"other", posterior, support, against}], damping}."""
    lr = (reliability / (1.0 - reliability)) ** damping
    members = []
    for h in hypotheses(db, status="open", key=key, limit=50):
        groups = (h["verification"] or {}).get("independent_groups") or {}
        support, against = int(groups.get("supporting") or 0), int(groups.get("contradicting") or 0)
        members.append({"hypothesis_id": h["id"], "statement": h["statement"], "support": support,
                        "against": against,
                        "weight": lr ** (support - against)})
    if not members:
        return {"key": key, "members": [], "damping": damping}
    members.append({"hypothesis_id": "other", "statement": None, "support": 0, "against": 0, "weight": 1.0})
    total = sum(m["weight"] for m in members)
    for m in members:
        m["posterior"] = round(m.pop("weight") / total, 6)
    members.sort(key=lambda m: (-m["posterior"], str(m["hypothesis_id"])))
    return {"key": key, "members": members, "damping": damping, "reliability": reliability,
            "label": "dampened comparison of the recorded evidence; ranks nothing and decides nothing"}


def _relation(values, polarity, other_values, other_polarity, other_statement=None) -> str:
    """'same' | 'conflict' | 'series' | 'incomparable' (consolidate._compare over plain values). Given the other
    side's statement (a hypothesis's), its stored values are read as this version reads that statement, as a
    knowledge item's are (consolidate._read_values): a forecast saved when "2,900 residents" read as the year 2900
    is settled as a quantity."""
    from .consolidate import _compare
    return _compare(list(values or []), polarity, {"polarity": other_polarity, "statement": other_statement,
                                                   "claim_values": list(other_values or [])})[0]


# ---- REASSESS: settle predictions when new evidence arrives -----------------------------------------------------

def _alternatives(relation, observed_verdict, hyp) -> list[str]:
    """Alternative explanations of a prediction error, from the records only (labelled template text)."""
    out = ["the hypothesis was wrong when it was formed"]
    if relation in ("conflict", "series"):
        out.append("the subject changed between the hypothesis and the observation (a later state supersedes it)")
    if relation == "series":
        out.append("the two statements describe different reference periods or scopes")
    groups = (observed_verdict.get("independent_groups") or {}).get("supporting", 0)
    if groups < 2:
        out.append(f"the observation rests on {groups} independent group(s) and may itself be wrong")
    if (hyp.get("verification") or {}).get("state") in ("conflicting", "disputed"):
        out.append("the sources behind the hypothesis already disagreed; the other side may have been right")
    return out


def _settle(db, pred, outcome, now, *, task_id, observation, analysis=None):
    detail = dict(pred["detail"]) | {"observation": observation}
    if analysis:
        detail["error_analysis"] = analysis
    db.execute("UPDATE web_predictions SET outcome=?, checked_at=?, detail=?, task_id=coalesce(task_id, ?) "
               "WHERE id=?", (outcome, now, state.dumps(detail), task_id, pred["id"]))
    if task_id is not None:
        link(db, task_id, "prediction", pred["id"], "observed", "observation", task_id,
             {"outcome": outcome, "state": observation.get("state")}, now)
    state.log_event(db, task_id, "prediction_error" if outcome == "error" else "feedback",
                    {"prediction_id": pred["id"], "hypothesis_id": pred["hypothesis_id"],
                     "knowledge_id": pred["knowledge_id"], "outcome": outcome}, now)


def _revise(db, pred, hyp, *, task_id, statement, values, polarity, verification, claims, analysis, now,
            freshness_class, ttl_overrides):
    """observation -> error -> revised hypothesis: the error node is the prediction id; the revision states what
    the observation verified (a new hypothesis, parent = the refuted one), itself with a new prediction."""
    link(db, task_id, "observation", task_id, "error_of", "error", pred["id"], {"reason": analysis["reason"]}, now)
    new_id, _ = open_hypothesis(db, key=hyp["key"] if hyp else analysis.get("key", ""), statement=statement,
                                origin="prediction_error", verification=verification, claims=claims, values=values,
                                polarity=polarity, now=now, task_id=task_id, parent_id=hyp["id"] if hyp else None,
                                note=f"revision after prediction #{pred['id']} failed", freshness_class=freshness_class,
                                ttl_overrides=ttl_overrides)
    link(db, task_id, "error", pred["id"], "revised_to", "hypothesis", new_id, {"alternatives": analysis.get(
        "alternatives", [])}, now)
    return new_id


def _linked_hypothesis(db, task_id):
    """The hypothesis id a task was created to investigate (task context 'hypothesis', autonomy.idle), or None."""
    row = db.execute("SELECT checkpoint FROM web_tasks WHERE id=?", (task_id,)).fetchone()
    linked = ((state.loads(row[0], None) or {}).get("context") or {}).get("hypothesis") if row else None
    return linked if isinstance(linked, int) and not isinstance(linked, bool) else None


def _linked_match(hyp, observed):
    """For the hypothesis a task was created for: its decisive (supported / disputed) cluster that names most of
    the hypothesis' key tokens (more than MIN_LINKED_COVERAGE of them; ties by key similarity), else None. The
    research question shifts a cluster's key tokens (claims.claim_key question stems), so the keys of the task's
    clusters and the hypothesis may differ beyond SIMILAR_KEY while naming the same subject."""
    from .consolidate import _key_tokens, _similar
    wanted = _key_tokens(hyp["key"])
    best, rank = None, None
    for cluster, verdict, statement in observed:
        if verdict["state"] not in _DECISIVE or not wanted:
            continue
        coverage = len(wanted & _key_tokens(cluster.key)) / len(wanted)
        if coverage <= MIN_LINKED_COVERAGE:
            continue
        if rank is None or (coverage, _similar(cluster.key, hyp["key"])) > rank:
            best, rank = (cluster, verdict, statement), (coverage, _similar(cluster.key, hyp["key"]))
    return best


def observe(db, task_id, observed, now, *, freshness_class="stable", ttl_overrides=None, knowledge_by_key=None) \
        -> list[dict]:
    """Compare the open hypothesis predictions with a task's verified evidence. `observed`: [(cluster, verdict,
    statement)] of the task. A prediction of the hypothesis this task was created for (task context 'hypothesis':
    explicit linkage, _linked_match) or, failing that, formed by an earlier task whose hypothesis key matches
    (equal or near-identical: the lexical fallback) a cluster verified 'supported' here is settled: same values
    and polarity -> confirmed, else error (error analysis, alternatives, revised hypothesis); a 'disputed' cluster
    stating the hypothesis' own values -> error. Other observations are tracked on the prediction and it stays
    open. Predictions past their deadline become 'inconclusive'. Caller owns the transaction. Returns the
    settlements."""
    from .consolidate import SIMILAR_KEY, _similar, _values
    out = []
    knowledge_by_key = knowledge_by_key or {}
    linked = _linked_hypothesis(db, task_id)
    rows = db.execute(f"SELECT {','.join('p.' + c for c in P_COLUMNS)} FROM web_predictions p JOIN web_hypotheses h "
                      "ON h.id=p.hypothesis_id WHERE p.outcome IS NULL AND h.status='open' AND "
                      "(h.task_id IS NULL OR h.task_id<>?) ORDER BY p.id", (task_id,)).fetchall()
    for pred in map(_p_row, rows):
        hyp = hypothesis(db, pred["hypothesis_id"])
        match = None
        if hyp["id"] == linked:
            match, how = _linked_match(hyp, observed), "linked"
        if match is None:
            how = "lexical"
            for cluster, verdict, statement in observed:
                if cluster.key == hyp["key"] or _similar(cluster.key, hyp["key"]) >= SIMILAR_KEY:
                    match = (cluster, verdict, statement)
                    if verdict["state"] in _DECISIVE:
                        break
        if match is None:
            continue
        cluster, verdict, statement = match
        values, polarity = _values(cluster)
        relation = _relation(values, polarity, hyp["claim_values"], hyp["polarity"], hyp.get("statement"))
        observation = {"task_id": task_id, "state": verdict["state"], "basis": verdict.get("basis"),
                       "statement": statement, "values": values, "polarity": polarity, "relation": relation,
                       "confidence": verdict.get("confidence"), "at": now, "match": how, "key": cluster.key}
        if verdict["state"] == "supported" and relation == "same":
            outcome = "confirmed"
        elif (verdict["state"] == "supported" and relation in ("conflict", "series")) or \
                (verdict["state"] == "disputed" and relation == "same"):
            outcome = "error"
        else:                                       # tracked, not decisive
            detail = dict(pred["detail"])
            detail.setdefault("observations", []).append(observation)
            detail["observations"] = detail["observations"][-10:]
            db.execute("UPDATE web_predictions SET detail=? WHERE id=?", (state.dumps(detail), pred["id"]))
            continue
        support = [c.id for c in cluster.claims if c.id in set(cluster.supporting)] or [c.id for c in cluster.claims]
        if outcome == "confirmed":
            _settle(db, pred, outcome, now, task_id=task_id, observation=observation)
            db.execute("UPDATE web_hypotheses SET status='supported', resolved_at=?, updated_at=?, "
                       "knowledge_id=coalesce(?, knowledge_id), verification=? WHERE id=?",
                       (now, now, knowledge_by_key.get(cluster.key), state.dumps(verdict), hyp["id"]))
            record_revision(db, hyp["id"], "supported", {"prediction_id": pred["id"], "task_id": task_id,
                                                         "state": verdict["state"], "relation": relation}, now)
            out.append({"prediction_id": pred["id"], "hypothesis_id": hyp["id"], "outcome": outcome})
            continue
        reason = (f"expected {hyp['claim_values'] or 'the statement'} (polarity {hyp['polarity']:+d}); the "
                  f"observation {'verified' if verdict['state'] == 'supported' else 'disputed'} "
                  f"{values or 'the statement'} (polarity {polarity:+d}, relation {relation})")
        analysis = {"reason": reason, "expected": {"values": hyp["claim_values"], "polarity": hyp["polarity"]},
                    "observed": {"values": values, "polarity": polarity, "state": verdict["state"]},
                    "probability": pred["probability"], "alternatives": _alternatives(relation, verdict, hyp),
                    "label": "template analysis from the stored records"}
        _settle(db, pred, outcome, now, task_id=task_id, observation=observation, analysis=analysis)
        db.execute("UPDATE web_hypotheses SET status=?, resolved_at=?, updated_at=?, alternatives=? WHERE id=?",
                   ("refuted" if verdict["state"] == "disputed" else "revised", now, now,
                    state.dumps(analysis["alternatives"]), hyp["id"]))
        record_revision(db, hyp["id"], "refuted" if verdict["state"] == "disputed" else "revised",
                        {"prediction_id": pred["id"], "task_id": task_id, "state": verdict["state"],
                         "relation": relation}, now)
        revised = None
        if verdict["state"] == "supported":
            revised = _revise(db, pred, hyp, task_id=task_id, statement=statement, values=values, polarity=polarity,
                              verification=verdict, claims=support, analysis=analysis, now=now,
                              freshness_class=freshness_class, ttl_overrides=ttl_overrides)
        else:
            link(db, task_id, "observation", task_id, "error_of", "error", pred["id"], {"reason": reason}, now)
        out.append({"prediction_id": pred["id"], "hypothesis_id": hyp["id"], "outcome": outcome,
                    "revised_hypothesis": revised})
    out += expire(db, now)
    if out:
        record_calibration(db, now)
    return out


def expire(db, now) -> list[dict]:
    """Open hypothesis predictions past their deadline -> 'inconclusive' (no decisive evidence in time); the
    hypothesis stays open as a research opportunity. Caller owns the transaction."""
    out = []
    for pred in map(_p_row, db.execute(f"SELECT {','.join(P_COLUMNS)} FROM web_predictions WHERE outcome IS NULL "
                                       "AND hypothesis_id IS NOT NULL AND deadline IS NOT NULL AND deadline<=? "
                                       "ORDER BY id", (now,)).fetchall()):
        _settle(db, pred, "inconclusive", now, task_id=None,
                observation={"state": None, "reason": f"no decisive evidence by {_day(pred['deadline'])}",
                             "at": now})
        out.append({"prediction_id": pred["id"], "hypothesis_id": pred["hypothesis_id"], "outcome": "inconclusive"})
    for item in out:                    # lane K: the evidence rule for 'abandoned' (setting hypothesis_abandon)
        if abandon_if_unsupported(db, item["hypothesis_id"], now):
            item["abandoned"] = True
    return out


def knowledge_prediction_error(db, pred, item, reason, consolidation, now) -> dict:
    """Error analysis of a failed 'still holds' prediction about a knowledge item (predictions.check): links
    prediction -> observation (the refresh task) -> error, and error -> the revision: the superseding knowledge
    version, or a hypothesis stating the contradicting claim. Caller owns the transaction."""
    task_id = (consolidation or {}).get("task_id") or pred.get("task_id")
    analysis = {"reason": reason, "probability": pred.get("probability"), "label": "template analysis from the "
                "stored records", "alternatives": ["the item was wrong when it was verified",
                                                   "the subject changed after it was verified",
                                                   "the contradicting evidence may itself be wrong"]}
    obs = task_id or f"prediction:{pred['id']}"
    link(db, task_id, "prediction", pred["id"], "observed", "observation", obs, {"outcome": "error"}, now)
    link(db, task_id, "observation", obs, "error_of", "error", pred["id"], {"reason": reason}, now)
    if item is None:
        return analysis
    if item.get("superseded_by"):
        link(db, task_id, "error", pred["id"], "revised_to", "knowledge", item["superseded_by"],
             {"reason": "superseded"}, now)
        analysis["revision"] = {"knowledge_id": item["superseded_by"]}
        return analysis
    against = [a for a in item.get("contradicting") or [] if isinstance(a, dict) and a.get("text")]
    if against:
        last = against[-1]
        hid, _ = open_hypothesis(db, key=item["key"], statement=last["text"], origin="prediction_error",
                                 verification={"policy": "ADR-035", "state": "conflicting", "basis": "contradiction",
                                               "confidence": 0.5}, claims=[last["claim"]] if last.get("claim") else [],
                                 values=[], polarity=1, now=now, task_id=task_id, knowledge_id=item["id"],
                                 note=f"alternative to knowledge #{item['id']} after prediction #{pred['id']} failed",
                                 freshness_class=item.get("freshness_class") or "stable")
        link(db, task_id, "error", pred["id"], "revised_to", "hypothesis", hid, {"reason": "contradicted"}, now)
        analysis["revision"] = {"hypothesis_id": hid}
    return analysis


# ---- calibration --------------------------------------------------------------------------------------------

def _counted(alias=""):
    """SQL: the prediction's settlement still stands on an observation the individual holds. A task the owner
    forgot takes its observation with it (governance._scrub_observations leaves {"forgotten": true}): the outcome
    stays on the prediction (ADR-029), but it no longer counts as evidence of how the forecasts fare (ADR-048)."""
    column = f"{alias}detail"
    return (f"coalesce(CASE WHEN json_valid({column}) THEN json_extract({column},'$.observation.forgotten') END, 0)"
            " IS NOT 1")


def calibration(db, *, since=None) -> dict:
    """Brier score and reliability bins over every settled prediction with a forecast probability: outcome
    'confirmed' = 1, 'error' = 0 ('inconclusive' is excluded: nothing was observed). Skill against the base rate
    (climatology) and the expected calibration error (bin-weighted |mean forecast - observed frequency|).
    A settlement whose observation went with a forgotten task does not count (_counted)."""
    since = state.finite(since, 0.0)
    rows = db.execute("SELECT probability, outcome FROM web_predictions WHERE outcome IN ('confirmed','error') AND "
                      f"probability IS NOT NULL AND coalesce(checked_at, 0)>=? AND {_counted()}", (since,)).fetchall()
    n = len(rows)
    inconclusive = db.execute("SELECT count(*) FROM web_predictions WHERE outcome='inconclusive' AND "
                              f"coalesce(checked_at,0)>=? AND {_counted()}", (since,)).fetchone()[0]
    out = {"n": n, "inconclusive": inconclusive, "brier": None, "base_rate": None, "brier_reference": None,
           "skill": None, "ece": None, "bins": []}
    if not n:
        return out
    ys = [1.0 if o == "confirmed" else 0.0 for _, o in rows]
    ps = [float(p) for p, _ in rows]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    base = sum(ys) / n
    ref = base * (1 - base)
    bins, ece = [], 0.0
    for b in range(CALIBRATION_BINS):
        lo, hi = b / CALIBRATION_BINS, (b + 1) / CALIBRATION_BINS
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == CALIBRATION_BINS - 1 and p == 1.0)]
        if not idx:
            continue
        mean_p = sum(ps[i] for i in idx) / len(idx)
        freq = sum(ys[i] for i in idx) / len(idx)
        ece += len(idx) / n * abs(mean_p - freq)
        bins.append({"lower": lo, "upper": hi, "n": len(idx), "mean_probability": round(mean_p, 6),
                     "observed_frequency": round(freq, 6)})
    return out | {"brier": round(brier, 6), "base_rate": round(base, 6), "brier_reference": round(ref, 6),
                  "skill": round(1 - brier / ref, 6) if ref > 0 else None, "ece": round(ece, 6), "bins": bins}


# ---- recalibration: what the settled forecasts say about the next one (ADR-046) ---------------------------------
# A forecast is a number some rule produced: the confidence of a knowledge item, the posterior of a hypothesis, the
# owner's own estimate. calibration() measures how such numbers fared; this maps the next forecast of the same kind
# through what was measured:  logit q = logit p + shift + tilt * (logit p - centre).
# shift = 0 and tilt = 0 is the forecast as stated, and that is where the estimate starts: it is the maximum of the
# log-likelihood of the settled outcomes under a Gaussian prior on shift and tilt around 0, so a few settlements move
# it little, and below RECALIBRATION_MINIMUM settlements it is not moved at all. Computed on read from the settled
# rows of web_predictions (the rows calibration() reads): no stored parameter, so a prediction a forget removes, or
# one whose settling task the owner forgot, stops counting at once. docs/knowledge/KNOWLEDGE_EVOLUTION.md section 5 has the measurements behind the numbers.

RECALIBRATION_KINDS = ("knowledge", "hypothesis", "owner")   # one mapping per kind of forecaster
# The four numbers below were chosen on the development streams of bench/calibration.py (the lowest mean distance
# to the true probability among the settings that never made a stream worse by more than 0.002) and are reported
# on its held-out streams: docs/quality/evidence/open_items_calibration.json.
RECALIBRATION_MINIMUM = 20        # settled forecasts of a kind before any forecast of that kind is adjusted
RECALIBRATION_WINDOW = 100        # only the most recent settlements count (a forecaster's habits change)
RECALIBRATION_PRIOR_SHIFT = 16.0  # prior precision of shift: about 64 settlements at even odds weigh as much
RECALIBRATION_PRIOR_TILT = 16.0   # prior precision of tilt, in the same units
RECALIBRATION_TILT = (-1.0, 1.0)  # slope 1 + tilt stays in [0, 2]: the mapping never reverses an order
# A sudden change of a forecaster's habits (ADR-048): the window is tested for a split every STEP settlements; above
# THRESHOLD (twice the log-likelihood gain of fitting the two parts apart) the older part is dropped. None: off.
RECALIBRATION_CHANGE_STEP = 10
RECALIBRATION_CHANGE_THRESHOLD = None
IDENTITY = {"applied": False, "shift": 0.0, "tilt": 0.0, "centre": 0.0}


def _logit(p: float) -> float:
    p = min(MAX_FORECAST, max(MIN_FORECAST, float(p)))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def _solve(used, prior_shift, prior_tilt):
    """(shift, tilt, centre) maximising the log-likelihood of `used` [(p, y)] under the Gaussian prior. Newton steps
    from the identity on a concave objective, forecasts grouped by value; prior_tilt None fits the shift alone."""
    groups: dict = {}
    for p, y in used:
        g = groups.setdefault(round(_logit(p), 9), [0.0, 0.0])
        g[0] += 1.0
        g[1] += y
    centre = sum(z * g[0] for z, g in groups.items()) / len(used)
    rows = [(z, z - centre, g[0], g[1]) for z, g in sorted(groups.items())]
    lo, hi = RECALIBRATION_TILT
    tilt_weight = 0.0 if prior_tilt is None else float(prior_tilt)
    a = c = 0.0
    for _ in range(60):
        ga, gc, haa, hac, hcc = -prior_shift * a, -tilt_weight * c, float(prior_shift), 0.0, tilt_weight
        for z, d, k, wins in rows:
            s = _sigmoid(z + a + c * d)
            w = k * s * (1.0 - s)
            ga += wins - k * s
            gc += (wins - k * s) * d
            haa += w
            hac += w * d
            hcc += w * d * d
        det = haa * hcc - hac * hac
        if prior_tilt is None or det <= 1e-12 or (c <= lo and gc < 0) or (c >= hi and gc > 0):
            step_a, step_c = ga / haa, 0.0                         # the tilt is fixed: one unknown
        else:
            step_a, step_c = (ga * hcc - gc * hac) / det, (gc * haa - ga * hac) / det
        step_a = max(-2.0, min(2.0, step_a))                       # damped: the first steps can be long
        new_c = max(lo, min(hi, c + max(-0.5, min(0.5, step_c))))
        moved = abs(step_a) + abs(new_c - c)
        a, c = a + step_a, new_c
        if moved < 1e-10:
            break
    return a, c, centre


def _loglik(used, a, c, centre):
    """Log-likelihood of the outcomes of `used` under the mapping (shift a, tilt c, centre)."""
    total = 0.0
    for p, y in used:
        z = _logit(p)
        q = min(1.0 - 1e-12, max(1e-12, _sigmoid(z + a + c * (z - centre))))
        total += math.log(q) if y else math.log(1.0 - q)
    return total


def _change_point(used, step, prior_shift, prior_tilt):
    """(cut, statistic): the split of `used` into an earlier and a later part, at a multiple of `step` with at least
    `step` settlements on each side, that most improves the log-likelihood of fitting the two parts apart over
    fitting them together (twice the gain); (None, 0.0) when no split improves it."""
    whole = _loglik(used, *_solve(used, prior_shift, prior_tilt))
    best, best_stat = None, 0.0
    for cut in range(step, len(used) - step + 1, step):
        before, after = used[:cut], used[cut:]
        stat = 2.0 * (_loglik(before, *_solve(before, prior_shift, prior_tilt))
                      + _loglik(after, *_solve(after, prior_shift, prior_tilt)) - whole)
        if stat > best_stat + 1e-12:
            best, best_stat = cut, stat
    return best, best_stat


def fit_recalibration(pairs, *, minimum=RECALIBRATION_MINIMUM, window=RECALIBRATION_WINDOW,
                      prior_shift=RECALIBRATION_PRIOR_SHIFT, prior_tilt=RECALIBRATION_PRIOR_TILT,
                      change_step=RECALIBRATION_CHANGE_STEP, change_threshold=RECALIBRATION_CHANGE_THRESHOLD) -> dict:
    """The mapping for one kind of forecast from its settled (forecast, outcome) pairs, oldest first; outcome 1 =
    confirmed, 0 = error. {n (pairs given), used (the last `window`, or fewer after a change), minimum, applied,
    shift, tilt, centre}; applied False (fewer than `minimum` pairs used) is the identity. Deterministic.

    A forecaster's habits can change at once (a source it trusted turns unreliable): then what the window learned
    describes the old habits and reads the next forecasts worse than they are stated. With change_threshold set,
    the window is split where fitting its two parts apart gains most (_change_point); when twice that gain exceeds
    the threshold, only the settlements after the split are used, and below `minimum` of them the mapping is the
    identity again until enough have settled. `change` then says where and how strongly (ADR-048)."""
    pairs = [(float(p), 1.0 if y else 0.0) for p, y in pairs if state.finite(p) is not None]
    used = pairs[-int(window):] if window else pairs
    out = {"n": len(pairs), "used": len(used), "minimum": int(minimum)} | IDENTITY
    if not used or len(used) < minimum:
        return out
    if change_threshold is not None and change_step and len(used) >= 2 * change_step:
        cut, stat = _change_point(used, int(change_step), prior_shift, prior_tilt)
        if cut is not None and stat > change_threshold:
            used = used[cut:]
            out |= {"used": len(used), "change": {"dropped": cut, "statistic": round(stat, 3)}}
            if len(used) < minimum:
                return out
    a, c, centre = _solve(used, prior_shift, prior_tilt)
    return out | {"applied": True, "shift": round(a, 6), "tilt": round(c, 6), "centre": round(centre, 6)}


def recalibrate(p, fit) -> float | None:
    """The forecast `p` as the mapping `fit` (fit_recalibration) reads it, inside [MIN_FORECAST, MAX_FORECAST];
    `p` itself when the mapping is the identity, None for no forecast."""
    p = state.finite(p)
    if p is None or not fit or not fit.get("applied"):
        return p
    z = _logit(p)
    q = _sigmoid(z + fit["shift"] + fit["tilt"] * (z - fit["centre"]))
    return round(min(MAX_FORECAST, max(MIN_FORECAST, q)), 6)


_KIND_SQL = ("CASE WHEN p.knowledge_id IS NOT NULL THEN 'knowledge' WHEN h.origin='owner' THEN 'owner' "
             "ELSE 'hypothesis' END")


def settled_forecasts(db) -> dict:
    """{kind: [(forecast, outcome 1 or 0), ...] oldest first} of every settled prediction with a forecast: the
    rows calibration() scores, by who made the forecast ('knowledge': an item's confidence; 'hypothesis': a
    derived hypothesis' posterior; 'owner': the owner's own estimate, `nsf knowledge hypothesize`)."""
    out = {kind: [] for kind in RECALIBRATION_KINDS}
    for kind, p, outcome in db.execute(
            f"SELECT {_KIND_SQL}, p.probability, p.outcome FROM web_predictions p LEFT JOIN web_hypotheses h ON "
            "h.id=p.hypothesis_id WHERE p.outcome IN ('confirmed','error') AND p.probability IS NOT NULL "
            f"AND {_counted('p.')} ORDER BY coalesce(p.checked_at, 0), p.id"):
        out[kind].append((float(p), 1.0 if outcome == "confirmed" else 0.0))
    return out


def calibrated_forecasts_enabled(db) -> bool:
    return bool(getattr(state.get_config(db), "calibrated_forecasts", True))


def recalibrations(db) -> dict:
    """{kind: mapping} from the settled forecasts as they are stored now. With the setting `calibrated_forecasts`
    off every mapping is the identity (and says so)."""
    if not calibrated_forecasts_enabled(db):
        return {kind: {"n": None, "used": 0, "minimum": RECALIBRATION_MINIMUM, "off": True} | IDENTITY
                for kind in RECALIBRATION_KINDS}
    return {kind: fit_recalibration(pairs) for kind, pairs in settled_forecasts(db).items()}


def prediction_kind(db, prediction_id) -> str | None:
    """Whose forecast a prediction holds (RECALIBRATION_KINDS), or None for no such prediction."""
    row = db.execute(f"SELECT {_KIND_SQL} FROM web_predictions p LEFT JOIN web_hypotheses h ON h.id=p.hypothesis_id "
                     "WHERE p.id=?", (prediction_id,)).fetchone()
    return row[0] if row else None


def prequential(pairs, **fit_options) -> dict:
    """How the mapping would have fared on these settled pairs had it been in use all along: each forecast is
    mapped with what the pairs before it give, then scored against its own outcome. {n, adjusted (forecasts the
    mapping changed), brier_stated, brier_mapped}; the two scores are equal while nothing is adjusted."""
    stated = mapped = 0.0
    adjusted = 0
    for i, (p, y) in enumerate(pairs):
        fit = fit_recalibration(pairs[:i], **fit_options)
        adjusted += bool(fit["applied"])
        stated += (p - y) ** 2
        mapped += (recalibrate(p, fit) - y) ** 2
    n = len(pairs)
    return {"n": n, "adjusted": adjusted, "brier_stated": round(stated / n, 6) if n else None,
            "brier_mapped": round(mapped / n, 6) if n else None}


def recalibration_report(db) -> dict:
    """For `nsf knowledge calibration`: per kind of forecast the mapping in force, what it makes of a few stated
    forecasts, and how it would have fared on this individual's own settled predictions (prequential)."""
    fits, settled = recalibrations(db), settled_forecasts(db)
    kinds = {}
    for kind in RECALIBRATION_KINDS:
        fit, pairs = fits[kind], settled[kind][-RECALIBRATION_WINDOW:]
        kinds[kind] = {"settled": len(settled[kind]), "mapping": fit,
                       "reads": {f"{p:.1f}": recalibrate(p, fit) for p in (0.1, 0.3, 0.5, 0.7, 0.9)},
                       "had_it_been_used": prequential(pairs) if pairs else None}
    return {"enabled": calibrated_forecasts_enabled(db), "minimum": RECALIBRATION_MINIMUM,
            "window": RECALIBRATION_WINDOW, "kinds": kinds,
            "used_by": "the order of autonomous research among the candidates admitted from their stated "
                       "forecasts (autonomy.candidates: the uncertainty of a forecast and the information gain "
                       "that ranks a hypothesis; never min_gain). Stored forecasts, verification states, verdicts "
                       "and answers are not changed by it.",
            "off": "nsf web config --set calibrated_forecasts=false"}


def _snapshot(db, kind, value, now) -> int:
    return db.execute("INSERT INTO web_metrics(kind,value,computed_at) VALUES(?,?,?)",
                      (kind, state.dumps(value), now)).lastrowid


def record_calibration(db, now) -> int:
    """Persist the current calibration (web_metrics kind 'calibration'). Caller owns the transaction."""
    return _snapshot(db, "calibration", calibration(db), now)


def calibration_history(db, *, limit=20) -> list[dict]:
    return [{"id": i, "computed_at": t} | state.loads(v, {}) for i, v, t in db.execute(
        "SELECT id, value, computed_at FROM web_metrics WHERE kind='calibration' ORDER BY id DESC LIMIT ?",
        (int(limit),))]


# ---- EVOLVE: metacognitive metrics ----------------------------------------------------------------------------

def metrics(db, now, *, since=None, record=False) -> dict:
    """Metacognition computed from the stored records (never "how much is stored"):
    verified knowledge gained, research opportunities (gaps) resolved, contradictions clarified, prediction
    accuracy and calibration, retrieval effectiveness (memory-answered share of finished tasks), research
    efficiency (network requests per verified claim) and information gain (bits, from web_feedback).
    `since` (seconds) limits every count to records created/settled since then. `record` stores the snapshot."""
    since = state.finite(since, 0.0)
    one = lambda sql, *p: db.execute(sql, (*p,)).fetchone()[0] or 0  # noqa: E731
    verified = one("SELECT count(*) FROM web_knowledge WHERE provenance='verified_conclusion' AND "
                   "json_extract(verification,'$.state')='supported' AND created_at>=?", since)
    legacy = one("SELECT count(*) FROM web_knowledge WHERE provenance='verified_conclusion' AND "
                 "json_extract(verification,'$.policy')='ADR-026' AND created_at>=?", since)
    accepted = one("SELECT count(*) FROM web_knowledge WHERE status='accepted' AND provenance='verified_conclusion' "
                   "AND updated_at>=?", since)
    strengthened = one("SELECT count(*) FROM web_feedback WHERE effect='strengthened' AND created_at>=?", since)
    bits = float(one("SELECT sum(info_gain) FROM web_feedback WHERE created_at>=?", since))
    gaps_opened = one("SELECT count(*) FROM web_hypotheses WHERE created_at>=?", since)
    gaps_resolved = one("SELECT count(*) FROM web_hypotheses WHERE status IN ('supported','refuted','revised') AND "
                        "resolved_at>=?", since)
    gaps_open = one("SELECT count(*) FROM web_hypotheses WHERE status='open'")
    recorded = one("SELECT count(*) FROM web_feedback WHERE json_extract(detail,'$.contested') AND created_at>=?",
                   since)
    clarified_q = one("SELECT count(*) FROM pending WHERE scope LIKE 'web:contested:%' AND resolved_at>=? AND "
                      "question<>'#forgotten'", since)
    reversed_ = one("SELECT count(*) FROM web_feedback WHERE effect='reversed' AND created_at>=?", since)
    clarified_h = one("SELECT count(*) FROM web_hypotheses WHERE origin IN ('conflicting','disputed') AND status IN "
                      "('supported','refuted','revised') AND resolved_at>=?", since)
    outcomes = dict(db.execute("SELECT outcome, count(*) FROM web_predictions WHERE outcome IS NOT NULL AND "
                               f"coalesce(checked_at,0)>=? AND {_counted()} GROUP BY outcome", (since,)).fetchall())
    uncounted = one("SELECT count(*) FROM web_predictions WHERE outcome IS NOT NULL AND coalesce(checked_at,0)>=? "
                    f"AND NOT ({_counted()})", since)
    decided = outcomes.get("confirmed", 0) + outcomes.get("error", 0)
    finished = db.execute("SELECT usage, json_extract(result,'$.memory.answered_from_memory') FROM web_tasks WHERE "
                          "status IN ('completed','partial') AND coalesce(finished_at, updated_at)>=?",
                          (since,)).fetchall()
    requests = sum(int(state.finite((state.loads(u, {}) or {}).get("requests"), 0)) for u, _ in finished)
    from_memory = sum(1 for _, m in finished if m)
    verified_claims = verified + strengthened
    out = {
        "as_of": now, "since": since or None,
        "verified_knowledge": {"gained": verified, "owner_accepted": accepted, "strengthened": strengthened,
                               "pre_adr035_versions": legacy},
        "gaps": {"opened": gaps_opened, "resolved": gaps_resolved, "open": gaps_open},
        "contradictions": {"recorded": recorded, "clarified": clarified_q + reversed_ + clarified_h,
                           "owner_answered": clarified_q, "superseded": reversed_, "hypotheses_resolved": clarified_h},
        "predictions": {"confirmed": outcomes.get("confirmed", 0), "error": outcomes.get("error", 0),
                        "inconclusive": outcomes.get("inconclusive", 0),
                        "open": one("SELECT count(*) FROM web_predictions WHERE outcome IS NULL"),
                        "accuracy": round(outcomes.get("confirmed", 0) / decided, 6) if decided else None,
                        "settled_by_forgotten_tasks": uncounted,
                        "calibration": calibration(db, since=since)},
        "retrieval": ({"measurable": True, "finished_tasks": len(finished), "answered_from_memory": from_memory,
                       "memory_answer_rate": round(from_memory / len(finished), 6)} if finished else
                      {"measurable": False, "reason": "no finished research task in the window"}),
        "research_efficiency": {"requests": requests, "verified_claims": verified_claims,
                                "requests_per_verified_claim": round(requests / verified_claims, 3)
                                if verified_claims else None},
        "information_gain_bits": round(bits, 6),
        "not_metrics": "Counts of stored sources, claims or rows are deliberately not reported: more stored data "
                       "is not more understanding.",
    }
    if record:
        with store.write(db):
            out["snapshot_id"] = _snapshot(db, "evolution", out, now)
            store.bump_state_version(db)
    return out


# ---- "What has changed in your understanding of X?" -----------------------------------------------------------

def _keys_for(db, topic, limit):
    from . import text
    terms = text.content_tokens(topic)
    rows = db.execute("SELECT key, group_concat(statement, ' ') FROM (SELECT key, statement FROM web_knowledge "
                      "UNION ALL SELECT key, statement FROM web_hypotheses) GROUP BY key").fetchall()
    if not terms or not rows:
        return []
    scores = text.BM25([text.content_tokens(f"{s} {k.replace('<', ' ').replace('>', ' ')}")
                        for k, s in rows]).scores(terms)
    ranked = sorted(((s, k) for s, (k, _) in zip(scores, rows) if s > 0), key=lambda x: (-x[0], x[1]))
    return [k for _, k in ranked[:limit]]


def _ev(at, kind, text, **detail):
    return {"at": at, "date": _day(at), "kind": kind, "text": text} | detail


def changes(db, topic, now, *, since=None, limit=5, phrase=False, gateway=None) -> dict:
    """What has changed in the understanding of `topic`: for each matching claim key (BM25 over every version and
    hypothesis, any status), the version history with its times and verification, supersessions, new evidence
    (feedback rows), contradictions, owner decisions, prediction outcomes and hypothesis revisions, as one dated
    timeline. `summary` is template text from these records; with `phrase` and a configured, reachable local
    inference gateway a model may rephrase it from the returned records only, labelled 'model:<name>'."""
    from . import knowledge
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 2000:
        raise ValidationError("topic must be a non-blank string of at most 2000 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValidationError("limit must be an integer between 1 and 50")
    since = state.finite(since, 0.0)
    subjects = []
    for key in _keys_for(db, topic, limit):
        rows = knowledge.rows(db, "key=?", (key,))
        timeline, versions = [], []
        for r in rows:
            v = state.loads(r.get("verification"), {}) if isinstance(r.get("verification"), str) else \
                (r.get("verification") or {})
            versions.append({"id": r["id"], "version": r["version"], "statement": r["statement"],
                             "status": r["status"], "provenance": r.get("provenance"),
                             "verification": {k: v.get(k) for k in ("policy", "state", "category", "basis",
                                                                     "confidence", "independent_groups")},
                             "confidence": r["confidence"],
                             "valid_from": r["valid_from"], "valid_until": r["valid_until"],   # T1-18
                             "times": {"published_at": r.get("published_at"), "event_at": r.get("event_at"),
                                       "retrieved_at": r.get("retrieved_at"),
                                       "first_verified_at": r["first_verified_at"],
                                       "last_verified_at": r["last_verified_at"], "effective_at": r["valid_from"],
                                       "valid_from": r["valid_from"], "valid_until": r["valid_until"]},
                             "superseded_by": r["superseded_by"]})
            how = v.get("state") and f"{v.get('state')} by {v.get('basis') or 'n/a'} ({v.get('policy')})" or \
                f"verified under {v.get('policy') or 'ADR-026'}"
            timeline.append(_ev(r["created_at"], "version", f"v{r['version']} «{_quote(r['statement'])}» recorded as "
                                f"{r['status'] if r['status'] != 'superseded' else 'knowledge'}; {how}",
                                knowledge_id=r["id"], provenance=r.get("provenance")))
            if r["superseded_by"]:
                timeline.append(_ev(r["valid_until"] or r["updated_at"], "superseded",
                                    f"v{r['version']} superseded by knowledge #{r['superseded_by']}",
                                    knowledge_id=r["id"], superseded_by=r["superseded_by"]))
            for pid, question, resolution, resolved in db.execute(
                    "SELECT id, question, resolution, resolved_at FROM pending WHERE scope=? AND resolved_at IS NOT "
                    "NULL AND question<>'#forgotten'", (f"web:knowledge:{r['id']}",)):
                timeline.append(_ev(resolved, "owner_decision", f"owner answered on knowledge #{r['id']}: "
                                    f"{_quote(resolution or '')}", knowledge_id=r["id"], pending_id=pid))
        for fid, kid, effect, before, after, bits, detail, at, task in db.execute(
                "SELECT id, knowledge_id, effect, before_confidence, after_confidence, info_gain, detail, created_at, "
                "task_id FROM web_feedback WHERE key=? ORDER BY id", (key,)):
            d = state.loads(detail, {})
            what = "contradicting evidence" if d.get("contested") else {
                "strengthened": "new supporting evidence", "unchanged": "re-read, nothing new",
                "reversed": "a newer statement superseded it", "new": "first verified",
                "weakened": "contradicting evidence"}[effect]
            timeline.append(_ev(at, "evidence", f"{what} (confidence {before if before is not None else '-'} -> "
                                f"{after}; {round(bits or 0.0, 3)} bits)", knowledge_id=kid, effect=effect,
                                task_id=task, reason=d.get("reason")))
        for h in hypotheses(db, key=key, limit=100):
            timeline.append(_ev(h["created_at"], "hypothesis", f"hypothesis #{h['id']} «{_quote(h['statement'])}» "
                                f"({h['origin']})", hypothesis_id=h["id"], parent_id=h["parent_id"]))
            if h["resolved_at"]:
                timeline.append(_ev(h["resolved_at"], "hypothesis_" + h["status"],
                                    f"hypothesis #{h['id']} {h['status']}", hypothesis_id=h["id"]))
        ids = [r["id"] for r in rows]
        hids = [h["id"] for h in hypotheses(db, key=key, limit=100)]
        preds = []
        if ids or hids:
            marks_k, marks_h = ",".join("?" * len(ids)) or "NULL", ",".join("?" * len(hids)) or "NULL"
            preds = [_p_row(p) for p in db.execute(
                f"SELECT {','.join(P_COLUMNS)} FROM web_predictions WHERE knowledge_id IN ({marks_k}) OR "
                f"hypothesis_id IN ({marks_h}) ORDER BY id", (*ids, *hids))]
        for p in preds:
            if p["outcome"]:
                why = (p["detail"].get("reason") or (p["detail"].get("error_analysis") or {}).get("reason") or
                       (p["detail"].get("observation") or {}).get("reason") or "")
                timeline.append(_ev(p["checked_at"], "prediction_" + p["outcome"],
                                    f"prediction #{p['id']} «{_quote(p['expectation'])}» {p['outcome']}"
                                    + (f": {why}" if why else ""), prediction_id=p["id"],
                                    probability=p["probability"]))
        timeline = sorted((e for e in timeline if (e["at"] or 0) >= since), key=lambda e: (e["at"] or 0, e["kind"]))
        current = [v for v in versions if v["status"] in state.LIVE_KNOWLEDGE]
        subjects.append({"key": key, "current": current, "versions": versions, "timeline": timeline,
                         "predictions": [{k: p[k] for k in ("id", "expectation", "probability", "deadline",
                                                             "outcome", "checked_at")} for p in preds],
                         "changed": any(e["kind"] in ("superseded", "evidence", "owner_decision", "prediction_error",
                                                       "hypothesis_revised") for e in timeline) or len(versions) > 1})
    summary = _template_summary(topic, subjects, since)
    out = {"topic": topic, "as_of": now, "since": since or None, "subjects": subjects,
           "summary": {"text": summary, "generated_by": "template",
                       "label": "Template summary of the stored records above (not model reasoning)."}}
    if phrase:
        out["summary"] = _phrase(db, out, gateway) or out["summary"]
    return out


def _template_summary(topic, subjects, since) -> str:
    if not subjects:
        return f"Nothing about «{_quote(topic)}» is in memory, so nothing has changed."
    parts = []
    for s in subjects:
        vs = s["versions"]
        live = s["current"]
        line = f"«{s['key']}»: {len(vs)} version(s)"
        if live:
            cur = live[-1]
            line += f"; current v{cur['version']} «{_quote(cur['statement'])}» ({cur['status']}"
            state_ = (cur["verification"] or {}).get("state")
            line += f", {state_} by {(cur['verification'] or {}).get('basis')})" if state_ else ")"
        sup = [e for e in s["timeline"] if e["kind"] == "superseded"]
        if sup:
            line += "; superseded on " + ", ".join(e["date"] or "?" for e in sup)
        ev = [e for e in s["timeline"] if e["kind"] == "evidence"]
        if ev:
            line += f"; {len(ev)} evidence event(s), last on {ev[-1]['date']}"
        pe = [e for e in s["timeline"] if e["kind"].startswith("prediction_")]
        if pe:
            line += f"; {len(pe)} prediction outcome(s)"
        if not s["changed"]:
            line += "; unchanged since first recorded"
        parts.append(line)
    head = f"Since {_day(since)}: " if since else ""
    return head + " | ".join(parts)


def _phrase(db, result, gateway):
    """A local model's rephrasing of the template summary, from the returned records only; None when no gateway
    is configured, reachable or local. Remote endpoints are never used here (memory is private)."""
    try:
        if gateway is None:
            from ..inference.gateway import Gateway
            gateway = Gateway.from_db(db)
        if gateway.guard() == "remote":
            return {"text": result["summary"]["text"], "generated_by": "template",
                    "label": "Template summary: the configured model endpoint is remote and memory is not sent to it."}
        records = [{"key": s["key"], "timeline": [{k: e[k] for k in ("date", "kind", "text")} for e in s["timeline"]]}
                   for s in result["subjects"]]
        reply = gateway.chat([
            {"role": "system", "content": "Rephrase the change history below in at most five plain sentences. Use "
                                          "only these records; keep every date; add nothing."},
            {"role": "user", "content": state.dumps(records)[:12000]}], purpose="knowledge_changes", max_tokens=400)
        return {"text": reply.text.strip(), "generated_by": f"model:{reply.model}",
                "label": "Model phrasing of the records above; the records, not this text, are the evidence."}
    except Exception as exc:   # degraded: the structured answer stands
        return {"text": result["summary"]["text"], "generated_by": "template",
                "label": f"Template summary (model phrasing unavailable: {type(exc).__name__})."}


# ---- procedural learning: research-policy statistics ----------------------------------------------------------

POLICY_STEP = 0.1          # a weight moves at most this much per update
POLICY_BOUNDS = (0.5, 1.5)  # and stays inside these bounds
POLICY_MIN_SOURCES = 5     # a provider needs this many read sources before its weight moves


def _yields(db) -> dict:
    """Per provider: sources read, and sources behind at least one verified conclusion (supporting claim of a
    'verified_conclusion' web_knowledge row). Per mode: finished tasks, and tasks that produced one."""
    verified_claims = {c for (s,) in db.execute("SELECT supporting FROM web_knowledge WHERE "
                                                "provenance='verified_conclusion'") for c in state.loads(s, [])}
    providers = {}
    for provider, task_id, url in db.execute("SELECT coalesce(provider,'(direct)'), task_id, canonical_url FROM "
                                             "web_sources"):
        p = providers.setdefault(provider, {"sources": 0, "verified_sources": 0})
        p["sources"] += 1
        ids = {i for (i,) in db.execute("SELECT id FROM web_claims WHERE task_id=? AND canonical_url=?",
                                        (task_id, url))}
        if ids & verified_claims:
            p["verified_sources"] += 1
    modes = {}
    for mode, n, produced in db.execute(
            "SELECT mode, count(*), sum(EXISTS(SELECT 1 FROM web_knowledge k WHERE k.task_id=t.id AND "
            "k.provenance='verified_conclusion')) FROM web_tasks t WHERE status IN ('completed','partial') "
            "GROUP BY mode"):
        modes[mode] = {"tasks": n, "tasks_with_verified": produced or 0}
    return {"providers": providers, "modes": modes}


def research_policy(db) -> dict:
    """The current procedural research-policy statistics (the latest 'procedural' snapshot) or the neutral policy."""
    row = db.execute("SELECT id, value, computed_at FROM web_metrics WHERE kind='procedural' ORDER BY id DESC "
                     "LIMIT 1").fetchone()
    if row is None:
        return {"snapshot_id": None, "weights": {}, "statistics": None, "note": "neutral: no update yet"}
    return {"snapshot_id": row[0], "computed_at": row[2]} | state.loads(row[1], {})


def update_research_policy(db, now) -> dict:
    """Procedural learning (CONTINUAL_LEARNING §3): recompute provider yields from the current records (so
    forgotten records drop out) and move each provider's advisory weight toward 0.5 + yield, by at most
    POLICY_STEP, inside POLICY_BOUNDS, only with >= POLICY_MIN_SOURCES sources. Logged as a snapshot holding the
    previous weights; revert_research_policy restores them. Advisory only: nothing reads it to change a search
    automatically in this release."""
    stats = _yields(db)
    previous = research_policy(db)
    old = previous.get("weights") or {}
    weights, changes_ = {}, {}
    for name, p in stats["providers"].items():
        w = float(old.get(name, 1.0))
        if p["sources"] >= POLICY_MIN_SOURCES:
            target = 0.5 + p["verified_sources"] / p["sources"]
            new = min(POLICY_BOUNDS[1], max(POLICY_BOUNDS[0], w + max(-POLICY_STEP, min(POLICY_STEP, target - w))))
            if round(new, 6) != round(w, 6):
                changes_[name] = {"from": round(w, 6), "to": round(new, 6)}
            w = new
        weights[name] = round(w, 6)
    value = {"weights": weights, "previous": old, "previous_snapshot": previous.get("snapshot_id"),
             "changes": changes_, "statistics": stats, "reverted": False}
    with store.write(db):
        sid = _snapshot(db, "procedural", value, now)
        state.log_event(db, None, "decision", {"procedural_policy": sid, "changed": sorted(changes_)}, now)
        store.bump_state_version(db)
    return {"snapshot_id": sid} | value


def revert_research_policy(db, now) -> dict:
    """Undo the last procedural update: a new snapshot with the previous weights (history is kept)."""
    current = research_policy(db)
    if current.get("snapshot_id") is None:
        raise Conflict("no procedural policy update to revert")
    value = {"weights": current.get("previous") or {}, "previous": current.get("weights") or {},
             "previous_snapshot": current["snapshot_id"], "changes": {}, "statistics": current.get("statistics"),
             "reverted": True}
    with store.write(db):
        sid = _snapshot(db, "procedural", value, now)
        state.log_event(db, None, "decision", {"procedural_policy": sid, "reverted": current["snapshot_id"]}, now)
        store.bump_state_version(db)
    return {"snapshot_id": sid} | value


# ---- model adaptation guard ---------------------------------------------------------------------------------

AUTONOMOUS_ORIGINS = ("curiosity", "refresh", "monitor", "scheduler", "autonomous", "pocket")


def model_adaptation_guard(db, request: dict, now) -> dict:
    """Refuse any weight-changing action unless it carries (1) the owner's explicit authorisation, (2) an isolated
    evaluation plan (held-out data never used for training, a metric and a pass bar) and (3) a rollback plan (a
    restorable checkpoint of the current weights and a procedure); requests from an autonomous origin are always
    refused. Every decision is logged (web_metrics kind 'adaptation', names and booleans only). Even a permitted
    request is not executed: this package contains no weight-changing implementation (CONTINUAL_LEARNING §4).
    Raises Conflict when refused."""
    if not isinstance(request, dict):
        raise ValidationError("request must be an object")
    action = str(request.get("action") or "")[:80]
    missing = []
    origin = str(request.get("origin") or "")
    if any(origin == o or origin.startswith(o + ":") for o in AUTONOMOUS_ORIGINS):
        missing.append("autonomous origin: model adaptation is never started autonomously")
    auth = request.get("owner_authorization") or {}
    if not (isinstance(auth, dict) and auth.get("by") == "owner" and auth.get("confirmed") is True and
            state.finite(auth.get("at")) is not None):
        missing.append("owner_authorization {by: 'owner', confirmed: true, at}")
    ev = request.get("isolated_evaluation") or {}
    if not (isinstance(ev, dict) and ev.get("isolated") is True and ev.get("heldout") and ev.get("metric") and
            state.finite(ev.get("pass_bar")) is not None):
        missing.append("isolated_evaluation {isolated: true, heldout, metric, pass_bar}")
    rb = request.get("rollback_plan") or {}
    if not (isinstance(rb, dict) and rb.get("checkpoint") and rb.get("procedure")):
        missing.append("rollback_plan {checkpoint, procedure}")
    changes_weights = request.get("changes_weights", True) is not False
    allowed = not missing or not changes_weights
    decision = {"action": action, "changes_weights": changes_weights, "allowed": allowed,
                "missing": [m.split(" ")[0] for m in missing], "implemented": False}
    with store.write(db):
        decision["decision_id"] = _snapshot(db, "adaptation", decision, now)
        store.bump_state_version(db)
    if not allowed:
        raise Conflict("model adaptation refused: missing " + "; ".join(missing))
    return decision | {"note": "Permitted by the guard, but no weight-changing implementation ships in this "
                               "package; run it outside, in isolation, against the stated evaluation and rollback "
                               "plan." if changes_weights else "Does not change weights."}


# ---- CLI: nsf knowledge ... -----------------------------------------------------------------------------------

def _cli_evolution(db, a, now):
    return metrics(db, now, since=(now - a.since_days * 86400.0) if a.since_days else None, record=a.record)


def _cli_changes(db, a, now):
    return changes(db, a.topic, now, since=(now - a.since_days * 86400.0) if a.since_days else None,
                   limit=a.limit, phrase=a.phrase)


def _cli_hypotheses(db, a, now):
    return {"hypotheses": hypotheses(db, status=a.status, limit=a.limit)}


def _cli_hypothesize(db, a, now):
    from . import claims, text
    statement = a.statement.strip()
    if not statement or len(statement) > 2000:
        raise ValidationError("statement must be a non-blank string of at most 2000 characters")
    with store.write(db):
        hid, created = open_hypothesis(db, key=claims.claim_key(statement), statement=statement, origin="owner",
                                       verification={"policy": "ADR-035", "state": "not_evaluated",
                                                     "basis": "owner_hypothesis", "confidence": a.probability},
                                       claims=[], values=text.values(statement), polarity=claims.polarity(statement),
                                       now=now, with_prediction=False)
        pid = predict(db, hid, now, deadline=now + a.deadline_days * 86400.0, probability=a.probability) \
            if created else None
        store.bump_state_version(db)
    return {"hypothesis": hypothesis(db, hid), "prediction": prediction(db, pid) if pid else None}


def _cli_calibration(db, a, now):
    return {"current": calibration(db), "recalibration": recalibration_report(db),
            "history": calibration_history(db, limit=a.limit)}


_NODE_TABLES = {"knowledge": "web_knowledge", "hypothesis": "web_hypotheses", "prediction": "web_predictions",
                "claim": "web_claims"}


def _cli_graph(db, a, now):
    kind, _, nid = a.node.partition(":")
    if not nid:
        raise ValidationError("node must be KIND:ID, e.g. knowledge:12")
    out = graph(db, kind, nid, depth=a.depth)
    table = _NODE_TABLES.get(kind)
    if not out["edges"] and table and not db.execute(f"SELECT 1 FROM {table} WHERE CAST(id AS TEXT)=? LIMIT 1",
                                                     (nid,)).fetchone():
        raise NotFound(f"no {kind} {nid}")
    return out


def _cli_learning(db, a, now):
    if a.update:
        return update_research_policy(db, now)
    if a.revert:
        return revert_research_policy(db, now)
    return research_policy(db) | {"statistics_now": _yields(db)}


def _cli_assert(db, a, now):
    from . import knowledge
    return knowledge.assert_fact(db, a.statement, now, note=a.note or "")


def _cli_adapt(db, a, now):
    import json
    request = json.loads(a.request_json) if a.request_json else {}
    if not isinstance(request, dict):
        raise ValidationError("--request-json must be a JSON object")
    request.setdefault("action", a.action)
    request.setdefault("origin", "owner-cli")
    return model_adaptation_guard(db, request, now)


def register(sub):
    from ..instance.cli import LIMIT, bounded_int, days, with_db

    def _probability(text):
        value = float(text)
        if not 0.0 <= value <= 1.0:
            raise ValueError(text)
        return value

    k = sub.add_parser("knowledge", help="knowledge evolution: metrics, changes, hypotheses, calibration (ADR-038)")
    ksub = k.add_subparsers(dest="knowledge_command", required=True, metavar="KNOWLEDGE_COMMAND")

    def cmd(name, handler, help_text):
        p = ksub.add_parser(name, help=help_text)
        store.add_db_argument(p)
        p.set_defaults(handler=with_db(handler))
        return p

    p = cmd("evolution", _cli_evolution, "metacognitive metrics computed from the stored records")
    p.add_argument("--since-days", type=days)
    p.add_argument("--record", action="store_true", help="store the snapshot (web_metrics)")
    p = cmd("changes", _cli_changes, "what has changed in the understanding of TOPIC (versions, evidence, dates)")
    p.add_argument("topic")
    p.add_argument("--since-days", type=days)
    p.add_argument("--limit", type=LIMIT, default=5)
    p.add_argument("--phrase", action="store_true", help="let a configured local model rephrase the summary")
    p = cmd("hypotheses", _cli_hypotheses, "research opportunities and hypotheses (provisional, never knowledge)")
    p.add_argument("--status", choices=HYPOTHESIS_STATES)
    p.add_argument("--limit", type=LIMIT, default=50)
    p = cmd("hypothesize", _cli_hypothesize, "record the owner's hypothesis with a testable prediction")
    p.add_argument("statement")
    p.add_argument("--probability", type=_probability, default=0.5, help="your forecast that it holds")
    p.add_argument("--deadline-days", type=days, default=30.0)
    p = cmd("calibration", _cli_calibration, "Brier score and reliability bins of settled predictions")
    p.add_argument("--limit", type=LIMIT, default=10)
    p = cmd("graph", _cli_graph, "knowledge-graph edges around KIND:ID (claim, knowledge, hypothesis, ...)")
    p.add_argument("node")
    p.add_argument("--depth", type=bounded_int(1, 3), default=1)
    p = cmd("learning", _cli_learning, "procedural research-policy statistics (bounded, logged, reversible)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--update", action="store_true")
    group.add_argument("--revert", action="store_true")
    p = cmd("assert", _cli_assert, "record a fact as the owner's assertion (provenance user_asserted)")
    p.add_argument("statement")
    p.add_argument("--note")
    p = cmd("adapt", _cli_adapt, "model adaptation guard: refuses weight changes without authorisation, isolated "
                                 "evaluation and a rollback plan (nothing is executed)")
    p.add_argument("action")
    p.add_argument("--request-json", help="{owner_authorization, isolated_evaluation, rollback_plan, ...}")
    from . import reflection          # lane K: rechecks, reflect, reflections, strategy, ledger, compete
    reflection.register_cli(cmd)

