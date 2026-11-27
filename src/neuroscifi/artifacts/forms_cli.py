"""`nsf form plan`: print the form planner's plan for a request (ADR-073). Reads nothing but the files named; writes
nothing; makes nothing. A plan is made by the system and is not evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import forms
from .types import ArtifactError, ContentShape, Limits

MAX_SHAPE_JSON = 64 << 10
_FIELDS = {"kinds": tuple, "n_items": int, "n_series": int, "has_time": bool, "has_space": int,
           "has_parameters": bool, "duration_s": float, "text_chars": int}


def register(sub):
    form = sub.add_parser("form", help="decide the form of a result (plan only; nothing is made)")
    ops = form.add_subparsers(dest="form_op", required=True, metavar="OP")
    p = ops.add_parser("plan", help="print which form a result should take, and why")
    p.add_argument("--request", required=True, help="the words of the request")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--content", nargs="+", type=Path, metavar="FILE",
                       help="files whose content the result would present (decoded here, never stored)")
    group.add_argument("--shape", metavar="JSON",
                       help='the content\'s shape, e.g. {"kinds": ["series"], "n_items": 365, "has_time": true}, '
                            "or a list of them")
    p.add_argument("--device", choices=forms.DEVICES, default="console")
    p.add_argument("--accessibility", action="append", choices=forms.ACCESSIBILITY, default=[],
                   help="a need to respect (repeatable)")
    p.add_argument("--form", dest="explicit_form", help="a form the person named (a form name or a plain alias)")
    p.add_argument("--assume-available", action="store_true",
                   help="plan as if every generator existed here (to see the choice once they are installed)")
    p.add_argument("--alternatives", type=int, default=0, metavar="N",
                   help="also list up to N forms that differ in kind")
    p.add_argument("--json", action="store_true", help="print the whole plan as JSON")
    p.add_argument("--db", type=Path, default=None,
                   help="an individual whose learned form choices apply (only with `nsf config --form-learning "
                        "apply`; ADR-078); without it no individual is read")
    p.set_defaults(handler=_plan)
    from ..instance import store
    q = ops.add_parser("learned", help="what was learned from your reactions to made forms, per kind of content, "
                                       "purpose and device, and what it changes (ADR-078)")
    store.add_db_argument(q)
    q.add_argument("--json", action="store_true", help="print the record as JSON")
    q.set_defaults(handler=_learned)


def _fail(message: str) -> int:
    print(f"nsf form plan: {message}", file=sys.stderr)
    return 2


def shapes_from_json(text: str) -> tuple[ContentShape, ...]:
    """Parse --shape: one object or a list of objects with ContentShape's fields (missing counts are 0)."""
    if len(text.encode("utf-8")) > MAX_SHAPE_JSON:
        raise ValueError("the shape is longer than 64 KiB")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"the shape is not JSON ({error.msg})") from None
    items = value if isinstance(value, list) else [value]
    if not items or len(items) > 64 or not all(isinstance(i, dict) for i in items):
        raise ValueError("the shape is an object or a list of 1 to 64 objects")
    shapes = []
    for item in items:
        unknown = sorted(set(item) - set(_FIELDS))
        if unknown:
            raise ValueError(f"unknown shape field(s): {', '.join(unknown[:5])}")
        kinds = item.get("kinds", [])
        if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds) or len(kinds) > 18:
            raise ValueError("kinds is a list of content kinds")
        args = {"kinds": tuple(kinds)}
        for name, kind in _FIELDS.items():
            if name == "kinds":
                continue
            raw = item.get(name, kind())
            if kind is bool:
                if not isinstance(raw, bool):
                    raise ValueError(f"{name} is true or false")
            elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{name} is a number")
            args[name] = kind(raw)
        shapes.append(ContentShape(**args))
    return tuple(shapes)


def shapes_from_files(paths, limits: Limits = Limits()) -> tuple[tuple[ContentShape, ...], list[str]]:
    """The shapes the planner is handed for these files: each file's decoded representation's `shape()`, and for an
    archive, workbook or database also its decoded members' shapes (`shapes_b1.content_shapes`). A file only
    identified (nothing decodes it, or only its header is read) adds an empty shape and a note."""
    from . import adapters
    from .safe import read_file
    from .shapes_b1 import content_shapes
    from .types import HeaderOnly
    shapes, notes = [], []
    for path in paths:
        try:
            data = read_file(path, limits.max_bytes)
        except ArtifactError as error:           # not there, not a regular file, too large: named, as before
            raise ValueError(f"cannot read a file ({path.name}: {error})") from None
        try:
            decoded = adapters.decode(data, name=path.name, limits=limits)
        except HeaderOnly as error:
            notes.append(f"{path.name}: only its header could be read ({error.facts.get('format', 'its format')}); "
                         "planned as words")
            shapes.append(ContentShape((), 0, 0, False, 0, False, 0.0, len(error.words)))
            continue
        found = content_shapes(decoded.representation)
        if not found[0].kinds:
            notes.append(f"{path.name}: nothing here could decode it ({decoded.detected.format}); planned as words")
        shapes.extend(found)
    return tuple(shapes), notes


