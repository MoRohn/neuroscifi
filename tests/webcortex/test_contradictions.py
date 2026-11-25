"""Contradictions and restatements in other words (W2): consolidate.propose / _paired with stand-in models.

The memory models only propose. These tests pin what is not theirs: the same-subject and comparable-value tests,
the answer model's last word, what a pair may and may not do to knowledge, and that nothing changes when the
switch is off or a model is down."""
import pytest

from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import InferenceError
from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import claims, consolidate, knowledge, semantic, state
from neuroscifi.webcortex import text as wtext
from test_consolidate import DAY, T0, claim, cluster, consolidated, open_db

LENGTH = "The Varnel River is 412 kilometres long."
BASIN = "The Varnel River drains a basin of 9,800 square kilometres."
HEIGHT = "The Brightwater dam stands 186 metres tall."
RESTATED = "From its source to the sea the Varnel runs for 412 km."
CONTRADICTED = "From its source to the sea the Varnel runs for 389 km."
DEEP = "At its deepest point the Varnel River is 38 metres deep."
OTHER_RIVER = "From its source to the sea the Ostrel runs for 389 km."
UNNAMED = "From its source to the sea the river runs for 389 km."
# what each statement is about, for the stand-in models: one axis per property
ABOUT = {"kilometres long": "length", "km long": "length", "metres long": "length", "runs for": "length", "drains a basin": "basin", "metres tall": "height",
         "metres deep": "depth", "metres wide": "width"}
AXES = ("length", "basin", "height", "depth", "width")


def axis(text):
    return next((a for words, a in ABOUT.items() if words in text), None)


