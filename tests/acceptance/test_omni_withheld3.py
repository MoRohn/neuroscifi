"""The THIRD withheld split of the OMNIMIND benchmark (lane ZW3): its pins, its independence, its counts, and every
truth re-derived from the bytes by readers of this test (or of the earlier tests: omni_oracle.py, the public cross
readers of test_omni_cross_bench.py), never by calling the generators back.

Nobody but lane ZW3 opens this file or the two case files (acceptance/omni/withheld3_*.py). Nothing here prints a
case. Hostile files are checked for their hostile property without decoding them as a product would: headers are
parsed with struct, inflation is streamed and stops at a bound, nothing is extracted, executed or unpickled."""
import ast
import gzip
import hashlib
import io
import json
import math
import posixpath
import re
import struct
import tarfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

import numpy as np
import pytest

import omni_oracle as oracle
import test_omni_cross_bench as public_cross
from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import cases, cross_cases, final
from neuroscifi.acceptance.omni import withheld3_cross as X
from neuroscifi.acceptance.omni import withheld3_unknown_hostile as W

ROOT = final.REPO
OMNI = ROOT / "src" / "neuroscifi" / "acceptance" / "omni"
# The third split's files as lane ZW3 committed them. Do not update these to make a run pass: a changed file has been
# seen or edited, and a look at it stops being comparable.
PINNED = {
    "withheld3_unknown_hostile.py": "cdf4c891a652ccc3ce103975a6ce303f0497237d3dc132de27572361503f7c38",
    "withheld3_cross.py": "058ddc14897093ca8723d8bdaa38c3dea76aff1ae7c879f2cef6c3bd6f2f3821",
}
BOUND = 256 << 20                       # DESIGN's max_decoded_bytes: a bomb here must expand past it
# Kinds the second split's look named in the benchmark documents (the third split's new kinds avoid them).
NAMED_BEFORE = {"image_size_caption", "image_depicts", "translation", "diagram_text", "archive_checksums",
                "audio_cues"}


# ---- pins, independence, seeds, names, counts -------------------------------------------------------------------------

def test_the_files_are_pinned_here_and_in_final():
    for name, sha in PINNED.items():
        assert hashlib.sha256((OMNI / name).read_bytes()).hexdigest() == sha, name
    assert final.WITHHELD3_SHA256 == PINNED
    assert set(final.FILE_OF3.values()) == set(PINNED) and set(final.FILE_OF3) == {"unknown", "hostile", "cross"}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            yield node.module or "", [a.name for a in node.names], node.level
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, [], 0


def test_the_case_files_import_no_product_code_and_no_other_withheld_module():
    allowed = {"", "intake", "cross"}
    for name in PINNED:
        for module, names, level in _imports(OMNI / name):
            assert not module.startswith("neuroscifi") and "withheld" not in module, (name, module)
            assert not any("withheld" in n for n in names), (name, names)
            if level:
                assert level == 1 and module in allowed, (name, module)


def test_every_seed_is_reserved_and_distinct():
    seeds = [c["seed"] for c in W.UNKNOWN] + [c["seed"] for c in W.HOSTILE] + [p["seed"] for p in X.PAIRS]
    assert len(seeds) == len(set(seeds))
    assert all(s in omni.WITHHELD3_SEEDS for s in seeds)


def _public_cross_files():
    out = set()
    for pairs in cross_cases.ALL.values():
        for p in pairs:
            out |= {v for v in p["knobs"].values() if isinstance(v, str) and "." in v}
    return out


def test_nothing_repeats_a_public_case():
    public_names = {c["name"] for t in (cases.UNKNOWN, cases.HOSTILE) for split in t.values() for c in split}
    assert not public_names & {c["name"] for c in (*W.UNKNOWN, *W.HOSTILE)}
    public_pairs = [p for pairs in cross_cases.ALL.values() for p in pairs]
    assert not {p["id"] for p in public_pairs} & {p["id"] for p in X.PAIRS}
    assert not {p["question"] for p in public_pairs} & {p["question"] for p in X.PAIRS}
    mine = {v for p in X.PAIRS for v in p["knobs"].values() if isinstance(v, str) and "." in v}
    assert not mine & _public_cross_files()
    assert len({p["question"] for p in X.PAIRS}) == len(X.PAIRS)


def test_counts_meet_the_brief():
    """At least the second split's counts (lane-zw2.md) and at least a third of the classes / kinds new."""
    unknown = W.tasks("unknown")
    disguised = [t for t in unknown if W.is_disguised(t.id)]
    assert len(W.UNKNOWN) >= 60 and len(unknown) >= 171
    assert sum(c["kind"] == "disguised" for c in W.UNKNOWN) >= 36 and len(disguised) >= 103
    assert sum(c["kind"] == "unknown" for c in W.UNKNOWN) >= 24 and len(unknown) - len(disguised) >= 68
    public_attacks = {c["attack"] for split in cases.HOSTILE.values() for c in split}
    public_makes = {c["make"] for split in cases.UNKNOWN.values() for c in split}
    assert len(W.HOSTILE) >= 62 and len(W.HOSTILE_CLASSES) >= 25
    assert all(c["public"] is None or c["public"] in public_attacks for c in W.HOSTILE)
    assert not set(W.HOSTILE_NEW_CLASSES) & public_attacks
    assert 3 * len(W.HOSTILE_NEW_CLASSES) >= len(W.HOSTILE_CLASSES)
    assert not set(W.UNKNOWN_CLASSES) & public_makes and len(W.UNKNOWN_CLASSES) >= 25
    assert len(X.PAIRS) >= 49 and len(X.KINDS) >= 12 and 3 * len(X.NEW_KINDS) >= len(X.KINDS)
    assert not set(X.NEW_KINDS) & (set(X.PUBLIC_KINDS) | NAMED_BEFORE)
    undecidable = [k for k in X.KINDS if not X.DECIDABLE[k][0]]
    assert len(undecidable) >= 2 and sum(p["kind"] in undecidable for p in X.PAIRS) >= 9
    assert set(X.DECIDABLE) == set(X.KINDS) and all(reason for _, reason in X.DECIDABLE.values())


