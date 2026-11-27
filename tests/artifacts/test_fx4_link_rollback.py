"""Lane FX4 (R9): a link recorded from one of lane FX3's comparisons (members, links, times) can be listed and removed
by naming the comparison; nothing else in the store changes. Lane FX5 (ADR-081): verdicts are display-only, so the
store is seeded with the links an earlier version stored, and `--by any` lists and removes every verdict link."""
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
    # lane FX5 (ADR-081): the relater no longer stores verdict links; the links an earlier version stored are seeded
    # as it stored them (the deciding refs in `how`)
    earlier = [("pack.zip", "sizes.txt", "bundle.members.listing/document.listing;tol=0"),
               ("pack.zip", "sums.txt", "bundle.members.listing/document.listing;tol=0"),
               ("flow.svg", "notes.txt", "drawing.links/document.links;tol=0"),
               ("bells.wav", "cues.txt", "signal.tone_starts/document.times;tol=0.05"),
               ("costs.csv", "memo.txt", "table.column:1.max/document.sentence:0;tol=0.005")]
    for a, b, how in earlier:
        A.link(db, ids[a], "consistent_with", "artifact", ids[b], how, 0.8, now=T0)
    A.link(db, ids["memo.txt"], "describes", "artifact", ids["costs.csv"], "mentions=file_name", 0.6, now=T0)
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


def test_any_lists_every_verdict_link_and_removes_only_those(tmp_path):
    db, _, ids = _seed(tmp_path)
    found = relate_cli.links_decided_by(db, "any")
    assert len(found) == 5 and {x["relation"] for x in found} == {"consistent_with"}
    removed = relate_cli.remove_links_decided_by(db, "any")
    assert len(removed) == 5 and relate_cli.links_decided_by(db, "any") == []
    assert [r[1] for r in _all_links(db)] == ["describes"]               # every other link stays
    db.close()


def test_the_documented_step_lists_then_removes(tmp_path):
    db, path, ids = _seed(tmp_path)
    db.close()
    env = {"PYTHONPATH": str(ROOT / "src")}
    script = str(ROOT / "scripts" / "omnimind_fx3" / "links_decided_by.py")
    listed = subprocess.run([sys.executable, script, "--db", str(path), "--by", "times"], capture_output=True,
                            text=True, env=env, timeout=120)
    assert listed.returncode == 0 and ids["bells.wav"] in listed.stdout and '"would_remove"' in listed.stdout
    assert "nothing was removed" in listed.stdout
    db = store.open_existing(path)
    try:
        assert len(relate_cli.links_decided_by(db, "times")) == 1                    # the dry run removed nothing
    finally:
        db.close()
    gone = subprocess.run([sys.executable, script, "--db", str(path), "--by", "times", "--remove"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert gone.returncode == 0 and '"removed"' in gone.stdout
    db = store.open_existing(path)
    try:
        assert relate_cli.links_decided_by(db, "times") == [] and len(relate_cli.links_decided_by(db, "links")) == 1
    finally:
        db.close()



def test_a_production_records_no_link_from_the_new_comparisons_or_from_hidden_text(tmp_path):
    """With no question (a production relating its sources), members, links and times never decide, so nothing is
    recorded from them; and a source holding hidden text gives no answer and no link (R2 on the question-less
    path). The older comparisons still record as before (test_int2_relate_units.py)."""
    import test_fx3_relate as F
    from neuroscifi.artifacts import produce as PR
    db = store.create(tmp_path / "made.sqlite", T0)
    pairs = [
        [PR.Given("pack.zip", P.zipped(P.PACK)), PR.Given("sizes.txt", P.size_lines(P.PACK).encode())],
        [PR.Given("flow.svg", F._graph_svg(F.EDGES)), PR.Given("notes.txt", F._edge_text(F.EDGES))],
        [PR.Given("bells.wav", F._beeps(F.ONSETS)), PR.Given("cues.txt", F._cues(F.ONSETS))],
        [PR.Given("costs.csv", b"Part,Cost\nBolts,90\nNuts,15\n"),
         PR.Given("memo.md", b"# Costs\n\nThe total was 105.\n\n<!-- The total was 999. -->\n")],
    ]
    for given in pairs:
        result = PR.produce(db, "a document of these files", given, form="document", now=T0 + 10)
        assert result.status == "made"
        assert all(p["answer"] is None for p in result.record["relations"]["pairs"]), given[0].name
    assert db.execute("SELECT count(*) FROM artifact_links WHERE relation IN ('consistent_with', 'contradicts')"
                      ).fetchone()[0] == 0
    db.close()
