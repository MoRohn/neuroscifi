"""Lane G2 in Chromium: explorable documents run as made (check/browser.py, every request refused, light and dark,
axe-core where installed), and the same runtime imported by a host page under a strict first-party policy
(`script-src 'self'; style-src 'self'`, no inline anything), where mount() and unmount() touch nothing else."""
import json
from pathlib import Path

import pytest

from neuroscifi.artifacts import make
from neuroscifi.artifacts.check import browser as browser_check
from neuroscifi.artifacts.make import _g2  # noqa: F401
from neuroscifi.artifacts.make import explorable

from conftest import AXE

WEB = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "artifacts" / "web"


def _spec(form, data, title):
    return {"spec": form, "version": 1, "title": title, "description": "Made for a browser test.", "data": data,
            "options": {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}}


MODEL = _spec("explorable_model", {
    "parameters": [{"name": "m", "label": "Mass", "min": 0.5, "max": 4, "step": 0.5, "value": 1, "unit": "kg"},
                   {"name": "k", "label": "Stiffness", "min": 1, "max": 20, "step": 1, "value": 10, "unit": "N/m"},
                   {"name": "c", "label": "Damping", "min": 0, "max": 2, "step": 0.1, "value": 0.3}],
    "quantities": [{"name": "omega", "label": "Natural frequency", "expr": "sqrt(k / m)", "unit": "rad/s"},
                   {"name": "zeta", "label": "Damping ratio", "expr": "c / (2 * sqrt(k * m))"},
                   {"name": "energy", "label": "Energy", "expr": "0.5 * m * v^2 + 0.5 * k * x^2", "unit": "J"}],
    "dynamics": {"state": [{"name": "v", "label": "Velocity", "initial": "0",
                            "update": "v + (-(k * x) - c * v) / m * dt", "unit": "m/s"},
                           {"name": "x", "label": "Position", "initial": "0.5", "update": "x + v * dt", "unit": "m"}],
                 "steps": 500, "dt": 0.01, "time_label": "Time", "time_unit": "s"},
    "views": [{"kind": "plot", "title": "Position over time", "x": "time", "y": ["x", "v"]},
              {"kind": "table", "title": "Numbers", "items": ["omega", "zeta", "x", "energy"]},
              {"kind": "bars", "title": "Compared", "items": ["omega", "energy"]},
              {"kind": "plot", "title": "Damping ratio", "x": "c", "y": ["zeta"], "points": 21}]}, "A spring")
STEPS = _spec("explorable_steps", {
    "code": ["lo ← 0; hi ← n − 1", "look at the middle", "found"],
    "arrays": [{"name": "a", "label": "The list"}],
    "steps": [{"note": "Start.", "line": 0, "arrays": [[1, 3, 5, 7, 9]],
               "pointers": [{"name": "lo", "array": 0, "index": 0}, {"name": "hi", "array": 0, "index": 4}]},
              {"note": "The middle is 5.", "line": 1, "arrays": [[1, 3, 5, 7, 9]],
               "marks": [{"array": 0, "index": 2, "kind": "active"}], "vars": [{"name": "mid", "value": 2}]},
              {"note": "Found 5.", "line": 2, "arrays": [[1, 3, 5, 7, 9]],
               "marks": [{"array": 0, "index": 2, "kind": "found"}]}]}, "Three steps")
SOLID = explorable.solid_view_spec(_spec("solid_revolve", {"profile": [[0, 0], [2, 0], [1, 3], [0, 3]]},
                                         "A cone"), title="A cone to turn")
