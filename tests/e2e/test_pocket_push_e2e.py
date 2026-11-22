"""Pocket Web Push (ADR-040) in a real Chromium: the opt-in switch, its explanation and permission state, the test
push, an empty push when a question finishes, the service worker's generic notification, opting out and unpairing,
and the hint where push is unsupported.

Playwright's browser contexts are incognito, where Chromium offers no Push API, so PushManager is replaced by a stub
that returns a subscription on a local fake push service (allowed through Push(test_origins=...), a test-only
override). The push service's delivery to the browser is then simulated with CDP ServiceWorker.deliverPushMessage,
which runs the shipped service worker's real 'push' handler."""
import base64
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest
from playwright.sync_api import expect

from conftest import AXE, SHOTS, pair
from neuroscifi.pocket import server as bridge
from test_pocket_research_e2e import QUESTION, Spark, ask
from test_pocket_uat import unlock

push = pytest.importorskip("neuroscifi.pocket.push", reason="needs the crypto extra")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/18.1 Mobile/15E148 Safari/604.1")
STUB = """(() => {
    window.pushCalls = [];
    let current = null;
    PushManager.prototype.subscribe = function (options) {
        window.pushCalls.push({subscribe: Array.from(new Uint8Array(options.applicationServerKey)),
                               userVisibleOnly: options.userVisibleOnly});
        current = {endpoint: ENDPOINT, options: {applicationServerKey: options.applicationServerKey, userVisibleOnly: true},
                   unsubscribe() { window.pushCalls.push({unsubscribe: true}); sessionStorage.setItem('unsubscribed', '1');
                                   current = null; return Promise.resolve(true); }};
        return Promise.resolve(current);
    };
    PushManager.prototype.getSubscription = function () { return Promise.resolve(current); };
})()"""


class FakePushService:
    def __init__(self):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def pushing(tmp_path):
    """The research Spark with Web Push on, its dispatcher running, and a local fake push service."""
    spark, fake, stop = Spark(tmp_path, delay=0.0), FakePushService(), threading.Event()
    server = spark.bridge.server
    server.push = push.Push(server, push.load_key(tmp_path / "keys" / "vapid.pem"), interval=0, poll=0.2,
                            test_origins={fake.origin})
    threading.Thread(target=server.push.run, args=(stop,), daemon=True).start()
    spark.fake = fake
    sender = server.push
    yield spark
    stop.set()
    sender.wake.set()
    fake.close()
    spark.close()


def stored(spark):
    with closing(bridge.connect(spark.bridge.server.store.path)) as db:
        return [r[0] for r in db.execute("SELECT endpoint FROM push_subs")]


def wait(page, check, seconds=10):
    deadline = time.monotonic() + seconds   # wait_for_function would need eval, which the app CSP forbids
    while not check() and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert check()


def deliver(page, spark):
    """What the browser's push service does on a push: CDP runs the service worker's real 'push' event (no data)."""
    cdp = page.context.new_cdp_session(page)
    registrations = []
    cdp.on("ServiceWorker.workerRegistrationUpdated", lambda e: registrations.extend(e["registrations"]))
    cdp.send("ServiceWorker.enable")
    wait(page, lambda: registrations)
    origin = spark.url.rstrip("/")
    cdp.send("ServiceWorker.deliverPushMessage", {"origin": origin, "registrationId":
                                                  registrations[0]["registrationId"], "data": ""})


def record_notifications(page):
    """Inside the running service worker, record each showNotification call (and still show it). The page's
    getNotifications() proved unreliable in headless Chromium (empty in 2 of 3 runs), so the worker is asked."""
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    worker = next(w for w in page.context.service_workers if w.url.endswith("/sw.js"))
    worker.evaluate("""() => { self.shown = []; const show = self.registration.showNotification.bind(self.registration);
        self.registration.showNotification = (title, options) => { self.shown.push({title, ...options}); return show(title, options); }; }""")
    return worker


