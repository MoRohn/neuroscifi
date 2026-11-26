"""Spreadsheets to a `Bundle` of `Table`s, one member per sheet (lane B1): XLSX and ODS.

XLSX (ZIP + XML through the bounded readers): sheets in workbook order with their names; shared strings and inline
strings; numbers; booleans; error cells as missing (counted); dates where the cell's style has a date number
format (built-in formats 14-22, 27-36, 45-47, 50-58, or a custom format code holding y or d outside quotes and
brackets), converted from serial days in the 1900 system (Excel's phantom 29 February 1900 accounted for) or the
1904 system when the workbook says so. Formulas are NOT evaluated: the cached value stored with each formula cell is
used, a formula cell without one is missing, and a note counts them. Merged ranges are noted (their value sits in
the top-left cell). Time-only formats (h, m, s without y or d) stay numbers (fractions of a day; noted).
Each sheet's header row is decided by the rule of `tables.py`.

ODS: `content.xml` `table:table` elements with their names; typed cells from `office:value-type` (float,
percentage, currency, boolean, date); repeated rows and columns expanded at most `office.MAX_REPEAT` times;
formulas likewise not evaluated (the stored value is used).
"""
from __future__ import annotations

import re

from ..safe_b1 import injection_notes, local
from ..types import Decoded, Detected, Malformed, Member, Refused, Bundle
from . import Adapter
from .office import OFFICE, TABLE, Package, odf_table_cells
from .tables import TimeValue, table_from_rows

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))
_REF = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")


def column_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def is_date_format(code: str) -> bool:
    """True when a number-format code shows a calendar date (y or d outside quoted text and brackets)."""
    stripped = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code).lower()
    return bool(re.search(r"[yd]", stripped))


def is_time_only(code: str) -> bool:
    stripped = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code).lower()
    return not is_date_format(code) and bool(re.search(r"[hs]", stripped))


def serial_to_epoch(serial: float, date1904: bool) -> float:
    if date1904:
        return (serial + 1462 - 25569) * 86400.0
    if serial < 60:
        serial += 1                      # Excel counts a 29 February 1900 that never was
    return (serial - 25569) * 86400.0


def _styles(pkg: Package) -> tuple[list[str], list[int]]:
    """(kind per cellXfs index: "date" | "time" | "number", numFmtId per index)."""
    root = pkg.xml("xl/styles.xml", required=False)
    if root is None:
        return [], []
    custom = {}
    for fmt in root.iter(S + "numFmt"):
        fid = fmt.get("numFmtId") or ""
        if fid.isdigit():
            custom[int(fid)] = fmt.get("formatCode") or ""
    kinds, ids = [], []
    xfs = root.find(S + "cellXfs")
    for xf in (xfs if xfs is not None else []):
        fid = xf.get("numFmtId") or "0"
        fid = int(fid) if fid.isdigit() else 0
        code = custom.get(fid)
        if code is not None:
            kind = "date" if is_date_format(code) else "time" if is_time_only(code) else "number"
        else:
            kind = "time" if fid in (18, 19, 20, 21, 45, 46, 47) else "date" if fid in DATE_IDS else "number"
        kinds.append(kind)
        ids.append(fid)
    return kinds, ids


def _shared_strings(pkg: Package) -> list[str]:
    root = pkg.xml("xl/sharedStrings.xml", required=False)
    if root is None:
        return []
    out = []
    for si in root.findall(S + "si"):        # plain text in <t>, rich text in <r><t>; phonetic runs left out
        out.append("".join(t.text or "" for t in si.findall(S + "t") + si.findall(f"{S}r/{S}t")))
    return out


def _sheet(pkg, root, shared, kinds, date1904, notes) -> list[list]:
    data = root.find(S + "sheetData")
    cells: dict[tuple[int, int], object] = {}
    formulas = errors = no_cache = times = 0
    next_row = 0
    max_col = -1
    for row in (data if data is not None else []):
        r_attr = row.get("r")
        r = int(r_attr) - 1 if r_attr and r_attr.isdigit() else next_row
        next_row = r + 1
        col = -1
        for c in row.findall(S + "c"):
            m = _REF.match(c.get("r") or "")
            col = column_index(m.group(1)) if m else col + 1
            if col >= 16384 or r >= 1048576:
                raise Malformed("a cell reference lies outside the sheet")
            t = c.get("t") or "n"
            v = c.find(S + "v")
            f = c.find(S + "f")
            raw = v.text if v is not None else None
            if f is not None:
                formulas += 1
                if raw is None and t != "inlineStr":
                    no_cache += 1
                    continue
            value: object = None
            if t == "s":
                try:
                    value = shared[int(raw)]
                except (ValueError, IndexError, TypeError):
                    raise Malformed("a cell points to a shared string that does not exist") from None
            elif t == "inlineStr":
                value = "".join(x.text or "" for x in c.iter(S + "t"))
            elif t == "str":
                value = raw or ""
            elif t == "b":
                value = raw == "1"
            elif t == "e":
                errors += 1
                value = None
            elif t == "d":
                from .tables import _iso
                parsed = _iso((raw or "").strip())
                value = TimeValue(parsed[0]) if parsed else raw
            elif raw is not None:
                try:
                    number = float(raw)
                except ValueError:
                    raise Malformed("a numeric cell holds something that is not a number") from None
                style = c.get("s") or "0"
                kind = kinds[int(style)] if style.isdigit() and int(style) < len(kinds) else "number"
                if kind == "date":
                    value = TimeValue(serial_to_epoch(number, date1904))
                else:
                    times += kind == "time"
                    value = int(number) if number.is_integer() and "." not in raw and "E" not in raw.upper() \
                        else number
            if value is not None and value != "":
                cells[(r, col)] = value
                max_col = max(max_col, col)
    if not cells:
        return []
    n_rows = max(r for r, _ in cells) + 1
    width = max_col + 1
    if not pkg.budget.fits_elements(n_rows * width):
        raise Refused(f"a sheet spans {n_rows} x {width} cells, over the element limit", rule="max_elements",
                      reason="too_large")
    pkg.budget.take_elements(n_rows * width)
    first = min(r for r, _ in cells)
    rows = [[cells.get((r, c)) for c in range(width)] for r in range(first, n_rows)]
    if formulas:
        notes.append(f"{formulas} formula cell(s) are not evaluated: their cached values are used"
                     + (f"; {no_cache} have no cached value and are missing" if no_cache else ""))
    if errors:
        notes.append(f"{errors} error cell(s) are counted as missing")
    if times:
        notes.append(f"{times} time-of-day cell(s) stay numbers (fractions of a day)")
    merges = root.find(S + "mergeCells")
    if merges is not None and len(merges):
        notes.append(f"{len(merges)} merged range(s): the value sits in each range's top-left cell")
    if first:
        notes.append(f"the sheet's first {first} row(s) are empty and are skipped")
    return rows


