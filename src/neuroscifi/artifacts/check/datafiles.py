"""Independent checks of made data files: parse them back by their stated dialects and compare with the source
exactly (ADR-074).

CSV ("neuroscifi.csv/1"): read by this module's own RFC 4180 reader, which keeps whether a field was quoted (so a
quoted empty field is empty text and an unquoted empty field is a missing value); CRLF after every record, no byte
order mark, UTF-8. Header cells are "name" or "name [unit]". Text fields lose exactly one leading apostrophe when the
character after it is one of = + - @ TAB CR ' (the formula guard), and no text field may start with = + - @ unguarded.
Numbers must read back to the identical float64; times are ISO 8601 UTC read exactly.

JSON ("neuroscifi.table/1"): parsed with duplicate keys, NaN and the infinities refused; columns compared exactly;
the made-by statement must say the file is not evidence.

The source is the specification's `data` ({"columns": [...]}) or a `types.Table`.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal

from ..types import Malformed, Refused
from . import Finding

MAX_BYTES = 64 << 20
_GUARDED = ("=", "+", "-", "@", "\t", "\r", "'")


def read_csv(text: str) -> tuple[list[list[tuple[str, bool]]], list[str]]:
    """Records of (field, was_quoted), and dialect problems found while reading."""
    problems: list[str] = []
    records: list[list[tuple[str, bool]]] = []
    field: list[str] = []
    record: list[tuple[str, bool]] = []
    i, n = 0, len(text)
    quoted = False
    at_start = True
    while i < n:
        ch = text[i]
        if at_start and ch == '"':
            quoted, at_start = True, False
            i += 1
            while True:
                j = text.find('"', i)
                if j < 0:
                    raise Malformed("a quoted field is not closed")
                field.append(text[i:j])
                if j + 1 < n and text[j + 1] == '"':
                    field.append('"')
                    i = j + 2
                    continue
                i = j + 1
                break
            if i < n and text[i] not in ",\r\n":
                problems.append("text after a closing quote")
            continue
        if ch == ",":
            record.append(("".join(field), quoted))
            field, quoted, at_start = [], False, True
            i += 1
            continue
        if ch == "\r" or ch == "\n":
            if ch == "\n" or not text.startswith("\r\n", i):
                problems.append("a line ends without CRLF")
            record.append(("".join(field), quoted))
            records.append(record)
            field, record, quoted, at_start = [], [], False, True
            i += 2 if text.startswith("\r\n", i) else 1
            continue
        if ch == '"':
            problems.append("a quote inside an unquoted field")
        field.append(ch)
        at_start = False
        i += 1
    if field or record or quoted:
        record.append(("".join(field), quoted))
        records.append(record)
        problems.append("the last record has no CRLF")
    return records, problems


def _iso_exact(text: str) -> float | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z", text)
    if not m:
        return None
    y, mo, d, h, mi, s, frac = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return float(Decimal(delta.days * 86400 + delta.seconds) + (Decimal(frac) if frac else 0))


def source_columns(source) -> list[dict] | None:
    """[{name, kind, unit, values}] from a spec's data or a types.Table (missing values as None)."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get("columns")
    columns = getattr(source, "columns", None)
    if columns is None:
        return None
    out = []
    for c in columns:
        vals = []
        for v in list(c.values):
            if c.kind == "text":
                vals.append(str(v))
            elif isinstance(v, float) and math.isnan(v):
                vals.append(None)
            elif c.kind == "integer":
                vals.append(int(v))
            elif c.kind == "boolean":
                vals.append(bool(v))
            else:
                vals.append(float(v))
        out.append({"name": c.name, "kind": c.kind, "unit": c.unit, "values": vals})
    return out


def _same(kind: str, got, want) -> bool:
    if want is None or got is None:
        return want is None and got is None
    if kind in ("number", "time"):
        return isinstance(got, float) and got == float(want) and math.copysign(1, got) == math.copysign(1, float(want))
    if kind == "integer":
        return isinstance(got, int) and got == int(want)
    if kind == "boolean":
        return got is bool(want)
    return got == want


def _csv_value(kind: str, field: str, quoted: bool):
    """(value, ok) of one field read by the dialect for `kind`."""
    if field == "" and not quoted:
        return None, True
    if kind == "number":
        try:
            return float(field), re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?|-?inf|nan", field) is not None
        except ValueError:
            return None, False
    if kind == "integer":
        return (int(field), True) if re.fullmatch(r"-?\d+", field) else (None, False)
    if kind == "boolean":
        return (field == "true", True) if field in ("true", "false") else (None, False)
    if kind == "time":
        v = _iso_exact(field)
        return v, v is not None
    if field.startswith("'") and len(field) > 1 and field[1] in _GUARDED:
        return field[1:], True
    return field, True


