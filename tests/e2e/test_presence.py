"""Presence Cortex in a real Chromium under the real CSP (docs/presence/FEATURE_ACCEPTANCE.md).

Real app state: research tasks run on the console's embedded worker against an OFFLINE public web (FixtureWeb hosts +
CorpusProvider, the pattern of test_console_research.py). Two test-only sleeps (evaluating and synthesizing each take
1.5 s longer) keep fast stages visible to a human-speed observer; the pipeline itself is unchanged.
Voice scenarios (A, B, F) are EVENT-LEVEL: the page receives Lane V's `nsf:voice` events dispatched by the test, not
real microphone capture.
"""
from contextlib import closing
import json
import re
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, pair
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = ROOT / "docs" / "presence" / "snapshots"
TOKEN = "e2e-presence-token-" + "q" * 30
QUESTION = "Was the Zephyr compiler version 4.2.0 released?"
PAGES = {
    ("news-one.example", "/zephyr"): ("Zephyr compiler 4.2 released", [
        "The Zephyr compiler version 4.2.0 was released on 3 November 2026 by the Zephyr project team.",
        "The release adds faster incremental builds for large embedded projects."]),
    ("tech-two.example", "/news/zephyr-42"): ("Zephyr 4.2 is out", [
        "The Zephyr compiler version 4.2.0 was released on 3 November 2026 after a long beta.",
        "Developers report that the compiler now supports more targets."]),
    ("blog-three.example", "/zephyr-review"): ("Hands on with the Zephyr compiler", [
        "We tested the Zephyr compiler version 4.2.0 released on 3 November 2026 on three boards.",
        "Build times dropped by 30 percent in our tests."]),
}
OKAPI = {("zoo-one.example", "/okapi"): ("Okapi in the wild", [
    "The okapi is a giraffid mammal native to the Ituri rainforest of the Congo basin.",
    "Okapi stripes help calves follow their mothers through dense forest."])}


def html(title, paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><head><title>{title}</title><meta property='article:published_time' content='2026-11-04T10:00:00Z'>"
            f"</head><body><article><h1>{title}</h1>{body}</article></body></html>")


class Spark:
    def __init__(self, folder, delay=1.5):
        self.web = FixtureWeb().start()
        for (host, path), page in {**PAGES, **OKAPI}.items():
            self.web.add(host, path, html(*page), delay=delay)
        results = [{"url": f"http://{h}{p}", "title": t, "snippet": " ".join(x)[:160]} for (h, p), (t, x) in
                   {**PAGES, **OKAPI}.items()]
        self.path = Path(folder) / "owner.sqlite"
        with closing(store.create(self.path, time.time())) as db:
            state.set_config(db, {"enabled": True, "autonomous": True, "min_domain_interval": 0.0}, time.time())
        self.server = ConsoleServer(self.path, 0, token=TOKEN, web_worker=True,
                                    worker_options={"fixture": self.web, "providers": [CorpusProvider(results)]})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = self.server.url
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def submit(self, objective=QUESTION, origin="console"):
        with closing(store.connect(self.path)) as db:
            return orchestrator.submit(db, objective, time.time(), mode="fact_verification", origin=origin)

    def task(self, task_id):
        with closing(store.connect(self.path)) as db:
            return orchestrator.get(db, task_id)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.web.stop()


@pytest.fixture
def spark(tmp_path, monkeypatch):
    # test-only pacing of two fast stages (see module docstring); everything else is the real pipeline
    for name in ("evaluate", "synthesize"):
        original = getattr(orchestrator._Run, name)

        def slowed(self, *a, _original=original, **kw):
            time.sleep(1.5)
            return _original(self, *a, **kw)
        monkeypatch.setattr(orchestrator._Run, name, slowed)
    s = Spark(tmp_path)
    yield s
    s.close()


@pytest.fixture
def idle_spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


RECORD = """() => {
  window.__seq = [];
  const root = document.querySelector('.nsf-presence');
  const push = () => { const last = window.__seq[window.__seq.length - 1];
    const now = [root.dataset.activity, root.dataset.concept, root.dataset.tier];
    if (!last || last[0] !== now[0] || last[1] !== now[1]) window.__seq.push([...now, Date.now()]); };
  push();
  new MutationObserver(push).observe(root, { attributes: true, attributeFilter: ['data-activity', 'data-concept'] });
}"""


