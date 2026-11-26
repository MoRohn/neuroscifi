"""NumPy `.npy` arrays and `.npz` archives of them.

The `.npy` header is parsed here by a strict pattern (never evaluated): `descr` must be a plain dtype string,
`fortran_order` True or False, `shape` a tuple of non-negative integers. Object dtypes are refused (loading them
would unpickle). Structured dtypes are not read. shape x itemsize is checked against `max_decoded_bytes` and the
bytes present BEFORE any array exists; the data is then viewed with `numpy.frombuffer` (no pickle path exists in
it), in Fortran order when the header says so.

A 1-D or 2-D array of numbers, booleans, strings or datetimes becomes a `Table` (columns `value`, or `c0`, `c1`,
...); anything else (0-D, 3-D and more, complex, more than MAX_COLUMNS columns) becomes an `Opaque` describing its
shape, dtype and value range, with no element listed. `.npz`: a `Bundle` whose members are the arrays; member
sizes, the deflate ratio, the CRC and the running total of decoded bytes are checked.
"""
from __future__ import annotations

import math
import re

import numpy as np

from ..safe_b2 import Deadline, need, zip_directory, zip_read
from ..types import Bundle, Column, Decoded, Detected, Limits, Malformed, Member, Opaque, Refused, Table, Unsupported
from . import Adapter

MAGIC = b"\x93NUMPY"
MAX_COLUMNS = 1024
_DESCR = re.compile(r"^[<>|=]?(b1|i1|i2|i4|i8|u1|u2|u4|u8|f2|f4|f8|f16|c8|c16|c32|U\d{1,6}|S\d{1,6}|"
                    r"M8\[(?:Y|M|W|D|h|m|s|ms|us|ns)\]|m8\[(?:D|h|m|s|ms|us|ns)\])$")
_ITEM = re.compile(r"""['"](descr|fortran_order|shape)['"]\s*:\s*(\([^()]*\)|'[^']*'|"[^"]*"|True|False)""")
TIME_UNITS = {"Y": None, "M": None, "W": 604800.0, "D": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3,
              "us": 1e-6, "ns": 1e-9}


def parse_header(data: bytes) -> tuple[np.dtype, bool, tuple[int, ...], int]:
    """(dtype, fortran_order, shape, data offset) from an .npy file's header, without evaluating it."""
    if data[:6] != MAGIC or len(data) < 10:
        raise Malformed("the .npy magic is missing")
    major = data[6]
    if major == 1:
        hlen, at = int.from_bytes(data[8:10], "little"), 10
    elif major in (2, 3):
        if len(data) < 12:
            raise Malformed("the .npy header is cut short")
        hlen, at = int.from_bytes(data[8:12], "little"), 12
    else:
        raise Unsupported(f".npy format version {major} is not read here", needs=(f"a reader for .npy v{major}",))
    if hlen > 1 << 20 or at + hlen > len(data):
        raise Malformed("the .npy header length is impossible")
    text = data[at:at + hlen].decode("utf-8" if major == 3 else "latin-1", "replace").strip()
    items = dict(_ITEM.findall(text))
    if set(items) != {"descr", "fortran_order", "shape"}:
        if "'descr': [" in text or '"descr": [' in text:
            raise Unsupported("structured .npy arrays are not read here", needs=("a reader for structured dtypes",))
        raise Malformed("the .npy header is not the expected dictionary of descr, fortran_order and shape")
    descr = items["descr"][1:-1]
    if descr.lstrip("<>|=") in ("O", "O8", "O4"):
        raise Refused("object arrays are refused: loading them would unpickle", rule="pickle")
    if not _DESCR.match(descr):
        raise Unsupported(f"the .npy dtype {descr[:20]!r} is not read here", needs=("a reader for this dtype",))
    shape_text = items["shape"].strip("()").replace(" ", "")
    try:
        shape = tuple(int(x) for x in shape_text.split(",") if x)
    except ValueError:
        raise Malformed("the .npy shape is not a tuple of integers") from None
    if any(d < 0 for d in shape) or len(shape) > 32:
        raise Malformed("the .npy shape has a negative or too many dimensions")
    return np.dtype(descr), items["fortran_order"] == "True", shape, at + hlen


def load_npy(data: bytes, limits: Limits) -> np.ndarray:
    dtype, fortran, shape, at = parse_header(data)
    n = math.prod(shape)
    need(n * dtype.itemsize, limits, "the .npy array")
    if len(data) - at < n * dtype.itemsize:
        raise Malformed(f"the .npy file holds {len(data) - at} data bytes; its header needs {n * dtype.itemsize}")
    arr = np.frombuffer(data, dtype, n, at)
    return arr.reshape(shape, order="F" if fortran else "C")


