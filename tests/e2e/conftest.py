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


@pytest.fixture
def bridge(tmp_path):
    b = Bridge(tmp_path)
    yield b
    b.close()


@pytest.fixture(scope="session")
def browser():
    override = os.environ.get("NSF_CHROMIUM") or None  # e.g. /usr/bin/chromium; default is Playwright's bundled build
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=override, args=FAKE_MEDIA)
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
