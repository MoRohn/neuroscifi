"""Delimited text and line-delimited JSON to `Table` (lane B1). Also the column rules every B1 table shares.

Formats: CSV, TSV (and `;` or `|` delimited text detected as CSV), NDJSON / JSON Lines. `table_from_records` turns
a JSON array of flat records into a `Table` for whichever adapter reads the JSON.

The stated rules (each has a test):

- Delimiter: on the first 64 KiB (at most 64 lines) each of `,` `;` TAB `|` is tried with the csv module's quoting
  (`"`, doubled to escape); the winner gives at least 2 fields on the largest share of lines, then the most fields.
  A declared TSV always uses TAB.
- Missing cell: empty, or (case-insensitive, after trimming) one of `na`, `n/a`, `null`, `none`, `nan`, `-`.
- Column kinds, on the non-missing cells of the whole column:
  boolean when every cell is true/false or yes/no; integer when every cell is a plain signed integer that fits in
  64 bits; number when every cell is a plain decimal (`1`, `-2.5`, `.5`, `1e3`); number with thousands separators
  when every cell is a plain integer or `1,234,567(.89)` and at least one has a comma, unless the delimiter is `;`;
  number with decimal commas when every cell is an integer, `3,25` or `1.234,5` and at least one has a comma;
  time when every cell is ISO 8601 (`2024-05-01`, `2024-05-01T10:00:00Z`, `2024-05-01 10:00`), `YYYY/MM/DD`,
  `DD.MM.YYYY`, or `DD/MM/YYYY` / `MM/DD/YYYY` when some cell settles the order (a first field over 12 means day
  first, a second field over 12 means month first; if none does the column stays text and a note says why);
  times without a zone are taken as UTC (noted); otherwise text.
- Header row: the first row is a header when every cell in it is non-missing and none is a number, boolean or
  time, AND either some column below it has a non-text kind, or every first-row cell is distinct and appears
  nowhere else in its own column. A single row is data. Without a header, columns are `column_1`, `column_2`, ...
- Unit: a header `price (EUR)` or `price [EUR]` gives the name `price` and the unit `EUR`.
- Bounds: cells count against `max_elements` and (cell characters + 160 bytes each) against `max_decoded_bytes`;
  reaching either stops reading, marks the result partial and says after which row.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime, timezone

import numpy as np

from ..safe import Deadline
from ..safe_b1 import DecodeBudget, decode_text, injection_notes
from ..types import Column, Decoded, Detected, Limits, Malformed, Refused, Table
from . import Adapter

MISSING = frozenset({"", "na", "n/a", "null", "none", "nan", "-"})
DELIMITERS = (",", ";", "\t", "|")
SNIFF_CHARS = 64 << 10
SNIFF_LINES = 64
CELL_OVERHEAD = 160            # bytes charged per cell beyond its characters (measured about 150 with its column)
MAX_COLUMNS = 16384
JSON_MAX_DEPTH = 64

_PLAIN = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_INT = re.compile(r"^[+-]?\d+$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_DEC_COMMA = re.compile(r"^[+-]?\d+,\d+$")
_DEC_COMMA_DOTS = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")
_BOOL = {"true": True, "false": False, "yes": True, "no": False}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$")
_YMD_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_DMY_DOT = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_XY_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_UNIT = re.compile(r"^(.*?\S)\s*[\(\[]\s*([^()\[\]]{1,24}?)\s*[\)\]]\s*$")
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_INT64 = 2 ** 63


class TimeValue(float):
    """A cell already known to be a time (seconds since the epoch, UTC), as typed formats deliver it."""


def is_missing(cell) -> bool:
    return cell is None or (isinstance(cell, str) and cell.strip().lower() in MISSING) or (
        isinstance(cell, float) and math.isnan(cell))


def split_unit(header: str) -> tuple[str, str | None]:
    """`"price (EUR)"` -> ("price", "EUR"); a header without a bracketed suffix has no unit."""
    m = _UNIT.match(header.strip()) if len(header) <= 200 else None
    if m:
        return m.group(1), m.group(2)
    return header.strip(), None


# ---------------------------------------------------------------------------------------------------------------
# Times


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso(cell: str) -> tuple[float, bool] | None:
    """(seconds, had_zone) for an ISO 8601 date or date-time, else None."""
    if not _ISO.match(cell):
        return None
    text = cell.replace(" ", "T", 1)
    if "." in text:                       # fromisoformat takes at most 6 fractional digits
        head, frac = text.split(".", 1)
        digits = re.match(r"\d+", frac).group(0)
        text = head + "." + digits[:6].ljust(6, "0") + frac[len(digits):]
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _epoch(dt), dt.tzinfo is not None


def _hms(m, i):
    return int(m.group(i) or 0), int(m.group(i + 1) or 0), int(m.group(i + 2) or 0)


def _times(cells: list[str]) -> tuple[list[float], list[str]] | None:
    """Seconds for every cell when the whole column is one stated date pattern, else None. Notes on the side."""
    notes: list[str] = []
    if all(_ISO.match(c) for c in cells):
        out, zoned = [], 0
        for c in cells:
            parsed = _iso(c)
            if parsed is None:
                return None
            out.append(parsed[0])
            zoned += parsed[1]
        if zoned < len(cells):
            notes.append("times without a zone are taken as UTC")
        return out, notes
    for pattern, order in ((_YMD_SLASH, "ymd"), (_DMY_DOT, "dmy")):
        matches = [pattern.match(c) for c in cells]
        if all(matches):
            try:
                out = []
                for m in matches:
                    if order == "ymd":
                        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    else:
                        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    out.append(_epoch(datetime(y, mo, d, *_hms(m, 4))))
            except ValueError:
                return None
            return out, ["times without a zone are taken as UTC"]
    matches = [_XY_SLASH.match(c) for c in cells]
    if all(matches):
        first_big = any(int(m.group(1)) > 12 for m in matches)
        second_big = any(int(m.group(2)) > 12 for m in matches)
        if first_big == second_big:
            return None if first_big else ([], ["dates written n/n/yyyy could be day-first or month-first and no "
                                                "value settles it: kept as text"])
        try:
            out = []
            for m in matches:
                a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                d, mo = (a, b) if first_big else (b, a)
                out.append(_epoch(datetime(y, mo, d, *_hms(m, 4))))
        except ValueError:
            return None
        return out, ["day-first order settled by a day over 12" if first_big else
                     "month-first order settled by a day over 12", "times without a zone are taken as UTC"]
    return None


# ---------------------------------------------------------------------------------------------------------------
# Columns


def _scalar_kind(cell: str) -> str:
    """The kind a single cell would have on its own (used by the header rule)."""
    low = cell.strip().lower()
    if low in _BOOL:
        return "boolean"
    if _PLAIN.match(cell) or _THOUSANDS.match(cell) or _DEC_COMMA.match(cell):
        return "number"
    if _XY_SLASH.match(cell.strip()):
        return "time"                     # a date either way round
    times = _times([cell.strip()])
    return "time" if times and times[0] else "text"


def _int64(text: str) -> bool:
    digits = text.lstrip("+-")
    return bool(_INT.match(text)) and (len(digits) < 19 or (len(digits) == 19 and abs(int(text)) < _INT64))


def _numbers(cells: list[str], delimiter: str | None) -> tuple[list[str], bool, str | None] | None:
    """(normalised decimal strings, all_int64, note) when the whole column is numbers under one stated convention."""
    values, note = None, None
    if all(_PLAIN.match(c) for c in cells):
        values = cells
    elif delimiter != ";" and any(_THOUSANDS.match(c) for c in cells) and all(
            _INT.match(c) or _THOUSANDS.match(c) for c in cells):
        values, note = [c.replace(",", "") for c in cells], "',' read as a thousands separator (every value agrees)"
    elif any("," in c for c in cells) and all(_INT.match(c) or _DEC_COMMA.match(c) or _DEC_COMMA_DOTS.match(c)
                                              for c in cells):
        values = [c.replace(".", "").replace(",", ".") for c in cells]
        note = "',' read as a decimal comma (every value agrees)"
    if values is None:
        return None
    return values, all(_int64(v) for v in values), note


def infer_column(name: str, cells: list, *, delimiter: str | None = None) -> tuple[Column, list[str]]:
    """One `Column` from raw cells (str, or already typed: bool, int, float, TimeValue, None), by the module's rules."""
    name, unit = split_unit(str(name))
    n = len(cells)
    present = [(i, c) for i, c in enumerate(cells) if not is_missing(c)]
    n_missing = n - len(present)
    notes: list[str] = []
    values = [c for _, c in present]
    where = [i for i, _ in present]
    exact = None

    def floats(vals):
        arr = np.full(n, np.nan, dtype=np.float64)
        if where:
            arr[where] = vals
        return arr

    if values and all(isinstance(v, str) for v in values):
        stripped = [v.strip() for v in values]
        low = [v.lower() for v in stripped]
        if all(v in _BOOL for v in low):
            kind, arr = "boolean", floats([1.0 if _BOOL[v] else 0.0 for v in low])
        elif (num := _numbers(stripped, delimiter)) is not None:
            vals, ints, note = num
            kind, arr = ("integer" if ints else "number"), floats([float(v) for v in vals])
            if ints and n_missing == 0:
                exact = np.array([int(v) for v in vals], dtype=np.int64)
            if note:
                notes.append(f"column {name!r}: {note}")
        elif (tm := _times(stripped)) is not None and tm[0]:
            kind, arr = "time", floats(tm[0])
            notes.extend(f"column {name!r}: {t}" for t in tm[1])
        else:
            if tm is not None:
                notes.extend(f"column {name!r}: {t}" for t in tm[1])
            kind = "text"
    elif values and all(isinstance(v, bool) for v in values):
        kind, arr = "boolean", floats([1.0 if v else 0.0 for v in values])
    elif values and all(isinstance(v, TimeValue) for v in values):
        kind, arr = "time", floats([float(v) for v in values])
    elif values and all(isinstance(v, int) and not isinstance(v, bool) and abs(v) < _INT64 for v in values):
        kind, arr = "integer", floats([float(v) for v in values])
        if n_missing == 0:
            exact = np.array(values, dtype=np.int64)
    elif values and all(isinstance(v, (int, float)) and not isinstance(v, (bool, TimeValue)) for v in values):
        kind, arr = "number", floats([float(v) for v in values])
    else:
        kind = "text"
        if values and not all(isinstance(v, str) for v in values):
            notes.append(f"column {name!r} mixes kinds of value: kept as text")
    if kind == "text":
        text = tuple("" if is_missing(c) else _as_text(c) for c in cells)
        return Column(name, "text", unit, text, n_missing), notes
    if kind == "integer" and n_missing == 0 and exact is not None:
        arr = exact
    elif kind == "boolean" and n_missing == 0:
        arr = arr.astype(np.bool_)
    return Column(name, kind, unit, arr, n_missing), notes


