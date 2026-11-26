"""Lane G2: explorable documents (make/explorable.py, web/explorable.mjs, web/explorable.css) and their independent
static check (check/explorable.py). The browser run is tested in tests/e2e/test_g2_explorable_e2e.py."""
import base64
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.check import _g2 as _checks  # noqa: F401
from neuroscifi.artifacts.check import explorable as xcheck
from neuroscifi.artifacts.make import _g2  # noqa: F401
from neuroscifi.artifacts.make import explorable
from neuroscifi.artifacts.spec import Spec
from neuroscifi.artifacts.types import Malformed

WEB = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "artifacts" / "web"
MEDIA = "application/vnd.neuroscifi.explorable+html"


def _spec(form, data, title="A model", description="Made for a test."):
    return {"spec": form, "version": 1, "title": title, "description": description, "data": data, "options": {},
            "sources": ["request"], "made_by": {"generator": "", "version": 0}}


SPRING = _spec("explorable.model", {
    "parameters": [{"name": "m", "label": "Mass", "min": 0.1, "max": 5, "step": 0.1, "value": 1, "unit": "kg"},
                   {"name": "k", "label": "Stiffness", "min": 1, "max": 50, "step": 1, "value": 10, "unit": "N/m"},
                   {"name": "c", "label": "Damping", "min": 0, "max": 5, "step": 0.1, "value": 0.4}],
    "quantities": [{"name": "omega", "label": "Natural frequency", "expr": "sqrt(k / m)", "unit": "rad/s"},
                   {"name": "energy", "label": "Energy", "expr": "0.5 * m * v^2 + 0.5 * k * x^2", "unit": "J"}],
    "dynamics": {"state": [{"name": "v", "label": "Velocity", "initial": "0", "update": "v + (-(k * x) - c * v) / m * dt"},
                           {"name": "x", "label": "Position", "initial": "0.5", "update": "x + v * dt"}],
                 "steps": 600, "dt": 0.01, "time_label": "Time", "time_unit": "s"},
    "views": [{"kind": "plot", "title": "Position", "x": "time", "y": ["x"]},
              {"kind": "table", "title": "Numbers", "items": ["omega", "x", "energy"]},
              {"kind": "plot", "title": "Frequency", "x": "k", "y": ["omega"], "points": 20}]})

TRACE = _spec("explorable.steps", {
    "code": ["lo ← 0; hi ← n − 1", "while lo ≤ hi: look at the middle"],
    "arrays": [{"name": "a", "label": "The list"}],
    "steps": [{"note": "Start.", "line": 0, "arrays": [[1, 3, 5, 7]], "pointers": [{"name": "lo", "array": 0, "index": 0}]},
              {"note": "Look at 3.", "line": 1, "arrays": [[1, 3, 5, 7]], "marks": [{"array": 0, "index": 1}],
               "vars": [{"name": "mid", "value": 1}]}]})

BOX = explorable.solid_view_spec(_spec("solid.box", {"size": [1, 2, 3]}), title="A box")


def _failed(verdict):
    return {f.rule for f in verdict.technical if not f.passed}


def _b64(text):
    return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def test_the_checker_pins_the_shipped_runtime_and_style():
    assert xcheck.RUNTIME_SHA256 == _b64((WEB / "explorable.mjs").read_text())
    assert xcheck.STYLE_SHA256 == _b64((WEB / "explorable.css").read_text())


@pytest.mark.parametrize("value", [SPRING, TRACE, BOX], ids=["model", "steps", "solid"])
def test_documents_are_made_checked_and_deterministic(value):
    made = make.make(value)
    assert made.media_type == MEDIA
    verdict = check.check(made.bytes, made.media_type, made.spec)
    assert verdict.ok, [(f.rule, f.detail) for f in verdict.technical if not f.passed]
    assert any(f.rule == "browser_run" and "not run in a browser" in f.detail for f in verdict.style)
    assert made.bytes == make.make(value).bytes
    text = made.bytes.decode()
    assert explorable.MADE_LABEL in text
    assert text.count("<script") == 2 and text.count("<style") == 1


