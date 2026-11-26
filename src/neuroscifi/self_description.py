"""Self-description: one structured, versioned record of what this individual is doing, knows, is unsure of, can do
next and why (Cognitive Genesis §6.2, ADR-053). A functional record built on request from the existing stores; it
says nothing about experience or awareness, and nothing in it is text a model wrote.

build(db, path, now, scope="owner") -> the record (SCHEMA). Every field is {"value", "source", "at", "age_seconds"}:
the function or table it was read from, and when that value was last true (`at` = now for a value computed now from
the stores; the row's own time for a persisted one). The record carries the state_version it was built at, so a reader
can tell whether two records (or a record and a store) describe the same state.

Sections and the five questions they answer (record["answers"] lists the field paths):
  doing      What am I doing?            tasks (running, queued, with origins), goals, open questions, epoch, quiet,
                                         the background loop (the latest background investigation and how it was
                                         admitted, the latest arbitrations, the settled predictions; ADR-056)
  knows      What do I know?             memory inventory, web knowledge counts
  uncertain  What am I uncertain about?  least certain factor of the current epoch's beliefs, open contradictions,
                                         lowest-confidence knowledge in use, unsettled predictions, the monitoring
                                         record of the latest answers (ADR-054), open "to re-check" marks (ADR-055),
                                         how far each learner of real outcomes is from its minimum (owner scope only;
                                         readiness.report, ADR-058)
  can        What can I do next?         capabilities available now (models, speech, search, devices, imagination
                                         settings, ADR-052), limits and budgets left, authorised tools (model tools,
                                         MCP tools, voice commands)
  decisions  What evidence supports that decision?  the executive's decision for the current epoch now (computed,
                                         not recorded) with its reason, the recorded decisions with their reasons and
                                         state_version, autonomy decisions, selective-broadcast decisions, the
                                         improvement ledger's latest decisions (ADR-055)
  resources  host memory level and reasons, audio, background gate, idle-unloaded services (resources.status)
  internal_state  the internal-state record (internal_state.snapshot, ADR-051), owner scope only
  provenance software and schema version, model ids, counts of records by provenance kind (imagined included)
  recent     what we were just doing: the latest research a person asked for, by reference (task id, objective,
             device, status); Pocket sees the owner's only when the owner switched pocket_recent on

Scope "family" (a Pocket pairing family) carries only what Pocket may already see (ADR-028): its own tasks, the global
conditions Presence shows a phone (writes, memory level, quiet; whether background research may start, with a generic
reason: never the audio state or the policy's own reason, which tell when the owner speaks), its capabilities and the
recent research it may see. Never the owner's questions, decisions, beliefs or tools.

Scope "agent" (the MCP self_status tool, review1 S1/S5): the owner scope restricted to what the owner's own research
holds: tasks, questions, goals and recent research of the owner only (family IS NULL; never a Pocket family's), no
absolute path, and every string that can come from the web (task objectives, goals, knowledge keys, autonomy
objectives) inside the untrusted envelope (webcortex.safety.untrusted) the MCP server uses for retrieved content.

Robustness (review1 A1, A4): build() reads in one read transaction (a deferred BEGIN, so a read-only connection works
too): every field describes the same snapshot. Each field is built under its own guard: a part that cannot be read
(a damaged setting row, a missing table) is that field with value None and an "error" naming the failure, and the
rest of the record is built.

short(record) -> a few plain sentences built from the record's values (the voice command "status report", and "what
are you doing" only when the owner turns the voice setting status_questions on; and the console's plain-words panel). consistency(db, path, record, now) -> the checks of the record against the existing
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
    "what_am_i_doing": ["doing.tasks", "doing.questions", "doing.goals", "doing.epoch", "doing.quiet", "doing.cycle"],
    "what_do_i_know": ["knows.memory", "knows.knowledge", "provenance.counts"],
    "what_am_i_uncertain_about": ["uncertain.beliefs", "uncertain.contradictions", "uncertain.low_confidence",
                                  "uncertain.predictions"],
    "what_can_i_do_next": ["decisions.executive_now", "can.capabilities", "can.limits", "can.tools"],
    "what_evidence_supports_that_decision": ["decisions.executive_now", "decisions.executive_recorded",
                                             "decisions.autonomy", "decisions.broadcast", "doing.cycle"],
}
FAMILY_ANSWERS = {"what_am_i_doing": ["doing.tasks", "doing.quiet"], "what_can_i_do_next": ["can.capabilities",
                                                                                            "can.limits"]}
DEVICE = {"console": "console", "voice": "console voice", "pocket": "Pocket", "pocket-voice": "Pocket voice",
          "cli": "command line", "mcp": "an agent (MCP)", "owner": "owner"}
CYCLE_WORDS = {"idle": "a look while nothing else was queued", "tick": "a look on the timer",
               "recheck": "a re-check of something that may be out of date",
               "prediction refresh (predictions.check_due)": "a check of something I expected to still hold",
               "refresh": "a check of something I expected to still hold"}
DECISION_WORDS = {"act": "act on what I have learned", "observe": "wait for readings", "ask": "ask you a question",
                  "test_reference": "ask for a known reference test", "defer": "wait"}
RUNNING = ("discovering", "reading", "evaluating", "synthesizing")
TERMINAL = ("completed", "partial", "failed", "cancelled")
LIVE_KNOWLEDGE = ("provisional", "accepted", "stale")


def _f(value, source, at, now) -> dict:
    """One field: its value, where it was read from, and when it was last true."""
    age = None if at is None else round(max(0.0, float(now) - float(at)), 3)
    return {"value": value, "source": source, "at": at, "age_seconds": age}


def _guarded(source, now, make):
    """make() -> a field (or a dict of fields); a failure becomes that one field with value None and its error, so one
    damaged part never takes the record down (review1 A1)."""
    try:
        return make()
    except Exception as exc:          # noqa: BLE001 - reported in the field, never raised into the reader
        return {"value": None, "source": source, "at": None, "age_seconds": None,
                "error": f"this part could not be read ({type(exc).__name__})"}


def _untrusted(text, source) -> dict:
    """Text that can come from the web, in the envelope the MCP server gives retrieved content (scope "agent")."""
    from .webcortex.safety import untrusted
    return untrusted(str(text), source)


def _short(text, n=TEXT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _web(db) -> bool:
    from .webcortex import state as wstate
    return wstate.has_tables(db)


# ---- doing ---------------------------------------------------------------------------------------------------------

def _tasks(db, now, family=None, own_only=False) -> dict:
    """Running and queued research: a family's own (family), the owner's own (own_only: family IS NULL), or all."""
    from .presence.aggregator import _origin_class
    where, args = "", []
    if family is not None:
        where, args = " AND family=?", [family]
    elif own_only:
        where = " AND family IS NULL"
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


def _family_scopes(db, rows) -> set:
    """The scopes of `rows` (pending rows, scope third) that name a Pocket family's research task."""
    parts = {p for r in rows if str(r[2] or "").startswith("web:") for p in str(r[2]).split(":")[2:] if p}
    if not parts or not _web(db):
        return set()
    family = set()
    listed = sorted(parts)
    for i in range(0, len(listed), 300):
        chunk = listed[i:i + 300]
        family |= {t for (t,) in db.execute(f"SELECT id FROM web_tasks WHERE family IS NOT NULL AND id IN "
                                            f"({','.join('?' * len(chunk))})", chunk)}
    return {r[2] for r in rows if str(r[2] or "").startswith("web:") and family & set(str(r[2]).split(":")[2:])}


