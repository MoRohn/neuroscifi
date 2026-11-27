"""Lane FX5 (ADR-081, C5): the hostile classes the second review found still open, closed by class.

- PDF cross-reference loops: the chain is parsed (every startxref, every /Prev and /XRefStm, names decoded), so a
  comment naming "startxref", a name escape "/P#72ev" or a looping /XRefStm no longer hides a loop; honest
  incremental and hybrid files stay readable.
- The address screen: an address to a model or agent (normalised; "To the AI:", "Bot,", a comment's words) and an
  imperative to disregard, reveal, or decide what to answer. Best effort, English only, measured.
- Honest refusals: a Markdown thematic break is a rule, not nesting; a Windows code-page ZIP name with its Unicode
  Path field is one name written twice.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx5_address_lines as L  # noqa: E402

from neuroscifi.artifacts import adapters  # noqa: E402
from neuroscifi.artifacts.adapters.pdf import xref_chain_loops  # noqa: E402
from neuroscifi.artifacts.types import ArtifactError, Refused  # noqa: E402


def _pdf(*, trailer=b"", tail=b"", prev="self", update=False, xrefstm=None) -> bytes:
    """A one-page PDF built here. prev: "self" (the trailer's /Prev is its own section) or None; `trailer` more
    trailer keys ("{x}" is the section's own offset); `tail` bytes after %%EOF; `update` an honest incremental
    section after it; `xrefstm` a cross-reference stream (its /Prev "self" makes it loop) named by /XRefStm."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 120] >>"]
    out = bytearray(b"%PDF-1.5\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    stm = None
    if xrefstm is not None:
        stm = len(out)
        loop = b" /Prev %d" % stm if xrefstm == "self" else b""
        out += b"4 0 obj\n<< /Type /XRef /Size 5 /W [1 2 1] /Length 0" + loop + b" >>\nstream\n\nendstream\nendobj\n"
    x = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    keys = trailer.replace(b"{x}", b"%d" % x)
    if prev == "self":
        keys += b" /Prev %d" % x
    if stm is not None:
        keys += b" /XRefStm %d" % stm
    out += b"trailer\n<< /Size 4 /Root 1 0 R" + keys + b" >>\nstartxref\n%d\n%%%%EOF\n" % x + tail
    if update:
        at = len(out)
        out += b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 240] >>\nendobj\n"
        x2 = len(out)
        out += b"xref\n3 1\n%010d 00000 n \n" % at
        out += b"trailer\n<< /Size 4 /Root 1 0 R /Prev %d >>\nstartxref\n%d\n%%%%EOF\n" % (x, x2)
    return bytes(out)


def _refused(data: bytes):
    with pytest.raises(ArtifactError) as err:
        adapters.decode(data, name="f.pdf")
    assert isinstance(err.value, Refused) and err.value.rule == "self_reference"


@pytest.mark.parametrize("data", [
    _pdf(tail=b"% the startxref above is final\n"),                       # review s2 M2: a comment after %%EOF
    _pdf(tail=b"% see startxref 0 below\n%%EOF\n"),                       # own: a comment naming a number too
    _pdf(prev=None, trailer=b" /P#72ev {x}"),                            # review s2: a name escape of /Prev
    _pdf(prev=None, trailer=b" /#50#72#65#76 {x}"),                      # own: every letter escaped
    _pdf(prev=None, xrefstm="self"),                                      # review s2: a looping /XRefStm
    _pdf(prev=None, trailer=b" /Prev 9 /Prev {x}"),                      # own: a second /Prev key
    _pdf(tail=b"startxref\n9\n%%EOF\n"),                                  # own: a later decoy startxref
])
def test_a_looping_chain_is_refused_in_any_form(data):
    assert xref_chain_loops(data)
    _refused(data)


def test_honest_chains_stay_readable():
    for data in (_pdf(prev=None), _pdf(prev=None, update=True), _pdf(prev=None, xrefstm="plain"),
                 _pdf(prev=None, tail=b"% a comment that names startxref\n")):
        assert not xref_chain_loops(data)


# ---- the artifacts-only address screen (H-B), generalised by class -------------------------------------------------


def _screened(line: str) -> bool:
    try:
        return bool(adapters.decode(L.note(line), name="n.md").flagged)
    except Refused as err:
        assert err.rule == "injection"
        return True


@pytest.mark.parametrize("line", L.REVIEW1 + L.REVIEW2 + L.OWN)
def test_lines_addressed_to_an_ai_reader_with_a_directive_are_refused(line):
    assert _screened(line)


@pytest.mark.parametrize("line", L.HONEST + L.FRESH_HONEST)
def test_honest_lines_that_mention_assistants_bots_or_models_are_read(line):
    assert not _screened(line)


def test_the_screen_is_best_effort_its_measured_limits_are_recorded():
    # ADR-081: measured once on lines written after the screen was changed, never tuned on them: 5 of 12 caught
    assert sum(_screened(x) for x in L.FRESH) == 5
    assert all(_screened(x) for x in L.COST)                       # honest lines it refuses (a person named Claude)
    assert not any(_screened(x) for x in L.NOT_ENGLISH)            # English only


# ---- honest refusals the second review found (L4) -----------------------------------------------------------------

@pytest.mark.parametrize("rule", ["- " * 300, "* " * 400, "_ " * 1000, " -  -  - " * 100])
def test_a_long_thematic_break_is_a_rule_not_nesting(rule):
    out = adapters.decode(f"# T\n\nText.\n\n{rule}\n\nMore.\n".encode(), name="n.md")
    assert out.representation.kind == "document"


def test_nesting_by_markers_past_the_bound_stays_refused():
    for line in ("- " * 100000, "> " * 1000 + "x", "- " * 300 + "x"):
        with pytest.raises(Refused) as err:
            adapters.decode(f"# T\n\n{line}\n".encode(), name="n.md")
        assert err.value.rule == "max_depth"


def _zip_raw(raw_name: bytes, body: bytes, extra: bytes = b"") -> bytes:
    """One stored member whose name is `raw_name` (no UTF-8 flag) with `extra` in both headers, hand-assembled."""
    crc = zlib.crc32(body) & 0xFFFFFFFF
    local = struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0, crc, len(body), len(body), len(raw_name),
                        len(extra)) + raw_name + extra + body
    central = struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 0, 0, 0, crc, len(body), len(body),
                          len(raw_name), len(extra), 0, 0, 0, 0, 0) + raw_name + extra
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


