"""Product defects the development run of the OMNIMIND benchmark found (integration lane, item 5), each with the
test that fails without its fix. Inputs are assembled here with struct, zlib, zipfile and tarfile."""
import io
import struct
import tarfile
import zipfile
import zlib

import pytest

from neuroscifi.artifacts import adapters
from neuroscifi.artifacts import types as T


def _zip(members, *, stored=False) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), data)
    return out.getvalue()


def _pdf(text: str) -> bytes:
    body = f"BT /F1 12 Tf 20 20 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R"
            b" >> >> >>", b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _png() -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80")) + chunk(b"IEND", b""))


# ---- a file that is also a ZIP archive (hostile/development/pdf-that-is-a-zip was answered as a PDF) -----------------

@pytest.mark.parametrize("head", [_pdf("quiet words"), _png()], ids=["pdf", "png"])
def test_a_file_that_is_also_a_zip_archive_is_refused_not_read_one_way(head):
    adapters.decode(head)                                           # alone, each reads
    polyglot = head + _zip([("payload.txt", b"the other reading\n")])
    with pytest.raises(T.Refused) as e:
        adapters.decode(polyglot)
    assert e.value.rule == "polyglot" and "also a ZIP archive" in str(e.value)


def test_zip_based_formats_archives_and_stray_bytes_are_not_polyglots():
    inner = _zip([("a.txt", b"inside\n")], stored=True)
    assert adapters.decode(_zip([("inner.zip", inner), ("b.txt", b"x\n")], stored=True)).detected.format == "zip"
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w", format=tarfile.USTAR_FORMAT) as t:
        info = tarfile.TarInfo("last.zip")
        info.size = len(inner)
        t.addfile(info, io.BytesIO(inner))                          # a stored ZIP as the tar's last member
    assert adapters.decode(tar.getvalue()).representation.kind == "bundle"
    stray = _pdf("the bytes PK") + b"PK\x05\x06" + b"\x00" * 18   # an end record with an empty directory
    assert adapters.decode(stray).representation.kind == "document"


# ---- a member past the depth limit is refused, by the chain's own rule (zip-nested-20 read it as "not decoded") -----

def test_a_member_nested_past_max_depth_is_refused_by_name():
    data = b"deep,1\n"
    for level in range(6):
        data = _zip([(f"level{level}.zip" if level else "deep.csv", data)])
    rep = adapters.decode(data).representation
    for _ in range(T.Limits().max_depth):
        (member,) = rep.members
        rep = member.inner
    (member,) = rep.members
    assert member.inner is None and member.note.startswith("refused:") and "max_depth" in member.note