class Models:
    """An embedding model and a reranker that know ABOUT; they record what they are shown."""

    def __init__(self, model, near=()):
        self.cfg = iconfig.InferenceConfig(base_url="http://127.0.0.1:1/v1", model=model)
        self.seen, self.calls, self.fail, self.in_transaction = [], 0, None, []
        self.near = near          # pairs of axes this model wrongly takes for the same property

    def same(self, a, b):
        return a is not None and (a == b or (a, b) in self.near or (b, a) in self.near)

    def embed(self, texts, *, purpose, timeout=None, input_type=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        self.seen += texts
        return [[1.0 if axis(t) == a else 0.6 for a in AXES] for t in texts]     # every pair is fairly similar

    def rerank(self, query, documents, *, purpose, timeout=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        self.seen += [query, *documents]
        return [0.97 if self.same(axis(query), axis(d)) else 0.03 for d in documents]

    def health(self):
        return {"state": "ok"}


@pytest.fixture(autouse=True)
def fresh():
    semantic.clear()
    knowledge.clear_index()
    yield
    semantic.clear()
    knowledge.clear_index()


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


@pytest.fixture
def models(monkeypatch):
    pair = [Models("embedder"), Models("reranker")]
    monkeypatch.setattr(semantic, "_gateways", lambda db: tuple(pair))
    return pair


@pytest.fixture
def judge(monkeypatch):
    """The answer model's confirmation: says yes when the two statements are about the same axis, and records."""
    asked = []

    def verdict(statement, stored):
        asked.append((statement, stored))
        return axis(statement) == axis(stored)
    monkeypatch.setattr(consolidate, "_pair_judge", lambda db, task_id=None, gateway=None: (verdict, None))
    return asked


def sources(text, hosts=("a.example", "b.example"), published=T0 - DAY, **kw):
    """Two independent sources stating `text`, keyed as the claim stage keys it."""
    key = claims.claim_key(text)
    return cluster([claim(f"http://{h}/page", text if n == 0 else f"As the survey read by reviewer {n} has it, {text}",
                          key=key, values=wtext.values(text), published=published)
                    for n, h in enumerate(hosts)], **kw)


def known(db, text=LENGTH, now=T0):
    """`text` as live knowledge from two web sources -> its id."""
    _, out = consolidated(db, [sources(text, hosts=("k1.example", "k2.example"))], now,
                          objective=f"What is known: {text}")
    assert len(out["provisional"]) == 1, out
    return out["provisional"][0]


def on(db, **settings):
    semantic.set_config(db, {"enabled": True} | settings, T0)


# ---- the tests that are not the models' --------------------------------------------------------------------------------

@pytest.mark.parametrize("a, b, same", [
    (RESTATED, LENGTH, True),                                             # {varnel} within {varnel, river}
    ("The catchment of the Varnel extends over 9,800 km².", BASIN, True),
    ("Sennet is home to 61,500 people.", "The town of Sennet has 61,500 inhabitants.", True),      # proven by b
    ("The town of Sennet has 61,500 inhabitants.", "Sennet is home to 61,500 people.", True),
    ("Lumen-7 flies 705,000 metres above the ground.", "Lumen-7 orbits at an altitude of 705 kilometres.", True),
    ("The payroll of Hollin & Reeve counts roughly 3,200 people.", "Hollin & Reeve employs about 3,200 people.", True),
    ("Indexing a big repository with Orbix takes 4 GB of RAM or more.",
     "Orbix needs at least 4 GB of memory to index a large repository.", True),                     # GB is a unit
    (OTHER_RIVER, LENGTH, False),
    ("The Ostrel River is 520 kilometres long.", LENGTH, False),
    ("Brandt & Voss employs about 5,100 people.", "Hollin & Reeve employs about 3,200 people.", False),
    ("Corvus-3 orbits at an altitude of 512 kilometres.", "Lumen-7 orbits at an altitude of 705 kilometres.", False),
    ("Lumen-9 orbits at an altitude of 512 kilometres.", "Lumen-7 orbits at an altitude of 705 kilometres.", False),
    ("Mount Corrin rises 3,480 metres above sea level.", "Mount Avarel rises 3,912 metres above sea level.", False),
    (UNNAMED, LENGTH, False),                                             # names nothing: never paired
    ("The river is 389 kilometres long.", "The lake is 12 kilometres long.", False),
    ("Visitors pay 12 euros.", "Visitors climb 214 steps.", False),     # the same first word proves no name
    ("Orbix wants 4,000 MB of memory.", "Orbix needs at least 4 GB of memory.", False),      # ... nor here, alone
])
def test_same_subject_is_decided_by_the_names_the_two_statements_carry(a, b, same):
    assert consolidate._same_subject(a, b) is same
    assert consolidate._same_subject(b, a) is same


def test_a_first_word_that_memory_writes_as_a_name_counts_as_one(db):
    a, b = "Orbix wants 4,000 MB of memory.", "Orbix needs at least 4 GB of memory."
    is_name = consolidate._name_in_memory(db)
    assert consolidate._same_subject(a, b, is_name) is False            # memory does not know the word
    knowledge.assert_fact(db, "By default, the Orbix server listens on port 7420.", T0)
    assert consolidate._same_subject(a, b, consolidate._name_in_memory(db)) is True
    knowledge.assert_fact(db, "Visitors climb 214 steps to reach the gallery.", T0)     # a first word, not a name
    assert consolidate._same_subject("Visitors pay 12 euros.", "Visitors climb 214 steps.",
                                     consolidate._name_in_memory(db)) is False
    assert consolidate._names("Lumen-7 carries 220 bands for NASA.") == ({"nasa"}, "Lumen-7")
    assert consolidate._names("the river is 4 GB long") == (set(), None)


@pytest.mark.parametrize("new, stored, comparable", [
    (["412 km"], ["389 km"], True),
    (["256 mi"], ["412 km"], True),                       # one dimension, other units
    (["412 km"], ["9800 km²"], False),                    # a length and an area
    (["61500 people"], ["61500"], True),                  # a plain number stands for the quantity
    (["1869-05-12"], ["1874"], True),                     # a date and a year
    (["1874"], ["214"], False),                           # a year and a count
    (["v4.2.0"], ["4.2"], True),                          # a bare decimal beside a version is a version
    (["EUR 610000000"], ["3200 people"], False),
    ([], ["412 km"], False),
    (["412 km"], [], False),
])
def test_values_are_comparable_only_like_for_like(new, stored, comparable):
    assert consolidate._comparable(new, stored) is comparable
    assert consolidate._comparable(stored, new) is comparable
    if comparable and new != stored:        # where there is something to compare, the comparison has a verdict
        assert wtext.values_conflict(new, stored) is not None or wtext.values_equal(new[0], stored[0]) is not False


def test_the_reranker_reads_the_statements_with_their_numbers_masked():
    assert semantic._masked("The Varnel runs for 412 km, or 256.0 miles, since 1,874.") == \
        "The Varnel runs for # km, or # miles, since #."
    assert semantic._masked("Lumen-7 carries 220 bands") == "Lumen-# carries # bands"


@pytest.mark.parametrize("statement, like, read", [
    ("The Varnel flows 256 miles to the sea.", "The Varnel River is 412 kilometres long.",
     "The Varnel flows # kilometres to the sea."),
    ("Yearly sales came to 0.61 billion euros.", "Revenue was 610 million euros.", "Yearly sales came to # million euros."),
    ("Sennet counts 61.5 thousand inhabitants.", "The town of Sennet has 61,500 inhabitants.",
     "Sennet counts # inhabitants."),
    ("Orbix wants 4,000 MB of memory.", "Orbix needs 4 GB of memory.", "Orbix wants # GB of memory."),
    ("The ferry cruises at 40.7 km/h.", "The ferry has a speed of 22 knots.", "The ferry cruises at # knots."),
    ("The spring is 161.6 °F.", "The spring is 72 °C.", "The spring is # °C."),
    # nothing of the other's kind: as written; the same unit: as before
    ("The dam generates 740 MW.", "The dam stands 186 metres tall.", "The dam generates # MW."),
    ("The Varnel runs for 389 km.", "The Varnel River runs for 412 km.", "The Varnel runs for # km."),
    # binary prefixes are not decimal ones (text._DIMENSION): never rewritten into GB
    ("Quorra uses 8 GiB of memory.", "Quorra needs 8 GB of memory.", "Quorra uses # GiB of memory."),
])
def test_each_statement_is_read_in_the_units_of_the_other(statement, like, read):
    """What the units are is not what the models are asked: a restatement in miles of a length in kilometres read
    under the reranker's floor one way round (scripts/bench_knowledge.py, lane K of the remaining-items round)."""
    assert semantic._masked(statement, like=like) == read


# ---- off, or a model down: what consolidation was ----------------------------------------------------------------------

def test_with_the_switch_off_nothing_is_asked_and_the_summary_is_what_it_was(db, tmp_path, models, judge):
    twin = store.create(tmp_path / "twin.sqlite", T0)
    state.set_config(twin, {"enabled": True, "min_domain_interval": 0.0}, T0)
    on(db, contradictions=False)                                  # memory by meaning on, pairing off
    out = {}
    for name, conn in (("switch_off", db), ("never_configured", twin)):
        known(conn)
        _, out[name] = consolidated(conn, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    twin.close()
    for summary in out.values():       # the task's id, and one more state version for the setting itself
        summary.pop("task_id"), summary.pop("state_version")
    assert out["switch_off"] == out["never_configured"]
    assert "pairing" not in out["switch_off"] and "paired_by_meaning" not in out["switch_off"]
    assert len(out["switch_off"]["provisional"]) == 1 and out["switch_off"]["contested"] == []     # a second item
    assert models[0].calls == 0 and models[1].calls == 0 and judge == []


def test_semantic_memory_off_means_pairing_off_whatever_its_own_switch_says(db, models, judge):
    assert semantic.get_config(db).contradictions is True and semantic.get_config(db).enabled is False
    known(db)
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert "pairing" not in out and len(out["provisional"]) == 1 and models[0].calls == 0


@pytest.mark.parametrize("break_it, reason", [
    (lambda m: setattr(m[0], "fail", InferenceError("unavailable", "cannot connect")), "unavailable"),
    (lambda m: setattr(m[1], "fail", InferenceError("timeout", "too slow")), "timeout"),
    (lambda m: setattr(m[0], "fail", OSError("no route")), "OSError"),
])
def test_a_model_that_fails_leaves_the_words_to_decide_and_says_why(db, models, judge, break_it, reason):
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    break_it(models)
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert out["pairing"]["mode"] == "lexical" and reason in out["pairing"]["reason"]
    assert out["paired_by_meaning"] == [] and len(out["provisional"]) == 1 and out["contested"] == []
    assert knowledge.row(db, kid) == before
    calls = models[0].calls + models[1].calls
    _, again = consolidated(db, [sources("From source to mouth the Varnel covers 401 km.")], T0 + 20,
                            objective="How long is the Varnel?")
    assert "retry_in_seconds" in again["pairing"] and models[0].calls + models[1].calls == calls     # left alone


def test_without_a_reranker_nothing_is_paired(db, monkeypatch, judge):
    embedder = Models("embedder")
    monkeypatch.setattr(semantic, "_gateways", lambda db: (embedder, None))
    on(db)
    known(db)
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert out["pairing"]["mode"] == "lexical" and "needs the reranker" in out["pairing"]["reason"]
    assert embedder.calls == 0 and out["paired_by_meaning"] == [] and len(out["provisional"]) == 1


# ---- a pair, and what it may do ------------------------------------------------------------------------------------------

def test_the_same_fact_in_other_words_is_reported_but_not_merged(db, models, judge):
    """A same-value pair found by meaning does not strengthen the stored item: counting the new sources as its
    independent support on a judgement could inflate it (two properties that share a value). The statement is stored
    as its own item, as in 0.7.0, and the pair is reported."""
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    tid, out = consolidated(db, [sources(RESTATED)], T0 + 10, objective="How long is the Varnel?")
    assert out["paired_by_meaning"] == [{"key": claims.claim_key(RESTATED), "knowledge_id": kid, "relation": "same",
                                         "similarity": 1.0, "score": 0.97, "score_back": 0.97, "merged": False}]
    assert out["pairing"]["mode"] == "semantic" and out["pairing"]["asked"] == 1 and out["pairing"]["proposed"] == 1
    assert out["pairing"]["confirmed"] == 1 and out["pairing"]["refused_by_the_answer_model"] == 0
    assert out["strengthened"] == [] and len(out["provisional"]) == 1 and out["contested"] == []
    assert knowledge.row(db, kid) == before                                     # the stored item is untouched
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 2
    assert judge == [(RESTATED, LENGTH)]
    after = knowledge.row(db, kid)
    assert "paired_by" not in after and "pair" not in after                       # nothing of the pair is stored
    event = db.execute("SELECT detail FROM web_events WHERE task_id=? AND kind='consolidated'", (tid,)).fetchone()
    assert '"paired_by_meaning":1' in event[0]


def test_another_value_in_other_words_is_recorded_as_a_contradiction_and_asked(db, models, judge):
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    _, out = consolidated(db, [sources(CONTRADICTED, published=T0 + 5)], T0 + 10,
                          objective="How long is the Varnel?")            # even a newer statement
    assert [p["relation"] for p in out["paired_by_meaning"]] == ["conflict"]
    after = knowledge.row(db, kid)
    assert after["status"] == before["status"] == "provisional" and after["superseded_by"] is None     # never superseded
    assert after["confidence"] == before["confidence"]                                             # never lowered
    assert len(after["contradicting"]) == 1 and "389 km" in after["contradicting"][0]["reason"]
    assert "matched by meaning" in after["contradicting"][0]["reason"]
    assert out["contested"][0]["knowledge_id"] == kid and out["provisional"] == [] and out["superseded"] == []
    question = db.execute("SELECT question FROM pending WHERE id=?", (out["contested"][0]["pending_id"],)).fetchone()[0]
    assert "Sources now contradict known" in question and "Keep it as knowledge?" in question
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 1
    found = knowledge.query(db, "Varnel river length", T0 + 11)["items"][0]
    assert found["id"] == kid and found["contested"] is True


def test_the_owners_assertion_is_contradicted_by_meaning_never_changed(db, models, judge):
    on(db)
    kid = knowledge.assert_fact(db, LENGTH, T0)["id"]
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    item = knowledge.row(db, kid)
    assert item["status"] == "accepted" and item["confidence"] == 0.5 and item["statement"] == LENGTH
    assert "the owner asserted the stored statement" in item["contradicting"][0]["reason"]
    assert out["paired_by_meaning"][0]["relation"] == "conflict"


def rejected_value(db, kid, value, statement):
    """A row of the stored item's key, stating `value`, that the owner rejected -> its id."""
    key = knowledge.row(db, kid)["key"]
    with store.write(db):
        return db.execute(
            "INSERT INTO web_knowledge(key,version,statement,status,confidence,uncertainty,freshness_class,"
            "first_verified_at,last_verified_at,claim_values,polarity,note,created_at,updated_at) "
            "VALUES(?,?,?,'rejected',0.5,0.5,'stable',?,?,?,1,'owner rejected',?,?)",
            (key, consolidate.next_version(db, key), statement, T0, T0, f'["{value}"]', T0, T0)).lastrowid


@pytest.mark.parametrize("probe, value, relation", [(CONTRADICTED, "389 km", "conflict"), (RESTATED, "412 km", "same")])
def test_a_value_the_owner_rejected_is_not_asked_about_again_when_found_by_meaning(db, models, judge, probe, value,
                                                                                   relation):
    """Review: the lexical path holds a value the owner rejected (`_rejected_before`); a pair found by meaning did
    not, and would ask the owner again about a value already rejected."""
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    rid = rejected_value(db, kid, value, f"The Varnel River is {value.split()[0]} kilometres long.")
    pending = db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0]
    _, out = consolidated(db, [sources(probe)], T0 + 10, objective="How long is the Varnel?")
    pair = out["paired_by_meaning"][0]
    assert pair["relation"] == relation and pair["held"] == f"owner rejected this before (knowledge #{rid})"
    assert [h["reasons"] for h in out["held"]] == [[f"owner rejected this before (knowledge #{rid})"]]
    assert out["contested"] == [] and out["provisional"] == [] and out["strengthened"] == []
    assert knowledge.row(db, kid) == before
    assert db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0] == pending
    assert db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0] == 2        # nothing new written


