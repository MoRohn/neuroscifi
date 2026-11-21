"""The JSON Schema subset NeuroSciFi uses for structured output and tool arguments, validated locally (the server's
guided decoding is a convenience, never the guarantee).

Supported keywords: type (string or list), properties, required, additionalProperties (bool), items, enum, const,
minItems, maxItems, minLength, maxLength, minimum, maximum, pattern (NaN and the infinities never validate). Anything else in a schema is refused when the
schema is checked (`check_schema`), so a schema never silently validates less than it says.
"""
from __future__ import annotations

import math
import re

SUPPORTED = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "minItems",
             "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern", "description", "title"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def check_schema(schema: dict, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: schema must be an object")
    extra = set(schema) - SUPPORTED
    if extra:
        raise ValueError(f"{path}: unsupported schema keyword(s): {', '.join(sorted(extra))}")
    for name, sub in (schema.get("properties") or {}).items():
        check_schema(sub, f"{path}.{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]")


def _is_type(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":   # NaN and the infinities are not JSON numbers
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    expected = _TYPES.get(name)
    if expected is None:
        return False
    if expected is dict or expected is list or expected is str:
        return isinstance(value, expected)
    return type(value) is expected


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Every violation as 'path: message'; [] means valid."""
    errors: list[str] = []
    if isinstance(value, float) and not math.isfinite(value):
        return [f"{path}: not a finite number"]
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is_type(value, k) for k in kinds):
            return [f"{path}: expected {' or '.join(kinds)}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match {schema['pattern']!r}")
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}: missing {name}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    errors.append(f"{path}: unexpected property {name}")
        for name, sub in props.items():
            if name in value:
                errors.extend(validate(value[name], sub, f"{path}.{name}"))
    return errors


__all__ = ["check_schema", "validate"]