def open_console(page, spark):
    page.goto(spark.url)
    expect(page.locator(".nsf-presence")).to_be_visible()
    expect(page.locator(".nsf-presence")).to_have_attribute("data-activity", re.compile("idle|resting"))


def voice(page, **detail):
    page.evaluate("d => dispatchEvent(new CustomEvent('nsf:voice', {detail: d}))", detail)


def stats(page):
    return page.evaluate("window.__nsfPresence.stats()")


def body_d(page):
    return page.evaluate("document.querySelector('.nsf-presence path[data-slot=body]').getAttribute('d')")


def stroke(page, slot="body"):
    return page.evaluate(f"getComputedStyle(document.querySelector('.nsf-presence path[data-slot={slot}]')).stroke")


def nums(d):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", d)]


# ---- C, D, E: a real research task drives the presence ------------------------------------------------------------

def test_research_task_drives_presence_through_stages(spark, new_page):
    page = new_page(1280, 900)
    open_console(page, spark)
    page.evaluate(RECORD)
    task = spark.submit()
    deadline = time.time() + 90
    while spark.task(task["id"])["status"] not in ("completed", "partial", "failed") and time.time() < deadline:
        time.sleep(0.3)
    done = spark.task(task["id"])
    assert done["status"] in ("completed", "partial"), done
    finished_at = time.time()
    # delivered: 'achievement'; when consolidation asked the owner to review a finding, the fresher, user-critical
    # question wins the arbitration and the delivery is kept as a secondary activity
    expect(page.locator(".nsf-presence")).to_have_attribute("data-concept", re.compile("achievement|asking"), timeout=5000)
    shown_at = time.time()
    intent = page.evaluate("window.__nsfPresence.intent()")
    if intent["concept"] == "asking":
        with closing(store.connect(spark.path)) as db:
            assert db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0] >= 1
        assert any(o["concept"] == "achievement" for o in intent["others"]), intent
    seq = page.evaluate("window.__seq")
    acts = [s[0] for s in seq]

    def first(*names):
        return min((i for i, a in enumerate(acts) if a in names), default=None)
    # C: web research (searching / reading) during the real investigation
    c = first("searching", "reading")
    # D: research -> verification shows inspection / comparison
    d = first("verifying", "comparing")
    # E: verification -> response: the report is being written (generating), then delivered
    e = first("generating", "reasoning", "consolidating")
    r = max(i for i, s in enumerate(seq) if s[1] in ("achievement", "asking"))
    assert None not in (c, d, e) and c < d < e < r, seq
    assert {s[1] for s in seq if s[0] == "verifying"} <= {"evaluating evidence"}, seq   # Tier B inspection doodle
    assert any(s[2] == "B" for s in seq if s[0] == "verifying"), seq
    assert shown_at - finished_at < 3.0   # the delivered state follows the real completion within one long-poll
    assert stats(page)["maxConcurrentLoops"] <= 1 and stats(page)["paths"] == 14
    assert page.problems == []


# ---- G: authorised background research is shown subtly --------------------------------------------------------------

def test_background_research_is_subtle(spark, new_page):
    page = new_page(1280, 900)
    open_console(page, spark)
    spark.submit("Where does the okapi live?", origin="curiosity")   # autonomous research, authorised by config
    root = page.locator(".nsf-presence")
    expect(root).to_have_attribute("data-concept", "exploring", timeout=20_000)
    intent = page.evaluate("window.__nsfPresence.intent()")
    assert intent["priority"] == "background" and intent["subtle"] is True
    expect(page.locator(".nsf-presence path[data-slot=a3]")).not_to_have_class("pc-off")   # the orbiting dot
    assert stats(page)["energy"] <= 0.5 * 1.0 + 1e-9                                       # half motion energy
    page.locator(".pc-toggle").click()
    expect(page.locator(".pc-status")).to_contain_text("background research")
    assert page.problems == []


# ---- H: theme toggle mid-animation -----------------------------------------------------------------------------------