@pytest.mark.parametrize("statement, why", [
    (OTHER_RIVER, "another subject"),
    (UNNAMED, "no subject named"),
    ("The catchment of the Varnel extends over 9,800 km².", "an area against a length"),
])
def test_what_fails_a_test_of_its_own_is_never_shown_to_the_reranker(db, models, judge, statement, why):
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    _, out = consolidated(db, [sources(statement)], T0 + 10, objective="What is known about rivers?")
    assert out["paired_by_meaning"] == [] and len(out["provisional"]) == 1, why     # stored as knowledge of its own
    assert knowledge.row(db, kid) == before and judge == []
    assert out["pairing"]["asked"] == 0 and models[1].calls == 0                    # the embedding call only


def test_a_neighbouring_property_the_reranker_accepts_is_refused_by_the_answer_model(db, monkeypatch, judge):
    wrong = [Models("embedder"), Models("reranker", near=[("depth", "length"), ("width", "length")])]
    monkeypatch.setattr(semantic, "_gateways", lambda db: tuple(wrong))
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    _, out = consolidated(db, [sources(DEEP)], T0 + 10, objective="How deep is the Varnel?")
    assert judge == [(DEEP, LENGTH)]
    assert out["pairing"]["refused_by_the_answer_model"] == 1 and out["pairing"]["proposed"] == 0
    assert out["paired_by_meaning"] == [] and len(out["provisional"]) == 1 and knowledge.row(db, kid) == before
    # without the confirmation the reranker's mistake becomes a contradiction on good knowledge (measured: 3 of 23)
    semantic.set_config(db, {"pair_check": False}, T0)
    _, out = consolidated(db, [sources("At its mouth the Varnel River is 300 metres wide.")], T0 + 20,
                          objective="How wide is the Varnel?")
    assert [(p["knowledge_id"], p["relation"]) for p in out["paired_by_meaning"]] == [(kid, "conflict")]
    assert len(judge) == 1 and len(knowledge.row(db, kid)["contradicting"]) == 1


