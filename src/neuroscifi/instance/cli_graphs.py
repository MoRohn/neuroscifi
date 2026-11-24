"""`nsf graph ...`: datasets, graph learning, the promotion gate, recall, lesions and owner rules."""
import json
import os
from pathlib import Path

from . import datasets, graphs, recall, rules, store
from .errors import ValidationError


def _rules_add(db, args, now):
    if not args.owner_approved:
        raise ValidationError("Adding a scoring rule requires --owner-approved; learners cannot change their exam")
    if (args.file is None) == (args.builtin is None):
        raise ValidationError("Give either a rule file or --builtin NAME (one of: "
                              + ", ".join(store.BUILTIN_RULES) + ")")
    if args.builtin is not None:
        # A rule that ships with the software, so adopting it (or going back to the earlier one) needs no file. Rules
        # are immutable and the newest is used, so a built-in that is already registered is added again under a new
        # name with --as.
        if args.builtin not in store.BUILTIN_RULES:
            raise ValidationError(f"Unknown built-in rule {args.builtin!r}; one of: " + ", ".join(store.BUILTIN_RULES))
        definition = dict(store.BUILTIN_RULES[args.builtin])
        if args.version is not None:
            definition["version"] = args.version
        registered = {r["version"] for r in rules.listing(db)}
        if definition["version"] in registered and rules.latest(db) != definition["version"]:
            raise ValidationError(
                f"Rule {definition['version']} is already registered and a newer rule is in use ({rules.latest(db)}). "
                f"Rules are never edited or reordered: add it under a new name, for example "
                f"--builtin {args.builtin} --as {args.builtin}-again")
    else:
        if args.version is not None:
            raise ValidationError("--as renames a built-in rule; a rule file carries its own version")
        try:
            definition = json.loads(args.file.read_text())
        except (OSError, ValueError) as error:
            raise ValidationError(f"Cannot read rule file {args.file}: {error}") from None
    with store.write(db):
        version = rules.register(db, definition, now, created_by="owner")
        store.bump_state_version(db)
    return {"version": version, "definition": rules.get(db, version), "latest": rules.latest(db)}


def register(sub, *, with_db, run):
    graph = sub.add_parser("graph", help="population datasets, executable graphs and their promotion gate")
    cmds = graph.add_subparsers(dest="graph_command", required=True, metavar="ACTION")

    def command(name, handler, help):
        p = cmds.add_parser(name, help=help)
        store.add_db_argument(p)
        p.set_defaults(handler=with_db(handler))
        return p

    p = command("simulate", lambda db, a, now: datasets.simulate(db, a.context, now, seed=a.seed, world=a.world,
                                                                 steps=a.steps, path=store.resolve_db(a)),
                "simulate train/validation/heldout trajectories of a graph_bench world")
    p.add_argument("context")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--world", choices=["a", "b"], default="a")
    p.add_argument("--steps", type=int, default=1536)

    p = command("datasets", lambda db, a, now: datasets.listing(db, a.context), "list datasets")
    p.add_argument("--context")

    p = command("learn", lambda db, a, now: {"revision_id": graphs.learn(
        db, a.context, now, train=a.train, validation=a.validation, holder=a.holder)},
        "learn a candidate graph (takes the context lease)")
    p.add_argument("context")
    p.add_argument("--train", type=int, required=True)
    p.add_argument("--validation", type=int, required=True)
    p.add_argument("--holder", default=f"cli:{os.getpid()}")

    p = command("evaluate", lambda db, a, now: graphs.evaluate(db, a.revision, now),
                "score a revision on independent held-out data")
    p.add_argument("revision", type=int)

    p = command("promote", lambda db, a, now: graphs.promote(db, a.revision, now),
                "run the promotion gate of the candidate's recorded rule")
    p.add_argument("revision", type=int)

    p = command("reject", lambda db, a, now: graphs.reject(db, a.revision, now, a.reason), "reject a candidate")
    p.add_argument("revision", type=int)
    p.add_argument("--reason", required=True)

    p = command("rollback", lambda db, a, now: graphs.rollback(db, a.context, a.revision, now),
                "re-activate an archived revision")
    p.add_argument("context")
    p.add_argument("revision", type=int)

    p = command("list", lambda db, a, now: graphs.listing(db, a.context), "list graph revisions")
    p.add_argument("--context")

    p = command("show", lambda db, a, now: graphs.show(db, a.revision), "one revision with its decoded matrix")
    p.add_argument("revision", type=int)

    p = command("lesion", lambda db, a, now: graphs.lesion(db, a.revision, a.dataset, now),
                "zero each retained edge and measure the change in error")
    p.add_argument("revision", type=int)
    p.add_argument("dataset", type=int)

    p = command("coactivation", lambda db, a, now: graphs.functional_map(db, a.dataset, now, lags=a.lags, top=a.top),
                "lagged coactivation of populations; propose routes missing from the active graph")
    p.add_argument("dataset", type=int)
    p.add_argument("--lags", type=int, nargs="+", default=list(graphs.COACTIVATION_LAGS))
    p.add_argument("--top", type=int, default=20)

    p = command("recall", lambda db, a, now: recall.run(db, a.dataset, now, contexts=a.context),
                "replay a dataset through the active graphs and pick a context")
    p.add_argument("dataset", type=int)
    p.add_argument("--context", nargs="+")

    p = command("export", lambda db, a, now: graphs.export(db, a.revision, a.out),
                "write a revision's NSG1 snapshot to a new file")
    p.add_argument("revision", type=int)
    p.add_argument("out", type=Path)

    rule_parser = cmds.add_parser("rules", help="immutable scoring rules")
    rule_cmds = rule_parser.add_subparsers(dest="rules_command", required=True, metavar="ACTION")
    p = rule_cmds.add_parser("list", help="every registered rule version")
    store.add_db_argument(p)
    p.set_defaults(handler=with_db(lambda db, a, now: rules.listing(db)))
    p = rule_cmds.add_parser("add", help="register a new rule version (owner only)")
    store.add_db_argument(p)
    p.add_argument("file", type=Path, nargs="?", help="a rule definition as JSON (or use --builtin)")
    p.add_argument("--builtin", metavar="NAME", help="a rule that ships with the software, no file needed: "
                   + ", ".join(store.BUILTIN_RULES) + ". New individuals are born with both, the last in use; "
                   "an older individual adopts best-learner-v1 with this")
    p.add_argument("--as", dest="version", metavar="VERSION",
                   help="register the built-in rule under this version name (needed when it is registered already, "
                        "for example to go back to gate-v1: --builtin gate-v1 --as gate-v1-again)")
    p.add_argument("--owner-approved", action="store_true", help="required: confirms the owner adds this rule")
    p.set_defaults(handler=with_db(_rules_add))
