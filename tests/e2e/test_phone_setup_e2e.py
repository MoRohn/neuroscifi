"""ADR-043 in a real Chromium: the console's Add a phone, the phone setup page and pairing from the QR code's URL.

The Spark side is real: a Pocket bridge with a private CA and a server certificate (pocket/tls.py), its plain-HTTP
setup page and its control socket, and an owner console pointed at the bridge's database. The "phone" is a second
browser context with an iPhone or Android user agent. Whether it trusts the Spark is emulated with Playwright's
ignore_https_errors (a real phone trusts the CA after installing it, which no browser automation can do); the QR
codes are not scanned with a camera: the test opens the URLs they encode, read from the console's API.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import socket
import threading
import time

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS
from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store
from neuroscifi.pocket import server as bridge, tls

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile "
           "Safari/537.36")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Spark:
    def __init__(self, folder: Path, monkeypatch):
        run = Path(f"/tmp/nsf-e2e-{os.getpid()}-{time.monotonic_ns() % 10**6}")
        run.mkdir(mode=0o700)
        self.run = run
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
        out = tls.init(folder / "tls", ["spark.local", "127.0.0.1"], ca_dir=folder / "ca")
        ca = folder / "neuroscifi-ca.crt"
        tls.export_ca(ca, ca_dir=folder / "ca", der=True)
        self.db = folder / "ind.sqlite"
        with closing(store.create(self.db, time.time())):
            pass
        self.pocket_db = folder / "ind.pocket.sqlite"
        self.port, self.setup_port = free_port(), free_port()
        argv = ["--db", str(self.pocket_db), "--bind", "127.0.0.1", "--port", str(self.port), "--cert",
                out["files"]["fullchain"], "--key", out["files"]["privkey"], "--setup-port", str(self.setup_port),
                "--ca-file", str(ca)]
        threading.Thread(target=bridge.main, args=(argv,), daemon=True).start()
        deadline = time.monotonic() + 15
        while True:
            try:
                bridge.invitation_status(self.pocket_db, "0" * 16)
                break
            except OSError:
                assert time.monotonic() < deadline, "the bridge did not start"
                time.sleep(0.05)
        self.console = ConsoleServer(self.db, 0, pocket_db=self.pocket_db)
        threading.Thread(target=self.console.serve_forever, daemon=True).start()

    def close(self):
        self.console.shutdown()
        self.console.server_close()
        for f in self.run.rglob("*"):
            if not f.is_dir():
                f.unlink()
        for d in sorted(self.run.rglob("*"), reverse=True):
            d.rmdir()
        self.run.rmdir()


@pytest.fixture
def spark(tmp_path, monkeypatch):
    s = Spark(tmp_path, monkeypatch)
    yield s
    s.close()


def phone(new_page, agent, trusted, **options):
    return new_page(390, 844, user_agent=agent, is_mobile=True, has_touch=True, ignore_https_errors=trusted, **options)


# Emulated trust (ignore_https_errors) covers page loads and fetches, but Chromium still refuses to register a service
# worker from a certificate it does not itself trust. A phone that installed the CA trusts it, so Pocket's offline
# shell registers there; here that one message is expected and nothing else may appear.
SW_UNTRUSTED = {"An unknown error occurred when fetching the script.",
                "An SSL certificate error occurred when fetching the script."}


def problems(page):
    return [p for p in page.problems if p not in SW_UNTRUSTED]


def test_add_a_phone_from_the_console_to_a_paired_phone(spark, new_page):
    desk = new_page(1280, 900)
    desk.goto(spark.console.url)
    desk.locator("#nav a[data-view=phones]").click()
    expect(desk.locator("#h-phones")).to_have_text("Add a phone or tablet")
    expect(desk.locator("#qrSetup svg.qr")).to_have_count(1)
    expect(desk.locator("#qrPair svg.qr")).to_have_count(1)
    expect(desk.locator("#phoneStatus")).to_have_text("Waiting for your phone…")
    for slot in ("#qrSetup", "#qrPair"):
        box = desk.locator(f"{slot} svg.qr").bounding_box()
        assert box["width"] >= 200 and abs(box["width"] - box["height"]) < 1, box
        fill = desk.evaluate(f"getComputedStyle(document.querySelector('{slot} .qr-fg')).fill")
        assert fill == "rgb(0, 0, 0)"
    SHOTS.mkdir(exist_ok=True)
    desk.screenshot(path=str(SHOTS / "phones-console.png"), full_page=True)
    desk.locator("details.manual summary").first.click()
    setup_url = desk.locator("#setupUrl").inner_text()
    app_url = desk.locator("#appUrl").inner_text()
    assert re.fullmatch(rf"http://[^/]+:{spark.setup_port}/", setup_url) and app_url.endswith(f":{spark.port}/")
    expect(desk.locator("#caPrint")).to_have_text(re.compile(r"^([0-9A-F]{4} ){15}[0-9A-F]{4}$"))
    assert desk.locator("#caCheck").text_content() == desk.locator("#caPrint").text_content()
    # a phone that trusts the Spark scans code 2: the Pocket app URL with the code in its fragment (what the code
    # encodes; a camera would read it from the image)
    app = app_url.replace("spark.local", "127.0.0.1")
    page = phone(new_page, IPHONE, trusted=True)
    page.goto(app + "#pair=" + desk.locator("#pairCode").inner_text().replace(" ", ""))
    assert "#" not in page.url, "the code is removed from the address bar at once"
    expect(page.locator("#pairhelp")).to_have_text("This device was invited to pair with your Spark. Tap Pair to finish.")
    expect(page.locator("#spark")).to_have_class(re.compile("invited"))
    expect(page.locator("#pairnudge")).to_be_hidden()
    page.screenshot(path=str(SHOTS / "phones-pocket-invited.png"))
    page.locator("#pairform button").click()
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")
    expect(desk.locator("#phoneDone")).to_be_visible(timeout=10_000)       # the console notices by itself
    desk.screenshot(path=str(SHOTS / "phones-console-paired.png"))
    expect(desk.locator("#phoneStatus")).to_have_text("Paired.")
    expect(desk.locator("#phoneCodes")).to_be_hidden()
    # another phone, without a camera: the code typed by hand, in its groups of four
    desk.locator("#phoneAnother").click()
    expect(desk.locator("#phoneStatus")).to_have_text("Waiting for your phone…")
    code = desk.locator("#pairCode").inner_text()
    assert " " in code
    second = phone(new_page, ANDROID, trusted=True)
    second.goto(app)
    expect(second.locator("#pairnudge")).to_be_visible()                     # unpaired: where pairing starts
    second.locator("#code").fill(code)
    second.locator("#pairform button").click()
    expect(second.locator("#linkstate")).to_have_text("Paired with Spark")
    expect(desk.locator("#phoneDone")).to_be_visible(timeout=10_000)
    expect(second.locator("#pairnudge")).to_be_hidden()
    assert desk.problems == [] and problems(page) == [] and problems(second) == []


def test_the_setup_page_guides_an_iphone_until_it_trusts_the_spark(spark, new_page):
    bridge.request_invitation(spark.pocket_db)                # what the console's Add a phone does
    untrusted = phone(new_page, IPHONE, trusted=False)
    untrusted.goto(f"http://127.0.0.1:{spark.setup_port}/")
    expect(untrusted.locator("h1")).to_have_text("Set up Pocket on this phone")
    expect(untrusted.locator("li[data-os=ios]")).to_have_count(2)
    for step in untrusted.locator("li[data-os=ios]").all():
        expect(step).to_be_visible()
    expect(untrusted.locator("li[data-os=android]")).to_be_hidden()
    expect(untrusted.locator("#download")).to_have_attribute("href", "/neuroscifi-ca.crt")
    expect(untrusted.locator("#trust")).to_have_text("Not yet trusted. Follow the steps above, then come back here.")
    assert untrusted.locator("#fingerprint").count() == 0          # unauthenticated page: the console shows it
    SHOTS.mkdir(exist_ok=True)
    untrusted.screenshot(path=str(SHOTS / "phones-setup-iphone.png"), full_page=True)
    with untrusted.expect_download() as download:
        untrusted.locator("#download").click()
    assert download.value.suggested_filename == "neuroscifi-ca.crt"
    assert Path(download.value.path()).read_bytes()[:1] == b"\x30"
    trusted = phone(new_page, ANDROID, trusted=True, bypass_csp=True)   # bypass only so axe can be injected
    trusted.goto(f"http://127.0.0.1:{spark.setup_port}/")
    expect(trusted.locator("li[data-os=android]")).to_be_visible()
    expect(trusted.locator("li[data-os=ios]")).to_have_count(2)
    expect(trusted.locator("#trust")).to_have_text("This phone trusts your Spark.")
    expect(trusted.locator("#next")).to_be_visible()
    expect(trusted.locator("#step-check")).to_have_attribute("data-trusted", "true")
    trusted.screenshot(path=str(SHOTS / "phones-setup-android-trusted.png"), full_page=True)
    trusted.add_script_tag(path=str(AXE))
    result = trusted.evaluate("axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa']})")
    assert result["violations"] == [], json.dumps(result["violations"], indent=1)[:3000]
    assert untrusted.problems in ([], ) or all("Failed to load resource" in p or "ERR_CERT" in p
                                               for p in untrusted.problems)
    assert trusted.problems == []


def test_console_add_a_phone_when_pocket_is_not_running(tmp_path, new_page):
    db = tmp_path / "ind.sqlite"
    with closing(store.create(db, time.time())):
        pass
    console = ConsoleServer(db, 0, pocket_db=tmp_path / "none.sqlite")
    threading.Thread(target=console.serve_forever, daemon=True).start()
    try:
        page = new_page(1280, 900, bypass_csp=True)                    # bypass only so axe can be injected
        page.goto(console.url)
        page.locator("#nav a[data-view=phones]").click()
        expect(page.locator("#phoneStopped")).to_be_visible()
        expect(page.locator("#phoneStopped")).to_contain_text("nsf start")
        expect(page.locator("#phoneCodes")).to_be_hidden()
        page.add_script_tag(path=str(AXE))
        result = page.evaluate("axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa']})")
        assert result["violations"] == [], json.dumps(result["violations"], indent=1)[:3000]
        assert page.problems == []
    finally:
        console.shutdown()
        console.server_close()
