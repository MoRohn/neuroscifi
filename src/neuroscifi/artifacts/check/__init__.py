"""The validator registry (DESIGN §6): bytes -> verdict, by independent code paths.

`register(media_type, validator)`, `check(data, media_type, spec=None, source=None) -> Verdict`. A validator is a
callable `validator(data, media_type, spec, source) -> Verdict` that re-parses the bytes with its own reader (never
by calling a generator or reading its internals). `check` runs every validator registered for the media type and
merges their findings; `ok` is decided by technical findings only (every technical finding passed). Bytes no
validator is registered for are not ok: unchecked is never reported as checked. A validator that raises becomes a
failed technical finding naming only the exception's type.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..types import ArtifactError


@dataclass(frozen=True)
class Finding:
    rule: str                   # stable machine word: "svg_allowed_elements", "values_recovered", ...
    passed: bool
    detail: str                 # a plain sentence; never quotes untrusted content at length
    measure: float | None = None   # a measured number where the rule has one (share of values recovered, ...)


@dataclass(frozen=True)
class Verdict:
    technical: tuple[Finding, ...]
    style: tuple[Finding, ...]
    ok: bool

    @classmethod
    def of(cls, technical=(), style=()) -> Verdict:
        technical, style = tuple(technical), tuple(style)
        return cls(technical, style, bool(technical) and all(f.passed for f in technical))


Validator = Callable[..., Verdict]
_VALIDATORS: dict[str, list[tuple[str, Validator]]] = {}


def _media(media_type: str) -> str:
    return (media_type or "").split(";")[0].strip().lower()


def register(media_type: str, validator: Validator, *, name: str | None = None) -> Validator:
    name = name or getattr(validator, "__name__", "validator")
    entries = _VALIDATORS.setdefault(_media(media_type), [])
    if any(n == name for n, _ in entries):
        raise ValueError(f"a validator named {name!r} is already registered for {media_type!r}")
    entries.append((name, validator))
    return validator


def unregister(media_type: str, name: str) -> None:
    key = _media(media_type)
    _VALIDATORS[key] = [(n, v) for n, v in _VALIDATORS.get(key, []) if n != name]
    if not _VALIDATORS[key]:
        del _VALIDATORS[key]


def catalog() -> list[dict]:
    return [{"media_type": m, "validator": n} for m in sorted(_VALIDATORS) for n, _ in _VALIDATORS[m]]


def check(data: bytes, media_type: str, spec=None, source=None) -> Verdict:
    entries = _VALIDATORS.get(_media(media_type), [])
    if not entries:
        return Verdict.of((Finding("validator_available", False, f"no validator is registered for {_media(media_type)}"),))
    technical: list[Finding] = []
    style: list[Finding] = []
    for name, validator in entries:
        try:
            verdict = validator(bytes(data), media_type, spec, source)
            if not isinstance(verdict, Verdict):
                raise TypeError("not a Verdict")
        except ArtifactError as error:
            technical.append(Finding("readable", False, f"{name}: {error.reason}: {error}"))
            continue
        except Exception as error:
            technical.append(Finding("readable", False, f"{name} could not read the bytes ({type(error).__name__})"))
            continue
        technical.extend(verdict.technical)
        style.extend(verdict.style)
    return Verdict.of(technical, style)


__all__ = ["Finding", "Validator", "Verdict", "catalog", "check", "register", "unregister"]
