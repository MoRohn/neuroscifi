"""Instance commands for `nsf`. Each workstream registers its commands from its own module.

Output is JSON on stdout. Instance, input-file, JSON and database errors exit with status 2 and a one-line message on stderr.
"""
from importlib import import_module
import json
import sqlite3
import sys
import time

from . import store
from .errors import InstanceError

SUBMODULES = ["neuroscifi.instance.cli_evidence", "neuroscifi.instance.cli_graphs", "neuroscifi.instance.cli_memory"]


def emit(value):
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


def _fail(message):
    print(f"nsf: {message}", file=sys.stderr)
    return 2


class Failed(Exception):
    """A command that failed but has a JSON report to print (stdout) and an exit status (default 1)."""
    def __init__(self, report, status=1):
        super().__init__(str(report)[:200])
        self.report, self.status = report, status


def bounded_int(low, high):
    """argparse type: an integer in [low, high] (a huge or negative --limit is refused, never a traceback)."""
    import argparse

    def parse(text):
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"expected an integer from {low} to {high}, got {text}")
        return value
    return parse


def days(text):
    """argparse type: a finite number of days, more than 0 and at most 36 500 (100 years)."""
    import argparse
    import math
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of days, got {text!r}") from None
    if not math.isfinite(value) or not 0 < value <= 36500:
        raise argparse.ArgumentTypeError(f"expected a number of days above 0 and at most 36500, got {text}")
    return value


def days_or_zero(text):
    """argparse type: a finite number of days from 0 to 36 500."""
    import argparse
    import math
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of days, got {text!r}") from None
    if not math.isfinite(value) or not 0 <= value <= 36500:
        raise argparse.ArgumentTypeError(f"expected a number of days from 0 to 36500, got {text}")
    return value


LIMIT = bounded_int(1, 100_000)


def run(handler):
    """Wrap a handler(args) -> JSON-able value with uniform error handling."""
    def wrapped(args):
        try:
            return emit(handler(args))
        except Failed as failed:
            emit(failed.report)
            return failed.status
        except InstanceError as error:
            print(f"nsf: {error}", file=sys.stderr)
            return 2
        except OverflowError as error:
            return _fail(f"number out of range: {error}")
        except json.JSONDecodeError as error:
            return _fail(f"invalid JSON: {error}")
        except FileNotFoundError as error:
            return _fail(f"no such file: {error.filename}")
        except IsADirectoryError as error:
            return _fail(f"is a directory: {error.filename}")
        except PermissionError as error:
            return _fail(f"permission denied: {error.filename}")
        except sqlite3.Error as error:
            busy = "locked" in str(error) or "busy" in str(error)
            return _fail("instance busy; retry" if busy else f"database error: {error}")
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
                               ask_budget_per_epoch=args.ask_budget,
                               ask_budget_window_seconds=args.ask_window_days * 86400,
                               max_evidence_rows=args.max_evidence_rows,
                               min_free_bytes=int(args.min_free_mib * 1024 * 1024))
    founding = args.founding_graph.read_bytes() if args.founding_graph else None
    db = store.create(store.resolve_db(args), time.time(), action_semantics=semantics,
                      config=cfg, founding_graph=founding, note=args.note)
    try:
        return store.inventory(db)
    finally:
        db.close()


def _config(db, args, now):
    """`nsf config`: the instance settings; with flags, change them first (store.set_config)."""
    changes = {}
    if args.ask_budget is not None:
        changes["ask_budget_per_epoch"] = args.ask_budget
    if args.ask_window_days is not None:
        changes["ask_budget_window_seconds"] = args.ask_window_days * 86400
    if changes:
        return store.set_config(db, changes)
    from dataclasses import asdict
    return {"config": asdict(store.config(db)), "changed": []}


def register(sub):
    from pathlib import Path
    p = sub.add_parser("init", help="create a new individual (refuses an existing file)")
    store.add_db_argument(p)
    p.add_argument("--action-semantics", help='JSON like {"0":"turn left","1":"turn right"}')
    p.add_argument("--founding-graph", type=Path, help="NSG1 file registered as the founding graph")
    p.add_argument("--ttl-days", type=float, default=7.0, help="default lifetime of unpromoted evidence")
    p.add_argument("--ask-budget", type=int, default=3, help="questions per epoch before deferring")
    p.add_argument("--ask-window-days", type=days_or_zero, default=7.0,
                   help="the ask budget counts questions of the last this many days (0 = the whole epoch)")
    p.add_argument("--max-evidence-rows", type=int, default=store.InstanceConfig.max_evidence_rows,
                   help="regulation: pause new evidence writes at this journal size")
    p.add_argument("--min-free-mib", type=float, default=store.InstanceConfig.min_free_bytes / 1048576,
                   help="regulation: pause new evidence writes below this free space")
    p.add_argument("--note")
    p.set_defaults(handler=run(_init))

    p = sub.add_parser("config", help="instance settings: show them, or change the ones an owner may change")
    store.add_db_argument(p)
    p.add_argument("--ask-budget", type=bounded_int(0, 1000), help="questions per epoch and window before deferring")
    p.add_argument("--ask-window-days", type=days_or_zero,
                   help="the ask budget counts questions of the last this many days (0 = the whole epoch)")
    p.set_defaults(handler=with_db(_config))

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
