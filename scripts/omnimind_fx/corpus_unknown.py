"""Lane FX's own disguises and near-misses for identification (item 1). Each item: (class, Task).
Truth by construction. Seeds 40000+ (outside every reserved range). Built with the benchmark's encoders and the
standard library only."""
from __future__ import annotations

import bz2
import gzip
import json
import lzma
import struct

import numpy as np

from neuroscifi.acceptance.omni import Task, enc
from neuroscifi.acceptance.omni.intake import csv_bytes, pixels, table_values

ONE_OF = {"one_of": True}
REL = {"rel": 1e-9}
EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg", ".mp4": "video/mp4",
        ".pdf": "application/pdf", ".zip": "application/zip", ".gz": "application/gzip",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".csv": "text/csv",
        ".json": "application/json", ".xml": "application/xml", ".txt": "text/plain", ".stl": "model/stl",
        ".obj": "model/obj", ".ply": "model/x-ply", ".npy": "application/x-npy", ".sqlite": "application/vnd.sqlite3",
        ".tar": "application/x-tar", ".7z": "application/x-7z-compressed", ".html": "text/html",
        ".md": "text/markdown", ".py": "text/x-python", ".geojson": "application/geo+json", ".svg": "image/svg+xml",
        ".mid": "audio/midi", ".ico": "image/vnd.microsoft.icon", ".psd": "image/vnd.adobe.photoshop",
        ".heic": "image/heic", ".webp": "image/webp", ".fits": "application/fits", ".dcm": "application/dicom",
        ".shp": "application/x-esri-shape", ".glb": "model/gltf-binary", ".ini": "text/x-ini",
        ".toml": "application/toml", ".yaml": "application/yaml", ".tsv": "text/tab-separated-values",
        ".ipynb": "application/x-ipynb+json", ".kml": "application/vnd.google-earth.kml+xml",
        ".gpx": "application/gpx+xml", ".ndjson": "application/x-ndjson", ".js": "text/javascript", ".c": "text/x-c",
        ".sh": "text/x-shellscript", ".sql": "application/sql", ".css": "text/css", ".rtf": "application/rtf",
        ".odt": "application/vnd.oasis.opendocument.text", ".epub": "application/epub+zip",
        ".npz": "application/x-npz", ".xz": "application/x-xz", ".bz2": "application/x-bzip2",
        ".zst": "application/zstd", ".aiff": "audio/aiff", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
        ".pgm": "image/x-portable-graymap", ".ppm": "image/x-portable-pixmap", ".off": "", ".wkt": "",
        ".parquet": "application/vnd.apache.parquet", ".h5": "application/x-hdf5", ".nii": "application/x-nifti"}

MAGICS = {"png": b"\x89PNG\r\n\x1a\n", "pdf": b"%PDF-1.4\n", "zip": b"PK\x03\x04\x14\x00\x00\x00\x08\x00",
          "gif": b"GIF89a", "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "wav": b"RIFF\x24\x08\x00\x00WAVEfmt ",
          "mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00", "ogg": b"OggS\x00\x02" + bytes(8), "flac": b"fLaC",
          "bzip2": b"BZh91AY&SY", "gzip": b"\x1f\x8b\x08\x00", "7z": b"7z\xbc\xaf\x27\x1c\x00\x04",
          "rar": b"Rar!\x1a\x07\x00", "sqlite": b"SQLite format 3\x00", "hdf5": b"\x89HDF\r\n\x1a\n",
          "npy": b"\x93NUMPY\x01\x00", "elf": b"\x7fELF\x02\x01\x01\x00", "midi": b"MThd\x00\x00\x00\x06",
          "glb": b"glTF\x02\x00\x00\x00", "tiff": b"II*\x00\x08\x00\x00\x00", "zstd": b"\x28\xb5\x2f\xfd",
          "xz": b"\xfd7zXZ\x00", "psd": b"8BPS\x00\x01", "parquet": b"PAR1", "macho": b"\xcf\xfa\xed\xfe",
          "class": b"\xca\xfe\xba\xbe\x00\x00\x00\x34", "wasm": b"\x00asm\x01\x00\x00\x00",
          "ico": b"\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00\x01\x00\x20\x00", "bmp": b"BM\x36\x00\x00\x00\x00\x00\x00\x00\x36\x00\x00\x00\x28\x00",
          "pgm": b"P5\n4 4\n255\n", "ply": b"ply\nformat ascii 1.0\n", "fits": b"SIMPLE  =                    T",
          "flac2": b"fLaC\x00\x00\x00\x22", "tar": b"", "mkv": b"\x1a\x45\xdf\xa3", "dicom": b""}
UNKNOWN_CONTENT = ("table.n_rows", "image.size", "audio.duration_s", "text.chars", "tree.keys:", "mesh.n_faces",
                   "bundle.n_members", "array.shape", "doc.headings")

_ID = [0]