def test_no_answer_model_no_pair_and_the_summary_says_so(db, models):
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert out["pairing"]["check"] == "no answer model is configured to confirm a pair"
    assert out["pairing"]["proposed"] == 0 and out["paired_by_meaning"] == []
    assert knowledge.row(db, kid) == before and len(out["provisional"]) == 1


def test_one_direction_of_the_reranker_is_not_enough(db, monkeypatch, judge):
    class OneWay(Models):
        def rerank(self, query, documents, *, purpose, timeout=None):
            self.calls += 1
            return [0.97 if "runs for" in query else 0.2 for _ in documents]      # yes only read from the new one
    monkeypatch.setattr(semantic, "_gateways", lambda db: (Models("embedder"), OneWay("reranker")))
    on(db)
    known(db)
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert out["pairing"]["asked"] == 1 and out["pairing"]["proposed"] == 0 and judge == []


def test_a_cluster_the_words_already_place_is_not_asked_about(db, models, judge):
    on(db)
    kid = known(db)
    other = "The Varnel River is 389 kilometres long."                  # the same key words: the lexical path
    _, out = consolidated(db, [sources(other)], T0 + 10, objective="How long is the Varnel?")
    assert out["pairing"]["asked"] == 0 and out["paired_by_meaning"] == [] and judge == []
    assert out["contested"][0]["knowledge_id"] == kid                   # found, and decided, as before


