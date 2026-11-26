"""JSON -> Tree (lane K's pattern adapter), and `as_table(tree)`: the documented helper that offers an array of flat
records as a Table.

Tree layout: every JSON value is a Node. `attrs["type"]` is "object", "array", "string", "number", "integer",
"boolean" or "null"; an object member's node is named by its key, an array item's by its index ("0", "1", ...), the
root is named "$"; scalars carry their value in `value`, containers have `value=None` and their members as
children in document order.

Bounds: the input is already within max_bytes; an upper bound on the element count (structural characters) is
checked BEFORE parsing against max_elements; nesting deeper than the parser allows is refused (json raises
RecursionError, caught); the Tree holds at most max_elements nodes (partial beyond, with a note). The non-standard
constants NaN and Infinity are read and noted. Strings are the artifact's own words and untrusted.
"""
from __future__ import annotations

import json
import math

from ..safe import Deadline
from ..types import Decoded, Detected, Limits, Malformed, Node, Refused, Table, Tree
from . import Adapter
from .plain import decode_text

TABLE_MAX_COLUMNS = 4096


def _type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def to_tree(value, limits: Limits, deadline: Deadline | None = None) -> tuple[Tree, bool]:
    """(Tree, partial). Built iteratively (post-order), so depth never meets Python's recursion limit."""
    budget = [limits.max_elements]
    partial = [False]

    def items(v):
        if isinstance(v, dict):
            return list(v.items())
        return [(str(i), x) for i, x in enumerate(v)]

    # frame: [name, value, pending item list, index, built children]
    stack = [["$", value, None, 0, []]]
    budget[0] -= 1
    result = None
    steps = 0
    while stack:
        frame = stack[-1]
        name, v = frame[0], frame[1]
        if not isinstance(v, (dict, list)):
            stack.pop()
            node = Node(name, v, {"type": _type(v)}, ())
        else:
            if frame[2] is None:
                frame[2] = items(v)
            if frame[3] < len(frame[2]) and budget[0] > 0:
                key, child = frame[2][frame[3]]
                frame[3] += 1
                budget[0] -= 1
                stack.append([str(key), child, None, 0, []])
                steps += 1
                if deadline is not None and steps % 8192 == 0:
                    deadline.check("building the JSON tree")
                continue
            if frame[3] < len(frame[2]):
                partial[0] = True
            stack.pop()
            node = Node(name, None, {"type": _type(v), "length": str(len(frame[2]))}, tuple(frame[4]))
        if stack:
            stack[-1][4].append(node)
        else:
            result = node
    return Tree(result), partial[0]


def element_bound(data: bytes) -> int:
    """An upper bound on the number of JSON values in `data` (every value but the first follows ',' or ':' or opens
    a container), computed without parsing."""
    return data.count(b",") + data.count(b":") + data.count(b"[") + data.count(b"{") + 1


def as_table(tree: Tree, *, title: str | None = None) -> Table | None:
    """The records as a Table when the root is an array of objects whose members are all scalars; else None.
    The table is built by lane B1's `tables.table_from_records` (the one set of rules for JSON records: columns
    in first-seen order, a record without a key is a missing cell, kinds by the same string rules as delimited
    text), so a JSON array of records reads the same here and as NDJSON."""
    from .tables import table_from_records
    root = tree.root
    if root.attrs.get("type") != "array" or not root.children:
        return None
    keys: dict[str, None] = {}
    for record in root.children:
        if record.attrs.get("type") != "object":
            return None
        for cell in record.children:
            if cell.children or cell.attrs.get("type") in ("object", "array"):
                return None
            keys.setdefault(cell.name, None)
            if len(keys) > TABLE_MAX_COLUMNS:
                return None
    records = [{c.name: c.value for c in record.children} for record in root.children]
    return table_from_records(records, title=title)[0]


class JsonTree(Adapter):
    name = "json"
    formats = ("json", "geojson", "ipynb")
    families = ("tree", "geo", "notebook")

    def decode(self, data: bytes, detected: Detected, limits: Limits) -> Decoded:
        deadline = Deadline(limits.max_seconds)
        bound = element_bound(data)
        if bound > limits.max_elements:
            raise Refused(f"the JSON may hold up to {bound} values, more than the limit of {limits.max_elements}",
                          rule="max_elements")
        text, enc = decode_text(data)
        constants = []

        def constant(word):
            constants.append(word)
            return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[word]
        try:
            value = json.loads(text, parse_constant=constant)
        except RecursionError:
            raise Refused("the JSON nests deeper than can be read", rule="json_depth") from None
        except ValueError as error:
            where = getattr(error, "pos", None)
            raise Malformed("the bytes are not valid JSON" + (f" (at character {where})" if where is not None else "")) \
                from None
        deadline.check()
        tree, partial = to_tree(value, limits, deadline)
        notes = [] if enc == "utf-8" else [f"read as {enc.upper()} (JSON should be UTF-8)"]
        if constants:
            notes.append("non-standard constants (NaN or Infinity) were read as numbers")
        if partial:
            notes.append(f"the tree stops at {limits.max_elements} nodes (max_elements)")
        if detected.format in ("geojson", "ipynb"):
            notes.append(f"read as a JSON tree: no {detected.format}-specific representation is built here")
        if isinstance(value, list) and value and all(isinstance(r, dict) for r in value[:1]) and \
                as_table(tree) is not None:
            notes.append(f"an array of {len(value)} flat records: adapters.jsontree.as_table() gives it as a Table")
        return Decoded(detected, tree, tuple(notes), partial)


__all__ = ["JsonTree", "as_table", "element_bound", "to_tree"]