def _questions(db, now, own_only=False) -> dict:
    rows = db.execute("SELECT id, kind, scope, epoch, created_at FROM pending WHERE resolved_at IS NULL "
                      "ORDER BY created_at DESC, id DESC").fetchall()
    if own_only:                       # never a question about a Pocket family's research
        theirs = _family_scopes(db, rows)
        rows = [r for r in rows if r[2] not in theirs]
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
        held = imagined.counts(db)
        out["imagined"] = held["counts"]
        out["imagined_label"] = held["label"]
    return _f(out, "evidence, evidence_consolidated, web_* and imagined tables (provenance.mapping)", now, now)


# ---- uncertain -----------------------------------------------------------------------------------------------------

def _beliefs(db, epoch, now) -> dict:
    """The current epoch's factors, least certain first. least_certain: 'mechanism' while the mechanism is not
    identifiable, else the camera or partner factor nearest p=0.5 within the identifiability margin, else 'nothing'."""
    from .instance import beliefs
    source = "instance.beliefs.compute (exact posterior, computed on read)"
    if epoch is None:
        return _f({"epoch": None, "least_certain": None, "factors": [], "readings": 0}, source, now, now)
    readings = db.execute("SELECT count(*) FROM evidence WHERE epoch=?", (epoch,)).fetchone()[0]
    if schema_consolidated(db):
        readings += db.execute("SELECT count(*) FROM evidence_consolidated WHERE epoch=?", (epoch,)).fetchone()[0]
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
    return _f({"epoch": epoch, "least_certain": least, "margin": margin, "factors": factors[:MAX_ITEMS],
               "readings": readings}, source, newest if newest is not None else now, now)


def schema_consolidated(db) -> bool:
    from .instance import schema
    return schema.has_consolidated(db)


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
    if not isinstance(url, str) or not url:         # a damaged settings row holds anything (review1 A1)
        return "not configured"
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return "not readable"
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