def test_contested_and_unverified_clusters_are_never_paired(db, models, judge):
    on(db)
    kid = known(db)
    before = knowledge.row(db, kid)
    single = cluster([claim("http://a.example/page", CONTRADICTED, key=claims.claim_key(CONTRADICTED),
                            values=wtext.values(CONTRADICTED))])             # one source: not eligible
    contested = sources("From its source to the sea the Varnel runs for 401 km.", status="contested",
                        contradicting=[{"a": "x", "b": "y", "reason": "different values: 401 km vs 377 km"}])
    _, out = consolidated(db, [single, contested], T0 + 10, objective="How long is the Varnel?")
    assert out["paired_by_meaning"] == [] and knowledge.row(db, kid) == before


def test_the_models_are_asked_before_the_file_is_locked(db, monkeypatch, judge):
    seen = []

    class Watching(Models):
        def embed(self, texts, **kw):
            seen.append(db.in_transaction)
            return super().embed(texts, **kw)

        def rerank(self, query, documents, **kw):
            seen.append(db.in_transaction)
            return super().rerank(query, documents, **kw)
    monkeypatch.setattr(semantic, "_gateways", lambda db: (Watching("embedder"), Watching("reranker")))
    on(db)
    known(db)
    consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert seen and not any(seen)


def test_an_item_forgotten_after_it_was_proposed_is_not_paired(db, models, judge, monkeypatch):
    on(db)
    kid = known(db)
    real = consolidate.propose

    def proposed_then_forgotten(conn, clusters, **kw):
        out = real(conn, clusters, **kw)
        knowledge.archive(conn, kid, T0 + 9)
        return out
    monkeypatch.setattr(consolidate, "propose", proposed_then_forgotten)
    _, out = consolidated(db, [sources(CONTRADICTED)], T0 + 10, objective="How long is the Varnel?")
    assert out["pairing"]["proposed"] == 1 and out["paired_by_meaning"] == [] and len(out["provisional"]) == 1


def test_nothing_of_a_pair_outlives_a_forget(db, models, judge):
    on(db)
    kid = known(db)
    consolidated(db, [sources(RESTATED)], T0 + 10, objective="How long is the Varnel?")
    assert semantic.held(db) == 1                                       # the stored statement's vector, in memory
    knowledge.forget(db, kid, T0 + 20)
    assert semantic.held(db) == 0 and len(knowledge._INDEXES) == 0
    tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {t for t in tables if "vector" in t or "pair" in t or "embedding" in t}
    settings = " ".join(str(v) for (v,) in db.execute("SELECT value FROM meta"))
    assert "Varnel" not in settings
    # The restatement was not merged into the forgotten item: it is knowledge of its own and stays (as in 0.7.0)
    left = db.execute("SELECT id, statement FROM web_knowledge").fetchall()
    assert len(left) == 1 and left[0][0] != kid and left[0][1] == RESTATED


# ---- semantic.pairs on its own -----------------------------------------------------------------------------------------

