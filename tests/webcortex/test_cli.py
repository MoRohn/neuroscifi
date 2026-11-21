"""`nsf web …` and `nsf forget research|knowledge|source` through neuroscifi.cli.main, offline: FixtureWeb serves
the pages and CorpusProvider stands in for the search engines (via the cli.SERVICES_OPTIONS test hook)."""
import json

import pytest

from neuroscifi.cli import main
from neuroscifi.webcortex import cli as wcli
from test_orchestrator import PAGES, QUESTION, T0, corpus, serve


def nsf(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else captured.err)


@pytest.fixture
def db(tmp_path, capsys):
    path = str(tmp_path / "individual.sqlite")
    assert nsf(capsys, "init", "--db", path)[0] == 0
    return path


@pytest.fixture
def offline(web, monkeypatch):
    serve(web)
    monkeypatch.setattr(wcli, "SERVICES_OPTIONS", {"fixture": web, "providers": [corpus()], "clock": lambda: T0})
    return web


def enable(capsys, db, *extra):
    code, out = nsf(capsys, "web", "config", "--enable", "--set", "min_domain_interval=0", *extra, "--db", db)
    assert code == 0, out
    return out


def test_config_switches_and_validation(capsys, db):
    code, out = nsf(capsys, "web", "config", "--db", db)
    assert code == 0 and out["changed"] == [] and out["config"]["enabled"] is False
    code, out = nsf(capsys, "web", "config", "--enable", "--autonomous", "--pocket-research", "--browser",
                    "--set", 'private_terms=["Zorblax"]', "--set", "cache_max_age_days=7", "--db", db)
    assert code == 0 and out["changed"] == ["autonomous", "browser_rendering", "cache_max_age_days", "enabled",
                                            "pocket_research", "private_terms"]
    cfg = out["config"]
    assert cfg["enabled"] and cfg["autonomous"] and cfg["pocket_research"] and cfg["browser_rendering"]
    assert cfg["private_terms"] == ["Zorblax"] and cfg["cache_max_age_days"] == 7.0
    code, out = nsf(capsys, "web", "config", "--disable", "--no-autonomous", "--no-pocket-research", "--no-browser",
                    "--db", db)
    assert code == 0 and not any(out["config"][k] for k in ("enabled", "autonomous", "pocket_research",
                                                             "browser_rendering"))
    assert nsf(capsys, "web", "config", "--set", "nonsense=1", "--db", db)[0] == 2
    code, err = nsf(capsys, "web", "config", "--set", "engines=[bing", "--db", db)
    assert code == 2 and "not valid JSON" in err
    code, err = nsf(capsys, "web", "config", "--set", "noequals", "--db", db)
    assert code == 2 and "KEY=JSON" in err
    code, err = nsf(capsys, "web", "config", "--set", 'openwebsearch_url="http://example.com:3210"', "--db", db)
    assert code == 2 and "loopback" in err


@pytest.mark.parametrize("argv", [
    ("ask", "What is the latest Zephyr release?"), ("search", "zephyr compiler"), ("fetch", "https://example.com/"),
    ("deps",), ("deps", "--audit"), ("daemon", "start"), ("health", "--probe"), ("work", "--once"),
    ("curiosity", "--propose", "1")])
def test_network_commands_refuse_while_disabled(capsys, db, argv):
    code, err = nsf(capsys, "web", *argv, "--db", db)
    assert code == 2 and "Web access is disabled" in err


def test_disabled_tick_and_status_stay_quiet(capsys, db):
    code, out = nsf(capsys, "web", "tick", "--db", db)
    assert code == 0 and out["skipped"][0]["what"] == "everything"
    code, out = nsf(capsys, "web", "status", "--db", db)
    assert code == 0 and out["enabled"] is False and out["tasks"] == {}
    assert out["node"]["gpu_note"] == "GPU not used by Web Cortex (ADR-022)" and out["node"]["cpus"]
    assert out["public_cache"]["exists"] is False and out["daemon"]["base_url"] == "http://127.0.0.1:3210"
    code, out = nsf(capsys, "web", "health", "--db", db)
    assert code == 0 and out["probed"] is False and "reachable" in out["daemon"]


