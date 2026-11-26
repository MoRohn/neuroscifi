"""The generator registry (DESIGN §6): spec + data -> bytes, by fixed audited generators only.

`register(form, generator)`, `catalog()`, `make(spec, limits) -> Made`. A generator is a `Generator` (name, version,
needs, `available()`, `make(spec, limits) -> Made`). `make` validates the spec (spec.validate) before anything is
made, picks the generator the spec's `made_by` names if it is registered for the form, else the first available one,
checks the result's type and size, and returns a `Made` whose spec records the generator that really made it.
Generators must be deterministic: the same spec gives the same bytes on every host (lanes G1/G2 test it).
Nothing made here is evidence (COMMON, ADR-049, ADR-071).
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, replace

from ..spec import Spec
from ..spec import validate as validate_spec
from ..types import ArtifactError, Limits, Malformed, Refused, Unsupported


@dataclass(frozen=True)
class Made:
    bytes: bytes
    media_type: str
    spec: Spec
    notes: tuple[str, ...] = ()


class Generator:
    name: str = ""
    version: int = 1
    needs: tuple[str, ...] = ()

    def available(self) -> tuple[bool, str]:
        missing = [m for m in self.needs if importlib.util.find_spec(m) is None]
        if missing:
            return False, "needs " + ", ".join(missing) + " (not installed)"
        return True, "ready"

    def make(self, spec: Spec, limits: Limits) -> Made:
        raise NotImplementedError


_GENERATORS: dict[str, list[Generator]] = {}


def register(form: str, generator: Generator) -> Generator:
    if not isinstance(getattr(generator, "name", None), str) or not generator.name:
        raise ValueError("a generator needs a name")
    existing = _GENERATORS.setdefault(form, [])
    if any(g.name == generator.name for g in existing):
        raise ValueError(f"a generator named {generator.name!r} is already registered for {form!r}")
    existing.append(generator)
    return generator


def unregister(form: str, name: str) -> None:
    _GENERATORS[form] = [g for g in _GENERATORS.get(form, []) if g.name != name]
    if not _GENERATORS[form]:
        del _GENERATORS[form]


def _available(generator: Generator) -> tuple[bool, str]:
    try:
        ok, reason = generator.available()
        return bool(ok), str(reason)
    except Exception as error:
        return False, f"availability check failed ({type(error).__name__})"


def catalog() -> list[dict]:
    """The live list of what can be made here: one row per (form, generator)."""
    rows = []
    for form in sorted(_GENERATORS):
        for g in _GENERATORS[form]:
            ok, reason = _available(g)
            rows.append({"form": form, "generator": g.name, "version": g.version, "available": ok, "reason": reason})
    return rows


def make(spec: Spec | dict, limits: Limits = Limits()) -> Made:
    value = spec.to_dict() if isinstance(spec, Spec) else spec
    errors = validate_spec(value)
    if errors:
        raise Malformed("the specification is not valid: " + "; ".join(errors[:10]))
    spec = Spec.from_dict(value)
    candidates = _GENERATORS.get(spec.spec, [])
    if not candidates:
        raise Unsupported(f"nothing here makes the form {spec.spec!r}", needs=(f"a generator for {spec.spec}",),
                          reason="missing_capability")
    named = [g for g in candidates if g.name == spec.made_by.get("generator")]
    usable = [g for g in named + candidates if _available(g)[0]]
    if not usable:
        raise Unsupported(f"no generator for {spec.spec!r} is available here", reason="missing_capability",
                          needs=tuple(n for g in candidates for n in g.needs))
    generator = usable[0]
    try:
        made = generator.make(spec, limits)
    except ArtifactError:
        raise
    except Exception as error:
        raise Malformed(f"the {generator.name} generator failed ({type(error).__name__})") from None
    if not isinstance(made, Made) or not isinstance(made.bytes, bytes):
        raise Malformed(f"the {generator.name} generator returned {type(made).__name__}, not Made")
    if len(made.bytes) > limits.max_bytes:
        raise Refused(f"the {generator.name} generator produced more than {limits.max_bytes} bytes",
                      rule="max_bytes", reason="too_large")
    return replace(made, spec=replace(spec, made_by={"generator": generator.name, "version": int(generator.version)}),
                   notes=tuple(made.notes))


__all__ = ["Generator", "Made", "catalog", "make", "register", "unregister"]
