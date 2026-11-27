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

Active content and outside references (ADR-080 rules 2 and 3) are never run or followed and never refuse the file:
a script (`/JavaScript`, `/JS`), an auto-run action (an `/OpenAction` that is an action, `/AA`), a launch action
(`/Launch`, which would start a program), an embedded file (`/EmbeddedFile`), a link address (`/URI`) or another
document (`/GoToR`, `/GoToE`) is named in the notes (`outcome.active`, `outcome.outside`). They are found by name in
the uncompressed bytes (escaped names decoded) and in the document catalog as pypdf reads it; one hidden inside a
compressed stream elsewhere is not found (unverified as complete).

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

from .. import outcome
from ..safe import Deadline
from ..safe_b1 import DecodeBudget
from ..types import Block, Malformed, Refused, TEXT_MAX_CHARS, Unsupported
from . import Adapter
from .documents import _finish

_VERSION = re.compile(rb"%PDF-(\d\.\d)")
_PAGES = re.compile(rb"/Type\s*/Pages\b(?:(?!endobj).){0,2048}?/Count\s+(\d{1,9})", re.S)
_PAGES_REV = re.compile(rb"/Count\s+(\d{1,9})(?:(?!endobj|obj).){0,2048}?/Type\s*/Pages\b", re.S)
SCAN_BYTES = 8 << 20
_NAME = re.compile(rb"/((?:[A-Za-z0-9]|#[0-9A-Fa-f]{2}){1,40})")
# name -> ("active" | "outside", plain words); OpenAction counts only when it is an action (a dictionary), not a view
ACTIVE_NAMES = {"JavaScript": ("active", "a script"), "JS": ("active", "a script"),
                "AA": ("active", "an auto-run action"), "Launch": ("active", "a launch action (it would start a program)"),
                "EmbeddedFile": ("active", "an embedded file"), "EmbeddedFiles": ("active", "an embedded file"),
                "URI": ("outside", "link address"), "GoToR": ("outside", "other document"),
                "GoToE": ("outside", "other document")}
_OPEN_ACTION = re.compile(rb"/OpenAction\s*<<")
# Lane FX5 (W3): the cross-reference chain read as a tolerant PDF reader reads it. PDF white space is NUL, tab, line
# feed, form feed, carriage return and space; integers are read whole (leading zeros, any length, an optional "+").
_WS = rb"[\x00\t\n\x0c\r ]"
_STARTXREF = re.compile(rb"startxref" + _WS + rb"+\+?(\d+)")
_CHAIN_KEYS = re.compile(rb"/(Prev|XRefStm)" + _WS + rb"+\+?(\d+)")
_OBJ_HEAD = re.compile(rb"\d+" + _WS + rb"+\d+" + _WS + rb"+obj\b")
_XREF_TYPE = re.compile(rb"/Type" + _WS + rb"*/XRef\b")
MAX_XREF_SECTIONS = 4096
MAX_STARTXREF = 64
LOOP, UNREADABLE = "loop", "unreadable"


_DICT_TOKEN = re.compile(rb"<<|>>|\(|%")
_STRING_TOKEN = re.compile(rb"[()\\]")
_LINE_END = re.compile(rb"[\r\n]")
_BLANK_START = re.compile(rb"\(|(?<!<)<(?!<)|%")


def _dictionary_tokens(raw: bytes) -> bytes:
    """A dictionary's bytes with its literal strings, hex strings and comments blanked out (lane FX4, R6): a "/Prev 9"
    inside a string is a decoy, and a comment may stand between /Prev and its number. Same length as `raw`; one pass
    (lane FX5: linear on any input)."""
    out = bytearray(raw)
    i, n = 0, len(raw)
    while True:
        m = _BLANK_START.search(raw, i)
        if m is None:
            break
        start = m.start()
        if m.group() == b"(":
            level, i = 1, m.end()
            while level:
                k = _STRING_TOKEN.search(raw, i)
                if k is None:
                    i = n
                    break
                i = k.end()
                if k.group() == b"\\":
                    i += 1
                else:
                    level += 1 if k.group() == b"(" else -1
        elif m.group() == b"<":
            j = raw.find(b">", start)
            i = n if j < 0 else j + 1
        else:
            k = _LINE_END.search(raw, start)
            i = n if k is None else k.start()
        i = min(i, n)
        out[start:i] = b" " * (i - start)
    return bytes(out)


