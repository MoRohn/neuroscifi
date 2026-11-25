"""Pocket's outbox in a real Chromium (ENGINEERING_PLAN.md §6 item 12): a question written while the Spark is out of
reach waits on the device, survives a reload inside the encrypted vault, is sent once when the Spark answers, and a
retry after a lost reply never queues it twice. Offline is Playwright's network emulation; a real handset moving
between networks is not tested here."""
from contextlib import closing
import json
import re

import pytest
from playwright.sync_api import expect

from conftest import AXE, pair
from test_pocket_research_e2e import PASS, QUESTION, Spark, posts, unlock
from neuroscifi.pocket import server as bridge
from neuroscifi.webcortex import orchestrator

UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


def tasks(spark):
    from neuroscifi.instance import store
    with closing(store.connect(spark.instance)) as db:
        return orchestrator.listing(db)


def submissions(spark):
    with closing(bridge.connect(spark.bridge.server.store.path)) as db:
        return db.execute("SELECT count(*) FROM submissions").fetchone()[0]


def offline(page, spark, on):
    """The device loses (on) or regains its network. Playwright's offline emulation does not stop every request a
    service worker passes on to the network, so the Spark also refuses every connection while the device is
    offline: nothing reaches it, as on a real network."""
    spark.bridge.server.verify_request = (lambda request, address: False) if on else (lambda request, address: True)
    page.context.set_offline(on)


def offline_noise(problems):
    """Console errors other than the browser's own reports of requests made while offline or refused (a 400)."""
    return [p for p in problems if not p.startswith("Failed to load resource")]


def write_offline(page, spark, text=QUESTION):
    offline(page, spark, True)
    page.locator("#objective").fill(text)
    expect(page.locator("#researchsubmithint")).to_have_text(
        "This device is offline. Your question will wait here and be sent once your Spark answers.")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_have_text(
        "Your Spark is out of reach, so the question waits on this device. Pocket sends it once your Spark answers.")
    item = page.locator("#outboxlist li").filter(has_text=text)
    expect(item).to_have_count(1)
    expect(item).to_contain_text("Waiting to send")
    expect(page.locator("#objective")).to_have_value("")


def test_a_question_written_offline_waits_and_is_sent_once_when_back(spark, new_page):
    page = new_page()
    sent = posts(page)
    page.goto(spark.url)
    pair(page)
    expect(page.locator("#researchoutbox")).to_be_hidden()
    write_offline(page, spark)
    expect(page.locator("#outboxhelp")).to_contain_text("Kept in this tab only")
    expect(page.locator("#outboxsend")).to_be_enabled()
    assert tasks(spark) == []
    assert page.evaluate("Object.keys(localStorage)") == [], "a waiting question outside the vault is never stored"
    # a reload would lose a question kept in this tab only, so leaving the page asks first
    assert page.evaluate("() => { const e = new Event('beforeunload', {cancelable: true}); "
                         "window.dispatchEvent(e); return e.defaultPrevented; }") is True
    offline(page, spark, False)                  # back online: Pocket checks the Spark, then sends what waits
    expect(page.locator("#researchmsg")).to_have_text("Sent a waiting question to your Spark.")
    expect(page.locator("#researchoutbox")).to_be_hidden()
    expect(page.locator("#researchlist")).to_contain_text(QUESTION)
    assert [t["objective"] for t in tasks(spark)] == [QUESTION]
    bodies = [json.loads(raw) for route, raw in sent if route == "research_submit"]
    # the try that failed offline and the send: the same question with the same key, and nothing else
    assert len(bodies) == 2 and bodies[0] == bodies[1] and set(bodies[0]) == {"objective", "request"}
    assert UUID4.fullmatch(bodies[0]["request"])
    assert page.evaluate("() => { const e = new Event('beforeunload', {cancelable: true}); "
                         "window.dispatchEvent(e); return e.defaultPrevented; }") is False
    assert not offline_noise(page.problems), page.problems


