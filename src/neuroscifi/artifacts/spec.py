"""The specification envelope of a thing to make (DESIGN §6, ADR-072 for the envelope, ADR-074 for the forms).

A `Spec` is the editable source of a made artifact:

    {"spec": "<form>", "version": 1, "title": str, "description": str, "data": {...}, "options": {...},
     "sources": [artifact ids or "request"], "made_by": {"generator": name, "version": n}}

`validate(value)` returns every violation ([] = valid): the envelope's schema (checked with
`inference/jsonschema.py`), the size bounds, the nesting bound, the "no markup, script, URL or path in any field"
rule (every string and every key, recursively), and, when the form is registered, the form's own schemas for
`data` and `options`. A form nobody registered is a violation: nothing is made from a spec no schema checked.
Lanes G1/G2 register each form with `register_form(form, data=..., options=...)`.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from ..inference.jsonschema import check_schema
from ..inference.jsonschema import validate as _schema_validate
from .types import Malformed

VERSION = 1
MAX_ENVELOPE_BYTES = 256 << 10       # the spec serialised with every list inside `data` emptied
MAX_TOTAL_BYTES = 32 << 20           # the whole spec, data arrays included (each form bounds its arrays further)
MAX_DEPTH = 16                       # nesting of objects and arrays
MAX_STRING = 4096                    # characters of any one string or key

FORM_NAME = r"^[a-z][a-z0-9_]{0,47}$"
SOURCE_ID = r"^(request|[A-Za-z0-9_-]{1,128})$"

ENVELOPE = {
    "type": "object", "additionalProperties": False,
    "required": ["spec", "version", "title", "description", "data", "options", "sources", "made_by"],
    "properties": {
        "spec": {"type": "string", "pattern": FORM_NAME},
        "version": {"type": "integer", "const": VERSION},
        "title": {"type": "string", "maxLength": 200},
        "description": {"type": "string", "maxLength": 2000},
        "data": {"type": "object"},
        "options": {"type": "object"},
        "sources": {"type": "array", "maxItems": 256, "items": {"type": "string", "pattern": SOURCE_ID}},
        "made_by": {"type": "object", "additionalProperties": False, "required": ["generator", "version"],
                    "properties": {"generator": {"type": "string", "pattern": r"^[a-z0-9_.-]{0,64}$"},
                                   "version": {"type": "integer", "minimum": 0}}},
    },
}

# What no field may hold. Deliberately broad: a false refusal costs a rephrased title; a false pass costs a script.
_FORBIDDEN = (
    ("markup", re.compile(r"<\s*[A-Za-z!/?]|&#?[A-Za-z0-9]+;")),
    ("script", re.compile(r"(?i)\b(?:javascript|vbscript)\s*:|\bon[a-z]+\s*=|\beval\s*\(|\bexpression\s*\(")),
    ("url", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://|\b(?:data|mailto|file|blob):\S|\bwww\.[a-z0-9-]+\.")),
    ("path", re.compile(r"(?:^|[\s\"'(=])(?:/|~/|\.{1,2}/)[^\s/]|\\|(?:^|\s)[A-Za-z]:[\\/]|\.\./")),
    ("control", re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")),
)

_FORMS: dict[str, dict] = {}


def register_form(form: str, *, data: dict, options: dict | None = None) -> None:
    """Register the schemas of one form's `data` and `options` (the JSON Schema subset of inference/jsonschema)."""
    if re.match(FORM_NAME, form) is None:
        raise ValueError(f"form name {form!r} is not a lower-case word")
    if form in _FORMS:
        raise ValueError(f"form {form!r} is already registered")
    options = options if options is not None else {"type": "object", "additionalProperties": False}
    check_schema(data)
    check_schema(options)
    _FORMS[form] = {"data": data, "options": options}


def forms() -> list[str]:
    return sorted(_FORMS)


def form_schema(form: str) -> dict | None:
    return _FORMS.get(form)


def forbidden(text: str) -> str | None:
    """The name of the first rule `text` breaks ("markup", "script", "url", "path", "control"), or None."""
    for rule, pattern in _FORBIDDEN:
        if pattern.search(text):
            return rule
    return None


