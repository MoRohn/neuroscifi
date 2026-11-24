"""knowledge.query's lexical index kept between calls (W8).

The index must never answer from older memory than the query's own read: every test compares the kept index with
one built from nothing for the same call (`cached=False`), after writes, status changes and forgets made on this
connection, on a second connection and by another process."""
import random
import subprocess
import sys

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import knowledge, semantic
from neuroscifi.webcortex import text as wtext
from test_consolidate import T0, open_db

SUBJECTS = ("Orbix", "Tessaly", "Varnel", "Lumen", "Quillstream", "Tolvane", "Halvorsen", "Ostra", "Imre", "Zephyr")
NOUNS = ("server", "river", "bridge", "satellite", "library", "lighthouse", "census", "harbour", "medal", "compiler")
VERBS = ("listens on port", "is", "spans", "was launched in", "reached version", "stands", "counted", "opened in",
         "was awarded in", "targets")
TOPICS = ("Orbix server port", "Varnel river length", "lighthouse", "census of Tessaly", "medal", "compiler targets",
          "nothing about this at all", "Lumen satellite launched", "bridge", "harbour opened")


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


@pytest.fixture(autouse=True)
def fresh_indexes():
    knowledge.clear_index()
    semantic.clear()
    yield
    knowledge.clear_index()
    semantic.clear()


def fact(rng, n):
    s, noun, verb = rng.choice(SUBJECTS), rng.choice(NOUNS), rng.choice(VERBS)
    return f"The {s} {noun} {verb} {rng.randint(2, 9000)} in record {n}."


def same(db, now, topics=TOPICS, limit=10):
    """The kept index and one built from nothing give the same answer, for every topic."""
    for topic in topics:
        kept = knowledge.query(db, topic, now, limit=limit)
        fresh = knowledge.query(db, topic, now, limit=limit, cached=False)
        assert kept == fresh, topic
    return True


# ---- the scoring is text.BM25's, float for float --------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(6))
def test_index_scores_equal_bm25_exactly(seed):
    rng = random.Random(seed)
    vocab = [f"w{i}" for i in range(60)]
    live = [(n + 1, " ".join(rng.choice(vocab) for _ in range(rng.randint(0, 14))), f"k{n} <num>")
            for n in range(rng.randint(1, 120))]
    index = knowledge._Index(live, lambda row: knowledge._doc_tokens(row[1], row[2]))
    reference = wtext.BM25([knowledge._doc_tokens(s, k) for _, s, k in live])
    for _ in range(40):
        terms = [rng.choice(vocab + ["absent", "k3"]) for _ in range(rng.randint(0, 6))]
        assert index.scores(terms) == reference.scores(terms)      # the same floats, not close ones


def test_an_index_of_nothing_and_of_empty_statements():
    tokens = lambda row: knowledge._doc_tokens(row[1], row[2])      # noqa: E731
    assert knowledge._Index([], tokens).scores(["a"]) == []
    index = knowledge._Index([(1, "the of and", ""), (2, "of", "")], tokens)   # stop words only
    assert index.scores(["the"]) == wtext.BM25([[], []]).scores(["the"]) == [0.0, 0.0]


# ---- the kept index is the index of memory as it is now -----------------------------------------------------------

@pytest.mark.parametrize("seed", range(5))
def test_kept_index_equals_a_fresh_one_after_every_write_on_either_connection(db, tmp_path, seed):
    rng = random.Random(seed)
    other = store.open_existing(tmp_path / "individual.sqlite")
    try:
        conns, now, n = (db, other), T0, 0
        same(db, now)
        for step in range(60):
            conn = rng.choice(conns)
            now += 10
            live = [r["id"] for r in knowledge.rows(conn, "status IN ('provisional','accepted','stale')")]
            op = rng.choice(("assert",) * 5 + ("archive", "reject", "forget", "review", "restate"))
            if op == "assert" or not live:
                n += 1
                knowledge.assert_fact(conn, fact(rng, n), now)
            elif op == "archive":
                knowledge.archive(conn, rng.choice(live), now)
            elif op == "reject":
                knowledge.reject(conn, rng.choice(live), now)
            elif op == "forget":
                knowledge.forget(conn, rng.choice(live), now)
            elif op == "review":
                knowledge.review(conn, rng.choice(live), True, now)
            else:       # the owner states another value under the same words: a new version supersedes the old
                item = knowledge.row(conn, rng.choice(live))
                knowledge.assert_fact(conn, item["statement"].replace(" in record", " and 7 more in record"), now)
            reader = rng.choice(conns)
            assert same(reader, now, rng.sample(TOPICS, 4))
        assert same(db, now) and same(other, now)
    finally:
        other.close()


