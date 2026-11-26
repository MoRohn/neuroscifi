"""ADR-065 (Wave 4 lane D): Pocket's report shows the caution in plain words where the answer rests on pages that look
like copies; never the flag about the individual's own earlier answers (family scope). Real Chromium; axe clean."""
import re

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, pair
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from test_pocket_research_e2e import PAGES, QUESTION, Spark, ask

COPY_HOST, COPY_PATH = "copy-four.example", "/zephyr-copy"


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    title, date, text = PAGES[("news-one.example", "/zephyr")]
    s.web.add(COPY_HOST, COPY_PATH, f"<html><head><title>{title}</title><meta property='article:published_time' "
                                    f"content='{date}'></head><body><article><h1>{title}</h1><p>{text}</p>"
                                    "<p>The release adds faster builds.</p></article></body></html>")
    s.corpus = CorpusProvider([{"url": f"http://{h}{p}", "title": t, "snippet": x[:160]}
                               for (h, p), (t, _, x) in {**PAGES, (COPY_HOST, COPY_PATH): (title, date, text)}.items()])
    yield s
    s.close()


@pytest.mark.parametrize("size", [(390, 844), (1024, 768)], ids=["phone", "tablet"])
def test_pocket_cautions_where_pages_look_like_copies(spark, new_page, size):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected
    replies = []
    page.on("response", lambda r: replies.append(r) if r.url.endswith("/research_get") else None)
    page.goto(spark.url)
    pair(page)
    ask(page)
    spark.work()
    page.locator("#refresh").click()
    expect(page.locator("#researchlist")).to_contain_text("Completed")
    page.get_by_role("button", name="Open: " + QUESTION).click()
    caution = page.locator("#researchview .rcaution").first
    expect(caution).to_be_visible()
    expect(caution).to_contain_text(re.compile(r"^Caution: .*look like copies"))
    expect(page.locator("#researchview")).not_to_contain_text("said earlier")
    bodies = [r.text() for r in replies if r.ok]
    assert bodies and all("own_answer" not in b for b in bodies)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    SHOTS.mkdir(exist_ok=True)
    page.locator("#research").screenshot(path=str(SHOTS / f"pocket-caution-{'tablet' if size[0] > 800 else 'phone'}.png"))
    page.add_script_tag(path=str(AXE))
    found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")
    assert [v for v in found if v["impact"] in ("serious", "critical")] == []