# ---- unknown: the truths ------------------------------------------------------------------------------------------------

def _unzstd(data: bytes) -> bytes:
    """A Zstandard frame of raw blocks, read by hand (the only block type this split's disguises use)."""
    assert data[:4] == b"\x28\xb5\x2f\xfd"
    fhd = data[4]
    pos = 5 + (0 if fhd & 0x20 else 1) + {0: 1 if fhd & 0x20 else 0, 1: 2, 2: 4, 3: 8}[fhd >> 6]
    out = bytearray()
    while True:
        header = int.from_bytes(data[pos:pos + 3], "little")
        last, kind, size = header & 1, (header >> 1) & 3, header >> 3
        assert kind == 0
        out += data[pos + 3:pos + 3 + size]
        pos += 3 + size
        if last:
            return bytes(out)


ORACLE_NAME = {
    "tsv_named_as_log": "x.tsv", "text_header_like_magic": "x.csv", "csv_semicolon_decimal_comma": "x.csv",
    "csv_quoted_newlines_crlf": "x.csv", "json_utf16_big_endian": "x.json", "json_records_named_csv": "x.json",
    "xml_utf16_little_endian": "x.xml", "geojson_line_as_text": "x.geojson", "gpx_named_xml": "x.gpx",
    "svg_without_declaration": "x.svg", "npy_named_bin": "x.npy", "npy_fortran_named_dat": "x.npy",
    "ply_ascii_as_text": "x.ply", "ply_binary_no_extension": "x.ply", "obj_named_dat": "x.obj",
    "stl_ascii_as_text": "x.stl", "stl_binary_named_obj": "x.stl", "gif_named_png": "x.gif",
    "gif_no_extension": "x.gif", "bmp32_named_ico": "x.bmp", "ppm_ascii_as_text": "x.ppm",
    "pgm_16bit_named_raw": "x.pgm", "png_16bit_named_bin": "x.png", "png_palette_named_gif": "x.png",
    "png_interlaced_no_extension": "x.png", "apng_named_gif": "x.png", "wav_8bit_named_raw": "x.wav",
    "wav_named_mp3": "x.wav", "wav_odd_chunk_padding": "x.wav", "gzip_wav_named_wav": "x.wav",
    "tar_no_extension": "x.tar", "tar_gzip_declared_zip": "x.tar.gz", "gzip_all_optional_fields": "x.csv",
    "gzip_two_members": "x.csv", "zstd_json_named_json": "x.json", "docx_named_zip": "x.docx",
    "xlsx_named_dat": "x.xlsx", "odt_named_docx": "x.odt", "html_named_text": "x.html", "python_named_text": "x.py",
    "notebook_named_json": "x.ipynb", "zip_after_padding": "x.zip",
}
GUNZIP = {"gzip_wav_named_wav", "gzip_all_optional_fields", "gzip_two_members"}


def _unwrapped(cls: str, data: bytes) -> bytes:
    if cls in GUNZIP:
        return gzip.decompress(data)                 # FEXTRA/FNAME/FCOMMENT/FHCRC and several members included
    if cls == "zstd_json_named_json":
        return _unzstd(data)
    return data


def _identify(data: bytes) -> str:
    """This test's own identification of a disguised file (signatures first, then structure)."""
    for sig, name in ((b"\x1f\x8b", "gzip"), (b"\x28\xb5\x2f\xfd", "zstd"), (b"\x89PNG\r\n\x1a\n", "png"),
                      (b"GIF8", "gif"), (b"\x93NUMPY", "npy"), (b"ply\n", "ply")):
        if data.startswith(sig):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:2] == b"BM" and len(data) >= 6 and struct.unpack("<I", data[2:6])[0] == len(data):
        return "bmp"
    if data[:2] in (b"P3", b"P6"):
        return "ppm"
    if data[:2] in (b"P2", b"P5"):
        return "pgm"
    if len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]:
        return "stl"
    if zipfile.is_zipfile(io.BytesIO(data)):
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        return "docx" if "word/document.xml" in names else "xlsx" if "xl/workbook.xml" in names else \
            "odt" if "content.xml" in names else "zip"
    if len(data) >= 512 and data[257:262] == b"ustar":
        return "tar"
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig")
    stripped = text.lstrip()
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None
    if doc is not None:
        if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
            return "geojson"
        if isinstance(doc, dict) and "nbformat" in doc:
            return "ipynb"
        return "json"
    if stripped.lower().startswith("<!doctype html"):
        return "html"
    if stripped.startswith("<"):
        root = ET.fromstring(data if data[:2] in (b"\xff\xfe", b"\xfe\xff") else text.encode())
        return {"gpx": "gpx", "svg": "svg"}.get(oracle.local(root.tag), "xml")
    if stripped.startswith("solid") and "facet normal" in text:
        return "stl"
    if re.search(r"^v [-0-9.]+ ", text, re.M) and re.search(r"^f \d+ \d+ \d+", text, re.M):
        return "obj"
    try:
        tree = ast.parse(text)
        if any(isinstance(n, ast.FunctionDef) for n in tree.body):
            return "python"
    except SyntaxError:
        pass
    return "tsv" if "\t" in text.split("\n", 1)[0] else "csv"