def test_the_second_query_tokenises_nothing_and_a_new_fact_only_itself(db, monkeypatch):
    for n in range(30):
        knowledge.assert_fact(db, f"The Nolvar{n} daemon listens on port {7000 + n}.", T0 + n)
    seen = []
    real = knowledge._doc_tokens
    monkeypatch.setattr(knowledge, "_doc_tokens", lambda s, k: seen.append(s) or real(s, k))
    first = knowledge.query(db, "daemon port", T0 + 100)
    assert len(seen) == 30 and first["matched"] == 10
    seen.clear()
    assert knowledge.query(db, "daemon port", T0 + 101)["items"] == first["items"] and seen == []
    assert knowledge.query(db, "which daemon is Nolvar7", T0 + 102) and seen == []     # another topic too
    knowledge.assert_fact(db, "The Tolvane Lighthouse is 41 metres tall.", T0 + 103)
    assert knowledge.query(db, "lighthouse", T0 + 104)["matched"] == 1
    assert seen == ["The Tolvane Lighthouse is 41 metres tall."]           # 30 statements kept their tokens
    seen.clear()
    assert knowledge.query(db, "lighthouse", T0 + 105, cached=False)["matched"] == 1 and len(seen) == 31


def test_a_write_by_another_process_is_seen_by_the_next_query(db, tmp_path):
    knowledge.assert_fact(db, "The Tolvane Lighthouse is 41 metres tall.", T0)
    assert knowledge.query(db, "Halvorsen bridge", T0 + 1)["items"] == []
    code = ("import sys; from neuroscifi.instance import store; from neuroscifi.webcortex import knowledge; "
            "db = store.open_existing(sys.argv[1]); "
            "knowledge.assert_fact(db, 'The Halvorsen Bridge opened in 1987.', float(sys.argv[2])); db.close()")
    subprocess.run([sys.executable, "-c", code, str(tmp_path / "individual.sqlite"), str(T0 + 2)], check=True)
    found = knowledge.query(db, "Halvorsen bridge", T0 + 3)
    assert [i["statement"] for i in found["items"]] == ["The Halvorsen Bridge opened in 1987."]
    assert found == knowledge.query(db, "Halvorsen bridge", T0 + 3, cached=False)


def test_forget_drops_the_index_of_this_process_at_once(db):
    kid = knowledge.assert_fact(db, "The Tolvane Lighthouse is 41 metres tall.", T0)["id"]
    knowledge.assert_fact(db, "The Halvorsen Bridge opened in 1987.", T0)
    assert knowledge.query(db, "lighthouse", T0 + 1)["matched"] == 1 and len(knowledge._INDEXES) == 1
    knowledge.forget(db, kid, T0 + 2)
    assert len(knowledge._INDEXES) == 0                 # no token of the forgotten statement is held
    assert knowledge.query(db, "lighthouse", T0 + 3)["items"] == []
    held = next(iter(knowledge._INDEXES.values()))
    assert all("Lighthouse" not in statement for _, statement, _ in held.live) and held.n == 1


def test_an_item_that_stopped_being_live_after_the_index_was_read_is_left_out(db, tmp_path, monkeypatch):
    first = knowledge.assert_fact(db, "The Tolvane Lighthouse is 41 metres tall.", T0)["id"]
    second = knowledge.assert_fact(db, "The Tolvane Lighthouse was built in 1874.", T0)["id"]
    other = store.open_existing(tmp_path / "individual.sqlite")
    real = knowledge._index

    def read_then_archived_elsewhere(conn, **kw):
        index = real(conn, **kw)
        knowledge.archive(other, first, T0 + 1)         # between the read of the index and the read of the rows
        return index
    monkeypatch.setattr(knowledge, "_index", read_then_archived_elsewhere)
    try:
        found = knowledge.query(db, "Tolvane lighthouse", T0 + 2)
    finally:
        other.close()
    assert [i["id"] for i in found["items"]] == [second] and found["matched"] == 1


def test_bounds_nothing_is_kept_above_the_size_limit_and_only_a_few_individuals(db, tmp_path, monkeypatch):
    for n in range(3):
        knowledge.assert_fact(db, f"The Nolvar{n} daemon listens on port {7000 + n}.", T0 + n)
    monkeypatch.setattr(knowledge, "INDEX_MAX_STATEMENTS", 2)
    assert knowledge.query(db, "daemon", T0 + 9)["matched"] == 3 and len(knowledge._INDEXES) == 0
    monkeypatch.setattr(knowledge, "INDEX_MAX_STATEMENTS", 120_000)
    monkeypatch.setattr(knowledge, "INDEX_FILES", 2)
    others = []
    try:
        for n in range(3):
            conn = store.create(tmp_path / f"other{n}.sqlite", T0)
            others.append(conn)
            knowledge.assert_fact(conn, "The Halvorsen Bridge opened in 1987.", T0)
            assert knowledge.query(conn, "bridge", T0 + 1)["matched"] == 1
        assert len(knowledge._INDEXES) == 2
        assert knowledge.query(db, "daemon", T0 + 9)["matched"] == 3       # an evicted individual is simply rebuilt
    finally:
        for conn in others:
            conn.close()


def test_an_individual_in_memory_is_never_kept():
    import sqlite3
    from neuroscifi.instance import schema
    conn = sqlite3.connect(":memory:")
    schema.migrate(conn)
    assert store.database_path(conn) in (None, "")
    assert knowledge._index(conn).n == 0 and len(knowledge._INDEXES) == 0