def test_the_model_document_states_its_starting_values_without_scripts():
    made = make.make(SPRING)
    verdict = check.check(made.bytes, MEDIA, made.spec)
    rec = next(f for f in verdict.technical if f.rule == "values_recovered")
    assert rec.passed and rec.measure == 1.0
    assert "Natural frequency" in made.bytes.decode() and "3.162 rad/s" in made.bytes.decode()


def _edit(value, old, new, count=1):
    data = make.make(value).bytes.decode()
    assert data.count(old) >= count, old
    return data.replace(old, new, count).encode()


WRONG = {
    "second script": ('<script type="module">', '<script>alert(1)</script><script type="module">',
                      {"one_runtime_script"}),
    "changed runtime": ("const SVG =", "const SVG  =", {"one_runtime_script"}),
    "changed style": (".nsf-xp-page{margin:0", ".nsf-xp-page{margin:1px", {"one_style"}),
    "no policy": ('<meta http-equiv="Content-Security-Policy"', '<meta name="generator"', {"csp_exact"}),
    "looser policy": ("default-src &#x27;none&#x27;", "default-src *", {"csp_exact"}),
    "refresh": ('<meta charset="utf-8">', '<meta charset="utf-8"><meta http-equiv="refresh" content="0">',
                {"allowed_markup"}),
    "image": ("<h1>", '<img src="x.png"><h1>', {"allowed_markup"}),
    "event attribute": ("<h1>", '<h1 onclick="x()">', {"allowed_markup"}),
    "style attribute": ("<h1>", '<h1 style="color:red">', {"allowed_markup"}),
    "link": ("<h1>", '<link rel="stylesheet" href="https://example.org/x.css"><h1>', {"allowed_markup"}),
    "svg": ("<h1>", "<svg><circle r=\"3\"/></svg><h1>", {"allowed_markup"}),
    "comment": ("<h1>", "<!-- hidden --><h1>", {"allowed_markup"}),
    "address in an attribute": ('<main id="nsf-xp-root"', '<main id="javascript:x"', {"allowed_markup"}),
    "iframe": ("<h1>", "<iframe></iframe><h1>", {"allowed_markup"}),
    "markup in the spec block": ('"title":"A model"', '"title":"</script><script>x()</script>"',
                                 {"spec_block", "one_runtime_script"}),
    "broken spec block": ('"version":1', '"version":1,', {"spec_block"}),
    "second spec block": ('<script type="module">', '<script type="application/json" id="nsf-xp-spec">{}</script>'
                          '<script type="module">', {"spec_block"}),
    "changed starting value": ('data-name="omega" data-value="3.1622776601683795"',
                               'data-name="omega" data-value="3.2"', {"values_recovered"}),
}


@pytest.mark.parametrize("case", sorted(WRONG))
def test_wrong_documents_are_caught(case):
    old, new, rules = WRONG[case]
    verdict = check.check(_edit(SPRING, old, new), MEDIA, make.make(SPRING).spec)
    assert not verdict.ok
    assert rules <= _failed(verdict), (case, _failed(verdict))


def test_a_document_for_a_different_spec_is_caught():
    other = copy.deepcopy(SPRING)
    other["data"]["parameters"][0]["value"] = 2
    made = make.make(SPRING)
    assert "spec_block" in _failed(check.check(made.bytes, MEDIA, Spec.from_dict(other)))


def test_hostile_bytes_are_named_findings():
    for bad in (b"", b"\xff\xfe", b"<html>", b"<!DOCTYPE html><script>1</script>", b"<" * 10000):
        assert not check.check(bad, MEDIA).ok