def _as_text(cell) -> str:
    if isinstance(cell, str):
        return cell
    if isinstance(cell, bool):
        return "true" if cell else "false"
    if isinstance(cell, float) and cell.is_integer() and abs(cell) < 1e15:
        return str(int(cell)) if not isinstance(cell, TimeValue) else _time_text(cell)
    if isinstance(cell, TimeValue):
        return _time_text(cell)
    return str(cell)


def _time_text(seconds: float) -> str:
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return str(float(seconds))


def _detect_header(rows: list[list], delimiter: str | None) -> tuple[bool, str]:
    if len(rows) < 2:
        return False, "a single row is read as data"
    first = rows[0]
    if not first or any(is_missing(c) or not isinstance(c, str) for c in first):
        return False, "the first row has an empty or non-text cell: no header"
    if any(_scalar_kind(c) != "text" for c in first):
        return False, "the first row holds a number, boolean or date: no header"
    width = len(first)
    for j in range(width):
        below = [r[j] if j < len(r) else None for r in rows[1:]]
        if infer_column("x", below, delimiter=delimiter)[0].kind != "text":
            return True, "header row: the first row is text above a typed column"
    names = [c.strip() for c in first]
    if len(set(names)) == len(names) and all(
            names[j] not in {(r[j].strip() if j < len(r) and isinstance(r[j], str) else None) for r in rows[1:]}
            for j in range(width)):
        return True, "header row: the first row's cells are distinct and appear nowhere else in their columns"
    return False, "no header: the first row looks like the rows below it"