def _column(name: str, values: np.ndarray) -> Column | None:
    k = values.dtype.kind
    if k == "b":
        return Column(name, "boolean", None, values.astype(np.bool_), 0)
    if k in "iu":
        if k == "u" and values.dtype.itemsize == 8 and values.size and values.max() > np.iinfo(np.int64).max:
            v = values.astype(np.float64)
            return Column(name, "number", None, v, 0)
        return Column(name, "integer", None, values.astype(np.int64), 0)
    if k == "f":
        v = values.astype(np.float64)
        return Column(name, "number", None, v, int(np.isnan(v).sum()))
    if k in "US":
        texts = tuple(x.decode("latin-1") if isinstance(x, bytes) else str(x) for x in values.tolist())
        return Column(name, "text", None, texts, 0)
    if k == "M":
        unit = np.datetime_data(values.dtype)[0]
        scale = TIME_UNITS.get(unit)
        if scale is None:
            v = values.astype("datetime64[s]").astype(np.float64)
        else:
            v = values.view(np.int64).astype(np.float64) * scale
        v[np.isnat(values)] = np.nan
        return Column(name, "time", "s", v, int(np.isnat(values).sum()))
    return None


def represent(arr: np.ndarray, size: int) -> tuple[object, tuple[str, ...]]:
    """A Table for 1-D/2-D arrays of plain kinds, else an Opaque describing the array."""
    notes = []
    if arr.ndim == 1:
        col = _column("value", arr)
        if col is not None:
            return Table((col,), len(arr), None), ()
    if arr.ndim == 2 and arr.shape[1] <= MAX_COLUMNS:
        cols = [_column(f"c{j}", np.ascontiguousarray(arr[:, j])) for j in range(arr.shape[1])]
        if all(c is not None for c in cols):
            return Table(tuple(cols), arr.shape[0], None), ()
    findings = [f"shape: {tuple(arr.shape)}", f"dtype: {arr.dtype.str}", f"elements: {arr.size}"]
    if arr.dtype.kind in "biuf" and arr.size:
        v = arr.astype(np.float64).ravel()
        finite = v[np.isfinite(v)]
        if finite.size:
            findings += [f"min: {finite.min():.6g}", f"max: {finite.max():.6g}", f"mean: {finite.mean():.6g}"]
        findings.append(f"non-finite values: {int(v.size - finite.size)}")
    why = ("more than 2 dimensions" if arr.ndim > 2 else "0 dimensions" if arr.ndim == 0 else
           f"more than {MAX_COLUMNS} columns" if arr.ndim == 2 and arr.shape[1] > MAX_COLUMNS
           else f"dtype {arr.dtype.str}")
    notes.append(f"not represented as a table ({why}); described by shape, dtype and value range only")
    return Opaque(size, tuple(findings), (), ("a representation for n-dimensional or complex arrays",)), tuple(notes)


class NPYAdapter(Adapter):
    name = "b2-npy"
    formats = ("npy",)
    families = ("array",)

    def decode(self, data, detected, limits):
        arr = load_npy(data, limits)
        rep, notes = represent(arr, len(data))
        return Decoded(detected, rep, notes + ("header parsed without evaluation; data viewed with frombuffer",),
                       False)


class NPZAdapter(Adapter):
    name = "b2-npz"
    formats = ("npz",)
    families = ("array",)

    def decode(self, data, detected, limits):
        deadline = Deadline(limits)
        used = 0
        out, notes = [], []
        for m in zip_directory(data, limits):
            deadline.check("reading .npz members")
            if not m.name.endswith(".npy"):
                out.append(Member(m.name, m.size, Detected("unknown", "unknown", "application/octet-stream", "none",
                                                           "zip", ("not a .npy member",)), None,
                                  "not an array; not read"))
                continue
            raw = zip_read(data, m, limits, deadline=deadline, budget=limits.max_decoded_bytes - used)
            used += len(raw)
            arr = load_npy(raw, Limits(max_decoded_bytes=max(0, limits.max_decoded_bytes - used)))
            used += arr.nbytes
            rep, member_notes = represent(arr, len(raw))
            det = Detected("array", "npy", "application/x-npy", "signature", "zip",
                           ("the member starts with the NumPy array magic",))
            out.append(Member(m.name, m.size, det, rep, "; ".join(member_notes)))
        if not out:
            notes.append("the archive holds no members")
        return Decoded(detected, Bundle(tuple(out)), tuple(notes), False)


ADAPTERS = (NPYAdapter(), NPZAdapter())

__all__ = ["ADAPTERS", "MAX_COLUMNS", "NPYAdapter", "NPZAdapter", "load_npy", "parse_header", "represent"]