def test_theme_toggle_mid_animation_keeps_state_and_motion(spark, new_page):
    page = new_page(1280, 900)
    open_console(page, spark)
    spark.submit()
    root = page.locator(".nsf-presence")
    expect(root).to_have_attribute("data-activity", "reading", timeout=20_000)   # reading: continuous motion
    page.locator(".pc-toggle").click()
    page.wait_for_timeout(600)
    before = stats(page)
    assert before["running"] is True
    assert stroke(page) == "rgb(16, 16, 16)", (before, page.evaluate("window.__nsfPresence.intent()"))
    d0 = nums(body_d(page))
    page.get_by_label("Theme").select_option("dark")
    after_d = nums(body_d(page))
    assert stroke(page) == "rgb(247, 247, 247)"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(7, 9, 8)"
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.wait_for_timeout(500)
    after = stats(page)
    assert after["transitions"] == before["transitions"], "theme switch must not re-plan the scene"
    assert after["loopStarts"] == before["loopStarts"], "theme switch must not restart the animation loop"
    assert after["frames"] > before["frames"] and after["running"] is True
    assert after["concept"] == before["concept"] == "reading"
    assert len(d0) == len(after_d) and max(abs(a - b) for a, b in zip(d0, after_d)) < 6, "geometry preserved"
    page.reload()   # persisted: the new page starts dark without a flash (theme-boot.js)
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.goto("about:blank")
    page.goto(spark.url)
    page.locator(".pc-toggle").click()
    page.get_by_label("Theme").select_option("light")
    assert stroke(page) == "rgb(16, 16, 16)"
    assert page.problems == []


# ---- A, B, F: voice (event-level: nsf:voice events dispatched by the test, not real capture) -------------------------

def test_voice_events_listening_level_interrupt_and_mute(idle_spark, new_page):
    page = new_page(1280, 900)
    open_console(page, idle_spark)
    root = page.locator(".nsf-presence")
    # A: idle -> listening when capture starts; motion follows the reported level
    voice(page, mic_capturing=True, speaking=False, output_muted=False, mode="voice_text", level=0.05)
    expect(root).to_have_attribute("data-activity", "listening", timeout=2000)
    expect(root).to_have_attribute("data-concept", "listening")
    page.wait_for_timeout(400)
    low = page.evaluate("document.querySelector('.nsf-presence path[data-slot=a1]').getBBox().height")
    for _ in range(20):
        voice(page, mic_capturing=True, speaking=False, output_muted=False, mode="voice_text", level=0.95)
        page.wait_for_timeout(30)
    page.wait_for_timeout(500)
    high = page.evaluate("document.querySelector('.nsf-presence path[data-slot=a1]').getBBox().height")
    assert stats(page)["level"] == pytest.approx(0.95) and high > low * 1.8, (low, high)
    # mic off -> no listening motion
    voice(page, mic_capturing=False, speaking=False, output_muted=False, mode="voice_text", level=0.0)
    expect(root).to_have_attribute("data-activity", re.compile("idle|resting"), timeout=2000)
    assert stats(page)["level"] is None
    # B: after the utterance a real research task is what the presence shows (no invented "reasoning")
    voice(page, mic_capturing=True, speaking=False, output_muted=False, mode="voice_text", level=0.4)
    expect(root).to_have_attribute("data-activity", "listening", timeout=2000)
    idle_spark.submit()
    voice(page, mic_capturing=False, speaking=False, output_muted=False, mode="voice_text", level=0.0)
    expect(root).to_have_attribute("data-activity", re.compile("waiting|searching|reading|verifying|generating|consolidating|responding"),
                                   timeout=5000)
    # E (voice): speaking follows the output level; F: interruption -> listening without snapping
    voice(page, mic_capturing=True, speaking=True, output_muted=False, mode="voice_text", level=0.7)
    expect(root).to_have_attribute("data-activity", "responding", timeout=2000)
    page.wait_for_timeout(120)
    samples = []
    voice(page, mic_capturing=True, speaking=False, output_muted=False, mode="voice_text", level=0.6)   # barge-in
    for _ in range(40):
        samples.append(nums(body_d(page)))
        page.wait_for_timeout(16)
    expect(root).to_have_attribute("data-activity", "listening", timeout=2000)
    assert "user_interruption" in page.evaluate("window.__nsfPresence.intent()")["interaction"]
    jumps = [max(abs(a - b) for a, b in zip(p, q)) for p, q in zip(samples, samples[1:]) if len(p) == len(q)]
    assert jumps and max(jumps) < 20, max(jumps)          # no snap: bounded per-sample movement (springs)
    assert stats(page)["maxSpeed"] <= 260 + 1e-6          # the engine's velocity limit held
    # muted output: keep the cognitive state, never depict audible speech; text-only: no audio-driven motion
    voice(page, mic_capturing=False, speaking=True, output_muted=True, mode="voice_text", level=0.8)
    page.wait_for_timeout(500)
    assert page.evaluate("window.__nsfPresence.intent()")["activity"] != "responding"
    voice(page, mic_capturing=True, speaking=True, output_muted=False, mode="text_only", level=0.9)
    page.wait_for_timeout(500)
    i = page.evaluate("window.__nsfPresence.intent()")
    assert i["activity"] not in ("listening", "responding") and stats(page)["level"] is None
    assert page.problems == []


