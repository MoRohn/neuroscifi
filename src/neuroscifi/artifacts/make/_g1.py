"""Lane G1's forms: their schemas (spec.register_form) and their generators (make.register), in one table.

Importing this module registers every form below once. The registry module (`make/__init__.py`) imports it at the
merge; nothing else does. Form names use underscores because the specification's form-name pattern has no dot.
"""
from __future__ import annotations

from .. import spec as _spec
from ..types import Limits, Malformed, Refused
from . import Generator, Made, register
from .svg_base import Fit

VERSION = 1


class G1Generator(Generator):
    """A fixed generator: `fn(spec) -> (text, notes)`, standard library and numpy only, deterministic."""

    version = VERSION

    def __init__(self, form: str, fn, media_type: str):
        self.form, self.fn, self.media_type = form, fn, media_type
        self.name = "g1." + form

    def make(self, spec, limits: Limits = Limits()) -> Made:
        try:
            text, notes = self.fn(spec)
        except Fit as error:
            raise Malformed(f"cannot make a readable {self.form}: {error}") from None
        except RecursionError:
            raise Refused(f"the {self.form} specification nests too deeply", rule="max_depth") from None
        return Made(text.encode("utf-8"), self.media_type, spec, tuple(notes))


def _table():
    from . import charts
    return [(form, fn, data, charts.OPTIONS, "image/svg+xml") for form, (fn, data) in charts.GENERATORS.items()]


FORMS: dict[str, str] = {}      # form -> media type of what its generator makes


def register_all() -> None:
    for form, fn, data, options, media_type in _table():
        if form in FORMS:
            continue
        if _spec.form_schema(form) is None:
            _spec.register_form(form, data=data, options=options)
        register(form, G1Generator(form, fn, media_type))
        FORMS[form] = media_type


register_all()

__all__ = ["FORMS", "G1Generator", "VERSION", "register_all"]
