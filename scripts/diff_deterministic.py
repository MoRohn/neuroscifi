#!/usr/bin/env python3
"""List the leaves where two offline research benchmark outputs differ, with both values.

  scripts/diff_deterministic.py A B

A and B are files written by `scripts/bench_wave1.py offline --deterministic-out FILE` (or that command's `--out`
JSON, whose "deterministic" member is used). Each line: the leaf's path, A's value, B's value. Exit status 0 when
the two are equal, 1 when any leaf differs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def load(path):
    tree = json.loads(Path(path).read_text())
    return tree["deterministic"] if isinstance(tree, dict) and "sha256" in tree and "deterministic" in tree else tree


def leaves(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                yield sub, a.get(key, "<absent>"), b.get(key, "<absent>")
            else:
                yield from leaves(a[key], b[key], sub)
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a) or i >= len(b):
                yield f"{path}[{i}]", a[i] if i < len(a) else "<absent>", b[i] if i < len(b) else "<absent>"
            else:
                yield from leaves(a[i], b[i], f"{path}[{i}]")
    elif a != b or type(a) is not type(b):
        yield path, a, b


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.stderr.write(__doc__)
        return 2
    a, b = load(argv[0]), load(argv[1])
    for name, tree in zip(argv, (a, b)):
        digest = hashlib.sha256(json.dumps(tree, sort_keys=True, default=str).encode()).hexdigest()
        print(f"# {name}: sha256 {digest}")
    differing = 0
    for path, x, y in leaves(a, b):
        differing += 1
        print(f"{path}\n  A: {json.dumps(x)}\n  B: {json.dumps(y)}")
    print(f"# {differing} differing leaves")
    return 1 if differing else 0


if __name__ == "__main__":
    sys.exit(main())