# ---- J: rapid transitions -------------------------------------------------------------------------------------------

def test_rapid_transitions_do_not_accumulate(idle_spark, new_page):
    page = new_page(1280, 900)
    open_console(page, idle_spark)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")

    def metrics():
        cdp.send("HeapProfiler.collectGarbage")
        return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    page.wait_for_timeout(500)
    m0, s0 = metrics(), stats(page)
    page.evaluate("""async () => {
      const kinds = [{mic_capturing:true,speaking:false},{mic_capturing:true,speaking:true},{mic_capturing:false,speaking:false},
                     {mic_capturing:false,speaking:true,output_muted:true}];
      for (let i = 0; i < 300; i++) {
        dispatchEvent(new CustomEvent('nsf:voice', {detail: {...kinds[i % 4], mode: 'voice_text', level: (i % 10) / 10}}));
        await new Promise(r => setTimeout(r, 8));
      }
    }""")
    page.wait_for_timeout(1500)
    m1, s1 = metrics(), stats(page)
    assert s1["paths"] == 14 and s1["maxConcurrentLoops"] <= 1
    assert s1["transitions"] - s0["transitions"] <= 300 * 8 / 220 + 4, "coalesced, not one scene per event"
    assert m1["JSEventListeners"] <= m0["JSEventListeners"], (m0["JSEventListeners"], m1["JSEventListeners"])
    assert m1["Nodes"] <= m0["Nodes"] + 5, (m0["Nodes"], m1["Nodes"])
    assert page.locator(".nsf-presence path").count() == 14
    assert page.problems == []


# ---- K: small screens ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [320, 360, 390])
def test_small_screens_console_and_pocket(idle_spark, bridge, new_page, width):
    for url in (idle_spark.url, bridge.url):
        page = new_page(width, 740)
        page.goto(url)
        root = page.locator(".nsf-presence")
        expect(root).to_be_visible()
        expect(root).to_have_attribute("data-size", "compact")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (url, width)
        box = page.locator(".nsf-presence .pc-svg").bounding_box()
        assert box["width"] <= 44 and box["x"] >= 0 and box["x"] + box["width"] <= width
        page.locator(".pc-toggle").click()
        panel = page.locator(".pc-panel").bounding_box()
        assert panel["x"] >= 0 and panel["x"] + panel["width"] <= width + 0.5, (url, width, panel)
        expect(root).to_have_attribute("data-size", "expanded")
        page.keyboard.press("Escape")
        expect(page.locator(".pc-panel")).to_be_hidden()
        expect(page.locator(".pc-toggle")).to_be_focused()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.problems == [], page.problems


# ---- L: reduced motion -----------------------------------------------------------------------------------------------

