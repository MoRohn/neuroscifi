"""The optional browser run of an explorable document (DESIGN §6, ADR-074 lane G2). It imports nothing from `make`.

Only for documents the system made itself (trusted bytes). Playwright's Chromium is an optional extra: imported
lazily, and when it is missing the verdict says "not run in a browser" and names what is missing. The document is
loaded with `set_content` into a context that is offline and refuses every request (aborted, routed nowhere); the
page runs under the document's own Content-Security-Policy.

Technical findings: `browser_loaded` (the runtime mounted), `no_requests` (the page asked for nothing),
`console_clean` (no error, no uncaught exception, no policy violation), `keyboard_reachable` (every slider and button
is reached by Tab), and by form: `values_follow_parameters` (a model: each slider moved by the keyboard to its
largest value and back to its smallest; every value the runtime shows in its tables, including the plotted values,
equals what artifacts/expr.py computes for the slider positions read from the page, within 1e-9), `steps_move` (a
trace: Next and Last move the step, the status and the marked line), `solid_turns` (a solid: the arrow keys turn it and
the canvas is drawn), and `axe` when the caller passes axe-core's source (no violations in the mounted view).
The caller may ask for a screenshot.
"""
from __future__ import annotations

import importlib.util
import math

from .. import expr
from . import Finding, Verdict

_COLLECT = """
window.__nsfViolations = [];
document.addEventListener('securitypolicyviolation', (e) => window.__nsfViolations.push(e.violatedDirective));
"""

_TABLES = """() => {
  const out = [];
  for (const td of document.querySelectorAll('#nsf-xp-root td[data-name][data-value]')) {
    const table = td.closest('table');
    const row = td.closest('tr');
    out.push({name: td.getAttribute('data-name'), value: td.getAttribute('data-value'),
              values: table.classList.contains('nsf-xp-values'), index: row.getAttribute('data-index'),
              x: row.getAttribute('data-x'), kind: table.getAttribute('data-x-kind'),
              plot: [...document.querySelectorAll('#nsf-xp-root table.nsf-xp-values')].indexOf(table)});
  }
  return out;
}"""


def available() -> tuple[bool, str]:
    if importlib.util.find_spec("playwright") is None:
        return False, "needs the optional playwright package and its Chromium (not installed)"
    return True, "ready"


def _number(text: str) -> float:
    return float({"NaN": "nan", "Infinity": "inf", "-Infinity": "-inf"}.get(text, text))


def _close(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _compare_model(page, spec_value: dict) -> tuple[int, int]:
    """(cells that agree, cells read) for the page's current slider positions."""
    data = spec_value["data"]
    positions = page.eval_on_selector_all("#nsf-xp-root input[type=range]", "els => els.map(e => Number(e.value))")
    values = {p["name"]: v for p, v in zip(data["parameters"], positions, strict=True)}
    want = expr.evaluate_model(data, values)
    sweeps = iter(want["sweeps"])
    plot_sources = []
    for view in data["views"]:
        if view["kind"] == "plot":
            plot_sources.append(("time", None) if view["x"] == "time" else ("sweep", next(sweeps)))
    agree = total = 0
    for cell in page.evaluate(_TABLES):
        total += 1
        got = _number(cell["value"])
        name = cell["name"]
        if not cell["values"]:
            expected = want["values"].get(name)
        else:
            k = int(cell["index"])
            source = plot_sources[cell["plot"]]
            if source[0] == "time":
                series = want["series"].get(name)
                expected = series[k] if series is not None else want["values"].get(name)
                if not _close(_number(cell["x"]), want["times"][k]):
                    continue
            else:
                expected = source[1][name][k]
                if not _close(_number(cell["x"]), source[1]["x"][k]):
                    continue
        if expected is not None and _close(got, float(expected)):
            agree += 1
    return agree, total


def run(data: bytes, spec=None, *, browser=None, color_scheme: str = "light", width: int = 1100,
        axe_source: str | None = None, screenshot: str | None = None, timeout_s: float = 30.0) -> Verdict:
    """Load the document in Chromium and judge it. `browser`: a Playwright browser the caller already runs (an e2e
    session); without it one is started here and closed again."""
    if browser is None:
        ok, reason = available()
        if not ok:
            return Verdict.of((), [Finding("browser_run", True, f"not run in a browser: {reason}")])
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as pw:
                own = pw.chromium.launch(channel="chromium")
                try:
                    return _run(own, data, spec, color_scheme, width, axe_source, screenshot, timeout_s)
                finally:
                    own.close()
        except PlaywrightError as error:
            return Verdict.of((), [Finding("browser_run", True, "not run in a browser: Chromium could not start "
                                                                f"({type(error).__name__})")])
    return _run(browser, data, spec, color_scheme, width, axe_source, screenshot, timeout_s)


def _run(browser, data, spec, color_scheme, width, axe_source, screenshot, timeout_s) -> Verdict:
    from playwright.sync_api import Error as PlaywrightError

    html = data.decode("utf-8")
    try:
        spec_value = spec.to_dict() if hasattr(spec, "to_dict") else (spec or None)
    except (TypeError, ValueError):
        spec_value = None
    technical: list[Finding] = []
    style: list[Finding] = []
    requests: list[str] = []
    errors: list[str] = []
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=color_scheme,
                                  offline=True, service_workers="block")
    try:
        context.route("**/*", lambda route: (requests.append(route.request.resource_type), route.abort()))
        page = context.new_page()
        page.set_default_timeout(timeout_s * 1000)
        page.add_init_script(_COLLECT)
        page.on("console", lambda m: errors.append("console " + m.type) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("uncaught " + type(e).__name__))
        page.set_content(html, wait_until="load")
        try:
            page.wait_for_selector("#nsf-xp-root > .nsf-xp", timeout=timeout_s * 1000)
            mounted = page.locator("#nsf-xp-root .nsf-xp-error").count() == 0
        except PlaywrightError:
            mounted = False
        technical.append(Finding("browser_loaded", mounted, "the runtime mounted the view" if mounted else
                                 "the runtime did not mount, or showed an error"))
        if mounted:
            _interact(page, spec_value, technical)
            if axe_source:
                page.evaluate(axe_source)
                result = page.evaluate("async () => { const r = await axe.run("
                                       "document.getElementById('nsf-xp-root')); "
                                       "return r.violations.map(v => v.id); }")
                technical.append(Finding("axe", not result, "axe-core finds no violation" if not result
                                         else "axe-core violations: " + ", ".join(result)))
            if screenshot:
                page.screenshot(path=screenshot, full_page=True)
        errors += ["policy " + v for v in page.evaluate("window.__nsfViolations || []")]
    finally:
        context.close()
    technical.append(Finding("no_requests", not requests, "the page asked for nothing" if not requests else
                             f"{len(requests)} request(s) refused: " + ", ".join(sorted(set(requests)))))
    technical.append(Finding("console_clean", not errors, "no error, exception or policy violation" if not errors
                             else "; ".join(sorted(set(errors))[:4])))
    style.append(Finding("browser_run", True, f"run in Chromium ({color_scheme}, {width} pixels wide)"))
    return Verdict.of(technical, style)


