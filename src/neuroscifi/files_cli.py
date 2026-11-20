"""`nsf archive` and `nsf graphfile`: byte-exact archives and graph snapshots."""
from pathlib import Path

import numpy as np

from . import archive, graph_store


def register(sub):
    arc = sub.add_parser("archive", help="NSFA byte-exact archives")
    arc.add_argument("op", choices=["pack", "unpack", "checkpoint"])
    arc.add_argument("source", type=Path)
    arc.add_argument("destination", type=Path)
    arc.add_argument("--policy", choices=["fast", "cold", "experimental"], default="fast")
    arc.set_defaults(handler=_archive)
    gf = sub.add_parser("graphfile", help="NSG1 graph snapshots")
    gf.add_argument("mode", choices=["pack", "unpack"])
    gf.add_argument("input", type=Path)
    gf.add_argument("output", type=Path)
    gf.add_argument("--bits", type=int, choices=[4, 8, 32], default=8)
    gf.add_argument("--degree", type=int, default=3)
    gf.set_defaults(handler=_graphfile)


def _archive(a):
    policy = {"fast": archive.FAST, "cold": archive.COLD, "experimental": archive.TRIAL}[a.policy]
    archive.convert(a.op, a.source, a.destination, policy)


def _graphfile(a):
    if a.mode == "pack":
        result = graph_store.encode(np.load(a.input, allow_pickle=False), a.degree, a.bits)
        with a.output.open("xb") as out:
            out.write(result)
    else:
        result = graph_store.decode(a.input.read_bytes())
        with a.output.open("xb") as out:
            np.save(out, result, allow_pickle=False)
