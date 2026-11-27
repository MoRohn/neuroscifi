"""Lane R (ADR-077): `nsf artifact relate` and the links it keeps. Links are the system's claims about its artifacts:
recorded in artifact_links only, labelled wherever shown, removed when either end is forgotten (and when a restore
replays that forget), never across a forgotten root, never in a knowledge table."""
import json
import sqlite3

import pytest

import r_pairs as RP
from neuroscifi.artifacts import relate_cli, store as A
from neuroscifi.instance import governance, store

T0 = 1_800_000_000.0
PAIRS = {k: (q, m, o) for k, q, m, o in RP.pairs()}


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        yield db, path
    finally:
        db.close()


def held(db, files):
    return [A.add(db, data, name=name, now=T0).id for name, data in files.items()]


def counts(db):
    tables = [r for (r,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {t: db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables
            if not t.startswith(("artifact", "sqlite_")) and t not in ("meta",)}


def links(db):
    return db.execute("SELECT src, relation, dst_id, how, confidence FROM artifact_links ORDER BY relation").fetchall()


def test_relate_records_the_verdict_as_a_labelled_claim(individual):
    db, _ = individual
    q, matching, other = PAIRS["doc_mean_csv"]
    a, b = held(db, other)
    before = counts(db)
    out = relate_cli.relate(db, a, b, question=q, now=T0)
    assert out["answer"] is False
    rows = links(db)
    assert [(r[0], r[1], r[2]) for r in rows] == [(a, "contradicts", b)]
    assert rows[0][3].startswith("document.sentence:1/table.column:1.mean;tol=") and rows[0][4] == 0.8
    assert counts(db) == before                    # nothing outside the artifact tables changed: no knowledge row
    shown = A.show(db, b)
    assert any(link["relation"] == "contradicts" for link in shown["links"])
    from neuroscifi.artifacts.cli import _plain
    assert "the system's claim from measuring both artifacts; not evidence" in _plain("show", shown)
    c, d = held(db, {"note2.md": matching["note.md"] + b"\n", "temps2.csv": matching["temps.csv"]})
    assert relate_cli.relate(db, c, d, question=q, now=T0)["answer"] is True
    assert ("consistent_with",) in {(r[1],) for r in links(db) if r[0] == c}


def test_describes_and_same_subject_are_weak_links(individual):
    db, _ = individual
    a, b = held(db, {"README.md": b"# Towns\n\nThe file `towns.csv` lists the population of each town.\n",
                     "towns.csv": RP.csv(["town", "population"], [("a", 1), ("b", 2)])})
    c = held(db, {"other.csv": RP.csv(["population", "area"], [(3, 4)])})[0]
    relate_cli.relate(db, a, b, now=T0)
    relate_cli.relate(db, b, c, now=T0)
    rel = {(r[0], r[1], r[2]): r for r in links(db)}
    assert rel[(a, "describes", b)][3] == "mentions=file_name" and rel[(a, "describes", b)][4] == 0.6
    assert rel[(b, "same_subject", c)][3] == "shared_names=1" and rel[(b, "same_subject", c)][4] == 0.3


def test_a_shared_name_that_is_a_forgotten_root_is_never_used(individual):
    db, _ = individual
    b, c = held(db, {"a.csv": RP.csv(["Halvorsen", "x"], [(1, 2)]), "b.csv": RP.csv(["Halvorsen", "y"], [(3, 4)])})
    instance_id = store.birth(db)["instance_id"]
    with store.write(db):
        db.execute("INSERT INTO forgotten_roots(digest,kind,forgotten_at) VALUES(?,?,?)",
                   (governance.root_digest(instance_id, "halvorsen"), "partner", T0))
    out = relate_cli.relate(db, b, c, now=T0)
    assert out["relation"]["shared_names"] == [] and links(db) == []


@pytest.mark.parametrize("end", ["first", "second"])
def test_forgetting_either_end_removes_the_link_and_a_restore_replays_it(individual, tmp_path, end):
    db, _ = individual
    q, _, other = PAIRS["doc_count_zip"]
    a, b = held(db, other)
    relate_cli.relate(db, a, b, question=q, now=T0)
    assert len(links(db)) == 1
    copy = sqlite3.connect(tmp_path / "copy.sqlite")
    db.backup(copy)
    A.forget_artifact(db, a if end == "first" else b, now=T0 + 1)
    assert links(db) == []
    with copy:
        A.replay(copy, A.read_forgotten(db), T0 + 2)
    assert copy.execute("SELECT count(*) FROM artifact_links").fetchone()[0] == 0
    copy.close()


def test_an_unreadable_end_is_cannot_and_records_nothing(individual):
    db, _ = individual
    a, b = held(db, {"x.bin": bytes(range(256)) * 8, "n.txt": b"The total was 5.\n"})
    out = relate_cli.relate(db, a, b, now=T0)
    assert out["answer"] is None and links(db) == []


def test_the_command_speaks_plain_words_and_json(individual, capsys):
    db, path = individual
    q, _, other = PAIRS["doc_size_png"]
    a, b = held(db, other)
    db.close()
    from neuroscifi.cli import main
    assert main(["artifact", "relate", "--db", str(path), a, b, "--question", q]) == 0
    text = capsys.readouterr().out
    assert "they DISAGREE" in text and "recorded:" in text and "not evidence" in text
    assert main(["artifact", "relate", "--db", str(path), a, b, "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    # lane FX4 (R3): without a question the answer is shown, and no consistent_with/contradicts link is recorded
    assert value["answer"] is False
    assert not [x for x in value["links"] if x["relation"] in ("consistent_with", "contradicts")]
    assert main(["artifact", "relate", "--db", str(path), a, a]) == 2