def test_unshowable_specs_are_refused_before_anything_is_made():
    broken = copy.deepcopy(SPRING)
    broken["data"]["quantities"][0]["expr"] = "sqrt(k / mass)"
    with pytest.raises(Malformed, match="unknown name"):
        make.make(broken)
    trace = copy.deepcopy(TRACE)
    trace["data"]["steps"][0]["pointers"][0]["index"] = 9
    with pytest.raises(Malformed, match="outside its array"):
        make.make(trace)
    solid = copy.deepcopy(BOX)
    solid["data"]["faces"][0] = [0, 1, 99]
    with pytest.raises(Malformed, match="does not exist"):
        make.make(solid)


def test_the_spec_block_cannot_end_its_element():
    value = copy.deepcopy(SPRING)
    value["description"] = "a & b > c"
    made = make.make(value)
    block = re.search(r'<script type="application/json" id="nsf-xp-spec">(.*?)</script>', made.bytes.decode(), re.S)
    assert "<" not in block.group(1) and ">" not in block.group(1) and "&" not in block.group(1)
    assert json.loads(block.group(1))["description"] == "a & b > c"
    assert check.check(made.bytes, MEDIA, made.spec).ok


def test_the_runtime_uses_no_dangerous_interface():
    source = (WEB / "explorable.mjs").read_text()
    code = re.sub(r"(^|\s)//[^\n]*", r"\1", source)
    for word in ("eval(", "Function(", "import(", "fetch(", "XMLHttpRequest", "WebSocket", "EventSource",
                 "localStorage", "sessionStorage", "indexedDB", "document.cookie", "innerHTML", "outerHTML",
                 "insertAdjacentHTML", "document.write", "setAttribute('style'", "sendBeacon", "postMessage",
                 "navigator.", "location", "window.open", "createContextualFragment", "srcdoc", "<script",
                 "importScripts", "Worker("):
        assert word not in code, word
    # the one address-shaped text is the SVG namespace identifier, never fetched
    assert re.findall(r"https?://[^'\"\s]+", code) == ["http://www.w3.org/2000/svg"]
    css = (WEB / "explorable.css").read_text()
    assert "url(" not in css and "@import" not in css
    rules = [r.split("{")[0] for r in re.findall(r"[^{}]+\{[^{}]*\}", re.sub(r"@media[^{]+\{", "", css))]
    for selector in rules:
        for part in selector.split(","):
            assert ".nsf-xp" in part, f"a rule outside the runtime's classes: {part.strip()}"


def test_the_palette_is_gray():
    css = (WEB / "explorable.css").read_text()
    for hexcode in re.findall(r"#([0-9a-fA-F]{6})\b", css):
        r, g, b = (int(hexcode[i:i + 2], 16) for i in (0, 2, 4))
        assert r == g == b, hexcode


# Strings in a specification may hold "<" as text (DESIGN §6, lane K's relaxed content rule): an expression's
# comparison and pseudocode's "<" are text, escaped wherever they are written.


def test_a_comparison_written_with_less_than_is_accepted_in_a_spec():
    value = copy.deepcopy(SPRING)
    value["data"]["quantities"].append({"name": "slow", "label": "Slow", "expr": "if(omega < k, 1, 0)"})
    made = make.make(value)
    assert check.check(made.bytes, MEDIA, made.spec).ok


def test_pseudocode_with_less_than_is_shown_as_text():
    value = copy.deepcopy(TRACE)
    value["data"]["code"][1] = "while lo <= hi: if a[mid] < target"
    made = make.make(value)
    assert check.check(made.bytes, MEDIA, made.spec).ok
    assert "a[mid] &lt; target" in made.bytes.decode()


def test_the_same_comparisons_written_with_signs_that_pass_today():
    value = copy.deepcopy(SPRING)
    value["data"]["quantities"].append({"name": "slow", "label": "Slow", "expr": "if(omega ≤ k, 1, 0)"})
    made = make.make(value)
    assert check.check(made.bytes, MEDIA, made.spec).ok
