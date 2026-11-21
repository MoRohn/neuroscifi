"""Pocket research 0.7.0 in a real Chromium: a cancelled question is continued from the phone, the Spark's worker runs
it, the evidence review shows ADR-035 states, and a spent daily budget is explained without queuing anything."""
from contextlib import closing
import time

from playwright.sync_api import expect

from conftest import pair
from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, orchestrator
import pytest

from test_pocket_research_e2e import QUESTION, Spark, ask


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


def test_continue_review_evidence_and_budget_refusal(spark, new_page):
    page = new_page()
    page.goto(spark.url)
    pair(page)
    ask(page)
    page.get_by_role("button", name="Cancel research").click()
    expect(page.locator(".rstage")).to_have_text("Cancelled")
    page.get_by_role("button", name="Continue research").click()
    expect(page.locator("#researchmsg")).to_have_text("Continued on your Spark.")
    expect(page.locator(".rstage")).to_contain_text("Queued")
    spark.work()
    page.locator("#refresh").click()
    page.get_by_role("button", name="Open: " + QUESTION).first.click()
    expect(page.locator(".rstage")).to_have_text("Completed", timeout=30_000)
    page.get_by_role("button", name="Review evidence").click()
    expect(page.locator(".revidence h4")).to_have_text("Evidence review")
    expect(page.locator(".revidence li").first).to_contain_text("Supported")
    expect(page.locator(".revidence")).to_contain_text("basis: ")
    with closing(store.connect(spark.instance)) as db:
        tasks = orchestrator.listing(db)
        assert [t["status"] for t in tasks] == ["completed", "cancelled"] and tasks[0]["parent_id"] == tasks[1]["id"]
        autonomy.set_policy(db, {"user_daily": {"searches": 0}}, time.time())
    page.locator("#objective").fill("Zephyr compiler supported boards")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("daily research budget is used up")
    with closing(store.connect(spark.instance)) as db:
        assert len(orchestrator.listing(db)) == 2
    assert [p for p in page.problems if "status of 429" not in p] == []   # the refusal itself is the only 429
