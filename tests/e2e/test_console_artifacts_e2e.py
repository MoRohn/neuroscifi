"""The console's "Made and kept" view in Chromium under the console's real policy (OMNIMIND lane U, ADR-079): light and
dark with axe, the keyboard walk, a made chart rebuilt from its SVG, a document with "How the sources relate", the
"why", an explorable model operated by keyboard and unmounted on leaving, a sound played through Web Audio, a raster
picture from its short-lived address, making from the console (a picture, and words), upload off and on, and the
360 px layout. Screenshots go to test-results/ (and the lane's scratch folder when OMNI_U_SHOTS names it)."""
import json
import math
import os
import shutil
import struct
import threading
import time
import wave
import io
import zlib
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS
from neuroscifi.artifacts import produce, store as astore
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store

TOKEN = "e2e-artifacts-token-" + "a" * 28
WEEK = b"day,calls\nmon,120\ntue,95\nwed,130\nthu,80\nfri,75\n"
REPORT = b"# Weekly calls\n\nThe total of calls was 520.\n"
MODEL = {"format": "neuroscifi.model/1", "title": "Falling ball", "description": "Height under gravity.",
         "parameters": [{"name": "g", "label": "Gravity", "min": 1, "max": 20, "step": 0.1, "value": 9.8,
                         "unit": "m/s²"},
                        {"name": "h0", "label": "Start height", "min": 1, "max": 100, "step": 1, "value": 50,
                         "unit": "m"}],
         "quantities": [{"name": "t_fall", "label": "Time to fall", "expr": "sqrt(2*h0/g)", "unit": "s"}],
         "views": [{"kind": "table", "items": ["t_fall"]}]}
EXTRA = Path(os.environ["OMNI_U_SHOTS"]) if os.environ.get("OMNI_U_SHOTS") else None


