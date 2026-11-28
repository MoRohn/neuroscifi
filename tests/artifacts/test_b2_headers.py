"""Header-only formats (JPEG, WebP, HEIC/AVIF, PSD, compressed TIFF, MP3, AAC, FLAC, Ogg, M4A), the decodable
TIFF/DICOM/FITS paths, video containers (MP4, Matroska/WebM, AVI) and MIDI (lane B2). Every file is built here
byte by byte; header-only answers carry the facts and name what is missing, and never a pixel or sample."""
import struct
import zlib

import numpy as np
import pytest

from b2_fixtures import b2_registered  # noqa: F401

from neuroscifi.artifacts import adapters
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2, video_containers
from neuroscifi.artifacts.types import HeaderOnly



def _header_only(data, **kw):
    with pytest.raises(HeaderOnly) as err:
        adapters.decode(data, **kw)
    assert err.value.needs and isinstance(err.value.opaque, T.Opaque)
    return err.value


# ---------------------------------------------------------------------------------------------------------- images


def _tiff_ifd(entries, e="<"):
    """A minimal little/big-endian TIFF with one IFD; entries: (tag, type, values), data placed after the IFD."""
    codes = {1: "B", 2: "s", 3: "H", 4: "I"}
    n = len(entries)
    ifd_at = 8
    extra_at = ifd_at + 2 + 12 * n + 4
    head = (b"II*\x00" if e == "<" else b"MM\x00*") + struct.pack(e + "I", ifd_at)
    ifd, extra = struct.pack(e + "H", n), b""
    for tag, typ, vals in sorted(entries):
        if typ == 2:
            blob = vals.encode() + b"\x00"
            count = len(blob)
        else:
            blob = struct.pack(e + codes[typ] * len(vals), *vals)
            count = len(vals)
        if len(blob) <= 4:
            ifd += struct.pack(e + "HHI", tag, typ, count) + blob.ljust(4, b"\x00")
        else:
            ifd += struct.pack(e + "HHII", tag, typ, count, extra_at + len(extra))
            extra += blob + (b"\x00" if len(blob) % 2 else b"")
    return head + ifd + b"\x00\x00\x00\x00" + extra


def _tiff_image(w, h, pixels, *, spp=3, bits=8, comp=1, photo=2, e="<", extra_tags=()):
    strip = pixels
    if comp == 32773:
        strip = b"".join(bytes((len(pixels[i:i + 128]) - 1,)) + pixels[i:i + 128] for i in range(0, len(pixels), 128))
    elif comp == 8:
        strip = zlib.compress(pixels)
    entries = [(256, 4, (w,)), (257, 4, (h,)), (258, 3, (bits,) * spp), (259, 3, (comp,)), (262, 3, (photo,)),
               (277, 3, (spp,)), (278, 4, (h,)), (279, 4, (len(strip),)), (273, 4, (0,)), *extra_tags]
    probe = _tiff_ifd(entries, e)
    entries = [x if x[0] != 273 else (273, 4, (len(probe),)) for x in entries]
    return _tiff_ifd(entries, e) + strip


@pytest.mark.parametrize("comp", [1, 32773, 8])
@pytest.mark.parametrize("e", ["<", ">"])
def test_tiff_baseline_strips_decode(comp, e):
    img = np.random.default_rng(comp).integers(0, 256, (5, 7, 3)).astype(np.uint8)
    data = _tiff_image(7, 5, img.tobytes(), comp=comp, e=e, extra_tags=[(270, 2, "a test scan")])
    out = adapters.decode(data)
    assert out.detected.format == "tiff" and (out.representation.pixels == img).all()
    assert "description: a test scan" in out.representation.text()


def test_tiff_white_is_zero_and_16bit_grey():
    out = adapters.decode(_tiff_image(2, 1, bytes([0, 255]), spp=1, photo=0))
    assert out.representation.pixels.tolist() == [[255, 0]]
    out = adapters.decode(_tiff_image(2, 1, struct.pack(">2H", 1, 65535), spp=1, bits=16, photo=1, e=">"))
    assert out.representation.bits == 16 and out.representation.pixels.tolist() == [[1, 65535]]


