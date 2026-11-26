"""Lane G2's forms, registered in one place: their spec schemas (spec.register_form) and their generators
(make.register). Importing this module registers them once.

Form names use underscores (`sound_sonify`, not `sound.sonify`): the envelope's form name is a lower-case word
(`spec.FORM_NAME`), which has no dots.
"""
from __future__ import annotations

from .. import spec as _spec
from ..spec import Spec
from ..types import Limits
from . import Generator, Made, register
from . import sound

GENERATOR_VERSION = 1


class _Form(Generator):
    def __init__(self, name: str, media_type, render):
        self.name = name
        self.version = GENERATOR_VERSION
        self._media_type = media_type
        self._render = render

    def make(self, spec: Spec, limits: Limits) -> Made:
        data, notes = self._render(spec, limits)
        media = self._media_type(spec) if callable(self._media_type) else self._media_type
        return Made(data, media, spec, tuple(notes))


FORMS: dict[str, tuple[dict, Generator]] = {}
REPAIRS = {}


def _add(form: str, schemas: dict, generator: Generator, repair=None) -> None:
    _spec.register_form(form, data=schemas["data"], options=schemas["options"])
    register(form, generator)
    FORMS[form] = (schemas, generator)
    if repair is not None:
        REPAIRS[form] = repair


for _form in ("sound_tone", "sound_sequence", "sound_sonify"):
    _add(_form, sound.SCHEMAS[_form], _Form("g2.sound", "audio/wav", sound.render), sound.repair)


def repair(value: dict, problem) -> dict | None:
    """The bounded repair rule of a lane G2 form, or None when there is none for this problem (DESIGN §6: a repair
    never changes the data's meaning silently; it is recorded in `options.history`, at most three rounds)."""
    rule = REPAIRS.get(value.get("spec")) if isinstance(value, dict) else None
    return rule(value, problem) if rule else None


__all__ = ["FORMS", "GENERATOR_VERSION", "repair"]
