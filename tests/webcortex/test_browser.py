"""BrowserRenderer: JavaScript runs, but every request is fulfilled by SafeFetcher or refused.

The fixture hosts are fake names Chromium cannot resolve (and Chromium's resolver is pinned to NOTFOUND), so any
rendered content proves the request interception path served it.
"""
import pytest

from neuroscifi.webcortex.browser import BrowserRenderer
from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
from neuroscifi.webcortex.types import Budget, BudgetExceeded, FetchError, Usage


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def fetcher_for(web):
    clock = FakeClock()
    return SafeFetcher(NetPolicy(), resolver=web.resolver, connector=web.connector, clock=clock, sleep=clock.sleep)


def renderer_or_skip(fetcher):
    renderer = BrowserRenderer(fetcher, timeout=25.0)
    ok, reason = renderer.available()
    if not ok:
        pytest.skip(f"browser rendering unavailable: {reason}")
    return renderer


SHELL = """<!doctype html><html><head><title>Shell</title></head><body><div id="root"></div>
<script>
fetch('/data.json').then(r => r.json()).then(d => {
  document.getElementById('root').textContent = 'Rendered: ' + d.msg;
});
fetch('http://internal.test/secret').catch(() => {});
fetch('http://127.0.0.1:%d/secret').catch(() => {});
fetch('/submit', {method: 'POST', body: 'x'}).catch(() => {});
try { new WebSocket('ws://app.example/socket'); } catch (e) {}
</script><img src="/pixel.png"></body></html>"""


def test_render_executes_js_through_safe_fetcher(web):
    web.add("app.example", "/", SHELL % web.port)
    web.add("app.example", "/data.json", '{"msg": "hello from the API"}', content_type="application/json")
    web.add("app.example", "/pixel.png", b"\x89PNG", content_type="image/png")
    renderer = renderer_or_skip(fetcher_for(web))
    usage = Usage()
    result = renderer.render("http://app.example/", budget=Budget(max_browser_sessions=1), usage=usage)
    assert result.retrieval_mode == "rendered" and result.content_type == "text/html"
    assert b"Rendered: hello from the API" in result.body
    assert usage.browser_sessions == 1 and usage.requests == 3          # robots.txt, page, data.json
    served = {r["path"] for r in web.requests_to("app.example")}
    assert served == {"/robots.txt", "/", "/data.json"}
    assert web.requests_to("internal.test") == [] and web.requests_to("127.0.0.1") == []
    assert all(r["method"] == "GET" for r in web.log)
    outcomes = {e["url"]: e.get("code") or e["outcome"] for e in renderer.last_events if e["url"]}
    assert outcomes["http://internal.test/secret"] == "blocked_policy"
    assert outcomes[f"http://127.0.0.1:{web.port}/secret"] == "blocked_policy"
    assert outcomes["http://app.example/submit"] == "aborted"
    assert outcomes["http://app.example/pixel.png"] == "aborted"


def test_render_requires_browser_budget(web):
    web.add("app.example", "/", "<p>x</p>")
    renderer = renderer_or_skip(fetcher_for(web))
    with pytest.raises(BudgetExceeded):
        renderer.render("http://app.example/", budget=Budget(max_browser_sessions=0), usage=Usage())
    assert web.log == []


def test_render_refuses_internal_targets_before_launch(web):
    renderer = BrowserRenderer(fetcher_for(web))
    with pytest.raises(FetchError) as info:
        renderer.render("http://internal.test/", budget=Budget(max_browser_sessions=1), usage=Usage())
    assert info.value.code in ("blocked_policy", "disabled")


def test_available_reports_missing_executable(web, monkeypatch):
    monkeypatch.setenv("NSF_CHROMIUM", "/nonexistent/chromium")
    ok, reason = BrowserRenderer(fetcher_for(web)).available()
    assert ok is False and reason