def wav_bytes(seconds=1.0, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def png_bytes(w=24, h=16):
    raw = b"".join(b"\x00" + bytes((x * 10 + y * 5) % 256 for x in range(w)) for y in range(h))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Console:
    def __init__(self, folder):
        self.path = Path(folder) / "owner.sqlite"
        now = time.time()
        db = store.create(self.path, now)
        try:
            self.ids = {"week": astore.add(db, WEEK, name="week.csv", now=now).id,
                        "sound": astore.add(db, wav_bytes(), name="tone.wav", now=now).id,
                        "picture": astore.add(db, png_bytes(), name="gradient.png", now=now).id}
            self.ids["model"] = produce.produce(db, "what happens when I change gravity?",
                                                [produce.Given("ball.json", json.dumps(MODEL).encode())],
                                                now=now).artifact
            self.ids["document"] = produce.produce(db, "a report of the week", [
                produce.Given("week-copy.csv", WEEK), produce.Given("report.md", REPORT)], form="document",
                now=now).artifact
            self.ids["chart"] = produce.produce(db, "compare calls by day", [self.ids["week"]], now=now).artifact
        finally:
            db.close()
        assert all(self.ids.values()), self.ids
        self.server = ConsoleServer(self.path, 0, token=TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = self.server.url

    def set(self, **changes):
        db = store.open_existing(self.path)
        try:
            store.set_config(db, changes, time.time())
        finally:
            db.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def console(tmp_path):
    c = Console(tmp_path)
    yield c
    c.close()


def shot(page, name, locator=None):
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / f"artifacts-{name}.png"
    (locator or page).screenshot(path=str(path), **({} if locator else {"full_page": True}))
    if EXTRA:
        EXTRA.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, EXTRA / path.name)


def go(page, console):
    page.goto(console.url)
    expect(page.locator("#h-overview")).to_be_visible()
    page.locator("#nav").get_by_role("link", name="Made and kept").click()
    expect(page.locator("html[data-view='artifacts'][data-inflight='0']")).to_have_count(1)
    expect(page.locator("#artifactList .art-item").first).to_be_visible()


def show(page, console, which):
    page.locator(f"#artifactList .art-item[data-id='{console.ids[which]}'] .art-open").click()
    expect(page.locator(f"#artifactList .art-item.current[data-id='{console.ids[which]}']")).to_have_count(1)
    expect(page.locator("#artifactPane[aria-busy='true']")).to_have_count(0)


def axe(page):
    page.add_script_tag(path=str(AXE))
    page.locator("#artifacts details").evaluate_all("els => els.forEach(e => e.open = true)")
    found = page.evaluate("""axe.run(document.getElementById('artifacts'),{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' ')).slice(0,5)})))""")
    return [v for v in found if v["impact"] in ("serious", "critical")]


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page overflows"
    wide = page.evaluate("""[...document.querySelectorAll('#artifacts *')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && r.right > window.innerWidth + 1 &&
          !e.closest('.tablewrap, .art-table, .art-explorable, pre'); }).map(e => e.tagName + '.' + e.className).slice(0, 5)""")
    assert wide == [], wide


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1280, 360], ids=["wide", "phone"])
def test_the_view_in_both_themes_and_at_phone_width_with_axe(console, new_page, scheme, width):
    page = new_page(width, 900, color_scheme=scheme, bypass_csp=True)   # bypass only so axe can be injected
    go(page, console)
    tag = f"{scheme}-{'phone' if width == 360 else 'wide'}"
    expect(page.locator("html")).to_have_attribute("data-theme", scheme)
    # the Presence entity is the view's centrepiece: it sits in this view's stage
    expect(page.locator("#artifacts [data-presence-dock] .nsf-presence")).to_have_count(1)
    expect(page.locator("#artifactList .art-item")).to_have_count(9)   # 3 given, their 3 sources kept by make, 3 made
    made = page.locator(f"#artifactList .art-item[data-id='{console.ids['chart']}']")
    expect(made).to_contain_text("Made by NeuroSciFi, not evidence")
    expect(made).to_contain_text("Made from week.csv")
    expect(page.locator(f"#artifactList .art-item[data-id='{console.ids['week']}']")).to_contain_text(
        "A table of 5 rows and 2 columns")
    shot(page, f"list-{tag}")
    no_overflow(page)
    show(page, console, "chart")
    expect(page.locator("#artifactPane figure svg.art-svg g.plot rect.mark")).to_have_count(5)
    expect(page.locator("#artifactPane .warnline")).to_have_count(0)            # nothing had to be left out
    shot(page, f"chart-{tag}", page.locator("#artifactCard"))
    no_overflow(page)
    show(page, console, "document")
    expect(page.locator("#artifactPane .art-doc")).to_contain_text("How the sources relate")
    expect(page.locator("#artifactPane .art-doc")).to_contain_text("They DISAGREE")
    expect(page.locator("#artifactPane .art-doc figure svg.art-svg").first).to_be_visible()
    page.locator("#artifactPane details.art-why summary").click()
    expect(page.locator("#artifactPane details.art-why")).to_contain_text("Related week-copy.csv and report.md")
    shot(page, f"document-{tag}", page.locator("#artifactCard"))
    no_overflow(page)
    show(page, console, "model")
    expect(page.locator("#artifactPane .art-explorable input[type=range]").first).to_be_visible()
    shot(page, f"explorable-{tag}", page.locator("#artifactCard"))
    no_overflow(page)
    bad = axe(page)
    assert bad == [], bad
    show(page, console, "chart")
    page.locator("#artifactPane details.art-why summary").click()
    shot(page, f"why-{tag}", page.locator("#artifactCard"))
    bad = axe(page)
    assert bad == [], bad
    assert page.problems == []