def _task(cls, name, data, declared, question, expect, tol=None, kind="measure"):
    _ID[0] += 1
    return cls, Task(f"fx/{cls}/{_ID[0]}/{question}", "unknown", {name: data}, {name: declared} if declared else {},
                     question, expect, tol, kind)


def unknown_items(cls, name, data, declared=None, accept=("unknown",), content=UNKNOWN_CONTENT[:4]):
    out = [_task(cls, name, data, declared, "detect.format", list(accept), ONE_OF)]
    out += [_task(cls, name, data, declared, c, None, None, "cannot") for c in content]
    return out


def rng(seed):
    return np.random.default_rng(seed)


def csv_file(r, rows=6):
    t = table_values(r, {"columns": ("unit", "reading", "count", "label"), "rows": rows})
    return t, csv_bytes(t)


def build():
    items = []
    seed = 40000
    # A. random bytes, zeros and invented records under misleading names and declared types
    for ext, media in EXTS.items():
        seed += 1
        r = rng(seed)
        noise = r.integers(0, 256, 700).astype(np.uint8).tobytes()
        items += unknown_items("A_random_named", "file" + ext, noise)
        if media:
            items += unknown_items("A_random_declared", "blob", noise, media)
        items += unknown_items("A_zeros_named", "file" + ext, bytes(512 + seed % 7))
        recs = b"QXR1" + b"".join(struct.pack("<IfI", i, float(r.normal()), int(r.integers(8))) for i in range(40))
        items += unknown_items("A_records_named", "readings" + ext, recs, media or None)
    # B. buried signatures in random bytes
    for fmt, magic in MAGICS.items():
        if not magic:
            continue
        for off in (1, 7, 100, 513, 1500):
            seed += 1
            noise = bytearray(rng(seed).integers(0, 256, 3000).astype(np.uint8).tobytes())
            noise[off:off + len(magic)] = magic
            items += unknown_items("B_buried", "blob.bin", bytes(noise))
    # C. one-byte-off signatures at offset 0, then random
    for fmt, magic in MAGICS.items():
        if len(magic) < 4:
            continue
        core = {"jpeg": 3, "bmp": 2, "pgm": 1, "ply": 3, "mp3": 3, "gzip": 3, "bzip2": 3, "wav": 4, "ogg": 4,
                "elf": 4, "class": 4, "ico": 4, "tiff": 4, "pdf": 5, "zip": 4, "flac": 4, "flac2": 4, "midi": 4,
                "npy": 6, "glb": 4, "psd": 4, "fits": 6}.get(fmt, 6)
        for k in range(min(len(magic), core)):
            seed += 1
            bad = bytearray(magic)
            bad[k] ^= 0x01 if k % 2 else 0x20
            noise = bytes(bad) + rng(seed).integers(0, 256, 1200).astype(np.uint8).tobytes()
            items += unknown_items("C_one_off", "blob.bin", noise)
    # D. exact signature then random: the signature is identifiable, content is not there
    for fmt, magic in MAGICS.items():
        if not magic:
            continue
        for size in (40, 2000):
            seed += 1
            noise = magic + rng(seed).integers(0, 256, size).astype(np.uint8).tobytes()
            accept = ("unknown", "flac" if fmt == "flac2" else fmt, *({"pgm": ("pnm",), "zip": ("zip",), "mp3": ("mp3",)}.get(fmt, ())))
            items += unknown_items("D_magic_then_random", "blob.bin", noise, accept=accept)
    # E. text that looks like a header of a binary format (prose after a magic word)
    texts = ["P5 is the fifth step of 12 in the plan\nand 3 more follow.\n", "ID3 tags were discussed at length today.\n",
             "BM 26 meeting notes: nothing decided.\n", "PK\x03\x04 is how a zip starts, said the note.\n",
             "solid ground, they said.\nfacet normal is a phrase from a file format.\n", "GIF89a is an old name.\n",
             "%PDF-1.7 was mentioned in the call, nothing else.\n", "MThd is a chunk name.\n",
             "RIFF and WAVE are words in a song.\n", "ply\nformat ascii 1.0 is a header line someone typed\n"]
    for i, t in enumerate(texts):
        data = (t * 3).encode()
        items += unknown_items("E_text_like_header", f"note{i}.txt", data,
                               accept=("text", "unknown") + (("csv",) if "," in t else ()),
                               content=("image.size", "audio.duration_s", "mesh.n_faces", "bundle.n_members"))
    # F. valid prefix followed by another format: the second is not answered as the first's content
    r = rng(seed + 1)
    arr = pixels(r, 6, 7, 1, 255)
    png = enc.png(arr, mode="gray")
    t, csvb = csv_file(r)
    wavb = enc.wav(np.sin(np.arange(800) / 5.0) * 0.4, 8000)
    for i, (a, b, fa, fb) in enumerate(((png, wavb, "png", "wav"), (wavb, png, "wav", "png"), (png, csvb, "png", "csv"),
                                        (MAGICS["gzip"], png, "gzip", "png"), (b"%PDF-1.4\n", png, "pdf", "png"))):
        items += unknown_items("F_prefix_then_other", f"joined{i}.bin", a + b, accept=("unknown", fa),
                               content=("table.n_rows",))
    # G. disguised decodable files: the right identification and a content value, or cannot
    seed += 10
    for i in range(6):
        r = rng(seed + i)
        t, data = csv_file(r, 5 + i)
        n, mean = len(t["cols"][0]), sum(t["cols"][2]) / len(t["cols"][2])
        col = t["names"][2]
        variants = {"csv_as_png": ("table.png", data, "image/png", "csv"),
                    "csv_as_json": ("table.json", data, "application/json", "csv"),
                    "csv_gz_as_zip": ("table.zip", gzip.compress(data, mtime=0), "application/zip", "gzip"),
                    "csv_bz2": ("table.dat", bz2.compress(data), None, "bzip2"),
                    "csv_xz_as_txt": ("table.txt", lzma.compress(data), "text/plain", "xz"),
                    "csv_gz_gz": ("table.bin", gzip.compress(gzip.compress(data, mtime=0), mtime=0), None, "gzip"),
                    "csv_zstd_gz": ("t", gzip.compress(enc.zstd_raw_frame(data), mtime=0), None, "gzip"),
                    "csv_xz_bz2": ("t.xz", bz2.compress(lzma.compress(data)), "application/x-xz", "bzip2"),
                    "csv_utf16be": ("t.csv", data.decode().encode("utf-16-be"), None, "csv"),
                    "csv_utf16le_bom_as_xml": ("t.xml", b"\xff\xfe" + data.decode().encode("utf-16-le"),
                                               "application/xml", "csv"),
                    "csv_bom_as_md": ("t.md", b"\xef\xbb\xbf" + data, "text/markdown", "csv")}
        for key, (name, b, decl, fmt) in variants.items():
            items.append(_task(f"G_disguised_{key}", name, b, decl, "detect.format", fmt))
            items.append(_task(f"G_disguised_{key}", name, b, decl, "table.n_rows", n))
            items.append(_task(f"G_disguised_{key}", name, b, decl, f"table.column_mean:{col}", mean, REL))
        arr = pixels(r, 5 + i, 4 + i, 3, 255)
        pngb = enc.png(arr, mode="rgb")
        for key, (name, b, decl, fmt) in {"png_as_csv": ("x.csv", pngb, "text/csv", "png"),
                                          "png_gz_as_png": ("x.png", gzip.compress(pngb, mtime=0), "image/png", "gzip"),
                                          "bmp_as_jpg": ("x.jpg", enc.bmp(arr), "image/jpeg", "bmp"),
                                          "png_in_zip_as_docx": ("x.docx", enc.zip_files({"img/x.png": pngb}),
                                                                 None, "zip")}.items():
            items.append(_task(f"G_disguised_{key}", name, b, decl, "detect.format", fmt))
            if fmt in ("png", "bmp"):
                items.append(_task(f"G_disguised_{key}", name, b, decl, "image.size", [4 + i, 5 + i]))
        wavb = enc.wav(np.sin(np.arange(1600 + 100 * i) / 4.0) * 0.5, 8000)
        items.append(_task("G_disguised_wav_as_mp3", "x.mp3", wavb, "audio/mpeg", "detect.format", "wav"))
        items.append(_task("G_disguised_wav_as_mp3", "x.mp3", wavb, "audio/mpeg", "audio.duration_s",
                           (1600 + 100 * i) / 8000, {"abs": 1e-6}))
        doc = {"k": [1, 2, 3], "name": f"n{i}"}
        jb = json.dumps(doc).encode()
        items.append(_task("G_disguised_json_as_xml", "x.xml", jb, "application/xml", "detect.format", "json"))
        items.append(_task("G_disguised_json_as_xml", "x.xml", jb, "application/xml", "tree.value:name", f"n{i}"))
        xb = f"<?xml version=\"1.0\"?><r><name>n{i}</name></r>".encode()
        items.append(_task("G_disguised_xml_as_json", "x.json", xb, "application/json", "detect.format", "xml"))
        zb = enc.zip_files({"a.txt": b"alpha\n" * (i + 1), "b.txt": b"beta\n"})
        items.append(_task("G_disguised_zip_as_docx", "x.docx", zb, enc_doc := EXTS[".docx"], "detect.format", "zip"))
        items.append(_task("G_disguised_zip_as_docx", "x.docx", zb, enc_doc, "bundle.n_members", 2))
        items.append(_task("G_disguised_zip_as_xlsx", "x.xlsx", zb, None, "detect.format", "zip"))
        verts = np.array([[0, 0, 0], [3, 0, 0], [0, 3, 0], [0, 0, 3]], float)
        stlb = enc.stl_binary(verts, [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
        items.append(_task("G_disguised_stl_as_obj", "m.obj", stlb, "model/obj", "detect.format", "stl"))
        items.append(_task("G_disguised_stl_as_obj", "m.obj", stlb, "model/obj", "mesh.n_faces", 4))
    return items


ITEMS = build()