def rows_of(db):
    return [{"id": i, "statement": s} for i, s in db.execute("SELECT id, statement FROM web_knowledge ORDER BY id")]


def test_pairs_bounds_what_one_consolidation_may_ask(db, models, monkeypatch):
    on(db)
    known(db)
    monkeypatch.setattr(semantic, "PAIR_STATEMENTS", 2)
    new = [f"From its source to the sea the Varnel runs for {300 + n} km." for n in range(5)]
    found, info = semantic.pairs(db, new, rows_of(db))
    assert [bool(f) for f in found] == [True, True, False, False, False] and info["not_asked"] == 3
    monkeypatch.setattr(semantic, "PAIR_STATEMENTS", 24)
    monkeypatch.setattr(semantic, "PAIR_BUDGET", -1.0)
    found, info = semantic.pairs(db, new, rows_of(db))
    assert not any(found) and info["not_asked"] == 5


def test_pairs_applies_the_callers_test_and_the_thresholds(db, models):
    on(db)
    known(db)
    known(db, BASIN, T0 + 1)
    rows = rows_of(db)
    found, info = semantic.pairs(db, [RESTATED], rows, admit=lambda n, row: "basin" not in row["statement"])
    assert [c["id"] for c in found[0]] == [rows[0]["id"]] and info["asked"] == 1 and info["proposed"] == 1
    assert semantic.pairs(db, [RESTATED], rows, admit=lambda n, row: False)[0] == [[]]
    semantic.set_config(db, {"pair_floor": 0.99}, T0)
    assert semantic.pairs(db, [RESTATED], rows)[0] == [[]]
    # identical vectors only: a cosine of exactly 1.0 is not reachable on every platform (it was 0.99999… on x86 CI)
    semantic.set_config(db, {"pair_floor": 0.8, "pair_similarity": 0.9999}, T0)
    assert [c["id"] for c in semantic.pairs(db, [RESTATED], rows)[0][0]] == [rows[0]["id"]]
    swept, _ = semantic.pairs(db, [RESTATED], rows, sweep=True)
    assert {c["id"] for c in swept[0]} == {r["id"] for r in rows} and all("score_back" in c for c in swept[0])


def test_pairs_sends_the_reranker_masked_text_and_the_pair_instruction(db, models):
    on(db)
    known(db)
    semantic.pairs(db, [CONTRADICTED], rows_of(db))
    read = [t for t in models[1].seen]
    assert any(semantic.PAIR_INSTRUCTION in t and "the Varnel runs for # km" in t for t in read)
    # each side is read in the unit the other wrote it in: forward the stored statement in the new one's km, back
    # the new statement in the stored one's kilometres
    assert any("The Varnel River is # km long." in t for t in read)
    assert any(semantic.PAIR_INSTRUCTION in t and "The Varnel River is # kilometres long." in t for t in read)
    assert any("the Varnel runs for # kilometres." in t for t in read)
    assert not any("389" in t or "412" in t for t in read)
    assert CONTRADICTED in models[0].seen                 # the embedding model reads the statement as it is


@pytest.mark.parametrize("name, bad", [("contradictions", "yes"), ("pair_check", 1), ("pair_floor", 1.5),
                                       ("pair_similarity", -0.1), ("pair_floor", True), ("pair_similarity", "0.5")])
def test_pairing_settings_are_validated(db, name, bad):
    with pytest.raises(ValidationError):
        semantic.set_config(db, {name: bad}, T0)


def test_settings_saved_before_the_switch_existed_take_the_defaults():
    old = semantic.SemanticConfig.from_json('{"enabled": true, "floor": 0.05}')
    assert (old.contradictions, old.pair_check, old.pair_floor, old.pair_similarity) == (True, True, 0.8, 0.6)


def test_status_says_what_pairing_does(db, models):
    assert semantic.status(db)["contradictions"].startswith("off:")
    on(db)
    assert "when the answer model confirms the pair" in semantic.status(db)["contradictions"]
    semantic.set_config(db, {"pair_check": False}, T0)
    assert "pair_check is off" in semantic.status(db)["contradictions"]
    semantic.set_config(db, {"contradictions": False}, T0)
    assert semantic.status(db)["contradictions"].startswith("off:")


# ---- the answer model's confirmation -------------------------------------------------------------------------------------

class Answering:
    """A stand-in answer-model gateway for consolidate._pair_judge."""

    def __init__(self, reply=None, error=None, reasoning=True, priority=0):
        self.cfg = iconfig.InferenceConfig(base_url="http://127.0.0.1:1/v1", model="m", reasoning=reasoning,
                                           background_priority=priority)
        self.reply, self.error, self.calls = reply, error, []

    def structured(self, messages, schema, **kw):
        self.calls.append((messages, schema, kw))
        if self.error:
            raise self.error
        return self.reply, []


