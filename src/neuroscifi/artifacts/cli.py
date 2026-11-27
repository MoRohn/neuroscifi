"""`nsf artifact add FILE… | list | show ID [--why] | facts ID | export ID PATH | forget ID | gaps [--report] |
adapters | forms | make FILE_OR_ID… --request TEXT | remake ID --form F | --option k=v | relate ID ID |
feedback ID kept|dismissed|replaced:FORM | feedback --reset` (ADR-071; make, remake and --why: ADR-076;
relate: ADR-077, artifacts/relate_cli.py; feedback and the gap report: ADR-078).

The owner's commands for the artifact store (artifacts/store.py). Plain words by default; `--json` prints the record
instead. Nothing here runs unless the owner types it. `add` follows a symbolic link the owner names on the command
line (the owner chose that path) and takes regular files only. `export` never overwrites and writes mode 0600.
`forget` then rewrites the checkpoint archives it made stale, as `nsf forget` does.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from . import outcome
from .inspect_cli import _clean, inert_json
from .outcome import CANNOT_REASONS

HELP = ("files you hand over, kept beside this individual with what could be read from them, and what NeuroSciFi "
        "made (always marked: made by the system, not evidence)")


READS = {"content": "reads the content",
         "content or header": "reads the content of some variants and only the header of the others",
         "header": "reads only the header (sizes, layout, metadata): the content is not decoded here",
         "structure": "reads the container's structure and tracks: no frame or sample is decoded",
         "identity": "names the format: nothing in it is read",
         "nothing": "reads nothing"}


def _forms_catalog() -> dict:
    from . import check, make
    validators: dict[str, list[str]] = {}
    for row in check.catalog():
        validators.setdefault(row["media_type"], []).append(row["validator"])
    return {"generators": make.catalog(), "validators": validators}


def _forms(args) -> int:
    value = _forms_catalog()
    print(json.dumps(value, indent=2, sort_keys=True) if args.json else _plain("forms", value))
    return 0


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
    held = f"; {item['held']}" if item.get("held") else ""
    return (f"{item['id']}  {item['label']}  ({item['format']}, {_size(item['size'])}; {item['provenance']}"
            f"{state}{flag}{held})")


_NOT_RECOVERED = re.compile(r"\b(not|never|no|skipped|missing|stopped|cut|partial|without|unreadable|left out|"
                            r"refused|ignored|dropped|lost)\b", re.I)


def _note_label(note: str) -> str:
    """What a decode note is (ruling C of fix lane F1): "not recovered" when it says something was not read, kept
    out or stopped (the decoders' own wording), else "read" (a measurement of what was read)."""
    return "not recovered" if _NOT_RECOVERED.search(str(note)) else "read"


def _plain(what, value) -> str:
    if what == "add":
        lines = []
        for item in value["added"]:
            lines.append("Kept " + _line(item))
            error = (item.get("index") or {}).get("error")
            if error and error.get("header_only"):
                lines.append(f"  Only its header could be read here: {error.get('message')} "
                             "(`nsf artifact facts ID` lists what the header says).")
            elif error:
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
    if what == "show" and value.get("held"):
        return "\n".join([_line(value), f"  Nothing of it is read or shown until you decide: `nsf artifact forget "
                                         f"{value['id']}` removes it; `nsf artifact release {value['id']}` ends the "
                                         "hold if it does not name what you forgot."])
    if what == "show":
        lines = [_line(value), f"  kind: {(value['index'].get('representation') or {}).get('kind', 'not read')}",
                 f"  sha256: {value['sha256']}"]
        for note in (value["index"].get("notes") or [])[:8]:
            lines.append(f"  {_note_label(note)}: {note}")
        error = value["index"].get("error")
        if error:
            lines.append(f"  refused: {error.get('message')}" if error.get("reason") not in CANNOT_REASONS
                         else f"  not recovered: {error.get('message')}")
        for note in (value["index"].get("made_notes") or [])[:8]:
            lines.append(f"  made: {note}")
        checked = value["index"].get("check")
        if checked:
            lines.append("  independent check: " + ("passed" if checked.get("ok") else
                                                     "FAILED (" + ", ".join(checked.get("failed") or []) + ")"))
            for warning in checked.get("warnings") or []:
                lines.append(f"  warning: {warning}")
        from .relate_cli import CLAIM_LABEL, CLAIM_RELATIONS
        for link in value["links"]:
            claim = f"; {CLAIM_LABEL}" if link["relation"] in CLAIM_RELATIONS else ""
            lines.append(f"  link: {link['src']} {link['relation']} {link['dst_id']} ({link['how']}{claim})")
        if "why" in value:
            from .produce import why_text
            lines.append("Why it has this form:")
            lines.extend("  " + line for line in why_text(value["why"]).splitlines())
        return "\n".join(lines)
    if what in ("make", "remake"):
        from .produce import carried_words, used_part_lines, why_text
        record = value["record"]
        if value["status"] == "made":
            made = record.get("made") or {}
            check = record.get("check") or {}
            carried = check.get("carried")
            lines = [f"Made {value['artifact']}: {made.get('title')} ({made.get('form')}, {_size(made.get('bytes'))}; "
                     "made by the system, not evidence).",
                     *used_part_lines(record.get("sources")),
                     f"Why this form: {(record.get('plan') or {}).get('reason') or 'you named it.'}",
                     "Independent check: passed" + (carried_words(carried, record, "file") if carried is not None
                                                    else "") + "."]
            if value["sources_kept"]:
                lines.append(f"The {len(value['sources_kept'])} file(s) it was made from are kept too: "
                             + ", ".join(value["sources_kept"]) + ".")
            for kept in value["kept"]:
                lines.append(f"Also kept (an alternative): {kept}.")
            lines.append(f"`nsf artifact show {value['artifact']} --why` tells the whole story; "
                         f"`nsf artifact export {value['artifact']} FILE` writes it out.")
            return "\n".join(lines)
        if value["status"] == "words":
            return "\n".join(value["words"] + [record.get("why", "")])
        return why_text(record, short=True)
    if what == "facts":
        lines = [f"{value['id']} ({value['provenance']})"]
        if not value["decoded"] and (value.get("error") or {}).get("header_only"):
            lines.append("Only this format's header is read here; its content is not decoded "
                         f"({', '.join(value['error'].get('needs') or []) or 'no decoder'} would be needed).")
        elif not value["decoded"]:
            lines.append("Nothing here can read this format, so only its size is known.")
        for fact in value["facts"]:
            lines.append(f"  {fact['name']}: {_value_words(fact['value'])}  [{fact['how']}]")
        return "\n".join(lines)
    if what == "relate":
        from .relate_cli import plain
        return plain(value)
    if what == "release":
        if value["status"] == "not_held":
            return f"{value['id']} is not held: nothing changed."
        return (f"Released {value['id']}  {value['label']}: you stated that it does not name what you forgot, so it "
                "is read and shown again. The release is recorded and a restore keeps it.")
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
    if what == "gaps" and isinstance(value, dict):
        lines = ["What is missing most, from what is already recorded (read-only: nothing acts on this):"]
        for line in value["lines"]:
            evidence = f" [{', '.join(line['evidence'])}]" if line["evidence"] else ""
            lines.append(f"  {line['words']}{evidence}")
        return "\n".join(lines)
    if what == "feedback":
        if "outcomes_removed" in value:
            return (f"Removed {value['outcomes_removed']} recorded reaction(s): nothing learned from them is used any "
                    "more. A restore of an older checkpoint removes them from it too.")
        if not value["recorded"]:
            return f"Nothing was recorded: {value['why']}."
        named = f" (you wanted {value['by']})" if value["by"] else ""
        return (f"Recorded: you {value['outcome']} the {value['form']} made for {value['key'][0]} on the "
                f"{value['key'][2]}{named}; {value['why'].removeprefix('recorded; ')}.")
    if what == "gaps":
        if not value:
            return "Every file so far was read by an adapter here."
        return "\n".join(f"{g['format']}: {g['count']} time(s) {g['outcome']}" for g in value)
    if what == "adapters":
        lines = [f"Approved external adapters: {', '.join(value['approved']) or 'none'}",
                 "The file readers here (this list is the only statement of what can be read):"]
        for row in value["adapters"]:
            reads = READS.get(row.get("reads", "content"), row.get("reads", ""))
            state = (reads + ("" if row["reason"] == "ready" else f" ({row['reason']})")) if row["available"] \
                else "not available: " + row["reason"]
            lines.append(f"  {', '.join(row['formats']) or '-'}: {state} [{row['name']}, {row['source']}]")
        return "\n".join(lines)
    if what == "forms":
        lines = ["What can be made here (everything made is marked as made by the system and is never evidence):"]
        for row in value["generators"]:
            state = "ready" if row["available"] else "not available: " + row["reason"]
            lines.append(f"  {row['form']}: made by {row['generator']} version {row['version']}: {state}")
        if not value["generators"]:
            lines.append("  nothing: no generator is registered")
        lines.append("Checked after making, by an independent reader of the bytes:")
        for media, names in value["validators"].items():
            lines.append(f"  {media}: {', '.join(names)}")
        if not value["validators"]:
            lines.append("  nothing: no validator is registered, so nothing made here could pass a check")
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


def _feedback(db, args, now):
    from . import learn
    from .types import Malformed
    if args.reset:
        if args.id or args.reaction:
            raise Malformed("--reset clears every recorded reaction; give it alone")
        result = learn.reset(db, now)
        if result.get("checkpoints_stale"):
            from ..instance import checkpoints
            checkpoints.after_forget(db, now)
        return result
    if not args.id or not args.reaction:
        raise Malformed("give an artifact id and kept, dismissed or replaced:FORM (or --reset)")
    outcome, _, by = args.reaction.partition(":")
    if outcome not in learn.OWNER_OUTCOMES or (outcome == "replaced") != bool(by):
        raise Malformed("the reaction is kept, dismissed or replaced:FORM")
    return learn.record_outcome(db, args.id, outcome, by=by or None, now=now)


def _gaps(db, args, now):
    from . import learn, store
    return learn.gap_report(db) if args.report else store.gaps(db)


def _show(db, args, now):
    from . import store
    value = store.show(db, args.id)
    if getattr(args, "why", False):
        value["why"] = (value.get("index") or {}).get("production") or {}
    return value


def _context(args):
    from . import forms
    from .types import Malformed
    kwargs = {"device": args.device}
    if args.seconds is not None:
        kwargs["budget_s"] = float(args.seconds)
    if args.bytes is not None:
        kwargs["budget_bytes"] = int(args.bytes)
    try:
        return forms.Context(**kwargs)
    except Malformed:
        raise
    except (TypeError, ValueError) as error:
        raise Malformed(f"the budget is not a number ({error})") from None


def _make(db, args, now):
    """The production pipeline (artifacts/produce.py): files are held in memory and kept (as given) only when something
    is made from them; artifact ids are read from the store."""
    from . import produce, store
    from .types import Malformed
    sources = []
    for item in args.sources:
        text = str(item)
        if store._ID.fullmatch(text):
            sources.append(text)
            continue
        path = Path(text)
        cap = store.limits(_instance_config(db)).max_bytes
        data = store._read_file(str(path), min(cap, _instance_config(db).artifact_max_bytes), follow_symlinks=True)
        sources.append(produce.Given(path.name, data))
    if not args.request.strip():
        raise Malformed("say what you want to know or see (--request)")
    result = produce.produce(db, args.request, sources, form=args.form, context=_context(args),
                             options=produce.parse_options(args.option), alternatives=args.alternatives,
                             keep=args.keep, now=now, partial_ok=getattr(args, "partial", False))
    return {"status": result.status, "artifact": result.artifact, "kept": list(result.kept),
            "sources_kept": list(result.stored_sources), "words": list(result.words), "record": result.record}


def _remake(db, args, now):
    from . import produce
    result = produce.remake(db, args.id, form=args.form, options=produce.parse_options(args.option),
                            context=_context(args), now=now)
    return {"status": result.status, "artifact": result.artifact, "kept": [], "sources_kept": [],
            "words": list(result.words), "record": result.record}


def _instance_config(db):
    from ..instance import store as instance_store
    return instance_store.config(db)


def _relate(db, args, now):
    from .relate_cli import relate
    return relate(db, args.first, args.second, question=(args.question or None) and args.question[:1000], now=now)


def _list(db, args, now):
    from . import store
    store.reconcile(db, now)
    return store.listing(db, of_origin=args.origin, limit=args.limit)


def _value_words(value) -> str:
    """A fact's value in plain words: numbers to six significant digits, lists and records spelled out (no JSON)."""
    if isinstance(value, bool) or value is None:
        return {True: "yes", False: "no", None: "none"}[value]
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_value_words(v) for v in value[:64]) + (f", and {len(value) - 64} more" if len(value) > 64
                                                                    else "")
    if isinstance(value, dict):
        return "; ".join(f"{k} {_value_words(v)}" for k, v in list(value.items())[:64])
    return str(value)


