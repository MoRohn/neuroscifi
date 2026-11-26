"""Lane G2's forms, registered in one place: their spec schemas (spec.register_form) and their generators
(make.register). Importing this module registers them once.

Form names use underscores (`sound_sonify`, not `sound.sonify`): the envelope's form name is a lower-case word
(`spec.FORM_NAME`), which has no dots.
"""
from __future__ import annotations

from dataclasses import replace

from .. import spec as _spec
from ..spec import Spec
from ..types import Limits
from . import Generator, Made, register
from . import explorable, raster, solids, sound

GENERATOR_VERSION = 1


class _Form(Generator):
    def __init__(self, name: str, media_type, render):
        self.name = name
        self.version = GENERATOR_VERSION
        self._media_type = media_type
        self._render = render

    def make(self, spec: Spec, limits: Limits) -> Made:
        # the spec a document embeds is the one the registry returns: stamped with the generator that made it
        spec = replace(spec, made_by={"generator": self.name, "version": self.version})
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
for _form in ("solid_box", "solid_extrude", "solid_revolve", "surface_grid", "solid_points"):
    _add(_form, solids.SCHEMAS[_form], _Form("g2.solids", solids.media_type, solids.render))
for _form in ("image_raster", "image_apng"):
    _add(_form, raster.SCHEMAS[_form], _Form("g2.raster", raster.media_type, raster.render))
for _form in ("explorable_model", "explorable_steps", "solid_view"):
    _add(_form, explorable.SCHEMAS[_form], _Form("g2.explorable", explorable.MEDIA_TYPE, explorable.render))


def repair(value: dict, problem) -> dict | None:
    """The bounded repair rule of a lane G2 form, or None when there is none for this problem (DESIGN §6: a repair
    never changes the data's meaning silently; it is recorded in `options.history`, at most three rounds)."""
    rule = REPAIRS.get(value.get("spec")) if isinstance(value, dict) else None
    return rule(value, problem) if rule else None


__all__ = ["FORMS", "GENERATOR_VERSION", "repair"]