def _unicode_field(raw: bytes, name: str, crc_of: bytes | None = None) -> bytes:
    body = b"\x01" + struct.pack("<I", zlib.crc32(raw if crc_of is None else crc_of) & 0xFFFFFFFF) + name.encode()
    return struct.pack("<HH", 0x7075, len(body)) + body


def test_a_windows_code_page_name_with_its_unicode_path_is_read_under_that_name():
    raw = "Łódź.txt".encode("cp852")
    out = adapters.decode(_zip_raw(raw, b"hello\n", _unicode_field(raw, "Łódź.txt")), name="w.zip")
    assert [m.path for m in out.representation.members] == ["Łódź.txt"]


_LODZ = "\u0141\u00f3d\u017a.txt"


@pytest.mark.parametrize("raw,field", [
    (b"safe.csv", _unicode_field(b"safe.csv", "../../evil.csv")),                     # another file (review s, M5)
    (b"safe.csv", _unicode_field(b"safe.csv", "other.csv")),                          # own: another plain name
    (_LODZ.encode("cp852"), _unicode_field(_LODZ.encode("cp852"), _LODZ, crc_of=b"x")),  # own: CRC of another name
    (_LODZ.encode("cp852"), _unicode_field(_LODZ.encode("cp852"), "\u0141\u00f3d\u017a.exe")),  # own: one letter off
])
def test_a_unicode_path_naming_another_file_stays_refused(raw, field):
    with pytest.raises(Refused) as err:
        adapters.decode(_zip_raw(raw, b"a,b\n1,2\n", field), name="w.zip")
    assert err.value.rule in ("name_mismatch", "header_mismatch")
