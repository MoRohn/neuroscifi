"""Lane FX4 (R9): a link recorded from one of lane FX3's comparisons (members, links, times) can be listed and removed
by naming the comparison; nothing else in the store changes. Seeded store: three specific answers, one from each
comparison, plus a link from an older comparison and a structural link that must stay."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx4_review_pairs as P  # noqa: E402
import test_fx3_relate as F  # noqa: E402

from neuroscifi.artifacts import relate_cli, store as A  # noqa: E402
from neuroscifi.instance import store  # noqa: E402

T0 = 1_800_000_000.0
ROOT = Path(__file__).resolve().parents[2]


def _seed(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    ids = {}
    files = {
        "pack.zip": P.zipped(P.PACK), "sizes.txt": P.size_lines(P.PACK).encode(),
        "sums.txt": "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in P.PACK.items()).encode(),
        "flow.svg": F._graph_svg(F.EDGES), "notes.txt": F._edge_text(F.EDGES),
        "bells.wav": F._beeps(F.ONSETS), "cues.txt": F._cues(F.ONSETS),
        "costs.csv": b"Part,Cost (GBP)\nBolts,90\nNuts,15\n", "memo.txt": b"The largest cost was 90 GBP.\n",
    }
    for name, data in files.items():
        ids[name] = A.add(db, data, name=name, now=T0).id
    asked = [("pack.zip", "sizes.txt", "Do the byte sizes in the list match the files in the archive?"),
             ("pack.zip", "sums.txt", "Do the checksums of the archive's files match the list?"),
             ("flow.svg", "notes.txt", "Do the connections in the drawing match the notes?"),
             ("bells.wav", "cues.txt", "Do the bells start at the times in the cue sheet?"),
             ("costs.csv", "memo.txt", "Is the largest cost in the memo right?")]
    for a, b, q in asked:
        out = relate_cli.relate(db, ids[a], ids[b], question=q, now=T0)
        assert out["answer"] is True, (a, b)
    return db, path, ids


def _all_links(db):
    return sorted(db.execute("SELECT src, relation, dst_id, how FROM artifact_links").fetchall())


def test_links_decided_by_a_comparison_are_listed_and_removed_and_nothing_else(tmp_path):
    db, _, ids = _seed(tmp_path)
    before = _all_links(db)
    members = relate_cli.links_decided_by(db, "members")
    assert {(x["src"], x["dst_id"]) for x in members} == {(ids["pack.zip"], ids["sizes.txt"]),
                                                         (ids["pack.zip"], ids["sums.txt"])}
    assert len(relate_cli.links_decided_by(db, "links")) == 1 and len(relate_cli.links_decided_by(db, "times")) == 1
    removed = relate_cli.remove_links_decided_by(db, "members")
    assert len(removed) == 2 and relate_cli.links_decided_by(db, "members") == []
    after = _all_links(db)
    assert len(after) == len(before) - 2
    assert set(after) <= set(before)                                     # nothing else changed
    kept = {(r[0], r[2]) for r in after if r[1] == "consistent_with"}
    assert (ids["costs.csv"], ids["memo.txt"]) in kept and (ids["flow.svg"], ids["notes.txt"]) in kept
    assert A.get(db, ids["pack.zip"]).id == ids["pack.zip"]             # the artifacts stay
    db.close()


def test_the_documented_step_lists_then_removes(tmp_path):
    db, path, ids = _seed(tmp_path)
    db.close()
    env = {"PYTHONPATH": str(ROOT / "src")}
    script = str(ROOT / "scripts" / "omnimind_fx3" / "links_decided_by.py")
    listed = subprocess.run([sys.executable, script, "--db", str(path), "--by", "times"], capture_output=True,
                            text=True, env=env, timeout=120)
    assert listed.returncode == 0 and ids["bells.wav"] in listed.stdout and '"found"' in listed.stdout
    gone = subprocess.run([sys.executable, script, "--db", str(path), "--by", "times", "--remove"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert gone.returncode == 0 and '"removed"' in gone.stdout
    db = store.open_existing(path)
    try:
        assert relate_cli.links_decided_by(db, "times") == [] and len(relate_cli.links_decided_by(db, "links")) == 1
    finally:
        db.close()
