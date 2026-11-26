"""ADR-065 (Wave 4 lane D): the owner console shows a plain caution where an answer rests on pages that look like
copies of each other, in the report and in the knowledge view; axe finds nothing serious, and the page has no
horizontal scroll on a phone. Real Chromium under the real CSP; the embedded worker researches offline."""
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store
from neuroscifi.webcortex import state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from test_console import scan
from test_console_research import enable_web, finish, go, html, ask

TOKEN = "e2e-caution-token-" + "c" * 30
NOW = time.time()
QUESTION = "Was the Quillon editor version 7.1.0 released?"


def day(days_ago):
    return datetime.fromtimestamp(NOW - days_ago * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


RELEASED = datetime.fromtimestamp(NOW - 3 * 86400, timezone.utc).strftime("%d %B %Y").lstrip("0")
COPY = f"The Quillon editor version 7.1.0 was released on {RELEASED} by the Quillon project team."
PAGES = {   # two copies of one story on two sites, and one report in other words
    ("wire-copy-one.example", "/quillon"): ("Quillon 7.1 released", day(2), [COPY, "It adds split views."]),
    ("wire-copy-two.example", "/quillon-71"): ("Quillon 7.1 is out", day(2), [COPY, "Plugins load faster."]),
    ("review-three.example", "/quillon-review"): ("Hands on with Quillon", day(1), [
        f"We tried the Quillon editor version 7.1.0, which its team released on {RELEASED}, for a week.",
        "Large files now open in under a second."]),
}


class Research:
    def __init__(self, folder):
        self.web = FixtureWeb().start()
        for (host, path), page in PAGES.items():
            self.web.add(host, path, html(*page))
        results = [{"url": f"http://{host}{path}", "title": title, "snippet": " ".join(paras)[:160]}
                   for (host, path), (title, _, paras) in PAGES.items()]
        self.path = Path(folder) / "owner.sqlite"
        db = store.create(self.path, NOW)
        state.set_config(db, {"min_domain_interval": 0.0}, NOW)
        db.close()
        self.server = ConsoleServer(self.path, 0, token=TOKEN, web_worker=True,
                                    worker_options={"fixture": self.web, "providers": [CorpusProvider(results)]})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = self.server.url

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.web.stop()


@pytest.fixture
def research(tmp_path):
    r = Research(tmp_path)
    yield r
    r.close()


@pytest.mark.parametrize("size", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
def test_the_console_cautions_where_pages_look_like_copies(research, new_page, size):
    SHOTS.mkdir(exist_ok=True)
    page = new_page(*size, bypass_csp=size[0] > 400)      # axe is injected on the desktop run only
    page.goto(research.url)
    go(page, "research", "Research")
    enable_web(page)
    ask(page, QUESTION, "fact_verification")
    finish(page)
    caution = page.locator("#report [data-caution]").first
    expect(caution).to_be_visible()
    expect(caution).to_contain_text(re.compile("look like copies"))
    expect(caution).to_contain_text("A reworded copy cannot be told from an independent report")
    expect(caution.locator(".badge")).to_have_text("Caution")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    found = {}
    if size[0] > 400:
        page.add_script_tag(path=str(AXE))
        scan(page, found, "research-caution")
    page.screenshot(path=str(SHOTS / f"console-caution-{'desktop' if size[0] > 400 else 'phone'}.png"),
                    full_page=True)
    go(page, "knowledge", "Knowledge")
    card = page.locator("#knowledgeList article[data-knowledge]", has_text="7.1.0").first
    expect(card.locator("[data-caution]")).to_contain_text("look like copies")
    if size[0] > 400:
        scan(page, found, "knowledge-caution")
    assert not found, found
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not page.problems, page.problems
