"""`nsf` commands for the evidence journal, beliefs and the executive (W2-A)."""
from pathlib import Path

from . import beliefs, evidence, executive, legacy, store


def _observe(db, args, now):
    obs = {"id": args.id, "epoch": args.epoch, "channel": args.channel, "action": args.action,
           "bit": args.bit, "root": args.root}
    if args.sample_id is not None:
        obs["sample_id"] = args.sample_id
    if args.actor is not None:
        obs["actor"] = args.actor
    if args.memory_only:
        obs["consent"] = "memory_only"
    if args.simulated:
        obs["provenance"] = "simulated"
    if args.acquired_at is not None:
        obs["acquired_at"] = args.acquired_at
    return evidence.observe(db, obs, now, path=store.resolve_db(args))


def register(sub, *, with_db, run):
    """Add epoch (start, match), observe, status, decide, explain, pending, resolve and import-legacy."""
    def command(name, handler, help, *arguments):
        parser = sub.add_parser(name, help=help)
        store.add_db_argument(parser)
        for args, kwargs in arguments:
            parser.add_argument(*args, **kwargs)
        parser.set_defaults(handler=with_db(handler))
        return parser

    epoch = sub.add_parser("epoch", help="calibration epochs")
    ops = epoch.add_subparsers(dest="epoch_command", required=True, metavar="OP")
    start = ops.add_parser("start", help="start (or reuse) a calibration epoch")
    store.add_db_argument(start)
    start.add_argument("name")
    start.add_argument("--note")
    start.set_defaults(handler=with_db(lambda db, a, now: evidence.start_epoch(db, a.name, now, a.note)))
    match = ops.add_parser("match", help="which earlier epoch does this epoch's recent evidence come from?")
    store.add_db_argument(match)
    match.add_argument("name")
    match.add_argument("--last", type=int, default=beliefs.MATCH_EVENTS, help="learning events to score")
    match.set_defaults(handler=with_db(lambda db, a, now: beliefs.match_epochs(db, a.name, last=a.last)))

    command("observe", _observe, "journal one observation",
            (("epoch",), {}), (("id",), {}), (("channel",), {"choices": evidence.CHANNELS}),
            (("action",), {"type": int, "choices": [0, 1]}), (("bit",), {"type": int, "choices": [0, 1]}),
            (("root",), {}), (("--sample-id",), {}), (("--actor",), {}),
            (("--memory-only",), {"action": "store_true", "help": "store without learning from it"}),
            (("--simulated",), {"action": "store_true", "help": "provenance 'simulated'"}),
            (("--acquired-at",), {"type": float, "help": "Unix seconds when the reading was taken"}))
    command("status", lambda db, a, now: beliefs.compute(db, a.epoch), "beliefs for an epoch", (("epoch",), {}))
    command("decide", lambda db, a, now: executive.decide(db, a.epoch, now, record=a.record,
                                                          path=store.resolve_db(a)),
            "choose the next step for an epoch", (("epoch",), {}),
            (("--record",), {"action": "store_true", "help": "record the decision and any open question"}))
    command("explain", lambda db, a, now: beliefs.explain(db, a.epoch),
            "beliefs with supporting evidence, uncertainty, revisions and change detection", (("epoch",), {}))
    command("pending", lambda db, a, now: executive.pending(db, epoch=a.epoch, include_resolved=a.all),
            "open questions", (("--epoch",), {}),
            (("--all",), {"action": "store_true", "help": "include resolved questions"}))
    resolve = command("resolve", lambda db, a, now: executive.resolve(db, a.id, a.text, now, confirm=a.confirm),
                      "answer an open question", (("id",), {"type": int}), (("text",), {}))
    answer = resolve.add_mutually_exclusive_group()
    answer.add_argument("--confirm", dest="confirm", action="store_const", const=True,
                        help="the change happened: relearn only that scope from its changepoint")
    answer.add_argument("--reject", dest="confirm", action="store_const", const=False,
                        help="no change: keep pooling and stop reporting it until new evidence arrives")
    command("import-legacy", lambda db, a, now: legacy.import_legacy(db, a.path, now, path=store.resolve_db(a)),
            "import a prototype live database (read-only)", (("path",), {"type": Path}))