def _scan(value, path: str, depth: int, errors: list[str]) -> None:
    if len(errors) >= 50:
        return
    if depth > MAX_DEPTH:
        errors.append(f"{path}: nested deeper than {MAX_DEPTH}")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            errors.append(f"{path}: string longer than {MAX_STRING} characters")
        rule = forbidden(value)
        if rule:
            errors.append(f"{path}: holds {rule}, which no field may hold")
    elif isinstance(value, float):
        if not math.isfinite(value):
            errors.append(f"{path}: not a finite number")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: a key is not a string")
                continue
            rule = forbidden(key) or ("long" if len(key) > MAX_STRING else None)
            if rule:
                errors.append(f"{path}: a key holds {rule}")
            _scan(item, f"{path}.{key}", depth + 1, errors)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _scan(item, f"{path}[{i}]", depth + 1, errors)
            if len(errors) >= 50:
                return
    elif value is not None and not isinstance(value, (bool, int)):
        errors.append(f"{path}: {type(value).__name__} is not a JSON value")


def _without_arrays(value):
    if isinstance(value, dict):
        return {k: _without_arrays(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return value


def canonical_json(value) -> str:
    """The one serialisation of a spec: sorted keys, compact, UTF-8, no NaN (a spec gives the same bytes anywhere)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def validate(value) -> list[str]:
    """Every violation of the envelope, its bounds, the content rule and the form's schema; [] means valid."""
    if not isinstance(value, dict):
        return ["$: a spec is a JSON object"]
    errors: list[str] = []
    _scan(value, "$", 0, errors)
    if errors:
        return errors
    try:
        total = len(canonical_json(value).encode("utf-8"))
        envelope = len(canonical_json({**value, "data": _without_arrays(value.get("data"))}).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return ["$: not serialisable as JSON"]
    if total > MAX_TOTAL_BYTES:
        errors.append(f"$: {total} bytes, over the limit of {MAX_TOTAL_BYTES}")
    if envelope > MAX_ENVELOPE_BYTES:
        errors.append(f"$: {envelope} bytes without data arrays, over the limit of {MAX_ENVELOPE_BYTES}")
    errors.extend(_schema_validate(value, ENVELOPE))
    if errors:
        return errors
    schema = _FORMS.get(value["spec"])
    if schema is None:
        return [f"$.spec: no form named {value['spec']!r} is registered"]
    errors.extend(_schema_validate(value["data"], schema["data"], "$.data"))
    errors.extend(_schema_validate(value["options"], schema["options"], "$.options"))
    return errors


@dataclass(frozen=True)
class Spec:
    """A validated specification. Build with `Spec.from_dict(d)` (raises Malformed listing the violations)."""

    spec: str
    title: str
    description: str
    data: dict
    options: dict = field(default_factory=dict)
    sources: tuple[str, ...] = ()
    made_by: dict = field(default_factory=lambda: {"generator": "", "version": 0})
    version: int = VERSION

    def to_dict(self) -> dict:
        return {"spec": self.spec, "version": self.version, "title": self.title, "description": self.description,
                "data": self.data, "options": self.options, "sources": list(self.sources),
                "made_by": dict(self.made_by)}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value) -> Spec:
        errors = validate(value)
        if errors:
            raise Malformed("the specification is not valid: " + "; ".join(errors[:10]))
        return cls(spec=value["spec"], title=value["title"], description=value["description"],
                   data=json.loads(canonical_json(value["data"])), options=json.loads(canonical_json(value["options"])),
                   sources=tuple(value["sources"]), made_by=dict(value["made_by"]), version=value["version"])

    @classmethod
    def from_json(cls, text: str | bytes) -> Spec:
        if len(text) > MAX_TOTAL_BYTES:
            raise Malformed("the specification is larger than allowed")
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            raise Malformed("the specification is not JSON") from None
        return cls.from_dict(value)


__all__ = ["ENVELOPE", "MAX_ENVELOPE_BYTES", "MAX_TOTAL_BYTES", "Spec", "VERSION", "canonical_json", "forbidden",
           "form_schema", "forms", "register_form", "validate"]
