"""An independent check of explorable documents (DESIGN §6, ADR-074 lane G2). It imports nothing from `make`.

The document is parsed with the standard library's HTML tokenizer and judged against an allow-list:
- `doctype`, `allowed_markup`: only the elements and attributes a document of this kind needs; no event attribute, no
  `style` attribute, no element that can load or run anything (link, img, iframe, object, embed, form, a, base,
  svg, audio, video, source, frame, template, ...), no comment, no address in any attribute.
- `one_runtime_script`: exactly one script that runs, inline, whose SHA-256 is RUNTIME_SHA256 (the fixed runtime);
  `one_style`: exactly one style element, whose SHA-256 is STYLE_SHA256 (the fixed stylesheet).
- `csp_exact`: exactly one Content-Security-Policy meta element, before any style or script, whose content is
  exactly EXPECTED_CSP (default-src 'none'; the two hashes; base-uri and form-action 'none'); no other http-equiv.
- `spec_block`: exactly one JSON data block (`application/json`, id nsf-xp-spec) holding no "<" character, which
  parses and is a valid specification of a form this runtime shows (checked here: the envelope, the form's own
  rules, and every expression parsed by artifacts/expr.py); with a spec given, it must be that spec.
- `values_recovered` (a model): the values the document states without scripts (its starting-settings table)
  match what artifacts/expr.py computes for the model.
Text inside elements is inert (it is never parsed as markup, and the runtime writes only text nodes), so an address
written as text in a title is text, not a reference; the hashes pin everything that runs or styles.

Whether the document behaves in a browser is the optional `check/browser.py` run; this module never starts one and
says so in a style note.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from html.parser import HTMLParser

from .. import expr
from . import Finding, Verdict

RUNTIME_SHA256 = "XUAmCNmBB6x/rDG0eNBI9v/tFJLzMpI07oEh7AGRWxA="
STYLE_SHA256 = "9C0U6Rqw+WnuqE/DpRhcS2tCPZxhLGiYV4gyMT7+SWU="
EXPECTED_CSP = (f"default-src 'none'; script-src 'sha256-{RUNTIME_SHA256}'; style-src 'sha256-{STYLE_SHA256}'; "
                "base-uri 'none'; form-action 'none'")
FORMS = ("explorable_model", "explorable_steps", "solid_view")
MAX_BYTES = 40 << 20
MAX_ELEMENTS = 200_000

_GLOBAL = {"class"}
ALLOWED = {
    "html": {"lang"}, "head": set(), "title": set(), "body": set(), "style": set(),
    "meta": {"charset", "name", "content", "http-equiv"}, "script": {"type", "id"},
    "main": {"id", "data-autoboot"}, "div": set(), "h1": set(), "h2": set(), "h3": set(), "p": set(),
    "table": set(), "caption": set(), "thead": set(), "tbody": set(), "tr": set(), "th": {"scope"},
    "td": {"data-name", "data-value"}, "ol": set(), "ul": set(), "li": set(), "pre": set(), "code": set(),
    "span": set(), "strong": set(),
}
_META_NAMES = {"viewport", "color-scheme", "generator"}
_ADDRESS = re.compile(r"(?i)[a-z][a-z0-9+.-]*:|//|url\s*\(|\\")
_VOID = {"meta"}


def b64sha256(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doctype = None
        self.problems: list[str] = []
        self.elements = 0
        self.scripts: list[tuple[dict, str]] = []
        self.styles: list[str] = []
        self.metas: list[dict] = []
        self.order: list[str] = []          # "csp", "style", "script" in document order
        self.cells: list[dict] = []
        self._raw: list[str] | None = None
        self._raw_attrs: dict | None = None
        self._raw_tag = None

    def handle_decl(self, decl):
        if decl.lower() != "doctype html" or self.doctype is not None:
            self.problems.append("a declaration other than one <!DOCTYPE html>")
        self.doctype = decl

    def unknown_decl(self, data):
        self.problems.append("an unknown declaration")

    def handle_pi(self, data):
        self.problems.append("a processing instruction")

    def handle_comment(self, data):
        self.problems.append("a comment")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        self.elements += 1
        if self.elements > MAX_ELEMENTS:
            if self.elements == MAX_ELEMENTS + 1:
                self.problems.append("too many elements")
            return
        allowed = ALLOWED.get(tag)
        if allowed is None:
            self.problems.append(f"the element <{tag}> is not allowed")
            return
        seen = {}
        for name, value in attrs:
            value = "" if value is None else value
            if name in seen:
                self.problems.append(f"<{tag}> repeats the attribute {name}")
            seen[name] = value
            if name.startswith("on") or name == "style" or (name not in allowed and name not in _GLOBAL):
                self.problems.append(f"the attribute {name} on <{tag}> is not allowed")
            elif name not in ("content",) and _ADDRESS.search(value) and not (tag == "td" and name == "data-value"):
                self.problems.append(f"the attribute {name} on <{tag}> holds an address")
        if tag == "meta":
            self.metas.append(seen)
            if seen.get("http-equiv", "").lower() == "content-security-policy":
                self.order.append("csp")
            elif "http-equiv" in seen:
                self.problems.append("a meta element with an http-equiv other than the policy")
            elif "name" in seen and seen["name"] not in _META_NAMES:
                self.problems.append(f"a meta element named {seen['name']!r}")
            elif "content" in seen and _ADDRESS.search(seen["content"]):
                self.problems.append("a meta element holds an address")
        if tag in ("script", "style"):
            self._raw, self._raw_attrs, self._raw_tag = [], seen, tag
            self.order.append(tag)
        if tag == "td" and "data-name" in seen:
            self.cells.append(seen)

    def handle_endtag(self, tag):
        if tag == self._raw_tag and self._raw is not None:
            text = "".join(self._raw)
            if tag == "script":
                self.scripts.append((self._raw_attrs, text))
            else:
                self.styles.append(text)
            self._raw = self._raw_attrs = self._raw_tag = None

    def handle_data(self, data):
        if self._raw is not None:
            self._raw.append(data)


# ---------------------------------------------------------------------------------------------------------------
# The specification inside, checked here on its own terms


def _text(v, limit) -> bool:
    return isinstance(v, str) and len(v) <= limit and not re.search(
        "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]", v)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def spec_problems(value) -> list[str]:
    if not isinstance(value, dict):
        return ["the specification is not an object"]
    if set(value) != {"spec", "version", "title", "description", "data", "options", "sources", "made_by"}:
        return ["the specification's fields are not the envelope's"]
    if value["spec"] not in FORMS or value["version"] != 1:
        return ["the form is not one this runtime shows"]
    if not _text(value["title"], 200) or not _text(value["description"], 2000):
        return ["the title or description is not plain text"]
    d = value["data"]
    if not isinstance(d, dict):
        return ["no data"]
    if value["spec"] == "explorable_model":
        params = d.get("parameters")
        if not isinstance(params, list) or not 1 <= len(params) <= 12 or not isinstance(d.get("views"), list):
            return ["a model needs 1 to 12 parameters and its views"]
        for p in params:
            if not isinstance(p, dict) or not _text(p.get("label"), 80) or not all(
                    _num(p.get(k)) for k in ("min", "max", "step", "value")):
                return ["a parameter is not a labelled range of numbers"]
        for group in ("quantities",):
            for q in d.get(group, []):
                if not isinstance(q, dict) or not isinstance(q.get("expr"), str):
                    return ["a quantity has no expression"]
        try:
            return expr.model_problems(d)
        except (AttributeError, TypeError, KeyError, StopIteration):
            return ["the model's parts are not shaped as the form says"]
    if value["spec"] == "explorable_steps":
        try:
            arrays, steps, code = d["arrays"], d["steps"], d.get("code", [])
            problems = []
            if not 1 <= len(arrays) <= 4 or not 1 <= len(steps) <= 1000 or len(code) > 60:
                problems.append("1 to 4 arrays, 1 to 1000 steps, at most 60 lines of code")
            if not all(_text(line, 120) for line in code):
                problems.append("a line of code is not plain text")
            for k, s in enumerate(steps):
                if not _text(s["note"], 300):
                    problems.append(f"step {k + 1}: the note is not plain text")
                if s.get("line") is not None and not (isinstance(s["line"], int) and 0 <= s["line"] < len(code)):
                    problems.append(f"step {k + 1}: no such line")
                if len(s["arrays"]) != len(arrays):
                    problems.append(f"step {k + 1}: one list per array")
                    continue
                for lst in s["arrays"]:
                    if len(lst) > 64 or not all(_num(v) or _text(v, 16) for v in lst):
                        problems.append(f"step {k + 1}: an array holds at most 64 numbers or short words")
                for p in s.get("pointers", []):
                    if not (0 <= p["array"] < len(arrays) and (p["index"] is None or 0 <= p["index"] < len(
                            s["arrays"][p["array"]]))):
                        problems.append(f"step {k + 1}: a pointer points outside its array")
                for m in s.get("marks", []):
                    if not (0 <= m["array"] < len(arrays) and 0 <= m["index"] < len(s["arrays"][m["array"]])):
                        problems.append(f"step {k + 1}: a mark lies outside its array")
            return problems[:20]
        except (KeyError, TypeError):
            return ["the trace is not shaped as the form says"]
    v, f = d.get("vertices"), d.get("faces")
    if not isinstance(v, list) or not 3 <= len(v) <= 20000 or not all(
            isinstance(p, list) and len(p) == 3 and all(_num(c) for c in p) for p in v):
        return ["a solid has 3 to 20000 vertices of three numbers"]
    if not isinstance(f, list) or not 1 <= len(f) <= 40000 or not all(
            isinstance(t, list) and len(t) == 3 and all(isinstance(i, int) and 0 <= i < len(v) for i in t)
            for t in f):
        return ["a solid has 1 to 40000 triangles of three vertex numbers"]
    return []


# ---------------------------------------------------------------------------------------------------------------
# The check


def check_explorable(data: bytes, media_type: str = "", spec=None, source=None) -> Verdict:
    technical: list[Finding] = []
    style: list[Finding] = []
    if len(data) > MAX_BYTES:
        return Verdict.of([Finding("allowed_markup", False, "larger than the size limit")])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Verdict.of([Finding("allowed_markup", False, "not UTF-8 text")])
    r = _Reader()
    try:
        r.feed(text)
        r.close()
    except (AssertionError, ValueError) as error:
        r.problems.append(f"the markup could not be read ({type(error).__name__})")
    technical.append(Finding("doctype", (r.doctype or "").lower() == "doctype html", "<!DOCTYPE html> first"
                             if r.doctype else "no doctype"))
    technical.append(Finding("allowed_markup", not r.problems, "only allowed elements and attributes, no addresses"
                             if not r.problems else "; ".join(sorted(set(r.problems))[:4])))
    running = [(a, t) for a, t in r.scripts if a.get("type") != "application/json"]
    data_blocks = [(a, t) for a, t in r.scripts if a.get("type") == "application/json"]
    run_ok = (len(running) == 1 and running[0][0] == {"type": "module"}
              and b64sha256(running[0][1]) == RUNTIME_SHA256)
    technical.append(Finding("one_runtime_script", run_ok, "one inline module script: the fixed runtime" if run_ok
                             else f"{len(running)} script(s) that run; the fixed runtime's hash "
                                  f"{'matches' if running and b64sha256(running[0][1]) == RUNTIME_SHA256 else 'does not match'}"))
    style_ok = len(r.styles) == 1 and b64sha256(r.styles[0]) == STYLE_SHA256
    technical.append(Finding("one_style", style_ok, "one style element: the fixed stylesheet" if style_ok
                             else f"{len(r.styles)} style element(s), or not the fixed stylesheet"))
    policies = [m for m in r.metas if m.get("http-equiv", "").lower() == "content-security-policy"]
    first = r.order[:1] == ["csp"]
    csp_ok = len(policies) == 1 and policies[0].get("content") == EXPECTED_CSP and first
    technical.append(Finding("csp_exact", csp_ok, "the policy admits only the runtime and its style, before both"
                             if csp_ok else "the Content-Security-Policy is missing, repeated, late or not exact"))
    value = None
    block_ok = len(data_blocks) == 1 and data_blocks[0][0] == {"type": "application/json", "id": "nsf-xp-spec"}
    detail = "one JSON specification block that validates"
    if block_ok:
        raw = data_blocks[0][1]
        if "<" in raw:
            block_ok, detail = False, "the JSON block holds a '<'"
        else:
            try:
                value = json.loads(raw)
            except (ValueError, RecursionError):
                block_ok, detail = False, "the JSON block does not parse"
        if block_ok:
            problems = spec_problems(value)
            if problems:
                block_ok, detail = False, "the specification is not valid: " + "; ".join(problems[:3])
            elif spec is not None and hasattr(spec, "to_dict") and json.dumps(spec.to_dict(), sort_keys=True) != \
                    json.dumps(value, sort_keys=True):
                block_ok, detail = False, "the document holds a different specification from the one given"
    else:
        detail = f"{len(data_blocks)} JSON block(s) where one is expected"
    technical.append(Finding("spec_block", block_ok, detail))
    if block_ok and value["spec"] == "explorable_model":
        try:
            want = expr.evaluate_model(value["data"])["values"]
        except (expr.ExprError, KeyError, TypeError, StopIteration):
            want = {}
        hits = 0
        for cell in r.cells:
            name = cell.get("data-name")
            try:
                got = float(cell.get("data-value", "nan"))
            except ValueError:
                continue
            w = want.get(name)
            if w is not None and (got == w or (math.isnan(got) and math.isnan(w))
                                  or abs(got - w) <= 1e-12 * max(1.0, abs(w))):
                hits += 1
        share = hits / len(want) if want else 0.0
        technical.append(Finding("values_recovered", bool(want) and hits == len(want) == len(r.cells),
                                 f"{hits} of {len(want)} starting values stated without scripts match the model",
                                 measure=share))
    style.append(Finding("browser_run", True, "not run in a browser (check/browser.py is the optional run)"))
    return Verdict.of(technical, style)


__all__ = ["EXPECTED_CSP", "RUNTIME_SHA256", "STYLE_SHA256", "b64sha256", "check_explorable", "spec_problems"]
