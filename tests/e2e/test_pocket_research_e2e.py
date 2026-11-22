"""Research from Pocket in a real Chromium (ADR-028): pair, submit, follow stages while the Spark's worker runs
offline (FixtureWeb + CorpusProvider), read the cited report, save it in the encrypted vault and reread it offline;
a linked tablet continues the same task; axe on phone and tablet."""
from contextlib import closing
import json
import re
import threading
import time

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, Bridge, pair
from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, scheduler, state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider

QUESTION = "What is the latest version of the Zephyr compiler?"
PASS = "correct horse battery"
PAGES = {
    ("news-one.example", "/zephyr"): ("Zephyr compiler 4.2 released", "2026-11-18T10:00:00Z",
                                      "The Zephyr compiler version 4.2.0 was released on 17 November 2026 by the "
                                      "Zephyr project team."),
    ("tech-two.example", "/news/zephyr-42"): ("Zephyr 4.2 is out", "2026-11-19T09:00:00Z",
                                              "The Zephyr compiler version 4.2.0 was released on 17 November 2026 "
                                              "after a long beta."),
    ("blog-three.example", "/zephyr-review"): ("Hands on with the Zephyr compiler", "2026-11-20T09:00:00Z",
                                               "We tested the Zephyr compiler version 4.2.0 released on 17 November "
                                               "2026 on three boards."),
}


class Spark:
    """The e2e bridge plus an individual with Pocket research on, and an offline public web for its worker."""

    def __init__(self, folder, delay):
        self.bridge = Bridge(folder)
        self.url = self.bridge.url
        self.instance = folder / "individual.sqlite"
        with closing(store.create(self.instance, time.time())) as db:
            state.set_config(db, {"enabled": True, "pocket_research": True, "min_domain_interval": 0.0}, time.time())
        self.bridge.server.instance_db = str(self.instance)
        self.web = FixtureWeb().start()
        for (host, path), (title, date, text) in PAGES.items():
            self.web.add(host, path, f"<html><head><title>{title}</title><meta property='article:published_time' "
                                     f"content='{date}'></head><body><article><h1>{title}</h1><p>{text}</p>"
                                     f"<p>The release adds faster builds.</p></article></body></html>", delay=delay)
        self.corpus = CorpusProvider([{"url": f"http://{h}{p}", "title": t, "snippet": x[:160]}
                                      for (h, p), (t, _, x) in PAGES.items()])
        self.worker = None

    def fulfill_signed(self, route, body: dict, status=200):
        """Answer `route` with `body` as this Spark would: signed with its key for that request's nonce (ADR-042
        draft). Lets a test shape a reply without being the tampering that Pocket refuses."""
        from neuroscifi.webcortex import signing
        raw = json.dumps(body).encode()
        path = "/" + route.request.url.split("/", 3)[3]
        message = signing.canonical_response(status, path, route.request.headers.get("x-nsf-nonce", ""), raw)
        key = signing.SparkKey.load_or_create(signing.key_path(self.instance))
        route.fulfill(status=status, content_type="application/json", body=raw,
                      headers={"X-NSF-Spark-Signature": signing.b64(key.sign(message))})

    def work(self, background=False):
        run = lambda: scheduler.work(self.instance, once=True, fixture=self.web, providers=[self.corpus])  # noqa: E731
        if not background:
            return run()
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def close(self):
        if self.worker:
            self.worker.join(60)
        self.web.stop()
        self.bridge.close()


@pytest.fixture
def spark(tmp_path):
    s = Spark(tmp_path, delay=0.0)
    yield s
    s.close()


@pytest.fixture
def slow_spark(tmp_path):
    s = Spark(tmp_path, delay=2.0)   # each page read takes 2 s, so the phone sees the stages go by
    yield s
    s.close()


def posts(page):
    seen = []
    page.on("request", lambda r: seen.append((r.url.rsplit("/", 1)[-1], r.post_data)) if r.method == "POST" else None)
    return seen


def ask(page, text=QUESTION):
    expect(page.locator("#research")).to_be_visible()
    page.locator("#objective").fill(text)
    expect(page.locator("#objectivecount")).to_have_text(
        f"{len(text)} / 300 characters · {len(json.dumps({'objective': text}, ensure_ascii=False, separators=(',', ':')).encode())} / 1024 bytes")
    page.locator("#researchsubmit").click()
    expect(page.locator("#researchmsg")).to_contain_text("Queued on your Spark")
    expect(page.locator("#researchview h3")).to_have_text(text)


