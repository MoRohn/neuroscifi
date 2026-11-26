"""Plain text -> Document (lane K's pattern adapter). The encoding is decided by byte-order mark, then by validity
(UTF-8, then UTF-16 without a mark when every other byte is zero); nothing is decoded with a guessed legacy code page.
Paragraphs are runs of lines separated by blank lines; at most max_elements paragraphs are kept (partial beyond).
The text is the artifact's own words and untrusted: `Document.text()` must pass `webcortex.safety.scan_injection`
before it may reach a model."""
from __future__ import annotations

import codecs
import re

from ..safe import Deadline
from ..types import Block, Decoded, Detected, Document, Limits, Malformed
from . import Adapter

_BOMS = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"), (codecs.BOM_UTF8, "utf-8"),
         (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))


def decode_text(data: bytes) -> tuple[str, str]:
    """(text, encoding) or Malformed. Used by every text-based adapter of lane K."""
    for bom, enc in _BOMS:
        if data.startswith(bom):
            body = data[len(bom):]
            break
    else:
        body, enc = data, "utf-8"
        if b"\x00" in data[:4096] and len(data) >= 2:
            odd, even = data[1:4096:2], data[0:4096:2]
            if odd.count(0) >= 0.9 * len(odd):
                enc = "utf-16-le"
            elif even.count(0) >= 0.9 * len(even):
                enc = "utf-16-be"
    try:
        return body.decode(enc), enc
    except UnicodeDecodeError as error:
        raise Malformed(f"the bytes are not valid {enc.upper()} text (first invalid byte at offset "
                        f"{error.start})") from None


class PlainText(Adapter):
    name = "plain-text"
    formats = ("text",)
    families = ("text",)

    def decode(self, data: bytes, detected: Detected, limits: Limits) -> Decoded:
        deadline = Deadline(limits.max_seconds)
        text, enc = decode_text(data)
        notes = [f"read as {enc.upper()}"]
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        blocks, partial, start = [], False, 0
        for m in re.finditer(r"\n[ \t]*\n+", text + "\n\n"):
            para = text[start:m.start()].strip("\n")
            start = m.end()
            if para.strip():
                if len(blocks) >= limits.max_elements:
                    partial = True
                    notes.append(f"stopped after {limits.max_elements} paragraphs (max_elements)")
                    break
                blocks.append(Block("paragraph", 0, para))
                if len(blocks) % 4096 == 0:
                    deadline.check()
        return Decoded(detected, Document(tuple(blocks), None), tuple(notes), partial)


__all__ = ["PlainText", "decode_text"]