def _plan(a) -> int:
    notes: list[str] = []
    try:
        if a.shape:
            content = shapes_from_json(a.shape)
        elif a.content:
            content, notes = shapes_from_files(a.content)
        else:
            content = ()
        generators = tuple(f.generator for f in forms.FORMS.values() if f.generator) if a.assume_available \
            else None
        context = forms.Context(device=a.device, accessibility=tuple(a.accessibility), generators=generators)
        request = forms.Request(a.request, explicit_form=a.explicit_form)
        learned = None
        if a.db is not None:
            result, learned = _learned_plan(a.db, request, content, context)
        else:
            result = forms.plan(request, content, context)
    except (ArtifactError, ValueError, OSError) as error:
        return _fail(str(error) if not isinstance(error, OSError) else f"cannot read a file ({error.strerror})")
    decided = learned.decided if learned is not None else None
    others = forms.alternatives(result, a.alternatives + 1)[1:] if a.alternatives > 0 else ()
    if a.json:
        out = result.to_dict()
        out["input_notes"] = notes
        out["assumed_available"] = bool(a.assume_available)
        if learned is not None:
            out["learned"] = {"status": learned.status, "decided": decided}
        if a.alternatives > 0:
            out["alternatives"] = [list(c.forms) for c in others]
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    for note in notes:
        print(f"Note: {note}")
    if a.assume_available:
        print("Note: planned as if every generator existed here.")
    print(forms.describe(result))
    if decided:
        print(f"Learned: {decided}")
    if others:
        print("Different kinds of form that would also work:")
        for cand in others:
            print(f"  {forms.label_of(cand.forms)} ({cand.total:.2f})")
    return 0


def _learned_plan(path, request, content, context):
    """The plan through what this individual learned (learn.plan); a named failure when the file cannot be opened."""
    import sqlite3
    from ..instance import store
    from ..instance.errors import InstanceError
    from . import learn
    try:
        db = store.open_existing(path)
    except (InstanceError, sqlite3.Error) as error:
        raise ValueError(f"cannot open the individual ({error})") from None
    try:
        return learn.plan(db, request, content, context)
    finally:
        db.close()


_MODE_WORDS = {"off": "Form learning is off: nothing is recorded and nothing learned is used "
                      "(`nsf config --form-learning record` or `apply`).",
               "record": "Form learning records your reactions and uses nothing learned "
                         "(`nsf config --form-learning apply` uses it).",
               "apply": "Form learning is on: what was learned is used when a form is chosen."}


def _learned(a) -> int:
    import sqlite3
    from ..instance import store
    from ..instance.errors import InstanceError
    from . import learn
    try:
        db = store.open_existing(store.resolve_db(a))
    except (InstanceError, sqlite3.Error) as error:
        print(f"nsf: {error}", file=sys.stderr)
        return 2
    try:
        value = learn.report(db)
    except (ArtifactError, InstanceError, sqlite3.Error) as error:
        print(f"nsf: {error}", file=sys.stderr)
        return 2
    finally:
        db.close()
    if a.json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    print(_MODE_WORDS[value["mode"]])
    print("What is learned here: " + value["what_is_learned"]["procedural_policy"] + ".")
    print("What is not: the examples (" + value["what_is_learned"]["stored_examples"] + "); model weights ("
          + value["what_is_learned"]["model_weights"] + ").")
    print(f"A kind of content acts on what was learned from {value['min_evidence']} reactions (a replacement counts "
          f"2). {value['rows']} reaction(s) and check(s) recorded (at most {value['max_rows']}).")
    if not value["keys"]:
        print("Nothing has been recorded yet.")
    for key in value["keys"]:
        print(f"  {key['words']}.")
    print("To undo: `nsf config --form-learning off` stops using it; `nsf artifact feedback --reset` removes it.")
    return 0