def test_tiff_lzw_is_header_only():
    err = _header_only(_tiff_image(4, 4, b"\x00" * 48, comp=5))
    assert err.facts["compression"] == "LZW" and err.facts["width"] == 4 and "LZW" in err.needs[0]


def _jpeg():
    exif_tiff = _tiff_ifd([(270, 2, "holiday photo"), (271, 2, "CamCo"), (34853, 4, (0,))], "<")
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description><title>Sunset</title></rdf:Description></rdf:RDF></x:xmpmeta>')

    def seg(marker, body):
        return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body
    return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")
            + seg(0xE1, b"Exif\x00\x00" + exif_tiff)
            + seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp)
            + seg(0xFE, b"a comment") + seg(0xC2, struct.pack(">BHHB", 8, 480, 640, 3) + b"\x01\x22\x00" * 3)
            + seg(0xDA, b"\x00" * 10) + b"\x12\x34" * 50 + b"\xff\xd9")


def test_jpeg_header_facts_and_words_never_pixels():
    err = _header_only(_jpeg())
    f = err.facts
    assert (f["width"], f["height"], f["components"], f["bits"], f["coding"]) == (640, 480, 3, 8, "progressive")
    assert f["metadata_blocks"] == ["JFIF", "Exif", "XMP"] and f["gps_block_present"] is True
    assert "description: holiday photo" in err.words and "title: Sunset" in err.words and "a comment" in err.words
    assert "JPEG decoder" in err.needs[0] and "640" in str(err)


def test_webp_variants_heif_and_psd():
    vp8 = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", 320, 200) + b"\x00" * 10
    err = _header_only(b"RIFF" + struct.pack("<I", 4 + 8 + len(vp8)) + b"WEBP" + b"VP8 " + struct.pack("<I", len(vp8))
                       + vp8)
    assert (err.facts["width"], err.facts["height"], err.facts["coding"]) == (320, 200, "lossy (VP8)")
    b = (99 | (49 << 14)).to_bytes(4, "little")
    vp8l = b"\x2f" + b + b"\x00" * 4
    err = _header_only(b"RIFF" + struct.pack("<I", 4 + 8 + len(vp8l)) + b"WEBP" + b"VP8L" + struct.pack("<I", 9) + vp8l)
    assert (err.facts["width"], err.facts["height"]) == (100, 50) and "lossless" in err.needs[0]

    def box(kind, body):
        return struct.pack(">I", 8 + len(body)) + kind + body
    ispe = box(b"ispe", b"\x00" * 4 + struct.pack(">II", 4032, 3024))
    pixi = box(b"pixi", b"\x00" * 4 + b"\x03\x0a\x0a\x0a")
    meta = box(b"meta", b"\x00" * 4 + box(b"hdlr", b"\x00" * 8 + b"pict" + b"\x00" * 13)
               + box(b"pitm", b"\x00" * 4 + b"\x00\x01")
               + box(b"iprp", box(b"ipco", box(b"av1C", b"\x81\x00\x00\x00") + ispe + pixi)))
    data = box(b"ftyp", b"avif\x00\x00\x00\x00mif1avif") + meta
    out = _header_only(data)
    assert out.facts["format"] == "avif" and (out.facts["width"], out.facts["height"]) == (4032, 3024)
    assert out.facts["bits"] == [10, 10, 10] and out.facts["coding"] == ["AV1"]
    psd = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 4, 100, 200, 8, 3)
    psd += struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">I", 10) + struct.pack(">I", 6) + \
        struct.pack(">h", -3)
    err = _header_only(psd)
    assert (err.facts["width"], err.facts["height"], err.facts["mode"], err.facts["layers"]) == (200, 100, "RGB", 3)