def table_from_rows(rows: list[list], *, header: bool | None = None, title: str | None = None,
                    delimiter: str | None = None) -> tuple[Table, list[str]]:
    """A `Table` from rows of cells (strings or typed values). `header=None` applies the header rule."""
    notes: list[str] = []
    if header is None:
        header, why = _detect_header(rows, delimiter)
        notes.append(why)
    width = max((len(r) for r in rows), default=0)
    if width > MAX_COLUMNS:
        raise Refused(f"the table has more than {MAX_COLUMNS} columns", rule="max_columns", reason="too_large")
    head = [str(c).strip() if not is_missing(c) else "" for c in rows[0]] if header and rows else []
    body = rows[1:] if header and rows else rows
    if header and len(head) < width:
        notes.append(f"{width - len(head)} column(s) have no header cell")
    ragged = sum(1 for r in body if len(r) != width)
    if ragged:
        notes.append(f"{ragged} row(s) have fewer cells than the widest row: missing cells added")
    names, seen = [], {}
    for j in range(width):
        base = head[j] if j < len(head) and head[j] else f"column_{j + 1}"
        seen[base] = seen.get(base, 0) + 1
        names.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    if any(v > 1 for v in seen.values()):
        notes.append("repeated header names were numbered")
    columns = []
    for j in range(width):
        column, col_notes = infer_column(names[j], [r[j] if j < len(r) else None for r in body], delimiter=delimiter)
        columns.append(column)
        notes.extend(col_notes)
    return Table(tuple(columns), len(body), title), notes


