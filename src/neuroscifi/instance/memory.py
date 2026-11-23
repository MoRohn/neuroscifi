"""Unified, read-only retrieval across the instance's memories.

Every record has the same envelope: kind, id, scope, time, permissions, confidence, outcome_history, payload.
Association or embedding search is out of scope; retrieval is by identity, scope and time.
Web Cortex kinds (migration 6): research (web_tasks), claim, knowledge, source, monitor; they also filter by
status and (claim, source) by task.
Kind 'consolidated' (migration 14, ADR-044): what expired learning events left behind, one record per event. Its id
is the event's seq (the event id is gone; the tombstone keeps it), its time is when it was consolidated, and it
filters by epoch, channel and root like evidence.
"""
import json

from . import store
from .errors import ValidationError

KINDS = ("evidence", "consolidated", "belief", "graph", "dataset", "checkpoint", "deletion", "decision", "pending",
         "research", "claim", "knowledge", "source", "monitor")
_FILTERS = {  # filters beyond id/since/until that each kind understands
    "evidence": {"epoch", "channel", "root"}, "consolidated": {"epoch", "channel", "root"},
    "belief": {"epoch"}, "graph": {"context"}, "dataset": {"context"},
    "checkpoint": set(), "deletion": set(), "decision": {"epoch"}, "pending": {"epoch", "context"},
    "research": {"status"}, "claim": {"status", "task"}, "knowledge": {"status"}, "source": {"task"},
    "monitor": {"status"},
}
_FILTER_COLUMNS = {"claim": {"status": "cluster_status", "task": "task_id"}, "source": {"task": "task_id"}}
_TEXT_IDS = ("evidence", "research", "claim")
_WEB = {"consent": "web", "expires_at": None}


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _perm(consent=None, retention=None, expires_at=None):
    return {"consent": consent, "retention": retention, "expires_at": expires_at}


def _record(kind, id, scope, time, payload, *, permissions=None, confidence=None, outcome_history=None):
    return {"kind": kind, "id": id, "scope": scope, "time": time, "permissions": permissions or _perm(),
            "confidence": confidence, "outcome_history": outcome_history or [], "payload": payload}


def _evidence(r):
    seq, id, epoch, channel, action, bit, root, sample_id, actor, consent, prov, acq, rec, ret, exp = r
    return _record("evidence", id, {"epoch": epoch, "root": root}, rec,
                   {"seq": seq, "channel": channel, "action": action, "bit": bit, "sample_id": sample_id,
                    "actor": actor, "provenance": prov, "acquired_at": acq},
                   permissions=_perm(consent, ret, exp))


def _consolidated(r):
    seq, epoch, channel, root, value, sample_id, at = r
    return _record("consolidated", seq, {"epoch": epoch, "root": root}, at,
                   {"seq": seq, "channel": channel, "reading": value, "sample_id": sample_id},
                   permissions=_perm("learn", "consolidated", None))


def _belief_confidence(summary):
    """Mean over the summary's factors (mechanism, sensors, partners) of max(p, 1-p); None if not a compute() summary.

    With no sensor or partner this is the mechanism's own confidence.
    """
    if not isinstance(summary, dict):
        return None
    probabilities = []
    mechanism = summary.get("mechanism")
    if isinstance(mechanism, dict):
        probabilities.append(mechanism.get("p_inverted"))
    for key in ("sensors", "partners"):
        if isinstance(summary.get(key), dict):
            probabilities.extend(v.get("p_inverted") if isinstance(v, dict) else None for v in summary[key].values())
    if not probabilities or not all(isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1
                                    for p in probabilities):
        return None
    return sum(max(p, 1 - p) for p in probabilities) / len(probabilities)


def _belief(r):
    id, epoch, version, created, cause, summary = r
    parsed = _json(summary)
    return _record("belief", id, {"epoch": epoch, "root": None}, created,
                   {"state_version": version, "cause": cause, "summary": parsed},
                   confidence=_belief_confidence(parsed))


def _graph(r):
    (id, context, parent, status, bits, degree, rows, cols, size, sha, rule, origin,
     metrics, decision, created, decided) = r
    metrics = _json(metrics)
    heldout = metrics.get("heldout_mse") if isinstance(metrics, dict) else None
    return _record("graph", id, {"context": context, "root": None}, created,
                   {"status": status, "parent_id": parent, "bits": bits, "degree": degree, "rows": rows,
                    "cols": cols, "byte_size": size, "sha256": sha, "rule_version": rule, "origin": origin,
                    "metrics": metrics, "decided_at": decided},
                   confidence=heldout if isinstance(heldout, (int, float)) else None,
                   outcome_history=[_json(decision)] if decision else [])


