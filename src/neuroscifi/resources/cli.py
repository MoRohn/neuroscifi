"""`nsf resources`: memory, GPU and container use, the pressure level and what the scheduling policies decide now
(docs/architecture/RESOURCE_SCHEDULING.md). JSON on stdout, like `nsf regulation`.

  nsf resources [--quick] [--cold-start]          the status (the idle policy's timeouts, state and events included)
  nsf resources idle [--set vllm=30m ...]         show or set the idle timeouts (off by default; `off` turns one off)
  nsf resources tick                              one idle-policy pass (neuroscifi-idle.timer runs it every minute)
  nsf resources unload|load SERVICE               the owner unloads or loads a service now (vllm, asr, tts, searxng,
                                                  embed, rerank)
  nsf resources load SERVICE --on-demand PURPOSE  reload it only if the idle policy unloaded it, recorded as "on
                                                  demand: PURPOSE": what a memory lookup starts in the background
  nsf resources wake [--dir DIR] [--linger S]     serve the wake requests in DIR (default $NSF_WAKE_DIR): reload each
                                                  service a request names, if the idle policy unloaded it and a
                                                  reload is due (neuroscifi-wake.service runs this). Creates DIR if
                                                  missing (mode 3770); an entry that is not a plain file is removed
                                                  or moved aside, and if that is impossible the command stays 30 s.
                                                  With --linger it stays S seconds and serves what arrives
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
        from . import monitor               # `loaded` as `nsf resources` shows it (from the running containers)
        return emit(policy.status(monitor.containers()))
    if args.action == "tick":
        return emit(policy.tick())
    if args.action == "wake":
        folder = args.dir or idle.wake_dir()
        if folder is None:
            raise SystemExit("nsf resources wake: name the request folder (--dir DIR or NSF_WAKE_DIR)")
        served = idle.serve_wake_requests(path, folder, idle=policy, linger=max(0.0, args.linger))
        emit({"served": served})
        return 1 if any(item["action"] in ("failed", "refused") for item in served) else 0
    if not args.service:
        raise SystemExit(f"nsf resources {args.action}: name the service ({', '.join(idle.SERVICES)})")
    if args.action == "unload":
        out = policy.unload(args.service, reason="owner command", by="owner")
    elif args.on_demand:
        # The background reload of resources.idle.using_now: a process of its own, so it outlives the lookup's.
        out = policy.ensure_loaded(args.service, purpose=args.on_demand) or \
            {"action": "none", "why": "nothing to do: the idle policy has not unloaded it"}
    else:
        out = policy.load(args.service, reason="owner command", by="owner",
                          notify=lambda text: print(text, file=sys.stderr, flush=True))
    emit(out)
    return 1 if out["action"] in ("failed", "refused") else 0


def register(sub):
    from . import idle
    from ..instance import store
    p = sub.add_parser("resources", help="memory/GPU pressure level, resource-scheduling decisions, idle unloading")
    store.add_db_argument(p)
    p.add_argument("action", nargs="?", default="status", choices=("status", "idle", "tick", "unload", "load", "wake"),
                   help="status (default); idle: show or --set the idle timeouts; tick: one idle-policy pass; "
                        "unload|load SERVICE: the owner's manual command")
    p.add_argument("service", nargs="?", choices=tuple(idle.SERVICES), help="for unload and load")
    p.add_argument("--set", action="append", metavar="SERVICE=DURATION",
                   help="idle timeout, e.g. vllm=30m, asr=15m, embed=15m, searxng=off (repeatable; with `idle`); "
                        "voice=30m: how long an open voice session may go unused and still hold off unloading "
                        "(default 30m; voice=off: for as long as it is open)")
    p.add_argument("--on-demand", metavar="PURPOSE",
                   help="with `load`: reload the service only if the idle policy unloaded it (an owner's unload "
                        "stands), and record it as on demand for PURPOSE")
    p.add_argument("--dir", metavar="DIR", help="with `wake`: the wake-request folder (default $NSF_WAKE_DIR)")
    p.add_argument("--linger", type=float, default=0.0, metavar="SECONDS",
                   help="with `wake`: stay this long and serve what arrives (neuroscifi-wake.service passes "
                        f"{idle.WAKE_LINGER:.0f}, which keeps its starts under its start limit); default 0")
    p.add_argument("--quick", action="store_true", help="memory only (no nvidia-smi or docker calls)")
    p.add_argument("--cold-start", action="store_true",
                   help="also report each neuroscifi-* container's last measured start-to-ready time")
    p.set_defaults(handler=_status)

    from .. import internal_state
    p = sub.add_parser("internal-state", help="the internal-state record: what the individual measures about its own "
                                              "operating condition, each reading with its source and age, the kept "
                                              "history and the predictive regulation's forecast (ADR-051)")
    store.add_db_argument(p)
    p.add_argument("--gateway", action="store_true", help="also ask the model endpoint for its health")
    p.add_argument("--history", type=int, default=0, metavar="N", help="include the N most recent history samples")
    from ..instance.cli import run
    p.set_defaults(handler=run(internal_state.cli))
