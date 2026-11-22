"""Bounded live operational validation against the real internet (Lane F, `nsf web bench --live`).

A temporary individual with web access enabled, the real providers (open-webSearch daemon started on a private
loopback port and stopped again, plus the structured APIs) and the real orchestrator. Every scenario records what
happened, including failures; nothing is retried to look better and no outcome is edited. Budgets are explicit and
small (see BUDGETS); the total of network requests charged to tasks and probes is reported.

Scenarios: A recency question; B scientific research plus a heuristic mapping onto NeuroSciFi's own architecture
documents (proposals only, see implications.py); D an owner monitor ticked twice across a reopened database
connection with the clock advanced past its interval; E fact verification of a contestable claim. Probes: SSRF,
robots.txt, JavaScript rendering (only when a browser is available), provider status after the runs.
Scenario C (`nsf web deps`) and F (Pocket) belong to other lanes and are only referenced.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import time

from ...instance import store
from .. import extract, orchestrator, scheduler, state
from .. import services as svc_mod
from ..types import Budget, FetchError, Usage
from .implications import implications

OBJECTIVE_A = "What are the most important developments in open-source AI inference this week?"
OBJECTIVE_B = ("Research recent evidence on dendritic computation and determine whether it affects NeuroSciFi's "
               "learning architecture.")
OBJECTIVE_E = "Is it true that the human brain contains 100 billion neurons?"
MONITOR_TOPIC = "predictive coding"
BUDGETS = {"A": {"max_requests": 30, "max_pages": 5},
           "B": {"max_requests": 60, "max_pages": 10},
           "D": {"max_requests": 30, "max_pages": 5},
           "E": {"max_requests": 40, "max_pages": 8}}
SSRF_URLS = ("http://169.254.169.254/", "http://localhost/")
ROBOTS_URL = "https://www.google.com/search?q=x"
JS_URL = "https://quotes.toscrape.com/js/"
REFERENCES = {"C": "Scenario C (dependency research, `nsf web deps`) is run by Lane E1; see its evidence file.",
              "F": "Scenario F (research from Pocket) is run by Lane E3; see its evidence file."}


def iso(ts):
    return None if ts is None else datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trim(report: dict | None) -> dict | None:
    """The report with long fields cut and timestamps as ISO strings (answers, sources, limitations kept)."""
    if not report:
        return None
    by_n = {s["n"]: s["url"] for s in report.get("sources") or []}
    return {
        "question": report.get("question"), "mode": report.get("mode"), "as_of": iso(report.get("as_of")),
        "live": report.get("live"),
        "answer": [{"text": a.get("text"), "type": a.get("type"), "citations": a.get("citations"),
                    "status": a.get("status"), "confidence": a.get("confidence")} for a in report.get("answer") or []],
        "findings": [{"statement": (f.get("statement") or "")[:400], "status": f.get("status"),
                      "confidence": f.get("confidence"), "independent_sources": f.get("independent_sources"),
                      "citations": f.get("citations"), "section": f.get("section")}
                     for f in (report.get("findings") or [])[:12]],
        "contradictions": [{"a": {"text": c["a"].get("text", "")[:300], "source": by_n.get(c["a"].get("citation"))},
                            "b": {"text": c["b"].get("text", "")[:300], "source": by_n.get(c["b"].get("citation"))},
                            "reason": c.get("reason"), "assessment": c.get("assessment")}
                           for c in (report.get("contradictions") or [])[:10]],
        "verdict": report.get("verdict"), "freshness": dict(report.get("freshness") or {},
                                                             newest_source_at=iso((report.get("freshness") or {})
                                                                                  .get("newest_source_at"))),
        "limitations": report.get("limitations"), "uncertainties": (report.get("uncertainties") or [])[:15],
        "methodology": report.get("methodology"),
        "sources": [{"n": s["n"], "title": (s.get("title") or "")[:160], "url": s["url"], "domain": s.get("domain"),
                     "published_at": iso(s.get("published_at")), "retrieved_at": iso(s.get("retrieved_at")),
                     "retrieval_mode": s.get("retrieval_mode"), "kind": s.get("kind"), "cited": s.get("cited"),
                     "injection_findings": len((s.get("flags") or {}).get("injection") or []),
                     "js_suspected": (s.get("flags") or {}).get("js_suspected")}
                    for s in report.get("sources") or []],
        "usage": report.get("usage"), "budget": report.get("budget"), "consolidation": report.get("consolidation")}


def _failures(db, task_id) -> list[dict]:
    out = []
    for e in state.events(db, task_id):
        if e["kind"] == "error":
            d = e["detail"] or {}
            out.append({"code": d.get("code") or d.get("error"), "url": d.get("url"), "detail": (d.get("detail") or "")[:160]})
        elif e["kind"] == "search":
            d = e["detail"] or {}
            if d.get("failures"):
                out.append({"code": "search_failures", "detail": ", ".join(map(str, d["failures"]))[:300]})
    return out


def _discovery(db, task_id) -> dict:
    """The planned queries and what each search round returned (from the episode log)."""
    queries, rounds = [], []
    for e in state.events(db, task_id):
        d = e["detail"] or {}
        if e["kind"] == "query":
            queries.append({"text": d.get("text"), "kind": d.get("kind"), "round": d.get("round")})
        elif e["kind"] == "search":
            rounds.append({"round": d.get("round"), "results": d.get("results"), "attempts": d.get("attempts"),
                           "failures": d.get("failures")})
    return {"queries": queries, "search_rounds": rounds}


def _task(db, services, objective, *, mode=None, budget=None, origin="bench-live"):
    started = time.perf_counter()
    task = orchestrator.submit(db, objective, time.time(), mode=mode, budget=budget, origin=origin)
    done = orchestrator.run(db, task["id"], services)
    wall = time.perf_counter() - started
    return done, {"task_id": done["id"], "objective": objective, "mode": done["mode"], "status": done["status"],
                  "error": done["error"], "wall_seconds": round(wall, 2), "usage": done["usage"],
                  "budget": done["budget"], "failures": _failures(db, done["id"]),
                  "discovery": _discovery(db, done["id"]), "report": trim(done["result"])}


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:   # a live scenario that breaks is a result to record, not to hide
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


def _summary(rec):
    rep = rec.get("report") or {}
    fresh = (rep.get("freshness") or {}).get("satisfied")
    return (f"{rec.get('mode')}, {len(rep.get('sources') or [])} sources, live={rep.get('live')}, "
            f"freshness satisfied={fresh}, {rec.get('usage', {}).get('requests')} requests, {rec.get('wall_seconds')} s")


def scenario_a(db, services):
    _, rec = _task(db, services, OBJECTIVE_A, budget=BUDGETS["A"])
    rec["summary"] = _summary(rec)
    return rec


def scenario_b(db, services, repo):
    done, rec = _task(db, services, OBJECTIVE_B, mode="scientific", budget=BUDGETS["B"])
    rec["architecture_implications"] = implications(done["result"] or {}, repo)
    rec["summary"] = _summary(rec) + f", {len(rec['architecture_implications']['proposals'])} proposal(s)"
    return rec


def scenario_e(db, services):
    _, rec = _task(db, services, OBJECTIVE_E, budget=BUDGETS["E"])
    verdict = (rec.get("report") or {}).get("verdict") or {}
    rec["summary"] = _summary(rec) + f", verdict={verdict.get('label')} ({verdict.get('confidence')})"
    return rec


def _monitor_view(db, mid):
    mon = next(m for m in scheduler.monitor_list(db) if m["id"] == mid)
    base = mon["baseline"] or {}
    return {"id": mon["id"], "status": mon["status"], "runs": mon["runs"], "interval_seconds": mon["interval_seconds"],
            "last_run_at": iso(mon["last_run_at"]), "next_run_at": iso(mon["next_run_at"]),
            "last_task_id": mon["last_task_id"], "baseline_urls": len(base.get("urls") or {}),
            "baseline_claim_keys": len(base.get("claims") or {})}


def _tick_view(db, tick):
    out = {"ran": tick.get("ran"), "skipped": tick.get("skipped"), "findings": tick.get("findings")}
    out["tasks"] = []
    for r in tick.get("ran") or []:
        task = orchestrator.get(db, r["task_id"])
        out["tasks"].append({"task_id": r["task_id"], "status": task["status"], "usage": task["usage"],
                             "sources": len((task["result"] or {}).get("sources") or []),
                             "live": (task["result"] or {}).get("live"), "failures": _failures(db, r["task_id"]),
                             "discovery": _discovery(db, r["task_id"]),
                             "sources_read": [{"url": x["url"], "retrieval_mode": x["retrieval_mode"],
                                               "published_at": iso(x["published_at"])}
                                              for x in (task["result"] or {}).get("sources") or []]})
    return out


def scenario_d(db, path, services, offset_holder):
    now1 = time.time()
    mon = scheduler.monitor_add(db, MONITOR_TOPIC, now1, interval=3600, budget=BUDGETS["D"], authorized_by="owner")
    started = time.perf_counter()
    tick1 = scheduler.tick(db, now1, services)
    t1 = time.perf_counter() - started
    view1 = _monitor_view(db, mon["id"])
    return {"status": "ok", "monitor": {"topic": MONITOR_TOPIC, "interval_seconds": 3600, "authorized_by": "owner"},
            "tick1": {"at": iso(now1), "wall_seconds": round(t1, 2), **_tick_view(db, tick1)}, "after_tick1": view1}


def scenario_d_second(path, rec, offset=3700.0):
    """Reopen the individual (new connection, new services) and tick with the clock advanced past the interval."""
    db = store.connect(path)
    services = svc_mod.build(db, path, clock=lambda: time.time() + offset)
    try:
        mid = rec["after_tick1"]["id"]
        before = _monitor_view(db, mid)
        now2 = time.time() + offset
        started = time.perf_counter()
        tick2 = scheduler.tick(db, now2, services)
        t2 = time.perf_counter() - started
        after = _monitor_view(db, mid)
        rec.update({"reopened_connection": {"state_preserved": before == rec["after_tick1"], "view": before},
                    "tick2": {"at": iso(now2), "clock_offset_seconds": offset, "wall_seconds": round(t2, 2),
                              **_tick_view(db, tick2)}, "after_tick2": after})
        changes = (tick2.get("findings") or [])
        rec["summary"] = (f"tick1 ran {len(rec['tick1']['ran'] or [])} task(s), tick2 ran {len(tick2.get('ran') or [])}; "
                          f"meaningful changes reported: {len(changes)}; runs={after['runs']}; state preserved across "
                          f"reopen: {rec['reopened_connection']['state_preserved']}")
        rec["status"] = "ok"
        return db, services
    except Exception:
        services.close()
        db.close()
        raise


def probes(db, services) -> dict:
    out = {"ssrf": [], "robots": None, "js": None}
    fetcher = services.fetcher
    for u in SSRF_URLS:
        try:
            r = fetcher.fetch(u, purpose="page", budget=Budget(max_requests=2), usage=Usage())
            out["ssrf"].append({"url": u, "outcome": "fetched", "status": r.status, "blocked": False})
        except FetchError as exc:
            out["ssrf"].append({"url": u, "outcome": exc.code, "detail": exc.detail, "blocked": exc.code == "blocked_policy"})
    usage = Usage()
    try:
        fetcher.fetch(ROBOTS_URL, purpose="page", budget=Budget(max_requests=3), usage=usage)
        out["robots"] = {"url": ROBOTS_URL, "outcome": "fetched", "blocked": False, "requests": usage.requests}
    except FetchError as exc:
        out["robots"] = {"url": ROBOTS_URL, "outcome": exc.code, "detail": exc.detail,
                         "blocked": exc.code == "blocked_robots", "requests": usage.requests}
    out["js"] = js_probe(db, services)
    return out


def js_probe(db, services) -> dict:
    from ..browser import BrowserRenderer
    renderer = BrowserRenderer(services.fetcher)
    ok, why = renderer.available()
    if not ok:
        return {"attempted": False, "reason": why}
    state.set_config(db, {"browser_rendering": True}, time.time())     # the owner switch for this temporary individual
    budget, usage = Budget(max_requests=20, max_pages=5, max_browser_sessions=1), Usage()
    out = {"url": JS_URL, "availability": why, "config_browser_rendering": state.get_config(db).browser_rendering}
    try:
        static = extract.extract(services.fetcher.fetch(JS_URL, purpose="page", budget=budget, usage=usage))
        out["static"] = {"js_suspected": bool(static.flags.get("js_suspected")), "text_chars": len(static.text)}
        started = time.perf_counter()
        rendered = extract.extract(renderer.render(JS_URL, budget=budget, usage=usage))
        out["rendered"] = {"text_chars": len(rendered.text), "retrieval_mode": rendered.retrieval_mode,
                           "seconds": round(time.perf_counter() - started, 2), "excerpt": rendered.text[:200],
                           "sub_requests": [{k: e.get(k) for k in ("url", "outcome", "code")}
                                            for e in renderer.last_events][:20]}
        out["attempted"], out["ok"] = True, True
    except FetchError as exc:
        out.update({"attempted": True, "ok": False, "error": exc.code, "detail": exc.detail[:200]})
    out["usage"] = usage.to_json()
    return out


def run(*, port: int = 3291) -> dict:
    from ..providers.openwebsearch import Daemon, OpenWebSearchProvider
    repo = Path(__file__).resolve().parents[4]
    tmp = Path(tempfile.mkdtemp(prefix="nsf-weblive-"))
    path = tmp / "live.sqlite"
    start = time.time()
    db = store.create(path, start, note="nsf web bench --live (temporary individual)")
    # Brave stays off even when the owner configured a key: its terms (§3(b)(xiii)) forbid benchmarking AI systems
    # with its results, and the benchmark measures the key-free providers (ADR-034 amendment 1).
    state.set_config(db, {"enabled": True, "openwebsearch_url": f"http://127.0.0.1:{port}",
                          "daily_provider_queries": {"brave": 0}}, start)
    daemon = Daemon(port=port, state_dir=tmp / "openwebsearch")
    started_daemon, daemon_info = False, None
    try:
        daemon_info = daemon.start(timeout=30.0)
        started_daemon = bool(daemon_info.get("started"))
    except Exception as exc:  # recorded: the run continues on the structured providers only
        daemon_info = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    services = svc_mod.build(db, path)
    scenarios, extra = {}, {}
    try:
        scenarios["A"] = _guard(scenario_a, db, services)
        scenarios["B"] = _guard(scenario_b, db, services, repo)
        scenarios["E"] = _guard(scenario_e, db, services)
        scenarios["D"] = _guard(scenario_d, db, path, services, None)
        services.close()
        db.close()
        db, services = None, None
        if scenarios["D"].get("status") != "error":
            try:
                db, services = scenario_d_second(path, scenarios["D"])
            except Exception as exc:
                scenarios["D"]["tick2"] = {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
        if db is None:
            db = store.connect(path)
            services = svc_mod.build(db, path)
        extra["probes"] = _guard(probes, db, services)
        extra["providers"] = services.registry.status()
        try:
            cfg = state.get_config(db)
            extra["daemon_health"] = OpenWebSearchProvider(cfg.openwebsearch_url, cfg.engines).health()
        except Exception as exc:
            extra["daemon_health"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        if services is not None:
            services.close()
        stopped = daemon.stop() if started_daemon else {"stopped": False, "reason": "not started by this run"}
    tasks = [r for r in db.execute("SELECT usage FROM web_tasks")]
    requests = sum(int(state.loads(u, {}).get("requests", 0) or 0) for (u,) in tasks)
    queries = sum(int(state.loads(u, {}).get("queries", 0) or 0) for (u,) in tasks)
    probe_requests = 0
    pr = extra.get("probes") or {}
    probe_requests += int((pr.get("robots") or {}).get("requests") or 0)
    probe_requests += int(((pr.get("js") or {}).get("usage") or {}).get("requests") or 0)
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"label": "LIVE: real internet, temporary individual, small budgets; outcomes recorded as they happened",
            "started_at": iso(start), "finished_at": iso(time.time()),
            "daemon": {"port": port, "start": daemon_info, "stop": stopped},
            "budgets": BUDGETS, "scenarios": scenarios, "references": REFERENCES, **extra,
            "totals": {"tasks": len(tasks), "requests": requests + probe_requests, "task_requests": requests,
                       "probe_requests": probe_requests, "search_queries": queries,
                       "note": "requests = network requests charged by SafeFetcher and the open-webSearch provider "
                               "(one per engine call); the daemon's own upstream traffic per engine call is not "
                               "visible beyond that"},
            "completed": True}
