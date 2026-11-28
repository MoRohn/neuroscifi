"""Fix lane F3, item 7 (ruling C, the rest): what the artifact commands print is plain words: no exception class
name, specification path, stage name, raw JSON or unformatted byte count."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from neuroscifi.artifacts import cli as acli, forms_cli, produce
from neuroscifi.artifacts.inspect_cli import _clean


def test_byte_counts_and_specification_paths_are_written_in_words():
    assert _clean("the decoded samples would need 313992000 bytes, over the limit of 268435456") == \
        "the decoded samples would need 299.4 MiB, over the limit of 256.0 MiB"
    assert _clean("ids 12345678 and 2026112700") == "ids 12345678 and 2026112700"         # not a byte count
    assert _clean("input of 313992000 bytes is over the limit of 268435456 bytes") == \
        "input of 299.4 MiB is over the limit of 256.0 MiB"
    assert _clean("the specification is not valid: $.options: unexpected property colour") == \
        "the specification is not valid: in options: unexpected property colour"
    assert _clean("$.data.values[3]: must be a number; $: missing title") == \
        "in data, values, item 4: must be a number; in the specification: missing title"
    assert _clean("a file of 512 bytes") == "a file of 512 bytes"


def test_a_fact_value_is_spelled_out_not_printed_as_json():
    assert acli._value_words(23.90780904502404) == "23.9078"
    assert acli._value_words({"std": 23.90780904502404, "n": 4}) == "std 23.9078; n 4"
    assert acli._value_words([1.0, 2.5, True, None]) == "1, 2.5, yes, none"
    assert acli._value_words("text") == "text"


def test_every_stage_a_production_can_stop_before_has_words():
    source = Path(produce.__file__).read_text()
    stages = set(re.findall(r'run\.step\("([a-z_]+)"', source))
    assert stages and stages <= set(produce.STAGE_WORDS), stages - set(produce.STAGE_WORDS)


def test_a_missing_file_is_named_in_words(tmp_path, capsys):
    from neuroscifi.cli import main
    from neuroscifi.instance import store
    path = tmp_path / "ind.sqlite"
    store.create(path, 1.0).close()
    code = main(["artifact", "add", "--db", str(path), str(tmp_path / "missing.csv")])
    out = capsys.readouterr()
    text = out.out + out.err
    assert code != 0 and "it does not exist" in text
    assert "FileNotFoundError" not in text and "Error" not in text


def test_form_plan_names_the_simplest_form_rule_when_it_decided(tmp_path, capsys):
    from neuroscifi.cli import main
    sales = tmp_path / "sales.csv"
    sales.write_text("region,sales\nnorth,120\nsouth,95\neast,143\nwest,88\ncentral,101\n")
    assert main(["form", "plan", "--request", "compare the sales by region", "--content", str(sales),
                 "--assume-available"]) == 0
    out = capsys.readouterr().out
    assert "Form: A bar chart" in out
    assert re.search(r"Why not the highest score: a bar chart with a table scored a little higher \(0\.\d+ against "
                     r"0\.\d+\), but when two forms are within 0\.03 of each other the simpler one is chosen\.", out)
    assert main(["form", "plan", "--request", "which region sold most", "--content", str(sales),
                 "--assume-available"]) == 0
    assert "Why not the highest score" not in capsys.readouterr().out     # the words rule decided, not the margin


@pytest.mark.parametrize("module", [acli, forms_cli])
def test_no_exception_class_name_is_formatted_into_plain_output(module):
    source = Path(module.__file__).read_text()
    assert "type(error).__name__" not in source and "type(e).__name__" not in source
