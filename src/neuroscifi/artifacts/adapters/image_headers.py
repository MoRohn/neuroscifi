"""Images whose pixels are mostly NOT decoded here, and the few that are.

Header only, then `HeaderOnly` (an `Unsupported`) with the facts and `needs` (NEVER a pixel value):
- JPEG: frame header (width, height, components, precision, baseline/progressive/lossless, arithmetic coding),
  JFIF/Exif/XMP/ICC/Adobe blocks present; Exif IFD0 ASCII fields and the XMP packet's text, and COM comments, are
  the artifact's own words; the presence of a GPS block is stated, its values are not read;
- WebP: lossy (VP8), lossless (VP8L) or extended (VP8X: canvas, alpha, animation frame count, ICC/Exif/XMP);
- HEIC/HEIF and AVIF: brands, image items, the spatial extent (ispe) and bit depth (pixi) of the primary item;
- PSD/PSB: channels, size, depth, colour mode, layer count;
- TIFF that is not baseline-decodable here (LZW, JPEG, tiles, planar, float, BigTIFF, ...): its IFD facts.

Decoded:
- TIFF baseline strips, uncompressed, PackBits or Deflate (with horizontal predictor), 1-16 bit unsigned grey
  (WhiteIsZero inverted to BlackIsZero), RGB(A), palette; the first page; ASCII tags as words;
- DICOM with an uncompressed transfer syntax (implicit or explicit VR little endian, explicit big endian):
  8/16-bit monochrome or RGB, the first frame; signed samples are offset by 2^(bits stored - 1) and said so;
  text elements (which may identify a person) are NOT returned;
- FITS primary arrays of BITPIX 8 and 16 (with BZERO 32768 as unsigned, else offset by 32768 and said so), 2-D
  (or 3-D with one plane); other BITPIX values are `HeaderOnly` with the header facts; header cards are words.
"""
from __future__ import annotations

import struct

import numpy as np

from .. import repr_b2 as R
from ..safe import Deadline
from ..safe_b2 import HeaderOnly, Reader, inflate, local, need, parse_xml, screen_words
from ..types import Decoded, Detected, Limits, Malformed, Raster
from . import Adapter
from .png import unpack_samples

SOF_NAMES = {0xC0: "baseline", 0xC1: "extended sequential", 0xC2: "progressive", 0xC3: "lossless",
             0xC5: "differential sequential", 0xC6: "differential progressive", 0xC7: "differential lossless",
             0xC9: "extended sequential, arithmetic", 0xCA: "progressive, arithmetic", 0xCB: "lossless, arithmetic",
             0xCD: "differential sequential, arithmetic", 0xCE: "differential progressive, arithmetic",
             0xCF: "differential lossless, arithmetic"}
TIFF_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 6: ("b", 1), 7: ("B", 1),
              8: ("h", 2), 9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8), 16: ("Q", 8)}
TIFF_TEXT = {270: "description", 271: "make", 272: "model", 305: "software", 306: "date", 315: "artist",
             33432: "copyright", 269: "document name", 285: "page name"}
TIFF_COMPRESSION = {1: "none", 2: "CCITT RLE", 3: "CCITT fax 3", 4: "CCITT fax 4", 5: "LZW", 6: "old JPEG",
                    7: "JPEG", 8: "Deflate", 32946: "Deflate", 32773: "PackBits", 34712: "JPEG 2000",
                    50000: "Zstandard", 34925: "LZMA", 50001: "WebP"}
PHOTOMETRIC = {0: "WhiteIsZero", 1: "BlackIsZero", 2: "RGB", 3: "palette", 4: "transparency mask", 5: "CMYK",
               6: "YCbCr", 8: "CIELab"}
PSD_MODES = {0: "bitmap", 1: "grayscale", 2: "indexed", 3: "RGB", 4: "CMYK", 7: "multichannel", 8: "duotone",
             9: "Lab"}
MAX_IFD_ENTRIES = 4096