# ---------------------------------------------------------------------------------------------------------------
# CSV / TSV


def sniff_delimiter(text: str) -> tuple[str | None, str]:
    """(delimiter, reason) by the module's rule; None when no candidate splits the lines."""
    sample = text[:SNIFF_CHARS]
    if len(text) > SNIFF_CHARS and "\n" in sample:
        sample = sample[:sample.rfind("\n")]
    lines = sample.splitlines()[:SNIFF_LINES]
    best, best_key = None, None
    for d in DELIMITERS:
        try:
            counts = [len(r) for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=d, quotechar='"',
                                                  strict=False) if r]
        except csv.Error:
            continue
        if not counts:
            continue
        mode = max(set(counts), key=lambda k: (counts.count(k), k))
        if mode < 2:
            continue
        key = (counts.count(mode) / len(counts), mode)
        if best_key is None or key > best_key:
            best, best_key = d, key
    if best is None:
        return None, "no delimiter splits the lines: one column"
    name = {"\t": "TAB"}.get(best, repr(best))
    return best, f"delimiter {name}: {best_key[1]} fields on {best_key[0]:.0%} of the first lines"


def _reject_controls(text: str) -> None:
    if "\x00" in text:
        raise Malformed("the text holds NUL characters: not delimited text")


def read_delimited(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline, budget = Deadline(limits.max_seconds), DecodeBudget(limits)
    text, notes = decode_text(data)
    notes = list(notes)
    _reject_controls(text)
    if detected.format == "tsv":
        delimiter, why = "\t", "delimiter TAB (declared TSV)"
    else:
        delimiter, why = sniff_delimiter(text)
    notes.append(why)
    rows, partial = [], False
    reader = (csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"', strict=False) if delimiter
              else ([line] for line in text.splitlines()))
    try:
        for i, row in enumerate(reader):
            if i % 1024 == 0:
                deadline.check()
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            cost = sum(len(c) for c in row) + CELL_OVERHEAD * len(row)
            if not budget.fits_elements(len(row)) or not budget.fits_bytes(cost):
                partial = True
                notes.append(f"stopped after {len(rows)} rows: the element or byte limit was reached")
                break
            budget.take_elements(len(row))
            budget.take_bytes(cost)
            rows.append(row)
    except csv.Error as error:
        raise Malformed(f"the delimited text cannot be split ({type(error).__name__}: a field is too long or "
                        "quoting is broken)") from None
    if not rows:
        raise Malformed("the delimited text has no rows")
    table, table_notes = table_from_rows(rows, delimiter=delimiter)
    notes.extend(table_notes)
    notes.extend(injection_notes(table.text()))
    return Decoded(detected, table, tuple(notes), partial)


# ---------------------------------------------------------------------------------------------------------------
# JSON records


def json_depth(text: str) -> int:
    """Deepest nesting of brackets in JSON text, ignoring strings (a bound checked before `json.loads`)."""
    stripped = _JSON_STRING.sub('""', text).encode("utf-8", "replace")
    if not stripped:
        return 0
    arr = np.frombuffer(stripped, dtype=np.uint8)
    steps = ((arr == 91) | (arr == 123)).astype(np.int32) - ((arr == 93) | (arr == 125)).astype(np.int32)
    return int(np.cumsum(steps).max(initial=0))


def load_json(text: str, *, max_depth: int = JSON_MAX_DEPTH):
    if json_depth(text) > max_depth:
        raise Refused(f"the JSON nests deeper than {max_depth} levels", rule="max_depth")
    try:
        return json.loads(text, parse_constant=lambda name: float("nan"))
    except (ValueError, RecursionError):
        raise Malformed("the text is not valid JSON") from None


def _flatten_value(value, nested: set, key: str):
    if isinstance(value, (dict, list)):
        nested.add(key)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:4096]
    return value