def check_csv(data: bytes, source) -> list[Finding]:
    if len(data) > MAX_BYTES:
        raise Refused(f"the file is over {MAX_BYTES} bytes", rule="max_bytes", reason="too_large")
    out = []
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding("csv_parses", False, "the file is not UTF-8")]
    records, problems = read_csv(text)
    if bom:
        problems.append("a byte order mark")
    widths = {len(r) for r in records}
    out.append(Finding("csv_parses", len(widths) <= 1 and bool(records),
                       f"{len(records)} records of {next(iter(widths), 0)} fields" if len(widths) <= 1 and records
                       else "records have different numbers of fields"))
    out.append(Finding("csv_dialect", not problems, "RFC 4180, CRLF, no byte order mark" if not problems else
                       "; ".join(sorted(set(problems)))))
    cols = source_columns(source)
    unguarded = 0
    if cols is None or not records:
        return out
    header = [f for f, _q in records[0]]
    want = [f"{c['name']} [{c['unit']}]" if c.get("unit") else (f"{c['name']} []" if str(c["name"]).endswith("]")
                                                               else str(c["name"])) for c in cols]
    out.append(Finding("header_matches", header == want, "the header names every column and unit" if header == want
                       else "the header differs from the source"))
    rows = records[1:]
    n = len(cols[0]["values"]) if cols else 0
    good = bad_fields = 0
    total = n * len(cols)
    for r in range(min(n, len(rows))):
        for i, c in enumerate(cols):
            if i >= len(rows[r]):
                continue
            field, quoted = rows[r][i]
            if c["kind"] == "text" and field[:1] in ("=", "+", "-", "@"):
                unguarded += 1
            value, ok = _csv_value(c["kind"], field, quoted)
            bad_fields += not ok
            good += ok and _same(c["kind"], value, c["values"][r])
    out.append(Finding("values_exact", good == total and len(rows) == n,
                       f"{good} of {total} values read back exactly" + ("" if len(rows) == n else
                                                                       f"; {len(rows)} rows for {n}"),
                       good / total if total else 1.0))
    out.append(Finding("formula_guard", unguarded == 0, "no text field can start a spreadsheet formula"
                       if not unguarded else f"{unguarded} text field(s) start like a formula"))
    return out


def check_json(data: bytes, source) -> list[Finding]:
    if len(data) > MAX_BYTES:
        raise Refused(f"the file is over {MAX_BYTES} bytes", rule="max_bytes", reason="too_large")

    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise Malformed("a JSON object repeats a key")
        return dict(items)

    def constant(name):
        raise Malformed(f"{name} is not a JSON number")
    try:
        doc = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        return [Finding("json_parses", False, f"not valid JSON ({type(error).__name__})")]
    out = [Finding("json_parses", True, "valid JSON")]
    ok = isinstance(doc, dict) and doc.get("dialect") == "neuroscifi.table/1" and isinstance(doc.get("columns"), list)
    out.append(Finding("json_dialect", ok, "the neuroscifi.table/1 dialect" if ok else "not the stated dialect"))
    label = isinstance(doc, dict) and "not evidence" in str(doc.get("made_by", "")).lower()
    out.append(Finding("made_by_label", label, "the file says it was made by the system and is not evidence"
                       if label else "the made-by statement is missing"))
    cols = source_columns(source)
    if cols is None or not ok:
        return out
    got = doc["columns"]
    heads = len(got) == len(cols) and all(isinstance(g, dict) and g.get("name") == c["name"] and
                                          g.get("unit") == c.get("unit") and g.get("kind") == c["kind"]
                                          for g, c in zip(got, cols))
    out.append(Finding("header_matches", heads, "every column's name, unit and kind match" if heads else
                       "the columns' names, units or kinds differ"))
    good = total = 0
    for g, c in zip(got, cols):
        values = g.get("values") if isinstance(g, dict) else None
        total += len(c["values"])
        if not isinstance(values, list) or len(values) != len(c["values"]):
            continue
        for v, w in zip(values, c["values"]):
            if c["kind"] in ("number", "time") and isinstance(v, int) and not isinstance(v, bool):
                v = float(v)
            good += _same(c["kind"], v, w)
    out.append(Finding("values_exact", good == total, f"{good} of {total} values read back exactly",
                       good / total if total else 1.0))
    return out


__all__ = ["check_csv", "check_json", "read_csv", "source_columns"]
