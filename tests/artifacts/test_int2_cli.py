"""Wave O-1 integration, item 6: one help for `nsf artifact`: every command is listed with one plain line, and the
catalogs the help points to answer without an individual."""
import argparse

from neuroscifi.cli import main

COMMANDS = ("add", "list", "show", "facts", "export", "forget", "gaps", "feedback", "relate", "adapters", "make",
            "remake", "forms")


def _artifact_parser():
    from neuroscifi import cli
    sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices["artifact"]


def test_every_artifact_command_is_listed_with_one_plain_line(capsys):
    try:
        main(["artifact", "--help"])
    except SystemExit as stop:
        assert stop.code == 0
    out = capsys.readouterr().out
    listed = out.split("positional arguments:")[1]
    for name in COMMANDS:
        assert f"\n    {name} " in listed, name
    sub = next(a for a in _artifact_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert set(sub.choices) == set(COMMANDS)
    helps = {c.dest: c.help for c in sub._choices_actions}
    assert set(helps) == set(COMMANDS)
    for name, text in helps.items():
        assert text and "\n" not in text and len(text) <= 260, name


def test_the_catalogs_reflect_every_lane(capsys, tmp_path):
    assert main(["artifact", "forms"]) == 0                        # reads no individual
    forms = capsys.readouterr().out
    assert "solid.mesh" in forms and "chart.line" in forms and "explorable.model" in forms
    path = tmp_path / "individual.sqlite"
    assert main(["init", "--db", str(path)]) == 0
    capsys.readouterr()
    assert main(["artifact", "adapters", "--db", str(path)]) == 0  # the owner's approved external readers too
    adapters = capsys.readouterr().out
    assert "model_file" in adapters
