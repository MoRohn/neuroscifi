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


def test_overflow_is_one_line(capsys):
    assert invoke(OverflowError("Python int too large to convert to SQLite INTEGER")) == 2
    assert capsys.readouterr().err.startswith("nsf: number out of range")


def _main(*argv):
    from neuroscifi.cli import main
    try:
        return main(list(argv))
    except SystemExit as exit_:     # argparse refusals
        return exit_.code


@pytest.mark.parametrize("argv, code, text", [
    (("voice", "settings", "--set", "{bad"), 2, "invalid JSON"),
    (("voice", "settings", "--set", "[1]"), 2, "JSON object"),
    (("voice", "settings", "--set", '{"bogus": 1}'), 2, "unknown voice setting"),
    (("voice", "forget", "nope"), 2, "voice session not found"),
    (("voice", "transcripts", "--search", "x" * 300), 2, "query must be"),
    (("voice", "transcripts", "--limit", "-1"), 2, "expected an integer"),
    (("voice", "transcripts", "--limit", "99999999999999999999999"), 2, "expected an integer"),
    (("knowledge", "hypotheses", "--limit", "99999999999999999999999"), 2, "expected an integer"),
    (("knowledge", "calibration", "--limit", "-5"), 2, "expected an integer"),
    (("knowledge", "hypothesize", "water boils at 100C", "--deadline-days", "inf"), 2, "number of days"),
    (("knowledge", "hypothesize", "water boils at 100C", "--deadline-days", "1e300"), 2, "number of days"),
    (("knowledge", "evolution", "--since-days", "inf"), 2, "number of days"),
    (("knowledge", "graph", "knowledge:abc"), 2, "no knowledge abc"),
    (("knowledge", "adapt", "request", "--request-json", "[1,2]"), 2, "JSON object"),
    (("llm", "ask", "  "), 2, "must not be blank"),
    (("llm", "ask", "hello", "--tools"), 2, "tools are switched off"),
    (("llm", "models"), 2, "not_configured"),
    (("llm", "usage", "--limit", "-1"), 2, "expected an integer"),
    (("web", "autonomy", "set", "--policy-json", '{"autonomous_daily": {"searches": Infinity}}'), 2, "non-negative"),
    (("web", "autonomy", "status", "--policy-json", '{"x": 1}'), 2, "only to `set`"),
    (("web", "node", "revoke", "ghost"), 2, "no enrolled node"),
    (("web", "node", "list", "x"), 2, "takes no LABEL"),
])
def test_cli_failures_are_one_line_with_a_non_zero_exit(tmp_path, capsys, argv, code, text):
    """T5-C01, C04, C05, M03: tracebacks, overflow, negative limits accepted, failures that exited 0."""
    db = tmp_path / "i.sqlite"
    assert _main("init", "--db", str(db)) == 0
    capsys.readouterr()
    assert _main(*argv, "--db", str(db)) == code
    err = capsys.readouterr().err
    assert text in err and "Traceback" not in err, err


def test_cli_voice_on_a_missing_or_broken_instance_is_one_line(tmp_path, capsys):
    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"not a database" * 100)
    for action in ("health", "settings", "transcripts", "sweep"):
        for db in (tmp_path / "none.sqlite", garbage):
            assert _main("voice", action, "--db", str(db)) == 2
            err = capsys.readouterr().err
            assert err.startswith("nsf: ") and err.count("\n") == 1, err


def test_cli_llm_ask_without_an_answer_exits_1_with_its_report(tmp_path, capsys):
    db = tmp_path / "i.sqlite"
    assert _main("init", "--db", str(db)) == 0
    capsys.readouterr()
    assert _main("llm", "ask", "hello", "--db", str(db)) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["generated_by"] == "none" and out["state"] == "not_configured"
