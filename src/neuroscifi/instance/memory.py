"""Unified, read-only retrieval across the instance's memories.

Every record has the same envelope: kind, id, scope, time, permissions, confidence, outcome_history, payload.
Association or embedding search is out of scope; retrieval is by identity, scope and time.
"""
import json

from . import store
from .errors import ValidationError

KINDS = ("evidence", "belief", "graph", "dataset", "checkpoint", "deletion", "decision", "pending")
_FILTERS = {  # filters beyond id/since/until that each kind understands
    "evidence": {"epoch", "channel", "root"}, "belief": {"epoch"}, "graph": {"context"}, "dataset": {"context"},
    "checkpoint": set(), "deletion": set(), "decision": {"epoch"}, "pending": {"epoch", "context"},
}


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


def _belief(r):
    id, epoch, version, created, cause, summary = r
    parsed = _json(summary)
    confidence = parsed.get("confidence") if isinstance(parsed, dict) else None
    return _record("belief", id, {"epoch": epoch, "root": None}, created,
                   {"state_version": version, "cause": cause, "summary": parsed},
                   confidence=confidence if isinstance(confidence, (int, float)) else None)


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


# kind -> (table, columns, time column, order column, filter column map, builder)
_TABLES = {
    "evidence": ("evidence", "seq,id,epoch,channel,action,bit,root,sample_id,actor,consent,provenance,"
                 "acquired_at,recorded_at,retention,expires_at", "recorded_at", "seq", "id", _evidence),
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
}


def _number(name, value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value != value):
        raise ValidationError(f"{name} must be a number")
    return value


def retrieve(db, kind, *, id=None, epoch=None, context=None, channel=None, root=None,
             since=None, until=None, limit=100):
    if kind not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    filters = {"epoch": epoch, "context": context, "channel": channel, "root": root}
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
        if kind == "evidence":
            where.append("id=?")
            params.append(str(id))
        else:
            try:
                params.append(int(id))
            except (TypeError, ValueError):
                raise ValidationError(f"id for kind '{kind}' must be an integer") from None
            where.append("id=?")
    for name, value in filters.items():
        if value is not None:
            where.append(f"{name}=?")
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