def test_reduced_motion_is_static_and_distinguishable(idle_spark, new_page):
    page = new_page(1280, 900)
    page.emulate_media(reduced_motion="reduce")
    open_console(page, idle_spark)
    shapes = {}
    for name, detail in (("listening", dict(mic_capturing=True, speaking=False)), ("responding", dict(mic_capturing=False, speaking=True)),
                         ("idle", dict(mic_capturing=False, speaking=False))):
        voice(page, **detail, output_muted=False, mode="voice_text", level=0.5)
        page.wait_for_timeout(500)
        shapes[name] = page.evaluate("[...document.querySelectorAll('.nsf-presence path')].map(p => p.getAttribute('class') + (p.getAttribute('d') || '')).join('|')")
    s = stats(page)
    assert s["reduced"] is True and s["frames"] == 0 and s["running"] is False, s
    assert len(set(shapes.values())) == 3, "each state has its own static geometry"
    assert page.evaluate("getComputedStyle(document.querySelector('.nsf-presence .pc-figure')).animationName") == "none"
    # the in-app toggle overrides the system: Full motion animates again, Reduced stops it
    page.emulate_media(reduced_motion="no-preference")
    page.locator(".pc-toggle").click()
    page.get_by_label("Motion", exact=True).select_option("reduced")
    frozen = stats(page)["frames"]
    voice(page, mic_capturing=True, speaking=False, output_muted=False, mode="voice_text", level=0.5)
    page.wait_for_timeout(500)
    assert stats(page)["frames"] == frozen and stats(page)["running"] is False
    page.get_by_label("Motion", exact=True).select_option("full")
    page.wait_for_timeout(500)
    assert stats(page)["frames"] > frozen
    assert page.problems == []


# ---- I: novel concepts -> procedural doodle or safe fallback; Tier C via the gallery ---------------------------------

def test_gallery_renders_every_tier_with_the_production_renderer(idle_spark, new_page):
    page = new_page(1280, 900)
    page.goto(idle_spark.base + "/presence/gallery")
    expect(page.locator("#glocked")).to_be_visible()          # behind the console token
    expect(page.locator("#gcontent")).to_be_hidden()
    page.goto(idle_spark.base + "/presence/gallery#token=" + TOKEN)
    expect(page.locator("#gcontent")).to_be_visible()
    cells = page.locator("#aLight figure")
    expect(cells).to_have_count(23)
    tiers = page.locator("#bLight figure").evaluate_all("fs => fs.map(f => [f.dataset.concept, f.dataset.tier])")
    assert len(tiers) >= 19 and all(t == "B" for _, t in tiers), tiers
    novel = dict(tiers)
    assert novel["quantum gardening"] == "B" and novel["tidal folklore"] == "B"
    # every visible path stays inside the viewBox
    out = page.evaluate("""() => [...document.querySelectorAll('#gcontent .gcell path:not(.pc-off)')].filter(p => {
        const b = p.getBBox(); return b.x < -5 || b.y < -5 || b.x + b.width > 105 || b.y + b.height > 105; }).length""")
    assert out == 0
    # Tier C without a configured model: the server says so and the page shows the procedural/canonical fallback
    page.locator("#cConcept").fill("untangling a timeline")
    page.locator("#cAsk").click()
    expect(page.locator("#cResult")).to_contain_text("not_configured")
    expect(page.locator("#cLight figure")).to_have_attribute("data-tier", re.compile("B|fallback"))
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for panel, name in (("aLight", "tierA-light"), ("aDark", "tierA-dark"), ("bLight", "tierB-light"), ("bDark", "tierB-dark"),
                        ("sLight", "sizes-light"), ("sDark", "sizes-dark")):
        page.locator(f"#{panel}").screenshot(path=str(SNAPSHOTS / f"gallery-{name}.png"))
    assert page.problems == []


