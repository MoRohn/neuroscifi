"""Lane B2's adapters (images, sound, video containers, meshes, maps, arrays) and their registration.

`register_all()` installs B2's detection (`detect_b2.install()`) and registers every B2 adapter not yet registered;
it is idempotent. The main session wires this module into the registry's built-in list; until then nothing here
runs unless called.

`content_shape(representation)` is the representation's own `shape()`: B2's shape rules (an image, an animation
with its duration, a sound with its duration, a solid when watertight else a surface, a map that is also a timeline
when features carry timestamps, a MIDI notes table as a sequence and timeline) live in `types.py` and `repr_b2.py`,
so that every consumer (the store, `nsf form plan`, B1's `content_shapes`) gets the same shape (ADR-072, integration).
"""
from __future__ import annotations

import importlib

from .. import types as T
from . import _ADAPTERS, register

MODULES = ("png", "netpbm", "bmp", "gif", "image_headers", "svg", "wav", "audio_headers", "midi",
           "video_containers", "meshes", "gltf", "geo", "arrays")


def adapters() -> list:
    out = []
    for name in MODULES:
        try:
            module = importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError as error:
            if error.name == f"{__package__}.{name}":
                continue      # a module of this lane that is not written yet
            raise
        out.extend(module.ADAPTERS)
    return out


def register_all() -> list[str]:
    """Install detection and register B2's adapters; returns the names registered now."""
    from .. import detect_b2
    detect_b2.install()
    taken = {a.name for a in _ADAPTERS}
    done = []
    for adapter in adapters():
        if adapter.name not in taken:
            register(adapter)
            done.append(adapter.name)
    return done


def unregister_all() -> None:
    """Undo `register_all()` (tests use it so that B2 does not change other lanes' tests)."""
    from .. import detect_b2
    from . import unregister
    for adapter in adapters():
        unregister(adapter.name)
    detect_b2.uninstall()


def content_shape(rep) -> T.ContentShape:
    return rep.shape()


__all__ = ["MODULES", "adapters", "content_shape", "register_all", "unregister_all"]
