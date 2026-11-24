"""Browser fixtures: the bridge runs in-process on a free port against a temporary database."""
import os
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from neuroscifi.pocket.server import Handler, Store, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "test-results"
AXE = ROOT / "node_modules" / "axe-core" / "axe.min.js"
FAKE_MEDIA = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
              "--autoplay-policy=no-user-gesture-required"]
expect.set_options(timeout=10_000)


def paced(per_page: float, pages: int) -> float:
    """The delay to give each of `pages` fixture pages (on distinct sites) so that a research task's reading stage
    lasts what the tests that watch it go by were written for: `pages` pages at `per_page` seconds each, one after
    the other. The individuals under test keep the default `read_ahead`, so pages are fetched several at once, in
    rounds of at most that many, and a round lasts one delay; with the same delay per page the stage would be that
    many times shorter, and a phone polling for progress, or a test that needs the stage to last a second, would
    miss it. Scaling the delay keeps the stage as long as it was while the browser suite exercises the default
    path."""
    import math

    from neuroscifi.webcortex.config import WebConfig
    at_once = max(1, min(WebConfig().read_ahead, pages))
    return per_page * pages / math.ceil(pages / at_once)


class Bridge:
    def __init__(self, folder):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = Store(Path(folder) / "e2e.sqlite")
        self.server.pair_lock = threading.Lock()
        self.invite("test-only")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"

    def invite(self, code):
        self.server.invitation = code
        self.server.invitation_deadline = time.monotonic() + 120

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        # The product's server (pocket.server.PocketServer) closes the audio beacon and the voice hub that a voice
        # request starts on it (voice.http.hub_for) in its server_close(). This fixture serves the same handler
        # from a plain ThreadingHTTPServer, whose server_close() knows nothing of them: close them here, as the
        # product does, or their threads outlive the test (the session-end thread check, tests/thread_check.py).
        beacon = getattr(self.server, "audio_beacon", None)
        if beacon is not None:
            beacon.close()
        hub = getattr(self.server, "voice_hub", None)
        if hub is not None:
            hub.close()


@pytest.fixture
def bridge(tmp_path):
    b = Bridge(tmp_path)
    yield b
    b.close()


def launch_chromium(browser_type, args):
    """NSF_CHROMIUM (e.g. /usr/bin/chromium) if set, else Playwright's full bundled Chromium in new headless mode.

    Not the default chromium-headless-shell: its browser process registers no binder for
    media.mojom.OnDeviceSpeechRecognition, so the page's own SpeechRecognition.available() feature check gets the
    renderer killed ("bad Mojo message ... reason 123"). Full Chromium of the same build answers "unavailable".
    """
    override = os.environ.get("NSF_CHROMIUM") or None
    return browser_type.launch(executable_path=override, channel=None if override else "chromium", args=args)


@pytest.fixture(scope="session")
def browser():
    override = os.environ.get("NSF_CHROMIUM") or None
    with sync_playwright() as pw:
        b = launch_chromium(pw.chromium, FAKE_MEDIA)
        print(f"\nchromium: {'NSF_CHROMIUM=' + override if override else pw.chromium.executable_path} {b.version}")
        yield b
        b.close()


@pytest.fixture
def new_page(browser):
    """Factory: new_page(width, height, **context_options) -> page with dialogs accepted and errors recorded."""
    contexts = []

    def make(width=390, height=844, **options):
        ctx = browser.new_context(viewport={"width": width, "height": height},
                                  permissions=["microphone", "camera"], **options)
        contexts.append(ctx)
        page = ctx.new_page()
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(str(e)))
        page.on("console", lambda m: page.problems.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept())
        return page

    yield make
    for ctx in contexts:
        ctx.close()


def pair(page, code="test-only"):
    page.locator("#code").fill(code)
    page.locator("#pairform button").click()
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")
    expect(page.locator("#message")).to_have_text("Paired.")