def test_ask_inline_then_inspect_review_refresh_and_forget(capsys, db, offline):
    enable(capsys, db)
    code, task = nsf(capsys, "web", "ask", QUESTION, "--db", db)
    assert code == 0, task
    assert task["status"] == "completed" and task["result"]["live"] is True and len(task["result"]["sources"]) == 3
    tid = task["id"]
    code, listed = nsf(capsys, "web", "tasks", "--db", db)
    assert code == 0 and listed[0]["id"] == tid and "result" not in listed[0]
    assert nsf(capsys, "web", "tasks", "--status", "queued", "--db", db)[1] == []
    code, shown = nsf(capsys, "web", "show", tid, "--db", db)
    assert code == 0 and shown["result"]["answer"]
    code, ev = nsf(capsys, "web", "evidence", tid, "--db", db)
    assert code == 0 and len(ev["sources"]) == 3 and all(c["untrusted"] for c in ev["claims"])
    code, status = nsf(capsys, "web", "status", "--db", db)
    assert status["tasks"] == {"completed": 1} and status["public_cache"]["exists"] and \
        status["public_cache"]["documents"] >= 3 and status["provider_health"]["corpus"]["successes"] >= 1

    kid = task["result"]["consolidation"]["provisional"][0]
    code, known = nsf(capsys, "web", "knowledge", "--db", db)
    assert code == 0 and kid in [i["id"] for i in known["items"]]
    code, found = nsf(capsys, "web", "knowledge", "Zephyr compiler version", "--db", db)
    assert code == 0 and found["matched"] >= 1
    code, preds = nsf(capsys, "web", "predictions", "--db", db)
    assert code == 0 and any(p["knowledge_id"] == kid for p in preds)
    assert nsf(capsys, "web", "predictions", "--due", "--db", db)[1] == []
    code, reviewed = nsf(capsys, "web", "review", str(kid), "--accept", "--note", "checked", "--db", db)
    assert code == 0 and reviewed["status"] == "accepted" and reviewed["pending_closed"]
    code, refreshed = nsf(capsys, "web", "refresh", str(kid), "--db", db)
    assert code == 0 and refreshed["status"] == "queued" and refreshed["mode"] == "fact_verification"
    code, cancelled = nsf(capsys, "web", "cancel", refreshed["id"], "--db", db)
    assert code == 0 and cancelled["status"] == "cancelled"
    code, err = nsf(capsys, "web", "resume", refreshed["id"], "--db", db)
    assert code == 2 and "cancelled" in err
    code, ran = nsf(capsys, "web", "refresh", str(kid), "--run", "--db", db)
    assert code == 0 and ran["status"] in ("completed", "partial")
    code, archived = nsf(capsys, "web", "archive", str(kid), "--note", "old", "--db", db)
    assert code == 0 and archived["status"] == "archived"
    code, err = nsf(capsys, "web", "reject", str(kid), "--db", db)
    assert code == 2 and "archived" in err   # an archived version is history; it cannot be rejected
    code, gone = nsf(capsys, "web", "forget", str(kid), "--db", db)
    assert code == 0 and gone["status"] != "absent"
    assert nsf(capsys, "web", "review", str(kid), "--accept", "--db", db)[0] == 2
    assert nsf(capsys, "web", "show", "no-such-task", "--db", db)[0] == 2


def test_queue_then_resume_and_worker(capsys, db, offline):
    enable(capsys, db)
    code, queued = nsf(capsys, "web", "ask", QUESTION, "--queue", "--mode", "fact_verification",
                       "--budget-json", '{"max_pages": 2}', "--db", db)
    assert code == 0 and queued["status"] == "queued" and queued["budget"]["max_pages"] == 2
    code, resumed = nsf(capsys, "web", "resume", queued["id"], "--db", db)
    assert code == 0 and resumed["status"] in ("completed", "partial")
    code, second = nsf(capsys, "web", "ask", QUESTION, "--queue", "--db", db)
    code, worked = nsf(capsys, "web", "work", "--once", "--db", db)
    assert code == 0 and worked["processed"][0]["task_id"] == second["id"]
    assert worked["stopped_by_signal"] is False
    assert nsf(capsys, "web", "work", "--once", "--db", db)[1]["processed"] == []
    code, err = nsf(capsys, "web", "ask", QUESTION, "--budget-json", "{oops", "--db", db)
    assert code == 2 and "not valid JSON" in err


def test_worker_stops_on_sigterm(capsys, db, offline, monkeypatch):
    import os
    import signal
    from neuroscifi.webcortex import scheduler
    enable(capsys, db)
    real = scheduler.work

    def work(path, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)   # arrives while the worker waits for tasks
        return real(path, **kwargs)
    monkeypatch.setattr(scheduler, "work", work)
    code, out = nsf(capsys, "web", "work", "--poll", "30", "--db", db)
    assert code == 0 and out["stopped_by_signal"] is True and out["processed"] == []
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL or callable(signal.getsignal(signal.SIGTERM))


