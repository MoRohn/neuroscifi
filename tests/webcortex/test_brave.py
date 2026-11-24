"""Brave Search API provider and keyed-provider plumbing (ADR-034 amendment 1), offline: fake fetchers, FixtureWeb
behind the real SafeFetcher, a fake clock. No test sees the owner's real key directory (tests/conftest.py)."""
import json
import logging
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import cli as wcli
from neuroscifi.webcortex import net, orchestrator, providers, services as svc_mod, state
from neuroscifi.webcortex.cache import PublicCache
from neuroscifi.webcortex.config import WebConfig
from neuroscifi.webcortex.providers import brave as bv, keys
from neuroscifi.webcortex.search import ProviderError, ProviderRegistry
from neuroscifi.webcortex.types import Budget, FetchError, FetchResult, Query, QueryPlan, SearchResult, Usage

KEY = "BSAtestKEY0123456789abcdefXYZ"
T0 = 1_794_905_400.0          # 2026-11-17 08:50:00 UTC


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def keydir(tmp_path, monkeypatch):
    folder = tmp_path / "search-keys"
    monkeypatch.setenv("NSF_SEARCH_KEY_DIR", str(folder))
    return folder


@pytest.fixture
def key(keydir):
    keys.write_key("brave", KEY + "\n")
    return KEY


WEB = {"type": "search", "query": {"original": "x"}, "web": {"type": "search", "results": [
    {"title": "<strong>Fusion</strong> record &amp; more", "url": "https://news.example/fusion",
     "description": "A <strong>new</strong> record.", "age": "2 days ago", "page_age": "2026-11-15T08:30:00",
     "page_fetched": "2026-11-16T10:00:00Z", "language": "en", "meta_url": {"hostname": "news.example"}},
    {"title": "Undated", "url": "https://blog.example/post", "description": "No date here.", "age": "March 3, 2024"},
    {"title": "Old", "url": "https://old.example/a", "description": "Old page.", "page_age": "2019-01-01T00:00:00"},
    {"title": "Bad date", "url": "https://odd.example/b", "description": "x", "page_age": "yesterday"},
    {"title": "Not http", "url": "javascript:alert(1)", "description": "x"},
    {"title": "Duplicate", "url": "https://news.example/fusion", "description": "dup"}]}}
NEWS = {"type": "news", "query": {"original": "x"}, "results": [
    {"type": "news_result", "title": "Breaking: thing", "url": "https://wire.example/1", "description": "Now.",
     "age": "1 hour ago", "page_age": "2026-11-17T07:50:00+00:00", "breaking": True,
     "meta_url": {"hostname": "wire.example"}},
    {"type": "news_result", "title": "Older", "url": "https://wire.example/2", "description": "Then.",
     "page_age": "2026-11-16T12:00:00Z"}]}
LIMIT_HEADERS = {"x-ratelimit-limit": "1, 2000", "x-ratelimit-policy": "1;w=1, 2000;w=2592000",
                 "x-ratelimit-remaining": "0, 1999", "x-ratelimit-reset": "1, 1209600"}


