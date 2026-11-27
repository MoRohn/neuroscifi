"""Lane B1 archives: ZIP and TAR (plain and compressed) assembled in the test, with hostile variants: traversal
names, links, encrypted members, inflation bombs, member floods, nesting deeper than allowed; 7z and RAR."""
import gzip
import io
import tarfile

import pytest
import zstandard

from neuroscifi.artifacts import types as T

import _b1_support
from _b1_support import make_zip

b1reg = _b1_support.b1reg      # the fixture

CSV = b"k,v\na,1\nb,2\n"


def by_path(bundle):
    return {m.path: m for m in bundle.members}


def test_zip_members_decoded_recursively(b1reg):
    inner = make_zip([("deep.csv", CSV)])
    data = make_zip([("data/t.csv", CSV), ("notes.md", b"# Title\n\n- a\n- b\n"), ("inner.zip", inner),
                     ("dir/", b"")])
    out = b1reg.decode(data, name="a.zip")
    assert out.detected.format == "zip"
    m = by_path(out.representation)
    assert m["data/t.csv"].detected.format == "csv" and m["data/t.csv"].inner.n_rows == 2
    assert m["data/t.csv"].detected.container == "zip" and m["data/t.csv"].size == len(CSV)
    assert m["notes.md"].inner.blocks[0].text == "Title"
    nested = by_path(m["inner.zip"].inner)
    assert list(nested["deep.csv"].inner.columns[1].values) == [1, 2]
    assert m["dir/"].detected.format == "directory"
    assert out.notes[0].startswith("4 member(s): 3 decoded")


def test_zip_hostile_names_and_links_refuse_the_whole_archive(b1reg):
    """The main session's ruling (integration): a member that climbs out, an absolute name or a link makes the
    WHOLE archive refused; nothing inside, not even the benign ok.csv, is decoded; the listing is inspection only."""
    data = make_zip([("../../etc/passwd", b"root"), ("/abs.txt", b"x"), ("ok.csv", CSV), ("link", b"/etc/shadow")],
                    symlinks=("link",))
    with pytest.raises(T.Refused) as err:
        b1reg.decode(data, name="h.zip")
    # the name is the file's own text: in the findings, never in the message (fix lane F1, security N5)
    assert err.value.rule == "unsafe_member_name" and "passwd" not in str(err.value)
    assert "climbs out" in str(err.value) and "3 more" not in str(err.value) and "2 more" in str(err.value)
    assert "nothing inside was decoded" in str(err.value)
    assert err.value.findings == ('member "../../etc/passwd": 4 bytes', 'member "/abs.txt": 1 bytes',
                                  f'member "ok.csv": {len(CSV)} bytes', 'member "link": 11 bytes')
    for alone, why in ((("/abs.txt", b"x"), "absolute"), (("link", b"/etc/shadow"), "symbolic link")):
        with pytest.raises(T.Refused) as err:
            b1reg.decode(make_zip([alone, ("ok.csv", CSV)], symlinks=("link",)), name="h.zip")
        assert err.value.rule == "unsafe_member_name" and why in str(err.value)
    odd = make_zip([("ok\u202e\x1b.csv", CSV), ("../x", b"x")])
    with pytest.raises(T.Refused) as err:                           # the name is data: escaped in the reason
        b1reg.decode(odd, name="o.zip")
    assert "\x1b" not in str(err.value) and "\u202e" not in str(err.value)


def test_zip_encrypted_members_are_refused_one_by_one(b1reg):
    enc = make_zip([("secret.csv", CSV), ("open.csv", CSV)], flags=1)
    m = by_path(b1reg.decode(enc, name="e.zip").representation)
    assert m["secret.csv"].inner is None and "encrypted" in m["secret.csv"].note


def test_zip_bomb_member_refuses_the_whole_archive(b1reg):
    # lane FX, item 2 (outcome.refuses_whole): a bomb-like member refuses the whole archive; its honest sibling is
    # not answered, and the listing is kept as findings
    data = make_zip([("bomb.txt", b"\x00" * (20 << 20)), ("ok.csv", CSV)])
    with pytest.raises(T.Refused) as err:
        b1reg.decode(data, name="b.zip")
    assert err.value.rule == "max_ratio" and any("ok.csv" in f for f in err.value.findings)


def test_zip_lying_sizes_and_crc(b1reg):
    good = make_zip([("t.csv", CSV)])
    corrupt = bytearray(good)
    pos = corrupt.find(b"PK\x01\x02")
    corrupt[pos + 16:pos + 20] = b"\x00\x00\x00\x00"           # central CRC
    with pytest.raises(T.Refused) as err:                       # lane FX: a damaged stored member refuses it all
        b1reg.decode(bytes(corrupt), name="c.zip")
    assert err.value.rule == "header_mismatch"  # lane FX4: only the central entry was changed, so the two headers disagree (refused before reading)
    lying = make_zip([("t.csv", CSV)])
    pos = lying.find(b"PK\x01\x02")
    lying = lying[:pos + 24] + (len(CSV) - 3).to_bytes(4, "little") + lying[pos + 28:]   # central size lies
    with pytest.raises(T.ArtifactError):
        b1reg.decode(lying, name="l.zip")


