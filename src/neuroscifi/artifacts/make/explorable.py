"""Explorable documents (DESIGN §6 and §8, ADR-074 lane G2): `explorable.model` (sliders, a model, plots),
`explorable.steps` (a step-through of a recorded trace) and `solid.view` (a solid to turn).

Each is ONE self-contained HTML document: the fixed runtime `artifacts/web/explorable.mjs` inlined as the only
script that runs, the fixed stylesheet `artifacts/web/explorable.css` as the only style, the specification as a JSON
data block, and a Content-Security-Policy meta element that admits exactly those two by their SHA-256 and nothing
else (`default-src 'none'`), so that even opened from a disk it can load and send nothing. Without scripts the
document still says what it is and shows the model at its starting settings (computed here by `artifacts/expr.py`),
the whole trace in words, or the solid's size.

The same runtime is imported unchanged by a host page that calls `mount(element, spec)` (a later wave: the console and
Pocket under their own policies, with `explorable.css` served as a first-party stylesheet).
Nothing made here is evidence (COMMON, ADR-049, ADR-071).
"""
from __future__ import annotations

import base64
import hashlib
import html
import math
from importlib import resources

from .. import expr
from ..spec import Spec, canonical_json
from ..types import Limits, Malformed, Refused
from . import solids

MADE_LABEL = "Made by NeuroSciFi from a specification: a model to explore, not evidence."
_TEXT = {"type": "string", "maxLength": 80}
_NAME = {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,31}$"}
_UNIT = {"type": "string", "maxLength": 24}
_EXPR = {"type": "string", "minLength": 1, "maxLength": expr.MAX_SOURCE}
_NUM = {"type": "number"}
_HISTORY = {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 300}}
_OPTIONS = {"type": "object", "additionalProperties": False, "properties": {"history": _HISTORY}}


def _obj(required, properties):
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


