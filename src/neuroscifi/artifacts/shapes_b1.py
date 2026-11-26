"""What a form planner is handed for one decoded artifact: `content_shapes(representation)` (lane B1).

The rule that maps a representation to a `ContentShape` lives in ONE place, each class's `shape()` in `types.py`
(lane K's): a table with a time column and numbers is a `series` with `has_time`; text beside numbers is
`categories`; a single numeric column is a `distribution`; a graph is a `network`; a document with headings is a
`hierarchy`; and so on. This module adds no second rule. It only answers the question `shape()` cannot: a `Bundle`
(an archive, a workbook, a database) is a hierarchy of members, but a planner choosing a form needs the shapes of
the members it would show, so `content_shapes` returns the bundle's own shape followed by the shape of each
decoded member (depth first, nested bundles included), at most `limit` in all. `forms.plan` takes a tuple of shapes.
"""
from __future__ import annotations

from .types import Bundle, ContentShape


def content_shapes(representation, limit: int = 64) -> tuple[ContentShape, ...]:
    out = [representation.shape()]
    stack = [representation] if isinstance(representation, Bundle) else []
    while stack and len(out) < limit:
        bundle = stack.pop()
        for member in bundle.members:
            if len(out) >= limit:
                break
            inner = member.inner
            if inner is None or not hasattr(inner, "shape"):
                continue
            out.append(inner.shape())
            if isinstance(inner, Bundle):
                stack.append(inner)
    return tuple(out)


__all__ = ["content_shapes"]