def _dataset(r):
    id, context, split, prov, rows, cols, sha, note, created, deleted = r
    return _record("dataset", id, {"context": context, "root": None}, created,
                   {"split": split, "provenance": prov, "rows": rows, "cols": cols, "sha256": sha,
                    "note": note, "deleted_at": deleted})


def _checkpoint(r):
    id, path, policy, sha, size, version, seq, created, verified, status = r
    return _record("checkpoint", id, {"context": None, "root": None}, created,
                   {"path": path, "policy": policy, "sha256": sha, "bytes": size, "state_version": version,
                    "max_evidence_seq": seq, "verified_at": verified, "status": status})


def _deletion(r):
    id, kind, target, requested, effects, failure = r
    return _record("deletion", id, {"context": None, "root": None}, requested,
                   {"target_kind": kind, "target_id": target, "effects": _json(effects), "failure": failure})


def _decision(r):
    id, epoch, version, decision, scope, reason, created = r
    return _record("decision", id, {"epoch": epoch, "root": None}, created,
                   {"state_version": version, "decision": decision, "scope": scope, "reason": reason})


def _pending(r):
    id, epoch, context, kind, scope, question, created, resolved, resolution = r
    return _record("pending", id, {"epoch": epoch, "root": None}, created,
                   {"context": context, "kind": kind, "scope": scope, "question": question,
                    "resolved_at": resolved, "resolution": resolution})


def _research(r):
    id, objective, mode, status, stage, origin, created, updated, finished, usage, error = r
    return _record("research", id, {"context": "web", "root": None}, created,
                   {"objective": objective, "mode": mode, "status": status, "stage": stage, "origin": origin,
                    "updated_at": updated, "finished_at": finished, "usage": _json(usage), "error": error},
                   permissions=_perm(**_WEB, retention="until_forgotten"))


def _claim(r):
    id, task, text, key, ckind, polarity, values, url, domain, published, retrieved, status, conf, created = r
    return _record("claim", id, {"context": "web", "root": None, "task": task}, created,
                   {"text": text, "key": key, "claim_kind": ckind, "polarity": polarity, "values": _json(values),
                    "source_url": url, "domain": domain, "published_at": published, "retrieved_at": retrieved,
                    "status": status, "untrusted": True},
                   permissions=_perm(**_WEB, retention="until_forgotten"), confidence=conf)


def _knowledge(r):
    (id, key, version, statement, status, conf, unc, fresh, first, last, vfrom, vuntil, sup_by, contradicting,
     sources, pending_id) = r
    return _record("knowledge", id, {"context": "web", "root": None, "key": key}, last,
                   {"version": version, "statement": statement, "status": status, "uncertainty": unc,
                    "freshness_class": fresh, "first_verified_at": first, "last_verified_at": last,
                    "valid_from": vfrom, "valid_until": vuntil, "superseded_by": sup_by,
                    "contested": bool(_json(contradicting)), "sources": _json(sources), "pending_id": pending_id},
                   permissions=_perm(**_WEB, retention="versioned"), confidence=conf)


def _source(r):
    id, task, url, canonical, domain, title, skind, published, retrieved, mode, sha = r
    return _record("source", id, {"context": "web", "root": None, "task": task}, retrieved,
                   {"url": url, "canonical_url": canonical, "domain": domain, "title": title, "source_kind": skind,
                    "published_at": published, "retrieval_mode": mode, "content_sha256": sha},
                   permissions=_perm(**_WEB, retention="until_forgotten"))


def _monitor(r):
    id, topic, mode, interval, status, by, created, last, next_run, runs = r
    return _record("monitor", id, {"context": "web", "root": None}, created,
                   {"topic": topic, "mode": mode, "interval_seconds": interval, "status": status,
                    "authorized_by": by, "last_run_at": last, "next_run_at": next_run, "runs": runs},
                   permissions=_perm(**_WEB, retention="until_stopped"))