FAMILY_GATE_REASON = "background research is waiting for now; your own requests still run"


def _limits(db, path, resources, epoch, now, *, family=False) -> dict:
    """The limits, each its own guarded field. family: the background gate's allowed flag with a generic reason only
    (its own reason names the owner's audio: review1 S6), and nothing but writes and that gate."""
    from .instance import store
    from .webcortex import autonomy, state as wstate

    def writes():
        allowed, why = store.write_allowed(db, path)
        return _f({"allowed": allowed, "reason": why}, "instance.store.write_allowed", now, now)

    def gate():
        if resources is None:
            raise LookupError("resources.status could not be read")
        g = ((resources.get("policy") or {}).get("background_research") or {})
        allowed = g.get("allowed")
        reason = g.get("reason") if not family else None if allowed or allowed is None else FAMILY_GATE_REASON
        return _f({"allowed": allowed, "reason": reason},
                  "resources.policy.background_gate (audio, memory pressure)", resources.get("measured_at") or now,
                  now)

    out = {"writes": _guarded("instance.store.write_allowed", now, writes),
           "background_research": _guarded("resources.policy.background_gate", now, gate)}
    if family:
        return out

    def journal():
        cfg = store.config(db)
        return _f({"evidence_rows": db.execute("SELECT count(*) FROM evidence").fetchone()[0],
                   "max_evidence_rows": cfg.max_evidence_rows, "min_free_bytes": cfg.min_free_bytes},
                  "instance settings + evidence journal", now, now)

    def ask_budget():
        cfg = store.config(db)
        asked = None
        if epoch is not None:
            window = cfg.ask_budget_window_seconds
            asked = db.execute("SELECT count(*) FROM pending WHERE epoch=? AND kind IN ('ask','calibrate')"
                               + (" AND created_at>?" if window else ""),
                               (epoch, now - window) if window else (epoch,)).fetchone()[0]
        return _f({"per_epoch": cfg.ask_budget_per_epoch, "window_seconds": cfg.ask_budget_window_seconds,
                   "used_in_current_epoch": asked}, "instance settings + pending (executive ask budget)", now, now)

    out["journal"] = _guarded("instance settings + evidence journal", now, journal)
    out["ask_budget"] = _guarded("instance settings + pending (executive ask budget)", now, ask_budget)
    if _web(db):
        def auto():
            # the parts of autonomy.status this field shows, without its list of recent decisions: that list scans
            # every decision event of the episode log (27 ms at 500,000 events) and is not shown here (review1 A2)
            pol = autonomy.policy(db)
            mine, theirs = (autonomy._scope_status(db, scope, now, pol) for scope in ("autonomous", "user"))
            return _f({"enabled": autonomy.enabled(db), "switch": wstate.get_config(db).autonomous,
                       "remaining": {"autonomous": mine["remaining"], "user": theirs["remaining"]},
                       "refusal": mine["refusal"], "day": autonomy.day_of(now)},
                      "webcortex.autonomy.status (daily budgets, recorded switch)", now, now)

        def web_questions():
            open_web = db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:%'"
                                  ).fetchone()[0]
            return _f({"open": open_web, "max_open": wstate.get_config(db).max_open_web_questions},
                      "pending (scope web:...) + web settings", now, now)
        out["autonomy"] = _guarded("webcortex.autonomy.status", now, auto)
        out["web_questions"] = _guarded("pending (scope web:...) + web settings", now, web_questions)
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


DECISIONS_READ = 2000     # the latest decision events read at most (web_events_kind_time, schema 19; review1 A2)


def _autonomy_decisions(db, now) -> dict:
    out = []
    for task_id, detail, at in db.execute(
            "SELECT task_id, detail, created_at FROM (SELECT id, task_id, detail, created_at FROM web_events WHERE "
            "kind='decision' ORDER BY created_at DESC, id DESC LIMIT ?) WHERE json_extract(detail,'$.autonomy') IS "
            "NOT NULL ORDER BY created_at DESC, id DESC LIMIT 5", (DECISIONS_READ,)):
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


ARBITRATION_ITEMS = 3


