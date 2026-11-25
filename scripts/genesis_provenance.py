#!/usr/bin/env python3
"""Evidence for the imagined store (ADR-049): every safeguard broken one at a time must be caught by a test, and what
the store costs where nothing is imagined (the default) and where it is full.

  python scripts/genesis_provenance.py mutations      # break each safeguard in a copy of src/, run its tests
  python scripts/genesis_provenance.py costs          # time the paths the store touches
  python scripts/genesis_provenance.py all --out docs/quality/evidence/genesis_provenance.json

`mutations` copies src/ into a temporary directory, applies one textual change from MUTATIONS there (the working tree is
never touched), runs the tests named for it against that copy (PYTHONPATH) and records whether they failed ("caught").
A control run with no change must pass. `costs` measures, on temporary individuals, with --repeats runs each (median and
spread reported): evidence and claim inserts under the version-17 triggers against the same inserts with those
triggers dropped; a forget with an empty store and with a full one (max_imagined_rows records derived from what is
forgotten); writing and listing records; and a checkpoint restore that replays a purge of every record.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

INSTANCE_TESTS = "tests/instance/test_imagined.py"
WEB_TESTS = "tests/webcortex/test_imagined_quarantine.py"

# (name, safeguard, file under src/neuroscifi, text, replacement, tests)
MUTATIONS = [
    ("trigger_table_dropped", "no reference column of web_knowledge may name an imagined record",
     "instance/schema.py",
     '    "web_knowledge": (("task_id",), (), {"supporting": (), "contradicting": ("claim", "task")}),\n', "",
     [INSTANCE_TESTS]),
    ("trigger_json_list_blind", "an imagined id in a JSON list of ids (escaped, in an object's id key) is refused",
     "instance/schema.py",
     '            tests.append(f"EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(NEW.{c}) THEN NEW.{c} END) j "\n'
     '                         f"WHERE {element})")\n', "", [INSTANCE_TESTS]),
    # review0 (Wave 0 fix lane): each finding's safeguard
    ("trigger_case_and_padding_blind", "an id in upper case or padded with white space is still refused (L4)",
     "instance/schema.py", "return f\"lower(trim(coalesce({expr},''),char(32,9,10,11,12,13))) GLOB '{_IMAGINED_ID}'\"",
     "return f\"coalesce({expr},'') GLOB '{_IMAGINED_ID}'\"", [INSTANCE_TESTS]),
    ("trigger_column_missing", "web_mentions' claim, knowledge and task ids may not name a record (L4)",
     "instance/schema.py", '    "web_mentions": (("claim_id", "knowledge_id", "task_id"), (), {}),\n', "",
     [INSTANCE_TESTS]),
    ("trigger_tests_free_text", "a page title or quoted text never trips the triggers (M2: consolidation aborted)",
     "instance/schema.py", '{"supporting": (), "contradicting": ("claim", "task")}',
     '{"supporting": (), "contradicting": ("claim", "task", "text"), "sources": ("title",)}',
     [INSTANCE_TESTS, WEB_TESTS]),
    ("record_replaceable", "INSERT OR REPLACE never rewrites a record (L1)", "instance/schema.py",
     "WHEN EXISTS (SELECT 1 FROM imagined WHERE id=NEW.id)", "WHEN 0", [INSTANCE_TESTS]),
    ("observation_refs_unrecorded", "the evidence an outcome names is forgotten with the record (M1)",
     "instance/imagined.py",
     '        db.executemany("INSERT INTO imagined_refs(record_id,ref_kind,ref_id,observed) VALUES(?,?,?,1)",\n'
     '                       [(record_id, k, i) for k, i in refs if (k, i) not in held])\n', "", [INSTANCE_TESTS]),
    ("observation_takes_text", "an observation holds no text of its own (M1)", "instance/imagined.py",
     "    if not (_scalar(observation) or (isinstance(observation, dict) and all(map(_scalar, observation.values())))):",
     "    if False:", [INSTANCE_TESTS]),
    ("observation_unregulated", "an observation is refused while writes are paused (note)", "instance/imagined.py",
     '    outcome = json.dumps({"observed": observation, "evidence": [list(r) for r in refs]}, sort_keys=True)\n'
     "    store.require_write_allowed(db)\n",
     '    outcome = json.dumps({"observed": observation, "evidence": [list(r) for r in refs]}, sort_keys=True)\n',
     [INSTANCE_TESTS]),
    ("lower_limit_ignored", "a lower row limit or lifetime applies to the records held (L3)", "instance/store.py",
     "                removed = imagined.bound(db, time.time() if now is None else now)",
     "                removed = 0", [INSTANCE_TESTS]),
    ("lifetime_not_brought_forward", "a shorter lifetime brings a held record's expiry forward (L3)",
     "instance/imagined.py",
     '    db.execute("UPDATE imagined SET expires_at=created_at+? WHERE expires_at>created_at+?",\n'
     "               (cfg.imagined_lifetime_seconds, cfg.imagined_lifetime_seconds))\n", "", [INSTANCE_TESTS]),
    ("sweep_skips_orphans", "the sweep removes a record whose source went any way (L3)", "instance/imagined.py",
     '    orphaned = db.execute(f"SELECT min(created_at) FROM imagined WHERE {_ORPHANS}").fetchone()[0]',
     "    orphaned = None", [INSTANCE_TESTS]),
    ("forgotten_epoch_reusable", "nothing new derives from a forgotten epoch (L3)", "instance/imagined.py",
     '(ref_id, float("-inf"))).fetchone()', "(ref_id, 9e18)).fetchone()", [INSTANCE_TESTS]),
    ("tombstones_kept_forever", "tombstones are dropped after the longest lifetime (note)", "instance/imagined.py",
     '    db.execute("DELETE FROM imagined_forgotten WHERE forgotten_at<=?", (now - store.MAX_IMAGINED_LIFETIME,))\n',
     "", [INSTANCE_TESTS]),
    ("first_build_not_repaired", "a file of the first build of migration 17 gets today's store", "instance/schema.py",
     '    if "imagined_refs" in present:\n        wanted |= _TRIGGERS_17', "    if False:\n        wanted |= _TRIGGERS_17",
     [INSTANCE_TESTS]),
    ("store_read_outside_the_allow_list", "only allow-listed modules touch the store (L5; the old opt-in list "
     "missed this module)", "webcortex/scheduler.py", "from ..instance import store\n",
     "from ..instance import imagined, store  # noqa: F401\n", [WEB_TESTS]),
    ("kind_check_widened", "the database takes only simulation, speculation and inference", "instance/schema.py",
     "kind TEXT NOT NULL CHECK(kind IN ('simulation','speculation','inference')),",
     "kind TEXT NOT NULL CHECK(kind IN ('simulation','speculation','inference','evidence')),", [INSTANCE_TESTS]),
    ("kind_relabel_allowed", "a record's kind is never rewritten", "instance/schema.py",
     "BEFORE UPDATE OF id, kind,\n      payload,", "BEFORE UPDATE OF id,\n      payload,", [INSTANCE_TESTS]),
    ("record_kind_unchecked", "record() refuses a kind that is not quarantined", "instance/imagined.py",
     "    if kind not in KINDS:\n        raise ValidationError(f\"an imagined record is one of",
     "    if False:\n        raise ValidationError(f\"an imagined record is one of", [INSTANCE_TESTS]),
    ("lineage_unchecked", "a record names only sources the file holds", "instance/imagined.py",
     "        missing = _unresolved(db, refs)\n        if missing:\n"
     "            raise ValidationError(f\"derived_from",
     "        missing = []\n        if missing:\n            raise ValidationError(f\"derived_from", [INSTANCE_TESTS]),
    ("observation_may_be_imagined", "an observation never names an imagined record", "instance/imagined.py",
     'OBSERVATION_KINDS = tuple(k for k in REF_KINDS if k != "imagined")', "OBSERVATION_KINDS = REF_KINDS",
     [INSTANCE_TESTS]),
    ("forgets_do_not_cascade", "every forget removes what was imagined from what it removed", "instance/governance.py",
     "    removed = imagined.purge_orphans(db, now)\n", "    removed = 0\n", [INSTANCE_TESTS, WEB_TESTS]),
    ("web_forgets_do_not_cascade", "a web forget removes what was imagined from what it removed",
     "instance/governance.py",
     "    _imagined_cascade(db, effects, now)\n    effects[\"checkpoints_stale\"] = _stale_all(db)\n",
     "    effects[\"checkpoints_stale\"] = _stale_all(db)\n", [WEB_TESTS]),
    ("epoch_forget_missed", "an epoch forgotten after a record was made removes it", "instance/imagined.py",
     " OR EXISTS (SELECT 1 FROM deletion_log d \"\n             \"WHERE d.target_kind='epoch' AND "
     "d.target_id=r.ref_id AND d.requested_at>=i.created_at))\"",
     ")\"", [INSTANCE_TESTS]),
    ("restore_skips_store", "a restore replays the store's removals", "instance/checkpoints.py",
     "imagined_removed = imagined.replay(copy, forgets.get(\"imagined\", []) if forgets else [], now)",
     "imagined_removed = 0", [INSTANCE_TESTS, WEB_TESTS]),
    ("replay_ignores_tombstones", "a restore removes what the live file purged, expired or evicted",
     "instance/imagined.py",
     "doomed = [i for (i,) in copy.execute(\"SELECT id FROM imagined\") if digest(copy, i, salt) in gone]",
     "doomed = []",
     [INSTANCE_TESTS]),
    ("no_tombstones", "every removal leaves a tombstone", "instance/imagined.py",
     "    db.executemany(\"INSERT OR IGNORE INTO imagined_forgotten(digest,reason,forgotten_at) VALUES(?,?,?)\",\n"
     "                   [(digest(db, i, salt), reason, now) for i in ids])\n", "", [INSTANCE_TESTS]),
    ("no_row_limit", "the store keeps at most max_imagined_rows records", "instance/imagined.py",
     "        evicting = held >= cfg.max_imagined_rows\n", "        evicting = False\n", [INSTANCE_TESTS]),
    ("no_expiry", "the sweep expires records past their lifetime", "instance/governance.py",
     "        expired_imagined = imagined.expire(db, now)\n", "        expired_imagined = 0\n", [INSTANCE_TESTS]),
    ("bundle_takes_quarantined", "the answer bundle never holds quarantined content", "webcortex/reasoning.py",
     'if f.get("statement") and f.get("provenance") not in QUARANTINED]', 'if f.get("statement")]', [WEB_TESTS]),
    ("guard_resolves_quarantined", "the guard never resolves a citation to quarantined content",
     "webcortex/reasoning.py",
     'claims = {c["id"]: c for c in b["claims"] if c.get("provenance") not in QUARANTINED}',
     'claims = {c["id"]: c for c in b["claims"]}', [WEB_TESTS]),
    ("consolidation_imports_store", "no evidence path imports the store", "webcortex/consolidate.py",
     "from ..instance import store\n", "from ..instance import imagined, store  # noqa: F401\n", [WEB_TESTS]),
    ("memory_lists_imagined", "memory.retrieve has no imagined kind", "instance/memory.py",
     '"research", "claim", "knowledge", "source", "monitor")', '"research", "claim", "knowledge", "source", "monitor", '
     '"imagined")', [WEB_TESTS]),
    ("listing_unlabelled", "every listing says 'imagined, not evidence'", "instance/imagined.py",
     'out = {"id": item["id"], "kind": item["kind"], "label": NOT_EVIDENCE,', 'out = {"id": item["id"], "kind": '
     'item["kind"],', [INSTANCE_TESTS]),
]


def _pytest(src_root, tests):
    env = os.environ | {"PYTHONPATH": str(src_root)}
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=REPO,
                          env=env, capture_output=True, text=True)
    tail = [line for line in done.stdout.splitlines() if line.strip()][-1:] or [""]
    return done.returncode, round(time.perf_counter() - started, 2), tail[0][:160]


def mutations(table=None, control_tests=None):
    """Each mutation of `table` (default MUTATIONS) in a copy of src/, its tests run against it; a control run of
    `control_tests` (default both test files) with no change must pass first. scripts/bench_genesis_safety.py runs
    its own table through this."""
    table = MUTATIONS if table is None else table
    control_tests = [INSTANCE_TESTS, WEB_TESTS] if control_tests is None else control_tests
    out = []
    with tempfile.TemporaryDirectory(prefix="nsf-mutations-") as tmp:
        root = Path(tmp) / "src"
        shutil.copytree(REPO / "src", root, ignore=shutil.ignore_patterns("__pycache__"))
        code, seconds, tail = _pytest(root, control_tests)
        control = {"exit": code, "seconds": seconds, "summary": tail}
        if code != 0:
            raise SystemExit(f"the control run fails before any mutation: {tail}")
        for name, safeguard, rel, old, new, tests in table:
            path = root / "neuroscifi" / rel
            original = path.read_text()
            if original.count(old) != 1:
                raise SystemExit(f"mutation {name}: the text to change occurs {original.count(old)} times in {rel}")
            path.write_text(original.replace(old, new))
            try:
                code, seconds, tail = _pytest(root, tests)
            finally:
                path.write_text(original)
            out.append({"mutation": name, "safeguard": safeguard, "file": f"src/neuroscifi/{rel}", "tests": tests,
                        "caught": code != 0, "exit": code, "seconds": seconds, "summary": tail})
    caught = sum(m["caught"] for m in out)
    return {"control": control, "caught": caught, "of": len(out), "results": out}


def _stats(samples):
    return {"median": round(statistics.median(samples), 6), "min": round(min(samples), 6),
            "max": round(max(samples), 6), "runs": len(samples)}


def costs(repeats=5, evidence_rows=20000, claim_rows=5000):
    from neuroscifi.instance import checkpoints, governance, imagined, schema, store
    T0 = 1_800_000_000.0
    out = {}

    def fresh(tmp, name, triggers=True):
        db = store.create(Path(tmp) / f"{name}.sqlite", T0)
        if not triggers:
            with store.write(db):
                for trigger in schema.NO_IMAGINED_TRIGGERS:
                    db.execute(f"DROP TRIGGER {trigger}")
        db.execute("INSERT INTO epochs(name,created_at) VALUES('e1',?)", (T0,))
        db.execute("INSERT INTO web_tasks(id,objective,mode,status,origin,created_at,updated_at) "
                   "VALUES('t1','o','quick_lookup','completed','test',1,1)")
        return db

    def inserts(db):
        started = time.perf_counter()
        with store.write(db):
            db.executemany("INSERT INTO evidence(id,epoch,channel,action,bit,root,sample_id,recorded_at) "
                           "VALUES(?,'e1','word',0,1,?,?,?)",
                           [(f"ev{i}", f"root{i % 50}", f"s{i}", T0) for i in range(evidence_rows)])
        middle = time.perf_counter()
        with store.write(db):
            db.executemany("INSERT INTO web_claims(id,task_id,text,key,kind,polarity,source_url,domain,retrieved_at,"
                           "created_at,flags) VALUES(?,'t1',?,'k','statement',1,'http://a/x','a',1,1,?)",
                           [(f"c{i}", f"sentence number {i} about a thing", json.dumps({"n": i}))
                            for i in range(claim_rows)])
        return middle - started, time.perf_counter() - middle

    with tempfile.TemporaryDirectory(prefix="nsf-costs-") as tmp:
        on = {"evidence": [], "claims": []}
        off = {"evidence": [], "claims": []}
        for run in range(repeats):
            for label, triggers, sink in (("on", True, on), ("off", False, off)):
                db = fresh(tmp, f"ins-{label}-{run}", triggers)
                ev, cl = inserts(db)
                sink["evidence"].append(ev)
                sink["claims"].append(cl)
                db.close()
        out["inserts"] = {
            "evidence_rows": evidence_rows, "claim_rows": claim_rows,
            "evidence_seconds": {"with_triggers": _stats(on["evidence"]), "without": _stats(off["evidence"])},
            "claim_seconds": {"with_triggers": _stats(on["claims"]), "without": _stats(off["claims"])},
            "overhead_per_evidence_row_us": round(1e6 * (statistics.median(on["evidence"]) -
                                                         statistics.median(off["evidence"])) / evidence_rows, 3),
            "overhead_per_claim_row_us": round(1e6 * (statistics.median(on["claims"]) -
                                                      statistics.median(off["claims"])) / claim_rows, 3)}

        empty, full, record_times, list_times = [], [], [], []
        cap = store.InstanceConfig.max_imagined_rows
        for run in range(repeats):
            db = fresh(tmp, f"forget-empty-{run}")
            db.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,recorded_at) "
                       "VALUES('x','e1','word',0,1,'r',?)", (T0,))
            started = time.perf_counter()
            governance.forget_evidence(db, "x", T0 + 1)
            empty.append(time.perf_counter() - started)
            db.close()
            db = fresh(tmp, f"forget-full-{run}")
            db.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,recorded_at) "
                       "VALUES('x','e1','word',0,1,'r',?)", (T0,))
            started = time.perf_counter()
            for n in range(cap):
                imagined.record(db, now=T0 + n * 1e-3, **imagined.example(n, derived_from=[("evidence", "x")]))
            record_times.append((time.perf_counter() - started) / cap)
            started = time.perf_counter()
            imagined.listing(db, T0 + 2, limit=100)
            list_times.append(time.perf_counter() - started)
            started = time.perf_counter()
            effects = governance.forget_evidence(db, "x", T0 + 3)["effects"]
            full.append(time.perf_counter() - started)
            assert effects["imagined_removed"] == cap
            db.close()
        out["forget_evidence_seconds"] = {"empty_store": _stats(empty), f"store_of_{cap}_derived": _stats(full)}
        out["record_seconds_each"] = _stats(record_times)
        out["listing_100_of_%d_seconds" % cap] = _stats(list_times)

        restores = []
        for run in range(repeats):
            db = fresh(tmp, f"restore-{run}")
            path = Path(tmp) / f"restore-{run}.sqlite"
            for n in range(cap):
                imagined.record(db, now=T0 + n * 1e-3, **imagined.example(n))
            cp = checkpoints.create(db, path, Path(tmp) / f"cps-{run}", T0 + 2)
            imagined.purge(db, T0 + 3)
            started = time.perf_counter()
            result = checkpoints.restore(db, cp["id"], Path(tmp) / f"restored-{run}.sqlite", T0 + 4)
            restores.append(time.perf_counter() - started)
            assert result["imagined_removed"] == cap
            db.close()
        out[f"restore_replaying_a_purge_of_{cap}_seconds"] = _stats(restores)
        db = fresh(tmp, "size")
        out["empty_store_pages"] = db.execute(
            "SELECT count(*) FROM dbstat WHERE name LIKE 'imagined%'").fetchone()[0] if _has_dbstat(db) else None
        db.close()
    return out


def _has_dbstat(db):
    try:
        db.execute("SELECT 1 FROM dbstat LIMIT 1")
        return True
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=("mutations", "costs", "all"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = {"what": "imagined store (ADR-049): safeguards broken one at a time, and costs",
              "host": {"python": platform.python_version(), "machine": platform.machine(),
                       "sqlite": __import__("sqlite3").sqlite_version}}
    if args.what in ("mutations", "all"):
        report["mutations"] = mutations()
    if args.what in ("costs", "all"):
        report["costs"] = costs(args.repeats)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    if "mutations" in report and report["mutations"]["caught"] != report["mutations"]["of"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
