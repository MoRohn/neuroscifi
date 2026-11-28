"""Representations of lane B2 that also carry the artifact's own words (PNG text chunks, WAV LIST/INFO fields, MIDI
track names and lyrics, OBJ group names, ...).

`types.Raster`, `Frames`, `Signal` and `Mesh` return "" from `text()` (and a container `Tree` has no node values
to offer). These subclasses add one field, `words`
(untrusted, bounded to TEXT_MAX_CHARS), and return it from `text()`; everything else (kind, invariants,
`describe()`) is the parent's, so `isinstance(x, types.Raster)` holds and no consumer needs to know them. `shape()`
is the parent's too, with `text_chars` counting these words (ADR-072, integration: blessed where they are).
`NotesTable` is the table of a MIDI file's notes: its shape is a `sequence` and `timeline` lasting until the last
note ends (the one rule that was lane B2's `content_shape`, now where every consumer finds it, in `shape()`).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import types as T


def _words(value) -> str:
    return T._bounded_text([value]) if value else ""


def _with_words(shape: T.ContentShape, text: str) -> T.ContentShape:
    return T._shape(shape.kinds, shape.n_items, shape.n_series, shape.has_time, shape.has_space,
                    shape.has_parameters, shape.duration_s, len(text))


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

    def shape(self) -> T.ContentShape:
        return _with_words(super().shape(), self.text())


@dataclass(frozen=True, eq=False)
class Signal(T.Signal):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)

    def shape(self) -> T.ContentShape:
        return _with_words(super().shape(), self.text())


@dataclass(frozen=True, eq=False)
class Mesh(T.Mesh):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)

    def shape(self) -> T.ContentShape:
        return _with_words(super().shape(), self.text())


@dataclass(frozen=True, eq=False)
class Tree(T.Tree):
    words: str = ""

    def text(self) -> str:
        return _words(self.words)

    def shape(self) -> T.ContentShape:
        return _with_words(super().shape(), self.text())


@dataclass(frozen=True, eq=False)
class Table(T.Table):
    """A table whose text() also holds words that are not cells (a MIDI file's track names and lyrics)."""

    words: str = ""

    def text(self) -> str:
        return T._bounded_text([super().text(), self.words])

    def shape(self) -> T.ContentShape:
        return _with_words(super().shape(), self.text())


@dataclass(frozen=True, eq=False)
class NotesTable(Table):
    """A MIDI file's notes (columns start_s, end_s, duration_s, pitch, ...): a sequence of timed events."""

    def shape(self) -> T.ContentShape:
        ends = [c for c in self.columns if c.name == "end_s"]
        duration = float(np.nanmax(ends[0].values)) if ends and self.n_rows else 0.0
        return T._shape(("sequence", "timeline"), n_items=self.n_rows, n_series=1, has_time=True,
                        duration_s=duration, text_chars=len(self.text()))


__all__ = ["Frames", "Mesh", "NotesTable", "Raster", "Signal", "Table", "Tree"]
