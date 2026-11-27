"""Instance commands for `nsf`. Each workstream registers its commands from its own module.

Output is JSON on stdout. Instance, input-file, JSON and database errors exit with status 2 and a one-line message on stderr.
"""
from importlib import import_module
import json
import sqlite3
import sys
import time

from . import store
from .errors import InstanceError, ValidationError

SUBMODULES = ["neuroscifi.instance.cli_evidence", "neuroscifi.instance.cli_graphs", "neuroscifi.instance.cli_memory",
              "neuroscifi.instance.cli_imagined", "neuroscifi.instance.cli_imagine"]


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


def imagined_days(text):
    """argparse type: an imagined record's lifetime in days, from one minute to 365 days."""
    import argparse
    import math
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of days, got {text!r}") from None
    if not math.isfinite(value) or not 60 <= value * 86400 <= 365 * 86400:
        raise argparse.ArgumentTypeError(f"expected a number of days from one minute to 365, got {text}")
    return value


def unit_interval(text):
    """argparse type: a finite number from 0 to 1."""
    import argparse
    import math
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number from 0 to 1, got {text!r}") from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"expected a number from 0 to 1, got {text}")
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
        # `nsf config` and `nsf graph migration` open the file without the graph-rule migration, so that an owner can
        # opt out of it, or see what it would do, before anything runs it (graph_migration.py); `nsf quiet`, a
        # setting too, changes nothing else either.
        command = getattr(args, "command", None)
        adopt = command not in ("config", "quiet") and not (command == "graph" and
                                                            getattr(args, "graph_command", None) == "migration")
        db = store.open_existing(store.resolve_db(args), graph_migration=adopt)
        try:
            now = time.time()
            # Like the Pocket bridge, expire due evidence before every read or write so that
            # beliefs never count records past their lifetime (MEM-09).
            # `nsf sweep` itself is excluded so that it can report what it expired, and `nsf config` so that a
            # setting of the sweep (consolidation) takes effect before the next one, not after it.
            if getattr(args, "command", None) not in ("sweep", "config", "quiet"):
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
                               consolidation=args.consolidation,
                               max_consolidated_rows=args.max_consolidated_rows,
                               max_evidence_rows=args.max_evidence_rows,
                               min_free_bytes=int(args.min_free_mib * 1024 * 1024))
    founding = None
    if args.founding_graph:
        from .. import graph_store, inputfiles
        try:
            founding = inputfiles.read_bounded(args.founding_graph, graph_store.MAX_BYTES, "graph snapshot")
        except inputfiles.InputRefused as error:
            raise ValidationError(str(error)) from None
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
    if args.consolidation is not None:
        changes["consolidation"] = args.consolidation
    if args.max_consolidated_rows is not None:
        changes["max_consolidated_rows"] = args.max_consolidated_rows
    if args.graph_rule_migration is not None:
        changes["graph_rule_migration"] = args.graph_rule_migration
    if args.max_imagined_rows is not None:
        changes["max_imagined_rows"] = args.max_imagined_rows
    if args.imagined_lifetime_days is not None:
        changes["imagined_lifetime_seconds"] = args.imagined_lifetime_days * 86400
    if args.quiet is not None:
        changes["quiet"] = args.quiet
    for name in ("internal_state_history", "predictive_regulation", "executive_rule",     # ADR-051
                 "decision_information"):
        if getattr(args, name) is not None:
            changes[name] = getattr(args, name)
    for name in store.IMAGINE_SWITCHES:
        if getattr(args, name) is not None:
            changes[name] = getattr(args, name)
    if args.imagine_max_seconds is not None:
        changes["imagine_max_seconds"] = args.imagine_max_seconds
    if args.imagine_max_mib is not None:
        changes["imagine_max_bytes"] = args.imagine_max_mib * 1024 * 1024
    if args.broadcast is not None:
        changes["broadcast"] = args.broadcast
    if args.broadcast_threshold is not None:
        changes["broadcast_threshold"] = args.broadcast_threshold
    if args.artifact_max_mib is not None:
        changes["artifact_max_bytes"] = args.artifact_max_mib * 1024 * 1024
    if args.artifact_store_max_mib is not None:
        changes["artifact_store_max_bytes"] = args.artifact_store_max_mib * 1024 * 1024
    approval_notes = []
    if args.artifact_adapters is not None:
        # each reader is approved as installed now (entry-point name, distribution, version: security review N4);
        # a changed distribution needs approval again
        from ..artifacts import adapters
        entries = []
        for n in (p.strip() for p in args.artifact_adapters.split(",")):
            if not n:
                continue
            try:
                entries.append(n if "=" in n else adapters.approval_for(n))
            except LookupError as error:
                raise ValidationError(f"--artifact-adapters: {error}") from None
            name, _, rest = entries[-1].partition("=")
            dist, _, version = rest.partition("==")
            approval_notes.append(f"approved the file reader {name} from {dist} version {version}")
        changes["artifact_adapters"] = entries
    if args.form_learning is not None:
        changes["form_learning"] = args.form_learning
    if args.console_artifacts is not None:
        changes["console_artifacts"] = args.console_artifacts == "on"
    if args.console_artifact_upload is not None:
        changes["console_artifact_upload"] = args.console_artifact_upload == "on"
    from dataclasses import asdict
    out = store.set_config(db, changes, now) if changes else {"config": asdict(store.config(db)), "changed": []}
    repair_notes = []
    for item in args.repair_fixed or ():      # review4 M1: a damaged setting fixed at creation pauses writes
        name, _, text = item.partition("=")
        try:
            value = json.loads(text)
        except ValueError:
            raise ValidationError(f"--repair-fixed {name}: the value must be JSON (a number, or null)") from None
        repaired = store.repair_fixed(db, name.strip(), value, now)
        repair_notes += repaired["notes"]
        out = out | {"changed": out["changed"] + repaired["changed"], "config": asdict(store.config(db))}
    problems = store.setting_problems(db) + store.consolidation_warnings(db)
    from . import graph_migration
    record = graph_migration._record(db) if store.config(db).graph_rule_migration else None
    notes = list(repair_notes) + approval_notes
    if record is not None and record.get("status") == "reversed":
        # The setting only decides whether a migration not yet run may run; an undone one never runs again.
        notes.append("the graph-rule migration was undone (`nsf graph migration undo`); turning "
                     "--graph-rule-migration on does not run it again")
    return out | ({"warnings": problems} if problems else {}) | ({"notes": notes} if notes else {})