def test_the_kept_index_gives_the_semantic_ranking_what_a_fresh_one_gives(db, monkeypatch):
    """With memory ranked by meaning the index supplies the statements the models read: kept or built for the
    call, the answer is the same, before and after a write."""
    from neuroscifi.inference import config as iconfig

    class Model:
        def __init__(self, name):
            self.cfg = iconfig.InferenceConfig(base_url="http://127.0.0.1:1/v1", model=name)

        def embed(self, texts, *, purpose, timeout=None, input_type=None):
            return [[1.0 if "port" in x else 0.1, 1.0 if "Lighthouse" in x or "beacon" in x else 0.1] for x in texts]

        def rerank(self, query, documents, *, purpose, timeout=None):
            return [0.9 if ("beacon" in query) == ("Lighthouse" in d) else 0.001 for d in documents]
    monkeypatch.setattr(semantic, "_gateways", lambda conn: (Model("embedder"), Model("reranker")))
    semantic.set_config(db, {"enabled": True}, T0)
    knowledge.assert_fact(db, "The Nolvar1 daemon listens on port 7001.", T0)
    knowledge.assert_fact(db, "The Tolvane Lighthouse is 41 metres tall.", T0 + 1)
    for now, expected in ((T0 + 2, 1), (T0 + 4, 2)):
        kept = knowledge.query(db, "how tall is the beacon", now)
        semantic.clear()                                  # the result cache must not be what makes them equal
        fresh = knowledge.query(db, "how tall is the beacon", now, cached=False)
        assert kept["retrieval"]["mode"] == "semantic" and kept["matched"] == expected
        assert {k: v for k, v in kept.items() if k != "retrieval"} == \
            {k: v for k, v in fresh.items() if k != "retrieval"}
        knowledge.assert_fact(db, "The Pellham Lighthouse was built in 1902.", T0 + 3)


# ---- every forget drops this process's copies (review F6) ---------------------------------------------------------------

class _Endpoint:
    """Enough of a gateway for semantic._vectors to name a store."""
    cfg = type("Cfg", (), {"base_url": "http://127.0.0.1:1/v1", "model": "stand-in"})()


def held_text():
    """Every statement this process holds outside the file: the kept lexical indexes and the semantic vectors."""
    texts = [statement for index in knowledge._INDEXES.values() for _, statement, _ in index.live]
    return texts + [t for vectors in semantic._STORES.values() for t in vectors.by_text]


def researched(db, now=T0):
    """Knowledge from a research task on two sources -> (task id, knowledge id)."""
    from test_consolidate import consolidated, two_sources
    tid, out = consolidated(db, [two_sources()], now)
    return tid, out["provisional"][0]


def warm(db):
    """This process holds the individual's index and a vector of its statement."""
    statement = db.execute("SELECT statement FROM web_knowledge").fetchone()[0]
    knowledge.query(db, statement, T0 + 50)
    semantic._vectors(db, _Endpoint()).by_text[statement] = None
    assert held_text().count(statement) == 2


@pytest.mark.parametrize("forget", ["knowledge", "research", "source", "restore_replay"])
def test_every_forget_that_removes_knowledge_drops_what_this_process_holds(db, tmp_path, forget):
    from neuroscifi.instance import checkpoints, governance
    tid, kid = researched(db)
    if forget == "restore_replay":
        checkpoint = checkpoints.create(db, str(tmp_path / "individual.sqlite"), tmp_path / "cps", T0 + 10)
        governance.forget_knowledge(db, kid, T0 + 20)
        knowledge.assert_fact(db, "The NumPy project publishes a release every six months.", T0 + 21)
        warm(db)                       # copies held for the live file; the restored copy replays the forget
        restored = checkpoints.restore(db, checkpoint["id"], tmp_path / "restored.sqlite", T0 + 30)
        assert restored["web_removed"]["knowledge"] == 1           # the replay removed the forgotten item
    else:
        warm(db)
        if forget == "knowledge":
            governance.forget_knowledge(db, kid, T0 + 60)
        elif forget == "research":
            governance.forget_research(db, tid, T0 + 60)
        else:
            governance.forget_source(db, "a.example", T0 + 60)
            governance.forget_source(db, "b.example", T0 + 61)
        assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 0
    assert held_text() == []


def test_a_forgotten_name_rewritten_in_a_statement_drops_what_this_process_holds(db):
    from neuroscifi.instance import governance
    knowledge.assert_fact(db, "Ana Tolvane keeps the lighthouse log.", T0)
    warm(db)
    with store.write(db):
        assert governance.scrub_roots(db, {"Tolvane": ("partner", "0123456789abcdef")}) >= 1
    assert held_text() == []
    assert "Tolvane" not in db.execute("SELECT statement FROM web_knowledge").fetchone()[0]


def test_a_forget_that_changes_no_knowledge_keeps_the_index(db):
    from neuroscifi.instance import governance
    knowledge.assert_fact(db, "The Tolvane Lighthouse is 41 metres tall.", T0)
    knowledge.query(db, "lighthouse", T0 + 1)
    with store.write(db):
        assert governance.scrub_roots(db, {"Nobodyhere": ("partner", "0123456789abcdef")}) == 0
    assert len(knowledge._INDEXES) == 1