def test_push_opt_in_test_push_finish_and_opt_out(pushing, new_page):
    spark, fake = pushing, pushing.fake
    endpoint = fake.origin + "/push/phone-1"
    page = new_page()
    page.context.grant_permissions(["notifications"], origin=spark.url.rstrip("/"))
    page.add_init_script(STUB.replace("ENDPOINT", json.dumps(endpoint)))
    page.goto(spark.url)
    expect(page.locator("#offlinestate")).to_have_text("Offline shell ready")
    pair(page)
    expect(page.locator("#pushbox")).to_be_visible()
    expect(page.locator("#pushhelp")).to_contain_text("Uses your browser vendor's push service; no question text is sent.")
    expect(page.locator("#pushhint")).to_be_hidden()
    expect(page.locator("#pushswitch")).not_to_be_checked()   # off by default
    expect(page.locator("#pushstate")).to_have_text("Browser permission: allowed · not subscribed")
    expect(page.locator("#pushtest")).to_be_disabled()
    assert stored(spark) == [] and page.evaluate("window.pushCalls") == []

    page.locator("#pushswitch").check()
    expect(page.locator("#researchmsg")).to_have_text("Push on for this device. Your Spark sends an empty push when a "
                                                      "question finishes; the question never leaves your Spark.")
    expect(page.locator("#pushstate")).to_have_text("Browser permission: allowed · this device is subscribed on your Spark")
    [call] = page.evaluate("window.pushCalls")
    key = spark.bridge.server.push.public
    assert bytes(call["subscribe"]) == base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)), "the Spark's VAPID key"
    assert call["userVisibleOnly"] is True
    assert stored(spark) == [endpoint]

    page.locator("#pushtest").click()
    expect(page.locator("#researchmsg")).to_have_text("Test push requested. A notification saying “Your Spark finished "
                                                      "a research task” should appear within a few seconds.")
    wait(page, lambda: len(fake.requests) == 1)
    sent = fake.requests[0]
    assert sent["path"] == "/push/phone-1" and sent["body"] == b""
    assert sent["headers"]["TTL"] == "43200" and sent["headers"]["Urgency"] == "normal"
    assert sent["headers"]["Authorization"].startswith("vapid t=") and sent["headers"]["Authorization"].endswith(", k=" + key)
    worker = record_notifications(page)
    deliver(page, spark)   # the push service hands it to the browser
    expect(page.locator("#researchmsg")).to_have_text("A push from your Spark arrived on this device.")
    wait(page, lambda: worker.evaluate("self.shown.length"))
    assert worker.evaluate("self.shown") == [{"title": "NeuroSciFi Pocket", "body": "Your Spark finished a research task.",
                                              "tag": "pocket-push", "icon": "/icon.svg"}]

    ask(page)
    spark.work()
    wait(page, lambda: len(fake.requests) == 2, 20)
    assert fake.requests[1]["body"] == b"" and fake.requests[1]["path"] == "/push/phone-1"
    assert all(QUESTION not in json.dumps(r["headers"]) + r["path"] for r in fake.requests), "no question text"
    expect(page.locator(".rstage")).to_have_text("Completed")

    page.locator("#pushswitch").uncheck()
    expect(page.locator("#researchmsg")).to_have_text("Push off. Your Spark no longer sends to this device, and this "
                                                      "browser dropped its push subscription.")
    assert stored(spark) == [] and page.evaluate("window.pushCalls.at(-1)") == {"unsubscribe": True}
    expect(page.locator("#pushtest")).to_be_disabled()
    assert not page.problems, page.problems


def test_unpairing_drops_the_subscription_on_the_spark_and_in_the_browser(pushing, new_page):
    spark = pushing
    page = new_page()
    page.context.grant_permissions(["notifications"], origin=spark.url.rstrip("/"))
    page.add_init_script(STUB.replace("ENDPOINT", json.dumps(spark.fake.origin + "/push/phone-2")))
    page.goto(spark.url)
    pair(page)
    page.locator("#pushswitch").check()
    expect(page.locator("#pushstate")).to_contain_text("subscribed on your Spark")
    assert len(stored(spark)) == 1
    page.locator("#unpair").click()   # Forget this pairing and its records (the dialog is accepted)
    expect(page.locator("#linkstate")).to_have_text("Phone only")
    assert stored(spark) == []
    assert page.evaluate("sessionStorage.getItem('unsubscribed')") == "1"
    assert not page.problems, page.problems


def hide(page, hidden):
    page.evaluate("""hidden => { Object.defineProperty(document, 'hidden', {configurable: true, value: hidden});
        document.dispatchEvent(new Event('visibilitychange')); }""", hidden)


def test_leaving_pocket_keeps_push_and_revocation_drops_it(pushing, new_page):
    """Leaving Pocket locks the vault, which unpairs the tab (POC-07): the push subscription must survive that, since
    a closed Pocket is what push is for. A pairing the Spark erased drops it in the browser too."""
    spark = pushing
    page = new_page()
    page.context.grant_permissions(["notifications"], origin=spark.url.rstrip("/"))
    page.add_init_script(STUB.replace("ENDPOINT", json.dumps(spark.fake.origin + "/push/phone-3")))
    page.goto(spark.url)
    pair(page)
    unlock(page)
    page.locator("#rememberpair").click()
    expect(page.locator("#fieldmsg")).to_have_text("Pairing protected by this vault for 24 hours.")
    page.locator("#pushswitch").check()
    expect(page.locator("#pushstate")).to_contain_text("subscribed on your Spark")
    hide(page, True)    # the vault locks and the tab forgets the pairing
    expect(page.locator("#linkstate")).to_have_text("Phone only")
    assert {"unsubscribe": True} not in page.evaluate("window.pushCalls") and len(stored(spark)) == 1
    hide(page, False)
    unlock(page)        # the pairing comes back from the vault, and so does the switch
    expect(page.locator("#linkstate")).to_have_text("Paired with Spark")
    expect(page.locator("#pushstate")).to_have_text("Browser permission: allowed · this device is subscribed on your Spark")
    expect(page.locator("#pushswitch")).to_be_checked()
    with closing(bridge.connect(spark.bridge.server.store.path)) as db:
        [label] = [r[0] for r in db.execute("SELECT label FROM devices")]
    assert spark.bridge.server.store.erase(label) == 1   # nsf pocket erase-pairing LABEL
    assert stored(spark) == []
    page.evaluate("window.pocketHistory().catch(() => {})")   # the next request (or a presence poll) gets 403
    expect(page.locator("#linkstate")).to_have_text("Not paired · pair again")
    wait(page, lambda: {"unsubscribe": True} in page.evaluate("window.pushCalls"))


