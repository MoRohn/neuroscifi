"""Self-description: one structured, versioned record of what this individual is doing, knows, is unsure of, can do
next and why (Cognitive Genesis §6.2, ADR-053). A functional record built on request from the existing stores; it
says nothing about experience or awareness, and nothing in it is text a model wrote.

build(db, path, now, scope="owner") -> the record (SCHEMA). Every field is {"value", "source", "at", "age_seconds"}:
the function or table it was read from, and when that value was last true (`at` = now for a value computed now from
the stores; the row's own time for a persisted one). The record carries the state_version it was built at, so a reader
can tell whether two records (or a record and a store) describe the same state.

Sections and the five questions they answer (record["answers"] lists the field paths):
  doing      What am I doing?            tasks (running, queued, with origins), goals, open questions, epoch, quiet
  knows      What do I know?             memory inventory, web knowledge counts
  uncertain  What am I uncertain about?  least certain factor of the current epoch's beliefs, open contradictions,
                                         lowest-confidence knowledge in use, unsettled predictions
  can        What can I do next?         capabilities available now (models, speech, search, devices), limits and
                                         budgets left, authorised tools (model tools, MCP tools, voice commands)
  decisions  What evidence supports that decision?  the executive's decision for the current epoch now (computed,
                                         not recorded) with its reason, the recorded decisions with their reasons and
                                         state_version, autonomy decisions, selective-broadcast decisions
  resources  host memory level and reasons, audio, background gate, idle-unloaded services (resources.status)
  provenance software and schema version, model ids, counts of records by provenance kind (imagined included)
  recent     what we were just doing: the latest research a person asked for, by reference (task id, objective,
             device, status); Pocket sees the owner's only when the owner switched pocket_recent on

Scope "family" (a Pocket pairing family) carries only what Pocket may already see (ADR-028): its own tasks, the global
conditions Presence shows a phone (writes, memory level, quiet), its capabilities and the recent research it may see.
Never the owner's questions, decisions, beliefs or tools.

short(record) -> a few plain sentences built from the record's values (the voice command "what are you doing" and the
console's plain-words panel). consistency(db, path, record, now) -> the checks of the record against the existing
surfaces (store.regulation, store.inventory, autonomy.status, resources.status, presence.derive) at the same
state_version. Read-only: building a record writes nothing.
"""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path

SCHEMA = "nsf.self-description/1"
MAX_ITEMS = 8
RECENT_ITEMS = 8
TEXT = 120          # characters of an objective or a key quoted by reference
ANSWERS = {
    "what_am_i_doing": ["doing.tasks", "doing.questions", "doing.goals", "doing.epoch", "doing.quiet"],
    "what_do_i_know": ["knows.memory", "knows.knowledge", "provenance.counts"],
    "what_am_i_uncertain_about": ["uncertain.beliefs", "uncertain.contradictions", "uncertain.low_confidence",
                                  "uncertain.predictions"],
    "what_can_i_do_next": ["decisions.executive_now", "can.capabilities", "can.limits", "can.tools"],
    "what_evidence_supports_that_decision": ["decisions.executive_now", "decisions.executive_recorded",
                                             "decisions.autonomy", "decisions.broadcast"],
}
FAMILY_ANSWERS = {"what_am_i_doing": ["doing.tasks", "doing.quiet"], "what_can_i_do_next": ["can.capabilities",
                                                                                            "can.limits"]}
DEVICE = {"console": "console", "voice": "console voice", "pocket": "Pocket", "pocket-voice": "Pocket voice",
          "cli": "command line", "mcp": "an agent (MCP)", "owner": "owner"}
DECISION_WORDS = {"act": "act on what I have learned", "observe": "wait for readings", "ask": "ask you a question",
                  "test_reference": "ask for a known reference test", "defer": "wait"}
RUNNING = ("discovering", "reading", "evaluating", "synthesizing")
TERMINAL = ("completed", "partial", "failed", "cancelled")
LIVE_KNOWLEDGE = ("provisional", "accepted", "stale")


