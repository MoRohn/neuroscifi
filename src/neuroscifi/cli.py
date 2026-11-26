"""The `nsf` command. Sub-commands live in the modules listed in COMMAND_MODULES."""
import argparse
from importlib import import_module

from . import __version__

COMMAND_MODULES = [
    "neuroscifi.pocket.cli",
    "neuroscifi.lab.cli",
    "neuroscifi.files_cli",
    "neuroscifi.instance.cli",
    "neuroscifi.console.cli",
    "neuroscifi.acceptance.cli",
    "neuroscifi.webcortex.cli",
    "neuroscifi.webcortex.evolution",
    "neuroscifi.inference.cli",
    "neuroscifi.voice.cli",
    "neuroscifi.resources.cli",
    "neuroscifi.setup_cli",
    "neuroscifi.start_cli",
    "neuroscifi.artifacts.inspect_cli",
    "neuroscifi.artifacts.cli",
    "neuroscifi.artifacts.forms_cli",
]


def build_parser():
    parser = argparse.ArgumentParser(prog="nsf", description="NeuroSciFi command line.")
    parser.add_argument("--version", action="version", version=f"nsf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name in COMMAND_MODULES:
        try:
            module = import_module(name)
        except ModuleNotFoundError as error:
            if error.name == name or name.startswith(error.name + "."):
                continue
            raise
        module.register(sub)
    return parser


def main(argv=None):
    parser = build_parser()
    args, args.extra = parser.parse_known_args(argv)
    if args.extra and not getattr(args, "passthrough", False):
        parser.error("unrecognized arguments: " + " ".join(args.extra))
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