def unlock(page):
    page.locator("summary", has_text="Keep episodes on this device").click()
    page.locator("#pass").fill(PASS)
    page.locator("#unlock").click()
    expect(page.locator("#vaultstatus")).to_have_text("Vault unlocked")


def test_research_journey_with_offline_copy(slow_spark, new_page):
    page = new_page()
    sent = posts(page)
    page.goto(slow_spark.url)
    expect(page.locator("#research")).to_be_hidden()
    expect(page.locator("#offlinestate")).to_have_text("Offline shell ready")
    assert not sent, "nothing leaves before pairing"
    pair(page)
    expect(page.locator("#navresearch")).to_be_visible()
    expect(page.locator("#dictate")).to_be_hidden()   # Chromium here has no on-device recognition gate
    expect(page.locator("#researchsubmit")).to_be_disabled()
    page.evaluate("""() => { window.stages = []; new MutationObserver(() => { const s = document.querySelector('.rstage');
        if (s && window.stages.at(-1) !== s.textContent) window.stages.push(s.textContent); })
        .observe(document.getElementById('researchview'), {subtree: true, childList: true, characterData: true}); }""")
    ask(page)
    expect(page.locator(".rstage")).to_contain_text("Queued")
    slow_spark.work(background=True)
    expect(page.locator(".rstage")).to_have_text("Completed", timeout=45_000)
    stages = page.evaluate("window.stages")
    assert any(s.startswith("Reading") for s in stages), stages
    assert stages[-1] == "Completed" and stages.index(next(s for s in stages if s.startswith("Reading"))) < len(stages) - 1
    # The report: quoted answer with [n] citations, dated sources with Live badges, http(s) links only.
    quote = page.locator("#researchview blockquote").first
    expect(quote).to_contain_text("4.2.0")
    expect(quote.locator(".cites a").first).to_have_text("[1]")
    expect(page.locator(".rsources li")).to_have_count(3)
    expect(page.locator(".rsources .badge").first).to_have_text("Live")
    expect(page.locator(".rsources li").first).to_contain_text("Published")
    for link in page.locator(".rsources a").all():
        assert link.get_attribute("rel") == "noopener noreferrer"
        assert link.get_attribute("href").startswith("http://")
    expect(page.locator("#researchlist")).to_contain_text("Completed")
    # Saving needs the vault; nothing is stored in plaintext.
    page.get_by_role("button", name="Save for offline").click()
    expect(page.locator("#researchmsg")).to_contain_text("Open your device vault first")
    assert page.evaluate("Object.keys(localStorage)") == []
    unlock(page)
    page.get_by_role("button", name="Save for offline").click()
    expect(page.locator("#researchmsg")).to_have_text("Saved in your encrypted vault for 24 hours.")
    expect(page.locator("#researchsaved li")).to_have_count(1)
    vault = page.evaluate("localStorage.getItem('neuroscifi.field.v1')")
    assert "4.2.0" not in vault and "Zephyr" not in vault, "the saved report is encrypted"
    assert [json.loads(raw) for route, raw in sent if route == "research_submit"] == [{"objective": QUESTION}]
    assert {route for route, _ in sent if route.startswith("research_")} == {   # session: who drives it (status)
        "research_list", "research_submit", "research_get", "research_session"}
    assert all(json.loads(raw)["action"] == "status" for route, raw in sent if route == "research_session")
    # Offline: cold reopen from the service worker, unlock the vault, read the saved finding.
    page.context.set_offline(True)
    page.reload()
    expect(page.locator("#research")).to_be_hidden()
    unlock(page)
    expect(page.locator("#research")).to_be_visible()
    expect(page.locator("#researchform")).to_be_hidden()
    page.get_by_role("button", name="View saved: " + QUESTION).click()
    expect(page.locator(".rstage")).to_have_text("Saved copy · Completed")
    expect(page.locator("#researchview blockquote").first).to_contain_text("4.2.0")
    expect(page.locator(".rsources li")).to_have_count(3)
    page.locator("#lock").click()
    expect(page.locator("#research")).to_be_hidden()
    page.context.set_offline(False)
    assert not page.problems, page.problems