def test_tier_c_model_spec_validated_in_browser(idle_spark, new_page):
    import sys
    sys.path.insert(0, str(ROOT / "tests" / "inference"))
    from fake_openai import FakeOpenAI
    from neuroscifi.inference import config as iconfig
    from neuroscifi.presence import scene_spec
    scene_spec._results.clear()
    fake = FakeOpenAI(key=None).start()
    try:
        with closing(store.connect(idle_spark.path)) as db:
            iconfig.set_config(db, {"base_url": fake.url}, time.time())
        good = {"schema": "nsf.presence.scene/1", "concept": "x", "body": "present", "eyes": "open", "motion": "drift",
                "elements": [{"primitive": "spiral", "x": 70, "y": 40, "size": 24, "rotation": 0, "tone": "ink"},
                             {"primitive": "node", "x": 86, "y": 72, "size": 10, "rotation": 0, "tone": "ink"},
                             {"primitive": "dot", "x": 60, "y": 76, "size": 6, "rotation": 0, "tone": "blue"}]}
        fake.replies += [{"content": json.dumps(good)},
                         {"content": "<svg onload=alert(1)>"}, {"content": "{\"schema\": \"evil\"}"}]
        page = new_page(1280, 900)
        page.goto(idle_spark.base + "/presence/gallery#token=" + TOKEN)
        page.locator("#cConcept").fill("untangling a timeline")
        page.locator("#cAsk").click()
        expect(page.locator("#cResult")).to_contain_text("Model spec accepted")
        expect(page.locator("#cLight figure")).to_have_attribute("data-tier", "C")
        page.locator("#cConcept").fill("weaving a hypothesis")
        page.locator("#cAsk").click()
        expect(page.locator("#cResult")).to_contain_text("No model spec (unavailable: schema_invalid)")
        expect(page.locator("#cLight figure")).to_have_attribute("data-tier", re.compile("B|fallback"))
        assert page.evaluate("window.alert.toString()").startswith("function alert")
        assert page.problems == []
    finally:
        fake.stop()


# ---- Pocket: family-scoped presence of a paired phone -------------------------------------------------------------

def test_pocket_presence_shows_own_family_task(bridge, new_page, tmp_path):
    instance = tmp_path / "individual.sqlite"
    with closing(store.create(instance, time.time())) as db:
        state.set_config(db, {"enabled": True, "pocket_research": True}, time.time())
        orchestrator.submit(db, "A question from another family", time.time(), origin="pocket",
                            family=state.family_digest(db, "someone-else"))
    bridge.server.instance_db = str(instance)
    page = new_page(390, 844)
    page.goto(bridge.url)
    pair(page)
    root = page.locator(".nsf-presence")
    expect(root).to_have_attribute("data-activity", re.compile("idle|resting"), timeout=15_000)
    page.locator(".pc-toggle").click()
    expect(page.locator(".pc-status")).not_to_contain_text("another family")
    page.keyboard.press("Escape")
    page.locator("#objective").fill("What is the Zephyr compiler?")
    page.locator("#researchsubmit").click()
    expect(root).to_have_attribute("data-activity", "waiting", timeout=15_000)
    page.locator(".pc-toggle").click()
    expect(page.locator(".pc-status")).to_contain_text("Zephyr")
    expect(page.locator(".pc-status")).not_to_contain_text("another family")
    assert page.problems == []


# ---- accessibility: axe on console and Pocket with the panel open ---------------------------------------------------

@pytest.mark.parametrize("theme", ["light", "dark"])
def test_presence_axe(idle_spark, bridge, new_page, theme):
    found = {}
    for name, url, size in (("console", idle_spark.url, (1280, 900)), ("pocket", bridge.url, (390, 844))):
        page = new_page(*size, bypass_csp=True)
        page.goto(url)
        expect(page.locator(".nsf-presence")).to_be_visible()
        page.locator(".pc-toggle").click()
        page.get_by_label("Theme").select_option(theme)
        page.add_script_tag(path=str(AXE))
        result = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
            r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))""")
        bad = [v for v in result if v["impact"] in ("serious", "critical")]
        if bad:
            found[name] = bad
        if name == "console" and theme == "dark":   # the whole console in the dark theme, view by view
            page.keyboard.press("Escape")
            for view in page.locator("#nav a").evaluate_all("as => as.map(a => a.dataset.view)"):
                page.locator(f"#nav a[data-view={view}]").click()
                expect(page.locator(f"html[data-view='{view}'][data-inflight='0']")).to_have_count(1)
                page.locator("details").evaluate_all("els => els.forEach(e => e.open = true)")
                result = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
                    r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))""")
                bad = [v for v in result if v["impact"] in ("serious", "critical")]
                if bad:
                    found[f"console-{view}"] = bad
        live = page.locator(".nsf-presence [aria-live=polite]")
        expect(live).to_have_count(1)
        SHOTS.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOTS / f"presence-{name}-{theme}.png"))
    assert not found, found