def test_under_the_real_policy_nothing_is_refused_and_no_markup_is_written(console, new_page):
    page = new_page(1280, 900)
    violations = []
    page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
    go(page, console)
    for which in ("chart", "document", "model", "sound", "week", "picture"):
        show(page, console, which)
    expect(page.locator("#artifactPane img.art-raster")).to_be_visible()
    page.wait_for_function("document.querySelector('#artifactPane img.art-raster').complete")
    assert page.evaluate("document.querySelector('#artifactPane img.art-raster').naturalWidth") == 24
    assert violations == [] and page.problems == []
    # the explorable runtime is unmounted when the owner leaves the view
    show(page, console, "model")
    expect(page.locator("#artifactPane .art-explorable input[type=range]").first).to_be_visible()
    page.locator("#nav").get_by_role("link", name="Overview").click()
    expect(page.locator("#artifactPane .art-explorable input")).to_have_count(0)


def test_the_keyboard_walk_reaches_everything_and_an_explorable_model_is_operated_by_keys(console, new_page):
    page = new_page(1280, 900)
    go(page, console)
    page.locator("#h-artifacts").focus()
    seen = []
    for _ in range(60):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.id || document.activeElement.className || document.activeElement.tagName"))
        if page.evaluate(f"document.activeElement.closest(\"[data-id='{console.ids['model']}']\") !== null "
                         "&& document.activeElement.classList.contains('art-open')"):
            break
    else:
        raise AssertionError(f"the model's entry was not reached by Tab: {seen}")
    assert "makeRequest" in seen
    page.keyboard.press("Enter")
    expect(page.locator("#h-artifact")).to_be_focused()
    slider = page.locator("#artifactPane .art-explorable input[type=range]").first
    expect(slider).to_be_visible()
    before = page.locator("#artifactPane .art-explorable table").first.inner_text()
    slider.focus()
    for _ in range(20):
        page.keyboard.press("ArrowRight")
    expect(page.locator("#artifactPane .art-explorable table").first).not_to_have_text(before)
    # the details are reachable too: the why disclosure and Forget
    page.locator("#artifactPane details.art-why summary").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#artifactPane details.art-why")).to_have_attribute("open", "")
    assert page.problems == []


def test_a_sound_plays_through_web_audio(console, new_page):
    page = new_page(1280, 900)
    go(page, console)
    show(page, console, "sound")
    expect(page.locator("#artifactPane .art-sound")).to_contain_text("A recording of 1 second: one channel")
    page.locator("#artPlay").click()
    expect(page.locator("html[data-artifact-audio='playing']")).to_have_count(1)
    expect(page.locator("#artSoundStatus")).to_contain_text("Playing (1.0 seconds)")
    page.locator("#artStop").click()
    expect(page.locator("html[data-artifact-audio='stopped']")).to_have_count(1)
    assert page.evaluate("document.querySelectorAll('audio, video, iframe, object, embed').length") == 0
    assert page.problems == []


def test_make_from_the_console_a_picture_and_words(console, new_page):
    page = new_page(1280, 900)
    go(page, console)
    page.locator(f"#artifactList input[data-id='{console.ids['week']}']").check()
    expect(page.locator("#makeChosen")).to_have_text("From one chosen file.")
    page.locator("#makeRequest").fill("compare calls by day")
    page.locator("#makeBtn").click()
    expect(page.locator("#makeStatus")).to_contain_text("Made, checked and kept", timeout=60_000)
    expect(page.locator("#artifactPane svg.art-svg g.plot")).to_be_visible()
    expect(page.locator("#artifactList .art-item").first).to_contain_text("Made by NeuroSciFi, not evidence")
    page.locator("#makeRequest").fill("what is the busiest day?")
    page.locator("#makeBtn").click()
    expect(page.locator("#makeStatus")).not_to_have_text("Working…", timeout=60_000)
    text = page.locator("#artifactPane").inner_text()
    assert "No picture was made because" in text or page.locator("#artifactPane svg.art-svg").count() == 1
    assert page.problems == []


