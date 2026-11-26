"""`nsf artifact add FILE… | list | show ID | facts ID | export ID PATH | forget ID | gaps | adapters` (ADR-071).

The owner's commands for the artifact store (artifacts/store.py). Plain words by default; `--json` prints the record
instead. Nothing here runs unless the owner types it. `add` follows a symbolic link the owner names on the command
line (the owner chose that path) and takes regular files only. `export` never overwrites and writes mode 0600.
`forget` then rewrites the checkpoint archives it made stale, as `nsf forget` does.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HELP = ("files you hand over, kept beside this individual with what could be read from them, and what NeuroSciFi "
        "made (always marked: made by the system, not evidence)")


def _size(n) -> str:
    n = float(n or 0)
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{int(n)} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def _line(item) -> str:
    flag = "; its text looked like instructions to an AI and is never shown to one" if item.get("flagged") else ""
    state = "" if item.get("state") == "stored" else f"; bytes {item.get('state')}"
    return (f"{item['id']}  {item['label']}  ({item['format']}, {_size(item['size'])}; {item['provenance']}"
            f"{state}{flag})")


def _plain(what, value) -> str:
    if what == "add":
        lines = []
        for item in value["added"]:
            lines.append("Kept " + _line(item))
            error = (item.get("index") or {}).get("error")
            if error:
                lines.append(f"  Could not read it: {error.get('message')}")
            elif (item.get("index") or {}).get("representation", {}).get("kind") == "opaque":
                lines.append("  Nothing here can read this format yet; what could be measured is kept "
                             "(`nsf artifact show ID`).")
        for item in value["refused"]:
            lines.append(f"Not kept: {item['file']}: {item['reason']}")
        return "\n".join(lines) or "Nothing was given."
    if what == "list":
        if not value["items"]:
            return "No artifacts are kept."
        counts = value["counts"]
        head = f"{counts['given']} given by you, {counts['made']} made by the system (not evidence)."
        return "\n".join([head] + [_line(i) for i in value["items"]])
    if what == "show":
        lines = [_line(value), f"  kind: {(value['index'].get('representation') or {}).get('kind', 'not read')}",
                 f"  sha256: {value['sha256']}"]
        for note in (value["index"].get("notes") or [])[:8]:
            lines.append(f"  not recovered: {note}")
        for link in value["links"]:
            lines.append(f"  link: {link['src']} {link['relation']} {link['dst_id']} ({link['how']})")
        return "\n".join(lines)
    if what == "facts":
        lines = [f"{value['id']} ({value['provenance']})"]
        if not value["decoded"]:
            lines.append("Nothing here can read this format, so only its size is known.")
        for fact in value["facts"]:
            lines.append(f"  {fact['name']}: {json.dumps(fact['value'], ensure_ascii=False)}  [{fact['how']}]")
        return "\n".join(lines)
    if what == "export":
        return f"Wrote {_size(value['bytes'])} to {value['path']} (only you can read it)."
    if what == "forget":
        if value["status"] == "absent":
            return "No artifact of that id is kept."
        effects = value["effects"]
        text = f"Forgotten: {effects.get('artifacts_removed', 0)} artifact(s), with what was made from them."
        for step in value.get("next_steps") or []:
            text += "\n" + step
        return text
    if what == "gaps":
        if not value:
            return "Every file so far was read by an adapter here."
        return "\n".join(f"{g['format']}: {g['count']} time(s) {g['outcome']}" for g in value)
    if what == "adapters":
        lines = [f"Approved external adapters: {', '.join(value['approved']) or 'none'}"]
        for row in value["adapters"]:
            state = "ready" if row["available"] else row["reason"]
            lines.append(f"  {row['name']} ({row['source']}): {', '.join(row['formats']) or '-'}: {state}")
        return "\n".join(lines)
    return json.dumps(value, indent=2, sort_keys=True)


def _add(db, args, now):
    from . import store
    from .types import Malformed
    if args.name is not None and len(args.files) != 1:
        raise Malformed("--name labels one file; give one file with it")
    out = {"added": [], "refused": []}
    for path in args.files:
        try:
            record = store.add(db, path, name=args.name if len(args.files) == 1 else None, follow_symlinks=True,
                               now=now)
            out["added"].append(record.to_dict())
        except store.StoreRefused as error:
            out["refused"].append({"file": str(path), "reason": str(error), "rule": error.rule})
        except Exception as error:
            from .types import ArtifactError
            if not isinstance(error, ArtifactError):
                raise
            out["refused"].append({"file": str(path), "reason": str(error), "rule": getattr(error, "rule", None)})
    return out


def _forget(db, args, now):
    from ..instance import checkpoints, governance
    from . import store
    result = store.forget_artifact(db, args.id, now)
    if not (result.get("effects") or {}).get("checkpoints_stale"):
        return result
    result = result | checkpoints.after_forget(db, now)
    result.pop("next_steps", None)
    if result["checkpoints_stale"]:
        result["next_steps"] = [governance.REBUILD_HINT.format(n=len(result["checkpoints_stale"]))]
    return result


def _list(db, args, now):
    from . import store
    store.reconcile(db, now)
    return store.listing(db, origin=args.origin, limit=args.limit)


def _command(what, handler):
    def run(args):
        from ..instance import store as instance_store
        from ..instance.errors import InstanceError
        from .types import ArtifactError
        try:
            db = instance_store.open_existing(instance_store.resolve_db(args))
        except (InstanceError, sqlite3.Error) as error:
            print(f"nsf: {error}", file=sys.stderr)
            return 2
        try:
            now = time.time()
            from ..instance.governance import sweep
            sweep(db, now)                         # as every `nsf` command on an individual does (MEM-09)
            value = handler(db, args, now)
        except (ArtifactError, InstanceError) as error:
            print(f"nsf: {error}", file=sys.stderr)
            return 2
        except sqlite3.Error as error:
            busy = "locked" in str(error) or "busy" in str(error)
            print("nsf: instance busy; retry" if busy else f"nsf: database error ({type(error).__name__})",
                  file=sys.stderr)
            return 2
        finally:
            db.close()
        if args.json:
            print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
        else:
            print(_plain(what, value))
        if what == "add" and value["refused"] and not value["added"]:
            return 1
        return 0
    return run


def register(sub):
    from ..instance import store as instance_store
    from . import store

    p = sub.add_parser("artifact", help=HELP, description=HELP + ".")
    asub = p.add_subparsers(dest="artifact_what", required=True)

    def command(name, handler, help_text):
        q = asub.add_parser(name, help=help_text)
        instance_store.add_db_argument(q)
        q.add_argument("--json", action="store_true", help="print the record as JSON instead of plain words")
        q.set_defaults(handler=_command(name, handler))
        return q

    q = command("add", _add, "keep one or more files you hand over (regular files only, each up to the limit "
                             "`nsf config --artifact-max-mib` sets)")
    q.add_argument("files", nargs="+", type=Path)
    q.add_argument("--name", help="the label to keep it under (one file only; default: the file's name)")
    q = command("list", _list, "what is kept, newest first; what the system made says so")
    q.add_argument("--origin", choices=store.ORIGINS)
    q.add_argument("--limit", type=int, default=100)
    q = command("show", lambda db, a, now: store.show(db, a.id), "one artifact: what was detected and read, its "
                                                                  "links and what was done to it")
    q.add_argument("id")
    q = command("facts", lambda db, a, now: store.facts(db, a.id), "measurements computed from what was read "
                                                                    "(counts, sizes, ranges, means), each with how")
    q.add_argument("id")
    q = command("export", lambda db, a, now: store.export(db, a.id, a.path, now=now),
                "write an artifact's bytes to a new file (never overwrites; only you can read it)")
    q.add_argument("id")
    q.add_argument("path", type=Path)
    q = command("forget", _forget, "forget an artifact, its bytes and everything made from it; checkpoint "
                                   "archives are rewritten without it")
    q.add_argument("id")
    command("gaps", lambda db, a, now: store.gaps(db), "which formats nothing here could read, and how often")
    command("adapters", lambda db, a, now: store.adapter_catalog(db), "the file readers here, and which external "
                                                                       "ones you approved")