def test_tablet_continues_phone_research_and_polling_stops_when_hidden(spark, new_page):
    phone, tablet, stranger = new_page(390, 844), new_page(1024, 768), new_page()
    phone.goto(spark.url)
    pair(phone)
    phone.locator("#linkdevice").click()
    expect(phone.locator("#handoff")).to_contain_text("Enter on your other device")
    code = phone.locator("#handoff").inner_text().rsplit(": ", 1)[1].strip()
    ask(phone)
    tablet.clock.install()
    sent = posts(tablet)
    tablet.goto(spark.url)
    pair(tablet, code)
    expect(tablet.locator("#researchlist li")).to_have_count(1)
    expect(tablet.locator("#researchlist")).to_contain_text(QUESTION)
    expect(tablet.locator("#researchlist")).to_contain_text("Queued")
    gets = lambda: sum(1 for route, _ in sent if route == "research_get")  # noqa: E731
    tablet.clock.run_for(3100)
    deadline = time.monotonic() + 5
    while not gets() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert gets() >= 1, "an active task is polled every 3 s"
    tablet.clock.run_for(3100)
    expect(tablet.locator("#researchlist")).to_contain_text("Queued")
    tablet.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:true});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    before = gets()
    tablet.clock.run_for(15000)
    assert gets() == before, "a hidden page stops polling"
    tablet.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:false});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    tablet.get_by_role("button", name="Open: " + QUESTION).click()
    tablet.get_by_role("button", name="Cancel research").click()
    expect(tablet.locator(".rstage")).to_have_text("Cancelled")
    phone.locator("#refresh").click()
    expect(phone.locator("#researchlist")).to_contain_text("Cancelled")
    # An unrelated pairing sees nothing; the owner (console/CLI) sees the task.
    spark.bridge.invite("other-code")
    stranger.goto(spark.url)
    pair(stranger, "other-code")
    expect(stranger.locator("#researchlist")).to_contain_text("No questions from your devices yet.")
    with closing(store.connect(spark.instance)) as db:
        assert [(t["objective"], t["status"], t["origin"]) for t in orchestrator.listing(db)] == [
            (QUESTION, "cancelled", "pocket")]
    assert not phone.problems and not tablet.problems and not stranger.problems


def test_dictation_fills_the_field_and_never_submits(spark, new_page):
    page = new_page()
    page.add_init_script("""(() => {
        class FakeRecognition { constructor(){ this.processLocally = false; }
          static async available(o){ return o.processLocally && o.langs.join() === 'en-US' ? 'available' : 'unavailable'; }
          start(){ setTimeout(() => { const alt = [{transcript: 'Zephyr compiler release notes'}]; alt.isFinal = true;
            this.onresult && this.onresult({results: [alt]}); this.onend && this.onend(); }, 50); }
          abort(){} }
        FakeRecognition.prototype.processLocally = false;
        window.SpeechRecognition = FakeRecognition; })()""")
    sent = posts(page)
    page.goto(spark.url)
    pair(page)
    expect(page.locator("#dictate")).to_be_visible()
    page.locator("#dictate").click()
    expect(page.locator("#objective")).to_have_value("Zephyr compiler release notes")
    expect(page.locator("#researchmsg")).to_contain_text("submit it yourself")
    page.wait_for_timeout(300)
    assert not [r for r, _ in sent if r == "research_submit"], "dictation never submits"


