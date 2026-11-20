"""`nsf` instance commands turn input, file and database failures into one-line errors with exit status 2."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from neuroscifi.instance.cli import run

ROOT = Path(__file__).resolve().parents[2]


def invoke(error):
    def handler(args):
        raise error
    return run(handler)(None)


@pytest.mark.parametrize("error, expected", [
    (json.JSONDecodeError("Expecting value", "{", 1), "nsf: invalid JSON: Expecting value"),
    (FileNotFoundError(2, "No such file", "/x/missing"), "nsf: no such file: /x/missing"),
    (IsADirectoryError(21, "Is a directory", "/x"), "nsf: is a directory: /x"),
    (PermissionError(13, "Denied", "/x/secret"), "nsf: permission denied: /x/secret"),
    (sqlite3.OperationalError("database is locked"), "nsf: instance busy; retry"),
    (sqlite3.DatabaseError("file is not a database"), "nsf: database error: file is not a database"),
])
def test_run_maps_errors_to_one_line(error, expected, capsys):
    assert invoke(error) == 2
    captured = capsys.readouterr()
    assert captured.err.strip().startswith(expected) and captured.err.count("\n") == 1
    assert captured.out == ""


def nsf(tmp_path, *argv):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-m", "neuroscifi.cli", *argv], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=60)


def test_real_commands_have_no_tracebacks(tmp_path):
    db = tmp_path / "a.sqlite"
    cases = [
        (("init", "--db", str(db), "--action-semantics", "{not json"), "invalid JSON"),
        (("init", "--db", str(db), "--founding-graph", str(tmp_path / "nope.nsg")), "no such file"),
        (("init", "--db", str(db), "--founding-graph", str(tmp_path)), "is a directory"),
    ]
    for argv, text in cases:
        done = nsf(tmp_path, *argv)
        assert done.returncode == 2 and text in done.stderr and "Traceback" not in done.stderr, (argv, done.stderr)
    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"this is not a database" * 100)
    done = nsf(tmp_path, "birth", "--db", str(garbage))
    assert done.returncode == 2 and "Traceback" not in done.stderr and done.stderr.startswith("nsf: ")


def test_permission_denied_has_no_traceback(tmp_path):
    secret = tmp_path / "graph.nsg"
    secret.write_bytes(b"x")
    secret.chmod(0)
    done = nsf(tmp_path, "init", "--db", str(tmp_path / "b.sqlite"), "--founding-graph", str(secret))
    assert done.returncode == 2 and "permission denied" in done.stderr and "Traceback" not in done.stderr
