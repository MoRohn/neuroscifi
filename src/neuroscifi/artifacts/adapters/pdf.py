"""PDF to `Document` through the optional `pypdf` (lane B1); header facts only without it.

With pypdf: the text layer of each page (pypdf's reading order), pages separated by `page_break` blocks, the
outline as headings placed before the text of the page they point to (level by outline nesting), the title from
the document information. Pages beyond `max_members` are not read (partial). NOT recovered, said in every result:
layout, columns, tables, figures, equations, annotations and scanned pages (a scan has no text layer: its pages
come out empty and a note counts them). Encrypted files are tried with the empty password only (the common
"permissions only" case); otherwise `Refused(rule="encrypted")`.

Stopping a slow parse: pypdf is pure Python, so a `sys.settrace` guard checks the deadline on every Python function
call inside the decode and raises a `BaseException` subclass (which pypdf's `except Exception` handlers cannot
swallow); the guard is removed however the decode ends. A single C-level step (one zlib inflate of one stream) is
not interruptible; it is bounded by the input size (`max_bytes`) and pypdf's own per-stream output limit.

Without pypdf: `Unsupported(needs=("pypdf",))` whose message and `.facts` carry what the bytes show without a
parser: the header version, whether the trailer names `/Encrypt`, linearisation, and the page count when a
`/Type /Pages` object with `/Count` is visible uncompressed.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import re
import sys
import time
from contextlib import contextmanager

from ..safe import Deadline
from ..safe_b1 import DecodeBudget
from ..types import Block, Malformed, Refused, TEXT_MAX_CHARS, Unsupported
from . import Adapter
from .documents import _finish

_VERSION = re.compile(rb"%PDF-(\d\.\d)")
_PAGES = re.compile(rb"/Type\s*/Pages\b(?:(?!endobj).){0,2048}?/Count\s+(\d{1,9})", re.S)
_PAGES_REV = re.compile(rb"/Count\s+(\d{1,9})(?:(?!endobj|obj).){0,2048}?/Type\s*/Pages\b", re.S)
SCAN_BYTES = 8 << 20


class _Timeout(BaseException):
    """Raised inside pypdf by the trace guard; a BaseException so that `except Exception` cannot swallow it."""


@contextmanager
def _deadline_guard(deadline: Deadline):
    end = time.monotonic() + deadline.remaining()

    def tracer(frame, event, arg):
        if event == "call" and time.monotonic() > end:
            raise _Timeout
        return None

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        yield
    finally:
        sys.settrace(previous)


def header_facts(data: bytes) -> dict:
    """What the bytes show without a PDF parser (no object is decompressed)."""
    facts: dict = {"size": len(data)}
    m = _VERSION.match(data[:16])
    facts["version"] = m.group(1).decode() if m else None
    tail = data[-4096:]
    facts["encrypted"] = b"/Encrypt" in tail or b"/Encrypt" in data[:SCAN_BYTES][-65536:]
    facts["linearized"] = b"/Linearized" in data[:2048]
    head = data[:SCAN_BYTES]
    counts = [int(x) for x in _PAGES.findall(head)] + [int(x) for x in _PAGES_REV.findall(head)]
    facts["pages"] = max(counts) if counts else None
    return facts


def _facts_sentence(facts: dict) -> str:
    parts = [f"PDF version {facts['version']}" if facts["version"] else "no PDF version in the header"]
    parts.append(f"{facts['pages']} page(s) declared" if facts["pages"] is not None else
                 "page count not readable without a parser")
    if facts["encrypted"]:
        parts.append("the trailer names an encryption dictionary")
    return "; ".join(parts)


def _outline(reader, deadline) -> dict[int, list[tuple[int, str]]]:
    """page index -> [(level, title)] from the outline, iteratively, at most 4096 entries."""
    out: dict[int, list[tuple[int, str]]] = {}
    try:
        items = reader.outline
    except Exception:
        return out
    stack, seen = [(items, 1)], 0
    while stack and seen < 4096:
        current, level = stack.pop()
        pending = []
        for item in current:
            if isinstance(item, list):
                pending.append((item, level + 1))
                continue
            seen += 1
            try:
                page = reader.get_destination_page_number(item)
                title = str(item.title or "").strip()
            except Exception:
                continue
            if title and page is not None and page >= 0:
                out.setdefault(page, []).append((min(level, 9), title[:500]))
        stack.extend(reversed(pending))
    return out


def read_pdf(data: bytes, detected, limits):
    if not data.startswith(b"%PDF-"):
        raise Malformed("the bytes do not start with '%PDF-'")
    facts = header_facts(data)
    if importlib.util.find_spec("pypdf") is None:
        error = Unsupported("PDF identified (" + _facts_sentence(facts) + "); its text needs pypdf, which is not "
                            "installed", needs=("pypdf",))
        error.facts = facts
        raise error
    deadline, budget = Deadline(limits.max_seconds), DecodeBudget(limits)
    blocks: list[Block] = []
    notes = ["PDF: text layer only; no layout, no columns, no tables, no figures, no equations, no scanned pages"]
    partial = False
    chars, empty = 0, 0
    title = None
    level = logging.getLogger("pypdf").level
    logging.getLogger("pypdf").setLevel(logging.ERROR)     # pypdf's warnings could quote the file's content
    from pypdf import PdfReader                             # imported before the guard: imports are not traced
    try:
        with _deadline_guard(deadline):
            try:
                reader = PdfReader(io.BytesIO(data), strict=False)
                if reader.is_encrypted:
                    try:
                        ok = reader.decrypt("")
                    except Exception:
                        ok = 0
                    if not ok:
                        raise Refused("the PDF is encrypted with a password: it is not decrypted", rule="encrypted")
                    notes.append("encrypted with an empty user password: opened without one")
                n_pages = len(reader.pages)
                try:
                    info = reader.metadata
                    title = str(info.title).strip() or None if info is not None and info.title else None
                except Exception:
                    title = None
                headings = _outline(reader, deadline)
                if not headings:
                    notes.append("the file has no usable outline: no headings")
                for i in range(n_pages):
                    if i >= limits.max_members:
                        partial = True
                        notes.append(f"stopped after {limits.max_members} pages of {n_pages}")
                        break
                    deadline.check()
                    if i:
                        blocks.append(Block("page_break", 0, "", {"page": i + 1}))
                    for lvl, text in headings.get(i, ()):
                        blocks.append(Block("heading", lvl, text, {"page": i + 1}))
                    text = reader.pages[i].extract_text() or ""
                    if not text.strip():
                        empty += 1
                    for para in re.split(r"\n\s*\n", text):
                        para = para.strip()
                        if not para:
                            continue
                        if chars + len(para) > TEXT_MAX_CHARS or not budget.fits_elements(1):
                            partial = True
                            break
                        budget.take_elements(1)
                        chars += len(para)
                        blocks.append(Block("paragraph", 0, para, {"page": i + 1}))
                    if partial:
                        notes.append(f"stopped in page {i + 1}: the text or element limit was reached")
                        break
            except (Refused, Unsupported):
                raise
            except _Timeout:
                raise
            except Exception as error:
                failure = Malformed("the PDF reader could not read this PDF: its structure is damaged or uses "
                                    "something the reader does not handle")
                failure.detail = type(error).__name__          # for a log, never printed to the owner
                raise failure from None
    except _Timeout:
        raise Refused(f"reading the PDF took longer than the limit of {limits.max_seconds:g} seconds",
                      rule="max_seconds", reason="timeout") from None
    finally:
        logging.getLogger("pypdf").setLevel(level)
    if empty:
        notes.append(f"{empty} page(s) have no text layer (scanned or drawn text is not recognised)")
    notes.append("header facts: " + _facts_sentence(facts))
    return _finish(detected, blocks, title, notes, partial)


class PdfAdapter(Adapter):
    name = "b1-pdf"
    formats = ("pdf",)
    families = ("document",)

    def available(self):
        if importlib.util.find_spec("pypdf") is None:
            return True, "header facts only: pypdf is not installed"
        return True, "ready (text layer through pypdf)"

    def decode(self, data, detected, limits):
        return read_pdf(data, detected, limits)


ADAPTERS = (PdfAdapter,)

__all__ = ["ADAPTERS", "PdfAdapter", "header_facts", "read_pdf"]