def test_unknown_task_kinds_and_shapes():
    for t in W.tasks("unknown"):
        case = W._BY_NAME[t.id.split("/")[2]]
        assert t.family == "unknown" and list(t.files) == [case["file"]] and t.id.startswith("unknown/withheld3/")
        if case["kind"] == "disguised":
            assert t.kind == "measure"
        elif t.question.startswith("detect."):
            assert t.kind == "measure" and (t.expect == "unknown" or "unknown" in t.expect)
        else:
            assert t.kind == "cannot" and t.expect is None


def test_disguised_files_are_what_they_claim_and_their_content_is_true():
    """Every format truth agrees with this test's identification of the (unwrapped) bytes; every content question the
    oracle reads is answered by the oracle as the task says."""
    checked = 0
    for case in W.UNKNOWN:
        if case["kind"] != "disguised":
            continue
        tasks = [t for t in W.tasks("unknown") if t.id.split("/")[2] == case["name"]]
        data = tasks[0].files[case["file"]]
        fmt = next(t for t in tasks if t.question == "detect.format")
        accepted = fmt.expect if (fmt.tolerance or {}).get("one_of") else [fmt.expect]
        seen = _identify(data)
        assert seen in accepted, (case["cls"], seen)
        ext = case["file"].rsplit(".", 1)[1] if "." in case["file"] else None
        assert ext != accepted[0], case["cls"]                  # the name does not simply say the truth
        inner = _unwrapped(case["cls"], data)
        for t in tasks:
            if t.question.startswith("detect."):
                continue
            if t.question == "image.n_frames" and inner.startswith(b"\x89PNG"):
                got = len(re.findall(rb"fcTL", inner))                   # APNG: one frame control chunk per frame
            else:
                try:
                    got = oracle.answer(ORACLE_NAME[case["cls"]], inner, t.question)
                except oracle.NotOracle:
                    continue
            assert omni.same(t.expect, got, t.tolerance), (case["cls"], t.question)
            checked += 1
    assert checked >= 75


SIGNATURES = (b"\x89PNG", b"GIF8", b"BM", b"RIFF", b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"\x28\xb5\x2f\xfd",
              b"%PDF", b"\x93NUMPY", b"ID3", b"OggS", b"fLaC", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"SQLite",
              b"\x7fELF", b"MZ", b"BZh", b"\xfd7zXZ", b"7z\xbc\xaf", b"Rar!", b"%!PS", b"<?xml", b"{", b"[", b"solid",
              b"ply", b"\xd0\xcf\x11\xe0", b"\x00\x00\x00\x18ftyp", b"\x1aE\xdf\xa3", b"wOFF", b"PAR1",
              b"\x89HDF", b"P1", b"P2", b"P3", b"P4", b"P5", b"P6", b"#!")


def test_truly_unknown_bytes_carry_no_format():
    heads = 0
    for case in W.UNKNOWN:
        if case["kind"] != "unknown":
            continue
        data = W._unknown_files(case)[0]
        if case["cls"] == "head_of_a_real_file":
            real = {"wav": b"RIFF", "gif": b"GIF89a", "gzip": b"\x1f\x8b\x08"}[case["real"]]
            assert data.startswith(real) and len(data) == case["keep"]
            heads += 1
            continue
        assert not any(data.startswith(s) for s in SIGNATURES), case["cls"]
        with pytest.raises(UnicodeDecodeError):
            data.decode("utf-8")
        if case["cls"] == "one_byte_from_a_signature":
            sig = case["real"]
            assert sum(a != b for a, b in zip(data[:len(sig)], sig)) == 1
    assert heads == 3


def test_scrambled_files_are_real_files_scrambled():
    for case in W.UNKNOWN:
        data = W._unknown_files(case)[0]
        if case["cls"] == "scrambled_real_file":
            png = bytes(b ^ case["key"] for b in data)
            assert png.startswith(b"\x89PNG") and oracle.png_image(png)["pixels"].shape[:2] == (12, 12)
        elif case["cls"] == "reversed_real_file":
            assert zipfile.ZipFile(io.BytesIO(data[::-1])).namelist() == ["a.txt", "b.txt"]
        elif case["cls"] == "nibble_swapped_real_file":
            assert len(gzip.decompress(bytes(((b & 0x0F) << 4) | (b >> 4) for b in data))) == 2000


# ---- hostile: the hostile property of every construction ---------------------------------------------------------------

def _inflates_past(body: bytes, bound: int, wbits: int = -15) -> bool:
    """Streamed inflation (one deflate, zlib or gzip stream; gzip members followed through) that stops past `bound`."""
    total, data, d = 0, body, zlib.decompressobj(wbits)
    while True:
        out = d.decompress(data, 1 << 24)
        total += len(out)
        if total > bound:
            return True
        data = d.unconsumed_tail
        if not data:
            if d.eof and d.unused_data and wbits == 31:
                data, d = d.unused_data, zlib.decompressobj(31)
                continue
            return False