class FakeFetcher:
    """Records every request; answers from `replies` (a FetchResult, an exception, or a JSON payload)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def fetch(self, url, *, method="GET", body=None, headers=None, purpose="page", budget=None, usage=None,
              conditional=True):
        self.calls.append({"url": url, "headers": dict(headers or {}), "purpose": purpose})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, FetchResult):
            return reply
        return FetchResult(url=url, final_url=url, status=200, headers={"content-type": "application/json",
                                                                         **LIMIT_HEADERS},
                           content_type="application/json", body=json.dumps(reply).encode(), retrieved_at=T0)


def answer(status, body=b"", headers=None):
    return FetchResult(url="u", final_url="u", status=status, headers={"content-type": "application/json",
                                                                       **(headers or {})},
                       content_type="application/json", body=body if isinstance(body, bytes) else
                       json.dumps(body).encode(), retrieved_at=T0)


def params(call):
    return {k: v[0] for k, v in parse_qs(urlsplit(call["url"]).query).items()}


# ---- parsing --------------------------------------------------------------------------------------------------

def test_web_results_are_normalised_with_dates_only_from_page_age(key):
    clock = Clock()
    p = bv.BraveProvider(FakeFetcher(WEB), clock=clock)
    out = p.search(Query("fusion record", "web"), 10)
    assert [r.url for r in out] == ["https://news.example/fusion", "https://blog.example/post",
                                    "https://old.example/a", "https://odd.example/b"]
    first = out[0]
    assert first.title == "Fusion record & more" and first.snippet == "A new record."
    assert (first.provider, first.engine, first.rank, first.kind) == ("brave", "web", 1, "web")
    assert first.published_at == 1_794_731_400.0                  # 2026-11-15T08:30:00, no offset: UTC
    meta = first.metadata
    assert meta["engines"] == ["web"] and meta["transient"] is True and meta["date_basis"] == bv.DATE_BASIS
    assert meta["age"] == "2 days ago" and meta["page_fetched"] == "2026-11-16T10:00:00Z"
    assert meta["source"] == "news.example" and meta["canonical_url"] and meta["retrieved_at"] == T0
    undated, bad = out[1], out[3]
    assert undated.published_at is None and undated.metadata["age"] == "March 3, 2024"   # never parsed into a date
    assert bad.published_at is None and "date_basis" not in bad.metadata
    since = p.search(Query("fusion record", "web", since=T0 - 30 * 86400), 10)   # freshness=pm, and filtered here
    assert params(p.fetcher.calls[-1])["freshness"] == "pm"
    assert "https://old.example/a" not in [r.url for r in since]
    assert "https://blog.example/post" in [r.url for r in since]                 # undated is kept, judged later


def test_news_results_use_the_news_endpoint(key):
    fetcher = FakeFetcher(NEWS)
    out = bv.BraveProvider(fetcher, clock=Clock()).search(Query("thing", "news"), 5)
    assert urlsplit(fetcher.calls[0]["url"]).path == "/res/v1/news/search"
    assert [(r.engine, r.metadata["breaking"] if "breaking" in r.metadata else None) for r in out] == \
        [("news", True), ("news", None)]
    assert out[0].published_at == T0 - 3600.0 and out[0].metadata["source"] == "wire.example"


def test_request_shape(key):
    clock = Clock()
    fetcher = FakeFetcher(WEB)
    p = bv.BraveProvider(fetcher, clock=clock)
    p.search(Query("fusion   record", "web", since=T0 - 5 * 86400, site="example.org"), 7)
    call = fetcher.calls[0]
    assert call["url"].startswith("https://api.search.brave.com/res/v1/web/search?") and call["purpose"] == "api"
    assert call["headers"] == {"X-Subscription-Token": KEY, "Accept": "application/json"}
    assert params(call) == {"q": "fusion record site:example.org", "count": "7", "safesearch": "off",
                            "spellcheck": "false", "text_decorations": "false", "result_filter": "web",
                            "freshness": "pw"}
    p.search(Query(" ".join(f"w{i}" for i in range(90)), "news"), 50)
    news = params(fetcher.calls[1])
    assert len(news["q"].split()) == 50 and news["count"] == "20" and "freshness" not in news
    assert not p.accepts(Query("pypi:numpy", "technical"))


# ---- the key ----------------------------------------------------------------------------------------------------

def test_key_file_is_owner_only_and_insecure_files_are_refused(keydir):
    path = keys.write_key("brave", f"  {KEY}\n")
    assert path == keydir / "brave.key" and path.read_text() == KEY + "\n"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(keydir).st_mode & 0o777) == "0o700"
    assert keys.read_key("brave") == KEY and keys.key_state("brave")["key"] == "configured"
    os.chmod(path, 0o644)
    with pytest.raises(keys.KeyProblem) as info:
        keys.read_key("brave")
    assert info.value.code == "key_insecure" and "0644" in info.value.detail and KEY not in str(info.value)
    assert keys.key_state("brave")["key"] == "insecure"
    fetcher = FakeFetcher(WEB)
    with pytest.raises(ProviderError) as err:
        bv.BraveProvider(fetcher, clock=Clock()).search(Query("x", "web"), 5)
    assert err.value.code == "key_insecure" and fetcher.calls == []
    assert bv.status(clock=Clock())["state"] == "failing"
    path.unlink()
    target = keydir / "elsewhere"
    target.write_text(KEY)
    os.chmod(target, 0o600)
    os.symlink(target, path)
    assert keys.key_state("brave")["key"] == "insecure"
    path.unlink()
    for bad in ("", "has space", "x" * 300, "naïve"):
        with pytest.raises(keys.KeyProblem):
            keys.write_key("brave", bad)
    assert keys.key_state("brave")["key"] == "missing" and bv.status(clock=Clock())["state"] == "unconfigured"


def test_the_key_is_never_logged_shown_or_stored(key, keydir, web, tmp_path, caplog, capsys):
    """Through the real SafeFetcher: the header reaches the API host, the key reaches no log, error, attempt,
    ledger, provider state or status output, even when the API echoes it back."""
    caplog.set_level(logging.DEBUG)
    clock = Clock()
    fetcher = net.SafeFetcher(net.NetPolicy(min_domain_interval=0), resolver=web.resolver, connector=web.connector,
                              cache=PublicCache(tmp_path / "c.sqlite"), clock=clock, sleep=clock.sleep)
    host = "api.search.brave.com"
    web.add(host, "/res/v1/web/search", json.dumps(WEB), content_type="application/json", headers=LIMIT_HEADERS)
    p = bv.BraveProvider(fetcher, clock=clock)
    p.BASE = f"http://{host}/res/v1"
    out = ProviderRegistry([p], clock=clock).search(QueryPlan(queries=(Query("fusion", "web"),)),
                                                     budget=Budget(), usage=Usage())
    assert out.results and web.requests_to(host)[0]["headers"]["x-subscription-token"] == KEY
    web.add(host, "/res/v1/news/search", json.dumps({"type": "ErrorResponse", "error": {
        "code": "SUBSCRIPTION_TOKEN_INVALID", "detail": f"The token {KEY} is invalid", "status": 401}}),
        status=401, content_type="application/json")
    failed = ProviderRegistry([p], clock=clock).search(QueryPlan(queries=(Query("fusion", "news"),)),
                                                        budget=Budget(), usage=Usage())
    assert failed.failures[0]["error"].startswith("key_rejected: HTTP 401")
    echoed = bv.BraveProvider(FakeFetcher(answer(401, {"error": {"code": "SUBSCRIPTION_TOKEN_INVALID",
                                                                 "detail": f"token {KEY} invalid"}})), clock=clock)
    with pytest.raises(ProviderError) as err:
        echoed.search(Query("x", "web"), 5)
    assert err.value.code == "key_rejected" and "token [key] invalid" in err.value.detail and KEY not in str(err.value)
    evidence = json.dumps([out.to_json(), failed.to_json(), str(vars(p)), str(vars(echoed)), bv.status(clock=clock),
                           (keydir / "brave.usage.json").read_text(), str(err.value)], default=str)
    captured = capsys.readouterr()
    for text in (caplog.text, captured.out, captured.err, evidence, repr(p)):
        assert KEY not in text
    assert bv.status(clock=clock)["key"] == "configured"


def test_a_cross_origin_redirect_drops_the_key_header(web, tmp_path):
    clock = Clock()
    fetcher = net.SafeFetcher(net.NetPolicy(min_domain_interval=0), resolver=web.resolver, connector=web.connector,
                              cache=None, clock=clock, sleep=clock.sleep)
    web.add_redirect("api.brave.test", "/search", "http://other.test/landing")
    web.add("other.test", "/landing", "{}", content_type="application/json")
    fetcher.fetch("http://api.brave.test/search", purpose="api", headers={"X-Subscription-Token": KEY})
    assert web.requests_to("api.brave.test")[0]["headers"]["x-subscription-token"] == KEY
    assert "x-subscription-token" not in web.requests_to("other.test")[0]["headers"]


def test_set_key_reads_a_file_or_stdin_never_argv_and_prints_only_configured(keydir, tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "i.sqlite")
    assert main(["init", "--db", db]) == 0
    capsys.readouterr()
    source = tmp_path / "brave-key.txt"
    source.write_text(KEY + "\n")
    source.chmod(0o600)
    assert main(["web", "provider", "set-key", "brave", "--from-file", str(source), "--db", db]) == 0
    out = capsys.readouterr().out
    view = json.loads(out)
    assert KEY not in out and view["key"] == "configured" and view["routing"]["web"][0] == "brave"
    assert keys.read_key("brave") == KEY
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("BSAsecondKEY987\n"))
    assert main(["web", "provider", "set-key", "brave", "--stdin", "--db", db]) == 0
    assert "BSAsecondKEY987" not in capsys.readouterr().out and keys.read_key("brave") == "BSAsecondKEY987"
    with pytest.raises(SystemExit):                   # no positional slot for a key on the command line
        main(["web", "provider", "set-key", "brave", KEY, "--db", db])
    assert KEY not in capsys.readouterr().out
    assert main(["web", "provider", "set-key", "brave", "--db", db]) == 2
    assert "never from the command line" in capsys.readouterr().err
    assert main(["web", "provider", "status", "--db", db]) == 0
    status = capsys.readouterr().out
    assert "BSAsecondKEY987" not in status and json.loads(status)["providers"]["brave"]["key"] == "configured"
    assert main(["web", "provider", "remove-key", "brave", "--db", db]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is True and keys.key_state("brave")["key"] == "missing"


def test_set_key_refuses_a_source_file_others_could_read_a_symlink_or_another_owners_file(keydir, tmp_path, capsys,
                                                                                          monkeypatch):
    """R6 D1: a 0644 source file was accepted. The source must be as private as the stored key."""
    import os
    db = str(tmp_path / "i.sqlite")
    assert main(["init", "--db", db]) == 0
    capsys.readouterr()
    source = tmp_path / "brave-key.txt"
    source.write_text(KEY + "\n")
    for mode in (0o644, 0o640, 0o604):
        source.chmod(mode)
        assert main(["web", "provider", "set-key", "brave", "--from-file", str(source), "--db", db]) == 2
        captured = capsys.readouterr()
        assert f"mode {mode:04o}" in captured.err and f"chmod 600 {source}" in captured.err
        assert KEY not in captured.out + captured.err and keys.key_state("brave")["key"] == "missing"
    source.chmod(0o600)
    link = tmp_path / "link.txt"
    link.symlink_to(source)
    assert main(["web", "provider", "set-key", "brave", "--from-file", str(link), "--db", db]) == 2
    assert "symbolic link" in capsys.readouterr().err and keys.key_state("brave")["key"] == "missing"
    real_getuid = os.getuid
    monkeypatch.setattr(os, "getuid", lambda: real_getuid() + 1)
    assert main(["web", "provider", "set-key", "brave", "--from-file", str(source), "--db", db]) == 2
    assert "not owned by this user" in capsys.readouterr().err
    monkeypatch.setattr(os, "getuid", real_getuid)
    assert keys.key_state("brave")["key"] == "missing"
    assert main(["web", "provider", "set-key", "brave", "--from-file", str(source), "--db", db]) == 0
    assert json.loads(capsys.readouterr().out)["key"] == "configured" and keys.read_key("brave") == KEY


def test_status_health_and_console_never_contain_the_key(key, tmp_path, capsys):
    from neuroscifi.console import server
    db_path = tmp_path / "i.sqlite"
    db = store.create(db_path, T0)
    try:
        cfg = state.get_config(db)
        for out in (wcli._provider_cmd(db, type("A", (), {"action": "status", "name": None})(), T0),
                    wcli._keyed_view(db, cfg, T0), server._provider_health(db, cfg, T0)):
            text = json.dumps(out, default=str)
            assert KEY not in text and '"configured"' in text
        row = next(r for r in server._provider_health(db, cfg, T0) if r["name"] == "brave")
        assert row["key"] == "configured" and row["daily_cap"] == bv.DEFAULT_DAILY_CAP
    finally:
        db.close()


def test_a_stored_key_with_cap_0_is_off_not_unconfigured(key, tmp_path):
    """R6 D9: key configured and cap 0 showed state 'unconfigured'. It is 'off (cap 0)' in the CLI and console."""
    from neuroscifi.console import server
    view = bv.status(daily_cap=0, clock=Clock())
    assert view["key"] == "configured" and view["state"] == "off" and view["state_note"].startswith("off (cap 0)")
    keys.remove_key("brave")
    assert bv.status(daily_cap=0, clock=Clock())["state"] == "unconfigured"
    keys.write_key("brave", KEY)
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        state.set_config(db, {"daily_provider_queries": {"brave": 0}}, T0)
        cfg = state.get_config(db)
        assert wcli._keyed_view(db, cfg, T0)["brave"]["state"] == "off"
        row = next(r for r in server._provider_health(db, cfg, T0) if r["name"] == "brave")
        assert row["health"] == "off" and row["key"] == "configured"
    finally:
        db.close()


# ---- rate limits, Retry-After and the daily cap ---------------------------------------------------------------

def test_rate_limit_headers_parse():
    parsed = bv.rate_limit(LIMIT_HEADERS)
    assert [w["window"] for w in parsed["windows"]] == [1.0, 2592000.0] and parsed["exhausted"] is None
    out = bv.rate_limit({**LIMIT_HEADERS, "x-ratelimit-remaining": "1, 0", "x-ratelimit-reset": "1, 3600"})
    assert out["exhausted"]["window"] == 2592000.0 and out["exhausted"]["reset"] == 3600.0
    assert bv.rate_limit({"x-ratelimit-remaining": "a, b"}) == {"windows": [], "exhausted": None}


def test_an_exhausted_quota_window_blocks_until_reset_and_the_fallback_answers(key):
    clock = Clock()
    exhausted = answer(200, WEB, {**LIMIT_HEADERS, "x-ratelimit-remaining": "1, 0", "x-ratelimit-reset": "1, 3600"})
    fetcher = FakeFetcher(exhausted)
    p = bv.BraveProvider(fetcher, clock=clock)
    assert p.search(Query("a", "web"), 5)                     # this answer counts; the quota is now used up
    with pytest.raises(ProviderError) as err:
        p.search(Query("b", "web"), 5)
    assert err.value.code == "rate_limited" and 3599 <= err.value.retry_after <= 3600 and len(fetcher.calls) == 1
    assert bv.status(clock=clock)["state"] == "rate_limited"

    class Fallback:
        name, kinds, general_web, fallback_for, calls = "searxng", frozenset({"web", "news"}), True, "brave", 0

        def search(self, query, limit, *, budget=None, usage=None):
            Fallback.calls += 1
            return [SearchResult(url="https://sx.example/1", title="s", snippet="", provider="searxng",
                                 engine="mwmbl")]

    reg = ProviderRegistry([p, Fallback()], clock=clock)
    out = reg.search(QueryPlan(queries=(Query("c", "web"),)), budget=Budget(), usage=Usage())
    assert Fallback.calls == 1 and [r.provider for r in out.results] == ["searxng"]
    assert {s["name"]: s for s in reg.status()}["brave"]["failures"] == 0        # politeness is health-neutral
    clock.t += 3601
    assert p.search(Query("d", "web"), 5) and len(fetcher.calls) == 2


def test_429_with_retry_after_is_honoured_through_safefetcher(key, keydir, web, tmp_path):
    clock = Clock()
    fetcher = net.SafeFetcher(net.NetPolicy(min_domain_interval=0), resolver=web.resolver, connector=web.connector,
                              cache=None, clock=clock, sleep=clock.sleep)
    host = "api.search.brave.com"
    web.add(host, "/res/v1/web/search", json.dumps({"error": {"code": "RATE_LIMITED"}}), status=429,
            content_type="application/json", headers={"Retry-After": "30"})
    p = bv.BraveProvider(fetcher, clock=clock)
    p.BASE = f"http://{host}/res/v1"
    with pytest.raises(ProviderError) as err:
        p.search(Query("a", "web"), 5)
    assert err.value.code == "rate_limited" and err.value.retry_after == 30.0
    assert keys.Ledger("brave", clock=clock).read()["queries"] == 0          # a 429 is not billed: refunded
    with pytest.raises(ProviderError) as again:
        p.search(Query("b", "web"), 5)
    assert again.value.code == "rate_limited" and len(web.requests_to(host)) == 1   # no request inside the wait
    clock.t += 31
    web.add(host, "/res/v1/web/search", json.dumps(WEB), content_type="application/json")
    assert p.search(Query("c", "web"), 5) and len(web.requests_to(host)) == 2


def test_the_daily_cap_is_enforced_per_key_and_resets_at_utc_midnight(key):
    clock = Clock()
    fetcher = FakeFetcher(WEB)
    p = bv.BraveProvider(fetcher, daily_cap=2, clock=clock)
    other = bv.BraveProvider(fetcher, daily_cap=2, clock=clock)     # another process or task, the same key
    p.search(Query("one", "web"), 5)
    other.search(Query("two", "web"), 5)
    with pytest.raises(ProviderError) as err:
        p.search(Query("three", "web"), 5)
    assert err.value.code == "daily_cap" and len(fetcher.calls) == 2
    assert p.search(Query("one", "web"), 5)                         # a cached answer costs nothing
    view = bv.status(daily_cap=2, clock=clock)
    assert view["state"] == "rate_limited" and view["used_today"] == 2 and "daily cap" in view["rate_limit_reason"]
    clock.t += 86400
    assert p.search(Query("four", "web"), 5) and len(fetcher.calls) == 3
    failing = bv.BraveProvider(FakeFetcher(answer(500, b"oops")), daily_cap=5, clock=clock)
    with pytest.raises(ProviderError) as err:
        failing.search(Query("five", "web"), 5)
    assert err.value.code == "http_error" and keys.Ledger("brave", clock=clock).read()["queries"] == 1   # refunded
    off = bv.BraveProvider(fetcher, daily_cap=0, clock=clock)
    with pytest.raises(ProviderError) as err:
        off.search(Query("six", "web"), 5)
    assert err.value.code == "daily_cap" and len(fetcher.calls) == 3


def test_daily_cap_configuration(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        assert state.get_config(db).daily_provider_queries == {"brave": bv.DEFAULT_DAILY_CAP}
        assert state.set_config(db, {"daily_provider_queries": {"brave": 5}}, T0).daily_provider_queries == \
            {"brave": 5}
        for bad in ({"google": 3}, {"brave": -1}, {"brave": 1.5}, {"brave": True}, []):
            with pytest.raises(ValidationError):
                state.set_config(db, {"daily_provider_queries": bad}, T0)
    finally:
        db.close()
    assert providers.daily_cap(WebConfig(daily_provider_queries={"brave": 7}), "brave") == 7


# ---- routing --------------------------------------------------------------------------------------------------

SX = "http://127.0.0.1:8888"


def test_routing_without_a_key_is_unchanged(keydir):
    names = [p.name for p in providers.build(WebConfig(searxng_url=SX), fetcher=None)]
    assert names[:2] == ["searxng", "openwebsearch"] and "brave" not in names
    assert not hasattr(providers.build(WebConfig(searxng_url=SX), fetcher=None)[0], "fallback_for")


def test_routing_with_a_key_makes_brave_primary(key):
    built = providers.build(WebConfig(searxng_url=SX), fetcher=None)
    assert [p.name for p in built][:3] == ["brave", "searxng", "openwebsearch"]
    assert built[1].fallback_for == "brave" and built[2].fallback_for == "searxng"
    alone = providers.build(WebConfig(), fetcher=None)
    assert [p.name for p in alone][:2] == ["brave", "openwebsearch"] and alone[1].fallback_for == "brave"
    named = providers.build(WebConfig(searxng_url=SX, providers=["wikipedia", "brave", "openwebsearch"]), None)
    assert [p.name for p in named] == ["searxng", "wikipedia", "brave", "openwebsearch"]
    off = providers.build(WebConfig(searxng_url=SX, daily_provider_queries={"brave": 0}), fetcher=None)
    assert "brave" not in [p.name for p in off] and not hasattr(off[0], "fallback_for")


class Fake:
    general_web = True

    def __init__(self, name, found, fallback_for=None, kinds=("web", "news")):
        self.name, self.found, self.calls, self.kinds = name, found, [], frozenset(kinds)
        if fallback_for:
            self.fallback_for = fallback_for

    def search(self, query, limit, *, budget=None, usage=None):
        self.calls.append(query.kind)
        return [SearchResult(url=f"https://{self.name}.example/1", title="t", snippet="", provider=self.name,
                             engine="e")] if self.found else []


@pytest.mark.parametrize("kind", ["web", "news"])
def test_fallbacks_chain_brave_then_searxng_then_open_websearch(kind):
    def run(found):
        b, s, o = Fake("brave", found[0]), Fake("searxng", found[1], "brave"), Fake("openwebsearch", found[2],
                                                                                    "searxng")
        out = ProviderRegistry([b, s, o]).search(QueryPlan(queries=(Query("q", kind),)), budget=Budget(),
                                                 usage=Usage())
        return [len(x.calls) for x in (b, s, o)], sorted({r.provider for r in out.results})
    assert run((True, True, True)) == ([1, 0, 0], ["brave"])
    assert run((False, True, True)) == ([1, 1, 0], ["searxng"])
    assert run((False, False, True)) == ([1, 1, 1], ["openwebsearch"])


def test_technical_queries_still_go_to_searxng_directly():
    b, s = Fake("brave", True), Fake("searxng", True, "brave", kinds=("web", "news", "technical"))
    ProviderRegistry([b, s]).search(QueryPlan(queries=(Query("asyncio timeout", "technical"),)), budget=Budget(),
                                    usage=Usage())
    assert (b.calls, s.calls) == ([], ["technical"])


# ---- privacy and retention ------------------------------------------------------------------------------------

def test_private_data_is_refused_before_any_request(key, tmp_path):
    fetcher = FakeFetcher(WEB)
    db_path = tmp_path / "i.sqlite"
    db = store.create(db_path, T0)
    try:
        state.set_config(db, {"enabled": True, "private_terms": ["Zorblax"]}, T0)
        services = svc_mod.build(db, db_path, providers=[bv.BraveProvider(fetcher, clock=Clock())])
        try:
            for text in ("email me at jane.doe@example.com", "call +1 415 555 0100 today", "zorblax project"):
                with pytest.raises(ValidationError) as info:
                    wcli.web_search(db, services, text)
                assert "not sent anywhere" in str(info.value)
            assert fetcher.calls == [] and keys.Ledger("brave", clock=Clock()).read()["queries"] == 0
            wcli.web_search(db, services, "fusion record")
            assert len(fetcher.calls) == 1
        finally:
            services.close()
    finally:
        db.close()


def test_a_finished_task_keeps_no_transient_result():
    kept = {"url": "https://a.example", "metadata": {"engines": ["mwmbl"]}}
    gone = {"url": "https://b.example", "metadata": {"engines": ["web"], "transient": True}}
    out = orchestrator._without_transient({"results": [kept, gone], "attempts": [1]})
    assert out == {"results": [kept], "attempts": [1], "transient_results_dropped": 1}
    same = {"results": [kept]}
    assert orchestrator._without_transient(same) is same


def test_provider_errors_are_provider_errors_and_only_unbilled_requests_are_refunded(key):
    from neuroscifi.webcortex.types import BudgetExceeded
    ledger = keys.Ledger("brave", clock=Clock())
    for reply, code, counted in (
            (answer(422, {"error": {"code": "VALIDATION", "detail": "q too long"}}), "bad_request", 0),
            (answer(403, b""), "forbidden", 0),
            (FetchError("http_error", "u", "HTTP 500", status=500), "http_error", 0),
            (FetchError("connect_failed", "u", "refused"), "connect_failed", 0),
            (BudgetExceeded("requests", 13, 12), "budget_exhausted", 0),       # refused before sending
            (FetchError("timeout", "u", "slow"), "timeout", 1),                 # may have reached Brave: kept
            (FetchError("too_large", "u", "body exceeds"), "too_large", 1),     # after a 2xx status line
            (BudgetExceeded("bytes", 10, 5), "budget_exhausted", 1),            # while reading the body
            (answer(200, b"not json"), "bad_response", 1)):                     # a 200 is billed
        before = ledger.read()["queries"]
        with pytest.raises(ProviderError) as err:
            bv.BraveProvider(FakeFetcher(reply), clock=Clock()).search(Query("x", "web"), 5)
        assert err.value.code == code and ledger.read()["queries"] - before == counted, (code, counted)


def test_a_damaged_ledger_counts_as_a_used_up_day(key, keydir):
    clock = Clock()
    fetcher = FakeFetcher(WEB)
    (keydir / "brave.usage.json").write_text('{"day": "2026-11-17", "quer')        # cut off mid-write
    with pytest.raises(ProviderError) as err:
        bv.BraveProvider(fetcher, clock=clock).search(Query("x", "web"), 5)
    assert err.value.code == "daily_cap" and fetcher.calls == []
    clock.t += 86400                                                           # the next UTC day starts afresh
    assert bv.BraveProvider(fetcher, clock=clock).search(Query("x", "web"), 5)


def test_a_429_never_shortens_a_quota_block_and_our_own_spacing_is_not_recorded(key):
    clock = Clock()
    ledger = keys.Ledger("brave", clock=clock)
    ledger.note(ok=False, limited_for=3600, reason="quota window of 2592000 s used up")
    ledger.note(ok=False, error="rate_limited: HTTP 429", limited_for=60, reason="Brave answered 429")
    assert ledger.limited() == {"until": T0 + 3600, "reason": "quota window of 2592000 s used up"}
    (ledger.path).unlink()
    spacing = FetchError("rate_limited", "u", "next request slot in 20s", retry_after=20.0)    # SafeFetcher's own
    with pytest.raises(ProviderError) as err:
        bv.BraveProvider(FakeFetcher(spacing), clock=clock).search(Query("x", "web"), 5)
    assert err.value.code == "rate_limited" and err.value.retry_after == 20.0 and ledger.limited() is None


def test_a_key_directory_others_can_write_or_we_cannot_read_is_refused_without_raising(key, keydir):
    os.chmod(keydir, 0o770)
    assert keys.key_state("brave")["key"] == "insecure" and "chmod 700" in keys.key_state("brave")["problem"]
    os.chmod(keydir, 0o000)
    try:
        assert keys.key_state("brave")["key"] == "insecure"                    # never raises: search goes on
        assert [p.name for p in providers.build(WebConfig(searxng_url=SX), fetcher=None)][0] == "brave"
    finally:
        os.chmod(keydir, 0o700)
    assert keys.key_state("brave")["key"] == "configured"


def test_open_websearch_waits_for_brave_while_searxng_is_open():
    def run(found):
        b, s, o = Fake("brave", found), Fake("searxng", True, "brave"), Fake("openwebsearch", True, "searxng")
        reg = ProviderRegistry([b, s, o])
        for _ in range(3):
            reg.record("searxng", False, 0.1, "down")                          # SearXNG's circuit is open
        out = reg.search(QueryPlan(queries=(Query("q", "web"),)), budget=Budget(), usage=Usage())
        return [len(x.calls) for x in (b, s, o)], sorted({r.provider for r in out.results})
    assert run(True) == ([1, 0, 0], ["brave"])
    assert run(False) == ([1, 0, 1], ["openwebsearch"])                        # SearXNG skipped, open circuit


# ---- review round: the owner's stop and the paid daily cap (review F5) ------------------------------------------

def brave_through_gate(web, tmp_path, ask, watch=None):
    """A Brave search through the real SafeFetcher inside the fetcher's gate, as a gated search runs it. Returns
    (the stop raised, queries counted against the daily cap)."""
    from neuroscifi.webcortex.types import Stopped
    clock = Clock()
    fetcher = net.SafeFetcher(net.NetPolicy(min_domain_interval=0), resolver=web.resolver, connector=web.connector,
                              cache=PublicCache(tmp_path / "c.sqlite"), clock=clock, sleep=clock.sleep)
    p = bv.BraveProvider(fetcher, clock=clock, daily_cap=5)
    p.BASE = "http://api.search.brave.com/res/v1"
    raised = None
    with fetcher.gated(ask, watch):
        try:
            p.search(Query("fusion", "web"), 5, budget=Budget(), usage=Usage())
        except Stopped as stop:
            raised = stop
    return raised, int(p.ledger.read().get("queries") or 0)


def test_a_query_stopped_before_it_was_sent_is_given_back_to_the_daily_cap(key, web, tmp_path):
    from neuroscifi.webcortex.types import Stopped
    web.add("api.search.brave.com", "/res/v1/web/search", json.dumps(WEB), content_type="application/json")

    def stop(url):
        raise Stopped("the task was cancelled")
    raised, counted = brave_through_gate(web, tmp_path, stop)
    assert raised is not None and web.log == [] and counted == 0


def test_a_query_cut_on_the_wire_by_the_stop_keeps_its_count(key, web, tmp_path):
    """The request went out and was then cut (the owner stopped the task while Brave answered): Brave may bill it,
    so it stays counted."""
    import threading
    from neuroscifi.webcortex.types import Stopped
    release, stopped = threading.Event(), threading.Event()

    class Slow:
        status, headers, hits = 200, {"Content-Type": "application/json"}, 0
        body = json.dumps(WEB).encode()

        @property
        def delay(self):
            stopped.set()                                     # the request has arrived: the owner stops now
            release.wait(5)
            return 0.0
    web._ip_for("api.search.brave.com")
    web.routes[("api.search.brave.com", "/res/v1/web/search")] = Slow()

    def ask(url):
        if stopped.is_set():
            raise Stopped("the task was cancelled")
    try:
        raised, counted = brave_through_gate(web, tmp_path, ask,
                                             watch=lambda url: "cancelled" if stopped.is_set() else None)
    finally:
        release.set()
    assert raised is not None and len(web.log) == 1 and counted == 1


def test_a_fetcher_that_cannot_say_what_it_sent_keeps_the_count(key):
    """Erring towards the cap: without SafeFetcher.written the provider cannot know, and keeps the query counted."""
    from neuroscifi.webcortex.types import Stopped

    class Gated:
        def fetch(self, url, **kwargs):
            raise Stopped("the task was cancelled")
    p = bv.BraveProvider(Gated(), clock=Clock(), daily_cap=5)
    with pytest.raises(Stopped):
        p.search(Query("fusion", "web"), 5, budget=Budget(), usage=Usage())
    assert p.ledger.read().get("queries") == 1
