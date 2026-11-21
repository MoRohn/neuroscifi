"""Controlled consolidation: a task's scored claim clusters -> versioned knowledge candidates (ADR-026).

Steps for each scored cluster of the task (state.load_clusters):
1. candidates: clusters ranked by confidence;
2. relevance and evidence quality: 'corroborated', or 'single_source' from a primary structured source (an API
   document or a metadata claim), with at least one relevant claim; 'contested' clusters become owner questions
   instead; everything else is *held* (stays a claim, listed with reasons);
3. duplication: live knowledge with the same (or a near-identical) key and the same values/polarity is refreshed
   (last_verified_at, supporting claims, sources), never duplicated -> feedback 'strengthened' (or 'unchanged');
4. comparison with memory: same key, conflicting values or polarity -> temporal.supersedes decides (a later
   statement of a series - every value group differs - updates the item when it is newer). A newer cluster
   that meets the provisional bar becomes a new version and the old row becomes 'superseded' (valid_until = the
   new valid_from, superseded_by = new id) -> feedback 'reversed'. Otherwise the contradiction is recorded on the
   knowledge (contradicting list), a 'contradicts' link is stored and the owner is asked -> feedback 'weakened';
5. provenance and time: sources, supporting claim ids, freshness class, validity interval;
6. commit: a new item is written only as 'provisional', and only with >= config.auto_provisional_min_sources
   independent sources, no contradiction and confidence >= config.auto_provisional_min_confidence; each new item
   gets a pending question (scope web:knowledge:<id>) so acceptance stays the owner's decision.
Entities and mentions are extracted from claim text (capitalised names, CamelCase, package names, acronyms,
identifiers). Feedback rows record before/after confidence and the information gain (entropy reduction, bits).
The binary-belief tables are never touched.
"""
from __future__ import annotations

import re

from ..instance import store
from . import knowledge, state
from .curiosity import entropy

MIN_RELEVANCE = 0.0          # a cluster needs at least one claim with relevance above this
SIMILAR_KEY = 0.8            # token-set Jaccard above which two keys name the same subject
MAX_ENTITIES_PER_CLAIM = 12