def _dicom(rows, cols, pixels, *, syntax="1.2.840.10008.1.2.1", bits=16, signed=0, spp=1, photo=b"MONOCHROME2"):
    def el(g, e, vr, value):
        if vr in (b"OB", b"OW", b"SQ", b"UN"):
            return struct.pack("<HH", g, e) + vr + b"\x00\x00" + struct.pack("<I", len(value)) + value
        return struct.pack("<HH", g, e) + vr + struct.pack("<H", len(value)) + value
    uid = syntax.encode() + (b"\x00" if len(syntax) % 2 else b"")
    meta = el(2, 0x10, b"UI", uid)
    body = el(0x0008, 0x0060, b"CS", b"CT")
    body += el(0x0010, 0x0010, b"PN", b"Doe^Jane")             # a person's name: must never be returned
    body += el(0x0028, 0x0002, b"US", struct.pack("<H", spp)) + el(0x0028, 0x0004, b"CS", photo)
    body += el(0x0028, 0x0010, b"US", struct.pack("<H", rows)) + el(0x0028, 0x0011, b"US", struct.pack("<H", cols))
    body += el(0x0028, 0x0100, b"US", struct.pack("<H", bits)) + el(0x0028, 0x0101, b"US", struct.pack("<H", bits))
    body += el(0x0028, 0x0103, b"US", struct.pack("<H", signed))
    body += el(0x7FE0, 0x0010, b"OW", pixels)
    return b"\x00" * 128 + b"DICM" + meta + body


def test_dicom_uncompressed_decodes_and_never_returns_names():
    vals = np.array([[0, 100], [4095, -5]], np.int16)
    out = adapters.decode(_dicom(2, 2, vals.astype("<i2").tobytes(), signed=1))
    r = out.representation
    assert out.detected.format == "dicom" and r.bits == 16
    assert (r.pixels.astype(np.int64) - 32768).tolist() == vals.tolist()
    assert "Doe" not in r.text() and not any("Doe" in n for n in out.notes)
    err = _header_only(_dicom(2, 2, b"\x00" * 8, syntax="1.2.840.10008.1.2.4.50"))
    assert "1.2.840.10008.1.2.4.50" in err.needs[0]


def _fits(bitpix, w, h, data, extra=()):
    cards = [f"SIMPLE  = {'T':>20}", f"BITPIX  = {bitpix:>20}", f"NAXIS   = {2:>20}", f"NAXIS1  = {w:>20}",
             f"NAXIS2  = {h:>20}", *extra, "OBJECT  = 'M31     '", "END"]
    head = "".join(c.ljust(80) for c in cards).encode()
    head += b" " * (-len(head) % 2880)
    return head + data + b"\x00" * (-len(data) % 2880)


def test_fits_16bit_with_bzero_and_float_header_only():
    vals = np.array([[0, 1], [65535, 32768]], np.int64)
    out = adapters.decode(_fits(16, 2, 2, (vals - 32768).astype(">i2").tobytes(), [f"BZERO   = {32768:>20}"]))
    assert out.representation.pixels.tolist() == vals.tolist() and "object: M31" in out.representation.text()
    err = _header_only(_fits(-32, 2, 2, np.zeros(4, ">f4").tobytes()))
    assert err.facts["bitpix"] == -32 and err.facts["axes"] == [2, 2]


# ----------------------------------------------------------------------------------------------------------- sound


def test_mp3_with_xing_and_id3():
    frame = bytearray(417)
    frame[:4] = b"\xff\xfb\x90\x64"                 # MPEG-1 layer III, 128 kbit/s, 44.1 kHz, joint stereo
    frame[36:40] = b"Xing"
    frame[40:44] = struct.pack(">I", 1)
    frame[44:48] = struct.pack(">I", 1000)
    tit2 = b"TIT2" + struct.pack(">I", 6) + b"\x00\x00" + b"\x03Song"
    id3 = b"ID3\x03\x00\x00" + bytes((0, 0, 0, len(tit2))) + tit2
    err = _header_only(id3 + bytes(frame) + b"\x00" * 100)
    f = err.facts
    assert (f["rate_hz"], f["bitrate_kbps"], f["channels"], f["frames"]) == (44100, 128, 2, 1000)
    assert f["duration_s"] == round(1000 * 1152 / 44100, 3) and "title: Song" in err.words


