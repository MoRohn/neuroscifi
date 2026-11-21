"""`nsf resources`: memory, GPU and container use, the pressure level and what the scheduling policies decide now
(docs/architecture/RESOURCE_SCHEDULING.md). JSON on stdout, like `nsf regulation`."""
from __future__ import annotations


def _status(args):
    from . import status
    from ..instance import store
    from ..instance.cli import emit
    return emit(status(store.resolve_db(args), full=not args.quick, cold_start=args.cold_start))


def register(sub):
    from ..instance import store
    p = sub.add_parser("resources", help="memory/GPU pressure level and resource-scheduling decisions")
    store.add_db_argument(p)
    p.add_argument("--quick", action="store_true", help="memory only (no nvidia-smi or docker calls)")
    p.add_argument("--cold-start", action="store_true",
                   help="also report each neuroscifi-* container's last measured start-to-ready time")
    p.set_defaults(handler=_status)