def _names_decoded(tokens: bytes) -> bytes:
    """PDF names with their #xx escapes decoded ("/P#72ev" is "/Prev" to every conforming reader; lane FX5, C5)."""
    def decode(m):
        return b"/" + re.sub(rb"#([0-9A-Fa-f]{2})", lambda h: bytes([int(h.group(1), 16)]), m.group(1))
    return re.sub(rb"/([^\x00\t\n\x0c\r /<>\[\]()%{}]+)", decode, tokens)


def _dictionary_at(data: bytes, start: int, budget: list) -> bytes | None:
    """The first `<< ... >>` dictionary at or after `start`, balanced (strings and comments not counted), or None
    when it does not close within the scan budget. One pass: every byte is looked at once."""
    at = data.find(b"<<", start, start + 4096)
    if at < 0:
        return None
    depth, i, n = 0, at, len(data)
    while True:
        m = _DICT_TOKEN.search(data, i)
        if m is None:
            return None
        budget[0] -= m.end() - i
        if budget[0] <= 0:
            return None
        tok, i = m.group(), m.end()
        if tok == b"<<":
            depth += 1
        elif tok == b">>":
            depth -= 1
            if depth == 0:
                return data[at:i]
        elif tok == b"(":                                 # a literal string: to its balancing parenthesis
            level = 1
            while level:
                k = _STRING_TOKEN.search(data, i)
                if k is None:
                    return None
                budget[0] -= k.end() - i
                if budget[0] <= 0:
                    return None
                i = k.end()
                if k.group() == b"\\":
                    i += 1
                else:
                    level += 1 if k.group() == b"(" else -1
        else:                                            # a comment: to the end of its line
            k = _LINE_END.search(data, i)
            i = n if k is None else k.end()


def _chain_edges(data: bytes, offset: int, budget: list) -> list[int] | None:
    """The offsets a cross-reference section at `offset` points on to (every /Prev and /XRefStm of its trailer, or of
    its cross-reference stream's dictionary), or None when no section can be read there within the budget."""
    m = re.compile(_WS + rb"*").match(data, offset)
    p = m.end() if m else offset
    if data.startswith(b"xref", p):
        at = data.find(b"trailer", p)
        budget[0] -= (at - p) if at >= 0 else len(data) - p
        if at < 0 or budget[0] <= 0:
            return None
        dictionary = _dictionary_at(data, at + 7, budget)
    elif _OBJ_HEAD.match(data, p):
        dictionary = _dictionary_at(data, p, budget)
        if dictionary is not None and not _XREF_TYPE.search(_names_decoded(_dictionary_tokens(dictionary))):
            return None                                   # an object, but not a cross-reference stream
    else:
        return None
    if dictionary is None:
        return None
    return [int(m.group(2)) for m in _CHAIN_KEYS.finditer(_names_decoded(_dictionary_tokens(dictionary)))]


def _startxrefs(data: bytes) -> list[int]:
    """Every `startxref <offset>` outside a comment, in file order (at most MAX_STARTXREF, the last ones)."""
    out = []
    for m in _STARTXREF.finditer(data):
        line = max(data.rfind(b"\n", 0, m.start()), data.rfind(b"\r", 0, m.start())) + 1
        if b"%" in data[line:m.start()]:
            continue                                      # inside a comment
        out.append(int(m.group(1)))
    return out[-MAX_STARTXREF:]