def _header_only(fmt, facts, needs, data, words=()):
    text = "\n".join(w for w in words if w)
    shown = {k: v for k, v in facts.items() if k in ("width", "height", "components", "bits", "mode")}
    summary = ", ".join(f"{k} {v}" for k, v in shown.items())
    raise HeaderOnly(f"{fmt}" + (f" ({summary})" if summary else "") + ": pixels are not decoded here",
                     facts={"format": fmt, **facts}, needs=tuple(needs), size=len(data), words=text)


# --------------------------------------------------------------------------------------------------------------- TIFF


def read_ifds(data: bytes, base: int = 0, max_ifds: int = 64):
    """(endian, [ifd dict tag -> (type, count, values or raw offset)]) of a TIFF held at data[base:]."""
    order = data[base:base + 2]
    if order == b"II":
        e = "<"
    elif order == b"MM":
        e = ">"
    else:
        raise Malformed("the TIFF byte-order mark is missing")
    r = Reader(data, e, base + 2, "the TIFF header")
    magic = r.u16()
    if magic == 43:
        raise HeaderOnly("BigTIFF is not read here", facts={"format": "tiff", "variant": "BigTIFF"},
                         needs=("a BigTIFF reader",), size=len(data))
    if magic != 42:
        raise Malformed("the TIFF magic number is wrong")
    offset = r.u32()
    ifds, seen = [], set()
    while offset and len(ifds) < max_ifds:
        if offset in seen:
            break
        seen.add(offset)
        r.seek(base + offset)
        n = r.u16()
        if n > MAX_IFD_ENTRIES:
            raise Malformed("a TIFF directory has too many entries")
        ifd = {}
        for _ in range(n):
            tag, typ, count, raw = r.unpack("HHI4s")
            if typ not in TIFF_TYPES:
                continue
            code, size = TIFF_TYPES[typ]
            total = size * count
            if total > 4:
                vo = struct.unpack(e + "I", raw)[0]
                if base + vo + total > len(data) or total > 1 << 24:
                    continue
                blob = data[base + vo:base + vo + total]
            else:
                blob = raw[:total]
            if typ == 2:
                ifd[tag] = blob.split(b"\x00", 1)[0].decode("latin-1", "replace")
            else:
                ifd[tag] = struct.unpack(e + code * count, blob) if count <= 1 << 20 else ()
                if typ in (5, 10):
                    vals = ifd[tag]
                    ifd[tag] = tuple(vals[i] / vals[i + 1] if vals[i + 1] else 0.0 for i in range(0, len(vals), 2))
        ifds.append(ifd)
        offset = r.u32() if r.remaining >= 4 else 0
    return e, ifds


def _one(ifd, tag, default=None):
    v = ifd.get(tag, default)
    if isinstance(v, tuple):
        return v[0] if v else default
    return v


def _packbits(src: bytes, max_out: int) -> bytes:
    out = bytearray()
    i = 0
    while i < len(src) and len(out) < max_out:
        n = src[i]
        i += 1
        if n < 128:
            out += src[i:i + n + 1]
            i += n + 1
        elif n > 128:
            out += src[i:i + 1] * (257 - n)
            i += 1
    return bytes(out[:max_out])