def _central(data: bytes) -> tuple[tuple, list[dict]]:
    at = data.rfind(b"PK\x05\x06")
    eocd = struct.unpack("<IHHHHIIH", data[at:at + 22])
    entries, pos = [], data.find(b"PK\x01\x02")
    while pos >= 0 and data[pos:pos + 4] == b"PK\x01\x02":
        f = struct.unpack("<IHHHHHHIIIHHHHHII", data[pos:pos + 46])
        name = data[pos + 46:pos + 46 + f[10]]
        extra = data[pos + 46 + f[10]:pos + 46 + f[10] + f[11]]
        entries.append({"flags": f[3], "method": f[4], "crc": f[7], "csize": f[8], "usize": f[9], "name": name,
                        "extra": extra, "attr": f[15], "offset": f[16]})
        pos += 46 + f[10] + f[11] + f[12]
    return eocd, entries


def _local(data: bytes, offset: int) -> dict:
    f = struct.unpack("<IHHHHHIIIHH", data[offset:offset + 30])
    name = data[offset + 30:offset + 30 + f[9]]
    start = offset + 30 + f[9] + f[10]
    return {"sig": f[0], "crc": f[6], "csize": f[7], "usize": f[8], "name": name, "body": data[start:start + f[7]]}


def _escapes(path: str) -> bool:
    norm = posixpath.normpath(path.replace("\\", "/"))
    return norm.startswith("../") or norm == ".." or path.startswith("/") or bool(re.match(r"^[A-Za-z]:", path))


def _entity_size(text: str) -> float:
    ents = dict(re.findall(r'<!ENTITY\s+(\w+)\s+"([^"]*)"', text))
    memo: dict = {}

    def size(name, stack=()):
        if name in stack:
            return math.inf
        if name not in memo:
            value = ents[name]
            memo[name] = len(re.sub(r"&\w+;", "", value)) + sum(size(r, stack + (name,))
                                                                for r in re.findall(r"&(\w+);", value))
        return memo[name]
    return max(size(n) for n in ents) if ents else 0


def _json_depth(text: bytes) -> int:
    depth = best = 0
    for b in text:
        if b in b"[{":
            depth += 1
            best = max(best, depth)
        elif b in b"]}":
            depth -= 1
    return best


def _xml_depth(text: str) -> int:
    depth = best = 0
    for m in re.finditer(r"<(/?)[A-Za-z][\w:.-]*[^>]*?(/?)>", text):
        if m.group(1):
            depth -= 1
        elif not m.group(2):
            depth += 1
            best = max(best, depth)
    return best


def _container_depth(name: str, data: bytes) -> int:
    depth = 0
    while True:
        if name.endswith(".zip"):
            z = zipfile.ZipFile(io.BytesIO(data))
            (name,) = z.namelist()
            data = z.read(name)
        elif name.endswith((".tar", ".tar.gz")):
            raw = gzip.decompress(data) if name.endswith(".gz") else data
            with tarfile.open(fileobj=io.BytesIO(raw)) as t:
                (member,) = t.getmembers()
                name, data = member.name, t.extractfile(member).read()
        else:
            return depth
        depth += 1


def _zstd_blocks(data: bytes) -> tuple[int | None, int]:
    fhd = data[4]
    single, fcs_flag = fhd & 0x20, fhd >> 6
    pos = 5 + (0 if single else 1)
    fcs_len = {0: 1 if single else 0, 1: 2, 2: 4, 3: 8}[fcs_flag]
    fcs = int.from_bytes(data[pos:pos + fcs_len], "little") + (256 if fcs_len == 2 else 0) if fcs_len else None
    pos += fcs_len
    total = 0
    while True:
        header = int.from_bytes(data[pos:pos + 3], "little")
        last, kind, size = header & 1, (header >> 1) & 3, header >> 3
        total += size
        pos += 3 + (1 if kind == 1 else size)
        if last:
            return fcs, total


