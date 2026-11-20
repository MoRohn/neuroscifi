"""Optional integration check. Requires Python Playwright and Chromium."""
import argparse
import json
from pathlib import Path
import tempfile
from threading import Thread, Lock
import time
from playwright.sync_api import sync_playwright, expect
from neuroscifi.pocket.server import Store, Handler, ThreadingHTTPServer

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", help="path to a Chromium binary")
    args=parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        server.store=Store(Path(tmp)/"test.sqlite")
        server.pair_lock=Lock()
        server.invitation="test-only"
        server.invitation_deadline=time.monotonic()+60
        Thread(target=server.serve_forever,daemon=True).start()
        browser=pw.chromium.launch(executable_path=args.chromium, args=["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream","--autoplay-policy=no-user-gesture-required"])
        try:
            ctx=browser.new_context(viewport={"width":390,"height":844},permissions=["microphone","camera"])
            page=ctx.new_page()
            errors=[]; payloads=[]
            page.on("pageerror",lambda err:errors.append(str(err)))
            page.on("request", lambda req:payloads.append((req.url.split("/")[-1],req.post_data)) if req.method=="POST" else None)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            expect(page.locator("#save")).to_be_disabled()
            page.locator("#sound").click()
            expect(page.locator("#draft")).to_contain_text("sound ·",timeout=12000)
            expect(page.locator("#stop")).to_be_disabled()
            page.locator("#light").click()
            expect(page.locator("#draft")).to_contain_text("light ·")
            assert not payloads, "Local sensor acquisition contacted the bridge"
            page.locator("#code").fill("test-only")
            page.locator("#pairform button").click()
            expect(page.locator("#linkstate")).to_have_text("Paired with Spark")
            page.locator("#save").click()
            expect(page.locator("#history")).to_contain_text("memory only")
            page.locator('[data-tag="walk"]').click()
            page.locator("#learn").check()
            page.locator("#save").click()
            expect(page.locator("#forecast")).to_contain_text("walk (40%)")
            page.locator("#history li").filter(has_text="walk").get_by_role("button").click()
            expect(page.locator("#forecast")).to_contain_text("No learning")
            # Advance the browser clock with fake microphone input; automatic writes are opt-in.
            page.clock.install()
            page.locator("#session summary").click()
            page.locator("#auto").check()
            page.locator("#listen").click()
            expect(page.locator("#status")).to_contain_text("Listening")
            page.clock.run_for(65000)
            expect(page.locator("#history")).to_contain_text("sound")
            assert page.locator("#auto").is_disabled()
            page.locator("#stop").click()
            expect(page.locator("#auto")).not_to_be_checked()
            n=sum(1 for name,_ in payloads if name=="save")
            page.clock.run_for(65000)
            assert sum(1 for name,_ in payloads if name=="save")==n
            page.locator("#listen").click()
            expect(page.locator("#status")).to_contain_text("Listening")
            page.evaluate("""() => { Object.defineProperty(document,'hidden',{configurable:true,value:true}); document.dispatchEvent(new Event('visibilitychange')); }""")
            expect(page.locator("#stop")).to_be_disabled()
            expect(page.locator("#draft")).to_contain_text("No observation")
            saves=[json.loads(raw) for route,raw in payloads if route=="save"]
            assert len(saves)==3 and all(set(r)=={"id","channel","value","learn"} for r in saves)
            assert saves[-1]["channel"]=="sound" and saves[-1]["learn"] is True
            assert not errors,errors
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            print("Passed: local sound/light, optional learning, pairing, review, deletion, timed sharing, stop, simulated hide, enum-only requests, mobile width.")
        finally:
            browser.close()
            server.shutdown()
            server.server_close()

if __name__=="__main__":
    main()
