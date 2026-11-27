"""Lane FX5 (ADR-081, the owner's decision of 2026-11-27): the relater's verdicts are DISPLAY-ONLY. No path stores a
consistent_with or contradicts link (the command with a fully worded question between plain sources, the production
pipeline, the older comparisons); the answer is shown with one fixed plain line; describes and same_subject are
recorded as before; verdict links an earlier version stored are never removed automatically, only by the owner's
step (`links_decided_by.py --by any`)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx5_pairs as P  # noqa: E402

from neuroscifi.artifacts import produce as PR, relate as R, relate_cli, store as A  # noqa: E402
from neuroscifi.instance import store  # noqa: E402

T0 = 1_800_000_000.0
COSTS = {"costs.csv": b"Item,Cost (EUR)\nRope,95\nPegs,25\n", "memo.txt": b"The total cost was 120 EUR.\n"}
Q = "Is the total cost in the memo the total of the costs in the table?"


def _verdicts(db):
    return db.execute("SELECT src, relation, dst_id FROM artifact_links WHERE relation IN ('consistent_with', "
                      "'contradicts')").fetchall()


def _held(db, files):
    return [A.add(db, data, name=name, now=T0).id for name, data in files.items()]


def test_no_path_stores_a_verdict_and_the_answer_says_so(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, COSTS)
    for question in (Q, "Cost?", None):
        out = relate_cli.relate(db, a, b, question=question, now=T0)
        assert not [x for x in out["links"] if x["relation"] in relate_cli.VERDICTS], question
    out = relate_cli.relate(db, a, b, question=Q, now=T0)
    assert out["answer"] is True and R.NOT_RECORDED in out["because"]
    assert "nothing recorded" in relate_cli.plain(out)
    made = PR.produce(db, "a document of these files", [a, b], form="document", now=T0 + 5)
    assert made.status == "made"
    assert _verdicts(db) == []
    db.close()


def test_describes_and_same_subject_are_recorded_as_before(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, {"README.txt": b"The file costs.csv lists what we paid.\n", "costs.csv": COSTS["costs.csv"]})
    out = relate_cli.relate(db, a, b, now=T0)
    assert [x["relation"] for x in out["links"]] == ["describes"]
    db.close()


def test_earlier_verdict_links_stay_until_the_owner_removes_them(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, {"k.zip": P.zip_of(P.KIT), "list.txt": P.sizes(P.KIT)})
    A.link(db, a, "contradicts", "artifact", b, "bundle.members.listing/document.listing;tol=0", 0.8, now=T0)
    relate_cli.relate(db, a, b, question=P.Q_KIT, now=T0 + 1)              # withdrawn: "cannot", nothing changes
    assert _verdicts(db) == [(a, "contradicts", b)]
    assert [x["src"] for x in relate_cli.remove_links_decided_by(db, "any")] == [a]
    assert _verdicts(db) == []
    db.close()