def test_zip_member_flood_is_partial(b1reg):
    data = make_zip([(f"f{i}.txt", b"x") for i in range(50)])
    out = b1reg.decode(data, name="f.zip", limits=T.Limits(max_members=10))
    assert out.partial and len(out.representation.members) == 10


def test_zip_declared_count_lie_is_malformed(b1reg):
    data = make_zip([("a.csv", CSV)], extra_central=3)
    with pytest.raises(T.Malformed):
        b1reg.decode(data, name="l.zip")


def test_nesting_deeper_than_allowed_refuses_the_whole_archive(b1reg):
    # lane FX, item 2: nesting past max_depth is the nested bomb's shape: the whole archive is refused
    data = CSV
    for i in range(4):
        data = make_zip([(f"level{i}.{'zip' if i else 'csv'}", data)])
    with pytest.raises(T.Refused) as err:
        b1reg.decode(data, name="n.zip", limits=T.Limits(max_depth=2))
    assert err.value.rule == "max_depth"
    assert b1reg.decode(data, name="n.zip").representation.members[0].inner is not None     # within the limit


def tar_bytes(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
            elif kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tf.addfile(info)
            elif kind == "hard":
                info.type, info.linkname = tarfile.LNKTYPE, payload
                tf.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tf.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
    return buf.getvalue()


TAR_ENTRIES = [("d", "dir", None), ("d/t.csv", "file", CSV)]
UNSAFE_TAR = {"climbs": (("../up.txt", "file", b"x"), "climbs out"),
              "sym": (("s", "sym", "/etc/passwd"), 'symbolic link to "/etc/passwd"'),
              "hard": (("h", "hard", "d/t.csv"), "hard link"),
              "fifo": (("p", "fifo", None), "special entry")}


def check_tar(bundle):
    m = by_path(bundle)
    assert m["d"].detected.format == "directory"
    assert m["d/t.csv"].inner.n_rows == 2


def test_tar_plain(b1reg):
    out = b1reg.decode(tar_bytes(TAR_ENTRIES), name="a.tar")
    assert out.detected.format == "tar"
    check_tar(out.representation)


@pytest.mark.parametrize("kind", list(UNSAFE_TAR))
def test_an_unsafe_tar_member_refuses_the_whole_archive(b1reg, kind):
    entry, why = UNSAFE_TAR[kind]
    with pytest.raises(T.Refused) as err:
        b1reg.decode(tar_bytes(TAR_ENTRIES + [entry]), name="a.tar")
    assert err.value.rule == "unsafe_member_name" and why in str(err.value)
    assert any('"d/t.csv"' in f for f in err.value.findings)       # listed, never decoded


@pytest.mark.parametrize("fmt,pack", [("gzip", gzip.compress),
                                      ("zstd", lambda b: zstandard.ZstdCompressor().compress(b))])
def test_tar_compressed(b1reg, fmt, pack):
    """K's compressed-stream adapter inflates; the inner bytes are a tar, read by B1's tar adapter."""
    out = b1reg.decode(pack(tar_bytes(TAR_ENTRIES)), name="a.bin")
    assert out.detected.format == fmt
    inner = out.representation.members[0]
    assert inner.detected.format == "tar"
    check_tar(inner.inner)


def test_tar_gz_bomb_refused(b1reg):
    data = gzip.compress(tar_bytes([("z", "file", b"\x00" * (30 << 20))]))
    with pytest.raises(T.Refused) as err:
        b1reg.decode(data, name="z.tgz")
    assert err.value.rule == "max_ratio"


def test_tar_truncated_member(b1reg):
    # lane FX, item 2: a header stating more bytes than the archive holds is a lying size: the whole TAR is refused
    data = tar_bytes([("t.csv", "file", CSV * 200)])[:900]
    with pytest.raises(T.Refused) as err:
        b1reg.decode(data, name="t.tar")
    assert err.value.rule == "lying_size"


def test_7z_and_rar_identified_unsupported(b1reg):
    with pytest.raises(T.Unsupported) as err:
        b1reg.decode(b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 40, name="x.7z")
    assert err.value.facts["version"] == "0.4" and err.value.needs
    with pytest.raises(T.Unsupported) as err:
        b1reg.decode(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 40, name="x.rar")
    assert err.value.facts["version"] == "5"