def _interact(page, spec_value, technical) -> None:
    controls = page.eval_on_selector_all("#nsf-xp-root input, #nsf-xp-root button, #nsf-xp-root canvas, "
                                         "#nsf-xp-root summary", "els => els.filter(e => !e.disabled).length")
    reached = set()
    page.locator("body").focus()
    for _ in range(controls * 2 + 10):
        page.keyboard.press("Tab")
        key = page.evaluate("() => { const e = document.activeElement; if (!e || !e.closest('#nsf-xp-root')) "
                            "return null; const all = [...document.querySelectorAll('#nsf-xp-root input, "
                            "#nsf-xp-root button, #nsf-xp-root canvas, #nsf-xp-root summary')]; "
                            "return all.indexOf(e); }")
        if key is not None and key >= 0:
            reached.add(key)
        if len(reached) >= controls:
            break
    technical.append(Finding("keyboard_reachable", len(reached) >= controls,
                             f"{len(reached)} of {controls} controls reached with Tab"))
    form = (spec_value or {}).get("spec")
    if form == "explorable.model":
        page.evaluate("() => document.querySelectorAll('#nsf-xp-root details').forEach(d => d.open = true)")
        agree, total = _compare_model(page, spec_value)
        moved_ok = total > 0 and agree == total
        sliders = page.locator("#nsf-xp-root input[type=range]")
        for k in range(sliders.count()):
            for key in ("End", "Home"):
                sliders.nth(k).focus()
                page.keyboard.press(key)
                page.wait_for_timeout(60)
                a, t = _compare_model(page, spec_value)
                agree, total = agree + a, total + t
                moved_ok = moved_ok and t > 0 and a == t
        technical.append(Finding("values_follow_parameters", moved_ok,
                                 f"{agree} of {total} shown values equal the model's for the slider positions "
                                 "(each slider moved to both ends with the keyboard)",
                                 measure=(agree / total) if total else 0.0))
    elif form == "explorable.steps":
        steps = len(spec_value["data"]["steps"])
        status = page.locator("#nsf-xp-root .nsf-xp-note")
        before = status.text_content()
        page.get_by_role("button", name="Next step").focus()
        page.keyboard.press("Enter")
        after = status.text_content()
        page.get_by_role("button", name="Last step").focus()
        page.keyboard.press("Enter")
        last = status.text_content()
        line = spec_value["data"]["steps"][-1].get("line")
        current = page.locator("#nsf-xp-root .nsf-xp-code li[aria-current=step]").count()
        moved = (before.startswith(f"Step 1 of {steps}") and (steps == 1 or after.startswith(f"Step 2 of {steps}"))
                 and last.startswith(f"Step {steps} of {steps}") and current == (0 if line is None else 1))
        technical.append(Finding("steps_move", moved, "Next and Last move the step, its words and its line"
                                 if moved else "the step controls do not move the trace as they say"))
    elif form == "solid.view":
        canvas = page.locator("#nsf-xp-root canvas")
        before = canvas.get_attribute("aria-label")
        canvas.focus()
        page.keyboard.press("ArrowLeft")
        after = canvas.get_attribute("aria-label")
        inked = page.evaluate("() => { const c = document.querySelector('#nsf-xp-root canvas'); const g = "
                              "c.getContext('2d'); const d = g.getImageData(0, 0, c.width, c.height).data; "
                              "let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] !== d[0]) n++; return n; }")
        turned = before != after and inked > 100
        technical.append(Finding("solid_turns", turned, "the arrow keys turn the solid and the canvas is drawn"
                                 if turned else "the solid does not turn or is not drawn"))


def check_in_browser(data: bytes, spec=None, **options) -> Verdict:
    """The static check and the browser run together (the static findings decide when no browser is here)."""
    from .explorable import check_explorable
    static = check_explorable(data, "", spec)
    live = run(data, spec, **options)
    return Verdict.of(static.technical + live.technical, static.style + live.style)


__all__ = ["available", "check_in_browser", "run"]

