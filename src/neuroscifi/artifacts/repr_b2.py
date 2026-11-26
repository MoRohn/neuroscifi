"""Representations of lane B2 that also carry the artifact's own words (PNG text chunks, WAV LIST/INFO fields, MIDI
track names and lyrics, OBJ group names, ...).

`types.Raster`, `Frames`, `Signal` and `Mesh` return "" from `text()`. These subclasses add one field, `words`
(untrusted, bounded to TEXT_MAX_CHARS), and return it from `text()`; everything else (kind, invariants,
`describe()`, `shape()`) is the parent's, so `isinstance(x, types.Raster)` holds and no consumer needs to know them.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import types as T


def _words(value) -> str:
    return T._bounded_text([value]) if value else ""


@dataclass(frozen=True, eq=False)
class Raster(T.Raster):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)

    def shape(self) -> T.ContentShape:
        base = super().shape()
        return T._shape(base.kinds, base.n_items, base.n_series, base.has_time, base.has_space,
                        base.has_parameters, base.duration_s, len(self.text()))


@dataclass(frozen=True, eq=False)
class Frames(T.Frames):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)


@dataclass(frozen=True, eq=False)
class Signal(T.Signal):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)


@dataclass(frozen=True, eq=False)
class Mesh(T.Mesh):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)


__all__ = ["Frames", "Mesh", "Raster", "Signal"]
