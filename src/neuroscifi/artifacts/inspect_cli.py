"""`nsf inspect FILE [--json]`: what a file is, why, and what could be read from it. Inspection only: nothing is
stored, nothing is sent anywhere, the file is never changed.

Reads a regular file only (no symbolic link, device or pipe), at most the input limit (64 MiB), and runs the whole
Detect -> Inspect -> Identify -> Resolve -> Decode -> Validate -> Represent chain on it. Prints what was identified
and on what basis, the structure that was decoded (measurements, never an interpretation), what was not recovered,
and for bytes nothing here can decode: the findings, the formats they are consistent with, and what would be needed.
Exit status: 0 when the file was read (even if nothing could decode it), 2 when it was refused or malformed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .types import ArtifactError, HeaderOnly, Limits, bounded

# Terminal control (C0 but tab and newline, ESC, DEL, C1) and direction marks, embeddings, overrides and isolates
# (fix lane F1, ruling C: everything from a file that reaches a terminal is inert, in every command).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def inert_json(text: str) -> str:
    """JSON text with every character `_CONTROL` names written as a \\uXXXX escape (still the same JSON value)."""
    return _CONTROL.sub(lambda m: f"\\u{ord(m.group(0)):04x}", text)


def register(sub):
    p = sub.add_parser("inspect", help="say what a file is and what can be read from it (stores nothing)",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", type=Path, help="the file to inspect (a regular file, at most 64 MiB)")
    p.add_argument("--json", action="store_true", help="print one JSON object instead of plain words")
    p.set_defaults(handler=_inspect)


_BYTE_COUNT = re.compile(r"\b(\d{7,}) bytes\b|(?<=limit of )(\d{7,})\b")      # "N bytes", "the limit of N"
_SPEC_PATH = re.compile(r"\$((?:\.[A-Za-z_][\w-]*|\[\d+\])*)(?=:)")


def _path_words(m) -> str:
    parts = re.findall(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]", m.group(1))
    words = [name or f"item {int(index) + 1}" for name, index in parts]
    return "in " + (", ".join(words) if words else "the specification")


def _clean(text) -> str:
    """Plain output: text from the file may hold terminal escape or direction-override characters (shown as '?');
    large byte counts are written in MiB, and a specification path ($.options) in words (fix lane F3, item 7)."""
    text = _CONTROL.sub("?", str(text))
    text = _BYTE_COUNT.sub(lambda m: f"{int(m.group(1) or m.group(2)) / 1048576:.1f} MiB", text)
    return _SPEC_PATH.sub(_path_words, text)


def _detected(d) -> dict:
    return {"family": d.family, "format": d.format, "media_type": d.media_type, "basis": d.basis,
            "container": d.container, "reasons": list(d.reasons)}


def report(data: bytes, name: str | None) -> dict:
    """What `nsf inspect` prints. A header-only format (HeaderOnly) is a read file too: its header facts are the
    structure, `header_only` says the content was not decoded, and its words are counted (never printed)."""
    from . import guarded
    from .detect import detect
    try:
        decoded = guarded.decode(data, name=name, limits=Limits())
    except HeaderOnly as error:
        d = detect(data[:64 << 10], name=name, size=len(data))
        return {"size": len(data), "detected": _detected(d), "representation": error.opaque.describe(),
                "header": bounded(error.facts), "words_chars": len(error.words), "notes": [str(error)],
                "partial": False, "decoded": False, "header_only": True}
    rep = decoded.representation
    return {"size": len(data), "detected": _detected(decoded.detected),
            "representation": rep.describe(), "notes": list(decoded.notes), "partial": decoded.partial,
            "decoded": rep.kind != "opaque", "header_only": False}


_BASIS = {"signature": "its signature (magic number)", "structure": "its structure",
          "declared": "the file name or declared type only (the bytes do not confirm it)", "none": "nothing"}


def _plain(name: str, out: dict) -> str:
    d, rep = out["detected"], out["representation"]
    lines = [f"File: {_clean(name)} ({out['size']} bytes)"]
    if d["format"] == "unknown":
        lines.append("Identified as: not identified")
    else:
        inside = f", inside a {d['container']} container" if d["container"] else ""
        lines.append(f"Identified as: {d['format']} ({d['family']}, {d['media_type']}){inside}, "
                     f"by {_BASIS[d['basis']]}")
    lines += ["Why:"] + [f"  - {_clean(r)}" for r in d["reasons"]]
    if out.get("header_only"):
        lines.append("Read as: its header only (the content is not decoded here)")
        lines += ["What the header says:"] + [f"  - {_clean(k)}: {_clean(v)}" for k, v in out["header"].items()]
        if out["words_chars"]:
            lines.append(f"  - it carries {out['words_chars']} characters of its own text (metadata; not shown)")
        lines += ["What would be needed:"] + [f"  - {_clean(n)}" for n in out["representation"].get("needs", [])]
    elif out["decoded"]:
        lines.append(f"Read as: {rep['kind']}" + (" (partly: a limit stopped the decode)" if out["partial"] else ""))
        lines.append("Structure (measured, not interpreted):")
        lines += ["  " + _clean(line) for line in json.dumps(rep, indent=2, ensure_ascii=False).splitlines()]
    else:
        lines.append("Read as: nothing here can decode these bytes")
        lines += ["What could be established:"] + [f"  - {_clean(f)}" for f in rep.get("findings", [])]
        if rep.get("candidates"):
            lines += ["Consistent with (not established):"] + [f"  - {_clean(c)}" for c in rep["candidates"]]
        lines += ["What would be needed:"] + [f"  - {_clean(n)}" for n in rep.get("needs", [])]
    if out["notes"]:
        lines += ["Notes:"] + [f"  - {_clean(n)}" for n in out["notes"]]
    return "\n".join(lines)


def _inspect(args) -> int:
    from .safe import read_file
    limits = Limits()
    try:
        data = read_file(args.file, limits.max_bytes)
        out = report(data, os.path.basename(str(args.file)))
    except ArtifactError as error:
        findings = [str(f) for f in getattr(error, "findings", ()) or ()]
        if args.json:
            print(json.dumps({"error": error.reason, "rule": getattr(error, "rule", None), "message": str(error),
                              "findings": findings}))
        elif findings:                          # e.g. an archive refused as a whole: its listing, never its content
            print("What could be established (inspection only, nothing inside was decoded):")
            print("\n".join(f"  - {_clean(f)}" for f in findings))
        print(f"nsf inspect: {_clean(error)}", file=sys.stderr)
        from . import outcome
        return outcome.exit_code(outcome.of_error(error))      # 1 cannot, 2 refused (artifacts/outcome.py)
    if args.json:
        print(json.dumps(out, ensure_ascii=True, sort_keys=True))
    else:
        print(_clean(_plain(str(args.file), out)))
    return 0


__all__ = ["register", "report"]
