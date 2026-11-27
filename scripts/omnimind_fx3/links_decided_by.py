"""List, or remove, the relation links a named comparison of lane FX3 decided (ADR-081, rollback; ruling R9).

    PYTHONPATH=src python scripts/omnimind_fx3/links_decided_by.py --db PATH --by members|links|times [--remove]

Without --remove it is a dry run: it prints what it would remove (source id, relation, other id, basis) and removes
nothing. Only with the owner's explicit --remove does it delete exactly those
consistent_with / contradicts links from the individual's artifact_links; artifacts, their events and every other
link stay. Ids and bases only: no artifact text is printed.
"""
from __future__ import annotations

import argparse
import json

from neuroscifi.artifacts import relate_cli
from neuroscifi.instance import store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--by", required=True, choices=sorted(relate_cli.DECIDED_BY_REFS))
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args(argv)
    db = store.open_existing(args.db)
    try:
        found = (relate_cli.remove_links_decided_by if args.remove else relate_cli.links_decided_by)(db, args.by)
    finally:
        db.close()
    if args.remove:
        print(json.dumps({"comparison": args.by, "removed": found}, indent=1))
    else:
        print(json.dumps({"comparison": args.by, "would_remove": found,
                          "note": "dry run: nothing was removed; pass --remove to remove exactly these links"},
                         indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