def _regulation(db, args, now):
    """`nsf regulation`; --precision adds executive.precision (ADR-054), computed only when asked."""
    out = store.regulation(db, store.resolve_db(args), measure=True)
    if getattr(args, "precision", False):
        from .executive import precision
        out["precision"] = precision(db)
    return out


def _self(db, args, now):
    """`nsf self`: the self-description record (self_description.build), or with --short its plain sentences."""
    from .. import self_description
    record = self_description.build(db, store.resolve_db(args), now)
    return {"short": self_description.short(record)} if args.short else record


def register(sub):
    import argparse
    from pathlib import Path
    p = sub.add_parser("init", help="create a new individual (refuses an existing file)")
    store.add_db_argument(p)
    p.add_argument("--action-semantics", help='JSON like {"0":"turn left","1":"turn right"}')
    p.add_argument("--founding-graph", type=Path, help="NSG1 file registered as the founding graph")
    p.add_argument("--ttl-days", type=float, default=7.0, help="default lifetime of unpromoted evidence")
    p.add_argument("--ask-budget", type=bounded_int(0, 1000), default=3, help="questions per epoch before deferring")
    p.add_argument("--ask-window-days", type=days_or_zero, default=7.0,
                   help="the ask budget counts questions of the last this many days (0 = the whole epoch)")
    p.add_argument("--consolidation", action=argparse.BooleanOptionalAction, default=True,
                   help="keep what an expired learning event taught as a consolidated reading (default: on)")
    p.add_argument("--max-consolidated-rows", type=bounded_int(0, store.MAX_ROW_LIMIT),
                   default=store.InstanceConfig.max_consolidated_rows,
                   help="the most consolidated readings kept; at the limit expiring events leave nothing (0 = no limit)")
    p.add_argument("--max-evidence-rows", type=int, default=store.InstanceConfig.max_evidence_rows,
                   help="regulation: pause new evidence writes at this journal size")
    p.add_argument("--min-free-mib", type=float, default=store.InstanceConfig.min_free_bytes / 1048576,
                   help="regulation: pause new evidence writes below this free space")
    p.add_argument("--note")
    p.set_defaults(handler=run(_init))

    p = sub.add_parser("config", help="instance settings: show them, or change the ones an owner may change")
    store.add_db_argument(p)
    p.add_argument("--repair-fixed", action="append", metavar="NAME=VALUE",
                   help="repair a DAMAGED setting fixed at creation (evidence_ttl_seconds, max_evidence_rows, "
                        "min_free_bytes, max_thermal_celsius); writes are paused until it is repaired. VALUE is JSON "
                        "(a number, or null for no thermal limit) that the setting's own check accepts; a sound fixed "
                        "setting cannot be changed. The value chosen at creation is recorded nowhere in the file once "
                        "its row is damaged (a checkpoint archive taken before holds it); the repair is logged")
    p.add_argument("--ask-budget", type=bounded_int(0, 1000), help="questions per epoch and window before deferring")
    p.add_argument("--ask-window-days", type=days_or_zero,
                   help="the ask budget counts questions of the last this many days (0 = the whole epoch)")
    p.add_argument("--consolidation", action=argparse.BooleanOptionalAction, default=None,
                   help="keep what an expired learning event taught as a consolidated reading (default: on); "
                        "--no-consolidation stops further consolidation, readings already kept stay until forgotten")
    p.add_argument("--max-consolidated-rows", type=bounded_int(0, store.MAX_ROW_LIMIT),
                   help="the most consolidated readings kept (0 = no limit); at the limit expiring learning events "
                        "leave nothing. Lowering it removes no reading: it only stops further consolidation")
    p.add_argument("--graph-rule-migration", action=argparse.BooleanOptionalAction, default=None,
                   help="at the next open, let an individual still on gate-v1 adopt best-learner-v1 and archive an "
                        "active graph worse than no graph (default: on; `nsf graph migration status` shows what it "
                        "would do). --no-graph-rule-migration before that open leaves the file as it is. It runs at "
                        "most once: after `nsf graph migration undo` turning it on again does nothing")
    p.add_argument("--max-imagined-rows", type=bounded_int(0, store.MAX_IMAGINED_ROWS),
                   help="the most imagined records kept (`nsf imagined list`; never evidence); the oldest goes first, "
                        "0 = keep none (default 1000)")
    p.add_argument("--imagined-lifetime-days", type=imagined_days,
                   help="how long an imagined record is kept, in days (at least one minute, at most 365 days; "
                        "default 7)")
    p.add_argument("--quiet", action=argparse.BooleanOptionalAction, default=None,
                   help="hold everything done unasked: background research, monitors, refreshes, idle "
                        "investigations (default: off; the same as `nsf quiet on|off`)")
    p.add_argument("--internal-state-history", action=argparse.BooleanOptionalAction, default=None,
                   help="the research worker keeps a 6-hour history of the internal-state record (numbers only; "
                        "`nsf internal-state`) (default: on)")
    p.add_argument("--predictive-regulation", action=argparse.BooleanOptionalAction, default=None,
                   help="new background research also waits while memory headroom is forecast to cross the critical "
                        "line, or a regular voice session is expected, within 3 minutes; owner work is never held "
                        "(default: off)")
    p.add_argument("--predictive-regulation-shadow", dest="predictive_regulation", action="store_const",
                   const="shadow", help="predictive regulation in shadow: nothing is held; what the forecast would "
                                        "have done is recorded and checked against what memory really did "
                                        "(`nsf internal-state --shadow-report`)")
    p.add_argument("--executive-rule", choices=store.RULE_VERSIONS, default=None,
                   help="the executive's rule version: rules-v1 (the fixed rule order) or information-v1 (asks the "
                        "most informative question); default rules-v1")
    p.add_argument("--decision-information", action=argparse.BooleanOptionalAction, default=None,
                   help="`nsf decide` also shows each question's expected information gain and whether the decision "
                        "rests on a matched earlier setup (display only; default: on)")
    for name, what in (("imagine_what_if", "`nsf imagine what-if` and `questions`: exact what-ifs on the beliefs "
                                           "(default: on)"),
                       ("imagine_forecast", "`nsf imagine forecast`: several steps ahead from an active graph "
                                            "(default: on)"),
                       ("imagine_retrospect", "`nsf imagine retrospect`: which reading most changed a belief "
                                              "(default: on)"),
                       ("imagine_scenarios", "scenario records for the Web Cortex's predictions, in the imagined "
                                             "store only, compared once settled (default: off)")):
        p.add_argument("--" + name.replace("_", "-"), dest=name, action=argparse.BooleanOptionalAction, default=None,
                       help=what)
    p.add_argument("--imagine-max-seconds", type=float,
                   help="the longest an imagination question may run on its private copy (0.1 to 600; default 10)")
    p.add_argument("--imagine-max-mib", type=bounded_int(1, 64 * 1024),
                   help="the largest individual file imagination may copy, in MiB (default 512)")
    p.add_argument("--broadcast", choices=("off", "log", "on"),
                   help="selective broadcast of findings (ADR-053): off = every finding reaches you as before "
                        "(default); log = as before, and each finding's salience is logged; on = only findings at or "
                        "above --broadcast-threshold become questions, the others stay in their task's record")
    p.add_argument("--broadcast-threshold", type=unit_interval,
                   help="the salience (0 to 1) a finding needs to be broadcast while --broadcast is on (default 0.5)")
    p.add_argument("--artifact-max-mib", type=bounded_int(1, 1024),
                   help="the largest file `nsf artifact add` keeps, in MiB (default 64); reading a file can take "
                        "several times its size in memory, so a larger value can make adding slower and heavier: "
                        "`nsf artifact make`, `facts` and `relate` read it in their own process, bounded at 256 MiB "
                        "+ 2 x the file + 512 MiB (896 MiB at 64, about 2.8 GiB at 1024; measured: 344 MiB at the "
                        "peak to make a chart from a 60 MiB CSV)")
    p.add_argument("--artifact-store-max-mib", type=bounded_int(0, 1024 * 1024),
                   help="the most the artifact store beside the individual may hold in all, in MiB (default 2048)")
    p.add_argument("--artifact-adapters", metavar="NAME,...",
                   help="external file readers you approve, by name, comma-separated (`nsf artifact adapters` lists "
                        "those found); each is approved as installed now (its package and version are recorded, and "
                        "a changed package needs approval again); an empty value approves none (the default)")
    p.add_argument("--form-learning", choices=store.FORM_LEARNING,
                   help="learning the form of a result from what you keep (ADR-078): off = nothing recorded "
                        "(default); record = your reactions to made forms are kept and change nothing; apply = what "
                        "was learned is also used when a form is chosen (`nsf form learned` shows it)")
    p.add_argument("--console-artifacts", choices=("on", "off"),
                   help="the owner console's \"Made and kept\" view of kept and made artifacts (ADR-079; default on)")
    p.add_argument("--console-artifact-upload", choices=("on", "off"),
                   help="let the owner console take new files (upload, ADR-079): a new way for bytes to enter, "
                        "so off by default")
    p.set_defaults(handler=with_db(_config))

    p = sub.add_parser("self", help="what this individual is doing, knows, is unsure of and can do next, each with "
                                    "its source and age (self-description; read-only)")
    store.add_db_argument(p)
    p.add_argument("--short", action="store_true", help="a few plain sentences instead of the full record")
    p.set_defaults(handler=with_db(_self))

    p = sub.add_parser("quiet", help="one switch for everything done unasked: on = nothing runs unless you ask "
                                     "(background research, monitors, refreshes); off = as your other settings say")
    store.add_db_argument(p)
    p.add_argument("quiet_action", nargs="?", default="status", choices=("on", "off", "status"))
    from .. import background
    p.set_defaults(handler=with_db(background.cli))

    p = sub.add_parser("birth", help="birth record, memory inventory and development counters")
    store.add_db_argument(p)
    p.set_defaults(handler=with_db(lambda db, args, now: store.inventory(db)))

    p = sub.add_parser("regulation", help="write budget, backlog and pause state")
    store.add_db_argument(p)
    p.add_argument("--precision", action="store_true",
                   help="also report how the executive's questions turned out (confirmed, rejected, expired) per "
                        "rule and per epoch: how often a change alarm was a change you confirmed (display only)")
    p.set_defaults(handler=with_db(_regulation))

    for name in SUBMODULES:
        try:
            module = import_module(name)
        except ModuleNotFoundError as error:
            if error.name == name:
                continue
            raise
        module.register(sub, with_db=with_db, run=run)