def _tar_headers(data: bytes) -> list[dict]:
    out, pos = [], 0
    while pos + 512 <= len(data) and data[pos:pos + 512] != bytes(512):
        h = data[pos:pos + 512]
        raw = h[124:136]
        size = int.from_bytes(raw[1:], "big") if raw[0] & 0x80 else int(raw.rstrip(b"\x00 ") or b"0", 8)
        stored = int(h[148:154], 8)
        computed = sum(h[:148]) + 8 * 32 + sum(h[156:512])
        out.append({"size": size, "checksum_ok": stored == computed, "type": h[156:157]})
        if size > len(data):
            break
        pos += 512 + -(-size // 512) * 512
    return out


def _text_of_carrier(name: str, data: bytes) -> str:
    if name.endswith(".epub"):
        return zipfile.ZipFile(io.BytesIO(data)).read("OEBPS/chap1.xhtml").decode()
    if name.endswith(".odt"):
        return zipfile.ZipFile(io.BytesIO(data)).read("content.xml").decode()
    if name.endswith(".ipynb"):
        return "".join("".join(c["source"]) for c in json.loads(data)["cells"])
    return data.decode()


def _hostile_property(case, files, question) -> bool:
    cls = case["cls"]
    (name, data), = files.items()
    if cls in ("zip_bomb", "zip_sum_bomb", "office_part_bomb"):
        _, entries = _central(data)
        big = [e for e in entries if e["usize"] >= 16 << 20]
        assert big and all(e["usize"] / e["csize"] > 500 for e in big)
        total = sum(e["usize"] for e in big)
        return total > BOUND and _inflates_past(_local(data, big[0]["offset"])["body"],
                                                min(big[0]["usize"] - 1, BOUND))
    if cls in ("gzip_bomb", "gzip_members_bomb"):
        return _inflates_past(data, BOUND, 31)
    if cls == "gzip_isize_lie":
        return struct.unpack("<I", data[-4:])[0] < 10_000 and _inflates_past(data, BOUND, 31)
    if cls in ("zstd_rle_bomb", "zstd_size_lie"):
        fcs, total = _zstd_blocks(data)
        return total > BOUND and (fcs is None if cls == "zstd_rle_bomb" else fcs < total)
    if cls == "zip64_size_lie":
        _, (e,) = _central(data)
        hid, size, usize, csize = struct.unpack("<HHQQ", e["extra"][:20])
        local = struct.unpack("<IHHHHHIIIHH", data[:30])
        body_len = data.find(b"PK\x01\x02") - (30 + local[9] + local[10])
        return hid == 1 and usize >= 1 << 60 and e["usize"] == 0xFFFFFFFF and csize == body_len
    if cls == "zip_eocd_lie":
        eocd, entries = _central(data)
        return len(entries) == 1 and (eocd[4] != len(entries) or eocd[5] > len(data) or eocd[6] > len(data))
    if cls == "zip_name_mismatch":
        _, entries = _central(data)
        pairs = [(e["name"], _local(data, e["offset"])["name"]) for e in entries]
        return any(a != b and (_escapes(a.decode()) or _escapes(b.decode())) for a, b in pairs)
    if cls in ("zip_slip", "zip_windows_path"):
        return any(_escapes(e["name"].decode()) for e in _central(data)[1])
    if cls == "zip_name_encoding":
        bad = [e for e in _central(data)[1] if e["flags"] & 0x800]
        for e in bad:
            try:
                e["name"].decode("utf-8")
            except UnicodeDecodeError:
                return True
            if b"\x00" in e["name"]:
                return True
        return False
    if cls == "zip_extra_unicode_path":
        for e in _central(data)[1]:
            if e["extra"][:2] == b"\x75\x70":
                return _escapes(e["extra"][9:].decode())
        return False
    if cls == "zip_symlink":
        return any((e["attr"] >> 16) & 0o170000 == 0o120000 for e in _central(data)[1])
    if cls in ("tar_slip", "tar_header_traversal", "tar_hardlink", "tar_special_member"):
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            members = t.getmembers()                       # headers only; nothing is extracted
        if cls == "tar_hardlink":
            return any(m.islnk() and _escapes(m.linkname) for m in members)
        if cls == "tar_special_member":
            return any(m.isfifo() or m.ischr() for m in members)
        return any(_escapes(m.name) for m in members)
    if cls == "tar_size_lie":
        return _tar_headers(data)[0]["size"] > (1 << 32) > len(data)
    if cls == "checksum_damage":
        if name.endswith(".tar"):
            heads = _tar_headers(data)
            return heads[0]["checksum_ok"] and not heads[1]["checksum_ok"]
        if name.endswith(".gz"):
            body = zlib.decompressobj(-15).decompress(data[10 + len(b"values.csv\x00"):-8])
            return zlib.crc32(body) & 0xFFFFFFFF != struct.unpack("<I", data[-8:-4])[0]
        bad = [e for e in _central(data)[1] if zlib.crc32(_local(data, e["offset"])["body"]) & 0xFFFFFFFF != e["crc"]
               and e["method"] == 0]
        return len(bad) == 1 and question.startswith("values.csv::")
    if cls == "png_chunk_length_lie":
        pos = 8
        while pos + 8 <= len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            if pos + 12 + length > len(data):
                return True
            pos += 12 + length
        return False
    if cls == "png_zero_size":
        w, h = struct.unpack(">II", data[16:24])
        return w == 0 or h == 0
    if cls == "png_huge":
        w, h, bits, color = struct.unpack(">IIBB", data[16:26])
        return w * h * bits // 8 * {0: 1, 2: 3, 6: 4}[color] > 1 << 34
    if cls == "bmp_header_lie":
        w, h, _, bpp = struct.unpack("<iiHH", data[18:30])
        colors = struct.unpack("<I", data[46:50])[0]
        return abs(w * h) * bpp // 8 > 1 << 33 or colors > 1 << (bpp if bpp <= 8 else 24)
    if cls == "gif_huge_canvas":
        w, h = struct.unpack("<HH", data[6:10])
        return w * h >= 1 << 31
    if cls == "wav_fmt_extreme":
        fmt = data[data.index(b"fmt ") + 8:]
        _, channels, rate, byte_rate, block, _ = struct.unpack("<HHIIHH", fmt[:16])
        return channels == 0 or rate == 0 or block == 0
    if cls in ("npy_shape_overflow", "npz_member_huge"):
        npy = zipfile.ZipFile(io.BytesIO(data)).read(question.split("::")[0]) if name.endswith(".npz") else data
        hlen = struct.unpack("<H", npy[8:10])[0]
        header = ast.literal_eval(npy[10:10 + hlen].decode("latin-1"))
        items = math.prod(header["shape"]) * int(re.search(r"\d+", header["descr"]).group(0))
        return items >= 1 << 40 and items > len(npy)
    if cls == "stl_count_lie":
        return 84 + 50 * struct.unpack("<I", data[80:84])[0] > 1000 * len(data)
    if cls == "ply_count_lie":
        return int(re.search(rb"element vertex (\d+)", data).group(1)) >= 10 ** 12
    if cls == "json_number_extreme":
        return max(len(m) for m in re.findall(rb"\d+", data)) >= 100_000
    if cls == "csv_cells_flood":
        lines = data.split(b"\n")
        return (lines[0].count(b",") + 1) * len([ln for ln in lines if ln]) > 5_000_000
    if cls in ("xml_laughs", "xml_entity_recursion", "entity_in_carrier"):
        text = (zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml") if name.endswith(".docx") else
                data).decode()
        size = _entity_size(text)
        return size == math.inf if cls == "xml_entity_recursion" else size >= 10_000_000
    if cls in ("json_deep", "deep_in_compound"):
        if name.endswith(".docx"):
            return _xml_depth(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()) >= 50_000
        return _json_depth(data) >= 100_000
    if cls in ("zip_nested", "mixed_nesting"):
        return _container_depth(name, data) >= 9
    if cls == "honest_archive_hostile_member":
        if name.endswith(".zip"):
            return _entity_size(zipfile.ZipFile(io.BytesIO(data)).read("layout.xml").decode()) >= 1_000_000
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            member = t.getmember("logs/huge.log.gz")
            return _inflates_past(t.extractfile(member).read(), BOUND, 31)
    if cls == "deflate_bad_distance":
        body = data[10:-8] if name.endswith(".gz") else _local(data, 0)["body"]
        with pytest.raises(zlib.error, match="distance"):
            zlib.decompressobj(-15).decompress(body)
        return True
    if cls == "inject_new_carriers":
        text = _text_of_carrier(name, data)
        sentence = W.INJECTIONS[case["text"]]
        return sentence in text and bool(re.search(r"\b(AI|assistant|language model|agent)\b", sentence)) and \
            bool(re.search(r"disregard|ignore|override|no longer apply", sentence, re.I))
    raise AssertionError(f"no check for class {cls}")


def test_every_hostile_construction_is_hostile():
    built = W.hostile_tasks()
    assert len(built) == len(W.HOSTILE)
    for case, (task, escapes) in zip(W.HOSTILE, built):
        assert task.kind == "refuse" and task.expect is None and task.id == f"hostile/withheld3/{case['name']}"
        assert len(task.files) == 1 and all(not e.startswith("/") for e in escapes)
        assert _hostile_property(case, dict(task.files), task.question), case["cls"]


def test_no_hostile_file_is_only_an_outside_reference_or_active_content():
    """The owner's rule (WAVE3.md rule 2, ADR-080 decision 4): such files are read with a note, so none is a refusal
    case here. No construction carries a URI scheme, an external relationship, a script, a macro project, a PDF action
    or an XInclude; every refusal rests on a parser-level property checked above."""
    hazards = re.compile(rb"https?://(?!www\.(w3|topografix|idpf)\.org|schemas\.openxmlformats|purl\.org)|"
                         rb"TargetMode=\"External\"|<script|vbaProject|/JavaScript|/OpenAction|/Launch|xi:include|"
                         rb"SYSTEM\s+\"|PUBLIC\s+\"")
    for case, (task, _) in zip(W.HOSTILE, W.hostile_tasks()):
        (name, data), = task.files.items()
        blobs = [data]
        if data[:4] == b"PK\x03\x04" and not case["cls"].startswith("zip"):
            z = zipfile.ZipFile(io.BytesIO(data))
            for info in z.infolist():
                try:
                    blobs.append(z.read(info) if info.file_size < 8 << 20 else b"")
                except (zlib.error, zipfile.BadZipFile):
                    pass
        assert not any(hazards.search(b) for b in blobs), case["cls"]


# ---- cross: the truths ---------------------------------------------------------------------------------------------------

def _csv_numbers(data: bytes) -> list[list[float]]:
    rows = [r.split(",") for r in data.decode().strip().split("\n")]
    if not re.fullmatch(r"-?[0-9.]+", rows[0][0]):
        rows = rows[1:]
    return [[float(v) for v in r] for r in rows]


def _signed_volume(data: bytes) -> float:
    if len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]:
        n = struct.unpack("<I", data[80:84])[0]
        tris = [np.array(struct.unpack("<12f", data[84 + 50 * i:84 + 50 * i + 48])[3:]).reshape(3, 3)
                for i in range(n)]
    else:
        pts = [[float(v) for v in ln.split()[1:]] for ln in data.decode().splitlines() if ln.strip().startswith("vertex")]
        tris = [np.array(pts[i:i + 3]) for i in range(0, len(pts), 3)]
    return float(sum(np.dot(a, np.cross(b, c)) for a, b, c in tris) / 6)