def xref_chain_state(data: bytes) -> str | None:
    """Lane FX5 (W3): LOOP when a walk of the cross-reference chain from any `startxref` comes back to a section
    already on it (a visited set over every /Prev and /XRefStm); UNREADABLE when the chain from the LAST `startxref`
    (the one a reader starts from) cannot be followed to its end within the budget (a section that is not there, a
    trailer or a dictionary that does not close, more than MAX_XREF_SECTIONS sections, more bytes scanned than four
    times the file); None otherwise, including a file with no `startxref` at all (nothing chains)."""
    starts = _startxrefs(data)
    budget = [4 * len(data) + (1 << 20)]
    unreadable = False
    for n, start in enumerate(reversed(starts)):
        last = n == 0
        state: dict[int, int] = {}                        # 1 on the current path, 2 done
        edges = _chain_edges(data, start, budget) if 0 <= start < len(data) else None
        if edges is None:
            unreadable = unreadable or last
            continue
        state[start] = 1
        stack = [(start, iter(edges))]
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state.get(nxt) == 1:
                return LOOP                               # a loop is named first, wherever it was found
            if nxt in state:
                continue
            if len(state) >= MAX_XREF_SECTIONS:
                unreadable = unreadable or last
                break
            more = _chain_edges(data, nxt, budget) if 0 <= nxt < len(data) else None
            if more is None:
                unreadable = unreadable or last
                state[nxt] = 2
                continue
            state[nxt] = 1
            stack.append((nxt, iter(more)))
    return UNREADABLE if unreadable else None


def xref_chain_loops(data: bytes) -> bool:
    """Lane FX3 (class H-C), parsed since lane FX5: whether the cross-reference chain loops (`xref_chain_state`)."""
    return xref_chain_state(data) == LOOP


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


def _active(data: bytes, reader=None) -> tuple[list[str], list[str]]:
    """(active content, outside references) the PDF names, as plain words (ADR-080): never run, never followed."""
    found: set[tuple[str, str]] = set()
    head = data[:SCAN_BYTES]
    for m in _NAME.finditer(head):
        name = re.sub(rb"#([0-9A-Fa-f]{2})", lambda h: bytes([int(h.group(1), 16)]), m.group(1)).decode("latin-1")
        if name in ACTIVE_NAMES:
            found.add(ACTIVE_NAMES[name])
    if _OPEN_ACTION.search(head):
        found.add(("active", "an auto-run action"))
    if reader is not None:
        try:
            root = reader.trailer["/Root"].get_object()
            action = root.get("/OpenAction")
            action = action.get_object() if action is not None else None
            if isinstance(action, dict) and "/S" in action:
                found.add(("active", "an auto-run action"))
                kind = str(action.get("/S"))[1:]
                if kind in ACTIVE_NAMES:
                    found.add(ACTIVE_NAMES[kind])
            if root.get("/AA") is not None:
                found.add(("active", "an auto-run action"))
            names = root.get("/Names")
            names = names.get_object() if names is not None else None
            if isinstance(names, dict):
                for key in ("/JavaScript", "/EmbeddedFiles"):
                    if names.get(key) is not None:
                        found.add(ACTIVE_NAMES[key[1:]])
        except Exception:       # a catalog pypdf cannot walk: what the bytes showed stands
            pass
    return (sorted(w for k, w in found if k == "active"), sorted(w for k, w in found if k == "outside"))


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
    chain = xref_chain_state(data)
    if chain == LOOP:
        raise Refused("the PDF's cross-reference sections point back at one already read (a loop): what the file "
                      "holds is not established", rule="self_reference")
    if chain == UNREADABLE:
        raise Refused("the PDF's cross-reference chain cannot be followed to its end (a section is not where it is "
                      "said to be, or it does not end within the limit): what the file holds is not established",
                      rule="xref_unreadable")
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
                active, outside = _active(data, reader)
                notes += [outcome.active(", ".join(active[:-1]) + " and " + active[-1] if len(active) > 1 else
                                         active[0])] if active else []
                notes += [outcome.outside(", ".join(outside))] if outside else []
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
        raise Refused(f"reading the PDF took longer than the limit of {limits.max_seconds:.3g} seconds",
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