@pytest.mark.parametrize("size", [(1024, 768), (390, 844)], ids=["tablet", "phone"])
def test_research_layout_and_accessibility(spark, new_page, size):
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; app CSP is asserted in the journey test
    page.goto(spark.url)
    pair(page)
    ask(page)
    spark.work()
    page.locator("#refresh").click()
    expect(page.locator("#researchlist")).to_contain_text("Completed")
    page.get_by_role("button", name="Open: " + QUESTION).click()
    expect(page.locator(".rsources li")).to_have_count(3)
    unlock(page)
    page.get_by_role("button", name="Save for offline").click()
    expect(page.locator("#researchsaved li")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for sel in ("#research button", "#research select"):
        for box in (b.bounding_box() for b in page.locator(sel).all() if b.is_visible()):
            assert box["height"] >= 43.5, (sel, box)
    SHOTS.mkdir(exist_ok=True)
    page.locator("#research").screenshot(path=str(SHOTS / f"pocket-research-{'tablet' if size[0] > 800 else 'phone'}.png"))
    page.add_script_tag(path=str(AXE))
    found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")
    assert [v for v in found if v["impact"] in ("serious", "critical")] == []


CLAIM = "Is it true that the Zephyr compiler version 4.2.0 was released on 17 November 2026?"
SPEECH = """(() => {
    window.spoken = []; window.voices = [{name: 'Remote', lang: 'en-US', localService: false},
                                         {name: 'Local', lang: 'en-US', localService: true}];
    window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
    Object.defineProperty(window, 'speechSynthesis', {configurable: true, value: {
        getVoices: () => window.voices, addEventListener() {}, cancel() {},
        speak(u) { window.spoken.push({text: u.text, voice: u.voice && u.voice.name});
                   setTimeout(() => u.onend && u.onend(), 300); }}}); })()"""


def test_shared_link_prefills_the_question_and_never_submits(spark, new_page):
    page = new_page()
    sent = posts(page)
    page.goto(spark.url + "?share_url=https://example.org/a&share_text=x")
    expect(page).to_have_url(spark.url)   # the shared link leaves the address bar and history at once
    expect(page.locator("#research")).to_be_visible()
    expect(page.locator("#researchform")).to_be_hidden()
    expect(page.locator("#researchhelp")).to_contain_text("Pair with a research-enabled Spark")
    expect(page.locator("#researchmsg")).to_have_text("Shared link ready. Review the question, then submit it yourself.")
    assert not sent, "nothing leaves before pairing"
    pair(page)
    expect(page.locator("#researchform")).to_be_visible()
    expect(page.locator("#objective")).to_have_value("Summarize: https://example.org/a")
    expect(page.locator("#objective")).to_be_focused()
    expect(page.locator("#researchsubmit")).to_be_enabled()
    SHOTS.mkdir(exist_ok=True)
    page.locator("#research").screenshot(path=str(SHOTS / "pocket-research-share.png"))
    page.wait_for_timeout(500)
    assert not [r for r, _ in sent if r == "research_submit"], "a shared link is never submitted for the owner"
    # Android puts the link in the text; anything but an http(s) link, or one too long, prefills nothing.
    for query, value, note in (
            ("?share_text=Worth+a+read+https://example.org/b", "Summarize: https://example.org/b", "Shared link ready"),
            ("?share_url=javascript:alert(1)&share_text=hello", "", "http or https"),
            ("?share_url=https://user:secret@example.org/c", "", "http or https"),
            ("?share_url=https://example.org/" + "a" * 290, "", "too long")):
        page.goto(spark.url + query)
        expect(page.locator("#objective")).to_have_value(value)
        expect(page.locator("#researchmsg")).to_contain_text(note)
        expect(page).to_have_url(spark.url)
    assert not [r for r, _ in sent if r == "research_submit"]
    assert not page.problems, page.problems


def test_claim_verdict_confidence_and_read_aloud(spark, new_page):
    page = new_page(bypass_csp=True)  # only so axe can be injected
    page.add_init_script(SPEECH)
    page.goto(spark.url)
    pair(page)
    ask(page, CLAIM)
    spark.work()
    page.locator("#refresh").click()
    expect(page.locator("#researchlist")).to_contain_text("Completed")
    page.get_by_role("button", name="Open: " + CLAIM).click()
    expect(page.locator(".rverdict")).to_have_text(re.compile(r"^Supported · \d{1,3}% confidence$"))
    expect(page.locator(".rclaim")).to_contain_text("Claim checked: the Zephyr compiler version 4.2.0")
    expect(page.locator("#researchview blockquote .rconf").first).to_have_text(
        re.compile(r"^(Corroborated|Single source|Contested)( · \d{1,3}% confidence)?$"))
    quotes = page.locator("#researchview blockquote").count()
    assert quotes >= 1
    # Read aloud: quoted sentences and "Source n" only, with the on-device voice; never the remote one.
    page.get_by_role("button", name="Read aloud").click()
    expect(page.locator("#readaloud")).to_have_text("Stop reading")
    expect(page.locator("#researchmsg")).to_contain_text("on-device voice")
    expect(page.locator("#readaloud")).to_have_text("Read aloud", timeout=15_000)
    spoken = page.evaluate("window.spoken")
    assert len(spoken) == quotes and {s["voice"] for s in spoken} == {"Local"}
    assert all("4.2.0" in s["text"] and re.search(r"Source \d+\.$", s["text"]) for s in spoken), spoken
    assert not any("http" in s["text"] or ".example" in s["text"] for s in spoken)
    SHOTS.mkdir(exist_ok=True)
    page.locator("#researchview").screenshot(path=str(SHOTS / "pocket-research-verdict.png"))
    page.evaluate("window.voices = [window.voices[0]]")   # only a network voice left
    page.get_by_role("button", name="Read aloud").click()
    expect(page.locator("#researchmsg")).to_have_text("No on-device voice is available, so nothing was read aloud.")
    assert len(page.evaluate("window.spoken")) == quotes
    page.add_script_tag(path=str(AXE))
    found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")
    assert [v for v in found if v["impact"] in ("serious", "critical")] == []


NOTIFY = """(() => {
    window.shown = []; window.asked = 0;
    ServiceWorkerRegistration.prototype.showNotification = function (title, options) {
        window.shown.push({title, body: options.body, tag: options.tag}); return Promise.resolve(); };
    const focus = Document.prototype.hasFocus;
    Document.prototype.hasFocus = function () { return window.unfocused ? false : focus.call(this); };
    const ask = Notification.requestPermission;
    Notification.requestPermission = function () { window.asked++; return window.deny ? Promise.resolve('denied')
        : ask.call(Notification); }; })()"""


def test_notifications_are_opt_in_and_only_from_the_open_page(spark, new_page):
    page = new_page()
    page.context.grant_permissions(["notifications"], origin=spark.url.rstrip("/"))
    page.add_init_script(NOTIFY)
    page.goto(spark.url)
    expect(page.locator("#offlinestate")).to_have_text("Offline shell ready")
    pair(page)
    expect(page.locator("#notifyswitch")).not_to_be_checked()   # off by default
    page.locator("#notifyswitch").check()
    expect(page.locator("#researchmsg")).to_have_text("Notifications on for this visit while Pocket stays open.")
    ask(page)
    page.evaluate("window.unfocused = true")   # Pocket visible beside another window
    spark.work()
    deadline = time.monotonic() + 15   # wait_for_function would need eval, which the app CSP forbids
    while not page.evaluate("window.shown.length") and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    shown = page.evaluate("window.shown")
    assert len(shown) == 1 and shown[0]["title"] == "Pocket research finished"
    assert shown[0]["body"] == "Completed. Open Pocket to read it.", "no question text on a lock screen"
    assert page.evaluate("window.asked") == 0, "already granted: no prompt"
    expect(page.locator(".rstage")).to_have_text("Completed")
    # Denied: the switch stays off and explains; the prompt is asked only from the switch.
    other = new_page()
    other.add_init_script(NOTIFY + """;window.deny = true;   // not yet decided; the owner declines the prompt
        Object.defineProperty(Notification, 'permission', {configurable: true, get: () => 'default'});""")
    spark.bridge.invite("other-code")
    other.goto(spark.url)
    pair(other, "other-code")
    assert other.evaluate("window.asked") == 0, "never prompted on load or pairing"
    other.locator("#notifyswitch").click()
    expect(other.locator("#researchmsg")).to_have_text("Notifications are blocked for this site in your browser settings.")
    expect(other.locator("#notifyswitch")).not_to_be_checked()
    assert other.evaluate("window.asked") == 1
    assert not page.problems and not other.problems


def test_finished_while_away_summary(spark, new_page):
    page = new_page()
    page.goto(spark.url)
    pair(page)
    ask(page)
    page.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:true});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    spark.work()
    page.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:false});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    expect(page.locator("#researchmsg")).to_have_text(f"Finished while you were away: “{QUESTION}” (Completed).")
    expect(page.locator("#researchlist")).to_contain_text("Completed")
    assert not page.problems, page.problems