def decode_tiff(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    e, ifds = read_ifds(data)
    if not ifds:
        raise Malformed("the TIFF file has no image directory")
    ifd = ifds[0]
    w, h = _one(ifd, 256), _one(ifd, 257)
    bits = ifd.get(258, (1,))
    spp = _one(ifd, 277, 1)
    comp = _one(ifd, 259, 1)
    photo = _one(ifd, 262)
    planar = _one(ifd, 284, 1)
    fmt_ = _one(ifd, 339, 1)
    predictor = _one(ifd, 317, 1)
    words = [f"{TIFF_TEXT[t]}: {ifd[t]}" for t in TIFF_TEXT if isinstance(ifd.get(t), str) and ifd[t].strip()]
    facts = {"width": w, "height": h, "bits": list(bits)[:8], "components": spp,
             "compression": TIFF_COMPRESSION.get(comp, f"code {comp}"), "mode": PHOTOMETRIC.get(photo, photo),
             "pages": len(ifds), "tiled": 322 in ifd}
    if 34853 in ifd:
        facts["gps_block_present"] = True
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
        raise Malformed("the TIFF directory has no valid size")
    missing = []
    if comp not in (1, 32773, 8, 32946):
        missing.append(f"a TIFF {TIFF_COMPRESSION.get(comp, f'compression {comp}')} decoder")
    if 322 in ifd:
        missing.append("a tiled TIFF reader")
    if planar != 1 and spp > 1:
        missing.append("a planar TIFF reader")
    if fmt_ != 1 or len(set(bits)) != 1 or bits[0] not in (1, 2, 4, 8, 16):
        missing.append("a reader for these TIFF sample formats")
    if photo not in (0, 1, 2, 3) or (photo == 2 and spp not in (3, 4)) or (photo in (0, 1) and spp not in (1, 2)):
        missing.append(f"a reader for TIFF {PHOTOMETRIC.get(photo, f'photometric {photo}')} images")
    if predictor not in (1, 2) or (predictor == 2 and bits[0] not in (8, 16)):
        missing.append("a TIFF predictor reader")
    if missing:
        _header_only("tiff", facts, missing, data, words)
    b, ch = bits[0], spp
    rowbytes = (w * b * ch + 7) // 8
    need(rowbytes * h * 2 + w * h * 4 * 2, limits, "the TIFF image")
    offsets, counts = ifd.get(273), ifd.get(279)
    if not offsets or not counts or len(offsets) != len(counts):
        raise Malformed("the TIFF strip offsets or byte counts are missing")
    rps = _one(ifd, 278, h) or h
    raw = bytearray()
    for i, (o, c) in enumerate(zip(offsets, counts)):
        deadline.check("reading TIFF strips")
        if o + c > len(data):
            raise Malformed("a TIFF strip lies outside the file")
        strip = data[o:o + c]
        rows = min(rps, h - i * rps)
        want = rows * rowbytes
        if comp == 32773:
            strip = _packbits(strip, want)
        elif comp in (8, 32946):
            strip = inflate(strip, want, what="a TIFF Deflate strip", deadline=deadline)
        raw += strip[:want]
    if len(raw) < rowbytes * h:
        raise Malformed("the TIFF strips hold fewer bytes than the image needs")
    rows = np.frombuffer(bytes(raw[:rowbytes * h]), np.uint8).reshape(h, rowbytes)
    if b == 16:
        samples = rows.view(e + "u2").reshape(h, w * ch).astype(np.uint16)
    else:
        samples = unpack_samples(rows, b, w * ch)
    if predictor == 2:
        s = samples.reshape(h, w, ch).astype(np.int64)
        samples = (np.cumsum(s, axis=1) % (1 << b)).astype(samples.dtype).reshape(h, w * ch)
    px = samples.reshape(h, w, ch)
    notes = [] if len(ifds) == 1 else [f"the first of {len(ifds)} pages was decoded"]
    if photo == 0:
        px = ((1 << b) - 1 - px).astype(px.dtype)
        notes.append("WhiteIsZero samples inverted so that 0 is black")
    if photo == 3:
        cmap = ifd.get(320)
        n = 1 << b
        if not cmap or len(cmap) != 3 * n:
            raise Malformed("the TIFF colour map is missing or has the wrong length")
        table = (np.array(cmap, np.uint32).reshape(3, n).T >> 8).astype(np.uint8)
        idx = px[:, :, 0]
        rep = Raster(table[idx], "rgb", 8)
    elif ch in (1, 2):
        if ch == 2:
            g, a = px[:, :, 0], px[:, :, 1]
            rep = Raster(np.stack([g, g, g, a], axis=2), "rgba", b)
            notes.append("grey+extra sample represented as RGBA (the extra sample taken as alpha)")
        else:
            rep = Raster(np.ascontiguousarray(px[:, :, 0]), "gray", b)
    else:
        rep = Raster(np.ascontiguousarray(px), "rgb" if ch == 3 else "rgba", b)
    text = "\n".join(words)
    notes.extend(screen_words(text))
    return Decoded(detected, R.Raster(rep.pixels, rep.mode, rep.bits, words=text), tuple(notes), False)


# --------------------------------------------------------------------------------------------------------------- JPEG


def _exif_words(blob: bytes, facts: dict) -> list[str]:
    try:
        _, ifds = read_ifds(blob, 0, max_ifds=2)
    except Exception:          # Exif blocks are often damaged; their absence is not an error of the image
        facts["exif"] = "present, unreadable"
        return []
    if not ifds:
        return []
    ifd0 = ifds[0]
    if 34853 in ifd0:
        facts["gps_block_present"] = True
    if 274 in ifd0:
        facts["orientation"] = _one(ifd0, 274)
    return [f"{TIFF_TEXT[t]}: {ifd0[t]}" for t in TIFF_TEXT if isinstance(ifd0.get(t), str) and ifd0[t].strip()]


def _xmp_words(packet: bytes, limits: Limits) -> list[str]:
    try:
        root = parse_xml(packet.strip(b"\x00"), limits, "the XMP packet")
    except Exception:
        return []
    out = []
    for el in root.iter():
        if el.text and el.text.strip() and len(out) < 256:
            out.append(f"{local(el.tag)}: {el.text.strip()}")
    return out


def read_jpeg(data: bytes, limits: Limits) -> tuple[dict, list[str]]:
    r = Reader(data, ">", 2, "the JPEG file")
    facts, words, blocks = {}, [], []
    while r.remaining >= 4:
        if r.u8() != 0xFF:
            raise Malformed("a JPEG marker is missing")
        marker = r.u8()
        while marker == 0xFF:
            marker = r.u8()
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            break
        length = r.u16()
        if length < 2:
            raise Malformed("a JPEG segment has an impossible length")
        body = r.take(length - 2)
        if marker in SOF_NAMES and len(body) >= 6:
            precision, height, width, comps = struct.unpack(">BHHB", body[:6])
            facts.update({"width": width, "height": height, "components": comps, "bits": precision,
                          "coding": SOF_NAMES[marker]})
        elif marker == 0xE0 and body[:5] == b"JFIF\x00":
            blocks.append("JFIF")
        elif marker == 0xE1 and body[:6] == b"Exif\x00\x00":
            blocks.append("Exif")
            words += _exif_words(body[6:], facts)
        elif marker == 0xE1 and body.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            blocks.append("XMP")
            words += _xmp_words(body[29:], limits)
        elif marker == 0xE2 and body[:12] == b"ICC_PROFILE\x00":
            if "ICC" not in blocks:
                blocks.append("ICC")
        elif marker == 0xEE and body[:5] == b"Adobe":
            blocks.append("Adobe")
        elif marker == 0xFE:
            words.append("comment: " + body.decode("latin-1"))
        elif marker == 0xDA:
            break
    if "width" not in facts:
        raise Malformed("the JPEG file has no frame header")
    facts["metadata_blocks"] = blocks
    if facts.get("height") == 0:
        facts["height"] = "defined later in the stream (DNL)"
    return facts, words


# ---------------------------------------------------------------------------------------------------- WebP, HEIF, PSD


def read_webp(data: bytes) -> dict:
    r = Reader(data, "<", 12, "the WebP file")
    facts: dict = {}
    frames = 0
    while r.remaining >= 8:
        cid = r.take(4)
        size = r.u32()
        body = r.take(min(size, r.remaining))
        if size % 2 and r.remaining:
            r.skip(1)
        if cid == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", body[6:10])
            facts.setdefault("width", w & 0x3FFF)
            facts.setdefault("height", h & 0x3FFF)
            facts.setdefault("coding", "lossy (VP8)")
        elif cid == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
            b = int.from_bytes(body[1:5], "little")
            facts.setdefault("width", (b & 0x3FFF) + 1)
            facts.setdefault("height", ((b >> 14) & 0x3FFF) + 1)
            facts.setdefault("coding", "lossless (VP8L)")
        elif cid == b"VP8X" and len(body) >= 10:
            flags = body[0]
            facts["width"] = int.from_bytes(body[4:7], "little") + 1
            facts["height"] = int.from_bytes(body[7:10], "little") + 1
            facts["extended"] = {"icc": bool(flags & 0x20), "alpha": bool(flags & 0x10), "exif": bool(flags & 0x08),
                                 "xmp": bool(flags & 0x04), "animation": bool(flags & 0x02)}
        elif cid == b"ANMF":
            frames += 1
    if frames:
        facts["frames"] = frames
    if "width" not in facts:
        raise Malformed("the WebP file has no image header")
    return facts


def read_heif(data: bytes, limits: Limits, fmt: str) -> dict:
    from .video_containers import _find, _first, iso_boxes
    boxes = iso_boxes(data, 0, len(data), 0, {"n": 0})
    facts: dict = {}
    ftyp = _first(boxes, [b"ftyp"])
    if ftyp:
        facts["brands"] = [data[i:i + 4].decode("latin-1") for i in [ftyp[2]] + list(range(ftyp[2] + 8, ftyp[3], 4))
                           ][:12]
    meta = _first(boxes, [b"meta"])
    if meta is None:
        raise Malformed(f"the {fmt.upper()} file has no meta box")
    for box in iso_boxes(data, meta[2] + 4, meta[3], 1, {"n": 0}):
        if box[0] == b"iinf":
            facts["items"] = struct.unpack(">H", data[box[2] + 4:box[2] + 6])[0] if data[box[2]] == 0 else \
                struct.unpack(">I", data[box[2] + 4:box[2] + 8])[0]
        if box[0] == b"pitm":
            facts["primary_item"] = struct.unpack(">H", data[box[2] + 4:box[2] + 6])[0]
    for ispe in _find(boxes, [b"meta", b"iprp", b"ipco", b"ispe"])[:1]:
        facts["width"], facts["height"] = struct.unpack(">II", data[ispe[2] + 4:ispe[2] + 12])
    for pixi in _find(boxes, [b"meta", b"iprp", b"ipco", b"pixi"])[:1]:
        n = data[pixi[2] + 4]
        facts["components"] = n
        facts["bits"] = list(data[pixi[2] + 5:pixi[2] + 5 + n])
    codecs = {b"hvcC": "HEVC", b"av1C": "AV1", b"avcC": "AVC", b"jpgC": "JPEG"}
    for b in _find(boxes, [b"meta", b"iprp", b"ipco"])[:1]:
        found = sorted({codecs[c[0]] for c in b[4] if c[0] in codecs})
        if found:
            facts["coding"] = found
    if "width" not in facts:
        facts["width"] = facts["height"] = "not stated (no ispe property found)"
    return facts


def read_psd(data: bytes) -> dict:
    r = Reader(data, ">", 4, "the PSD header")
    version = r.u16()
    r.skip(6)
    channels, height, width, depth, mode = r.unpack("HIIHH")
    facts = {"variant": "PSB" if version == 2 else "PSD", "width": width, "height": height, "components": channels,
             "bits": depth, "mode": PSD_MODES.get(mode, mode)}
    try:
        r.skip(r.u32())                    # colour mode data
        r.skip(r.u32())                    # image resources
        r.u32() if version == 1 else r.u64()
        r.u32() if version == 1 else r.u64()
        facts["layers"] = abs(r.i16())
    except Malformed:
        facts["layers"] = "not stated"
    return facts


# -------------------------------------------------------------------------------------------------------------- DICOM

UNCOMPRESSED = {"1.2.840.10008.1.2": ("<", False), "1.2.840.10008.1.2.1": ("<", True),
                "1.2.840.10008.1.2.2": (">", True)}
LONG_VRS = {b"OB", b"OD", b"OF", b"OL", b"OV", b"OW", b"SQ", b"SV", b"UC", b"UN", b"UR", b"UT", b"UV"}
DICOM_TAGS = {(0x0028, 0x0010): "rows", (0x0028, 0x0011): "columns", (0x0028, 0x0100): "bits_allocated",
              (0x0028, 0x0101): "bits_stored", (0x0028, 0x0103): "pixel_representation",
              (0x0028, 0x0002): "samples_per_pixel", (0x0028, 0x0004): "photometric",
              (0x0028, 0x0008): "frames", (0x0028, 0x0006): "planar", (0x0008, 0x0060): "modality"}


def _dicom_elements(data: bytes, pos: int, end: int, endian: str, explicit: bool, out: dict, depth: int,
                    deadline: Deadline, counter: list):
    while pos + 8 <= end:
        counter[0] += 1
        if counter[0] % 4096 == 0:
            deadline.check("reading DICOM elements")
        if counter[0] > 1_000_000:
            raise Malformed("the DICOM file holds too many elements")
        group, elem = struct.unpack(endian + "HH", data[pos:pos + 4])
        if group == 0xFFFE:                      # item / delimiters
            length = struct.unpack(endian + "I", data[pos + 4:pos + 8])[0]
            pos += 8
            if elem in (0xE00D, 0xE0DD):
                return pos
            if length != 0xFFFFFFFF:
                pos += length
            else:
                pos = _dicom_elements(data, pos, end, endian, explicit, {}, depth + 1, deadline, counter)
            continue
        vr = None
        if explicit and group != 0xFFFE:
            vr = data[pos + 4:pos + 6]
            if vr in LONG_VRS:
                length = struct.unpack(endian + "I", data[pos + 8:pos + 12])[0]
                head = 12
            else:
                length = struct.unpack(endian + "H", data[pos + 6:pos + 8])[0]
                head = 8
        else:
            length = struct.unpack(endian + "I", data[pos + 4:pos + 8])[0]
            head = 8
        value_at = pos + head
        if (group, elem) == (0x7FE0, 0x0010):
            out["pixel_data"] = (value_at, length)
            return end
        if length == 0xFFFFFFFF:
            if depth > 16:
                raise Malformed("DICOM sequences nest too deeply")
            pos = _dicom_elements(data, value_at, end, endian, explicit, {}, depth + 1, deadline, counter)
            continue
        if value_at + length > end:
            raise Malformed("a DICOM element runs past the end of the file")
        key = DICOM_TAGS.get((group, elem))
        if key and depth == 0:
            raw = data[value_at:value_at + length]
            if key in ("photometric", "modality", "frames"):
                out[key] = raw.decode("latin-1").strip("\x00 ")
            elif length >= 2:
                out[key] = struct.unpack(endian + "H", raw[:2])[0]
        pos = value_at + length
    return pos


def decode_dicom(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    if data[128:132] != b"DICM":
        raise Malformed("the DICOM prefix is missing")
    pos, syntax = 132, None
    while pos + 12 <= len(data):                 # group 0002: always explicit VR little endian
        group, elem = struct.unpack("<HH", data[pos:pos + 4])
        if group != 0x0002:
            break
        vr = data[pos + 4:pos + 6]
        if vr in LONG_VRS:
            length, head = struct.unpack("<I", data[pos + 8:pos + 12])[0], 12
        else:
            length, head = struct.unpack("<H", data[pos + 6:pos + 8])[0], 8
        if elem == 0x0010:
            syntax = data[pos + head:pos + head + length].decode("latin-1").strip("\x00 ")
        pos += head + length
    facts = {"transfer_syntax": syntax}
    if syntax not in UNCOMPRESSED:
        _header_only("dicom", facts, ("a decoder for DICOM transfer syntax " + str(syntax),), data)
    endian, explicit = UNCOMPRESSED[syntax]
    out: dict = {}
    try:
        _dicom_elements(data, pos, len(data), endian, explicit, out, 0, deadline, [0])
    except (struct.error, IndexError):
        raise Malformed("a DICOM element header is cut short") from None
    facts.update({k: v for k, v in out.items() if k != "pixel_data"})
    rows, cols = out.get("rows"), out.get("columns")
    alloc, stored = out.get("bits_allocated"), out.get("bits_stored", out.get("bits_allocated"))
    spp, photo = out.get("samples_per_pixel", 1), out.get("photometric", "")
    if "pixel_data" not in out or not rows or not cols:
        _header_only("dicom", facts, ("pixel data or its size (none found)",), data)
    if alloc not in (8, 16) or spp not in (1, 3) or (spp == 3 and alloc != 8):
        _header_only("dicom", facts, (f"a reader for {alloc}-bit DICOM samples with {spp} component(s)",), data)
    at, length = out["pixel_data"]
    n = rows * cols * spp * (alloc // 8)
    need(n * 4, limits, "the DICOM image")
    if length == 0xFFFFFFFF or at + n > len(data):
        raise Malformed("the DICOM pixel data is shorter than its size")
    notes = ["DICOM text elements (which may identify a person) are not returned"]
    try:
        frames = int(out.get("frames") or 1)
    except ValueError:
        frames = 1
    if frames > 1:
        notes.append(f"the first of {frames} frames was decoded")
    dtype = np.dtype(endian + ("u1" if alloc == 8 else "u2"))
    px = np.frombuffer(data, dtype, rows * cols * spp, at).astype(np.uint8 if alloc == 8 else np.uint16)
    if out.get("pixel_representation") == 1:
        shift = 1 << (stored - 1)
        signed = px.astype(np.int64)
        signed = np.where(signed & (1 << (stored - 1)), signed - (1 << stored), signed) if stored < alloc else \
            px.view(np.int8 if alloc == 8 else np.int16).astype(np.int64)
        px = (signed + shift).astype(px.dtype)
        notes.append(f"signed samples offset by {shift} so that they are unsigned")
    elif stored and stored < alloc:
        px = px & ((1 << stored) - 1)
    if photo == "MONOCHROME1":
        notes.append("MONOCHROME1: higher values are darker (values kept as stored)")
    bits = stored if stored and stored <= alloc else alloc
    if spp == 3:
        img = px.reshape(3, rows, cols).transpose(1, 2, 0) if out.get("planar") == 1 else px.reshape(rows, cols, 3)
        return Decoded(detected, Raster(np.ascontiguousarray(img), "rgb", bits), tuple(notes), False)
    return Decoded(detected, Raster(px.reshape(rows, cols), "gray", bits), tuple(notes), False)


# --------------------------------------------------------------------------------------------------------------- FITS


def decode_fits(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    cards, pos = {}, 0
    words = []
    while True:
        if pos + 80 > len(data) or pos > 2880 * 100:
            raise Malformed("the FITS header has no END card")
        card = data[pos:pos + 80].decode("ascii", "replace")
        pos += 80
        key = card[:8].strip()
        if key == "END":
            break
        if key in ("COMMENT", "HISTORY") or (key and card[8:10] != "= "):
            if card[8:].strip():
                words.append(f"{key.lower()}: {card[8:].strip()}")
            continue
        value = card[10:].split("/", 1)[0].strip() if not card[10:].lstrip().startswith("'") else \
            card[10:].strip().split("'")[1]
        cards[key] = value
        if isinstance(value, str) and card[10:].lstrip().startswith("'") and key in (
                "OBJECT", "TELESCOP", "INSTRUME", "OBSERVER", "DATE-OBS", "ORIGIN", "BUNIT"):
            words.append(f"{key.lower()}: {value.strip()}")
    data_at = (pos + 2879) // 2880 * 2880
    try:
        bitpix, naxis = int(cards["BITPIX"]), int(cards["NAXIS"])
        dims = [int(cards[f"NAXIS{i + 1}"]) for i in range(naxis)]
    except (KeyError, ValueError):
        raise Malformed("the FITS header lacks BITPIX, NAXIS or an axis length") from None
    bzero = float(cards.get("BZERO", "0") or 0)
    bscale = float(cards.get("BSCALE", "1") or 1)
    facts = {"bitpix": bitpix, "axes": dims, "bzero": bzero, "bscale": bscale}
    if dims and len(dims) >= 2:
        facts["width"], facts["height"] = dims[0], dims[1]
    text = "\n".join(words)
    if bitpix not in (8, 16) or not (naxis == 2 or (naxis == 3 and dims[2] == 1)) or bscale != 1:
        _header_only("fits", facts, (f"a representation for FITS BITPIX {bitpix} data with {naxis} axes",),
                     data, words)
    w, h = dims[0], dims[1]
    n = w * h * (bitpix // 8)
    need(n * 3, limits, "the FITS image")
    if data_at + n > len(data):
        raise Malformed("the FITS data unit is shorter than its axes")
    notes = []
    if bitpix == 8:
        px = np.frombuffer(data, np.uint8, w * h, data_at).reshape(h, w).copy()
        bits = 8
    else:
        raw = np.frombuffer(data, ">i2", w * h, data_at).astype(np.int64)
        px = (raw + 32768).astype(np.uint16).reshape(h, w)
        bits = 16
        if bzero != 32768:
            notes.append("signed 16-bit samples offset by 32768 so that they are unsigned (BZERO is "
                         f"{bzero:g}, not 32768)")
    if bzero not in (0, 32768) and bitpix == 8:
        notes.append(f"BZERO {bzero:g} is not applied")
    notes.append("FITS rows are stored bottom-up by convention; the first stored row is row 0 here")
    notes.extend(screen_words(text))
    return Decoded(detected, R.Raster(px, "gray", bits, words=text), tuple(notes), False)


# ----------------------------------------------------------------------------------------------------------- adapters


class JPEGAdapter(Adapter):
    name = "b2-jpeg"
    formats = ("jpeg",)
    families = ("image",)

    def decode(self, data, detected, limits):
        facts, words = read_jpeg(data, limits)
        _header_only("jpeg", facts, ("a JPEG decoder",), data, words)


class WebPAdapter(Adapter):
    name = "b2-webp"
    formats = ("webp",)
    families = ("image",)

    def decode(self, data, detected, limits):
        facts = read_webp(data)
        need_ = "a WebP lossless (VP8L) decoder" if "VP8L" in str(facts.get("coding")) else "a WebP (VP8) decoder"
        _header_only("webp", facts, (need_,), data)


class HEIFAdapter(Adapter):
    name = "b2-heif"
    formats = ("heic", "avif")
    families = ("image",)

    def decode(self, data, detected, limits):
        try:
            facts = read_heif(data, limits, detected.format)
        except (struct.error, IndexError):
            raise Malformed(f"a {detected.format.upper()} property box is cut short") from None
        _header_only(detected.format, facts,
                     ("an AV1 image decoder",) if detected.format == "avif" else ("an HEVC image decoder",), data)


class PSDAdapter(Adapter):
    name = "b2-psd"
    formats = ("psd",)
    families = ("image",)

    def decode(self, data, detected, limits):
        _header_only("psd", read_psd(data), ("a Photoshop document decoder",), data)


class TIFFAdapter(Adapter):
    name = "b2-tiff"
    formats = ("tiff",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_tiff(data, detected, limits)


class DICOMAdapter(Adapter):
    name = "b2-dicom"
    formats = ("dicom",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_dicom(data, detected, limits)


class FITSAdapter(Adapter):
    name = "b2-fits"
    formats = ("fits",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return decode_fits(data, detected, limits)


ADAPTERS = (JPEGAdapter(), WebPAdapter(), HEIFAdapter(), PSDAdapter(), TIFFAdapter(), DICOMAdapter(), FITSAdapter())

__all__ = ["ADAPTERS", "DICOMAdapter", "FITSAdapter", "HEIFAdapter", "JPEGAdapter", "PSDAdapter", "TIFFAdapter",
           "WebPAdapter", "decode_dicom", "decode_fits", "decode_tiff", "read_ifds", "read_jpeg", "read_psd",
           "read_webp"]