def test_aac_adts_frame_walk():
    def frame(n):
        length = 7 + n
        h = bytes((0xFF, 0xF1, (1 << 6) | (4 << 2) | 0, (2 << 6) | (length >> 11), (length >> 3) & 0xFF,
                   ((length & 7) << 5) | 0x1F, 0xFC))
        return h + b"\x00" * n
    err = _header_only(b"".join(frame(10) for _ in range(43)))
    f = err.facts
    assert (f["profile"], f["rate_hz"], f["channels"], f["frames"]) == ("LC", 44100, 2, 43)
    assert f["duration_s"] == round(43 * 1024 / 44100, 3)


def _flac():
    b = (44100 << 44) | (1 << 41) | (15 << 36) | 88200
    streaminfo = b"\x00" * 10 + b.to_bytes(8, "big") + b"\x00" * 16
    vc = struct.pack("<I", 3) + b"lib" + struct.pack("<I", 1) + struct.pack("<I", 9) + b"TITLE=Abc"
    return (b"fLaC" + bytes((0,)) + len(streaminfo).to_bytes(3, "big") + streaminfo
            + bytes((0x84,)) + len(vc).to_bytes(3, "big") + vc + b"\xff\xf8" + b"\x00" * 20)


def test_flac_streaminfo_and_comments():
    err = _header_only(_flac())
    f = err.facts
    assert (f["rate_hz"], f["channels"], f["bits_per_sample"], f["duration_s"]) == (44100, 2, 16, 2.0)
    assert "TITLE: Abc" in err.words


def _ogg_page(serial, seq, granule, packets, flags=0):
    segs, body = b"", b""
    for p in packets:
        n = len(p)
        segs += bytes([255] * (n // 255) + [n % 255])
        body += p
    head = b"OggS\x00" + bytes((flags,)) + struct.pack("<qIII", granule, serial, seq, 0) + bytes((len(segs),))
    return head + segs + body


def test_ogg_opus_duration_from_granule():
    head = b"OpusHead\x01\x02" + struct.pack("<HI", 312, 44100) + b"\x00\x00\x00"
    tags = b"OpusTags" + struct.pack("<I", 3) + b"enc" + struct.pack("<I", 1) + struct.pack("<I", 8) + b"ARTIST=Z"
    data = _ogg_page(7, 0, 0, [head], 2) + _ogg_page(7, 1, 0, [tags]) + _ogg_page(7, 2, 96312, [b"\x00" * 50], 4)
    err = _header_only(data)
    f = err.facts
    assert (f["format"], f["channels"], f["duration_s"]) == ("ogg opus", 2, 2.0) and "ARTIST: Z" in err.words
    assert "Opus decoder" in err.needs[0]


def _box(kind, body):
    return struct.pack(">I", 8 + len(body)) + kind + body


def _mp4(handler=b"vide", codec=b"avc1", w=640, h=360, samples=250, timescale=25, duration=250):
    mvhd = _box(b"mvhd", b"\x00" * 4 + struct.pack(">IIII", 0, 0, 1000, 10000) + b"\x00" * 80)
    tkhd = _box(b"tkhd", b"\x00" * 4 + struct.pack(">III", 0, 0, 1) + b"\x00" * 4 + struct.pack(">I", 0)
                + b"\x00" * 8 + b"\x00" * 8 + b"\x00" * 36 + struct.pack(">II", w << 16, h << 16))
    mdhd = _box(b"mdhd", b"\x00" * 4 + struct.pack(">IIII", 0, 0, timescale, duration) + struct.pack(">H", 0x15C7)
                + b"\x00\x00")
    hdlr = _box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 12 + b"Main track\x00")
    if handler == b"vide":
        entry = b"\x00" * 8 + b"\x00" * 16 + struct.pack(">HH", w, h) + b"\x00" * 50
    else:
        entry = b"\x00" * 8 + struct.pack(">HHI", 0, 0, 0) + struct.pack(">HHHH", 2, 16, 0, 0) + \
            struct.pack(">I", 48000 << 16)
    stsd = _box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + struct.pack(">I", 8 + len(entry)) + codec + entry)
    stsz = _box(b"stsz", b"\x00" * 4 + struct.pack(">II", 0, samples))
    stbl = _box(b"stbl", stsd + stsz)
    trak = _box(b"trak", tkhd + _box(b"mdia", mdhd + hdlr + _box(b"minf", stbl)))
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isomavc1") + _box(b"moov", mvhd + trak) + _box(b"mdat", b"\x00" * 64)


