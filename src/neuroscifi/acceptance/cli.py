"""`nsf acceptance`, `nsf bench` and `nsf doctor`. Output is JSON on stdout.

Exit codes: acceptance 0 only when every selected scenario passes, else 1 (2 for an unknown name);
doctor 1 when any check fails; bench 0.
"""
import json
from pathlib import Path
import sys

from . import bench, doctor, scenarios


def _emit(value):
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))


def _acceptance(args):
    names = list(scenarios.SCENARIOS) if args.all or not args.names else args.names
    unknown = sorted(set(names) - set(scenarios.SCENARIOS))
    if unknown:
        print(f"nsf: unknown scenario(s) {', '.join(unknown)}; choose from {', '.join(scenarios.SCENARIOS)}",
              file=sys.stderr)
        return 2
    report = scenarios.run(names, seed=args.seed, lifetimes=args.lifetimes, events=args.events,
                           test_lifetimes=args.test_lifetimes)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    _emit(report)
    return 0 if report["pass"] else 1


def _bench(args):
    _emit(bench.run(events=args.events, seed=args.seed))
    return 0


def _doctor(args):
    report = doctor.run(args.db)
    _emit(report)
    return 1 if doctor.failed(report) else 0


def _positive(text):
    value = int(text)
    if value < 1:
        raise ValueError(text)
    return value


def register(sub):
    p = sub.add_parser("acceptance", help="run the simulated first-release acceptance scenarios")
    p.add_argument("names", nargs="*", metavar="NAME", help=f"scenarios to run: {', '.join(scenarios.SCENARIOS)}")
    p.add_argument("--all", action="store_true", help="run every scenario (the default when no NAME is given)")
    p.add_argument("--seed", type=int, default=scenarios.DEFAULT_SEED)
    p.add_argument("--lifetimes", type=_positive, default=scenarios.DEFAULT_LIFETIMES,
                   help="simulated lifetimes in the grounding scenario")
    p.add_argument("--events", type=_positive, default=scenarios.DEFAULT_EVENTS,
                   help="loop events in the device_ownership scenario")
    p.add_argument("--test-lifetimes", type=_positive, default=scenarios.FIRST_TEST_LIFETIMES,
                   help="simulated lifetimes in the first_physical_test scenario")
    p.add_argument("--out", type=Path, help="also write the full report to this JSON file")
    p.set_defaults(handler=_acceptance)

    p = sub.add_parser("bench", help="host-local observe/decide latency percentiles (not a Spark measurement)")
    p.add_argument("--events", type=_positive, default=500)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(handler=_bench)

    p = sub.add_parser("doctor", help="check runtime versions and, with --db, the instance file's safety")
    p.add_argument("--db", type=Path, default=None, help="instance database to inspect read-only")
    p.set_defaults(handler=_doctor)
