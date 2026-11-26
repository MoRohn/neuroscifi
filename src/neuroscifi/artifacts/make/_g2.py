"""Lane G2's forms, registered in one place: their spec schemas (spec.register_form) and their generators
(make.register). Importing this module registers them once.

Every form lane G2 makes is in the one table FORM_TABLE below (dotted names, as the planner and the benchmark lanes
use them; `spec.FORM_NAME` allows one dot).
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


_SOUND = _Form("g2.sound", "audio/wav", sound.render)
_SOLIDS = _Form("g2.solids", solids.media_type, solids.render)
_RASTER = _Form("g2.raster", raster.media_type, raster.render)
_EXPLORABLE = _Form("g2.explorable", explorable.MEDIA_TYPE, explorable.render)

# form -> (schemas of its data and options, generator, bounded repair rule or None)
FORM_TABLE = {
    "sound.tone": (sound.SCHEMAS["sound.tone"], _SOUND, sound.repair),
    "sound.sequence": (sound.SCHEMAS["sound.sequence"], _SOUND, sound.repair),
    "sound.sonify": (sound.SCHEMAS["sound.sonify"], _SOUND, sound.repair),
    "solid.box": (solids.SCHEMAS["solid.box"], _SOLIDS, None),
    "solid.extrude": (solids.SCHEMAS["solid.extrude"], _SOLIDS, None),
    "solid.revolve": (solids.SCHEMAS["solid.revolve"], _SOLIDS, None),
    "surface.grid": (solids.SCHEMAS["surface.grid"], _SOLIDS, None),
    "solid.points": (solids.SCHEMAS["solid.points"], _SOLIDS, None),
    "image.raster": (raster.SCHEMAS["image.raster"], _RASTER, None),
    "image.apng": (raster.SCHEMAS["image.apng"], _RASTER, None),
    "explorable.model": (explorable.SCHEMAS["explorable.model"], _EXPLORABLE, None),
    "explorable.steps": (explorable.SCHEMAS["explorable.steps"], _EXPLORABLE, None),
    "solid.view": (explorable.SCHEMAS["solid.view"], _EXPLORABLE, None),
}

for _form, (_schemas, _generator, _repair) in FORM_TABLE.items():
    _add(_form, _schemas, _generator, _repair)


def repair(value: dict, problem) -> dict | None:
    """The bounded repair rule of a lane G2 form, or None when there is none for this problem (DESIGN §6: a repair
    never changes the data's meaning silently; it is recorded in `options.history`, at most three rounds)."""
    rule = REPAIRS.get(value.get("spec")) if isinstance(value, dict) else None
    return rule(value, problem) if rule else None


__all__ = ["FORMS", "GENERATOR_VERSION", "repair"]
