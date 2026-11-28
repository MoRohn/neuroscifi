"""Files named on the command line are untrusted (OMNIMIND lane S, audit-inputs hazards 1, 2 and 12).

`nsf epoch stream FILE.npz`, `nsf graphfile`, `nsf archive`, `nsf init --founding-graph` and `nsf graph rules add
FILE`: a FIFO or a device is refused instead of blocked on, a whole-file read is bounded by its format, a NumPy
file's header and zip directory are checked before NumPy allocates or decompresses anything, and a malformed file is
the command's named error (exit 2, "nsf: ..."), never a traceback. Honest files give byte-identical results."""
import io
import json
import os
import struct
import threading
import zipfile

import numpy as np
import pytest

from neuroscifi import archive, cli, graph_store, inputfiles
from neuroscifi.instance import datasets, store
from neuroscifi.instance.errors import ValidationError

T0 = 1_800_000_000.0


def nsf(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def npy_bytes(shape, descr="<f8", data=b""):
    """A .npy file written by hand: header text as numpy writes it, then `data` (independent of np.save)."""
    header = f"{{'descr': '{descr}', 'fortran_order': False, 'shape': {tuple(shape)!r}, }}"
    header += " " * (63 - (10 + len(header)) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin1") + data


def zip_bytes(members, method=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def individual(tmp_path, capsys):
    path = tmp_path / "cli.sqlite"
    store.create(path, T0).close()
    code, _, _ = nsf(capsys, "epoch", "start", "desk", "--db", path)
    assert code == 0
    return path


def streams(path):
    db = store.open_existing(path)
    try:
        return db.execute("SELECT rows, cols, sha256, blob FROM epoch_streams ORDER BY id").fetchall()
    finally:
        db.close()


# ---- 2. .npz: the header and the zip directory before numpy -------------------------------------------------------

HUGE = npy_bytes((1 << 22, 1 << 20), data=b"\0" * 64)        # declares 32 TiB of float64


def constant_npz() -> bytes:
    """np.savez_compressed of constant arrays (12.8 MB each, deflated to a few kB): honest data, not a bomb, since
    zipfile never inflates past the declared size and the declared size is within the accepted bound (code review
    L11; fix lane F1 ruling F removed the ratio rule that refused it)."""
    zeros = np.zeros((200_000, 8))
    buffer = io.BytesIO()
    np.savez_compressed(buffer, states=zeros, targets=zeros)
    return buffer.getvalue()


def hostile_npz():
    return {
        "huge_header.npz": zip_bytes({"states.npy": HUGE, "targets.npy": HUGE}),
        "bzip2.npz": zip_bytes({"states.npy": npy_bytes((8, 1), data=b"\0" * 64),
                                "targets.npy": npy_bytes((8, 1), data=b"\0" * 64)}, zipfile.ZIP_BZIP2),
        "many_members.npz": zip_bytes({f"m{i}.npy": b"" for i in range(5000)}),
        "short_data.npz": zip_bytes({"states.npy": npy_bytes((100_000, 100), data=b"\0" * 64),
                                     "targets.npy": npy_bytes((100_000, 100), data=b"\0" * 64)}),
        "long_header.npz": zip_bytes({"states.npy": b"\x93NUMPY\x02\x00" + struct.pack("<I", 1 << 30) + b"{",
                                      "targets.npy": b""}),
        "strings.npz": zip_bytes({"states.npy": npy_bytes((8, 1), "<U100000", b"\0" * 64),
                                  "targets.npy": npy_bytes((8, 1), data=b"\0" * 64)}),
    }


@pytest.mark.parametrize("name", sorted(hostile_npz()))
def test_epoch_stream_refuses_a_hostile_npz_before_numpy_allocates_or_decompresses(individual, tmp_path, capsys,
                                                                                    name):
    """Before: the crafted header was an uncaught MemoryError and the zero bomb was decompressed and stored."""
    (tmp_path / name).write_bytes(hostile_npz()[name])
    code, out, err = nsf(capsys, "epoch", "stream", "desk", tmp_path / name, "--db", individual)
    assert code == 2 and err.startswith("nsf: ") and "Traceback" not in err, (name, err)
    assert streams(individual) == []


def test_epoch_stream_refuses_a_fifo_without_blocking(individual, tmp_path, capsys):
    fifo = tmp_path / "stream.npz"
    os.mkfifo(fifo)
    result = {}
    worker = threading.Thread(target=lambda: result.update(code=cli.main(
        ["epoch", "stream", "desk", str(fifo), "--db", str(individual)])), daemon=True)
    worker.start()
    worker.join(10)
    if worker.is_alive():                                # unblock the old code's open() before failing
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(10)
        pytest.fail("the command blocked on a FIFO")
    assert result["code"] == 2 and "FIFO" in capsys.readouterr().err


def test_an_honest_npz_of_constant_data_is_not_refused(individual, tmp_path, capsys):
    (tmp_path / "zeros.npz").write_bytes(constant_npz())
    code, out, err = nsf(capsys, "epoch", "stream", "desk", tmp_path / "zeros.npz", "--db", individual)
    assert code == 0 and json.loads(out)["rows"] == 200_000, err


@pytest.mark.parametrize("save", [np.savez, np.savez_compressed])
def test_an_honest_npz_is_stored_exactly_as_before(individual, tmp_path, capsys, save):
    rng = np.random.default_rng(7)
    states, targets = rng.normal(size=(40, 5)), rng.normal(size=(40, 5)).astype(np.float32)
    save(tmp_path / "s.npz", states=states, targets=targets)
    code, out, _ = nsf(capsys, "epoch", "stream", "desk", tmp_path / "s.npz", "--db", individual)
    assert code == 0 and json.loads(out)["rows"] == 40
    expected = datasets._encode(np.asarray(states, dtype=np.float64), np.asarray(targets, dtype=np.float64))
    import hashlib
    assert streams(individual) == [(40, 5, hashlib.sha256(expected).hexdigest(), expected)]


def test_a_stored_blob_with_a_crafted_header_is_a_named_error_not_a_memory_error():
    """datasets.decode trusts a blob whose sha256 matches its record; an imported or edited file can bring both."""
    import hashlib
    blob = zip_bytes({"states.npy": HUGE, "targets.npy": HUGE})
    with pytest.raises(ValidationError, match="not a readable npz archive"):
        datasets.decode(blob, hashlib.sha256(blob).hexdigest(), 8, 1, "Dataset 1")
    honest = datasets._encode(np.ones((8, 2)), np.zeros((8, 2)))
    states, targets = datasets.decode(honest, hashlib.sha256(honest).hexdigest(), 8, 2, "Dataset 2")
    assert states.tolist() == np.ones((8, 2)).tolist() and targets.tolist() == np.zeros((8, 2)).tolist()


def test_the_header_reader_agrees_with_numpy_on_honest_files():
    for array in (np.zeros((3, 4)), np.arange(6, dtype="<i4").reshape(2, 3), np.ones(5, dtype=bool),
                  np.zeros((2, 2), dtype=np.complex128), np.zeros((0, 3), dtype=np.float32)):
        buffer = io.BytesIO()
        np.save(buffer, array)
        buffer.seek(0)
        shape, dtype, _ = inputfiles.npy_header(buffer)
        assert (shape, dtype) == (array.shape, array.dtype)
    with pytest.raises(inputfiles.InputRefused, match="not numeric"):
        inputfiles.npy_header(io.BytesIO(npy_bytes((3,), "|O")))


# ---- 3. whole-file reads: plain files only, bounded, named errors -------------------------------------------------

def blocks_or_fails(argv, fifo):
    """Run the CLI in a thread; a FIFO input must be refused at once (the old code blocked in open())."""
    result = {}
    worker = threading.Thread(target=lambda: result.update(code=cli.main([str(a) for a in argv])), daemon=True)
    worker.start()
    worker.join(10)
    if worker.is_alive():
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(10)
        pytest.fail(f"{argv[0]} blocked on a FIFO")
    return result.get("code")


def test_graphfile_and_archive_refuse_a_fifo(tmp_path, capsys):
    fifo = tmp_path / "in"
    os.mkfifo(fifo)
    for argv in (["graphfile", "unpack", fifo, tmp_path / "o1"], ["graphfile", "pack", fifo, tmp_path / "o2"],
                 ["archive", "pack", fifo, tmp_path / "o3"], ["archive", "unpack", fifo, tmp_path / "o4"]):
        assert blocks_or_fails(argv, fifo) == 2, argv
        assert "FIFO" in capsys.readouterr().err
    assert not any((tmp_path / f"o{i}").exists() for i in range(1, 5))


def test_graphfile_unpack_refuses_a_file_larger_than_any_snapshot_before_reading_it(tmp_path, capsys):
    big = tmp_path / "big.nsg"
    with open(big, "wb") as fh:
        fh.truncate(graph_store.MAX_BYTES + 1)     # sparse: no disk used
    code, _, err = nsf(capsys, "graphfile", "unpack", big, tmp_path / "out.npy")
    assert code == 2 and "more than" in err and not (tmp_path / "out.npy").exists()


@pytest.mark.parametrize("argv,data", [
    (["graphfile", "unpack"], b"not a graph"),
    (["graphfile", "pack"], npy_bytes((1 << 22, 1 << 20), data=b"\0" * 64)),
    (["graphfile", "pack"], npy_bytes((4, 4), "|O", b"\0" * 64)),
    (["graphfile", "pack"], b"junk"),
    (["archive", "unpack"], b"NSFA\x01\x00junk"),
    (["archive", "unpack"], b"nothing"),
])
def test_malformed_input_is_the_named_error_not_a_traceback(tmp_path, capsys, argv, data):
    source = tmp_path / "input"
    source.write_bytes(data)
    code, _, err = nsf(capsys, *argv, source, tmp_path / "output")
    assert code == 2 and err.startswith("nsf: ") and "Traceback" not in err
    assert not (tmp_path / "output").exists()


def test_init_and_rule_files_refuse_fifos_and_oversize_files(tmp_path, capsys):
    fifo = tmp_path / "graph.nsg"
    os.mkfifo(fifo)
    assert blocks_or_fails(["init", "--db", tmp_path / "a.sqlite", "--founding-graph", fifo], fifo) == 2
    assert not (tmp_path / "a.sqlite").exists()
    big = tmp_path / "big.nsg"
    with open(big, "wb") as fh:
        fh.truncate(graph_store.MAX_BYTES + 1)
    code, _, err = nsf(capsys, "init", "--db", tmp_path / "b.sqlite", "--founding-graph", big)
    assert code == 2 and "more than" in err and not (tmp_path / "b.sqlite").exists()
    path = tmp_path / "c.sqlite"
    store.create(path, T0).close()
    capsys.readouterr()
    rule = tmp_path / "rule.json"
    os.mkfifo(rule)
    assert blocks_or_fails(["graph", "rules", "add", rule, "--owner-approved", "--db", path], rule) == 2
    big_rule = tmp_path / "big.json"
    big_rule.write_bytes(b" " * ((1 << 20) + 1))
    code, _, err = nsf(capsys, "graph", "rules", "add", big_rule, "--owner-approved", "--db", path)
    assert code == 2 and "more than" in err


def test_honest_graph_and_archive_files_give_byte_identical_outputs(tmp_path, capsys):
    matrix = np.round(np.random.default_rng(3).random((20, 16)), 3).astype(np.float32)
    np.save(tmp_path / "g.npy", matrix)
    assert nsf(capsys, "graphfile", "pack", tmp_path / "g.npy", tmp_path / "g.nsg", "--bits", "8",
               "--degree", "4")[0] in (0, None)
    assert (tmp_path / "g.nsg").read_bytes() == graph_store.encode(matrix, 4, 8)
    assert nsf(capsys, "graphfile", "unpack", tmp_path / "g.nsg", tmp_path / "g2.npy")[0] in (0, None)
    expected = io.BytesIO()
    np.save(expected, graph_store.decode(graph_store.encode(matrix, 4, 8)), allow_pickle=False)
    assert (tmp_path / "g2.npy").read_bytes() == expected.getvalue()
    link = tmp_path / "link.npy"                         # a link to a plain file is followed, as before
    link.symlink_to(tmp_path / "g.npy")
    assert nsf(capsys, "graphfile", "pack", link, tmp_path / "g3.nsg", "--bits", "32")[0] in (0, None)
    assert (tmp_path / "g3.nsg").read_bytes() == graph_store.encode(matrix, 3, 32)
    data = bytes(range(256)) * 700
    (tmp_path / "d.bin").write_bytes(data)
    assert nsf(capsys, "archive", "pack", tmp_path / "d.bin", tmp_path / "d.nsfa")[0] in (0, None)
    direct = io.BytesIO()
    archive.pack(io.BytesIO(data), direct, archive.FAST)
    assert (tmp_path / "d.nsfa").read_bytes() == direct.getvalue()
    assert nsf(capsys, "archive", "unpack", tmp_path / "d.nsfa", tmp_path / "d2.bin")[0] in (0, None)
    assert (tmp_path / "d2.bin").read_bytes() == data