def test_ask_privacy_screen_and_authorised_disclosure(capsys, db, offline):
    enable(capsys, db, "--set", 'private_terms=["Zorblax"]')
    code, err = nsf(capsys, "web", "ask", "Zorblax Zephyr compiler version", "--queue", "--db", db)
    assert code == 2 and "private data" in err
    code, err = nsf(capsys, "web", "search", "mail me at someone@example.org", "--db", db)
    assert code == 2 and "private data" in err and "not sent" in err
    code, task = nsf(capsys, "web", "ask", "Zorblax Zephyr compiler version", "--queue", "--allow-private-terms",
                     "--db", db)
    assert code == 0 and task["status"] == "queued"


def test_search_and_fetch_offline(capsys, db, offline):
    enable(capsys, db)
    code, out = nsf(capsys, "web", "search", "Zephyr compiler release", "--limit", "2", "--db", db)
    assert code == 0 and 1 <= len(out["results"]) <= 2 and out["usage"]["queries"] == 1
    assert out["results"][0]["provider"] == "corpus" and "untrusted" in out["notice"]
    assert nsf(capsys, "web", "search", "x", "--limit", "99", "--db", db)[0] == 2
    (host, path_), (title, _, paragraphs) = next(iter(PAGES.items()))
    code, doc = nsf(capsys, "web", "fetch", f"http://{host}{path_}", "--db", db)
    assert code == 0 and doc["title"] == title and doc["text_chars"] > 0 and "markdown" not in doc
    assert "text" not in doc and doc["retrieval_mode"] == "live"
    code, doc = nsf(capsys, "web", "fetch", f"http://{host}{path_}", "--markdown", "--db", db)
    assert code == 0 and paragraphs[0][:30] in doc["markdown"]
    code, err = nsf(capsys, "web", "fetch", "http://internal.test/", "--db", db)
    assert code == 2


def test_monitor_lifecycle_and_tick(capsys, db, offline):
    enable(capsys, db)
    code, mon = nsf(capsys, "web", "monitor", "add", QUESTION, "--interval-hours", "6", "--mode", "fact_verification",
                    "--db", db)
    assert code == 0 and mon["status"] == "active" and mon["interval_seconds"] == 6 * 3600
    assert nsf(capsys, "web", "monitor", "add", "x", "--interval-hours", "0.1", "--db", db)[0] == 2
    mid = str(mon["id"])
    assert [m["id"] for m in nsf(capsys, "web", "monitor", "list", "--db", db)[1]] == [mon["id"]]
    assert nsf(capsys, "web", "monitor", "pause", mid, "--db", db)[1]["status"] == "paused"
    assert nsf(capsys, "web", "monitor", "list", "--status", "active", "--db", db)[1] == []
    assert nsf(capsys, "web", "monitor", "resume", mid, "--db", db)[1]["status"] == "active"
    code, ticked = nsf(capsys, "web", "tick", "--queue", "--max-tasks", "1", "--db", db)
    assert code == 0 and ticked["queued"] and ticked["queued"][0]["origin"] == f"monitor:{mid}"
    assert any("autonomous research is off" in s.get("reason", "") for s in ticked["skipped"])
    assert nsf(capsys, "web", "monitor", "stop", mid, "--db", db)[1]["status"] == "stopped"
    assert nsf(capsys, "web", "monitor", "resume", mid, "--db", db)[0] == 2


def test_tick_runs_due_monitor_inline(capsys, db, offline):
    enable(capsys, db)
    nsf(capsys, "web", "monitor", "add", QUESTION, "--interval-hours", "1", "--db", db)
    code, ticked = nsf(capsys, "web", "tick", "--db", db)
    assert code == 0 and ticked["ran"] and ticked["ran"][0]["status"] in ("completed", "partial")


def test_curiosity_rank_and_propose(capsys, db, offline):
    enable(capsys, db)
    code, task = nsf(capsys, "web", "ask", QUESTION, "--db", db)
    code, ranked = nsf(capsys, "web", "curiosity", "--limit", "5", "--db", db)
    assert code == 0 and ranked["proposed"] == [] and isinstance(ranked["candidates"], list)
    for c in ranked["candidates"]:
        assert {"target", "kind", "utility", "reason"} <= set(c)
    code, proposed = nsf(capsys, "web", "curiosity", "--propose", "1", "--db", db)
    assert code == 0
    for p in proposed["proposed"]:
        assert p["proposed"] is True and p["pending_id"] and p["status"] == "queued"