def _member(name: str, size: int, rows: list[list], notes: list[str], container: str) -> Member:
    if not rows:
        return Member(name, size, Detected("table", f"{container}-sheet", "application/octet-stream", "structure",
                                           container, ("a sheet of the workbook",)), None, "empty sheet")
    table, tnotes = table_from_rows(rows, title=name)
    return Member(name, size, Detected("table", f"{container}-sheet", "application/octet-stream", "structure",
                                       container, ("a sheet of the workbook",)), table, "; ".join(notes + tnotes)[:600])


def read_xlsx(data, detected, limits):
    pkg = Package(data, limits)
    wb = pkg.xml("xl/workbook.xml")
    rels = pkg.rels("xl/workbook.xml")
    pr = wb.find(S + "workbookPr")
    date1904 = pr is not None and (pr.get("date1904") or "").lower() in ("1", "true")
    kinds, _ = _styles(pkg)
    shared = _shared_strings(pkg)
    members = []
    sheets = wb.find(S + "sheets")
    for i, sheet in enumerate(sheets if sheets is not None else []):
        pkg.deadline.check()
        if i >= limits.max_members:
            pkg.partial = True
            pkg.notes.append(f"stopped after {limits.max_members} sheets")
            break
        name = sheet.get("name") or f"Sheet{i + 1}"
        target = rels.get(sheet.get(R + "id"), (None, None))[0]
        if target is None or pkg.zip.find(target) is None:
            pkg.notes.append(f"sheet {i + 1} is listed but its part is missing")
            continue
        entry = pkg.zip.find(target)
        root = pkg.xml(target)
        notes: list[str] = []
        rows = _sheet(pkg, root, shared, kinds, date1904, notes)
        members.append(_member(name, entry.size, rows, notes, "xlsx"))
    bundle = Bundle(tuple(members))
    notes = pkg.notes + ["formulas are never evaluated; charts, pivot tables, comments and formatting are not read"]
    if date1904:
        notes.append("the workbook uses the 1904 date system")
    return Decoded(detected, bundle, tuple(notes) + injection_notes(bundle.text()), pkg.partial)


def read_ods(data, detected, limits):
    pkg = Package(data, limits)
    root = pkg.xml("content.xml")
    sheet_root = root.find(f"{OFFICE}body/{OFFICE}spreadsheet")
    if sheet_root is None:
        raise Malformed("content.xml has no office:spreadsheet body")
    members = []
    for i, table in enumerate(sheet_root.findall(TABLE + "table")):
        pkg.deadline.check()
        if i >= limits.max_members:
            pkg.partial = True
            pkg.notes.append(f"stopped after {limits.max_members} sheets")
            break
        notes: list[str] = []
        rows = odf_table_cells(table, pkg.budget, notes, typed=True)
        if any(local(k) == "formula" for c in table.iter(TABLE + "table-cell") for k in c.attrib):
            notes.append("formulas are not evaluated: the stored values are used")
        members.append(_member(table.get(TABLE + "name") or f"Sheet{i + 1}", 0, rows, notes, "ods"))
    bundle = Bundle(tuple(members))
    notes = pkg.notes + ["formulas are never evaluated; charts and formatting are not read"]
    return Decoded(detected, bundle, tuple(notes) + injection_notes(bundle.text()), pkg.partial)


class XlsxAdapter(Adapter):
    name = "b1-xlsx"
    formats = ("xlsx", "xlsm")
    families = ("table",)

    def decode(self, data, detected, limits):
        return read_xlsx(data, detected, limits)


class OdsAdapter(Adapter):
    name = "b1-ods"
    formats = ("ods",)
    families = ("table",)

    def decode(self, data, detected, limits):
        return read_ods(data, detected, limits)


ADAPTERS = (XlsxAdapter, OdsAdapter)

__all__ = ["ADAPTERS", "OdsAdapter", "XlsxAdapter", "column_index", "is_date_format", "read_ods", "read_xlsx",
           "serial_to_epoch"]