AXE_SOURCE = AXE.read_text() if AXE.exists() else None


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("value", [MODEL, STEPS, SOLID], ids=["model", "steps", "solid"])
def test_documents_run_in_chromium_with_every_request_refused(browser, value, scheme):
    made = make.make(value)
    verdict = browser_check.check_in_browser(made.bytes, made.spec, browser=browser, color_scheme=scheme,
                                             axe_source=AXE_SOURCE)
    failed = [(f.rule, f.detail) for f in verdict.technical if not f.passed]
    assert verdict.ok, failed
    rules = {f.rule for f in verdict.technical}
    assert {"browser_loaded", "no_requests", "console_clean", "keyboard_reachable"} <= rules
    if AXE_SOURCE:
        assert "axe" in rules
    if value is MODEL:
        moved = next(f for f in verdict.technical if f.rule == "values_follow_parameters")
        assert moved.measure == 1.0 and moved.passed


def test_a_phone_width_and_an_invalid_spec(browser):
    made = make.make(MODEL)
    assert browser_check.run(made.bytes, made.spec, browser=browser, width=360).ok
    bad = made.bytes.decode().replace('"steps":500', '"steps":500000').encode()
    verdict = browser_check.run(bad, None, browser=browser)
    loaded = next(f for f in verdict.technical if f.rule == "browser_loaded")
    assert not loaded.passed                                  # the runtime refuses it and says so in words
    assert next(f for f in verdict.technical if f.rule == "console_clean").passed


HOST = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Host</title>
<link rel="stylesheet" href="/explorable.css"></head>
<body><main><h1>A host page</h1><div id="slot"><p id="kept">kept by the host</p></div></main>
<script type="module" src="/host.mjs"></script></body></html>"""
HOST_MODULE = """import { mount } from '/explorable.mjs';
const handle = mount(document.getElementById('slot'), SPEC);
document.getElementById('slot').dataset.mounted = '1';
document.addEventListener('nsf-test-unmount', () => { handle.unmount(); document.body.dataset.unmounted = '1'; });
"""
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; "
          "frame-ancestors 'none'; form-action 'none'")


def test_host_page_mounts_and_unmounts_under_a_strict_first_party_policy(browser):
    spec = make.make(MODEL).spec.to_dict()
    files = {"/": (HOST, "text/html"), "/explorable.mjs": ((WEB / "explorable.mjs").read_text(), "text/javascript"),
             "/explorable.css": ((WEB / "explorable.css").read_text(), "text/css"),
             "/host.mjs": (HOST_MODULE.replace("SPEC", json.dumps(spec)), "text/javascript")}
    other: list[str] = []

    def serve(route):
        url = route.request.url
        path = "/" + url.split("://", 1)[1].split("/", 1)[1] if url.startswith("http://host.test/") else None
        if path in files:
            body, kind = files[path]
            route.fulfill(status=200, body=body, content_type=kind, headers={"Content-Security-Policy": POLICY})
        else:
            other.append(url)
            route.abort()
    context = browser.new_context(viewport={"width": 900, "height": 900})
    try:
        context.route("**/*", serve)
        page = context.new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.add_init_script("window.__v = []; document.addEventListener('securitypolicyviolation', "
                             "e => window.__v.push(e.violatedDirective));")
        page.goto("http://host.test/")
        page.wait_for_selector("#slot[data-mounted='1'] .nsf-xp")
        head_before = page.evaluate("document.head.innerHTML")
        slider = page.locator("#slot input[type=range]").first
        slider.focus()
        page.keyboard.press("End")
        page.wait_for_timeout(700)
        assert "Mass set to 4 kg." in page.locator("#slot .nsf-xp-status").text_content()
        background = page.evaluate("getComputedStyle(document.querySelector('#slot .nsf-xp')).backgroundColor")
        assert background == "rgb(255, 255, 255)"            # the first-party stylesheet applied
        page.evaluate("document.dispatchEvent(new Event('nsf-test-unmount'))")
        page.wait_for_selector("body[data-unmounted='1']")
        assert page.evaluate("document.getElementById('slot').innerHTML") == '<p id="kept">kept by the host</p>'
        assert page.evaluate("document.head.innerHTML") == head_before
        assert page.evaluate("document.querySelectorAll('.nsf-xp, style').length") == 0
        assert page.evaluate("window.__v") == [] and problems == [] and other == []
    finally:
        context.close()
