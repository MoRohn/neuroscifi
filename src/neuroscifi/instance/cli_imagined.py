"""`nsf imagined list | show | purge | kinds`: the imagined store (ADR-049), read and emptied by the owner."""
from .. import provenance
from . import checkpoints, imagined, store

HELP = ("things NeuroSciFi imagined (scenarios, guesses, model-written inferences), kept apart from what it knows. "
        "They are never evidence and never become knowledge. Nothing writes here yet")


def _purge(db, args, now):
    """imagined.purge, then the in-place rewrite of the archives it made stale (as `nsf forget` does)."""
    result = imagined.purge(db, now, record_id=args.id, kind=args.kind)
    if not result["effects"].get("checkpoints_stale"):
        return result
    out = result | checkpoints.after_forget(db, now)
    if out["checkpoints_stale"]:
        from .governance import REBUILD_HINT
        out["next_steps"] = [REBUILD_HINT.format(n=len(out["checkpoints_stale"]))]
    return out


def register(sub, *, with_db, run):
    p = sub.add_parser("imagined", help=HELP, description=HELP + ".")
    isub = p.add_subparsers(dest="imagined_what", required=True)

    q = isub.add_parser("list", help="list what is held, newest first, each marked with its kind and 'not evidence'")
    store.add_db_argument(q)
    q.add_argument("--kind", choices=imagined.KINDS, help="only this kind")
    q.add_argument("--limit", type=int, default=100, help="at most this many (1 to 1000)")
    q.set_defaults(handler=with_db(lambda db, a, now: imagined.listing(db, now, kind=a.kind, limit=a.limit)))

    q = isub.add_parser("show", help="one record in full: what it says, what it assumed, how unsure it is, what it "
                                     "came from and what was later observed")
    store.add_db_argument(q)
    q.add_argument("id", help="the record's id (imagined:...)")
    q.set_defaults(handler=with_db(lambda db, a, now: imagined.show(db, a.id)))

    q = isub.add_parser("purge", help="delete one record, every record of a kind, or everything here; a restored "
                                      "checkpoint never brings them back")
    store.add_db_argument(q)
    target = q.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", help="one record")
    target.add_argument("--kind", choices=imagined.KINDS, help="every record of this kind")
    target.add_argument("--all", action="store_true", help="everything")
    q.set_defaults(handler=with_db(_purge))

    q = isub.add_parser("kinds", help="what evidence, inference, prediction, simulation and speculation mean, and "
                                      "which kind each stored record type is")
    q.set_defaults(handler=run(lambda a: {"kinds": list(provenance.KINDS),
                                          "never_evidence": sorted(provenance.QUARANTINED),
                                          "records": provenance.mapping()}))
