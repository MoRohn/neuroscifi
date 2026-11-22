"""`nsf resources`: memory, GPU and container use, the pressure level and what the scheduling policies decide now
(docs/architecture/RESOURCE_SCHEDULING.md). JSON on stdout, like `nsf regulation`.

  nsf resources [--quick] [--cold-start]          the status (the idle policy's timeouts, state and events included)
  nsf resources idle [--set vllm=30m ...]         show or set the idle timeouts (off by default; `off` turns one off)
  nsf resources tick                              one idle-policy pass (neuroscifi-idle.timer runs it every minute)
  nsf resources unload|load SERVICE               the owner unloads or loads a service now (vllm, asr, tts, searxng)
"""
from __future__ import annotations

import sys


def _status(args):
    from . import idle, status
    from ..instance import store
    from ..instance.cli import emit
    path = store.resolve_db(args)
    if args.action == "status":
        return emit(status(path, full=not args.quick, cold_start=args.cold_start))
    policy = idle.Idle(path)
    if args.action == "idle":
        if args.set:
            changes = {}
            for item in args.set:
                name, sep, value = item.partition("=")
                if not sep:
                    raise SystemExit(f"nsf resources idle: --set wants SERVICE=DURATION, got {item!r}")
                try:
                    changes[name.strip()] = idle.parse_timeout(value)
                except ValueError as error:
                    raise SystemExit(f"nsf resources idle: {error}") from None
            try:
                policy.configure(changes)
            except ValueError as error:
                raise SystemExit(f"nsf resources idle: {error}") from None
        return emit(policy.status())
    if args.action == "tick":
        return emit(policy.tick())
    if not args.service:
        raise SystemExit(f"nsf resources {args.action}: name the service ({', '.join(idle.SERVICES)})")
    if args.action == "unload":
        out = policy.unload(args.service, reason="owner command", by="owner")
    else:
        out = policy.load(args.service, reason="owner command", by="owner",
                          notify=lambda text: print(text, file=sys.stderr, flush=True))
    emit(out)
    return 1 if out["action"] == "failed" else 0


def register(sub):
    from . import idle
    from ..instance import store
    p = sub.add_parser("resources", help="memory/GPU pressure level, resource-scheduling decisions, idle unloading")
    store.add_db_argument(p)
    p.add_argument("action", nargs="?", default="status", choices=("status", "idle", "tick", "unload", "load"),
                   help="status (default); idle: show or --set the idle timeouts; tick: one idle-policy pass; "
                        "unload|load SERVICE: the owner's manual command")
    p.add_argument("service", nargs="?", choices=tuple(idle.SERVICES), help="for unload and load")
    p.add_argument("--set", action="append", metavar="SERVICE=DURATION",
                   help="idle timeout, e.g. vllm=30m, asr=15m, searxng=off (repeatable; with `idle`)")
    p.add_argument("--quick", action="store_true", help="memory only (no nvidia-smi or docker calls)")
    p.add_argument("--cold-start", action="store_true",
                   help="also report each neuroscifi-* container's last measured start-to-ready time")
    p.set_defaults(handler=_status)
