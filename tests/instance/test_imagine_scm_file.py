"""review1 S8: `nsf imagine scm FILE` reads its question bounded and without blocking, and a malformed question is a
one-line error: before, a FIFO blocked the command for ever, a large file was read whole, and a question with a wrong
shape ended in a Python traceback."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import neuroscifi

SRC = Path(neuroscifi.__file__).resolve().parents[1]           # the tree under test
GOOD = {"ask": "y", "do": {"x": 1.0}, "model": {"order": ["x", "y"], "edges": {"y": {"x": 2.0}},
                                                 "intercept": {"x": 0.0, "y": 0.0}}}


def scm(path, tmp_path):
    env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run([sys.executable, "-m", "neuroscifi.cli", "imagine", "scm", str(path)], capture_output=True,
                          text=True, timeout=60, env=env)


@pytest.mark.parametrize("question", [
    {"ask": "y", "do": {"x": 1.0}, "model": {"order": ["x", "y"], "edges": {"y": {"z": 2.0}}}},     # unknown parent
    {"ask": "y", "do": {"x": 1.0}, "model": {"order": 5, "edges": []}},                            # wrong types
    {"ask": "y", "do": [1, 2], "model": {"order": ["x", "y"], "edges": {"y": {"x": 2.0}}}},
    {"ask": "y", "model": "not a model"},
])
def test_a_malformed_question_is_a_one_line_error(tmp_path, question):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(question))
    out = scm(path, tmp_path)
    assert out.returncode != 0 and "Traceback" not in out.stderr and out.stderr.strip(), out.stderr


def test_too_deep_too_large_or_not_a_plain_file_is_refused_without_blocking(tmp_path):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 200_000)
    large = tmp_path / "large.json"
    large.write_text(" " * (2 << 20) + json.dumps(GOOD))
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    for path in (deep, large, fifo):
        out = scm(path, tmp_path)                    # a FIFO used to block here until the timeout
        assert out.returncode != 0 and "Traceback" not in out.stderr and out.stderr.strip(), (path, out.stderr)


def test_a_well_formed_question_is_answered(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(GOOD))
    link = tmp_path / "link.json"
    link.symlink_to(path)
    for where in (path, link):
        out = scm(where, tmp_path)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout)["rung"] == "interventional"
