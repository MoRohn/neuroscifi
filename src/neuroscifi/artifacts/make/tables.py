"""Tables: one schema for tabular data (used by table_html, data_csv, data_json and document tables), and the HTML
table fragment.

The table is a real HTML table: `<caption>` (the title, then the description), `<thead>` with `<th scope="col">`
cells ("Name (unit)"), `<tbody>` rows whose first cell is `<th scope="row">` when the first column names the rows
(`row_header`, default: when the first column is text). No script, no style attribute, no link: sorting is left
to the reader's own tools. Values are written without locale:
- number: the shortest decimal that reads back as the same float64, never with an exponent ("0.00001");
  with `options.decimals` = d, rounded to d places (the caption says so);
- integer: digits; boolean: "yes" / "no";
- time (seconds since 1970-01-01T00:00:00Z): ISO 8601 UTC, "2026-03-01" when every value in the column is a
  midnight, else "2026-03-01T06:30:00Z" (with the exact fraction when there is one);
- missing: an en dash in a cell of class "missing".
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .charts import HISTORY, STR, UNIT
from .scales import Fit
from .svg_base import esc, scope_id

KINDS = ("number", "integer", "boolean", "time", "text")
MAX_ROWS = 100_000
MAX_CELLS = 1_000_000

COLUMN = {"type": "object", "additionalProperties": False, "required": ["name", "kind", "values"],
          "properties": {"name": STR, "kind": {"enum": list(KINDS)}, "unit": UNIT,
                         "values": {"type": "array", "maxItems": MAX_ROWS,
                                    "items": {"type": ["number", "string", "boolean", "null"]}}}}
DATA = {"type": "object", "additionalProperties": False, "required": ["columns"],
        "properties": {"columns": {"type": "array", "minItems": 1, "maxItems": 200, "items": COLUMN},
                       "row_header": {"type": "boolean"}}}
OPTIONS = {"type": "object", "additionalProperties": False, "properties": {
    "decimals": {"type": "integer", "minimum": 0, "maximum": 12},
    "history": HISTORY}}

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def check_table(data: dict) -> int:
    """Validate the cells against their kinds; returns the number of rows."""
    cols = data["columns"]
    n = len(cols[0]["values"])
    if any(len(c["values"]) != n for c in cols):
        raise Fit("every column must have the same number of values")
    if n * len(cols) > MAX_CELLS:
        raise Fit(f"{n * len(cols)} cells is more than the {MAX_CELLS} a table holds")
    for c in cols:
        for v in c["values"]:
            if v is None:
                continue
            k = c["kind"]
            ok = ((k in ("number", "time") and isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v))
                  or (k == "integer" and isinstance(v, int) and not isinstance(v, bool))
                  or (k == "boolean" and isinstance(v, bool)) or (k == "text" and isinstance(v, str)))
            if not ok:
                raise Fit(f"column {c['name'][:40]!r} is {k} but holds a {type(v).__name__}")
            if k == "time" and not -62135596800 <= v <= 253402300799:
                raise Fit(f"column {c['name'][:40]!r} holds a time outside the years 1 to 9999")
    return n


def plain_number(v) -> str:
    """The shortest decimal that reads back as the same float64, without an exponent."""
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    text = format(Decimal(repr(float(v))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def iso_time(seconds, date_only: bool = False) -> str:
    """ISO 8601 UTC of a float64 number of seconds, exact (the fraction is the shortest exact decimal)."""
    exact = Decimal(repr(float(seconds)))
    whole = int(exact.to_integral_value(rounding="ROUND_FLOOR"))
    frac = exact - whole
    dt = EPOCH + timedelta(seconds=whole)
    if date_only:
        return dt.strftime("%Y-%m-%d")
    text = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if frac:
        text += format(frac, "f")[1:]          # ".25"
    return text + "Z"


def column_is_dates(c) -> bool:
    return c["kind"] == "time" and all(v is None or float(v) % 86400 == 0 for v in c["values"])


def cell_text(c, v, decimals=None, dates=False) -> str | None:
    if v is None:
        return None
    k = c["kind"]
    if k == "number":
        if decimals is None:
            return plain_number(v)
        text = f"{float(v):.{decimals}f}"
        return text[1:] if text.startswith("-") and float(text) == 0 else text     # never "-0.00"
    if k == "integer":
        return str(int(v))
    if k == "boolean":
        return "yes" if v else "no"
    if k == "time":
        return iso_time(v, dates)
    return str(v)


def header_text(c) -> str:
    return f"{c['name']} ({c['unit']})" if c.get("unit") else str(c["name"])


def table_html(spec_title: str, description: str, data: dict, options: dict, table_id: str) -> str:
    n = check_table(data)
    cols = data["columns"]
    row_header = data.get("row_header", cols[0]["kind"] == "text")
    decimals = options.get("decimals")
    dates = [column_is_dates(c) for c in cols]
    out = [f'<table class="nsf-table" id="{esc(table_id)}">']
    cap = esc(spec_title)
    note = description.strip()
    if decimals is not None:
        note = (note + " " if note else "") + f"Numbers are rounded to {decimals} decimal places."
    if note:
        cap += f'<br><span class="description">{esc(note)}</span>'
    out.append(f"<caption>{cap}</caption>")
    out.append("<thead><tr>")
    for c in cols:
        unit = f' <span class="unit">({esc(c["unit"])})</span>' if c.get("unit") else ""
        out.append(f'<th scope="col">{esc(c["name"])}{unit}</th>')
    out.append("</tr></thead><tbody>")
    for r in range(n):
        out.append("<tr>")
        for i, c in enumerate(cols):
            text = cell_text(c, c["values"][r], decimals, dates[i])
            tag = "th" if (i == 0 and row_header) else "td"
            scope = ' scope="row"' if tag == "th" else ""
            num = c["kind"] in ("number", "integer")
            if text is None:
                out.append(f'<{tag}{scope} class="missing{" num" if num else ""}">–</{tag}>')
            else:
                cls = ' class="num"' if num else ""
                out.append(f"<{tag}{scope}{cls}>{esc(text)}</{tag}>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def make_table(spec):
    tid = scope_id("table_html", spec.to_json())
    return table_html(spec.title, spec.description, spec.data, spec.options, tid) + "\n", ()


__all__ = ["COLUMN", "DATA", "KINDS", "OPTIONS", "cell_text", "check_table", "header_text", "iso_time",
           "make_table", "plain_number", "table_html"]
