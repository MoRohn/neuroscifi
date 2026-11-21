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


EXTERNAL = """<!doctype html><html><head><title>Quotes</title><link rel="stylesheet" href="/style.css"></head>
<body><div id="quotes">loading</div>
<script src="http://internal.test/evil.js"></script>
<script src="http://cdn.example/lib.js"></script>
<script src="/app.js"></script>
</body></html>"""


def test_render_runs_external_scripts_and_styles(web):
    """F4: external scripts and stylesheets are served (purpose='subresource'); one from internal.test is refused."""
    web.add("quotes.example", "/", EXTERNAL)
    web.add("quotes.example", "/style.css", "body { color: rgb(1, 2, 3); }", content_type="text/css")
    web.add("quotes.example", "/app.js", "document.getElementById('quotes').textContent = "
            "quote('Written by an external script') + ' color=' + getComputedStyle(document.body).color;",
            content_type="application/javascript")
    web.add("cdn.example", "/lib.js", "function quote(text) { return '\\u201c' + text + '\\u201d'; }",
            content_type="text/javascript; charset=utf-8")
    renderer = renderer_or_skip(fetcher_for(web))
    usage = Usage()
    result = renderer.render("http://quotes.example/", budget=Budget(max_browser_sessions=1), usage=usage)
    text = result.body.decode()
    assert "“Written by an external script” color=rgb(1, 2, 3)" in text
    assert web.requests_to("internal.test") == []
    outcomes = {e["url"]: e.get("code") or e["outcome"] for e in renderer.last_events if e["url"]}
    assert outcomes["http://internal.test/evil.js"] == "blocked_policy"
    for url in ("http://quotes.example/style.css", "http://quotes.example/app.js", "http://cdn.example/lib.js"):
        assert outcomes[url] == "served"
    assert usage.requests == 6        # robots.txt + page + style + app on quotes.example; robots.txt + lib on cdn


# ---- review remediation (V1): hung renders, blocked navigations, no double fetch --------------------------------

import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from neuroscifi.webcortex import browser as browser_mod  # noqa: E402


def _alive(pid):
    try:
        stat = open(f"/proc/{pid}/stat").read()
    except OSError:
        return False
    return stat[stat.rfind(")") + 2:][:1] != "Z"


class HangingFetcher:
    """Delegates to a SafeFetcher but blocks every sub-resource fetch, recording the processes below this test
    process (Playwright driver + Chromium) while the render hangs."""

    def __init__(self, inner):
        self.inner, self.policy, self._clock = inner, inner.policy, inner._clock
        self.release = threading.Event()
        self.tree: list[int] = []
        self.cmdlines: dict[int, str] = {}

    def fetch(self, url, **kwargs):
        if kwargs.get("purpose") == "subresource":
            self.tree = browser_mod._descendants(os.getpid())
            for pid in self.tree:
                try:
                    self.cmdlines[pid] = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
                except OSError:
                    pass
            self.release.wait(30)
        return self.inner.fetch(url, **kwargs)


def test_hung_render_kills_driver_and_chromium(web, monkeypatch):
    monkeypatch.setattr(browser_mod, "RENDER_GRACE", 1.0)
    web.add("hang.example", "/", '<html><body><p>text</p><script src="/slow.js"></script></body></html>')
    fetcher = HangingFetcher(fetcher_for(web))
    renderer = BrowserRenderer(fetcher, timeout=2.0)
    if not renderer.available()[0]:
        pytest.skip("browser rendering unavailable")
    started = time.monotonic()
    try:
        with pytest.raises(FetchError) as info:
            renderer.render("http://hang.example/", budget=Budget(max_browser_sessions=1), usage=Usage())
        assert info.value.code == "timeout" and time.monotonic() - started < 2.0 + 1.0 + 1.0 + 1.0
        chromium = [p for p, cmd in fetcher.cmdlines.items() if "chrom" in cmd.lower()]
        assert chromium, "the hang was not observed with Chromium running"
        deadline = time.monotonic() + 3
        while any(_alive(p) for p in fetcher.tree) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not [p for p in fetcher.tree if _alive(p)], "driver/Chromium processes outlived the render"
    finally:
        fetcher.release.set()


def test_blocked_navigation_error_page_is_not_content(web):
    page = ('<html><head><title>Real</title></head><body><p>real page text</p>'
            '<script>location.href = "http://internal.test/next";</script></body></html>')
    web.add("nav.example", "/", page)
    renderer = renderer_or_skip(fetcher_for(web))
    result = renderer.render("http://nav.example/", budget=Budget(max_browser_sessions=1), usage=Usage())
    assert b"real page text" in result.body and b"chromewebdata" not in result.body
    assert result.final_url.startswith("http://nav.example/")


def test_render_reuses_the_callers_main_fetch(web):
    web.add("reuse.example", "/", "<html><body><p id='p'>static</p>"
            "<script>document.getElementById('p').textContent = 'rendered';</script></body></html>")
    fetcher = fetcher_for(web)
    usage, budget = Usage(), Budget(max_browser_sessions=1)
    main = fetcher.fetch("http://reuse.example/", purpose="page", budget=budget, usage=usage)
    renderer = renderer_or_skip(fetcher)
    result = renderer.render("http://reuse.example/", budget=budget, usage=usage, main=main)
    assert b"rendered" in result.body and result.retrieval_mode == "rendered"
    assert len(web.requests_to("reuse.example", "/")) == 1 and usage.requests == 2   # robots.txt + page, once


def test_render_reads_text_inside_open_shadow_roots(web):
    """Live: MSN renders its article inside web components; page.content() (light DOM only) returned 35 characters
    ("Continue reading"). Open shadow roots are inlined into the rendered body; closed ones stay unreadable."""
    web.add("wc.example", "/", """<!doctype html><html><body><news-article></news-article><sealed-box></sealed-box>
<script>
customElements.define('news-article', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML =
    '<article><h1>Markets fall</h1><inner-quote></inner-quote><p>Stocks fell 3.7% on Friday.</p></article>'; }
});
customElements.define('inner-quote', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML = '<p>Nested root text.</p>'; }
});
customElements.define('sealed-box', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'closed'}).innerHTML = '<p>closed root text</p>'; }
});
</script></body></html>""")
    from neuroscifi.webcortex import extract
    renderer = renderer_or_skip(fetcher_for(web))
    result = renderer.render("http://wc.example/", budget=Budget(max_browser_sessions=1), usage=Usage())
    text = extract.extract(result).text
    assert "Stocks fell 3.7% on Friday." in text and "Markets fall" in text and "Nested root text." in text
    assert "closed root text" not in text
    plain = "<html><body><p id='p'>light only</p><script>document.getElementById('p').textContent += '!';</script>"
    web.add("light.example", "/", plain + "</body></html>")
    result = renderer.render("http://light.example/", budget=Budget(max_browser_sessions=1), usage=Usage())
    assert b"light only!" in result.body and b"data-shadow-root" not in result.body