def test_the_confirmation_is_one_private_masked_question_per_pair(db):
    gateway = Answering({"same_property": True}, priority=10)
    judge, why = consolidate._pair_judge(db, gateway=gateway)
    assert why is None and judge(CONTRADICTED, LENGTH) is True
    messages, schema, kw = gateway.calls[0]
    assert schema == consolidate.PAIR_CHECK_SCHEMA and messages[0]["content"] == consolidate.PAIR_CHECK_SYSTEM
    assert messages[1]["private"] is True
    assert "Statement A: From its source to the sea the Varnel runs for # km." in messages[1]["content"]
    assert "Statement B: The Varnel River is # km long." in messages[1]["content"]       # in A's unit
    assert "389" not in messages[1]["content"] and "412" not in messages[1]["content"]
    assert kw["purpose"] == "memory_pair_check" and kw["temperature"] == 0.0 and kw["priority"] == 10
    assert 0 < kw["timeout"] <= consolidate.PAIR_CHECK_TIMEOUT


@pytest.mark.parametrize("gateway, verdict", [
    (Answering({"same_property": False}), False),
    (Answering({"same_property": "yes"}), None),
    (Answering(["not", "an", "object"]), None),
    (Answering(error=InferenceError("unavailable", "down")), None),
])
def test_only_a_plain_yes_makes_a_pair(db, gateway, verdict):
    judge, _ = consolidate._pair_judge(db, gateway=gateway)
    assert judge(CONTRADICTED, LENGTH) is verdict


def test_no_model_or_reasoning_off_gives_no_judge(db):
    assert consolidate._pair_judge(db) == (None, "no answer model is configured to confirm a pair")
    judge, why = consolidate._pair_judge(db, gateway=Answering({"same_property": True}, reasoning=False))
    assert judge is None and "reasoning is off" in why


def test_the_confirmations_of_one_consolidation_share_one_deadline(db, monkeypatch):
    gateway = Answering({"same_property": True})
    monkeypatch.setattr(consolidate, "PAIR_CHECK_TIMEOUT", 0.2)
    judge, _ = consolidate._pair_judge(db, gateway=gateway)
    assert judge(CONTRADICTED, LENGTH) is None and gateway.calls == []        # under half a second left: not asked


# ---- the owner's switch ------------------------------------------------------------------------------------------------

def test_the_cli_switch_and_what_web_memory_shows(tmp_path, capsys):
    import json
    from neuroscifi.cli import main

    def nsf(*argv):
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, (json.loads(captured.out) if code == 0 else captured.err)
    path = str(tmp_path / "cli.sqlite")
    assert nsf("init", "--db", path)[0] == 0
    code, out = nsf("web", "memory", "--db", path)
    assert code == 0 and out["settings"]["contradictions"] is True and out["contradictions"].startswith("off:")
    code, out = nsf("web", "memory", "config", "--no-contradictions", "--db", path)
    assert code == 0 and out["settings"]["contradictions"] is False
    code, out = nsf("web", "memory", "config", "--contradictions", "--set", "pair_floor=0.9", "--db", path)
    assert code == 0 and out["settings"]["contradictions"] is True and out["settings"]["pair_floor"] == 0.9
    code, err = nsf("web", "memory", "config", "--set", "pair_floor=2", "--db", path)
    assert code != 0 and "pair_floor must be a number between 0 and 1" in err


# ---- a longer name, a stated alias, a binary factor (remaining-items round, lane K) --------------------------------------

@pytest.mark.parametrize("longer, shorter", [
    ("The village of Pemberly Cross, Ohio has 1,100 residents.", "The village of Pemberly Cross has 3,000 residents."),
    ("Pemberly, Ohio has 1,100 residents.", "In Pemberly there are 3,000 residents."),
    ("The Orbix Cloud gateway needs 16 GB of memory.", "The Orbix server needs at least 4 GB of memory."),
    ("Little Lake Arn covers 3 square kilometres.", "Lake Arn covers 54 square kilometres."),
    ("The Kelder Bridge II is 990 metres long.", "The Kelder Bridge is 1,340 metres long."),
    ("Hollin & Reeve Asia employs 310 people.", "Hollin & Reeve employs about 3,200 people."),
    ("The Bank of Tessaly employs 4,100 people.", "Tessaly employs 4,100 civil servants."),
])
def test_a_longer_name_that_contains_the_other_names_another_subject(longer, shorter):
    """'Paris, Texas' is not Paris: the longer name carries a word of its own that says which thing it is."""
    assert consolidate._same_subject(longer, shorter) is False
    assert consolidate._same_subject(shorter, longer) is False


@pytest.mark.parametrize("a, b", [
    (RESTATED, LENGTH),                                                            # "River": what kind of thing
    ("Avarel is 11,400 feet high.", "Mount Avarel rises 3,912 metres above sea level."),
    ("Visitors climb 214 steps to reach the gallery of the Tolvane Lighthouse.",
     "A staircase of 214 steps leads up the Tolvane tower."),
    ("The Marrow Valley railway runs 87 kilometres between Hesket and Pell.",     # other names, not qualifiers
     "Trains on the Marrow Valley line cover 87 km from end to end."),
    ("Hollin & Reeve Ltd employs about 3,200 people.", "Hollin & Reeve employs about 3,200 people."),
])
def test_a_word_that_only_says_what_kind_of_thing_it_is_does_not_make_another_subject(a, b):
    assert consolidate._same_subject(a, b) is True and consolidate._same_subject(b, a) is True


