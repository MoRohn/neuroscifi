"""`nsf archive` and `nsf graphfile`: byte-exact archives and graph snapshots.

Inputs are plain files only (a FIFO or a device is refused, not blocked on); an NSG1 snapshot is read only up to the
largest one the format allows, and a .npy file's header is checked before NumPy allocates the array. A malformed input
is reported as "nsf: <reason>" with exit code 2, like every other nsf command, instead of a traceback."""
import os
from pathlib import Path
import sys

import numpy as np

from . import archive, graph_store, inputfiles


def register(sub):
    arc = sub.add_parser("archive", help="NSFA byte-exact archives")
    arc.add_argument("op", choices=["pack", "unpack", "checkpoint"])
    arc.add_argument("source", type=Path)
    arc.add_argument("destination", type=Path)
    arc.add_argument("--policy", choices=["fast", "cold", "experimental"], default="fast")
    arc.set_defaults(handler=_named(_archive))
    gf = sub.add_parser("graphfile", help="NSG1 graph snapshots")
    gf.add_argument("mode", choices=["pack", "unpack"])
    gf.add_argument("input", type=Path)
    gf.add_argument("output", type=Path)
    gf.add_argument("--bits", type=int, choices=[4, 8, 32], default=8)
    gf.add_argument("--degree", type=int, default=3)
    gf.set_defaults(handler=_named(_graphfile))


def _named(handler):
    """A malformed input (ValueError, inputfiles.InputRefused included) -> "nsf: <reason>" and exit code 2."""
    def wrapped(args):
        try:
            return handler(args)
        except ValueError as error:
            print(f"nsf: {error}", file=sys.stderr)
            return 2
    return wrapped


def _archive(a):
    policy = {"fast": archive.FAST, "cold": archive.COLD, "experimental": archive.TRIAL}[a.policy]
    inputfiles.open_regular(a.source).close()
    archive.convert(a.op, a.source, a.destination, policy)


def _create_private(path):
    """New file, never overwritten, mode 0600 whatever the umask."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb")


def _graphfile(a):
    if a.mode == "pack":
        with inputfiles.open_regular(a.input) as fp:
            inputfiles.check_npy(fp, inputfiles.size_of(fp), max_elements=graph_store.MAX_ELEMENTS)
            matrix = np.load(fp, allow_pickle=False)
        result = graph_store.encode(matrix, a.degree, a.bits)
        with _create_private(a.output) as out:
            out.write(result)
    else:
        result = graph_store.decode(inputfiles.read_bounded(a.input, graph_store.MAX_BYTES, "graph snapshot"))
        with _create_private(a.output) as out:
            np.save(out, result, allow_pickle=False)