def test_upload_is_off_by_default_and_works_when_turned_on(console, new_page, tmp_path):
    page = new_page(1280, 900)
    go(page, console)
    expect(page.locator("#uploadBox")).to_be_hidden()
    expect(page.locator("#uploadOff")).to_contain_text("nsf config --console-artifact-upload on")
    console.set(console_artifact_upload=True)
    page.locator("#refreshArtifacts").click()
    expect(page.locator("#uploadBox")).to_be_visible()
    shot(page, "upload-on", page.locator("#makeForm").locator(".."))
    sample = tmp_path / "rain.csv"
    sample.write_bytes(b"month,rain\njan,80\nfeb,60\nmar,55\n")
    page.locator("#uploadFile").set_input_files(str(sample))
    expect(page.locator("#uploadStatus")).to_contain_text("Kept rain.csv: a table of 3 rows and 2 columns")
    expect(page.locator("#artifactList .art-item").first).to_contain_text("rain.csv")
    console.set(console_artifact_upload=False)
    page.locator("#refreshArtifacts").click()
    expect(page.locator("#uploadBox")).to_be_hidden()
    assert page.problems == []


def test_forget_asks_first(console, new_page):
    page = new_page(1280, 900)
    go(page, console)
    show(page, console, "picture")
    page.locator("#artForget").click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    page.locator("#confirmCancel").click()
    expect(page.locator(f"#artifactList .art-item[data-id='{console.ids['picture']}']")).to_have_count(1)
    page.locator("#artForget").click()
    page.locator("#confirmOk").click()
    expect(page.locator("#artifactStatus")).to_contain_text("Forgotten")
    expect(page.locator(f"#artifactList .art-item[data-id='{console.ids['picture']}']")).to_have_count(0)
    assert page.problems == []


HOSTILE = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" '
           'onload="window.pwned=1"><script>window.pwned=2</script><style>rect{fill:url(http://x/y)}</style>'
           '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject>'
           '<a href="javascript:window.pwned=3"><rect id="r" width="5" height="5" onclick="window.pwned=4"/></a>'
           '<use xlink:href="#r"/><image href="http://x/i.png"/><rect width="1" height="1" style="fill:red" '
           'fill="url(http://x/f)"/><rect width="2" height="2" fill="url(#r)" data-x="url(http://x)"/>'
           '<animate attributeName="x" to="javascript:1"/><text>ok</text></svg>')


def test_the_importer_drops_everything_active_in_a_hostile_picture(console, new_page):
    page = new_page(1280, 900)
    go(page, console)
    out = page.evaluate("""async (text) => {
        const m = await import('/artifacts.mjs');
        const { node, dropped } = m.importSvg(text);
        document.body.append(node);
        const all = [...node.querySelectorAll('*')];
        return { tags: [node.localName, ...all.map(e => e.localName)],
                 attrs: [node, ...all].flatMap(e => [...e.attributes].map(a => a.name + '=' + a.value)),
                 dropped: dropped.map(d => d[0]), pwned: window.pwned ?? null };
    }""", HOSTILE)
    assert out["pwned"] is None
    assert set(out["tags"]) <= {"svg", "rect", "text"}, out["tags"]
    joined = " ".join(a for a in out["attrs"] if not a.startswith("data-"))
    for bad in ("onload", "onclick", "href", "style=", "http://", "javascript"):
        assert bad not in joined, (bad, joined)
    assert any(a.startswith("fill=url(#art") for a in out["attrs"])                 # an inside reference, renamed
    assert "data-x=url(http://x)" in out["attrs"]                                    # text, never a reference
    for word in ("the element script", "the element foreignObject", "the element a", "the element use",
                 "the element image", "the element animate", "an event handler", "the attribute style",
                 "a reference outside the picture"):
        assert word in out["dropped"], (word, out["dropped"])
    # parsing the hostile text makes the browser report its handlers as refused by the policy (the second layer);
    # nothing else is reported and nothing ran
    assert all("Content Security Policy" in p and "blocked" in p for p in page.problems), page.problems
