"""`nsf memory | forget | promote | sweep | checkpoint` commands."""
from pathlib import Path

from . import checkpoints, governance, memory, store


def _dir(args):
    return args.dir if args.dir is not None else Path(store.resolve_db(args)).resolve().parent / "checkpoints"


def register(sub, *, with_db, run):
    p = sub.add_parser("memory", help="retrieve records of one memory kind")
    store.add_db_argument(p)
    p.add_argument("kind", choices=memory.KINDS)
    p.add_argument("--id")
    p.add_argument("--epoch")
    p.add_argument("--context")
    p.add_argument("--channel")
    p.add_argument("--root")
    p.add_argument("--since", type=float)
    p.add_argument("--until", type=float)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(handler=with_db(lambda db, a, now: memory.retrieve(
        db, a.kind, id=a.id, epoch=a.epoch, context=a.context, channel=a.channel, root=a.root,
        since=a.since, until=a.until, limit=a.limit)))

    p = sub.add_parser("forget", help="forget memory and everything derived from it")
    fsub = p.add_subparsers(dest="forget_what", required=True)
    for name, func, kind in (("evidence", governance.forget_evidence, str), ("epoch", governance.forget_epoch, str),
                             ("partner", governance.forget_partner, str), ("dataset", governance.forget_dataset, int)):
        q = fsub.add_parser(name)
        store.add_db_argument(q)
        q.add_argument("target", type=kind)
        q.set_defaults(handler=with_db(lambda db, a, now, func=func: func(db, a.target, now)))

    p = sub.add_parser("promote", help="keep one episode beyond the default lifetime")
    store.add_db_argument(p)
    p.add_argument("id")
    p.set_defaults(handler=with_db(lambda db, a, now: governance.promote(db, a.id, now)))

    p = sub.add_parser("sweep", help="expire evidence past its lifetime")
    store.add_db_argument(p)
    p.set_defaults(handler=with_db(lambda db, a, now: governance.sweep(db, now)))

    p = sub.add_parser("checkpoint", help="verified archives of the instance")
    csub = p.add_subparsers(dest="checkpoint_what", required=True)

    def sub_cmd(name, handler, *, dir_option=False, policy=False):
        q = csub.add_parser(name)
        store.add_db_argument(q)
        if dir_option:
            q.add_argument("--dir", type=Path, default=None, help="archive folder (default <db parent>/checkpoints)")
        if policy:
            q.add_argument("--policy", choices=sorted(checkpoints.POLICIES), default="cold")
        q.set_defaults(handler=with_db(handler))
        return q

    sub_cmd("create", lambda db, a, now: checkpoints.create(
        db, store.resolve_db(a), _dir(a), now, policy=a.policy), dir_option=True, policy=True)
    sub_cmd("list", lambda db, a, now: checkpoints.listing(db))
    sub_cmd("verify", lambda db, a, now: checkpoints.verify(db, a.id, now)).add_argument("id", type=int)
    q = sub_cmd("restore", lambda db, a, now: checkpoints.restore(db, a.id, a.dest, now))
    q.add_argument("id", type=int)
    q.add_argument("dest", type=Path)
    sub_cmd("rebuild", lambda db, a, now: checkpoints.rebuild_stale(
        db, store.resolve_db(a), _dir(a), now, policy=a.policy), dir_option=True, policy=True)