def test_a_lost_reply_is_asked_again_and_queues_one_task(spark, new_page):
    """The Spark queued the question but its reply never reached the phone: the phone keeps the question, asks
    again with the same key, and the Spark answers with the task it already queued."""
    page = new_page()
    sent = posts(page)
    page.goto(spark.url)
    pair(page)

    def lose_reply(route):
        route.fetch()          # the request reaches the Spark and is queued there ...
        route.abort()          # ... and the reply is lost on the way back
    page.route("**/api/research_submit", lose_reply)
    page.locator("#objective").fill(QUESTION)
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("the question waits on this device")
    expect(page.locator("#outboxlist li")).to_have_count(1)
    assert len(tasks(spark)) == 1, "the first try reached the Spark"
    page.unroute("**/api/research_submit")
    page.locator("#outboxsend").click()
    expect(page.locator("#researchmsg")).to_have_text("Sent a waiting question to your Spark.")
    expect(page.locator("#researchoutbox")).to_be_hidden()
    assert len(tasks(spark)) == 1, "the retry was replayed, not queued again"
    assert submissions(spark) == 1, "and not counted again against the hourly limit"
    keys = [json.loads(raw)["request"] for route, raw in sent if route == "research_submit"]
    assert len(keys) == 2 and keys[0] == keys[1]
    expect(page.locator("#researchlist li")).to_have_count(1)
    assert not offline_noise(page.problems), page.problems


def test_a_waiting_question_survives_a_reload_in_the_vault_and_is_sent_once(spark, new_page):
    page = new_page()
    page.goto(spark.url)
    expect(page.locator("#offlinestate")).to_have_text("Offline shell ready")
    pair(page)
    unlock(page)
    page.locator("#rememberpair").click()
    expect(page.locator("#fieldmsg")).to_have_text("Pairing protected by this vault for 24 hours.")
    write_offline(page, spark)
    expect(page.locator("#outboxhelp")).to_contain_text("Kept in your encrypted vault on this device for up to 7 days")
    vault = page.evaluate("localStorage.getItem('neuroscifi.field.v1')")
    assert "Zephyr" not in vault and "request" not in vault, "the waiting question is encrypted"
    page.reload()                                    # still offline: the shell comes from the service worker
    expect(page.locator("#research")).to_be_hidden()
    page.locator("summary", has_text="Keep episodes on this device").click()
    page.locator("#pass").fill(PASS)
    page.locator("#unlock").click()
    expect(page.locator("#outboxlist li")).to_have_count(1)
    expect(page.locator("#outboxlist li")).to_contain_text(QUESTION)
    expect(page.locator("#research")).to_be_visible()
    assert tasks(spark) == []
    offline(page, spark, False)
    page.locator("#refresh").click()                 # the vault's pairing is checked with the Spark
    expect(page.locator("#researchmsg")).to_have_text("Sent a waiting question to your Spark.", timeout=15_000)
    expect(page.locator("#researchoutbox")).to_be_hidden()
    assert [t["objective"] for t in tasks(spark)] == [QUESTION]
    page.reload()                                    # sent and removed: nothing waits after the next unlock
    unlock(page)
    expect(page.locator("#researchoutbox")).to_be_hidden()
    assert len(tasks(spark)) == 1


def test_a_question_the_spark_refuses_stays_for_the_owner_to_edit(spark, new_page):
    """A phone number is screened only by the Spark (its check needs context): a queued question with one is not
    dropped; it stays, says why, and Edit puts it back in the field."""
    page = new_page()
    page.goto(spark.url)
    pair(page)
    question = "Who answers the Zephyr support phone +44 7700 900123?"
    write_offline(page, spark, question)
    offline(page, spark, False)
    expect(page.locator("#outboxlist li")).to_contain_text("Not sent: your Spark found personal details in it.")
    expect(page.locator("#outboxsend")).to_be_disabled()
    assert tasks(spark) == []
    page.get_by_role("button", name="Edit waiting question: " + question).click()
    expect(page.locator("#objective")).to_have_value(question)
    expect(page.locator("#researchoutbox")).to_be_hidden()
    expect(page.locator("#researchmsg")).to_have_text("The question is back in the field. Change it, then submit it again.")
    assert not offline_noise(page.problems), page.problems


@pytest.mark.parametrize("size", [(1024, 768), (390, 844)], ids=["tablet", "phone"])
def test_waiting_questions_layout_and_accessibility(spark, new_page, size):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; the app's CSP holds in the other tests
    page.goto(spark.url)
    pair(page)
    write_offline(page, spark)
    write_offline(page, spark, "Compare the Zephyr and Aurora compilers")
    expect(page.locator("#outboxlist li")).to_have_count(2)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for box in (b.bounding_box() for b in page.locator("#researchoutbox button").all() if b.is_visible()):
        assert box["height"] >= 43.5, box
    page.add_script_tag(path=str(AXE))
    found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")
    assert [v for v in found if v["impact"] in ("serious", "critical")] == []
    offline(page, spark, False)
