"""`nsf form plan` (ADR-073): the plan and its reason in plain words; shapes from --shape or from files decoded by
the adapter registry; failures are named, never tracebacks."""
import json

import numpy as np
import pytest

from neuroscifi import cli
from neuroscifi.artifacts import adapters, make
from neuroscifi.artifacts import types as T

SERIES = '{"kinds": ["series"], "n_items": 365, "n_series": 1, "has_time": true}'


@pytest.fixture(autouse=True)
def no_generators(monkeypatch):
    monkeypatch.setattr(make, "_GENERATORS", {})


def run(capsys, *argv):
    code = cli.main(["form", "plan", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_plan_prints_the_form_and_the_reason(capsys):
    code, out, _ = run(capsys, "--request", "How did it change over the year?", "--shape", SERIES,
                       "--assume-available", "--alternatives", "2")
    assert code in (0, None)
    assert "Form: A line chart" in out
    assert "Why: A line chart, because there are 365 values in time order" in out
    assert "Decided by: the content itself" in out
    assert "Different kinds of form that would also work:" in out
    assert "never whether the content is true" in out


def test_without_generators_the_plan_says_what_is_missing(capsys):
    code, out, _ = run(capsys, "--request", "How did it change?", "--shape", SERIES)
    assert "Form: A sentence or two" in out and "nothing here can make a line chart yet" in out


def test_json_output_is_the_whole_plan(capsys):
    code, out, _ = run(capsys, "--request", "Read it aloud", "--shape", SERIES, "--device", "voice", "--json",
                       "--assume-available", "--alternatives", "3")
    plan = json.loads(out)
    assert plan["chosen"] == ["spoken"] and plan["decided_by"] == "explicit"
    assert "not evidence" in plan["label"] and plan["assumed_available"] is True
    assert {c["forms"][0] for c in plan["candidates"]} >= {"prose", "line_chart"}
    assert isinstance(plan["alternatives"], list)


def test_accessibility_and_named_form_options(capsys):
    _, out, _ = run(capsys, "--request", "", "--shape", SERIES, "--assume-available", "--accessibility",
                    "screen_reader", "--json")
    assert json.loads(out)["chosen"] == ["line_chart", "table"]
    _, out, _ = run(capsys, "--request", "", "--shape", SERIES, "--assume-available", "--form", "table", "--json")
    assert json.loads(out)["chosen"] == ["table"]


@pytest.mark.parametrize("shape, message", [
    ("not json", "not JSON"),
    ('{"kinds": ["series"], "colour": 3}', "unknown shape field"),
    ('{"kinds": ["vibes"]}', "unknown content kind"),
    ('{"kinds": ["series"], "n_items": "many"}', "is a number"),
    ('[]', "1 to 64 objects"),
    ('{"kinds": ["series"], "has_time": 1}', "true or false"),
])
def test_a_bad_shape_is_a_named_failure(capsys, shape, message):
    code, out, err = run(capsys, "--request", "x", "--shape", shape)
    assert code == 2 and message in err and "Traceback" not in err and out == ""


class CsvLike(adapters.Adapter):
    name = "test-csv"
    formats = ("csv", "text")   # the kernel detects this as text or csv
    families = ("table",)

    def decode(self, data, detected, limits):
        rows = data.decode().strip().splitlines()[1:]
        when = T.Column("t", "time", None, np.arange(len(rows), dtype=np.float64), 0)
        value = T.Column("v", "number", None, np.array([float(r.split(",")[1]) for r in rows]), 0)
        return T.Decoded(detected, T.Table((when, value), len(rows), None))


def test_content_files_are_decoded_into_shapes(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_ADAPTERS", [])
    adapters.register(CsvLike())
    data = tmp_path / "readings.csv"
    data.write_text("t,v\n" + "".join(f"{i},{i * 0.5}\n" for i in range(200)))
    unknown = tmp_path / "blob.bin"
    unknown.write_bytes(bytes(range(256)) * 4)
    code, out, _ = run(capsys, "--request", "How did it change?", "--content", str(data), "--assume-available")
    assert "Form: A line chart" in out and "200 values" in out
    code, out, _ = run(capsys, "--request", "What is it?", "--content", str(unknown), "--assume-available")
    assert "blob.bin: nothing here could decode it" in out and "Form: A sentence or two" in out


def test_a_missing_file_is_a_named_failure(capsys, tmp_path):
    code, out, err = run(capsys, "--request", "x", "--content", str(tmp_path / "absent.csv"))
    assert code == 2 and "cannot read a file" in err and "Traceback" not in err
