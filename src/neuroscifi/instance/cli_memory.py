"""`nsf memory | forget | promote | sweep | checkpoint` commands."""
from pathlib import Path

from . import checkpoints, governance, memory, store


def _dir(args):
    return args.dir if args.dir is not None else checkpoints.default_dir(store.resolve_db(args))


def _drop_cached(args, urls=(), domain=None):
    """Drop URLs (or every URL of a domain) from the public cache, which holds bodies and validators only
    (ADR-023). The cache file is opened only if it exists."""
    from ..webcortex.services import cache_path
    path = cache_path(store.resolve_db(args))
    dropped = {"path": str(path), "exists": path.exists(), "entries_removed": 0}
    if dropped["exists"] and (urls or domain):
        from ..webcortex.cache import PublicCache
        cache = PublicCache(path)
        try:
            if domain:
                dropped["entries_removed"] = cache.forget_domain(domain)
            else:
                dropped["entries_removed"] = sum(cache.forget_url(u) for u in sorted(set(urls)))
        finally:
            cache.close()
    return dropped


def _forget_source(db, args, now):
    """governance.forget_source, then drop the URL (or every URL of the domain) from the public cache."""
    result = governance.forget_source(db, args.target, now)
    target = args.target.strip()
    keys = (result.get("effects") or {}).get("cache") or {}
    if "://" in target:
        dropped = _drop_cached(args, urls=set(keys.get("urls") or []) | {target})
    else:
        dropped = _drop_cached(args, domain=target)
    return result | {"public_cache": dropped}


def _forget_research(db, args, now):
    """governance.forget_research, then drop the task's pages that no other task read from the public cache."""
    result = governance.forget_research(db, args.target, now)
    urls = ((result.get("effects") or {}).get("cache") or {}).get("urls") or []
    return result | {"public_cache": _drop_cached(args, urls=urls)}


def _forget_knowledge(db, args, now):
    """governance.forget_knowledge, then drop the public-cache pages of the refresh tasks forgotten with it."""
    result = governance.forget_knowledge(db, args.target, now)
    urls = ((result.get("effects") or {}).get("cache") or {}).get("urls") or []
    return result | ({"public_cache": _drop_cached(args, urls=urls)} if urls else {})


def _rewriting(func):
    """A forget, then the in-place rewrite of the checkpoint archives it made stale (checkpoints.after_forget): the
    result names the archives rewritten, those that still need `nsf checkpoint rebuild`, and next_steps only then."""
    def handler(db, args, now):
        result = func(db, args, now)
        effects = result.get("effects") or {}
        if not effects.get("checkpoints_stale"):
            return result
        result = result | checkpoints.after_forget(db, now)
        result.pop("next_steps", None)
        if result["checkpoints_stale"]:
            result["next_steps"] = [governance.REBUILD_HINT.format(n=len(result["checkpoints_stale"]))]
        return result
    return handler


def _forget_voice(db, args, now):
    """A voice session with its transcript and retained audio (the same as `nsf voice forget`)."""
    from ..voice import transcripts
    return transcripts.forget_session(db, args.target, now=now)


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
        q.set_defaults(handler=with_db(_rewriting(lambda db, a, now, func=func: func(db, a.target, now))))
    q = fsub.add_parser("research", help="a Web Cortex task with its events, sources and claims (and its pages in "
                                         "the public cache that no other task read)")
    store.add_db_argument(q)
    q.add_argument("target", help="task id")
    q.set_defaults(handler=with_db(_rewriting(_forget_research)))
    q = fsub.add_parser("knowledge", help="a web knowledge item and every version of it")
    store.add_db_argument(q)
    q.add_argument("target", type=int, help="knowledge id")
    q.set_defaults(handler=with_db(_rewriting(_forget_knowledge)))
    q = fsub.add_parser("source", help="a source URL or every source of a domain, also from the public cache")
    store.add_db_argument(q)
    q.add_argument("target", help="URL or registrable domain")
    q.set_defaults(handler=with_db(_rewriting(_forget_source)))
    q = fsub.add_parser("voice", help="a voice session with its transcript and retained audio (= nsf voice forget); "
                                      "checkpoint archives are rewritten without it")
    store.add_db_argument(q)
    q.add_argument("target", help="voice session id")
    q.set_defaults(handler=with_db(_forget_voice))

    p = sub.add_parser("promote", help="keep one episode beyond the default lifetime")
    store.add_db_argument(p)
    p.add_argument("id")
    p.set_defaults(handler=with_db(lambda db, a, now: governance.promote(db, a.id, now)))

    p = sub.add_parser("sweep", help="expire evidence past its lifetime")
    store.add_db_argument(p)
    p.set_defaults(handler=with_db(lambda db, a, now: governance.sweep(db, now)))

    p = sub.add_parser("checkpoint", help="verified archives of the instance")
    csub = p.add_subparsers(dest="checkpoint_what", required=True)

    def sub_cmd(name, handler, *, dir_option=False, policy=False, keep=False):
        q = csub.add_parser(name)
        store.add_db_argument(q)
        if dir_option:
            q.add_argument("--dir", type=Path, default=None,
                           help="archive folder (default <db parent>/<db stem>.checkpoints)")
        if policy:
            q.add_argument("--policy", choices=sorted(checkpoints.POLICIES), default="cold")
        if keep:
            q.add_argument("--keep", type=int, default=7, help="valid archives to keep (newest first)")
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
        db, store.resolve_db(a), _dir(a), now, policy=a.policy, keep=a.keep), dir_option=True, policy=True, keep=True)
    sub_cmd("prune", lambda db, a, now: checkpoints.prune(db, now, keep=a.keep,
                                                                  db_path=store.resolve_db(a)), keep=True)
    q = sub_cmd("rewrite", lambda db, a, now: checkpoints.rewrite_stale(db, now, ids=a.id or None))
    q.add_argument("--id", type=int, action="append", help="only this stale archive (repeatable; default: every "
                                                           "stale archive a rewrite can fix)")