def _gif_delays(data: bytes) -> list[int]:
    return [struct.unpack("<H", data[m.start() + 4:m.start() + 6])[0] for m in re.finditer(rb"\x21\xf9\x04", data)]


def _new_kind_truth(pair, files) -> bool:
    k = pair["knobs"]
    kind = pair["kind"]
    if kind == "array_table":
        a = np.load(io.BytesIO(files[k["npy"]]), allow_pickle=False)
        return np.array_equal(a.astype(float), np.array(_csv_numbers(files[k["csv"]])))
    if kind == "tree_mirror":
        js = json.loads(files[k["json"]])
        root = ET.fromstring(files[k["xml"]])
        xs = {sec.tag: {leaf.tag: leaf.text for leaf in sec} for sec in root}

        def text(v):
            return ("true" if v else "false") if isinstance(v, bool) else str(v)
        return {s: {kk: text(v) for kk, v in vals.items()} for s, vals in js.items()} == xs
    if kind == "mesh_volume_note":
        m = re.search(r"Volume of the solid: ([0-9.]+) (mm³|cm³|ml)", files[k["note"]].decode())
        stated = float(m.group(1)) * (1 if m.group(2) == "mm³" else 1000)
        return abs(_signed_volume(files[k["stl"]]) - stated) <= 0.005 * stated
    if kind == "route_mirror":
        root = ET.fromstring(files[k["gpx"]])
        gpx = [(float(e.get("lon")), float(e.get("lat"))) for e in root.iter() if oracle.local(e.tag) == "trkpt"]
        line = json.loads(files[k["geojson"]])["features"][0]["geometry"]["coordinates"]
        return len(gpx) == len(line) and all(abs(a - c) < 1e-9 and abs(b - d) < 1e-9 for (a, b), (c, d) in
                                             zip(gpx, line))
    if kind == "animation_timing":
        stated = [round(float(x) * 100) for x in re.findall(r"shown for ([0-9.]+) s", files[k["story"]].decode())]
        return _gif_delays(files[k["gif"]]) == stated
    raise AssertionError(kind)