def _f(value, source, at, now) -> dict:
    """One field: its value, where it was read from, and when it was last true."""
    age = None if at is None else round(max(0.0, float(now) - float(at)), 3)
    return {"value": value, "source": source, "at": at, "age_seconds": age}


def _short(text, n=TEXT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _web(db) -> bool:
    from .webcortex import state as wstate
    return wstate.has_tables(db)


# ---- doing ---------------------------------------------------------------------------------------------------------

def _tasks(db, now, family=None) -> dict:
    from .presence.aggregator import _origin_class
    where, args = "", []
    if family is not None:
        where, args = " AND family=?", [family]
    active = f"status NOT IN ('completed','partial','failed','cancelled'){where}"
    # a proposal (stage 'proposed') waits for the owner's yes and is counted through its question, as Presence does
    running, queued, proposed, at = db.execute(
        "SELECT coalesce(sum(status!='queued' AND coalesce(stage,'')!='proposed'),0), "
        "coalesce(sum(status='queued' AND coalesce(stage,'')!='proposed'),0), "
        f"coalesce(sum(coalesce(stage,'')='proposed'),0), max(updated_at) FROM web_tasks WHERE {active}",
        args).fetchone()
    rows = db.execute("SELECT id, objective, status, stage, origin, created_at, started_at FROM web_tasks "
                      f"WHERE {active} AND coalesce(stage,'')!='proposed' ORDER BY created_at DESC, rowid DESC "
                      f"LIMIT {MAX_ITEMS}", args).fetchall()
    items = [{"id": tid, "objective": _short(objective), "status": status, "stage": stage, "origin": origin,
              "origin_class": _origin_class(origin), "created_at": created, "started_at": started}
             for tid, objective, status, stage, origin, created, started in rows]
    return _f({"running": running, "queued": queued, "proposals_waiting": proposed, "items": items},
              "web_tasks (status, stage, origin)", at if at is not None else now, now)


def _questions(db, now) -> dict:
    rows = db.execute("SELECT id, kind, scope, epoch, created_at FROM pending WHERE resolved_at IS NULL "
                      "ORDER BY created_at DESC, id DESC").fetchall()
    items = [{"id": i, "kind": k, "scope": s, "epoch": e, "created_at": t} for i, k, s, e, t in rows[:MAX_ITEMS]]
    web = sum(1 for r in rows if str(r[2] or "").startswith("web:"))
    at = rows[0][4] if rows else now
    return _f({"open": len(rows), "web": web, "items": items}, "pending (unresolved rows)", at, now)


def current_epoch(db):
    """The epoch most recently started (epochs.created_at, then insertion order), or None."""
    row = db.execute("SELECT name, created_at FROM epochs ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else (None, None)


def _quiet(db, now) -> dict:
    from . import background
    status = background.status(db)
    return _f(status["quiet"], "instance setting quiet (background.status; `nsf quiet`)", status["changed_at"] or None,
              now)


# ---- knows ---------------------------------------------------------------------------------------------------------

def _provenance_counts(db, now) -> dict:
    count = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    out = {"evidence": {"observed_readings": count("SELECT count(*) FROM evidence WHERE provenance='observed'"),
                        "simulated_environment_readings": count("SELECT count(*) FROM evidence WHERE "
                                                                "provenance='simulated'"),
                        "consolidated_readings": count("SELECT count(*) FROM evidence_consolidated")},
           "prediction": {}, "imagined": {}}
    if _web(db):
        out["evidence"] |= {"web_sources": count("SELECT count(*) FROM web_sources"),
                            "web_claims": count("SELECT count(*) FROM web_claims"),
                            "web_knowledge": count("SELECT count(*) FROM web_knowledge")}
        out["prediction"] = {"hypotheses": count("SELECT count(*) FROM web_hypotheses"),
                             "predictions": count("SELECT count(*) FROM web_predictions")}
    from .instance import imagined
    if imagined.present(db):
        kinds = dict(db.execute("SELECT kind, count(*) FROM imagined GROUP BY kind").fetchall())
        out["imagined"] = {k: kinds.get(k, 0) for k in imagined.KINDS}
        out["imagined_label"] = imagined.NOT_EVIDENCE
    return _f(out, "evidence, evidence_consolidated, web_* and imagined tables (provenance.mapping)", now, now)


# ---- uncertain -----------------------------------------------------------------------------------------------------

def _beliefs(db, epoch, now) -> dict:
    """The current epoch's factors, least certain first. least_certain: 'mechanism' while the mechanism is not
    identifiable, else the camera or partner factor nearest p=0.5 within the identifiability margin, else 'nothing'."""
    from .instance import beliefs
    source = "instance.beliefs.compute (exact posterior, computed on read)"
    if epoch is None:
        return _f({"epoch": None, "least_certain": None, "factors": []}, source, now, now)
    result = beliefs.compute(db, epoch)
    margin = beliefs.IDENTIFIABLE_MARGIN
    factors = [{"scope": "mechanism", "p_inverted": result["mechanism"]["p_inverted"],
                "identifiable": bool(result["mechanism"]["identifiable"]), "n": None}]
    for prefix, group in (("camera", result.get("sensors") or {}), ("partner", result.get("partners") or {})):
        for root, v in group.items():
            p = v.get("p_inverted")
            if p is None:
                continue
            factors.append({"scope": f"{prefix}:{root}", "p_inverted": p, "identifiable": abs(p - .5) >= margin,
                            "n": v.get("n")})
    for f in factors:
        f["distance_from_even"] = round(abs(f["p_inverted"] - .5), 6)
    if not factors[0]["identifiable"]:
        least = "mechanism"
    else:
        open_ = sorted((f for f in factors[1:] if not f["identifiable"]),
                       key=lambda f: (f["distance_from_even"], f["scope"]))
        least = open_[0]["scope"] if open_ else "nothing"
    factors.sort(key=lambda f: (f["distance_from_even"], f["scope"]))
    newest = db.execute("SELECT max(recorded_at) FROM evidence WHERE epoch=?", (epoch,)).fetchone()[0]
    return _f({"epoch": epoch, "least_certain": least, "margin": margin, "factors": factors[:MAX_ITEMS]}, source,
              newest if newest is not None else now, now)


def _contradictions(db, now) -> dict:
    rows = db.execute("SELECT id, key, origin, created_at FROM web_hypotheses WHERE status='open' AND origin IN "
                      "('conflicting','disputed') ORDER BY created_at DESC LIMIT ?", (MAX_ITEMS,)).fetchall()
    contested = db.execute("SELECT count(*) FROM web_knowledge WHERE status IN ('provisional','accepted','stale') AND "
                           "contradicting NOT IN ('[]','')").fetchone()[0]
    open_n = db.execute("SELECT count(*) FROM web_hypotheses WHERE status='open' AND origin IN "
                        "('conflicting','disputed')").fetchone()[0]
    return _f({"open_hypotheses": open_n, "contested_knowledge": contested,
               "items": [{"hypothesis_id": i, "key": _short(k), "origin": o, "created_at": t} for i, k, o, t in rows]},
              "web_hypotheses (open, from conflicting or disputed claims), web_knowledge.contradicting",
              rows[0][3] if rows else now, now)


def _low_confidence(db, now) -> dict:
    rows = db.execute("SELECT id, key, status, confidence, last_verified_at FROM web_knowledge WHERE status IN "
                      "('provisional','accepted','stale') ORDER BY confidence, id LIMIT ?", (MAX_ITEMS,)).fetchall()
    return _f([{"knowledge_id": i, "key": _short(k), "status": s, "confidence": c, "last_verified_at": t}
               for i, k, s, c, t in rows],
              "web_knowledge (live items, lowest confidence first)", min((r[4] for r in rows), default=now), now)


def _predictions(db, now) -> dict:
    rows = db.execute("SELECT id, knowledge_id, hypothesis_id, probability, check_after, deadline FROM web_predictions "
                      "WHERE outcome IS NULL ORDER BY check_after, id LIMIT ?", (MAX_ITEMS,)).fetchall()
    n = db.execute("SELECT count(*) FROM web_predictions WHERE outcome IS NULL").fetchone()[0]
    return _f({"unsettled": n, "items": [{"prediction_id": i, "knowledge_id": k, "hypothesis_id": h,
                                          "probability": p, "check_after": c, "deadline": d}
                                         for i, k, h, p, c, d in rows]},
              "web_predictions (no outcome yet, soonest check first)", now, now)


# ---- can -----------------------------------------------------------------------------------------------------------

def _locality(url) -> str:
    from urllib.parse import urlsplit
    import ipaddress
    host = (urlsplit(url).hostname or "").lower() if url else ""
    if not host:
        return "not configured"
    try:
        return "this host" if host == "localhost" or ipaddress.ip_address(host).is_loopback else "another host"
    except ValueError:
        return "another host"


def _models(db, path, resources, now) -> dict:
    from .inference import config as iconfig
    out = {}
    for provider in iconfig.PROVIDERS:
        try:
            cfg = iconfig.get_config(db, provider)
        except Exception:
            continue
        out[provider] = {"configured": cfg.configured, "model": cfg.model or None, "where": _locality(cfg.base_url)}
        if provider == "chat":
            out[provider] |= {"reasoning": bool(cfg.reasoning), "tools_offered": bool(cfg.tools)}
    services = (((resources.get("policy") or {}).get("idle_unload") or {}).get("services") or {})
    local = {}
    for name, svc in services.items():
        state = (svc or {}).get("state")
        local[name] = {"state": state, "unloaded": state == "unloaded"}
    out["local_services"] = local
    return _f(out, "inference config rows (no key, no URL) + resources.idle state (no network call)", now, now)


def _speech(db, now) -> dict:
    from .voice import config as vconfig
    from .voice import state as vstate
    cfg = vconfig.get(db)
    measured = vstate.engines()
    return _f({"default_mode": cfg["mode"], "asr_model": cfg["asr_model"], "where": _locality(cfg["asr_url"]),
               "engines_last_measured": measured},
              "voice settings (meta 'voice') + voice.state.engines (this process, last 60 s; None = not measured)",
              now, now)


def _search(db, now) -> dict:
    from .webcortex import state as wstate
    cfg = wstate.get_config(db)
    return _f({"web_access": cfg.enabled, "providers": list(cfg.providers), "autonomous_switch": cfg.autonomous,
               "pocket_research": cfg.pocket_research, "pocket_recent": bool(getattr(cfg, "pocket_recent", False))},
              "web settings (webcortex.state.get_config)", now, now)


def pocket_db_path(path) -> Path | None:
    return Path(path).with_name(Path(path).stem + ".pocket.sqlite") if path else None


def _devices(db, path, now) -> dict:
    from .webcortex import nodes
    enrolled = sum(1 for n in nodes.listing(db) if n["state"] == "enrolled")
    pairings = None
    pocket = pocket_db_path(path)
    if pocket is not None and pocket.is_file():
        try:
            with closing(sqlite3.connect(f"file:{pocket}?mode=ro", uri=True, timeout=0.5)) as con:
                devices = con.execute("SELECT count(*) FROM devices").fetchone()[0]
                families = con.execute("SELECT count(DISTINCT family) FROM members").fetchone()[0]
            pairings = {"devices": devices, "families": families}
        except sqlite3.Error:
            pairings = None
    return _f({"research_nodes_enrolled": enrolled, "pocket_pairings": pairings},
              "autonomy_nodes + the Pocket pairing file <db>.pocket.sqlite (read-only; None = not found)", now, now)


def _limits(db, path, resources, epoch, now) -> dict:
    from .instance import store
    from .webcortex import autonomy, state as wstate
    cfg = store.config(db)
    allowed, why = store.write_allowed(db, path)
    gate = ((resources.get("policy") or {}).get("background_research") or {})
    out = {"writes": _f({"allowed": allowed, "reason": why}, "instance.store.write_allowed", now, now),
           "background_research": _f({"allowed": gate.get("allowed"), "reason": gate.get("reason")},
                                     "resources.policy.background_gate (audio, memory pressure)",
                                     resources.get("measured_at") or now, now),
           "journal": _f({"evidence_rows": db.execute("SELECT count(*) FROM evidence").fetchone()[0],
                          "max_evidence_rows": cfg.max_evidence_rows, "min_free_bytes": cfg.min_free_bytes},
                         "instance settings + evidence journal", now, now)}
    asked = None
    if epoch is not None:
        window = cfg.ask_budget_window_seconds
        asked = db.execute("SELECT count(*) FROM pending WHERE epoch=? AND kind IN ('ask','calibrate')"
                           + (" AND created_at>?" if window else ""),
                           (epoch, now - window) if window else (epoch,)).fetchone()[0]
    out["ask_budget"] = _f({"per_epoch": cfg.ask_budget_per_epoch, "window_seconds": cfg.ask_budget_window_seconds,
                            "used_in_current_epoch": asked}, "instance settings + pending (executive ask budget)",
                           now, now)
    if _web(db):
        status = autonomy.status(db, now)
        wcfg = wstate.get_config(db)
        open_web = db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:%'"
                              ).fetchone()[0]
        out["autonomy"] = _f({"enabled": status["enabled"], "switch": status["switch"],
                              "remaining": {"autonomous": status["autonomous"]["remaining"],
                                            "user": status["user"]["remaining"]},
                              "refusal": status["autonomous"]["refusal"], "day": status["day"]},
                             "webcortex.autonomy.status (daily budgets, recorded switch)", now, now)
        out["web_questions"] = _f({"open": open_web, "max_open": wcfg.max_open_web_questions},
                                  "pending (scope web:...) + web settings", now, now)
    return out


def _tools(now) -> dict:
    from .inference import tools as mtools
    from .voice import commands
    from .webcortex import mcp
    granted = mtools.default_registry()
    model = [{"name": t.name, "permission": t.permission, "granted_by_default": t.permission in granted.granted}
             for t in mtools.TOOLS]
    mcp_tools = [{"name": t["name"], "read_only": bool(t["annotations"].get("readOnlyHint"))} for t in mcp._tools()]
    voice = sorted(set(commands.KINDS))
    return _f({"model_tools": model, "mcp_tools": mcp_tools, "voice_commands": voice,
               "voice_owner_only": sorted(commands.OWNER_ONLY)},
              "inference.tools (default grant), webcortex.mcp tool list, voice.commands grammar", now, now)


# ---- decisions -----------------------------------------------------------------------------------------------------

def _executive_now(db, path, epoch, now) -> dict:
    from .instance import executive
    source = "instance.executive.decide (computed now, not recorded)"
    if epoch is None:
        return _f(None, source, now, now)
    d = executive.decide(db, epoch, now, record=False, path=path)
    return _f({"epoch": epoch, "decision": d["decision"], "scope": d["scope"], "reason": d["reason"],
               "pending_id": d["pending_id"]}, source, now, now)


def _executive_recorded(db, now) -> dict:
    rows = db.execute("SELECT epoch, state_version, decision, scope, reason, created_at FROM decisions "
                      "ORDER BY id DESC LIMIT ?", (5,)).fetchall()
    items = [{"epoch": e, "state_version": v, "decision": d, "scope": s, "reason": r, "at": t}
             for e, v, d, s, r, t in rows]
    return _f(items, "decisions (recorded by the executive, with their reasons)", rows[0][5] if rows else None, now)


def _autonomy_decisions(db, now) -> dict:
    out = []
    for task_id, detail, at in db.execute(
            "SELECT task_id, detail, created_at FROM web_events WHERE kind='decision' AND "
            "json_extract(detail,'$.autonomy') IS NOT NULL ORDER BY id DESC LIMIT 5"):
        value = (_json(detail) or {}).get("autonomy")
        if isinstance(value, dict):
            what, reason = "queued an investigation", value.get("objective")
        else:
            what, reason = str(value), (_json(detail) or {}).get("reason")
        out.append({"at": at, "task_id": task_id, "what": _short(what), "detail": _short(reason) if reason else None})
    return _f(out, "web_events kind=decision (autonomy)", out[0]["at"] if out else None, now)


def _broadcast_decisions(db, now) -> dict:
    from . import broadcast
    items = broadcast.log(db, limit=5)
    return _f(items, "web_events kind=decision (broadcast; selective broadcast log)", items[0]["at"] if items else None,
              now)


def _json(text):
    try:
        value = json.loads(text) if text else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# ---- recent (continuity) -------------------------------------------------------------------------------------------

def recent(db, now, *, family=None, include_owner=False, limit=RECENT_ITEMS) -> list[dict]:
    """What we were just doing: the latest research a person asked for, newest first, by reference only (task id,
    the question as asked, device, status, time). Owner (family None): every person-asked task, from every device.
    A Pocket family: its own tasks, and the owner's only with include_owner (the owner's pocket_recent switch); an
    owner task is then a reference the phone cannot open (its routes stay family-scoped)."""
    from .webcortex.orchestrator import USER_ORIGIN_PREFIXES, USER_ORIGINS
    if not _web(db):
        return []
    # orchestrator.user_initiated as SQL: a person asked for it
    person = (f"(origin IN ({','.join('?' * len(USER_ORIGINS))})"
              + "".join(" OR origin LIKE ?" for _ in USER_ORIGIN_PREFIXES) + ")")
    args = [*USER_ORIGINS, *(p + "%" for p in USER_ORIGIN_PREFIXES)]
    if family is not None:
        person += " AND (family=?" + (" OR family IS NULL)" if include_owner else ")")
        args.append(family)
    rows = db.execute("SELECT id, objective, status, origin, family, created_at, finished_at FROM web_tasks WHERE "
                      f"{person} ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, int(limit))).fetchall()
    return [{"task_id": tid, "objective": _short(objective), "status": status,
             "device": DEVICE.get(str(origin), "Pocket" if fam else str(origin)), "at": created,
             "finished_at": finished, "openable": family is None or fam == family}
            for tid, objective, status, origin, fam, created, finished in rows]


# ---- the record ----------------------------------------------------------------------------------------------------

def build(db, path, now, *, scope="owner", family=None) -> dict:
    """The self-description record (see the module docstring). `path`: the instance file (free-space check, the
    resources folder, the Pocket pairing file); `family`: the Pocket family digest for scope 'family'."""
    from . import __version__, resources as res
    from .instance import store
    if scope not in ("owner", "family") or (scope == "family") != (family is not None):
        raise ValueError("scope 'family' needs a family digest; scope 'owner' none")
    owner = scope == "owner"
    version = store.state_version(db)
    resources = res.status(str(path) if path else None, full=False)
    web = _web(db)
    epoch, epoch_at = current_epoch(db)
    birth = store.birth(db)
    record = {"schema": SCHEMA, "scope": scope, "built_at": now, "state_version": version,
              "note": "functional self-description from stored records; every field names its source and age; "
                      "no field is text a model wrote"}
    doing = {"quiet": _quiet(db, now)}
    if web:
        doing["tasks"] = _tasks(db, now, family)
    if owner:
        doing["questions"] = _questions(db, now)
        doing["epoch"] = _f(epoch, "epochs (most recently started)", epoch_at, now)
        if web:
            from .webcortex import autonomy
            doing["goals"] = _f([_short(g) for g in autonomy.goals(db, now)[:MAX_ITEMS]],
                                "webcortex.autonomy.goals (active owner monitors; research the owner asked for in the "
                                "last 7 days)", now, now)
    record["doing"] = doing
    level = {"level": resources["level"], "reasons": resources["reasons"],
             "audio_active": resources["audio_active"],
             "background_model_slots": (resources.get("policy") or {}).get("background_model_slots")}
    record["resources"] = _f(level, "resources.status (nsf resources, GET /api/resources; full=False)",
                             resources.get("measured_at") or now, now)
    limits = _limits(db, path, resources, epoch, now)
    if owner:
        record["knows"] = {"memory": _f(store.inventory(db)["memory"], "instance.store.inventory (nsf birth)", now,
                                        now)}
        if web:
            from .webcortex import state as wstate
            record["knows"]["knowledge"] = _f(wstate.counts(db), "webcortex.state.counts (web memory)", now, now)
        uncertain = {"beliefs": _beliefs(db, epoch, now)}
        if web:
            uncertain |= {"contradictions": _contradictions(db, now), "low_confidence": _low_confidence(db, now),
                          "predictions": _predictions(db, now)}
        record["uncertain"] = uncertain
        record["can"] = {"capabilities": {"models": _models(db, path, resources, now), "speech": _speech(db, now),
                                          "devices": _devices(db, path, now)},
                         "limits": limits, "tools": _tools(now)}
        if web:
            record["can"]["capabilities"]["search"] = _search(db, now)
        record["decisions"] = {"executive_now": _executive_now(db, path, epoch, now),
                               "executive_recorded": _executive_recorded(db, now)}
        if web:
            record["decisions"] |= {"autonomy": _autonomy_decisions(db, now),
                                    "broadcast": _broadcast_decisions(db, now)}
        from .inference import config as iconfig
        models = {p: (iconfig.get_config(db, p).model or None) for p in iconfig.PROVIDERS}
        record["provenance"] = {
            "software": _f({"version": __version__, "schema_version": birth.get("schema_version"),
                            "software_at_birth": birth.get("software_version"), "instance_id": birth["instance_id"],
                            "models": models}, "neuroscifi.__version__, birth record, inference config", now, now),
            "counts": _provenance_counts(db, now)}
        record["answers"] = ANSWERS
    else:
        caps = {"search": _search(db, now)} if web else {}
        record["can"] = {"capabilities": caps, "limits": {"writes": limits["writes"],
                                                          "background_research": limits["background_research"]}}
        record["provenance"] = {"software": _f({"version": __version__}, "neuroscifi.__version__", now, now)}
        record["answers"] = FAMILY_ANSWERS
    if web:
        from .webcortex import state as wstate
        share = bool(getattr(wstate.get_config(db), "pocket_recent", False))
        items = recent(db, now, family=family, include_owner=share and not owner)
        record["recent"] = _f(items, "web_tasks asked for by a person (by reference)"
                              + ("" if owner else "; the owner's only with the pocket_recent switch"),
                              items[0]["at"] if items else None, now)
    return record


def value(record, dotted):
    """The value at a dotted field path ('doing.tasks'), or None."""
    node = record
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node.get("value") if isinstance(node, dict) and "source" in node else node


# ---- plain words ---------------------------------------------------------------------------------------------------

def _n(count, one, many=None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def short(record) -> list[str]:
    """A few plain sentences from the record's values (no model): doing, uncertain, next, conditions."""
    out = []
    tasks = value(record, "doing.tasks")
    questions = value(record, "doing.questions")
    parts = []
    if tasks is not None:
        if tasks["running"] or tasks["queued"]:
            parts.append(f"{_n(tasks['running'], 'research task')} running and {tasks['queued']} queued")
        else:
            parts.append("no research is running")
    if questions is not None and questions["open"]:
        parts.append(f"{_n(questions['open'], 'question')} waiting for you")
    if parts:
        out.append("Right now: " + "; ".join(parts) + ".")
    beliefs = value(record, "uncertain.beliefs")
    if beliefs and beliefs.get("epoch"):
        least = beliefs["least_certain"]
        if least == "nothing":
            out.append(f"In epoch {beliefs['epoch']} every factor is settled.")
        else:
            what = "the mechanism" if least == "mechanism" else least.replace(":", " ")
            out.append(f"I'm least certain about {what} in epoch {beliefs['epoch']}.")
    now = value(record, "decisions.executive_now")
    if now:
        words = DECISION_WORDS.get(now["decision"], now["decision"])
        out.append(f"Next I would {words}" + (f" about {now['scope'].replace(':', ' ')}" if now.get("scope") and
                                               now["decision"] == "ask" else "") + ".")
    level = (value(record, "resources") or {}).get("level")
    writes = value(record, "can.limits.writes") or {}
    notes = []
    if level in ("elevated", "critical"):
        notes.append("memory is " + ("nearly full" if level == "critical" else "getting tight"))
    if writes and not writes.get("allowed"):
        notes.append("writes are paused to protect storage")
    if value(record, "doing.quiet"):
        notes.append("quiet is on, so nothing runs unless you ask")
    if notes:
        out.append("Note: " + "; ".join(notes) + ".")
    return out or ["Nothing to report."]


# ---- consistency ---------------------------------------------------------------------------------------------------

def consistency(db, path, record, now) -> dict:
    """Checks of the record against the existing surfaces read now. comparable=False when the state_version moved
    since the record was built (then nothing is compared: the record describes another state)."""
    from . import resources as res
    from .instance import store
    version = store.state_version(db)
    out = {"state_version": record["state_version"], "comparable": version == record["state_version"],
           "checks": 0, "mismatches": []}
    if not out["comparable"]:
        return out

    def check(name, got, want):
        out["checks"] += 1
        if got != want:
            out["mismatches"].append({"field": name, "record": got, "source": want})

    reg = store.regulation(db, path)
    if record["scope"] == "owner":
        check("doing.questions.open", value(record, "doing.questions")["open"], reg["open_questions"])
        check("knows.memory", value(record, "knows.memory"), store.inventory(db)["memory"])
        if _web(db):
            from .presence import aggregator
            from .webcortex import autonomy
            signals = aggregator.derive(db, str(path) if path else None, now, worker_alive=True)["signals"]
            tasks = value(record, "doing.tasks")
            check("doing.tasks.running", tasks["running"], signals["running"])
            check("doing.tasks.queued", tasks["queued"], signals["queued"])
            status = autonomy.status(db, now)
            auto = value(record, "can.limits.autonomy")
            check("can.limits.autonomy.enabled", auto["enabled"], status["enabled"])
            check("can.limits.autonomy.remaining", auto["remaining"]["autonomous"],
                  status["autonomous"]["remaining"])
        check("can.limits.journal.max_evidence_rows", value(record, "can.limits.journal")["max_evidence_rows"],
              reg["config"]["max_evidence_rows"])
    check("can.limits.writes.allowed", value(record, "can.limits.writes")["allowed"], reg["writes_allowed"])
    check("doing.quiet", value(record, "doing.quiet"), reg["config"]["quiet"])
    fresh = res.status(str(path) if path else None, full=False)
    check("resources.level", value(record, "resources")["level"], fresh["level"])
    check("resources.audio_active", value(record, "resources")["audio_active"], fresh["audio_active"])
    check("can.limits.background_research.allowed", value(record, "can.limits.background_research")["allowed"],
          fresh["policy"]["background_research"]["allowed"])
    return out


__all__ = ["ANSWERS", "SCHEMA", "build", "consistency", "current_epoch", "recent", "short", "value"]
