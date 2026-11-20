"""Output files of `nsf archive` and `nsf graphfile` are private (0600) whatever the umask."""
import os
from pathlib import Path
import stat

import numpy as np
import pytest

from neuroscifi import archive
from neuroscifi.cli import build_parser


@pytest.fixture
def umask022():
    old = os.umask(0o022)
    yield
    os.umask(old)


def run(*argv):
    args = build_parser().parse_args(list(argv))
    return args.handler(args)


def mode(path):
    return stat.S_IMODE(Path(path).stat().st_mode)


def test_archive_outputs_are_private(tmp_path, umask022):
    source = tmp_path / "data.bin"
    source.write_bytes(bytes(range(256)) * 20)
    packed, restored = tmp_path / "data.nsfa", tmp_path / "restored.bin"
    run("archive", "pack", str(source), str(packed))
    run("archive", "unpack", str(packed), str(restored))
    assert restored.read_bytes() == source.read_bytes()
    assert mode(packed) == 0o600 and mode(restored) == 0o600


def test_checkpoint_output_is_private(tmp_path, umask022):
    import sqlite3
    live = tmp_path / "live.sqlite"
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE t(x)")
    out = tmp_path / "snap.nsfa"
    run("archive", "checkpoint", str(live), str(out))
    assert mode(out) == 0o600


def test_archive_never_replaces_an_existing_file(tmp_path, umask022):
    source = tmp_path / "a"
    source.write_bytes(b"abc")
    target = tmp_path / "b"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        archive.convert("pack", source, target)
    assert target.read_bytes() == b"keep"


def test_graphfile_outputs_are_private(tmp_path, umask022):
    matrix = np.round(np.random.default_rng(1).random((12, 12)), 3).astype(np.float32)
    np.fill_diagonal(matrix, 0)
    source, packed, restored = tmp_path / "g.npy", tmp_path / "g.nsg", tmp_path / "g2.npy"
    np.save(source, matrix)
    run("graphfile", "pack", str(source), str(packed), "--bits", "32")
    run("graphfile", "unpack", str(packed), str(restored))
    assert mode(packed) == 0o600 and mode(restored) == 0o600
    with pytest.raises(FileExistsError):
        run("graphfile", "unpack", str(packed), str(restored))


def test_graphfile_refuses_a_symlink_output(tmp_path, umask022):
    matrix = np.zeros((6, 6), dtype=np.float32)
    source = tmp_path / "g.npy"
    np.save(source, matrix)
    victim = tmp_path / "victim"
    link = tmp_path / "out.nsg"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        run("graphfile", "pack", str(source), str(link))
    assert not victim.exists()


def test_profile_device_cuda_without_cupy_exits_2(tmp_path):
    import subprocess
    import sys
    blocker = tmp_path / "cupy.py"
    blocker.write_text("raise ModuleNotFoundError(\"No module named 'cupy'\")\n")
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": f"{tmp_path}{os.pathsep}{root / 'src'}"}
    done = subprocess.run([sys.executable, "-m", "neuroscifi.lab.profile_device", "--device", "cuda"],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert done.stderr.strip() == ("CUDA mode requires CuPy for this driver; install the 'gpu' extra "
                                   "(pip install 'neuroscifi[gpu]')")
