"""Wave 3 lane P: a panel drawn again keeps the keyboard where it was.

The console's overview is drawn on every load of the view, and a second load could land while the owner was on one
of its controls (CI, Wave 2: test_keyboard_reaches_every_enabled_button[overview]); Pocket's research list and open
question are polled every 3 s and listed every 10 s, and its kept episodes are painted every minute, each of them
rebuilt every time. For each panel: a control is focused, the panel is refreshed with the same data (the same node
keeps focus) and with changed data (the equivalent control has focus; an open "Technical detail" stays open)."""
from contextlib import closing
import json
import re
import time

from playwright.sync_api import expect

from conftest import pair
from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator
from test_console import console, go, seeded  # noqa: F401  (fixtures)
from test_pocket_research_e2e import QUESTION, ask, spark  # noqa: F401  (fixture)

FOCUS = """() => { const e = document.activeElement, d = e.closest('details');
    return {tag: e.tagName, id: e.id, label: e.getAttribute('aria-label') ?? e.textContent.trim(), mark: e.dataset.mark ?? null,
            panel: e.closest('.slot, ul')?.id ?? null, open: d ? d.open : null}; }"""


def mark(locator):
    locator.focus()
    locator.evaluate("e => { document.querySelectorAll('[data-mark]').forEach(x => delete x.dataset.mark); e.dataset.mark = '1'; }")
    expect(locator).to_be_focused()


def test_the_console_overview_keeps_focus_and_open_details_when_its_panels_are_drawn_again(seeded, new_page):  # noqa: F811
    page = new_page(1280, 900)
    served, changed = {}, {"self": 0, "resources": 0}

    def serve(route):
        name = "self" if "/api/self" in route.request.url else "resources"
        served.setdefault(name, route.fetch().json())
        body = json.loads(json.dumps(served[name]))
        if changed[name] and name == "self":
            body["self_short"] = [*body.get("self_short", []), f"A changed line ({changed[name]})."]
        elif changed[name]:
            body["reasons"] = [f"a changed reason ({changed[name]})"]
        route.fulfill(json=body)
    page.route(re.compile(r"/api/(self|resources)\b"), serve)
    page.goto(seeded.url)
    go(page, "overview")

    def refresh():
        page.evaluate("document.getElementById('refreshOverview').click()")   # programmatic: focus stays put
        expect(page.locator("html[data-inflight='0']")).to_have_count(1)
        expect(page.locator("#overview [aria-busy='true']")).to_have_count(0)

    for name, selector in (("self", "#selfdesc details > summary"), ("resources", "#resources details > summary"),
                           ("resources", "#resources > button")):
        control = page.locator(selector)
        control.evaluate("e => { const d = e.closest('details'); if (d) d.open = true; }")
        for change in (False, True):
            changed[name] += change
            mark(control)
            before = page.evaluate(FOCUS)
            refresh()
            after = page.evaluate(FOCUS)
            assert {k: after[k] for k in ("tag", "label", "panel", "open")} == \
                   {k: before[k] for k in ("tag", "label", "panel", "open")}, (selector, change, before, after)
            # the same data keeps the same node; changed data draws the panel again
            assert after["mark"] == (None if change else "1"), (selector, change, after)
    assert served.keys() == {"self", "resources"}
    assert not page.problems, page.problems


def test_pocket_keeps_focus_on_its_research_list_and_open_question_while_they_are_polled(spark, new_page):  # noqa: F811
    page = new_page(390, 844)
    page.clock.install()
    page.goto(spark.url)
    pair(page)
    ask(page)          # no research worker runs: the question stays queued, polled every 3 s, listed every 10 s
    listed = lambda: page.expect_response(lambda r: r.url.endswith("/research_list"))  # noqa: E731
    open_button = page.get_by_role("button", name="Open: " + QUESTION)
    for control in (open_button, page.locator("#researchbody").get_by_role("button", name="Cancel research")):
        mark(control)
        for _ in range(2):           # a list refresh, then the polls after it (which used to redraw the open view)
            with listed():
                page.clock.run_for(10_500)
            page.clock.run_for(3_100)
        expect(page.locator("[data-mark='1']")).to_be_focused()
    # Changed: the question is cancelled on the Spark; the list and the open view are drawn again.
    with closing(store.connect(spark.instance)) as db:
        orchestrator.cancel(db, orchestrator.listing(db)[0]["id"], time.time())
    mark(open_button)
    with listed():
        page.clock.run_for(10_500)
    expect(page.locator("#researchlist")).to_contain_text("Cancelled")
    expect(page.locator("[data-mark='1']")).to_have_count(0)
    expect(open_button).to_be_focused()
    assert not page.problems, page.problems


def test_pocket_keeps_focus_on_a_kept_episode_while_the_field_view_is_painted(spark, new_page):  # noqa: F811
    page = new_page(390, 844)
    page.clock.install()
    page.goto(spark.url)
    page.locator("#goal").select_option("work")
    page.locator("#predict").click()
    page.get_by_role("button", name="Continued").click()
    expect(page.locator("#historyLocal")).to_contain_text("work → continued")
    forget = page.get_by_role("button", name="Forget here: work → continued")
    mark(forget)
    page.clock.run_for(61_000)       # the minute's paint, same episodes
    expect(page.locator("#historyLocal [data-mark='1']")).to_be_focused()
    # Changed: the episode (kept 24 hours) expires; the list is drawn again and keeps the keyboard in itself.
    page.clock.fast_forward(25 * 3600 * 1000)
    page.clock.run_for(61_000)
    expect(page.locator("#historyLocal li")).to_have_count(0)
    expect(page.locator("#historyLocal")).to_be_focused()
    assert not page.problems, page.problems