def test_nsf_forget_research_knowledge_and_source(capsys, db, offline, tmp_path):
    from neuroscifi.webcortex.cache import PublicCache
    enable(capsys, db)
    first = nsf(capsys, "web", "ask", QUESTION, "--db", db)[1]
    kid = first["result"]["consolidation"]["provisional"][0]
    code, out = nsf(capsys, "forget", "knowledge", str(kid), "--db", db)
    assert code == 0 and out["status"] != "absent"
    assert nsf(capsys, "forget", "knowledge", str(kid), "--db", db)[1]["status"] == "absent"
    code, out = nsf(capsys, "forget", "research", first["id"], "--db", db)
    assert code == 0 and out["status"] != "absent"
    assert nsf(capsys, "web", "show", first["id"], "--db", db)[0] == 2

    second = nsf(capsys, "web", "ask", QUESTION, "--db", db)[1]
    host, path_ = next(iter(PAGES))
    url = f"http://{host}{path_}"
    cache_file = tmp_path / "individual.webcache.sqlite"
    cache = PublicCache(cache_file)
    try:
        assert cache.lookup(url) is not None
    finally:
        cache.close()
    code, out = nsf(capsys, "forget", "source", url, "--db", db)
    assert code == 0 and out["public_cache"]["entries_removed"] >= 1
    cache = PublicCache(cache_file)
    try:
        assert cache.lookup(url) is None
    finally:
        cache.close()
    code, ev = nsf(capsys, "web", "evidence", second["id"], "--db", db)
    assert url not in [s["url"] for s in ev["sources"]] and len(ev["sources"]) == 2
    other = list(PAGES)[1][0]
    code, out = nsf(capsys, "forget", "source", other, "--db", db)
    assert code == 0 and out["public_cache"]["entries_removed"] >= 1
    assert nsf(capsys, "forget", "source", "never-seen.example", "--db", db)[1]["status"] == "absent"


def test_bench_reports_clearly_when_unavailable(capsys, db, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1 and name == "bench" or name == "neuroscifi.webcortex.bench":
            raise ImportError("No module named 'neuroscifi.webcortex.bench'")
        return real_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    code = main(["web", "bench", "--anything", "goes"])
    assert code == 2 and "benchmark is not available" in capsys.readouterr().err


def test_bench_passes_arguments_through(capsys, monkeypatch):
    import sys
    import types
    seen = {}
    module = types.ModuleType("neuroscifi.webcortex.bench")
    module.main = lambda argv: seen.setdefault("argv", argv) and 0
    monkeypatch.setitem(sys.modules, "neuroscifi.webcortex.bench", module)
    assert main(["web", "bench", "--offline", "--out", "x.json"]) == 0
    assert seen["argv"] == ["--offline", "--out", "x.json"]


def test_feeds_cache_and_requeue_commands(capsys, db, offline, tmp_path):
    enable(capsys, db)
    code, out = nsf(capsys, "web", "feeds", "add", "https://lab.example/rss", "--db", db)
    assert code == 0 and out["feeds"] == ["https://lab.example/rss"]
    assert nsf(capsys, "web", "feeds", "list", "--db", db)[1]["count"] == 1
    assert nsf(capsys, "web", "feeds", "add", "ftp://x", "--db", db)[0] == 2
    assert nsf(capsys, "web", "feeds", "remove", "https://lab.example/rss", "--db", db)[1]["feeds"] == []
    assert nsf(capsys, "web", "cache", "stats", "--db", db)[1]["public_cache"]["exists"] is False
    done = nsf(capsys, "web", "ask", QUESTION, "--mode", "quick_lookup", "--db", db)[1]
    code, out = nsf(capsys, "web", "cache", "prune", "--db", db)
    assert code == 0 and out["pruned"]["removed_urls"] >= 0 and out["public_cache"]["exists"] is True
    code, out = nsf(capsys, "web", "requeue", done["id"], "--db", db)
    assert code == 2 and "completed" in out
    queued = nsf(capsys, "web", "ask", QUESTION, "--queue", "--db", db)[1]
    nsf(capsys, "web", "cancel", queued["id"], "--db", db)
    code, out = nsf(capsys, "web", "requeue", queued["id"], "--db", db)
    assert code == 0 and out["parent_id"] == queued["id"] and out["requeued"] == "child" and out["origin"] == "cli"