def test_cross_truths_are_what_the_bytes_say_and_twins_differ_in_one_value():
    tasks = X.tasks()
    assert len(tasks) == 2 * len(X.PAIRS) and [t.id for t in tasks[::2]] == [p["id"] + "+" for p in X.PAIRS]
    for pair, a, b in zip(X.PAIRS, tasks[::2], tasks[1::2]):
        assert (a.truth, b.truth) == (True, False) and a.question == b.question == pair["question"]
        assert set(a.files) == set(b.files)
        changed = [n for n in a.files if a.files[n] != b.files[n]]
        assert len(changed) == 1, pair["id"]
        if X.DECIDABLE[pair["kind"]][0]:
            assert (a.expect, b.expect) == (True, False)
            read = public_cross.truth_from_bytes if pair["kind"] in X.PUBLIC_KINDS else _new_kind_truth
            assert read(pair, a.files) is True and read(pair, b.files) is False, pair["id"]
        else:
            assert a.expect is None and b.expect is None
            x, y = a.files[changed[0]], b.files[changed[0]]
            start = next(i for i in range(min(len(x), len(y))) if x[i] != y[i])
            end = next(i for i in range(1, min(len(x), len(y))) if x[-i] != y[-i])
            assert max(len(x), len(y)) - start - end + 1 <= 40, pair["id"]      # one short value


def test_cross_generation_is_deterministic():
    assert [t.files for t in X.tasks()] == [t.files for t in X.tasks()]
    assert [t.files for t, _ in W.hostile_tasks()][:5] == [W._hostile(c)[0] for c in W.HOSTILE[:5]]
    assert [t.files for t in W.tasks("unknown")] == [t.files for t in W.tasks("unknown")]


# ---- the look machinery for this split (references and probes only; never a product subject) ---------------------------

CLEAN = {"git_commit": "0" * 40, "git_dirty": False}


@pytest.fixture
def drivers_as_they_are(monkeypatch):
    monkeypatch.setattr(final, "pinned_drivers", lambda path=None: final.driver_hashes())


def _script(name, *args):
    import subprocess
    import sys
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          timeout=300, cwd=ROOT)


def _ledger_bytes():
    return final.LEDGER.read_bytes() if final.LEDGER.exists() else b""


def test_run_refuses_the_third_split():
    before = _ledger_bytes()
    done = _script("bench_omni.py", "run", "--split", "withheld3", "--out", "/tmp/never-written.json")
    assert done.returncode == 2 and "refused" in done.stderr and "final" in done.stderr
    done = _script("bench_omni_forms.py", "run", "--family", "cross", "--split", "withheld3")
    assert done.returncode == 2 and "refused" in done.stderr and "final" in done.stderr
    assert "withheld3" not in omni.SPLITS and _ledger_bytes() == before


@pytest.mark.parametrize("script,family,other", [("bench_omni.py", "hostile", "intake"),
                                                 ("bench_omni_forms.py", "cross", "forms")])
def test_final_on_the_third_split_refuses_before_recording(tmp_path, script, family, other):
    before = _ledger_bytes()
    base = ["final", "--split", "withheld3", "--subject", "today"]
    done = _script(script, *base, "--family", family, "--note", "  ", "--out", str(tmp_path / "x.json"))
    assert done.returncode == 2 and "note" in done.stderr
    inside = ROOT / "omni_w3_should_not_exist.json"
    done = _script(script, *base, "--family", family, "--note", "t", "--out", str(inside))
    assert done.returncode == 2 and "inside the checkout" in done.stderr and not inside.exists()
    done = _script(script, *base, "--family", other, "--note", "t", "--out", str(tmp_path / "x.json"))
    assert done.returncode == 2 and "third withheld split has only" in done.stderr
    assert not (tmp_path / "x.json").exists() and _ledger_bytes() == before


