"""Sandboxed JavaScript rendering, every request served through SafeFetcher (ARCHITECTURE §3, ADR-030).

Contract
- `BrowserRenderer(fetcher).render(url, budget=, usage=, main=None)` charges one browser session, fetches the
  document with `fetcher.fetch(purpose="page")` (or uses `main`, the FetchResult the caller already fetched for
  `url`, so the page is neither fetched nor charged twice), then loads it in a fresh headless Chromium context and
  returns the rendered DOM as a FetchResult with retrieval_mode='rendered'. When the browser ends on a non-http(s)
  page (a blocked navigation shows `chrome-error://chromewebdata/`), that page is not content: `main` is returned. Off unless the task budget has browser sessions
  (`config.browser_rendering`); `available()` reports (False, reason) when Playwright or Chromium is missing.
- Isolation: Chromium's own sandbox on, no profile (fresh context per render, closed afterwards), no extensions,
  background networking/sync/component updates off, downloads refused, no permissions, service workers blocked,
  WebSockets never connected (`route_web_socket` keeps them server-less) and, like WebRTC and EventSource,
  their constructors throw (init script in every frame); popups closed, dialogs dismissed.
- Network: `context.route("**/*")` intercepts every request. Only GET/HEAD over http(s) is served, and only through
  `SafeFetcher.fetch(purpose="subresource")`, so the URL/address policy, DNS pinning, robots.txt, throttle (the
  shorter `subresource_interval`, not Crawl-delay), size/type limits and the task budget apply to every
  sub-resource. That purpose also admits scripts and stylesheets (net.SUBRESOURCE_TYPES); its responses are never
  cached and never become documents. Images, media and fonts are aborted (budget), anything refused is aborted.
  Defence in depth for traffic that bypasses routing: all DNS inside Chromium resolves to NOTFOUND, a dead proxy
  is configured, and WebRTC may not use non-proxied UDP. Chromium therefore never opens a
  socket to the page's hosts itself.
- Bounded: one wall-clock `timeout` per render; the rendered body is capped at the fetcher's max_bytes. If the
  render thread is still busy RENDER_GRACE seconds after the timeout, a watchdog SIGKILLs the Playwright driver
  and every process below it (Chromium and its helpers), so a hung render leaks neither a browser nor a thread
  that keeps driving one.
- `last_events` keeps the sub-request outcomes of the latest render ({url (no query), outcome, code}) for audit.
- Shadow DOM: `page.content()` serialises the light DOM only, so text a page renders inside web components (MSN
  articles live in open shadow roots) would be lost. When the page has open shadow roots, the DOM is serialised
  with each open root inlined as a `<div data-shadow-root>` first child of its host (FLATTEN_SCRIPT, at most
  MAX_SHADOW_ROOTS roots); closed roots stay unreadable. Extraction then applies the same hidden-content and
  boilerplate rules to that markup as to any other.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import threading
import time
from urllib.parse import urlsplit

from .net import _charge, _redact, check_url
from .types import BudgetExceeded, FetchError, FetchResult

CHROMIUM_ARGS = [
    "--disable-extensions", "--disable-background-networking", "--disable-sync", "--disable-default-apps",
    "--disable-component-update", "--no-first-run", "--no-default-browser-check", "--disable-domain-reliability",
    "--disable-client-side-phishing-detection", "--disable-breakpad", "--metrics-recording-only",
    "--disable-features=Translate,OptimizationHints,MediaRouter,DialMediaRouteProvider,AutofillServerCommunication,"
    "CertificateTransparencyComponentUpdater,InterestFeedContentSuggestions,NetworkPrediction",
    "--dns-prefetch-disable", "--host-resolver-rules=MAP * ~NOTFOUND",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--mute-audio", "--disable-remote-fonts", "--disable-speech-api", "--disable-gpu",
]
DEAD_PROXY = {"server": "http://127.0.0.1:1"}
BLOCKED_RESOURCES = ("image", "media", "font", "eventsource", "websocket", "manifest", "texttrack", "ping")
BLOCK_SCRIPT = """(() => {
  for (const name of ['WebSocket', 'WebSocketStream', 'RTCPeerConnection', 'webkitRTCPeerConnection', 'EventSource']) {
    const refuse = function () { throw new DOMException(name + ' is blocked by the renderer', 'SecurityError'); };
    try { Object.defineProperty(globalThis, name, {value: refuse, writable: false, configurable: false}); } catch (e) {}
  }
})();"""
SERVED_HEADERS = ("content-type", "content-security-policy", "content-language")
MAX_SHADOW_ROOTS = 2000
# Light-DOM copy of the document with every open shadow root inlined (null when the page has none). Runs once,
# after load, in the page; its output is untrusted page content like the rest of the DOM.
FLATTEN_SCRIPT = """(limit) => {
  if (!Array.from(document.querySelectorAll('*')).some((el) => el.shadowRoot)) return null;
  // copies go into an inert document: no custom-element upgrades, no page code runs while copying
  const inert = document.implementation.createHTMLDocument('');
  let roots = 0;
  const flat = (src) => {
    const dst = inert.importNode(src, false);
    if (src.shadowRoot && roots < limit) {
      roots += 1;
      const box = inert.createElement('div');
      box.setAttribute('data-shadow-root', '');
      for (const child of src.shadowRoot.childNodes) {
        box.appendChild(child.nodeType === 1 ? flat(child) : inert.importNode(child, false));
      }
      dst.appendChild(box);
    }
    for (const child of src.childNodes) {
      dst.appendChild(child.nodeType === 1 ? flat(child) : inert.importNode(child, false));
    }
    return dst;
  };
  return '<!DOCTYPE html>' + flat(document.documentElement).outerHTML;
}"""
RENDER_GRACE = 5.0      # seconds past the render timeout before the watchdog kills the driver and Chromium


class BrowserRenderer:
    def __init__(self, fetcher, *, executable=None, timeout=20.0):
        self.fetcher = fetcher
        self.executable = executable
        self.timeout = float(timeout)
        self.last_events: list[dict] = []

    def _executable(self) -> str | None:
        for candidate in (self.executable, os.environ.get("NSF_CHROMIUM"), "/usr/bin/chromium",
                          shutil.which("chromium"), shutil.which("chromium-browser")):
            if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    def available(self) -> tuple[bool, str]:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, "playwright is not installed (pip install 'neuroscifi[web-render]')"
        explicit = self.executable or os.environ.get("NSF_CHROMIUM")
        exe = self._executable()
        if explicit and exe != explicit:
            return False, f"chromium executable not found or not executable: {explicit}"
        if exe:
            return True, f"chromium at {exe}"
        bundled = _bundled_chromium()
        if bundled:
            return True, f"playwright chromium at {bundled}"
        return False, "no chromium found (install chromium or set NSF_CHROMIUM)"

    def render(self, url, *, budget, usage, main: FetchResult | None = None) -> FetchResult:
        """`main`: the FetchResult of `url` the caller already fetched (purpose='page'); it is not fetched or
        charged again. Without it the page is fetched here."""
        ok, reason = self.available()
        if not ok:
            raise FetchError("disabled", _redact(url), reason)
        if budget is None or usage is None:
            raise FetchError("disabled", _redact(url), "rendering needs a task budget and usage")
        check_url(url, self.fetcher.policy)
        _charge(budget, usage, browser_sessions=1)
        if main is None:
            main = self.fetcher.fetch(url, purpose="page", budget=budget, usage=usage)
        if main.content_type not in ("text/html", "application/xhtml+xml"):
            return main
        box: dict = {}
        worker = threading.Thread(target=self._render_thread, args=(main, budget, usage, box), daemon=True,
                                  name="webcortex-render")
        worker.start()
        worker.join(self.timeout + RENDER_GRACE)
        if worker.is_alive():
            _kill_tree(box.get("driver_pid"))   # Chromium dies with its driver; the thread's next call then fails
            worker.join(RENDER_GRACE)
            raise FetchError("timeout", _redact(url), f"renderer did not finish within {self.timeout:g}s; killed")
        if "error" in box:
            raise box["error"]
        return box["result"]

    # ---- inside the render thread (Playwright's sync API must not share a thread with an asyncio loop) -------
    def _render_thread(self, main, budget, usage, box):
        try:
            box["result"] = self._render(main, budget, usage, box)
        except BaseException as error:          # handed back to the calling thread
            box["error"] = error

    def _render(self, main: FetchResult, budget, usage, box=None) -> FetchResult:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        started = time.monotonic()
        deadline = started + self.timeout
        events: list[dict] = []
        self.last_events = events
        state = {"exhausted": None, "main_served": False}
        policy = self.fetcher.policy

        def handle(route):
            request = route.request
            target = request.url
            record = {"url": _redact(target), "type": request.resource_type}
            events.append(record)
            if target == main.final_url and request.is_navigation_request() and not state["main_served"]:
                state["main_served"] = True
                record["outcome"] = "served-main"
                route.fulfill(status=main.status, headers=_served_headers(main), body=main.body)
                return
            scheme = urlsplit(target).scheme.lower()
            if (request.method not in ("GET", "HEAD") or scheme not in ("http", "https")
                    or request.resource_type in BLOCKED_RESOURCES or state["exhausted"]
                    or time.monotonic() > deadline):
                record["outcome"] = "aborted"
                route.abort("blockedbyclient")
                return
            try:
                result = self.fetcher.fetch(target, method=request.method, purpose="subresource", budget=budget,
                                            usage=usage)
            except FetchError as error:
                record.update(outcome="refused", code=error.code)
                route.abort("blockedbyclient" if error.code in ("blocked_policy", "blocked_robots") else "failed")
                return
            except BudgetExceeded as error:
                state["exhausted"] = error
                record.update(outcome="refused", code="budget_exhausted")
                route.abort("blockedbyclient")
                return
            record["outcome"] = "served"
            route.fulfill(status=result.status, headers=_served_headers(result), body=result.body)

        exe = self._executable()
        manager = sync_playwright()
        with manager as playwright:
            if box is not None:
                box["driver_pid"] = _driver_pid(manager)
            browser = playwright.chromium.launch(executable_path=exe, headless=True, chromium_sandbox=True,
                                                 args=CHROMIUM_ARGS, proxy=DEAD_PROXY,
                                                 timeout=max(1000, (deadline - time.monotonic()) * 1000))
            try:
                context = browser.new_context(java_script_enabled=True, accept_downloads=False,
                                              service_workers="block", permissions=[], bypass_csp=False,
                                              user_agent=policy.user_agent, ignore_https_errors=False)
                try:
                    context.route("**/*", handle)
                    # Never connected to a server (closing inside a sync handler deadlocks Playwright 1.64);
                    # the init script also makes the constructors throw so pages see the refusal.
                    context.route_web_socket("**/*", lambda ws: None)
                    context.add_init_script(BLOCK_SCRIPT)
                    context.on("page", lambda page: page.on("dialog", lambda dialog: dialog.dismiss()))
                    page = context.new_page()
                    context.on("page", lambda popup: popup.close() if popup is not page else None)
                    page.on("download", lambda download: download.cancel())

                    def remaining_ms():
                        return max(1.0, (deadline - time.monotonic()) * 1000)

                    try:
                        page.goto(main.final_url, wait_until="load", timeout=remaining_ms())
                        try:
                            page.wait_for_load_state("networkidle", timeout=min(remaining_ms(), 5000))
                        except PlaywrightTimeout:
                            pass                     # long-polling pages: take the DOM as it is now
                        try:
                            html = page.evaluate(FLATTEN_SCRIPT, MAX_SHADOW_ROOTS)
                        except PlaywrightTimeout:
                            raise
                        except PlaywrightError:
                            html = None              # the page broke the serialiser: its light DOM is used
                        if not isinstance(html, str) or not html:
                            html = page.content()
                        final = page.url
                    except PlaywrightTimeout:
                        raise FetchError("timeout", _redact(main.final_url),
                                         f"render exceeded {self.timeout:g}s") from None
                    except PlaywrightError as error:
                        raise FetchError("decode_error", _redact(main.final_url),
                                         f"render failed ({str(error).splitlines()[0][:120]})") from None
                finally:
                    context.close()
            finally:
                browser.close()
        if state["exhausted"] is not None:
            events.append({"url": None, "outcome": "budget_exhausted"})
        body = html.encode("utf-8")
        truncated = len(body) > policy.max_bytes
        if urlsplit(final).scheme not in ("http", "https"):
            return main          # chrome-error:// (blocked navigation) or about:blank: not the page's content
        return FetchResult(url=main.url, final_url=final, status=main.status,
                           headers={"content-type": "text/html; charset=utf-8"}, content_type="text/html",
                           body=body[:policy.max_bytes], retrieved_at=self.fetcher._clock(),
                           retrieval_mode="rendered", redirects=list(main.redirects), truncated=truncated,
                           elapsed=time.monotonic() - started + main.elapsed, remote_ip=main.remote_ip)


def _served_headers(result: FetchResult) -> dict:
    headers = {name: result.headers[name] for name in SERVED_HEADERS if result.headers.get(name)}
    headers.setdefault("content-type", result.content_type or "application/octet-stream")
    return headers


def _driver_pid(manager) -> int | None:
    """Pid of the Playwright driver process behind a sync_playwright() manager (Chromium runs below it)."""
    try:
        return int(manager._connection._transport._proc.pid)   # playwright 1.64 internals; None if they move
    except (AttributeError, TypeError, ValueError):
        return None


def _descendants(pid: int) -> list[int]:
    """Every process below pid (Linux /proc), parents before children; [] where /proc is unavailable."""
    children: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            stat = Path(f"/proc/{entry}/stat").read_text()
            ppid = int(stat[stat.rfind(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    out, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), ()):
            out.append(child)
            stack.append(child)
    return out


def _kill_tree(pid: int | None) -> None:
    """SIGKILL a driver and everything below it. The tree is listed first: Chromium's helpers would be re-parented
    away (and become unfindable) once their parent dies."""
    if not pid:
        return
    for target in [pid] + _descendants(pid):
        try:
            os.kill(target, signal.SIGKILL)
        except OSError:
            pass


def _bundled_chromium() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            path = playwright.chromium.executable_path
    except Exception:              # no driver / no browsers downloaded: simply unavailable
        return None
    return path if path and Path(path).is_file() else None