def _command(what, handler):
    def run(args):
        from ..instance import store as instance_store
        from ..instance.errors import InstanceError, NotFound
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
        except NotFound as error:              # the id asked for is not held: about the input, not misuse
            print(f"nsf: {_clean(error)}", file=sys.stderr)
            return outcome.exit_code(outcome.CANNOT)
        except ArtifactError as error:
            print(f"nsf: {_clean(error)}", file=sys.stderr)
            return outcome.exit_code(outcome.of_error(error))
        except InstanceError as error:
            print(f"nsf: {_clean(error)}", file=sys.stderr)
            return outcome.MISUSE
        except sqlite3.Error as error:
            busy = "locked" in str(error) or "busy" in str(error)
            print("nsf: instance busy; retry" if busy else "nsf: the individual's file could not be read or written "
                  "(a database error)", file=sys.stderr)
            return 2
        finally:
            db.close()
        if args.json:
            print(inert_json(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)))
        else:
            print(_clean(_plain(what, value)))
        # the one exit-code table (artifacts/outcome.py): 0 done, 1 could not be done (input), 2 refused or misuse
        if what == "add" and value["refused"] and not value["added"]:
            return outcome.exit_code(outcome.REFUSED)
        if what == "feedback" and value.get("recorded") is False:
            return outcome.exit_code(outcome.CANNOT)
        if what in ("make", "remake") and value["status"] not in ("made", "words", "remade"):
            return outcome.exit_code(outcome.CANNOT)
        if what == "forget" and value.get("status") == "absent":
            return outcome.exit_code(outcome.CANNOT)
        return outcome.exit_code(outcome.ANSWERED)
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
                             "`nsf config --artifact-max-mib` sets); each is read once, in a separate process under "
                             "memory and time limits (about 0.1 s more)")
    q.add_argument("files", nargs="+", type=Path)
    q.add_argument("--name", help="the label to keep it under (one file only; default: the file's name)")
    q = command("list", _list, "what is kept, newest first; what the system made says so")
    q.add_argument("--origin", choices=store.ORIGINS)
    q.add_argument("--limit", type=int, default=100)
    q = command("show", _show, "one artifact: what was detected and read, its links and what was done to it")
    q.add_argument("id")
    q.add_argument("--why", action="store_true", help="for something the system made: what was asked, what the "
                                                      "sources were, which forms were considered, what the check found")
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
    q = command("release", lambda db, a, now: store.release(db, a.id, now),
                "end the hold on a kept file a forget could not check: you state that it does not name what you "
                "forgot (recorded; a restore keeps it)")
    q.add_argument("id")
    q = command("gaps", _gaps, "which formats nothing here could read, and how often")
    q.add_argument("--report", action="store_true",
                   help="everything recorded that shows what is missing most (unread formats, failed checks, "
                        "recurring repairs, words where a form would carry more, inconsistent relations), each with "
                        "its count and evidence ids; read-only")
    q = command("feedback", _feedback, "tell NeuroSciFi what you did with a form it made (kept, dismissed, or replaced "
                                       "by the form you wanted), so it can choose better; needs `nsf config "
                                       "--form-learning record` or `apply`")
    q.add_argument("id", nargs="?", help="the made artifact's id")
    q.add_argument("reaction", nargs="?", metavar="kept|dismissed|replaced:FORM")
    q.add_argument("--reset", action="store_true", help="remove every recorded reaction (what was learned is gone)")
    q = command("relate", _relate, "relate two artifacts by measuring both: what one states or shows against what "
                                   "the other measures; records the result as the system's claim (not evidence)")
    q.add_argument("first")
    q.add_argument("second")
    q.add_argument("--question", help="what to compare, in your words (e.g. \"does the total match the CSV?\")")
    command("adapters", lambda db, a, now: store.adapter_catalog(db), "the file readers here, and which external "
                                                                       "ones you approved")
    def budget(q):
        from . import forms
        q.add_argument("--form", help="the form you want (a planner form such as line_chart or table, a plain word "
                                      "such as chart, or a generator form such as solid.mesh); default: decided for you")
        q.add_argument("--option", action="append", metavar="KEY=VALUE", help="an option of the made form's "
                                                                              "specification (repeatable)")
        q.add_argument("--device", choices=forms.DEVICES, default="console", help="where it will be looked at")
        q.add_argument("--seconds", type=float, help="the most time it may take (default 60)")
        q.add_argument("--bytes", type=int, help="the most bytes the made file may have (default 16 MiB)")

    q = command("make", _make, "make something from files you hand over (or kept artifacts) for what you want to "
                               "know: the form is decided for you, made, checked independently and kept with why; "
                               "several files are also compared with each other")
    q.add_argument("sources", nargs="+", help="files, or ids of kept artifacts")
    q.add_argument("--request", "-r", required=True, help="what you want to know or see, in your own words")
    q.add_argument("--alternatives", type=int, default=1, help="try up to N forms that differ in kind (at most 4) "
                                                               "and keep the best")
    q.add_argument("--keep", action="store_true", help="keep the alternatives that passed their check too")
    q.add_argument("--partial", action="store_true", help="use a file that could be read only in part (a limit "
                                                          "stopped its reading); what is made says exactly which part "
                                                          "was read. Without it, such a file is not used")
    budget(q)
    q = command("remake", _remake, "make something the system made again from its kept specification, in another "
                                   "form or with changed options")
    q.add_argument("id")
    budget(q)
    q = asub.add_parser("forms", help="what can be made here and which independent check reads it back (reads no "
                                      "individual)")
    q.add_argument("--json", action="store_true", help="print the catalog as JSON instead of plain words")
    q.set_defaults(handler=_forms)