def test_without_push_support_pocket_still_works_and_explains(pushing, new_page):
    spark = pushing
    phone = new_page(user_agent=IPHONE)
    phone.add_init_script("delete window.PushManager;")   # iOS Safari outside an installed home-screen app
    phone.goto(spark.url)
    pair(phone)
    expect(phone.locator("#pushhint")).to_have_text(
        "Notifications while Pocket is closed: on iPhone and iPad, add Pocket to your Home Screen (Share, then Add to "
        "Home Screen) and open it from there (iOS 16.4 or later). Everything else works here as it is.")
    expect(phone.locator("#pushbox")).to_be_hidden()
    ask(phone)   # research is unaffected
    desktop = new_page(1280, 800)
    desktop.add_init_script("delete window.PushManager;")
    spark.bridge.invite("desk-code")
    desktop.goto(spark.url)
    pair(desktop, "desk-code")
    expect(desktop.locator("#pushhint")).to_have_text("This browser cannot receive push notifications. Everything else "
                                                      "in Pocket works; the switch above notifies you while Pocket is open.")
    spark.bridge.server.push = None   # a Spark started without --push-key offers nothing (and is never asked)
    other = new_page()
    spark.bridge.invite("other-code")
    other.goto(spark.url)
    pair(other, "other-code")
    expect(other.locator("#researchform")).to_be_visible()
    expect(other.locator("#pushbox")).to_be_hidden()
    expect(other.locator("#pushhint")).to_be_hidden()
    assert not phone.problems and not desktop.problems and not other.problems


@pytest.mark.parametrize("size", [(390, 844), (820, 1180)])
def test_push_controls_pass_axe(pushing, new_page, size):
    spark = pushing
    page = new_page(*size, bypass_csp=True)  # only so axe can be injected; the app CSP holds in the tests above
    page.context.grant_permissions(["notifications"], origin=spark.url.rstrip("/"))
    page.add_init_script(STUB.replace("ENDPOINT", json.dumps(spark.fake.origin + "/push/axe")))
    page.goto(spark.url)
    pair(page)
    page.locator("#pushswitch").check()
    expect(page.locator("#pushstate")).to_contain_text("subscribed on your Spark")
    page.locator("#researchtasks").scroll_into_view_if_needed()
    page.locator("#researchtasks").screenshot(path=str(SHOTS / f"pocket-push-{size[0]}.png"))
    page.add_script_tag(path=str(AXE))
    found = page.evaluate("""axe.run(document,{resultTypes:['violations']}).then(r =>
        r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target.join(' '))})))""")
    assert [v for v in found if v["impact"] in ("serious", "critical")] == []


KEPT = """(() => {   // a subscription this browser kept from an earlier pairing (it survives a reload, like a real one)
    window.pushCalls = [];
    const sub = () => sessionStorage.getItem('kept') === '1' ? {endpoint: 'https://push.example/kept',
        unsubscribe() { window.pushCalls.push({unsubscribe: true}); sessionStorage.removeItem('kept');
                        return Promise.resolve(true); }} : null;
    PushManager.prototype.getSubscription = function () { return Promise.resolve(sub()); };
})()"""


def test_a_device_turns_push_off_without_the_vault_or_a_pairing(pushing, new_page):
    """R6 setup (g): reopened without the vault, Pocket is "Phone only" and showed no push switch, so the device had
    to pair again to opt out. The Your Spark card now offers "Turn off push on this device" whenever this browser
    holds a push subscription and is not paired; the Spark drops its copy at its next push (404/410)."""
    page = new_page()
    page.add_init_script(KEPT)
    page.goto(pushing.url)
    expect(page.locator("#pushoptout")).to_be_hidden()                  # no subscription: nothing to offer
    page.evaluate("sessionStorage.setItem('kept', '1')")
    page.reload()                                                         # reopened: not paired, no vault
    expect(page.locator("#pushoptout")).to_be_visible()
    page.get_by_role("button", name="Turn off push on this device").click()
    expect(page.locator("#pushoptoutmsg")).to_contain_text("Push is off on this device.")
    expect(page.locator("#pushoptout")).to_be_hidden()
    assert page.evaluate("window.pushCalls") == [{"unsubscribe": True}]
    page.evaluate("sessionStorage.setItem('kept', '1')")
    page.reload()
    pair(page)                                                            # paired: the push switch is the control
    expect(page.locator("#pushoptout")).to_be_hidden()
    assert not page.problems