_ENTITY_PATTERNS = (
    ("identifier", re.compile(r"\b(?:CVE-\d{4}-\d{4,7}|GHSA(?:-[0-9a-z]{4}){3}|10\.\d{4,9}/[^\s\"'<>]+)",
                              re.IGNORECASE)),
    ("name", re.compile(r"\b[A-Z][a-z0-9]+(?:\s+(?:of\s+|de\s+|von\s+)?[A-Z][a-z0-9]+)+\b")),
    ("name", re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")),                   # CamelCase
    ("package", re.compile(r"(?<![\w.])[a-z][a-z0-9]+(?:[-_][a-z0-9]+)+(?![\w.])")),  # kebab/snake package names
    ("acronym", re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")),
)


def _key_tokens(key):
    return set(re.findall(r"[^\W_]+|<\w+>", key or ""))


def _similar(a, b) -> float:
    ta, tb = _key_tokens(a), _key_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _compare(cluster_values, polarity, item):
    """('same' | 'conflict' | 'series', reason) between a cluster and a knowledge item, by text.values_conflict
    applied separately to temporal values (dates, years) and the other values. Both differing is a series
    (text.values_conflict: another reference period, not a contradiction); one differing is a conflict."""
    from . import text
    if polarity != item["polarity"]:
        return "conflict", "opposite polarity (one source asserts, the other negates)"
    temporal_kinds = ("date", "year")

    def split(values):
        when = [v for v in values if text.value_kind(v)[0] in temporal_kinds]
        return when, [v for v in values if v not in when]

    (new_when, new_other), (old_when, old_other) = split(list(cluster_values)), split(list(item["claim_values"]))
    other = text.values_conflict(new_other, old_other)
    when = text.values_conflict(new_when, old_when)
    if other and when:
        return "series", f"{other}; {when} (another reference period)"
    if other or when:
        return "conflict", other or when
    return "same", None


def _later(new, old) -> bool:
    """Effective time (event date, else publication date) of `new` strictly after `old`'s (retrieval bounds an
    undated old statement)."""
    from . import temporal
    t_new, t_old = temporal.effective_time(new), temporal.effective_time(old)
    t_old = old.get("retrieved_at") if t_old is None else t_old
    return t_new is not None and t_old is not None and t_new > t_old


def _head(cluster):
    support = set(cluster.supporting)
    members = [c for c in cluster.claims if c.id in support] or list(cluster.claims)
    return members[0]


def _values(cluster):
    head = _head(cluster)
    return list(head.values or []), (-1 if head.polarity < 0 else 1)


def _times(cluster):
    support = set(cluster.supporting) or {c.id for c in cluster.claims}
    members = [c for c in cluster.claims if c.id in support]
    published = [c.published_at for c in members if state.finite(c.published_at) is not None]
    events = [c.event_at for c in members if state.finite(c.event_at) is not None]
    retrieved = [c.retrieved_at for c in members if state.finite(c.retrieved_at) is not None]
    return (max(published) if published else None, max(events) if events else None,
            max(retrieved) if retrieved else None)


def _validity(cluster, now):
    """(valid_from, valid_until) by temporal.validity over the newest event/publication date of the supporting
    claims (retrieval time as the upper bound for undated sources); open-ended until superseded."""
    from . import temporal
    published, event, retrieved = _times(cluster)
    out = temporal.validity({"event_at": event, "published_at": published, "retrieved_at": retrieved}, now=now)
    return state.finite(out.get("valid_from"), now), state.finite(out.get("valid_until"))


def _as_temporal(cluster, now, key):
    published, event, retrieved = _times(cluster)
    values, polarity = _values(cluster)
    return {"key": key, "text": cluster.statement or _head(cluster).text, "values": values, "polarity": polarity,
            "published_at": published, "event_at": event, "retrieved_at": retrieved}


def _item_temporal(item):
    sources = item["sources"]
    published = [s.get("published_at") for s in sources if state.finite(s.get("published_at")) is not None]
    return {"key": item["key"], "text": item["statement"], "values": item["claim_values"], "polarity": item["polarity"],
            "published_at": max(published) if published else None, "event_at": item["valid_from"],
            "retrieved_at": item["last_verified_at"], "valid_from": item["valid_from"]}


def _sources(cluster, extra=()):
    seen, out = set(), []
    for s in list(extra) + [{"url": c.source_url, "title": c.title, "domain": c.domain,
                              "published_at": state.finite(c.published_at), "retrieved_at": c.retrieved_at}
                             for c in cluster.claims if c.id in set(cluster.supporting) or not cluster.supporting]:
        if s.get("url") and s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    return out


def _primary_structured(db, task_id, cluster) -> bool:
    for c in cluster.claims:
        if c.kind == "metadata" or (c.flags or {}).get("source_type") == "primary" or (c.flags or {}).get("primary"):
            return True
        found = db.execute("SELECT kind FROM web_sources WHERE task_id=? AND canonical_url=?",
                           (task_id, c.source_url)).fetchone()
        if found and found[0] in ("api", "json"):
            return True
    return False


def _eligibility(db, task_id, cluster):
    """(eligible, reasons): evidence-quality and relevance gate before memory comparison."""
    reasons = []
    if not cluster.claims:
        return False, ["no claims"]
    if all((c.flags or {}).get("injection") for c in cluster.claims):
        reasons.append("every claim was flagged as instruction-like content")
    if max(state.finite(c.relevance, 0.0) for c in cluster.claims) <= MIN_RELEVANCE:
        reasons.append("no claim is relevant to the question")
    if cluster.status == "insufficient":
        reasons.append("insufficient evidence")
    elif cluster.status == "single_source" and not _primary_structured(db, task_id, cluster):
        reasons.append("single source that is not a primary structured source")
    return not reasons, reasons


def _meets_bar(cluster, cfg):
    reasons = []
    if cluster.independent_sources < cfg.auto_provisional_min_sources:
        reasons.append(f"{cluster.independent_sources} independent source(s) < "
                       f"{cfg.auto_provisional_min_sources} required")
    if cluster.contradicting:
        reasons.append("the sources contradict each other")
    if state.finite(cluster.confidence, 0.0) < cfg.auto_provisional_min_confidence:
        reasons.append(f"confidence {state.finite(cluster.confidence, 0.0):.2f} < "
                       f"{cfg.auto_provisional_min_confidence:.2f}")
    return not reasons, reasons


def _existing(db, cluster):
    """Live knowledge about the same subject: one already supported by one of the cluster's claims (claim ids hash
    the source URL and the sentence, so a re-read finds them whatever the question was), else the latest version
    with the same key, else the most similar live key above SIMILAR_KEY."""
    live = knowledge.rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
    ids = {c.id for c in cluster.claims}
    shared = [r for r in live if ids & set(r["supporting"])]
    if shared:
        return max(shared, key=lambda r: (len(ids & set(r["supporting"])), r["version"]))
    key = cluster.key
    exact = [r for r in live if r["key"] == key]
    if exact:
        return max(exact, key=lambda r: r["version"])
    scored = sorted(((_similar(key, r["key"]), r["version"], r) for r in live), key=lambda x: (-x[0], -x[1]))
    return scored[0][2] if scored and scored[0][0] >= SIMILAR_KEY else None


def _bits_gain(before, after):
    """Entropy reduction (bits) of the binary posterior that the statement holds."""
    return round(entropy(0.5 if before is None else before) - entropy(after), 6)


def _feedback(db, task_id, knowledge_id, key, effect, before, after, detail, now):
    db.execute("INSERT INTO web_feedback(task_id,knowledge_id,key,effect,before_confidence,after_confidence,"
               "info_gain,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (task_id, knowledge_id, key, effect, before, after, _bits_gain(before, after),
                state.dumps(detail or {}), now))


def _question(statement, n_sources, confidence, freshness):
    return (f"Accept as knowledge: «{knowledge.quote(statement)}» ({n_sources} independent sources, confidence "
            f"{confidence:.2f}, freshness class {freshness})?")


def _freshness(db, task):
    assessment = (task.get("checkpoint") or {}).get("assessment") or {}
    cls = assessment.get("freshness_class")
    if cls:
        return cls
    from . import temporal
    return temporal.classify(task["objective"], assessment or None)


def _insert_knowledge(db, task_id, cluster, freshness, now, *, key, version):
    values, polarity = _values(cluster)
    valid_from, valid_until = _validity(cluster, now)
    confidence = state.finite(cluster.confidence, 0.0)
    kid = db.execute(
        "INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
        "first_verified_at,last_verified_at,valid_from,valid_until,supporting,contradicting,sources,claim_values,"
        "polarity,task_id,created_at,updated_at) VALUES(?,?,?,'provisional',?,?,?,?,?,?,?,?,'[]',?,?,?,?,?,?)",
        (key, version, cluster.statement or _head(cluster).text, confidence,
         state.finite(cluster.uncertainty, 0.0), freshness, now, now, valid_from, valid_until,
         state.dumps(list(cluster.supporting) or [c.id for c in cluster.claims]), state.dumps(_sources(cluster)),
         state.dumps(values), polarity, task_id, now, now)).lastrowid
    pid = knowledge.open_question(db, f"web:knowledge:{kid}",
                                  _question(cluster.statement or _head(cluster).text, cluster.independent_sources,
                                            confidence, freshness), now)
    db.execute("UPDATE web_knowledge SET pending_id=? WHERE id=?", (pid, kid))
    return kid, pid, valid_from


def _entities(db, task_id, clusters, ids_by_key, now):
    found = 0
    for cluster in clusters:
        kid = ids_by_key.get(cluster.key)
        for c in cluster.claims:
            seen = set()
            for kind, pattern in _ENTITY_PATTERNS:
                for match in pattern.finditer(c.text or ""):
                    name = " ".join(match.group(0).split()).strip(".,;:")
                    norm = name.casefold()
                    if len(norm) < 2 or norm in seen or any(norm in s for s in seen):
                        continue
                    seen.add(norm)
                    db.execute("INSERT INTO web_entities(name,normalized,kind,first_seen,last_seen) "
                               "VALUES(?,?,?,?,?) ON CONFLICT(normalized) DO UPDATE SET last_seen=excluded.last_seen",
                               (name, norm, kind, now, now))
                    eid = db.execute("SELECT id FROM web_entities WHERE normalized=?", (norm,)).fetchone()[0]
                    db.execute("INSERT INTO web_mentions(entity_id,claim_id,knowledge_id,task_id,created_at) "
                               "VALUES(?,?,?,?,?)", (eid, c.id, kid, task_id, now))
                    found += 1
                    if len(seen) >= MAX_ENTITIES_PER_CLAIM:
                        break
                if len(seen) >= MAX_ENTITIES_PER_CLAIM:
                    break
    return found


def _contradict(db, task_id, cluster, item, reason, now, summary):
    """Record a contradiction of live knowledge by this cluster and ask the owner. Caller owns the txn."""
    head = _head(cluster)
    against = list(item["contradicting"])
    known = {a.get("claim") for a in against if isinstance(a, dict)}
    fresh = head.id not in known
    if fresh:
        against.append({"claim": head.id, "task": task_id, "reason": reason, "text": knowledge.quote(head.text),
                        "url": head.source_url})
        db.execute("UPDATE web_knowledge SET contradicting=?, updated_at=? WHERE id=?",
                   (state.dumps(against), now, item["id"]))
    if item["supporting"]:
        state.add_link(db, task_id, head.id, item["supporting"][0], "contradicts",
                       {"reason": reason, "knowledge_id": item["id"]}, now)
    pid = knowledge.open_question(
        db, f"web:knowledge:{item['id']}",
        f"Sources now contradict known «{knowledge.quote(item['statement'])}»: «{knowledge.quote(head.text)}» "
        f"({reason}). Keep it as knowledge?", now)
    before = state.finite(item["confidence"], 0.0)
    after = before if not fresh else round(before * (1 - state.finite(cluster.confidence, 0.0) / 2), 6)
    if after != before:
        db.execute("UPDATE web_knowledge SET confidence=? WHERE id=?", (after, item["id"]))
    _feedback(db, task_id, item["id"], item["key"], "weakened" if fresh else "unchanged", before, after,
              {"contested": True, "reason": reason}, now)
    summary["contested"].append({"key": cluster.key, "knowledge_id": item["id"], "pending_id": pid,
                                 "reason": reason})
    summary["pending"].append(pid)


def run(db, task_id, now) -> dict:
    """Consolidate one task's scored clusters into knowledge candidates. One transaction, one state bump."""
    from . import temporal
    cfg = state.get_config(db)
    summary = {"task_id": task_id, "candidates": 0, "provisional": [], "strengthened": [], "unchanged": [],
               "superseded": [], "contested": [], "held": [], "pending": [], "feedback": 0, "entities": 0}
    with store.write(db):
        task = state.task(db, task_id)
        clusters = sorted(state.load_clusters(db, task_id), key=lambda c: (-state.finite(c.confidence, 0.0), c.key))
        summary["candidates"] = len(clusters)
        freshness = _freshness(db, task)
        feedback_before = db.execute("SELECT count(*) FROM web_feedback").fetchone()[0]
        ids_by_key = {}
        for n, cluster in enumerate(clusters):
            item = _existing(db, cluster)
            if cluster.status == "contested":
                reason = "; ".join(sorted({c.get("reason", "") for c in cluster.contradicting} - {""})) or \
                    "the sources disagree"
                if item is not None:
                    _contradict(db, task_id, cluster, item, reason, now, summary)
                    continue
                pid = knowledge.open_question(
                    db, f"web:contested:{task_id}:{n}",
                    f"Sources disagree about «{knowledge.quote(cluster.statement or _head(cluster).text)}» "
                    f"({reason}); nothing was stored as knowledge. Which holds?", now)
                prior = db.execute("SELECT count(*) FROM web_feedback WHERE key=?", (cluster.key,)).fetchone()[0]
                conf = state.finite(cluster.confidence, 0.5)
                _feedback(db, task_id, None, cluster.key, "unchanged" if prior else "new", conf, conf,
                          {"contested": True, "reason": reason}, now)
                summary["contested"].append({"key": cluster.key, "knowledge_id": None, "pending_id": pid,
                                             "reason": reason})
                summary["pending"].append(pid)
                continue
            eligible, reasons = _eligibility(db, task_id, cluster)
            if not eligible:
                summary["held"].append({"key": cluster.key, "statement": cluster.statement, "reasons": reasons,
                                        "status": cluster.status})
                continue
            values, polarity = _values(cluster)
            if item is not None:
                relation, differ = _compare(values, polarity, item)
                if relation == "same":   # duplication: strengthen, never duplicate
                    new_claims = [c for c in cluster.supporting if c not in item["supporting"]]
                    before = state.finite(item["confidence"], 0.0)
                    after = max(before, state.finite(cluster.confidence, 0.0)) if new_claims else before
                    status = "provisional" if item["status"] == "stale" else item["status"]
                    db.execute("UPDATE web_knowledge SET last_verified_at=?, supporting=?, sources=?, confidence=?, "
                               "uncertainty=min(uncertainty, ?), status=?, updated_at=? WHERE id=?",
                               (now, state.dumps(item["supporting"] + new_claims),
                                state.dumps(_sources(cluster, item["sources"])), after,
                                state.finite(cluster.uncertainty, 1.0), status, now, item["id"]))
                    head = item["supporting"][0] if item["supporting"] else None
                    for cid in cluster.supporting:
                        if head and cid != head:
                            state.add_link(db, task_id, cid, head, "duplicates" if cid in item["supporting"]
                                           else "supports", {"knowledge_id": item["id"]}, now)
                    effect = "strengthened" if new_claims else "unchanged"
                    _feedback(db, task_id, item["id"], item["key"], effect, before, after,
                              {"new_claims": len(new_claims)}, now)
                    summary["strengthened" if new_claims else "unchanged"].append(item["id"])
                    ids_by_key[cluster.key] = item["id"]
                    continue
                new_t, old_t = _as_temporal(cluster, now, item["key"]), _item_temporal(item)
                if relation == "conflict":
                    newer = bool(temporal.supersedes(new_t, old_t))
                else:   # a later statement of a series updates the item; the old version keeps its period
                    newer = _later(new_t, old_t)
                    if not newer:
                        summary["held"].append({"key": cluster.key, "statement": cluster.statement, "status":
                                                cluster.status, "reasons": [f"older statement of a series than "
                                                                            f"knowledge #{item['id']} ({differ})"]})
                        continue
                ok, bar = _meets_bar(cluster, cfg)
                if newer and ok:
                    version = db.execute("SELECT max(version) FROM web_knowledge WHERE key=?",
                                         (item["key"],)).fetchone()[0] + 1
                    kid, pid, valid_from = _insert_knowledge(db, task_id, cluster, freshness, now,
                                                             key=item["key"], version=version)
                    db.execute("UPDATE web_knowledge SET status='superseded', valid_until=?, superseded_by=?, "
                               "updated_at=? WHERE id=?", (valid_from, kid, now, item["id"]))
                    knowledge.close_questions(db, f"web:knowledge:{item['id']}",
                                              f"superseded by knowledge #{kid}", now)
                    if item["supporting"]:
                        state.add_link(db, task_id, _head(cluster).id, item["supporting"][0], "supersedes",
                                       {"old": item["id"], "new": kid, "reason": differ}, now)
                    _feedback(db, task_id, kid, item["key"], "reversed", state.finite(item["confidence"], 0.0),
                              state.finite(cluster.confidence, 0.0), {"superseded": item["id"], "reason": differ},
                              now)
                    summary["superseded"].append({"old": item["id"], "new": kid, "reason": differ})
                    summary["provisional"].append(kid)
                    summary["pending"].append(pid)
                    ids_by_key[cluster.key] = kid
                    continue
                why = differ if newer is False else f"{differ}; newer but below the provisional bar ({'; '.join(bar)})"
                _contradict(db, task_id, cluster, item, why, now, summary)
                continue
            ok, bar = _meets_bar(cluster, cfg)
            if not ok:
                summary["held"].append({"key": cluster.key, "statement": cluster.statement, "reasons": bar,
                                        "status": cluster.status})
                continue
            kid, pid, _ = _insert_knowledge(db, task_id, cluster, freshness, now, key=cluster.key, version=1)
            _feedback(db, task_id, kid, cluster.key, "new", None, state.finite(cluster.confidence, 0.0),
                      {"independent_sources": cluster.independent_sources}, now)
            summary["provisional"].append(kid)
            summary["pending"].append(pid)
            ids_by_key[cluster.key] = kid
        summary["entities"] = _entities(db, task_id, clusters, ids_by_key, now)
        summary["feedback"] = db.execute("SELECT count(*) FROM web_feedback").fetchone()[0] - feedback_before
        summary["pending"] = sorted(set(summary["pending"]))
        state.log_event(db, task_id, "consolidated", {k: (len(v) if isinstance(v, list) else v)
                                                       for k, v in summary.items() if k != "task_id"}, now)
        summary["state_version"] = store.bump_state_version(db)
    return summary
