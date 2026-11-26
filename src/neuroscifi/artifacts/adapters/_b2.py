"""Lane B2's adapters (images, sound, video containers, meshes, maps, arrays) and their registration.

`register_all()` installs B2's detection (`detect_b2.install()`) and registers every B2 adapter not yet registered;
it is idempotent. The main session wires this module into the registry's built-in list; until then nothing here
runs unless called.

`content_shape(representation)` is B2's one documented rule for the `ContentShape` of what its adapters return:
- an image (Raster) is `image`, has_space 2; an animation (Frames) is `image` + `sequence` with its duration;
- a signal is `sound` (and `series`) with `duration_s`;
- a mesh is `solid` when it is watertight (every edge shared by exactly two triangles), else `surface`; has_space 3;
- features are a `map` with has_space 2, and has_time when any feature carries timestamps (`times_s`);
- a table of MIDI notes (title "notes") is a `sequence` and `timeline` with has_time and its duration;
- anything else: the representation's own `shape()`.
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
    if isinstance(rep, T.Mesh):
        return T._shape(("solid",) if rep.closed() else ("surface",), n_items=len(rep.vertices), has_space=3,
                        text_chars=len(rep.text()))
    if isinstance(rep, T.Features):
        has_time = any(f.properties.get("times_s") for f in rep.features)
        kinds = ("map", "timeline") if has_time else ("map",)
        return T._shape(kinds, n_items=len(rep.features), has_time=has_time, has_space=2,
                        text_chars=len(rep.text()))
    if isinstance(rep, T.Signal):
        n, c = rep.samples.shape
        return T._shape(("sound", "series"), n_items=n, n_series=c, has_time=True, duration_s=n / rep.rate_hz,
                        text_chars=len(rep.text()))
    if isinstance(rep, T.Table) and rep.title == "notes":
        ends = [c for c in rep.columns if c.name == "end_s"]
        duration = float(ends[0].values.max()) if ends and rep.n_rows else 0.0
        return T._shape(("sequence", "timeline"), n_items=rep.n_rows, n_series=1, has_time=True,
                        duration_s=duration, text_chars=len(rep.text()))
    return rep.shape()


__all__ = ["MODULES", "adapters", "content_shape", "register_all", "unregister_all"]