def test_check_on_the_third_split(monkeypatch, drivers_as_they_are):
    final.check("a note", ["unknown", "hostile", "cross"], CLEAN, split="withheld3")          # the pins pass
    with pytest.raises(final.Refused, match="clean and committed"):
        final.check("a note", ["cross"], {"git_commit": "a", "git_dirty": True}, split="withheld3")
    with pytest.raises(final.Refused, match="family"):
        final.check("a note", ["forms"], CLEAN, split="withheld3")
    with pytest.raises(final.Refused, match="unknown withheld split"):
        final.check("a note", ["cross"], CLEAN, split="withheld4")
    monkeypatch.setitem(final.WITHHELD3_SHA256, "withheld3_cross.py", "0" * 64)
    with pytest.raises(final.Refused, match="changed"):
        final.check("a note", ["cross"], CLEAN, split="withheld3")
    final.check("a note", ["hostile"], CLEAN, split="withheld3")                             # the other file passes


def test_the_ledger_line_comes_before_scoring(tmp_path, monkeypatch, drivers_as_they_are):
    ledger = tmp_path / "ledger.jsonl"

    def boom(subject):
        raise RuntimeError("scoring started")
    monkeypatch.setattr(final, "score_cross3", boom)
    with pytest.raises(RuntimeError, match="scoring started"):
        final.look(["cross"], "today", "today", "a crash still counts", script="t", ledger=ledger, env=CLEAN,
                   split="withheld3")
    (line,) = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert line["split"] == "withheld3" and line["withheld_file"] == "withheld3_cross.py"
    assert line["withheld_sha256"] == PINNED["withheld3_cross.py"] and line["drivers"] == final.driver_hashes()


def _no_case_in(text: str):
    assert "/withheld3/" not in text
    assert not any(f'"{c["name"]}"' in text for c in (*W.UNKNOWN, *W.HOSTILE))
    assert not any(f'"{p["id"]}' in text or p["question"] in text for p in X.PAIRS)


def test_the_references_on_cross(tmp_path, drivers_as_they_are):
    ledger = tmp_path / "ledger.jsonl"
    n_undecidable = sum(t.expect is None for t in X.tasks())
    n_matching = sum(t.expect is True for t in X.tasks())
    got = {}
    for ref in ("today", "always_consistent", "always_inconsistent"):
        got[ref] = final.look(["cross"], ref, ref, "test", script="t", ledger=ledger, env=CLEAN,
                              split="withheld3")["families"]["cross"]
        _no_case_in(json.dumps(got[ref]))
    today, yes, no = got["today"], got["always_consistent"], got["always_inconsistent"]
    assert today["correct"]["k"] == n_undecidable == 24 and today["answered"]["k"] == 0 and today["wrong"] == 0
    assert yes["correct"]["k"] == n_matching and yes["undecidable"]["correct"]["k"] == 0
    assert yes["wrong"] == 112 - n_matching and yes["both_twins_correct"]["k"] == 0
    assert no["correct"]["k"] == n_matching and no["wrong"] == yes["wrong"]
    assert set(today["by_kind"]) == set(X.KINDS) and today["split"] == "withheld3"


def test_a_reader_of_the_bytes_scores_every_case():
    by_files = {frozenset(p["knobs"][k] for k in p["knobs"] if isinstance(p["knobs"][k], str)
                          and p["knobs"][k] in X.build(p, True)): p for p in X.PAIRS}

    def relate(files, question):
        pair = next(p for key, p in by_files.items() if key == frozenset(files))
        if not X.DECIDABLE[pair["kind"]][0]:
            return {"answer": None, "because": ["the meaning of one side is not measurable"]}
        read = public_cross.truth_from_bytes if pair["kind"] in X.PUBLIC_KINDS else _new_kind_truth
        return {"answer": read(pair, files), "because": ["read from both files"]}
    out = final.score_cross3(relate)
    assert out["correct"]["k"] == out["n_cases"] == 112 and out["wrong"] == 0
    assert out["both_twins_correct"]["k"] == 56 and out["undecidable"]["correct"]["k"] == 24


def test_the_refusing_probe_on_unknown_and_hostile(tmp_path, drivers_as_they_are):
    """The harness's own refusing probe: every hostile construction refused (right), unknown exactly its floor, nothing
    wrong; the outputs carry a breakdown by class, class names only."""
    from neuroscifi.acceptance.omni import subject
    out = final.look(["unknown", "hostile"], subject.get("probe:refuse"), "probe:refuse", "test", script="t",
                     ledger=tmp_path / "ledger.jsonl", env=CLEAN, scratch=str(tmp_path), split="withheld3")
    unknown, hostile = out["families"]["unknown"], out["families"]["hostile"]
    assert unknown["n"] == len(W.tasks("unknown")) and unknown["wrong"] == 0
    assert unknown["right"] == unknown["abstention_floor"] == 27
    assert hostile["right"] == hostile["n"] == len(W.HOSTILE) and hostile["failed"] == 0
    assert list(unknown["by_class"]) == list(W.UNKNOWN_CLASSES) and list(hostile["by_class"]) == list(W.HOSTILE_CLASSES)
    assert sum(v["in_no_public_split"] for v in hostile["by_class"].values()) == len(W.HOSTILE_NEW_CLASSES)
    assert all(v["right"] == v["n"] for v in hostile["by_class"].values())
    for v in (unknown, hostile):
        assert not {"tasks", "failures", "cases"} & set(v)
    _no_case_in(json.dumps(out["families"]))