def _cycle(db, now) -> dict:
    """The background loop as the records hold it (ADR-056): the latest background investigation and how it was
    admitted (path, rule, order, the salience score it carried, its place in the priority order), the latest
    arbitrations between background mechanisms (who took a subject, which mechanism lost, by what rule: recorded
    only for a real conflict, a subject the losing mechanism could have launched in that pass with budget left for
    it), and the predictions settled so far and how. Ids, numbers and fixed words only: no objective, key or statement, so nothing
    a forget removes stays here."""
    from .background import ORIGIN_SQL
    row = db.execute(f"SELECT id, origin, status, created_at FROM web_tasks WHERE {ORIGIN_SQL} AND "
                     "origin NOT GLOB 'monitor:*' ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    last = None
    if row is not None:
        tid, origin, status, at = row
        admitted = {"path": "prediction refresh (predictions.check_due)"} if str(origin).startswith("refresh:") \
            else {"path": None}
        for (detail,) in db.execute("SELECT detail FROM web_events WHERE task_id=? AND kind='decision' AND "
                                    "json_extract(detail,'$.autonomy') IS NOT NULL ORDER BY id LIMIT 1", (tid,)):
            picked = (_json(detail) or {}).get("autonomy")
            if isinstance(picked, dict):
                admission = picked.get("admission") or {}
                admitted = {"path": admission.get("path"), "rule": admission.get("rule"),
                            "order": admission.get("order", "priority"), "kind": picked.get("kind"),
                            "salience": (picked.get("salience") or {}).get("score")
                            if isinstance(picked.get("salience"), dict) else None,
                            "priority_rank": picked.get("priority_rank")}
        last = {"task_id": tid, "origin": "refresh" if str(origin).startswith("refresh:") else str(origin),
                "status": status, "at": at, "admitted": admitted}
    arbitration = []
    for tid, detail, at in db.execute(
            "SELECT task_id, detail, created_at FROM (SELECT id, task_id, detail, created_at FROM web_events WHERE "
            "kind='decision' ORDER BY created_at DESC, id DESC LIMIT ?) WHERE json_extract(detail,'$.arbitration') "
            "IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT ?", (DECISIONS_READ, ARBITRATION_ITEMS)):
        value = (_json(detail) or {}).get("arbitration") or {}
        arbitration.append({"task_id": tid, "winner": value.get("winner"), "loser": value.get("loser"),
                            "rule": value.get("rule"), "at": at})
    counts = dict(db.execute("SELECT outcome, count(*) FROM web_predictions WHERE outcome IS NOT NULL GROUP BY "
                             "outcome").fetchall())
    latest = db.execute("SELECT id, outcome, checked_at FROM web_predictions WHERE outcome IS NOT NULL ORDER BY "
                        "checked_at DESC, id DESC LIMIT 1").fetchone()
    predictions = {"settled": sum(counts.values()), "errors": counts.get("error", 0),
                   "confirmed": counts.get("confirmed", 0), "inconclusive": counts.get("inconclusive", 0),
                   "latest": {"prediction_id": latest[0], "outcome": latest[1], "at": latest[2]} if latest else None}
    times = [(last or {}).get("at"), *(a["at"] for a in arbitration), (predictions["latest"] or {}).get("at")]
    newest = max((t for t in times if t is not None), default=None)
    return _f({"last_investigation": last, "arbitration": arbitration, "predictions": predictions},
              "web_tasks (background origins), web_events kind=decision (autonomy, arbitration; ADR-056), "
              "web_predictions (settled outcomes)", newest, now)


# ---- the other Wave 1 records (integration): each read from its own module, with its source and age ----------------

def _internal_state(db, path, now, *, agent=False) -> dict:
    """Lane I's internal-state record (ADR-051), numbers only; each of its fields carries its own source and age.
    Without the per-item knowledge staleness (`nsf internal-state` has it; review1 A2); scope agent: no file path."""
    from . import internal_state
    snap = internal_state.snapshot(db, str(path) if path else None, now, staleness=False)
    if agent:
        snap["history"].pop("file", None)
    return _f(snap, "internal_state.snapshot (ADR-051; `nsf internal-state`)", snap.get("at"), now)


def _answer_monitoring(db, now, own_only=False) -> dict:
    """Lane C's monitoring record (ADR-054) of the latest research results that carry one: the answer confidence
    (none while no fitted map has enough development data, with the record's own "not enough development data"
    note), whether the answer was weak, what was decided, and whether the evidence was enough. own_only (scope
    agent): the owner's own research only (family IS NULL; review1b R4). A confidence stored by an earlier build
    for a stratum no shipped map now applies to is shown as none, with the same note (review1b R6); the stored
    report is not changed."""
    from .webcortex import monitoring
    mine = " AND family IS NULL" if own_only else ""
    latest = (f"SELECT id, updated_at, result FROM web_tasks WHERE status IN ({','.join('?' * len(TERMINAL))})"
              f"{mine} ORDER BY updated_at DESC LIMIT 50")     # the latest finished tasks only: never every report
    rows = db.execute(
        "SELECT id, updated_at, json_extract(result,'$.monitoring.confidence'), "
        "json_extract(result,'$.monitoring.confidence_note'), "
        "json_extract(result,'$.monitoring.below_threshold'), json_extract(result,'$.monitoring.decision'), "
        "json_extract(result,'$.monitoring.sufficiency'), json_extract(result,'$.monitoring.calibration.stratum') "
        f"FROM ({latest}) WHERE json_valid(result) AND "
        "json_extract(result,'$.monitoring.version') IS NOT NULL ORDER BY updated_at DESC LIMIT ?",
        (*TERMINAL, MAX_ITEMS)).fetchall()
    strata = monitoring.calibration().get("strata") or {}
    items = []
    for tid, at, conf, note, below, decision, enough, stratum in rows:
        if conf is not None and not monitoring.applied(strata.get(stratum) if isinstance(stratum, str) else None):
            conf, note = None, monitoring.NOT_ENOUGH_DATA
        items.append({"task_id": tid, "at": at, "confidence": conf, "confidence_note": note,
                      "below_threshold": bool(below), "decision": decision, "sufficiency": _json(enough)})
    return _f(items, "web_tasks.result monitoring record (webcortex/monitoring.py, ADR-054; answer_monitoring). "
              "confidence is null with confidence_note \"not enough development data\" while no fitted map has 30 "
              "development items in 3 blocks (monitoring.applied; webcortex/data/monitoring_calibration.json): none "
              "of the shipped maps has", rows[0][1] if rows else None, now)


def _to_recheck(db, now) -> dict:
    """Lane K's open "to re-check" marks (ADR-055) by subject kind and reason; marks change no belief."""
    from .webcortex import premises
    if not premises.present(db):
        return _f(None, "web_rechecks (schema 18 not present)", None, now)
    counts, newest = {}, None
    for kind, reason, n, at in db.execute(
            "SELECT subject_kind, reason, count(*), max(updated_at) FROM web_rechecks WHERE status IN "
            f"({','.join('?' * len(premises.OPEN))}) GROUP BY subject_kind, reason", premises.OPEN):
        counts.setdefault(kind, {})[reason] = n
        newest = at if newest is None else max(newest, at)
    total = sum(n for by in counts.values() for n in by.values())
    value = {"open": total, "by_kind": counts}
    from .webcortex import recheck, state as wstate
    order = getattr(wstate.get_config(db), "recheck_order", recheck.DEFAULT_ORDER)
    if order != recheck.DEFAULT_ORDER:  # ADR-060: the order the re-check takes its picks in (each pick's decision
        value["order"] = order          # event records why it was taken)
    return _f(value, "web_rechecks (webcortex/premises.py, ADR-055; `nsf knowledge rechecks`)", newest, now)


def _readiness(db, now) -> dict:
    """ADR-058: each learner of real outcomes against its own minimum (readiness.report; counts only)."""
    from . import readiness
    learners = readiness.report(db, now).get("learners") or {}
    keep = ("minimum", "have", "missing", "earliest")
    return _f({name: {k: v for k, v in item.items() if k in keep} for name, item in learners.items()},
              "readiness.report (`nsf readiness`)", now, now)


def _improvement_ledger(db, now) -> dict:
    """Lane K's improvement ledger (ADR-055): the latest decisions, and whether strategy selection ranks."""
    from .webcortex import strategies
    rows = [{k: e[k] for k in ("id", "capability", "level", "decision", "decided_by", "created_at")}
            for e in strategies.ledger(db, limit=MAX_ITEMS)]
    return _f({"strategy_selection_active": strategies.active(db) is True, "latest": rows},
              "improvement_ledger (webcortex/strategies.py, ADR-055; `nsf knowledge ledger`)",
              rows[0]["created_at"] if rows else None, now)


def _imagination(db, now) -> dict:
    """Lane M's imagination settings (ADR-052): what can be asked, and its bounds."""
    from .instance import store
    cfg = store.config(db)
    return _f({"what_if": cfg.imagine_what_if, "questions": cfg.imagine_what_if, "forecast": cfg.imagine_forecast,
               "retrospect": cfg.imagine_retrospect, "scenario_records": cfg.imagine_scenarios,
               "max_seconds": cfg.imagine_max_seconds, "max_bytes": cfg.imagine_max_bytes,
               "commands": ["nsf imagine what-if", "nsf imagine questions", "nsf imagine forecast",
                            "nsf imagine retrospect", "nsf imagine scm", "nsf imagine scenarios"],
               "label": "answers are computed on a private copy and are imagined, not evidence"},
              "InstanceConfig imagine_* (instance/imagine.py, ADR-052; `nsf config`)", now, now)


def _json(text):
    try:
        value = json.loads(text) if text else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# ---- recent (continuity) -------------------------------------------------------------------------------------------

def recent(db, now, *, family=None, include_owner=False, own_only=False, limit=RECENT_ITEMS) -> list[dict]:
    """What we were just doing: the latest research a person asked for, newest first, by reference only (task id,
    the question as asked, device, status, time). Owner (family None): every person-asked task, from every device.
    A Pocket family: its own tasks, and the owner's only with include_owner (the owner's pocket_recent switch); an
    owner task is then a reference the phone cannot open (its routes stay family-scoped). own_only (scope agent):
    the owner's own tasks only (family IS NULL)."""
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
    elif own_only:
        person += " AND family IS NULL"
    rows = db.execute("SELECT id, objective, status, origin, family, created_at, finished_at FROM web_tasks WHERE "
                      f"{person} ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, int(limit))).fetchall()
    return [{"task_id": tid, "objective": _short(objective), "status": status,
             "device": DEVICE.get(str(origin), "Pocket" if fam else str(origin)), "at": created,
             "finished_at": finished, "openable": family is None or fam == family}
            for tid, objective, status, origin, fam, created, finished in rows]


# ---- the record ----------------------------------------------------------------------------------------------------

SCOPES = ("owner", "agent", "family")


def build(db, path, now, *, scope="owner", family=None) -> dict:
    """The self-description record (see the module docstring). `path`: the instance file (free-space check, the
    resources folder, the Pocket pairing file); `family`: the Pocket family digest for scope 'family'; scope 'agent':
    the MCP client's view. One read transaction (a deferred BEGIN: a read-only connection works too) unless the
    caller already holds one, so every field describes the same snapshot (review1 A4); nothing is written."""
    if scope not in SCOPES or (scope == "family") != (family is not None):
        raise ValueError("scope 'family' needs a family digest; scope 'owner' and 'agent' none")
    own = not db.in_transaction
    if own:
        db.execute("BEGIN")
    try:
        return _build(db, path, now, scope, family)
    finally:
        if own and db.in_transaction:
            db.execute("ROLLBACK")          # read only: there is nothing to keep


def _goals(db, now, own_only) -> list[str]:
    """autonomy.goals; own_only (scope agent): the same list from the owner's own research only."""
    from .webcortex import autonomy
    if not own_only:
        return autonomy.goals(db, now)
    out = [t for (t,) in db.execute("SELECT topic FROM web_monitors WHERE status='active' AND authorized_by='owner'")]
    out += [o for o, origin in db.execute("SELECT objective, origin FROM web_tasks WHERE created_at>=? AND family IS "
                                          f"NULL AND NOT {autonomy.AUTONOMOUS_SQL} ORDER BY created_at DESC LIMIT 100",
                                          (now - autonomy.GOAL_WINDOW,)) if not str(origin).startswith("monitor:")]
    return out


def _for_agent(value, sentences):
    """`value` with every string that holds one of `sentences` (a refusal naming damaged settings) replaced by
    store.PAUSED_FOR_REPAIR (scope agent; Wave 4 second fix round, review4 security second pass 2a)."""
    from .instance import store
    if isinstance(value, dict):
        return {k: _for_agent(v, sentences) for k, v in value.items()}
    if isinstance(value, list):
        return [_for_agent(v, sentences) for v in value]
    if isinstance(value, str) and any(s in value for s in sentences):
        return store.PAUSED_FOR_REPAIR
    return value


def _repair_sentences(db) -> list:
    """The sentences that name damaged settings (fixed instance settings, web settings that closed web access)."""
    from .instance import store
    from .webcortex import state as wstate
    out = []
    damaged = store.fixed_damage(db)
    if damaged:
        out.append(store.fixed_damage_reason(damaged))
    if _web(db):
        closed = wstate.closed_settings(db)
        if closed:
            out.append(wstate.disabled_message(closed))
    return out


def _build(db, path, now, scope, family) -> dict:
    from . import __version__, resources as res
    from .instance import store
    owner = scope != "family"              # owner and agent
    agent = scope == "agent"
    g = lambda source, make: _guarded(source, now, make)  # noqa: E731
    version = store.state_version(db)
    try:
        resources = res.status(str(path) if path else None, full=False)
    except Exception:                      # noqa: BLE001 - reported in the resources field
        resources = None
    web = _web(db)
    try:
        epoch, epoch_at = current_epoch(db)
    except Exception:                      # noqa: BLE001 - the epoch's fields report it
        epoch = epoch_at = None
    record = {"schema": SCHEMA, "scope": scope, "built_at": now, "state_version": version,
              "note": "functional self-description from stored records; every field names its source and age; "
                      "no field is text a model wrote"}
    doing = {"quiet": g("background.quiet", lambda: _quiet(db, now))}
    if web:
        doing["tasks"] = g("web_tasks", lambda: _tasks(db, now, family, own_only=agent))
    if owner:
        doing["questions"] = g("pending", lambda: _questions(db, now, own_only=agent))
        doing["epoch"] = _f(epoch, "epochs (most recently started)", epoch_at, now)
        if web:
            doing["goals"] = g("webcortex.autonomy.goals", lambda: _f(
                [_short(x) for x in _goals(db, now, agent)[:MAX_ITEMS]],
                "webcortex.autonomy.goals (active owner monitors; research the owner asked for in the last 7 days)"
                + ("; the owner's own research only" if agent else ""), now, now))
            doing["cycle"] = g("web_tasks, web_events, web_predictions (the background loop)",
                               lambda: _cycle(db, now))
    record["doing"] = doing

    def level():
        if resources is None:
            raise LookupError("resources.status could not be read")
        value = {"level": resources["level"], "reasons": resources["reasons"]}
        if owner:                          # the audio state and slots tell when the owner speaks (review1 S6)
            value |= {"audio_active": resources["audio_active"],
                      "background_model_slots": (resources.get("policy") or {}).get("background_model_slots")}
        return _f(value, "resources.status (nsf resources, GET /api/resources; full=False)",
                  resources.get("measured_at") or now, now)
    record["resources"] = g("resources.status", level)
    limits = _limits(db, path, resources, epoch, now, family=not owner)
    if owner:
        record["knows"] = {"memory": g("instance.store.inventory", lambda: _f(
            store.inventory(db)["memory"], "instance.store.inventory (nsf birth)", now, now))}
        if web:
            from .webcortex import state as wstate
            record["knows"]["knowledge"] = g("webcortex.state.counts", lambda: _f(
                wstate.counts(db), "webcortex.state.counts (web memory)", now, now))
        record["internal_state"] = g("internal_state.snapshot", lambda: _internal_state(db, path, now, agent=agent))
        uncertain = {"beliefs": g("instance.beliefs.compute", lambda: _beliefs(db, epoch, now))}
        if web:
            uncertain |= {"contradictions": g("web_hypotheses", lambda: _contradictions(db, now)),
                          "low_confidence": g("web_knowledge", lambda: _low_confidence(db, now)),
                          "predictions": g("web_predictions", lambda: _predictions(db, now)),
                          "answers": g("web_tasks.result monitoring record",
                                       lambda: _answer_monitoring(db, now, own_only=agent)),
                          "to_recheck": g("web_rechecks", lambda: _to_recheck(db, now))}
            if scope == "owner":           # ADR-058: the owner's scope only (counts; never the agent's or a family's)
                uncertain["outcomes_readiness"] = g("readiness.report", lambda: _readiness(db, now))
        record["uncertain"] = uncertain
        record["can"] = {"capabilities": {
            "models": g("inference config rows", lambda: _models(db, path, resources or {}, now)),
            "speech": g("voice settings", lambda: _speech(db, now)),
            "devices": g("autonomy_nodes + the Pocket pairing file", lambda: _devices(db, path, now)),
            "imagination": g("InstanceConfig imagine_*", lambda: _imagination(db, now))},
            "limits": limits, "tools": g("inference.tools, webcortex.mcp, voice.commands", lambda: _tools(now))}
        if web:
            record["can"]["capabilities"]["search"] = g("web settings", lambda: _search(db, now))
        record["decisions"] = {
            "executive_now": g("instance.executive.decide", lambda: _executive_now(db, path, epoch, now)),
            "executive_recorded": g("decisions", lambda: _executive_recorded(db, now))}
        if web:
            record["decisions"] |= {
                "autonomy": g("web_events kind=decision (autonomy)", lambda: _autonomy_decisions(db, now)),
                "broadcast": g("web_events kind=decision (broadcast)", lambda: _broadcast_decisions(db, now)),
                "improvement_ledger": g("improvement_ledger", lambda: _improvement_ledger(db, now))}
        record["provenance"] = {"software": g("neuroscifi.__version__, birth record, inference config",
                                              lambda: _software(db, now, __version__)),
                                "counts": g("evidence, web_* and imagined tables", lambda: _provenance_counts(db, now))}
        record["answers"] = ANSWERS
    else:
        record["can"] = {"capabilities": {"search": g("web settings", lambda: _search(db, now))} if web else {},
                         "limits": limits}
        record["provenance"] = {"software": _f({"version": __version__}, "neuroscifi.__version__", now, now)}
        record["answers"] = FAMILY_ANSWERS
    if web:
        def recent_field():
            from .webcortex import state as wstate
            share = bool(getattr(wstate.get_config(db), "pocket_recent", False))
            items = recent(db, now, family=family, include_owner=share and not owner, own_only=agent)
            return _f(items, "web_tasks asked for by a person (by reference)"
                      + ("" if owner else "; the owner's only with the pocket_recent switch")
                      + ("; the owner's own research only" if agent else ""), items[0]["at"] if items else None, now)
        record["recent"] = g("web_tasks asked for by a person", recent_field)
    if agent:
        _agent_view(record)
        sentences = _repair_sentences(db)
        if sentences:                       # the owner's to read; the agent is told it is paused for repair
            record = _for_agent(record, sentences)
    return record


def _software(db, now, version) -> dict:
    from .inference import config as iconfig
    from .instance import store
    birth = store.birth(db)
    models = {}
    for provider in iconfig.PROVIDERS:
        try:
            models[provider] = iconfig.get_config(db, provider).model or None
        except Exception:                  # noqa: BLE001 - a damaged settings row: that model is not named
            models[provider] = None
    return _f({"version": version, "schema_version": birth.get("schema_version"),
               "software_at_birth": birth.get("software_version"), "instance_id": birth["instance_id"],
               "models": models}, "neuroscifi.__version__, birth record, inference config", now, now)


def _agent_view(record) -> None:
    """Scope agent: every string that can come from the web goes inside the untrusted envelope (review1 S5)."""
    source = "neuroscifi:self-description/"

    def each(path, key=None):
        node = record
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        items = node.get("value") if isinstance(node, dict) else None
        if isinstance(items, dict):
            items = items.get("items")
        if not isinstance(items, list):
            return
        for i, item in enumerate(items):
            if key is None and isinstance(item, str):
                items[i] = _untrusted(item, source + path)
            elif isinstance(item, dict):
                for k in (key if isinstance(key, tuple) else (key,)):
                    if isinstance(item.get(k), str):
                        item[k] = _untrusted(item[k], source + path)
    each("doing.tasks", "objective")
    each("doing.goals")
    each("uncertain.contradictions", "key")
    each("uncertain.low_confidence", "key")
    each("decisions.autonomy", ("what", "detail"))
    each("recent", "objective")


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
    cycle = value(record, "doing.cycle") or {}
    last = cycle.get("last_investigation")
    if last:
        how = CYCLE_WORDS.get((last.get("admitted") or {}).get("path") or last.get("origin"), "on my own")
        settled = (cycle.get("predictions") or {}).get("settled") or 0
        wrong = (cycle.get("predictions") or {}).get("errors") or 0
        out.append(f"My last background look was {how} ({last.get('status')})"
                   + (f"; {_n(settled, 'prediction')} checked so far, {wrong} turned out wrong" if settled else "")
                   + ".")
    beliefs = value(record, "uncertain.beliefs")
    if beliefs and beliefs.get("epoch"):
        least = beliefs["least_certain"]
        if beliefs.get("readings") == 0:              # nothing to be certain or uncertain about (review1 A16)
            out.append(f"In epoch {beliefs['epoch']} nothing has been read yet.")
        elif least == "nothing":
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

    def part(dotted, key=None):
        """The value at the path (and key); None where the record could not read it (a field with an error)."""
        got = value(record, dotted)
        return got if key is None else got.get(key) if isinstance(got, dict) else None

    reg = store.regulation(db, path)
    if record["scope"] == "owner":
        check("doing.questions.open", part("doing.questions", "open"), reg["open_questions"])
        check("knows.memory", value(record, "knows.memory"), store.inventory(db)["memory"])
        if _web(db):
            from .presence import aggregator
            from .webcortex import autonomy
            signals = aggregator.derive(db, str(path) if path else None, now, worker_alive=True)["signals"]
            check("doing.tasks.running", part("doing.tasks", "running"), signals["running"])
            check("doing.tasks.queued", part("doing.tasks", "queued"), signals["queued"])
            status = autonomy.status(db, now)
            check("can.limits.autonomy.enabled", part("can.limits.autonomy", "enabled"), status["enabled"])
            check("can.limits.autonomy.remaining", (part("can.limits.autonomy", "remaining") or {}).get("autonomous"),
                  status["autonomous"]["remaining"])
        check("can.limits.journal.max_evidence_rows", part("can.limits.journal", "max_evidence_rows"),
              reg["config"]["max_evidence_rows"])
    check("can.limits.writes.allowed", part("can.limits.writes", "allowed"), reg["writes_allowed"])
    check("doing.quiet", value(record, "doing.quiet"), reg["config"]["quiet"])
    fresh = res.status(str(path) if path else None, full=False)
    check("resources.level", part("resources", "level"), fresh["level"])
    if record["scope"] != "family":        # a family's record does not carry the audio state (review1 S6)
        check("resources.audio_active", part("resources", "audio_active"), fresh["audio_active"])
    check("can.limits.background_research.allowed", part("can.limits.background_research", "allowed"),
          fresh["policy"]["background_research"]["allowed"])
    return out


__all__ = ["ANSWERS", "SCHEMA", "SCOPES", "build", "consistency", "current_epoch", "recent", "short", "value"]
