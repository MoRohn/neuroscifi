"""Instance commands for `nsf`. Each workstream registers its commands from its own module.

Output is JSON on stdout. Instance errors exit with status 2 and a one-line message on stderr.
"""
from importlib import import_module
import json
import sys
import time

from . import store
from .errors import InstanceError

SUBMODULES = ["neuroscifi.instance.cli_evidence", "neuroscifi.instance.cli_graphs", "neuroscifi.instance.cli_memory"]


def emit(value):
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


def run(handler):
    """Wrap a handler(args) -> JSON-able value with uniform error handling."""
    def wrapped(args):
        try:
            return emit(handler(args))
        except InstanceError as error:
            print(f"nsf: {error}", file=sys.stderr)
            return 2
    return wrapped


def with_db(handler):
    """handler(db, args, now) on an existing instance; the connection is always closed."""
    def wrapped(args):
        db = store.open_existing(store.resolve_db(args))
        try:
            now = time.time()
            # Like the Pocket bridge, expire due evidence before every read or write so that
            # beliefs never count records past their lifetime (MEM-09).
            # `nsf sweep` itself is excluded so that it can report what it expired.
            if getattr(args, "command", None) != "sweep":
                from .governance import sweep
                sweep(db, now)
            return handler(db, args, now)
        finally:
            db.close()
    return run(wrapped)


def _init(args):
    semantics = json.loads(args.action_semantics) if args.action_semantics else None
    cfg = store.InstanceConfig(evidence_ttl_seconds=int(args.ttl_days * 86400),
                               ask_budget_per_epoch=args.ask_budget)
    founding = args.founding_graph.read_bytes() if args.founding_graph else None
    db = store.create(store.resolve_db(args), time.time(), action_semantics=semantics,
                      config=cfg, founding_graph=founding, note=args.note)
    try:
        return store.inventory(db)
    finally:
        db.close()


def register(sub):
    from pathlib import Path
    p = sub.add_parser("init", help="create a new individual (refuses an existing file)")
    store.add_db_argument(p)
    p.add_argument("--action-semantics", help='JSON like {"0":"turn left","1":"turn right"}')
    p.add_argument("--founding-graph", type=Path, help="NSG1 file registered as the founding graph")
    p.add_argument("--ttl-days", type=float, default=7.0, help="default lifetime of unpromoted evidence")
    p.add_argument("--ask-budget", type=int, default=3, help="questions per epoch before deferring")
    p.add_argument("--note")
    p.set_defaults(handler=run(_init))

    p = sub.add_parser("birth", help="birth record, memory inventory and development counters")
    store.add_db_argument(p)
    p.set_defaults(handler=with_db(lambda db, args, now: store.inventory(db)))

    p = sub.add_parser("regulation", help="write budget, backlog and pause state")
    store.add_db_argument(p)
    p.set_defaults(handler=with_db(lambda db, args, now: store.regulation(db, store.resolve_db(args))))

    for name in SUBMODULES:
        try:
            module = import_module(name)
        except ModuleNotFoundError as error:
            if error.name == name:
                continue
            raise
        module.register(sub, with_db=with_db, run=run)
