"""`nsf lab <analysis>` and `nsf reproduce`."""
import runpy
import subprocess
import sys
from pathlib import Path

ANALYSES = ["core", "sensory", "recalibrate", "source_groups", "graph_bench", "human_counts",
            "graph_compression_bench", "context_recall", "compression_bench", "profile_device"]


def register(sub):
    lab = sub.add_parser("lab", help="run a research analysis")
    names = lab.add_subparsers(dest="analysis", required=True, metavar="ANALYSIS")
    for name in ANALYSES:
        one = names.add_parser(name, add_help=False, help="python -m neuroscifi.lab." + name)
        one.set_defaults(handler=_run, module=name, passthrough=True)
    rep = sub.add_parser("reproduce", help="rebuild all deterministic evidence and compare")
    rep.set_defaults(handler=_reproduce)


def _run(a):
    sys.argv = ["neuroscifi.lab." + a.module, *a.extra]
    runpy.run_module("neuroscifi.lab." + a.module, run_name="__main__")


def _reproduce(a):
    for d in Path(__file__).resolve().parents:
        if (d / "pyproject.toml").is_file() and (d / "scripts" / "reproduce.py").is_file():
            return subprocess.call([sys.executable, str(d / "scripts" / "reproduce.py")])
    raise SystemExit("nsf reproduce needs a repository checkout containing scripts/reproduce.py")