SCHEMAS = {
    "explorable.model": {"data": _obj(["parameters", "views"], {
        "parameters": {"type": "array", "minItems": 1, "maxItems": 12, "items": _obj(
            ["name", "label", "min", "max", "step", "value"],
            {"name": _NAME, "label": _TEXT, "min": _NUM, "max": _NUM, "step": _NUM, "value": _NUM, "unit": _UNIT})},
        "quantities": {"type": "array", "maxItems": 32, "items": _obj(
            ["name", "expr"], {"name": _NAME, "label": _TEXT, "expr": _EXPR, "unit": _UNIT})},
        "dynamics": _obj(["state", "steps", "dt"], {
            "state": {"type": "array", "minItems": 1, "maxItems": 8, "items": _obj(
                ["name", "initial", "update"],
                {"name": _NAME, "label": _TEXT, "initial": _EXPR, "update": _EXPR, "unit": _UNIT})},
            "steps": {"type": "integer", "minimum": 1, "maximum": expr.MAX_STEPS},
            "dt": {"type": "number", "minimum": 1e-12}, "time_label": {"type": "string", "maxLength": 40},
            "time_unit": _UNIT}),
        "views": {"type": "array", "minItems": 1, "maxItems": 6, "items": _obj(["kind"], {
            "kind": {"type": "string", "enum": ["plot", "table", "bars"]}, "title": {"type": "string", "maxLength": 120},
            "x": {"type": "string", "maxLength": 32}, "y": {"type": "array", "minItems": 1, "maxItems": 4, "items": _NAME},
            "from": _NUM, "to": _NUM, "points": {"type": "integer", "minimum": 2, "maximum": expr.MAX_SWEEP},
            "y_label": _TEXT, "items": {"type": "array", "minItems": 1, "maxItems": 16, "items": _NAME}})}}),
        "options": _OPTIONS},
    "explorable.steps": {"data": _obj(["arrays", "steps"], {
        "code": {"type": "array", "maxItems": 60, "items": {"type": "string", "maxLength": 120}},
        "arrays": {"type": "array", "minItems": 1, "maxItems": 4, "items": _obj(["name"], {"name": _NAME,
                                                                                        "label": _TEXT})},
        "steps": {"type": "array", "minItems": 1, "maxItems": 1000, "items": _obj(["note", "arrays"], {
            "note": {"type": "string", "maxLength": 300},
            "line": {"type": ["integer", "null"], "minimum": 0},
            "arrays": {"type": "array", "maxItems": 4, "items": {"type": "array", "maxItems": 64, "items": {
                "type": ["number", "string"], "maxLength": 16}}},
            "pointers": {"type": "array", "maxItems": 8, "items": _obj(["name", "array", "index"], {
                "name": {"type": "string", "maxLength": 16}, "array": {"type": "integer", "minimum": 0},
                "index": {"type": ["integer", "null"], "minimum": 0}})},
            "marks": {"type": "array", "maxItems": 64, "items": _obj(["array", "index"], {
                "array": {"type": "integer", "minimum": 0}, "index": {"type": "integer", "minimum": 0},
                "kind": {"type": "string", "enum": ["active", "out", "found"]}})},
            "vars": {"type": "array", "maxItems": 12, "items": _obj(["name", "value"], {
                "name": {"type": "string", "maxLength": 32},
                "value": {"type": ["number", "string", "null"], "maxLength": 40}})}})}}),
        "options": _OPTIONS},
    "solid.view": {"data": _obj(["vertices", "faces"], {
        "vertices": {"type": "array", "minItems": 3, "maxItems": 20000, "items": {
            "type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": -1e9,
                                                                      "maximum": 1e9}}},
        "faces": {"type": "array", "minItems": 1, "maxItems": 40000, "items": {
            "type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "integer", "minimum": 0}}},
        "unit": {"type": "string", "enum": solids.UNITS}}), "options": _OPTIONS},
}


# ---------------------------------------------------------------------------------------------------------------
# The fixed runtime and style


def runtime_text() -> str:
    return (resources.files("neuroscifi.artifacts") / "web" / "explorable.mjs").read_text(encoding="utf-8")


def style_text() -> str:
    return (resources.files("neuroscifi.artifacts") / "web" / "explorable.css").read_text(encoding="utf-8")


def sha256_b64(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def csp(script: str, style: str) -> str:
    return (f"default-src 'none'; script-src 'sha256-{sha256_b64(script)}'; style-src 'sha256-{sha256_b64(style)}'; "
            "base-uri 'none'; form-action 'none'")


# ---------------------------------------------------------------------------------------------------------------
# Words and numbers


_SUPER = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def fmt(v: float) -> str:
    """A number as a person reads it (the runtime's `fmt` follows the same rule)."""
    v = float(v)
    if math.isnan(v):
        return "undefined"
    if math.isinf(v):
        return "infinite" if v > 0 else "minus infinite"
    if v == 0:
        return "0"
    a = abs(v)
    if 1e-4 <= a < 1e6:
        text = f"{v:.{min(10, max(0, 3 - math.floor(math.log10(a))))}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text == "-0" else text.replace("-", "−")
    mant, exp = f"{v:.3e}".split("e")
    if "." in mant:
        mant = mant.rstrip("0").rstrip(".")
    return f"{mant.replace('-', '−')} × 10{str(int(exp)).translate(_SUPER)}"


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _json_block(value) -> str:
    """The spec as JSON that cannot end its script element: <, > and & are written as \\u escapes."""
    text = canonical_json(value)
    for ch, code in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"),
                     (" ", "\\u2029")):
        text = text.replace(ch, code)
    return text


def _with_unit(v, unit) -> str:
    return f"{fmt(v)} {unit}" if unit else fmt(v)


# ---------------------------------------------------------------------------------------------------------------
# The three forms' own checks (the semantic half; the JSON schema is the structural half)


def steps_problems(d: dict) -> list[str]:
    problems = []
    n_arrays, n_code = len(d["arrays"]), len(d.get("code", []))
    names = [a["name"] for a in d["arrays"]]
    if len(set(names)) != len(names):
        problems.append("two arrays have the same name")
    for k, s in enumerate(d["steps"]):
        if s.get("line") is not None and s["line"] >= n_code:
            problems.append(f"step {k + 1}: line {s['line']} does not exist")
        if len(s["arrays"]) != n_arrays:
            problems.append(f"step {k + 1}: one list per declared array")
            continue
        for p in s.get("pointers", []):
            if p["array"] >= n_arrays or (p["index"] is not None and p["index"] >= len(s["arrays"][p["array"]])):
                problems.append(f"step {k + 1}: the pointer {p['name']!r} points outside its array")
        for m in s.get("marks", []):
            if m["array"] >= n_arrays or m["index"] >= len(s["arrays"][m["array"]]):
                problems.append(f"step {k + 1}: a mark lies outside its array")
    return problems[:20]


def mesh_problems(d: dict) -> list[str]:
    n = len(d["vertices"])
    if any(i >= n for f in d["faces"] for i in f):
        return ["a face refers to a vertex that does not exist"]
    return []


# ---------------------------------------------------------------------------------------------------------------
# The document


def _fallback(spec: Spec) -> str:
    d = spec.data
    parts = [f"<h1>{_esc(spec.title)}</h1>"]
    if spec.description:
        parts.append(f"<p>{_esc(spec.description)}</p>")
    parts.append(f'<p class="nsf-xp-made">{_esc(MADE_LABEL)}</p>')
    if spec.spec == "explorable.model":
        result = expr.evaluate_model(d)
        labels = {p["name"]: (p["label"], p.get("unit", "")) for p in d["parameters"]}
        labels.update({q["name"]: (q.get("label", q["name"]), q.get("unit", "")) for q in d.get("quantities", [])})
        dyn = d.get("dynamics")
        labels.update({s["name"]: (s.get("label", s["name"]), s.get("unit", "")) for s in (dyn or {}).get("state", [])})
        parts.append("<p>This page needs scripts to move the settings and draw the plots. Here is the model at its "
                     "starting settings.</p>")
        rows = []
        end = ""
        if dyn:
            end = f" at the end ({_esc(dyn.get('time_label', 'time').lower())} {fmt(dyn['steps'] * dyn['dt'])}" + (
                f" {_esc(dyn['time_unit'])})" if dyn.get("time_unit") else ")")
        for name, value in result["values"].items():
            label, unit = labels[name]
            after = end if name in result["series"] else ""
            rows.append(f'<tr><th scope="row">{_esc(label)}{after}</th><td data-name="{_esc(name)}" '
                        f'data-value="{float(value)!r}">{_esc(_with_unit(value, unit))}</td></tr>')
        parts.append("<table><caption>At the starting settings</caption><tbody>" + "".join(rows) + "</tbody></table>")
    elif spec.spec == "explorable.steps":
        parts.append("<p>This page needs scripts to step through the trace. Here is every step in words.</p>")
        if d.get("code"):
            parts.append("<pre><code>" + _esc("\n".join(d["code"])) + "</code></pre>")
        parts.append('<ol class="nsf-xp-steps">' + "".join(f"<li>{_esc(s['note'])}</li>" for s in d["steps"])
                     + "</ol>")
    else:
        v = d["vertices"]
        lo = [min(p[k] for p in v) for k in range(3)]
        hi = [max(p[k] for p in v) for k in range(3)]
        unit = d.get("unit", "")
        unit = "" if unit == "unitless" else unit
        size = " by ".join(fmt(b - a) for a, b in zip(lo, hi, strict=True))
        parts.append(f"<p>This page needs scripts to show the solid. It has {len(d['faces'])} triangles and measures "
                     f"{_esc(size)}{(' ' + _esc(unit)) if unit else ''} (width by depth by height).</p>")
    return '<div class="nsf-xp-fallback">' + "".join(parts) + "</div>"


def document(spec: Spec) -> bytes:
    script, style = runtime_text(), style_text()
    if "</script" in script.lower() or "<!--" in script or "</style" in style.lower():
        raise Malformed("the runtime or its style could end its own element")
    out = ["<!DOCTYPE html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           f'<meta http-equiv="Content-Security-Policy" content="{_esc(csp(script, style))}">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<meta name="color-scheme" content="light dark">',
           '<meta name="generator" content="NeuroSciFi explorable runtime">',
           f"<title>{_esc(spec.title)}</title>", f"<style>{style}</style>", "</head>",
           '<body class="nsf-xp-page">', f'<main id="nsf-xp-root" data-autoboot="1">{_fallback(spec)}</main>',
           f'<script type="application/json" id="nsf-xp-spec">{_json_block(spec.to_dict())}</script>',
           f'<script type="module">{script}</script>', "</body>", "</html>", ""]
    return "\n".join(out).encode("utf-8")


def render(spec: Spec, limits: Limits = Limits()) -> tuple[bytes, tuple[str, ...]]:
    d = spec.data
    if spec.spec == "explorable.model":
        problems = expr.model_problems(d)
    elif spec.spec == "explorable.steps":
        problems = steps_problems(d)
    else:
        problems = mesh_problems(d)
    if problems:
        raise Malformed("the specification cannot be shown: " + "; ".join(problems[:5]))
    if spec.spec == "explorable.model" and d.get("dynamics"):
        work = d["dynamics"]["steps"] * (len(d["dynamics"]["state"]) + len(d.get("quantities", [])))
        if work > limits.max_elements:
            raise Refused("the model would take too many evaluations", rule="max_elements", reason="too_large")
    data = document(spec)
    notes = ("made by the system from a specification; not evidence",
             f"runtime sha256-{sha256_b64(runtime_text())}", "the document loads nothing and sends nothing")
    return data, notes


def solid_view_spec(solid: dict, *, title: str | None = None, description: str | None = None) -> dict:
    """A `solid.view` spec that shows the mesh a solid spec makes (its vertices and triangles, exactly)."""
    made = Spec.from_dict(solid)
    vertices, faces = solids.mesh_of(made)
    return {"spec": "solid.view", "version": 1, "title": title or made.title,
            "description": description if description is not None else made.description,
            "data": {"vertices": [[float(c) for c in v] for v in vertices], "faces": faces.tolist(),
                     "unit": made.options.get("unit", "mm")},
            "options": {}, "sources": list(made.sources), "made_by": {"generator": "", "version": 0}}


MEDIA_TYPE = "application/vnd.neuroscifi.explorable+html"

__all__ = ["MADE_LABEL", "MEDIA_TYPE", "SCHEMAS", "csp", "document", "fmt", "mesh_problems", "render",
           "runtime_text", "sha256_b64", "solid_view_spec", "steps_problems", "style_text"]