# kind -> (table, columns, time column, order column, filter column map, builder)
_TABLES = {
    "evidence": ("evidence", "seq,id,epoch,channel,action,bit,root,sample_id,actor,consent,provenance,"
                 "acquired_at,recorded_at,retention,expires_at", "recorded_at", "seq", "id", _evidence),
    "consolidated": ("evidence_consolidated", "seq,epoch,channel,root,value,sample_id,consolidated_at",
                     "consolidated_at", "seq", "seq", _consolidated),
    "belief": ("belief_revisions", "id,epoch,state_version,created_at,cause,summary", "created_at", "id", "id", _belief),
    "graph": ("graph_revisions", "id,context,parent_id,status,bits,degree,rows,cols,byte_size,sha256,"
              "rule_version,origin,metrics,decision,created_at,decided_at", "created_at", "id", "id", _graph),
    "dataset": ("datasets", "id,context,split,provenance,rows,cols,sha256,note,created_at,deleted_at",
                "created_at", "id", "id", _dataset),
    "checkpoint": ("checkpoints", "id,path,policy,sha256,bytes,state_version,max_evidence_seq,created_at,"
                   "verified_at,status", "created_at", "id", "id", _checkpoint),
    "deletion": ("deletion_log", "id,target_kind,target_id,requested_at,effects,failure", "requested_at",
                 "id", "id", _deletion),
    "decision": ("decisions", "id,epoch,state_version,decision,scope,reason,created_at", "created_at", "id",
                 "id", _decision),
    "pending": ("pending", "id,epoch,context,kind,scope,question,created_at,resolved_at,resolution",
                "created_at", "id", "id", _pending),
    "research": ("web_tasks", "id,objective,mode,status,stage,origin,created_at,updated_at,finished_at,usage,error",
                 "created_at", "rowid", "id", _research),
    "claim": ("web_claims", "id,task_id,text,key,kind,polarity,claim_values,source_url,domain,published_at,"
              "retrieved_at,cluster_status,confidence,created_at", "created_at", "rowid", "id", _claim),
    "knowledge": ("web_knowledge", "id,key,version,statement,status,confidence,uncertainty,freshness_class,"
                  "first_verified_at,last_verified_at,valid_from,valid_until,superseded_by,contradicting,sources,"
                  "pending_id", "last_verified_at", "id", "id", _knowledge),
    "source": ("web_sources", "id,task_id,url,canonical_url,domain,title,kind,published_at,retrieved_at,"
               "retrieval_mode,content_sha256", "retrieved_at", "id", "id", _source),
    "monitor": ("web_monitors", "id,topic,mode,interval_seconds,status,authorized_by,created_at,last_run_at,"
                "next_run_at,runs", "created_at", "id", "id", _monitor),
}


def _number(name, value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value != value):
        raise ValidationError(f"{name} must be a number")
    return value


def retrieve(db, kind, *, id=None, epoch=None, context=None, channel=None, root=None,
             since=None, until=None, limit=100, status=None, task=None):
    if kind not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    filters = {"epoch": epoch, "context": context, "channel": channel, "root": root, "status": status,
               "task": task}
    for name, value in filters.items():
        if value is not None:
            if name not in _FILTERS[kind]:
                raise ValidationError(f"filter '{name}' does not apply to kind '{kind}'")
            if not isinstance(value, str):
                raise ValidationError(f"{name} must be a string")
    if channel is not None and channel not in ("camera", "word", "reference"):
        raise ValidationError("channel must be camera, word or reference")
    since, until = _number("since", since), _number("until", until)
    if since is not None and until is not None and since > until:
        raise ValidationError("since must not be later than until")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit must be an integer")
    limit = max(1, min(1000, limit))

    table, columns, time_col, order, id_col, build = _TABLES[kind]
    where, params = [], []
    if id is not None:
        if kind in _TEXT_IDS:
            where.append(f"{id_col}=?")
            params.append(str(id))
        else:
            try:
                params.append(int(id))
            except (TypeError, ValueError):
                raise ValidationError(f"id for kind '{kind}' must be an integer") from None
            where.append(f"{id_col}=?")
    for name, value in filters.items():
        if value is not None:
            where.append(f"{_FILTER_COLUMNS.get(kind, {}).get(name, name)}=?")
            params.append(value)
    if since is not None:
        where.append(f"{time_col}>=?")
        params.append(since)
    if until is not None:
        where.append(f"{time_col}<=?")
        params.append(until)
    sql = f"SELECT {columns} FROM {table}" + (" WHERE " + " AND ".join(where) if where else "")
    rows = db.execute(sql + f" ORDER BY {order} DESC LIMIT ?", (*params, limit)).fetchall()
    return {"state_version": store.state_version(db), "items": [build(r) for r in rows]}