ALIAS_SOURCE = "The Varnel River, also known as the Old Grey, drains a basin of 9,800 square kilometres."


@pytest.mark.parametrize("statement, name, alias", [
    (ALIAS_SOURCE, "Varnel River", "Old Grey"),
    ("The Meriden Hall (formerly the Corn Exchange) seats 900 people.", "Meriden Hall", "Corn Exchange"),
    ("The Brask Line, previously the Northern Spur, is 44 km long.", "Brask Line", "Northern Spur"),
    ("Hollin & Reeve, formerly known as Hollin Brothers, employs 3,200 people.", "Hollin & Reeve", "Hollin Brothers"),
    ("Lumen-7 (a.k.a. Skyeye) orbits at 705 km.", "Lumen-7", "Skyeye"),
])
def test_an_alias_is_what_a_statement_itself_says_a_name_is_also_called(statement, name, alias):
    found = consolidate._aliases([{"id": 7, "statement": statement}])
    assert [(a["name"], a["as"], a["knowledge_id"]) for a in found] == [(name, alias, 7)]


@pytest.mark.parametrize("statement", [
    "The largest city on the gulf coast, Tessaly, has 248,000 residents.",       # a description is no alias
    "The Varnel River, the longest in the region, is 412 kilometres long.",
    "The Varnel River and the Ostrel River are 412 and 520 kilometres long.",
    "The Varnel River was formerly navigable.",                                    # no name after the word
])
def test_nothing_else_is_an_alias(statement):
    assert consolidate._aliases([{"id": 1, "statement": statement}]) == []


def test_a_name_and_its_stated_alias_name_one_subject():
    aliases = consolidate._aliases([{"id": 3, "statement": ALIAS_SOURCE}])
    used = []
    new = "From its source to the sea the Old Grey runs for 389 km."
    assert consolidate._same_subject(new, LENGTH) is False                         # without the alias: two names
    assert consolidate._same_subject(new, LENGTH, aliases=aliases, used=used) is True
    assert [u["knowledge_id"] for u in used] == [3]
    assert consolidate._read_aliases(aliases)(new) == "From its source to the sea the Varnel River runs for 389 km."
    # an alias does not make a longer name the same: "Old Grey Bridge" is another subject
    assert consolidate._same_subject("The Old Grey Bridge is 120 metres long.", LENGTH, aliases=aliases) is False


def test_a_contradiction_found_through_a_stated_alias_records_the_statement_that_gives_it(db, models, judge):
    on(db)
    kid = known(db)
    source = known(db, ALIAS_SOURCE)
    new = "From its source to the sea the Old Grey runs for 389 km."
    _, out = consolidated(db, [sources(new)], T0 + 10, objective="How long is the Old Grey?")
    assert [(p["knowledge_id"], p["relation"]) for p in out["paired_by_meaning"]] == [(kid, "conflict")]
    assert out["paired_by_meaning"][0]["alias"] == [{"name": "Varnel River", "as": "Old Grey", "knowledge_id": source}]
    reason = knowledge.row(db, kid)["contradicting"][0]["reason"]
    assert f"Old Grey is Varnel River, as knowledge #{source} states" in reason
    # the answer model reads the alias as the name it stands for
    assert judge[-1] == ("From its source to the sea the Varnel River runs for 389 km.", LENGTH)


def test_an_alias_whose_statement_is_forgotten_names_nothing(db, models, judge):
    on(db)
    known(db)
    source = known(db, ALIAS_SOURCE)
    knowledge.forget(db, source, T0 + 5)
    _, out = consolidated(db, [sources("From its source to the sea the Old Grey runs for 389 km.")], T0 + 10,
                          objective="How long is the Old Grey?")
    assert out["paired_by_meaning"] == [] and len(out["provisional"]) == 1


@pytest.mark.parametrize("new, stored, refused", [
    (["8192 mb"], ["8 gb"], True),          # 8 GB in binary megabytes, or 8.192 GB: cannot be told
    (["4 gb"], ["4096 mb"], True),
    (["1 tb"], ["1099.511627776 gb"], True),
    (["8100 mb"], ["8 gb"], False),         # a plain disagreement is a contradiction like any other
    (["8000 mb"], ["8 gb"], False),
])
def test_data_sizes_off_by_the_binary_factor_are_not_paired(new, stored, refused):
    assert consolidate._binary_factor(new, stored) is refused
    item = {"statement": "The Quorra server needs 8 GB of memory.", "claim_values": stored, "polarity": 1}
    why = consolidate._admissible("The Quorra server needs 8 GB of memory.", new, 1, item)
    assert (why is not None and "binary" in why) is refused