def test_mp4_tracks_tree_and_table():
    out = adapters.decode(_mp4())
    assert out.detected.format == "mp4" and isinstance(out.representation, T.Tree)
    assert video_containers.NO_FRAMES in out.notes
    t = video_containers.tracks_table(out.representation)
    row = {c.name: (c.values[0] if c.kind == "text" else float(c.values[0])) for c in t.columns}
    assert row["kind"] == "video" and row["codec"] == "avc1" and row["width"] == 640 and row["height"] == 360
    assert row["duration_s"] == 10.0 and row["frame_rate"] == 25.0 and row["sample_count"] == 250
    assert row["language"] == "eng"
    assert out.representation.root.attrs["duration_s"] == "10"


def test_m4a_is_header_only_with_track_facts():
    data = _mp4(handler=b"soun", codec=b"mp4a", samples=100, timescale=48000, duration=96000)
    data = data.replace(b"isom\x00\x00\x02\x00isomavc1", b"M4A \x00\x00\x02\x00isomM4A ")
    err = _header_only(data)
    assert err.facts["codec"] == "mp4a" and err.facts["channels"] == 2 and err.facts["rate_hz"] == 48000
    assert err.facts["duration_s"] == 2.0 and "AAC decoder" in err.needs[0]


def _ebml(eid: int, body: bytes) -> bytes:
    idb = eid.to_bytes((eid.bit_length() + 7) // 8, "big")
    return idb + (0x01 << 56 | len(body)).to_bytes(8, "big") + body


def test_webm_tracks_and_title():
    header = _ebml(0x1A45DFA3, _ebml(0x4282, b"webm"))
    info = _ebml(0x1549A966, _ebml(0x2AD7B1, (1_000_000).to_bytes(3, "big")) + _ebml(0x4489, struct.pack(">d", 5000.0))
                 + _ebml(0x7BA9, b"My clip"))
    video = _ebml(0xAE, _ebml(0xD7, b"\x01") + _ebml(0x83, b"\x01") + _ebml(0x86, b"V_VP9")
                  + _ebml(0x23E383, (33_333_333).to_bytes(4, "big"))
                  + _ebml(0xE0, _ebml(0xB0, (1280).to_bytes(2, "big")) + _ebml(0xBA, (720).to_bytes(2, "big"))))
    audio = _ebml(0xAE, _ebml(0xD7, b"\x02") + _ebml(0x83, b"\x02") + _ebml(0x86, b"A_OPUS")
                  + _ebml(0xE1, _ebml(0xB5, struct.pack(">d", 48000.0)) + _ebml(0x9F, b"\x02")))
    seg = _ebml(0x18538067, info + _ebml(0x1654AE6B, video + audio) + _ebml(0x1F43B675, b"\x00" * 32))
    out = adapters.decode(header + seg)
    assert out.detected.format == "webm"
    t = video_containers.tracks_table(out.representation)
    cols = {c.name: c.values for c in t.columns}
    assert list(cols["codec"]) == ["V_VP9", "A_OPUS"] and cols["width"][0] == 1280 and cols["sample_rate_hz"][1] == 48000
    assert abs(cols["frame_rate"][0] - 30.0) < 0.01
    assert out.representation.root.attrs["duration_s"] == "5" and "My clip" in out.representation.text()


def test_avi_headers():
    avih = _riff_chunk(b"avih", struct.pack("<10I", 40000, 0, 0, 0, 100, 0, 1, 0, 320, 240) + b"\x00" * 16)
    strh = _riff_chunk(b"strh", b"vids" + b"MJPG" + struct.pack("<IHHI", 0, 0, 0, 0) + struct.pack("<4I", 1, 25, 0, 100)
                       + b"\x00" * 20)
    strf = _riff_chunk(b"strf", struct.pack("<IiiHHI", 40, 320, 240, 1, 24, struct.unpack("<I", b"MJPG")[0])
                       + b"\x00" * 20)
    strl = _riff_list(b"strl", strh + strf)
    data = b"RIFF" + struct.pack("<I", 0) + b"AVI " + _riff_list(b"hdrl", avih + strl) + _riff_list(b"movi", b"")
    data = data[:4] + struct.pack("<I", len(data) - 8) + data[8:]
    out = adapters.decode(data)
    assert out.detected.format == "avi" and out.representation.root.attrs["duration_s"] == "4"
    t = video_containers.tracks_table(out.representation)
    assert t.columns[2].values[0] == "MJPG" and any("MJPEG" in n for n in out.notes)


def _riff_chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def _riff_list(kind, body):
    return b"LIST" + struct.pack("<I", 4 + len(body)) + kind + body


def test_container_hostile_sizes_are_named():
    data = _mp4()
    with pytest.raises(T.ArtifactError):
        adapters.decode(data[:40], name="x.mp4")
    bad = bytearray(_mp4())
    struct.pack_into(">I", bad, 32, 0xFFFFFF00)          # moov box declares far more than present
    out = adapters.decode(bytes(bad))
    assert any("cut short" in n for n in out.notes)


# ------------------------------------------------------------------------------------------------------------ MIDI


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _mtrk(events):
    body = b"".join(_vlq(d) + e for d, e in events) + b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + body


def test_midi_notes_with_tempo_change_running_status_and_lyrics():
    tempo = _mtrk([(0, b"\xff\x51\x03" + (500000).to_bytes(3, "big")),
                   (960, b"\xff\x51\x03" + (250000).to_bytes(3, "big"))])
    notes = _mtrk([(0, b"\xff\x03\x05Piano"), (0, b"\x90\x3c\x64"), (480, b"\x40\x50"),   # running status: E4 on
                   (480, b"\x80\x3c\x00"), (0, b"\x90\x40\x00"),                          # C4 off; E4 off (vel 0)
                   (0, b"\xff\x05\x02la"), (480, b"\x91\x43\x7f"), (480, b"\x81\x43\x00")])
    data = b"MThd" + struct.pack(">IHHH", 6, 1, 2, 480) + tempo + notes
    out = adapters.decode(data)
    t = out.representation
    cols = {c.name: list(c.values) for c in t.columns}
    assert t.title == "notes" and t.n_rows == 3
    assert cols["pitch"] == [60, 64, 67] and cols["note"] == ["C4", "E4", "G4"]
    assert cols["start_s"] == [0.0, 0.5, 1.25] and cols["end_s"] == [1.0, 1.0, 1.5]      # tempo doubles at 1 s
    assert cols["channel"] == [1, 1, 2] and cols["velocity"] == [100, 80, 127]
    assert "track name: Piano" in t.text() and "lyric: la" in t.text()
    sh = _b2.content_shape(t)
    assert sh.kinds == ("sequence", "timeline") and sh.duration_s == 1.5


def test_midi_hostile():
    with pytest.raises(T.Malformed):
        adapters.decode(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + _mtrk([(0, b"\x3c\x64")]))
    with pytest.raises(T.Refused):
        adapters.decode(b"MThd" + struct.pack(">IHHH", 6, 1, 60000, 480))
    with pytest.raises(T.ArtifactError):
        adapters.decode(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", 5) + b"\xff\xff\xff\xff\xff")
