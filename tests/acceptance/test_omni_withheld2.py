"""Lane ZW2: the SECOND withheld split of the OMNIMIND benchmark (unknown, hostile, cross) and its look machinery.

Nobody but lane ZW2 opens this file or the two withheld2 modules (COMMON.md: a name containing "withheld"). It pins
both modules' SHA-256, re-derives every truth with readers of its own (the oracle of omni_oracle.py, the cross readers
of test_omni_cross_bench.py, and the parsers below: zipfile, tarfile, gzip, zstandard, wave, json, ElementTree,
hashlib), checks each hostile construction's hostile property without decoding it as a product would (nothing is
unpickled, extracted or expanded past a bound), and checks every refusal of `final --split withheld2`. It never prints
a case and never writes the real ledger.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import struct
import subprocess
import sys
import tarfile
import wave
import zipfile
import zlib

import numpy as np
import pytest
import zstandard

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import cases, final, subject
from neuroscifi.acceptance.omni import withheld2_cross as X
from neuroscifi.acceptance.omni import withheld2_unknown_hostile as W
import omni_oracle as oracle
import test_omni_cross_bench as cross_readers

ROOT = Path(__file__).resolve().parents[2]
OMNI = ROOT / "src" / "neuroscifi" / "acceptance" / "omni"
# As lane ZW2 committed them. Do not update these to make a run pass: a changed file has no look.
PINNED2 = {
    "withheld2_unknown_hostile.py": "bf296a3d4fd65ca4a833aa41de4a9eec055b516e5bbde8dd346ec9530f2e1ad3",
    "withheld2_cross.py": "a8b60d0df21fbed228a917a16ce69cb3d8e82977323bdd599ec28ecced4b2fe9",
}
CLEAN = {"git_commit": "0" * 40, "git_dirty": False}
BOUND = 384 << 20                     # hostile.MEMORY_BOUND


@pytest.fixture(autouse=True)
def drivers_pinned_as_they_are(monkeypatch):
    monkeypatch.setattr(final, "pinned_drivers", lambda path=None: final.driver_hashes())


# ---- pins, seeds, counts --------------------------------------------------------------------------------------------

def test_the_second_withheld_files_are_pinned_here_and_in_final():
    for name, sha in PINNED2.items():
        assert hashlib.sha256((OMNI / name).read_bytes()).hexdigest() == sha, name
    assert final.WITHHELD2_SHA256 == PINNED2
    assert set(final.FILE_OF2.values()) == set(PINNED2) and set(final.FILE_OF2) == {"unknown", "hostile", "cross"}


def test_the_modules_import_no_product_code():
    for name in PINNED2:
        tree = ast.parse((OMNI / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1 and not (node.module or "").startswith("neuroscifi"), (name, node.module)
                assert not any(a.name.startswith("withheld") for a in node.names) and \
                    not (node.module or "").startswith("withheld"), name
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith("neuroscifi") for a in node.names), name


def test_seeds_come_from_the_reserved_second_range_and_names_are_new():
    seeds = [c["seed"] for c in W.DISGUISED + W.TRULY_UNKNOWN + W.HOSTILE] + [p["seed"] for p in X.PAIRS]
    assert all(s in omni.WITHHELD2_SEEDS for s in seeds) and len(set(seeds)) == len(seeds)
    names = [c["name"] for c in W.DISGUISED + W.TRULY_UNKNOWN + W.HOSTILE]
    assert len(set(names)) == len(names)
    public = {c["name"] for table in (cases.UNKNOWN, cases.HOSTILE) for split in omni.SPLITS for c in table[split]}
    assert not set(names) & public
    public_files = {c["file"] for split in omni.SPLITS for c in cases.UNKNOWN[split]}
    assert not {c["file"] for c in W.DISGUISED + W.TRULY_UNKNOWN} & public_files
    tags = [c["tag"] for c in W.HOSTILE]
    assert len(set(tags)) == len(tags)
    from neuroscifi.acceptance.omni import cross_cases
    public_pairs = [p for split in cross.SPLITS for p in cross_cases.ALL[split]]
    assert not {p["question"] for p in X.PAIRS} & {p["question"] for p in public_pairs}
    assert len({p["id"] for p in X.PAIRS}) == len(X.PAIRS)


from neuroscifi.acceptance.omni import cross  # noqa: E402


def test_counts_meet_the_brief():
    c = W.counts()
    assert c["unknown"]["tasks"] >= 120 and c["unknown"]["disguised_cases"] >= 30
    assert c["hostile"]["tasks"] >= 48 and c["hostile"]["classes"] >= 16
    assert c["hostile"]["classes_in_no_public_split"] >= 8
    x = X.counts()
    assert x["pairs"] >= 48 and x["cases"] == 2 * x["pairs"]
    assert x["pairs_in_new_kinds"] * 2 >= x["pairs"]
    assert x["undecidable_pairs"] >= 6 and set(x["decidable_by_kind"]) == set(X.KINDS)
    assert set(X.NEW_KINDS).isdisjoint(cross.KINDS) and all(x["pairs_by_kind"][k] >= 3 for k in X.KINDS)


# ---- unknown: truths by an independent reading -----------------------------------------------------------------------

SIGNATURES = {b"\x1f\x8b": "gzip", b"\x28\xb5\x2f\xfd": "zstd", b"\x89PNG\r\n\x1a\n": "png", b"GIF87a": "gif",
              b"GIF89a": "gif", b"BM": "bmp", b"RIFF": "riff", b"%PDF-": "pdf", b"\x93NUMPY": "npy",
              b"PK\x03\x04": "zip", b"PK\x05\x06": "zip", b"\xff\xd8\xff": "jpeg", b"OggS": "ogg", b"fLaC": "flac",
              b"ID3": "mp3", b"\x1a\x45\xdf\xa3": "matroska", b"PAR1": "parquet", b"\x89HDF": "hdf5",
              b"\xd0\xcf\x11\xe0": "cfb", b"7z\xbc\xaf": "7z", b"Rar!": "rar", b"\xfd7zXZ": "xz", b"BZh": "bzip2",
              b"MThd": "midi", b"Salted__": "openssl", b"age-encryption": "age", b"SQLite format 3": "sqlite",
              b"\x7fELF": "elf", b"MZ": "pe", b"\xca\xfe\xba\xbe": "macho", b"wOFF": "woff", b"\x00\x00\x01\x00": "ico",
              b"ftyp": "mp4"}


def sniff(data: bytes) -> str | None:
    for sig, name in SIGNATURES.items():
        if data.startswith(sig) or (name == "mp4" and data[4:8] == sig):
            return name
    return None


def unwrap(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        return unwrap(gzip.decompress(data))
    if data[:4] == b"\x28\xb5\x2f\xfd":
        return unwrap(zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 24))
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:]
    if data[:2] == b"\xfe\xff":
        return data[2:].decode("utf-16-be").encode("utf-8")
    return data


_EXT_FORMAT = {"tsv": "tsv", "csv": "csv", "json": "json", "svg": "svg", "geojson": "geojson", "gpx": "gpx",
               "obj": "obj", "ply": "ply", "stl": "stl", "ipynb": "ipynb", "py": "python", "md": "markdown",
               "txt": "text", "xml": "xml", "html": "html", "ndjson": "ndjson", "tar": "tar"}


def _is_text(data: bytes) -> bool:
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8"), (b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le")):
        if data.startswith(bom):
            data = data[len(bom):].decode(codec).encode("utf-8")
            break
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "\x00" not in text


def test_disguised_files_hold_what_their_truths_say():
    checked = 0
    for case in W.DISGUISED:
        files, declared, questions = W.build_unknown(case)
        (name, data), = files.items()
        assert name == case["file"] and name != case["true"]
        fmt = questions[0]
        assert fmt[0] == "detect.format" and fmt[2] == {"one_of": True}
        if data.startswith(b"MZ"):                              # leading bytes, then a zip found from its end
            assert fmt[1] == ["zip"] and zipfile.ZipFile(io.BytesIO(data)).namelist()
        elif _is_text(data):                                    # text (whatever its first bytes look like)
            assert _EXT_FORMAT[case["true"].rsplit(".", 1)[1]] in fmt[1], case["name"]
        else:                                                   # binary: its signature names the outer format
            outer = sniff(data)
            accepted = {"zip": {"zip", "docx", "xlsx", "npz"}, "riff": {"wav"}}.get(outer, {outer})
            assert accepted & set(fmt[1]), case["name"]
        inner = unwrap(data) if case["wrap"] != "none" or data[:3] == b"\xef\xbb\xbf" else data
        for question, expect, tolerance, kind in questions[1:]:
            assert kind == "measure"
            if question == "detect.family":
                continue
            if question == "text.chars":
                assert omni.same(expect, len(inner.decode("utf-8")), tolerance)
                checked += 1
                continue
            got = oracle.answer(case["true"], inner, question)
            assert omni.same(expect, got, tolerance), (case["name"], question)
            checked += 1
    assert checked >= 50


def test_truly_unknown_bytes_carry_no_format_at_their_start():
    for case in W.TRULY_UNKNOWN:
        files, declared, questions = W.build_unknown(case)
        (name, data), = files.items()
        k = case["knobs"]
        if case["make"] == "head_of":
            assert sniff(data) in ({k["of"]} | ({"riff"} if k["of"] == "wav" else set()))
            assert questions[0][1] == [k["of"], "unknown"] and len(data) == k["keep"]
        else:
            assert sniff(data) is None, case["name"]
            assert questions[0][:2] == ("detect.format", "unknown") and questions[1][:2] == ("detect.family",
                                                                                                "unknown")
            try:
                data.decode("utf-8")
                text = True
            except UnicodeDecodeError:
                text = False
            assert not text, case["name"]                       # not text either
        if case["make"] == "buried":
            assert data.find(k["magic"]) == k["at"] > 0
        if case["make"] == "bad_utf16":
            with pytest.raises(UnicodeDecodeError):
                data.decode("utf-16")
        if case["make"].startswith("near_miss"):                # one byte from a real signature
            real = {b"\x89PNG\r\n\x1a\x0b": b"\x89PNG\r\n\x1a\n", b"PK\x03\x05": b"PK\x03\x04",
                    b"\x1f\x8c\x08\x00": b"\x1f\x8b\x08\x00", b"%PDG-1.4\n": b"%PDF-1.4\n",
                    b"\x28\xb5\x2f\xfe": b"\x28\xb5\x2f\xfd", b"GIF88a": b"GIF89a",
                    b"\x93NUMPZ\x01\x00": b"\x93NUMPY\x01\x00"}
            got = data[:len(k["magic"])] if "magic" in k else data[:4]
            want = real[k["magic"]] if "magic" in k else b"RIFF"
            assert sum(a != b for a, b in zip(got, want)) == 1
        contents = [qq for qq in questions if qq[3] == "cannot"]
        assert contents and all(qq[1] is None for qq in contents)


def test_unknown_task_ids_and_disguise_flags():
    tasks = W.tasks("unknown")
    assert len({t.id for t in tasks}) == len(tasks) >= 120
    assert all(t.id.startswith("unknown/withheld2/") for t in tasks)
    flagged = {t.id.split("/")[2] for t in tasks if W.is_disguised(t.id)}
    assert flagged == {c["name"] for c in W.DISGUISED}


# ---- hostile: each construction has the property its class names -----------------------------------------------------

def _inflates_past(stream: bytes, wbits: int, limit: int = BOUND) -> bool:
    d, out, chunk = zlib.decompressobj(wbits), 0, stream
    while chunk and out <= limit:
        out += len(d.decompress(chunk, 1 << 24))
        chunk = d.unconsumed_tail
    return out > limit


def _zip_raw_names(data: bytes) -> list[tuple[bytes, bytes, int]]:
    """(central name, local name, flags) per central entry, parsed by hand."""
    eocd = data.rfind(b"PK\x05\x06")
    count, _, start = struct.unpack_from("<HII", data, eocd + 10)
    out, pos = [], start
    for _ in range(count):
        flags, = struct.unpack_from("<H", data, pos + 8)
        n, e, c = struct.unpack_from("<HHH", data, pos + 28)
        offset, = struct.unpack_from("<I", data, pos + 42)
        ln, = struct.unpack_from("<H", data, offset + 26)
        out.append((data[pos + 46:pos + 46 + n], data[offset + 30:offset + 30 + ln], flags))
        pos += 46 + n + e + c
    return out


def _npy_header(data: bytes) -> dict:
    n, = struct.unpack_from("<H", data, 8)
    return ast.literal_eval(data[10:10 + n].decode("latin-1"))


def _pdf_objects(data: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj", data, re.S)}


def _laughs_size(text: str) -> int:
    ents = dict(re.findall(r'<!ENTITY (\w+) "([^"]*)">', text))
    memo = {}

    def size(name):
        if name not in memo:
            body = ents[name]
            refs = re.findall(r"&(\w+);", body)
            memo[name] = len(re.sub(r"&\w+;", "", body)) + sum(size(r) for r in refs)
        return memo[name]
    root = re.findall(r"&(\w+);", text.split("]>", 1)[1])
    return sum(size(r) for r in root)


def _injected(text: str) -> bool:
    text = text.replace("​", "").lower()
    return any(p in text for p in ("ignore your previous instructions", "forget the rules", "disregard what you were",
                                   "ignore all previous instructions", "override your safety rules"))


def _the(files):
    (name, data), = files.items()
    return name, data


def check_hostile(case, files, question, escapes):
    a = case["attack"]
    name, data = _the(files)
    assert len(data) < 64 << 20
    if a == "zstd_rle_bomb":
        assert zstandard.get_frame_parameters(data).content_size >= 1 << 30
    elif a == "pdf_flate_bomb":
        length = int(re.search(rb"/Length (\d+) /Filter /FlateDecode", data).group(1))
        start = data.index(b"/FlateDecode >>\nstream\n") + len(b"/FlateDecode >>\nstream\n")
        assert _inflates_past(data[start:start + length], 15)
    elif a == "gzip_members_bomb":
        d = zlib.decompressobj(31)
        first = d.decompress(data, 2 << 20)
        assert len(first) == 1 << 20 and len(d.unused_data) == len(data) - len(data) // 2000 and \
            data.count(data[:len(data) // 2000]) == 2000
    elif a == "mixed_wrapper_bomb":
        middle = zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 24)
        inner = gzip.decompress(middle)
        assert struct.unpack("<I", inner[-4:])[0] == 1 << 30 and _inflates_past(inner, 31)
    elif a == "tar_size_lie":
        assert int(data[124:135], 8) > 1 << 32 and len(data) < 1 << 16
    elif a == "png_chunk_length_lie":
        assert struct.unpack(">I", data[data.find(b"IDAT") - 4:data.find(b"IDAT")])[0] > len(data)
    elif a == "wav_fmt_length_lie":
        assert struct.unpack("<I", data[16:20])[0] > len(data)
    elif a == "gif_huge_canvas":
        w, h = struct.unpack("<HH", data[6:10])
        assert w * h * 3 > BOUND
    elif a == "bmp_huge_width":
        assert struct.unpack("<i", data[18:22])[0] * 3 > BOUND
    elif a in ("svg_laughs", "xml_quadratic", "xlsx_entity_part"):
        text = data.decode() if a != "xlsx_entity_part" else next(
            zipfile.ZipFile(io.BytesIO(data)).read(n) for n in zipfile.ZipFile(io.BytesIO(data)).namelist()
            if n.endswith("sharedStrings.xml")).decode()
        assert "<!DOCTYPE" in text and _laughs_size(text) > BOUND
    elif a == "xml_parameter_entity":
        assert re.search(rb'<!ENTITY % \w+ SYSTEM "file:', data)
    elif a == "epub_external_part":
        part = zipfile.ZipFile(io.BytesIO(data)).read("OEBPS/chap2.xhtml")
        assert re.search(rb'<!ENTITY \w+ SYSTEM "file:', part) and question == "doc.headings"
    elif a in ("tar_absolute", "tgz_inner_dotdot"):
        raw = gzip.decompress(data) if a == "tgz_inner_dotdot" else data
        names = tarfile.open(fileobj=io.BytesIO(raw)).getnames()
        assert any(n.startswith("/") or posixpath.normpath(n).startswith("..") for n in names) and escapes
    elif a == "zip_backslash":
        assert any("..\\" in n for n in zipfile.ZipFile(io.BytesIO(data)).namelist()) and escapes
    elif a == "zip_symlink_relative":
        info = zipfile.ZipFile(io.BytesIO(data)).getinfo("notes.txt")
        assert (info.external_attr >> 16) & 0o170000 == 0o120000
    elif a == "truncated_gif":
        assert not data.endswith(b"\x3b") and data.startswith(b"GIF89a")
    elif a == "truncated_stl":
        assert len(data) != 84 + 50 * struct.unpack("<I", data[80:84])[0]
    elif a == "truncated_xlsx":
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(data))
    elif a == "mixed_nesting":
        layers, blob = 0, data
        for member in question.split("::")[:-1]:
            blob = (zipfile.ZipFile(io.BytesIO(blob)).read(member) if blob[:2] == b"PK" else
                    tarfile.open(fileobj=io.BytesIO(blob)).extractfile(member).read())
            layers += 1
        assert layers == case["knobs"]["depth"] and blob.startswith(b"k,v")
    elif a == "json_object_deep":
        assert data.count(b'{"a":') == case["knobs"]["depth"] > 1000
    elif a == "html_deep":
        assert data.count(b"<div>") == case["knobs"]["depth"] > 1000
    elif a == "markdown_deep":
        assert data.split(b"#")[0].count(b"> ") == case["knobs"]["depth"] > 1000
    elif a == "ndjson_huge_value":
        assert max(len(line) for line in data.split(b"\n")) > 50 << 20
    elif a in ("png_zip", "wav_zip"):
        assert sniff(data) == ("png" if a == "png_zip" else "riff")
        assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["payload.txt"]
    elif a.startswith("inject_"):
        if a in ("inject_odt", "inject_epub"):
            z = zipfile.ZipFile(io.BytesIO(data))
            text = " ".join(z.read(n).decode() for n in z.namelist() if n.endswith(("content.xml", ".xhtml")))
        elif a == "inject_ipynb":
            text = " ".join("".join(c["source"]) for c in json.loads(data)["cells"])
        else:
            text = data.decode()
        assert _injected(text) and question == "doc.safe_text"
    elif a == "stl_count_lie":
        assert struct.unpack("<I", data[80:84])[0] != (len(data) - 84) // 50
    elif a == "ply_count_lie":
        declared = int(re.search(rb"element vertex (\d+)", data).group(1))
        assert declared > len(data)
    elif a == "zip_count_lie":
        eocd = data.rfind(b"PK\x05\x06")
        assert struct.unpack_from("<H", data, eocd + 10)[0] != len(zipfile.ZipFile(io.BytesIO(data)).infolist())
    elif a == "npy_object":
        assert _npy_header(data)["descr"] == "|O"
    elif a in ("npz_object", "zip_pickle_member"):
        z = zipfile.ZipFile(io.BytesIO(data))
        member = question.split("::")[0]
        member = member if member.endswith(".npy") else member + ".npy"
        assert _npy_header(z.read(member))["descr"] == "|O"
    elif a == "pdf_kids_cycle":
        objs = _pdf_objects(data)
        assert b"/Kids [3 0 R]" in objs[2] and b"/Kids [2 0 R]" in objs[3]
    elif a == "pdf_prev_loop":
        start = int(re.search(rb"startxref\n(\d+)", data).group(1))
        assert int(re.search(rb"/Prev (\d+)", data).group(1)) == start
    elif a == "pdf_length_self":
        assert _pdf_objects(data)[4].startswith(b"<< /Length 4 0 R >>")
    elif a in ("zip_duplicate", "zip_dotslash_alias"):
        names = [posixpath.normpath(n) for n in zipfile.ZipFile(io.BytesIO(data)).namelist()]
        assert len(set(names)) < len(names)
    elif a == "xlsx_duplicate_sheet":
        book = zipfile.ZipFile(io.BytesIO(data)).read("xl/workbook.xml").decode()
        sheets = re.findall(r'<sheet name="([^"]+)"', book)
        assert len(set(sheets)) < len(sheets)
    elif a in ("zip_central_evil", "zip_local_evil"):
        central, local, _ = _zip_raw_names(data)[0]
        assert central != local and (b".." in central) != (b".." in local) and escapes
    elif a == "zip_bad_crc":
        assert zipfile.ZipFile(io.BytesIO(data)).testzip() == "second.csv"
    elif a == "png_bad_crc":
        at = data.find(b"IDAT") - 4
        n, = struct.unpack_from(">I", data, at)
        assert struct.unpack_from(">I", data, at + 8 + n)[0] != zlib.crc32(data[at + 4:at + 8 + n]) & 0xFFFFFFFF
    elif a == "gzip_bad_trailer":
        with pytest.raises(gzip.BadGzipFile):
            gzip.decompress(data)
    elif a == "wav_zero_block_align":
        assert struct.unpack_from("<H", data, 32)[0] == 0
    elif a == "wav_zero_rate":
        assert struct.unpack_from("<I", data, 24)[0] == 0
    elif a == "bmp_int_min_height":
        assert struct.unpack_from("<i", data, 22)[0] == -(1 << 31)
    elif a == "json_megadigit":
        assert len(re.search(rb"\d+", data).group(0)) > sys.get_int_max_str_digits() > 0
    elif a == "obj_index_extreme":
        assert max(int(x) for line in data.splitlines() if line.startswith(b"f ") for x in line.split()[1:]) >= 2 ** 63 - 1
    elif a == "docx_bomb_part":
        z = zipfile.ZipFile(io.BytesIO(data))
        info = z.getinfo("word/document.xml")
        assert info.file_size >= 1 << 30 and _inflates_past(data[info.header_offset + 30 + len(info.filename):
                                                                  info.header_offset + 30 + len(info.filename)
                                                                  + info.compress_size], -15)
    elif a in ("tar_hardlink_abs", "tar_hardlink_dotdot"):
        links = [m for m in tarfile.open(fileobj=io.BytesIO(data)).getmembers() if m.islnk()]
        assert links and (links[0].linkname.startswith("/") or links[0].linkname.startswith(".."))
    elif a == "zip_nul_name":
        assert b"\x00" in _zip_raw_names(data)[0][0]
    elif a == "zip_bad_utf8_name":
        raw, _, flags = _zip_raw_names(data)[0]
        assert flags & 0x800
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
    elif a == "csv_columns":
        assert data.split(b"\n")[0].count(b",") + 1 == case["knobs"]["columns"] >= 1_000_000
    elif a == "json_elements":
        assert data.count(b",") + 1 == case["knobs"]["elements"] >= 1_000_000
    else:
        raise AssertionError(f"no check for {a}")


def test_every_hostile_construction_has_its_hostile_property():
    built = W.hostile_tasks()
    assert len(built) == len(W.HOSTILE)
    for case, (task, escapes) in zip(W.HOSTILE, built):
        assert task.kind == "refuse" and task.expect is None and task.id == f"hostile/withheld2/{case['name']}"
        check_hostile(case, dict(task.files), task.question, escapes)
        for path in escapes:
            assert case["tag"] in path and ("omnimind-w2-" in path)


def test_hostile_classes_public_and_new():
    public_attacks = {c["attack"] for split in omni.SPLITS for c in cases.HOSTILE[split]}
    assert len(public_attacks) >= 20 and not public_attacks & set(W.CLASSES)                  # every construction here is a new construction
    new = {c["class"] for c in W.HOSTILE} - W.PUBLIC_CLASSES
    assert len(new) >= 8 and all(sum(c["class"] == k for c in W.HOSTILE) >= 1 for k in new)
    assert len({c["class"] for c in W.HOSTILE}) >= 16


# ---- cross: truths and twins -------------------------------------------------------------------------------------------

def _gif_frames(data: bytes) -> int:
    pos = 13 + (3 << ((data[10] & 7) + 1) if data[10] & 0x80 else 0)
    frames = 0
    while data[pos] != 0x3B:
        if data[pos] == 0x21:
            pos += 2
        else:
            frames += 1
            flags = data[pos + 9]
            pos += 10 + ((3 << ((flags & 7) + 1)) if flags & 0x80 else 0) + 1
        while data[pos]:
            pos += data[pos] + 1
        pos += 1
    return frames


def _image_caption_truth(pair, files):
    k = pair["knobs"]
    img, cap = files[k["img"]], files[k["cap"]].decode()
    numbers = [int(v) for v in re.findall(r"\d+", cap)]
    if k["fmt"] == "gif":
        return _gif_frames(img) == numbers[-1]
    w, h = struct.unpack(">II", img[16:24])
    if k["stat"] == "height":
        return h == numbers[-1]
    return [w, h] == numbers[-2:]


def _diagram_truth(pair, files):
    k = pair["knobs"]
    import xml.etree.ElementTree as ET
    root = ET.fromstring(files[k["svg"]])
    ns = "{http://www.w3.org/2000/svg}"
    at = {}
    for g in root.iter(ns + "g"):
        c, t = g.find(ns + "circle"), g.find(ns + "text")
        at[(float(c.get("cx")), float(c.get("cy")))] = t.text
    drawn = {frozenset((at[(float(e.get("x1")), float(e.get("y1")))], at[(float(e.get("x2")), float(e.get("y2")))]))
             for e in root.iter(ns + "line")}
    written = {frozenset(m) for m in re.findall(rf"- (\w+) {k['verb']} (\w+)", files[k["txt"]].decode())}
    return drawn == written


def _checksum_truth(pair, files):
    k = pair["knobs"]
    z = zipfile.ZipFile(io.BytesIO(files[k["zip"]]))
    listed = dict(reversed(line.split("  ", 1)) for line in files[k["sums"]].decode().splitlines())
    return all(hashlib.sha256(z.read(n)).hexdigest() == listed[n] for n in z.namelist())


def _onsets(data: bytes) -> list[float]:
    with wave.open(io.BytesIO(data)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32767
    loud = np.abs(x) > 0.05
    out, quiet = [], rate
    for i, on in enumerate(loud):
        if on and quiet >= int(0.3 * rate):
            out.append(i / rate)
        quiet = 0 if on else quiet + 1
    return out


def _cue_truth(pair, files):
    k = pair["knobs"]
    heard = _onsets(files[k["wav"]])
    listed = [int(m) * 60 + float(s) for m, s in re.findall(r"^(\d+):(\d+\.\d+)", files[k["cues"]].decode(), re.M)]
    return len(heard) == len(listed) and all(abs(a - b) < 0.05 for a, b in zip(heard, listed))


TRUTH = {"image_size_caption": _image_caption_truth, "diagram_text": _diagram_truth,
         "archive_checksums": _checksum_truth, "audio_cues": _cue_truth}


def test_cross_truth_is_what_the_bytes_say():
    for pair in X.PAIRS:
        for matching in (True, False):
            files = X.build(pair, matching)
            if pair["kind"] in X.PUBLIC_KINDS:
                assert cross_readers.truth_from_bytes(pair, files) is matching, (pair["id"], matching)
            elif pair["kind"] in TRUTH:
                assert TRUTH[pair["kind"]](pair, files) is matching, (pair["id"], matching)


def test_cross_twins_differ_in_one_file_and_undecidable_kinds_expect_cannot():
    tasks = X.tasks()
    assert len(tasks) == 2 * len(X.PAIRS) and len({t.id for t in tasks}) == len(tasks)
    for a, b in zip(tasks[::2], tasks[1::2]):
        assert a.id[:-1] == b.id[:-1] and a.truth is True and b.truth is False and a.question == b.question
        assert set(a.files) == set(b.files)
        differ = [n for n in a.files if a.files[n] != b.files[n]]
        assert len(differ) == 1, a.id
        if X.DECIDABLE[a.kind][0]:
            assert a.expect is True and b.expect is False
        else:
            assert a.expect is None and b.expect is None
            left, right = a.files[differ[0]].decode().split(), b.files[differ[0]].decode().split()
            head = next(i for i, (x, y) in enumerate(zip(left, right)) if x != y)
            tail = next(i for i, (x, y) in enumerate(zip(left[::-1], right[::-1])) if x != y)
            assert len(left) - head - tail <= 3 and len(right) - head - tail <= 3   # one value: one short span
    assert X.build(X.PAIRS[0], True) == X.build(X.PAIRS[0], True)


def test_cross_references_score_as_designed():
    out = final.score_cross2("always_consistent")
    decidable_pairs = sum(1 for p in X.PAIRS if X.DECIDABLE[p["kind"]][0])
    assert out["correct"]["k"] == decidable_pairs and out["both_twins_correct"]["k"] == 0
    assert out["undecidable"]["correct"]["k"] == 0 and out["undecidable"]["wrong"] == out["undecidable"]["cases"]
    today = final.score_cross2("today")
    assert today["answered"]["k"] == 0 and today["correct"]["k"] == today["undecidable"]["cases"]

    def reader(files, question):                                # this test's own readers, from the bytes
        for pair in X.PAIRS:
            if pair["question"] == question:
                if not X.DECIDABLE[pair["kind"]][0]:
                    return {"answer": None, "because": ["meaning is not measured"]}
                read = TRUTH.get(pair["kind"]) or cross_readers.truth_from_bytes
                return {"answer": read(pair, files), "because": ["read"]}
    best = final.score_cross2(reader)
    assert best["correct"]["k"] == best["n_cases"] and best["both_twins_correct"]["k"] == best["n_pairs"]


# ---- the look machinery -------------------------------------------------------------------------------------------------

def _script(name, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          timeout=300, cwd=ROOT)


def _ledger_bytes():
    return final.LEDGER.read_bytes() if final.LEDGER.exists() else b""


def test_run_refuses_the_second_withheld_split():
    before = _ledger_bytes()
    done = _script("bench_omni.py", "run", "--split", "withheld2", "--out", "/tmp/never-written.json")
    assert done.returncode == 2 and "refused" in done.stderr and "final" in done.stderr
    done = _script("bench_omni_forms.py", "run", "--family", "cross", "--split", "withheld2")
    assert done.returncode == 2 and "refused" in done.stderr and "final" in done.stderr
    with pytest.raises(ValueError):
        omni.check_split("withheld2")
    assert _ledger_bytes() == before


def test_final_withheld2_refuses_before_recording(tmp_path):
    before = _ledger_bytes()
    out = str(tmp_path / "x.json")
    for script, family in (("bench_omni.py", "unknown"), ("bench_omni_forms.py", "cross")):
        base = ["final", "--split", "withheld2", "--family", family, "--subject", "today"]
        done = _script(script, *base, "--out", out)                                     # no note
        assert done.returncode == 2 and "--note" in done.stderr
        done = _script(script, *base, "--note", "  ", "--out", out)                     # blank note
        assert done.returncode == 2 and "note" in done.stderr
        done = _script(script, *base, "--note", "t", "--out", str(ROOT / "omni_w2_never.json"))
        assert done.returncode == 2 and "inside the checkout" in done.stderr
    done = _script("bench_omni.py", "final", "--split", "withheld2", "--family", "intake", "--subject", "today",
                   "--note", "t", "--out", out)
    assert done.returncode == 2 and "only the families unknown and hostile" in done.stderr
    done = _script("bench_omni_forms.py", "final", "--split", "withheld2", "--family", "forms", "--subject", "today",
                   "--note", "t", "--out", out)
    assert done.returncode == 2 and "only the family cross" in done.stderr
    assert not (tmp_path / "x.json").exists() and _ledger_bytes() == before


def test_check_refuses_for_the_second_split(monkeypatch):
    """The owner opened this split for its three families (WAVE3, decision 1; ADR-081): every other check still runs
    first, and a well-formed request ends in `final.Spent` (lane FX4, R8: explicit, replacing an expected failure)."""
    monkeypatch.setattr(final, "pinned_drivers", lambda path=None: final.driver_hashes())
    for families in (["unknown", "hostile", "cross"], ["unknown"], ["cross"]):
        with pytest.raises(final.Spent):
            final.check("note", families, CLEAN, split="withheld2")
    with pytest.raises(final.Refused, match="unknown or missing family"):
        final.check("note", ["forms"], CLEAN, split="withheld2")
    with pytest.raises(final.Refused, match="clean"):
        final.check("note", ["cross"], {"git_commit": "a", "git_dirty": True}, split="withheld2")
    monkeypatch.setitem(final.WITHHELD2_SHA256, "withheld2_cross.py", "0" * 64)
    with pytest.raises(final.Refused, match="changed"):
        final.check("note", ["cross"], CLEAN, split="withheld2")


def test_a_withheld2_look_is_refused_as_spent_and_recorded(tmp_path, monkeypatch):
    """A look at the opened split is refused with `final.Spent` before anything is scored, and the refusal is on the
    record (a ledger in tmp_path; the tracked ledger is untouched). References and the refusing probe only."""
    ledger = tmp_path / "ledger.jsonl"
    before = _ledger_bytes()
    monkeypatch.setattr(final, "pinned_drivers", lambda path=None: final.driver_hashes())

    def boom(subject):
        raise RuntimeError("scoring started")
    monkeypatch.setattr(final, "score_cross2", boom)
    with pytest.raises(final.Spent):
        final.look(["cross"], "always_inconsistent", "always_inconsistent", "test of the machinery", script="test",
                   ledger=ledger, env=CLEAN, split="withheld2")
    with pytest.raises(final.Spent):
        final.look(["unknown", "hostile"], subject.get("probe:refuse"), "probe:refuse", "test of the machinery",
                   script="test", ledger=ledger, env=CLEAN, split="withheld2")
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert len(lines) == 2 and all(x["split"] == "withheld2" and "refused" in x for x in lines)
    assert _ledger_bytes() == before
