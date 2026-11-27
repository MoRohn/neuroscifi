"""Lane FX5 (ADR-081, C4): `Relation.specific` is the one gate for a durable consistent_with / contradicts link on
every path, and a recorded verdict never outlives the answer it records: a pair holds at most one verdict link, the
latest decided one, and an undecided answer to the same question removes the link that answered it."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx5_pairs as P  # noqa: E402

from neuroscifi.artifacts import produce as PR, relate as R, relate_cli, store as A  # noqa: E402
from neuroscifi.instance import store  # noqa: E402

T0 = 1_800_000_000.0
Q = P.Q_KIT


def _verdicts(db):
    return sorted(db.execute("SELECT src, relation, dst_id, how FROM artifact_links WHERE relation IN "
                             "('consistent_with', 'contradicts')").fetchall())


def _held(db, files):
    return [A.add(db, data, name=name, now=T0).id for name, data in files.items()]


def test_the_latest_decided_answer_replaces_the_earlier_verdict(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, {"k.zip": P.zip_of(P.KIT), "list.txt": P.sizes(P.KIT)})
    digest = R.question_digest(Q)
    # an earlier, opposite verdict between the pair (as a first version of a comparison might have stored), in
    # either direction, with no question digest
    A.link(db, b, "contradicts", "artifact", a, "bundle.members.listing/document.listing;tol=0", 0.8, now=T0)
    out = relate_cli.relate(db, a, b, question=Q, now=T0 + 1)
    assert out["answer"] is True
    rows = _verdicts(db)
    assert [(r[0], r[1], r[2]) for r in rows] == [(a, "consistent_with", b)]
    assert rows[0][3].endswith(";q=" + digest)
    again = relate_cli.relate(db, a, b, question=Q, now=T0 + 2)        # the same answer: the link stays as it is
    assert [x["status"] for x in again["links"] if x["relation"] == "consistent_with"] == ["exists"]
    assert _verdicts(db) == rows
    db.close()


def test_an_undecided_answer_to_the_same_question_removes_its_stale_link(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, {"k.zip": P.zip_of(P.KIT), "list.md": P.sizes(P.KIT)})       # a Markdown listing: "cannot"
    digest = R.question_digest(Q)
    other = R.question_digest("Is the archive older than the list?")
    A.link(db, a, "consistent_with", "artifact", b, "bundle.members.listing/document.listing;tol=0;q=" + digest,
           0.8, now=T0)
    A.link(db, b, "contradicts", "artifact", a, "x.y/z.w;tol=0;q=" + other, 0.8, now=T0)
    out = relate_cli.relate(db, a, b, question=Q, now=T0 + 1)
    assert out["answer"] is None
    # the link that answered this question is gone; the one that answered another question stays
    assert [(r[0], r[1], r[2]) for r in _verdicts(db)] == [(b, "contradicts", a)]
    relate_cli.relate(db, a, b, question="Is the archive's size in the list?", now=T0 + 2)   # another question
    assert [(r[0], r[1], r[2]) for r in _verdicts(db)] == [(b, "contradicts", a)]
    db.close()


def test_one_gate_on_every_path(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    costs = {"costs.csv": b"Item,Cost (EUR)\nRope,95\nPegs,25\n", "memo.txt": b"The cost was 190 EUR in all.\n"}
    a, b = _held(db, costs)
    for question in ("Cost?", "Is the cost right?", None):
        out = relate_cli.relate(db, a, b, question=question, now=T0)
        assert not [x for x in out["links"] if x["relation"] in relate_cli.VERDICTS], question
    out = relate_cli.relate(db, a, b, question="Is the total cost in the memo the total of the costs in the table?",
                            now=T0)
    assert out["answer"] is False and [x["relation"] for x in out["links"]] == ["contradicts"]
    # the production pipeline relates its sources with no question: shown in its record, never a verdict link
    made = store.create(tmp_path / "m.sqlite", T0)
    result = PR.produce(made, "a document of these files", [PR.Given("costs.csv", costs["costs.csv"]),
                                                             PR.Given("memo.txt", costs["memo.txt"])],
                        form="document", now=T0 + 5)
    assert result.status == "made" and result.record["relations"]["pairs"][0]["answer"] is False
    assert _verdicts(made) == []
    db.close()
    made.close()
