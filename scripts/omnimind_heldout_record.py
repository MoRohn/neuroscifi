#!/usr/bin/env python3
"""The committed record of OMNIMIND's held-out runs (docs/quality/evidence/omnimind_heldout/), checked, and what
"right" is made of.

    omnimind_heldout_record.py check [DIR]          # every value is an id, an outcome or a summary (exit 1 if not)
    omnimind_heldout_record.py breakdown [--out FILE]

`check` walks every JSON file in DIR (default the record) and refuses any string that is not an outcome word, a
hash or commit, a date, a version, the label or note the run wrote about itself, a held-out task id of the frozen
tables (as a key), or a word that the tracked case tables already hold: so no file bytes and no request text beyond
what `cases.py`, `forms_cases.py`, `cross_cases.py` and `learn_people.py` hold is in the record.

`breakdown` sorts every RIGHT task of lane Z's families into what kind of answer it is, for development (from
docs/quality/evidence/omnimind_int2_development.json: every task right but the ones it lists as not right) and for
held-out (from the record's per-task outcomes): a format identification (`detect.family`, `detect.format`), a
measurement read off decoded content (any other measure), a correct refusal or abstention (a task whose only right
answer is "cannot" or a refusal), a made artifact read back (`made`, `transform`), and a question where either a
value or an honest "cannot" is right (`cannot_or_value`: header facts of formats nothing here decodes). The task
kinds come from the frozen tables (the tasks are generated, never answered: no subject runs here).
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from neuroscifi.acceptance import omni  # noqa: E402

RECORD = REPO / "docs" / "quality" / "evidence" / "omnimind_heldout"
DEVELOPMENT = REPO / "docs" / "quality" / "evidence" / "omnimind_int2_development.json"
TABLES = ("cases.py", "forms_cases.py", "cross_cases.py", "learn_people.py")
Z = ("intake", "honest", "unknown", "compound", "hostile", "made", "transform")
OUTCOMES = {"right", "wrong", "abstained", "failed", ""}
SELF_DESCRIBED = {"label", "note", "taken", "subject", "benchmark"}       # what a run wrote about itself
PLAIN = re.compile(r"[0-9a-f]{7,64}|\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?|\d+\.\d+\.\d+|aarch64|x86_64|H\d")


def _table_text() -> str:
    return "\n".join((Path(omni.__file__).parent / name).read_text(encoding="utf-8") for name in TABLES)


def _heldout_ids(families) -> set[str]:
    return {t.id for f in families for t in omni.family(f).tasks("held_out")}


def check(directory: Path) -> list[str]:
    """Problems (empty when every file holds ids, outcomes and summaries only)."""
    tables, problems = _table_text(), []
    names = {"development", "held_out", "artifacts", "today", *Z, "forms", "cross", "learn"}
    files = sorted(Path(directory).glob("*.json"))
    loaded = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in files}
    id_families = sorted({k.split("/")[0] for d in loaded.values() for k in _keys(d) if k.count("/") >= 2})
    ids = _heldout_ids([f for f in id_families if f in Z])

    for name, data in loaded.items():
        def walk(value, key=""):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k.count("/") >= 2 and k not in ids:
                        problems.append(f"{name}: key {k[:60]!r} is not a held-out task id")
                    walk(v, k)
            elif isinstance(value, list):
                for v in value:
                    walk(v, key)
            elif isinstance(value, str):
                words = re.findall(r"'([^']*)'", value) if value.startswith("(") else [value]
                if key in SELF_DESCRIBED or value in OUTCOMES or value in names or PLAIN.fullmatch(value):
                    return
                if key == "git_commit" or all(w and (f'"{w}"' in tables or f"'{w}'" in tables) for w in words):
                    return
                problems.append(f"{name}: {key or 'value'} holds {value[:60]!r}, which no case table holds")
        walk(data)
    return problems


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def kind_of(task) -> str:
    if task.family in ("made", "transform"):
        return "made artifact read back"
    if task.kind in ("cannot", "refuse"):
        return "correct refusal or abstention"
    if task.kind == "cannot_or_value":
        return "a value or an honest cannot (either right)"
    measure = task.question.rsplit("::", 1)[-1]
    if measure in ("detect.family", "detect.format"):
        return "format identification"
    return "measurement read off decoded content"


def breakdown() -> dict:
    out = {"label": "OMNIMIND benchmark: what the artifacts subject's RIGHT answers are, by kind of answer "
                    "(scripts/omnimind_heldout_record.py breakdown)", "splits": {}}
    dev = json.loads(DEVELOPMENT.read_text(encoding="utf-8"))
    not_right = set(dev["not_right"]["tasks"])
    record = {f: json.loads((RECORD / f"{f}.json").read_text(encoding="utf-8")) for f in Z}
    sources = {"development": (f"{DEVELOPMENT.name} at {dev['commit']}", None),
               "held_out": ("omnimind_heldout/{" + ",".join(Z) + "}.json at " +
                            ", ".join(sorted({r["commit"] for r in record.values()})), record)}
    for split, (source, rec) in sources.items():
        rows, n, total_right = Counter(), Counter(), 0
        for f in Z:
            tasks = omni.family(f).tasks(split)
            if rec is None:
                right = [t for t in tasks if t.id not in not_right]
            else:
                outcome = rec[f]["splits"]["held_out"]["families"][f]["tasks"]
                right = [t for t in tasks if outcome[t.id] == "right"]
            for t in tasks:
                n[kind_of(t)] += 1
            for t in right:
                rows[kind_of(t)] += 1
            total_right += len(right)
        out["splits"][split] = {"source": source, "n": sum(n.values()), "right": total_right,
                                "right_by_kind": {k: {"right": rows[k], "of": n[k]} for k in sorted(n)}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("directory", nargs="?", default=str(RECORD))
    b = sub.add_parser("breakdown")
    b.add_argument("--out")
    a = ap.parse_args(argv)
    if a.command == "check":
        problems = check(Path(a.directory))
        for p in problems:
            print(p)
        files = sorted(Path(a.directory).glob("*.json"))
        print(f"{len(files)} files, {sum(p.stat().st_size for p in files)} bytes; problems: {len(problems)}")
        return 1 if problems else 0
    text = json.dumps(breakdown(), indent=1, sort_keys=True) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out} ({len(text.encode())} bytes)")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