def table_from_records(records: list, *, title: str | None = None, budget: DecodeBudget | None = None,
                       deadline: Deadline | None = None) -> tuple[Table, list[str], bool]:
    """(Table, notes, partial) from a list of JSON objects. Keys in order of first appearance are the columns;
    nested objects and arrays are kept as compact JSON text in a text column (noted); non-object items are skipped
    (noted). String columns go through the string rules (dates, numbers written as text)."""
    notes: list[str] = []
    keys: dict[str, int] = {}
    nested: set[str] = set()
    kept, skipped, partial = [], 0, False
    for i, rec in enumerate(records):
        if deadline and i % 1024 == 0:
            deadline.check()
        if not isinstance(rec, dict):
            skipped += 1
            continue
        if budget is not None:
            if not budget.fits_elements(len(rec)) or not budget.fits_bytes(CELL_OVERHEAD * (len(rec) + 1)):
                partial = True
                notes.append(f"stopped after {len(kept)} records: the element or byte limit was reached")
                break
            budget.take_elements(len(rec))
            budget.take_bytes(CELL_OVERHEAD * (len(rec) + 1))
        for k in rec:
            if k not in keys:
                if len(keys) >= MAX_COLUMNS:
                    raise Refused(f"the records have more than {MAX_COLUMNS} distinct keys", rule="max_columns",
                                  reason="too_large")
                keys[k] = len(keys)
        kept.append(rec)
    if skipped:
        notes.append(f"{skipped} item(s) are not objects: skipped")
    rows = [[str(k) for k in keys]]
    for rec in kept:
        rows.append([_flatten_value(rec.get(k), nested, k) for k in keys])
    if nested:
        notes.append("nested values kept as JSON text in column(s) " + ", ".join(sorted(nested)[:16]))
    table, table_notes = table_from_rows(rows, header=True, title=title)
    absent = sum(1 for rec in kept for k in keys if k not in rec)
    if absent:
        notes.append(f"{absent} key(s) absent from some records: counted as missing")
    return table, notes + table_notes, partial


def read_ndjson(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline, budget = Deadline(limits.max_seconds), DecodeBudget(limits)
    text, notes = decode_text(data)
    notes = list(notes)
    records, bad = [], 0
    for i, line in enumerate(text.splitlines()):
        if i % 1024 == 0:
            deadline.check()
        if not line.strip():
            continue
        try:
            records.append(load_json(line))
        except Malformed:
            bad += 1
    if bad:
        notes.append(f"{bad} line(s) are not valid JSON: skipped")
    if not records:
        raise Malformed("no line holds a JSON value")
    table, rec_notes, partial = table_from_records(records, budget=budget, deadline=deadline)
    notes.extend(rec_notes)
    notes.extend(injection_notes(table.text()))
    return Decoded(detected, table, tuple(notes), partial)


# ---------------------------------------------------------------------------------------------------------------
# Adapters


class DelimitedAdapter(Adapter):
    name = "b1-delimited"
    formats = ("csv", "tsv")
    families = ("table",)

    def decode(self, data, detected, limits):
        return read_delimited(data, detected, limits)


class NdjsonAdapter(Adapter):
    name = "b1-ndjson"
    formats = ("ndjson", "jsonl")
    families = ("table",)

    def decode(self, data, detected, limits):
        return read_ndjson(data, detected, limits)


ADAPTERS = (DelimitedAdapter, NdjsonAdapter)

__all__ = ["ADAPTERS", "DelimitedAdapter", "MISSING", "NdjsonAdapter", "TimeValue", "infer_column", "is_missing",
           "json_depth", "load_json", "read_delimited", "read_ndjson", "sniff_delimiter", "split_unit",
           "table_from_records", "table_from_rows"]
