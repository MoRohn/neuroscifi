"""Data files: exact, round-trippable CSV and JSON of a table (the `tables.DATA` schema).

CSV dialect ("neuroscifi.csv/1", stated so a reader can parse it back exactly): RFC 4180; UTF-8 without a byte
order mark; comma separator; CRLF line ends, one after every record including the last; one header record.
- Header cell: the column's name, or "name [unit]" when it has a unit (a name that itself ends in "]" and has no
  unit is written "name []", so the header always splits back unambiguously).
- A field is quoted (with inner quotes doubled) when it holds a comma, a quote, CR or LF, starts or ends with a
  space, or is an empty text; a missing value is an empty UNquoted field. So "" (quoted) is empty text and
  nothing is missing.
- number: Python's shortest round-trip decimal (may use an exponent: "1e-05"); integer: digits;
  boolean: "true" / "false"; time: ISO 8601 UTC, exact ("2026-03-01T06:30:00.25Z");
- text: as is, except that a text starting with = + - @ TAB CR or an apostrophe gets one apostrophe in front (so
  a spreadsheet never runs it as a formula); a reader removes exactly one leading apostrophe when the character
  after it is one of those.
Column kinds are not in the CSV: a reader takes them from the specification (or uses the JSON file).

JSON dialect ("neuroscifi.table/1"): {"dialect", "title", "description", "made_by", "columns": [{"name", "kind",
"unit", "values"}]}; numbers as JSON numbers (shortest round-trip), times as numbers of seconds since
1970-01-01T00:00:00Z, missing values as null; compact, UTF-8, keys in the order shown.
"""
from __future__ import annotations

import json

from .tables import DATA, OPTIONS, check_table, iso_time

CSV_DIALECT = "neuroscifi.csv/1"
JSON_DIALECT = "neuroscifi.table/1"
_GUARD = ("=", "+", "-", "@", "\t", "\r", "'")


def _quote(text: str) -> str:
    if text == "" or any(ch in text for ch in ',"\r\n') or text[0] == " " or text[-1] == " ":
        return '"' + text.replace('"', '""') + '"'
    return text


def csv_header(column) -> str:
    name, unit = str(column["name"]), column.get("unit")
    if unit:
        return f"{name} [{unit}]"
    return f"{name} []" if name.endswith("]") else name


def csv_cell(kind: str, value) -> str:
    if value is None:
        return ""
    if kind == "number":
        return repr(float(value))
    if kind == "integer":
        return str(int(value))
    if kind == "boolean":
        return "true" if value else "false"
    if kind == "time":
        return iso_time(value)
    text = str(value)
    if text.startswith(_GUARD):
        text = "'" + text
    return _quote(text)


def make_csv(spec):
    data = spec.data
    n = check_table(data)
    cols = data["columns"]
    lines = [",".join(_quote(csv_header(c)) for c in cols)]
    for r in range(n):
        lines.append(",".join(csv_cell(c["kind"], c["values"][r]) for c in cols))
    return "\r\n".join(lines) + "\r\n", ()


def make_json(spec):
    data = spec.data
    check_table(data)
    cols = []
    for c in data["columns"]:
        values = c["values"]
        if c["kind"] in ("number", "time"):
            values = [None if v is None else float(v) for v in values]
        cols.append({"name": c["name"], "kind": c["kind"], "unit": c.get("unit"), "values": values})
    doc = {"dialect": JSON_DIALECT, "title": spec.title, "description": spec.description,
           "made_by": "NeuroSciFi, from a specification; this file is made by the system and is not evidence",
           "columns": cols}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", ()


__all__ = ["CSV_DIALECT", "DATA", "JSON_DIALECT", "OPTIONS", "csv_cell", "csv_header", "make_csv", "make_json"]
