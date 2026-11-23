"""Semantic memory retrieval (webcortex/semantic.py) with stand-in memory models: off means the lexical ranking
unchanged; on, the ranking follows meaning, a statement under the floor is not returned, any failure falls back to
the lexical ranking and says so, and no vector outlives the statement it was made from."""
import pytest

from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import InferenceError
from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import knowledge, semantic, state

T0 = 1_795_200_000.0
FACTS = ("By default, the Orbix server listens on port 7420 for client connections.",
         "Orbix is distributed under the Mozilla Public License 2.0.",
         "The Tolvane Lighthouse was built in 1874 by the Ostra Harbour Board.",
         "Imre Solvang received the Lindqvist Medal in 2012 for work on protein folding.")
# what each text is "about": a stand-in for meaning, one axis per subject matter
AXES = ("port", "licence", "lighthouse", "prize")
ABOUT = {"socket": "port", "port 7420": "port", "legal terms": "licence", "Public License": "licence",
         "Lighthouse": "lighthouse", "beacon": "lighthouse", "Medal": "prize", "honoured": "prize"}


def axis(text):
    return next((a for word, a in ABOUT.items() if word in text), None)


class Models:
    """An embedding model and a reranker that know ABOUT; they record every text they are shown."""

    def __init__(self, model):
        self.cfg = iconfig.InferenceConfig(base_url="http://127.0.0.1:1/v1", model=model)
        self.seen, self.calls, self.fail = [], 0, None

    def embed(self, texts, *, purpose, timeout=None, input_type=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        self.seen += texts
        return [[1.0 if axis(t) == a else 0.05 for a in AXES] for t in texts]

    def rerank(self, query, documents, *, purpose, timeout=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        self.seen += [query, *documents]
        return [0.97 if axis(query) and axis(query) == axis(d) else 0.001 for d in documents]


@pytest.fixture(autouse=True)
def fresh_vectors():
    semantic.clear()
    yield
    semantic.clear()


@pytest.fixture
def db(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    state.set_config(db, {"enabled": True}, T0)
    for n, fact in enumerate(FACTS):
        knowledge.assert_fact(db, fact, T0 + n)
    return db


@pytest.fixture
def models(monkeypatch):
    pair = [Models("embedder"), Models("reranker")]
    monkeypatch.setattr(semantic, "_gateways", lambda db: tuple(pair))
    return pair


def statements(result):
    return [i["statement"] for i in result["items"]]


def test_off_by_default_the_lexical_ranking_is_unchanged_and_no_model_is_asked(db, models):
    out = knowledge.query(db, "Orbix port", T0 + 10)
    assert statements(out) == [FACTS[0], FACTS[1]] and out["retrieval"] == {"mode": "lexical"}
    assert knowledge.query(db, "which socket does it accept traffic on", T0 + 10)["items"] == []
    assert models[0].calls == 0 and models[1].calls == 0


def test_on_a_paraphrase_finds_its_fact_and_unrelated_statements_are_not_returned(db, models):
    semantic.set_config(db, {"enabled": True}, T0)
    out = knowledge.query(db, "which socket does it accept traffic on", T0 + 10)
    assert statements(out) == [FACTS[0]]
    assert out["retrieval"]["mode"] == "semantic" and out["retrieval"]["ranked_by"] == "rerank"
    assert out["retrieval"]["dropped_below_floor"] == 3 and out["items"][0]["score"] == 0.97
    assert out["matched"] == 1 and out["known"] == 1


def test_a_question_about_nothing_in_memory_returns_nothing(db, models):
    """Lexically "port" would match the Orbix fact; the reranker says no statement is about PostgreSQL."""
    semantic.set_config(db, {"enabled": True}, T0)
    assert knowledge.query(db, "the default settings of PostgreSQL", T0 + 10)["items"] == []
    semantic.set_config(db, {"enabled": False}, T0)
    assert statements(knowledge.query(db, "the default port of PostgreSQL", T0 + 10)) == [FACTS[0]]


def test_the_best_lexical_hits_are_always_among_the_candidates(db, models):
    """A rare exact word the embedding does not place near the question still reaches the reranker."""
    semantic.set_config(db, {"enabled": True, "candidates": 1}, T0)
    knowledge.query(db, "Lindqvist socket", T0 + 10)          # nearest by vector: the port fact; lexical: Lindqvist
    shown = [t for t in models[1].seen if "Lindqvist Medal" in t]
    assert shown, "the lexical hit was not sent to the reranker"


def test_without_a_reranker_the_order_is_by_similarity_and_nothing_is_cut(db, models, monkeypatch):
    monkeypatch.setattr(semantic, "_gateways", lambda db: (models[0], None))
    semantic.set_config(db, {"enabled": True}, T0)
    out = knowledge.query(db, "which beacon is it", T0 + 10)
    assert statements(out)[0] == FACTS[2] and len(out["items"]) == 4
    assert out["retrieval"]["ranked_by"] == "embedding" and "floor" not in out["retrieval"]


@pytest.mark.parametrize("failing", [0, 1])
def test_a_failing_memory_model_falls_back_to_the_lexical_ranking_and_says_why(db, models, failing):
    semantic.set_config(db, {"enabled": True}, T0)
    models[failing].fail = InferenceError("unavailable", "cannot connect: Connection refused")
    out = knowledge.query(db, "Orbix port", T0 + 10)
    assert statements(out) == [FACTS[0], FACTS[1]]                  # exactly the lexical result
    assert out["retrieval"]["mode"] == "lexical" and "unavailable" in out["retrieval"]["reason"]


def test_a_failed_model_is_left_alone_for_a_while_then_tried_again(db, models, monkeypatch):
    """Live: with the reranker's port closed every lookup waited 1.5 s on connection retries. A failure now costs
    one call; lookups in the next RETRY_AFTER seconds are lexical without asking; then the models are tried again."""
    semantic.set_config(db, {"enabled": True}, T0)
    clock = [1000.0]
    monkeypatch.setattr(semantic.time, "monotonic", lambda: clock[0])
    models[1].fail = InferenceError("unavailable", "cannot connect: Connection refused")
    knowledge.query(db, "Orbix port", T0 + 10)
    calls = (models[0].calls, models[1].calls)
    out = knowledge.query(db, "Orbix licence", T0 + 11)
    assert (models[0].calls, models[1].calls) == calls                       # not asked again
    assert out["retrieval"]["mode"] == "lexical" and out["retrieval"]["retry_in_seconds"] == semantic.RETRY_AFTER
    models[1].fail = None
    clock[0] += semantic.RETRY_AFTER + 1
    out = knowledge.query(db, "which socket does it accept traffic on", T0 + 12)
    assert out["retrieval"]["mode"] == "semantic" and statements(out) == [FACTS[0]]


def test_memory_model_gateways_never_retry(tmp_path):
    db = store.create(tmp_path / "g.sqlite", T0)
    iconfig.set_config(db, {"base_url": "http://127.0.0.1:1/v1", "model": "e"}, T0, provider="embed")
    embedder, reranker = semantic._gateways(db)
    assert embedder.cfg.max_retries == 0 and reranker is None
    assert iconfig.get_config(db, "embed").max_retries == 2                  # the stored setting is not changed


def test_on_without_an_embedding_endpoint_is_lexical_with_the_command_that_sets_one(db):
    semantic.set_config(db, {"enabled": True}, T0)
    out = knowledge.query(db, "Orbix port", T0 + 10)
    assert statements(out) == [FACTS[0], FACTS[1]] and "--embed-url" in out["retrieval"]["reason"]


def test_a_forgotten_statement_leaves_no_vector_and_is_never_shown_to_a_model_again(db, models):
    semantic.set_config(db, {"enabled": True}, T0)
    knowledge.query(db, "which beacon is it", T0 + 10)
    held = semantic._vectors(db, "embedder")
    assert set(held.by_text) == set(FACTS)
    target = next(r["id"] for r in knowledge.rows(db, "1") if "Lighthouse" in r["statement"])
    knowledge.forget(db, target, T0 + 20)
    for m in models:
        m.seen.clear()
    out = knowledge.query(db, "which beacon is it", T0 + 30)
    assert FACTS[2] not in held.by_text and len(held.by_text) == 3 and out["items"] == []
    assert not any("Tolvane" in t for m in models for t in m.seen)


def test_the_same_question_over_the_same_memory_asks_the_models_once(db, models):
    semantic.set_config(db, {"enabled": True}, T0)
    first = knowledge.query(db, "which socket does it accept traffic on", T0 + 10)
    calls = (models[0].calls, models[1].calls)
    again = knowledge.query(db, "which socket does it accept traffic on", T0 + 11)
    assert statements(again) == statements(first) and (models[0].calls, models[1].calls) == calls
    assert again["retrieval"]["cached"] is True
    knowledge.assert_fact(db, "Orbix accepts TLS connections on port 7421.", T0 + 12)     # memory changed
    knowledge.query(db, "which socket does it accept traffic on", T0 + 13)
    assert models[1].calls == calls[1] + 1


def test_a_new_statement_is_embedded_once_and_the_others_are_not_embedded_again(db, models):
    semantic.set_config(db, {"enabled": True}, T0)
    knowledge.query(db, "anything", T0 + 10)
    models[0].seen.clear()
    knowledge.assert_fact(db, "Kestrel is honoured as a language.", T0 + 12)
    knowledge.query(db, "something else", T0 + 13)
    assert [t for t in models[0].seen if t in FACTS] == []
    assert "Kestrel is honoured as a language." in models[0].seen


def test_statements_reach_the_reranker_in_its_own_prompt_form(db, models):
    semantic.set_config(db, {"enabled": True}, T0)
    knowledge.query(db, "which socket", T0 + 10)
    query = next(t for t in models[1].seen if "<Query>: which socket" in t)
    assert query.startswith("<|im_start|>system\nJudge whether the Document") and semantic.INSTRUCTION in query
    assert any(t.startswith("<Document>: By default, the Orbix") and t.endswith("</think>\n\n") for t in models[1].seen)
    semantic.set_config(db, {"rerank_template": "plain"}, T0)
    models[1].seen.clear()
    knowledge.query(db, "which socket now", T0 + 11)
    assert "which socket now" in models[1].seen and FACTS[0] in models[1].seen


def test_settings_are_validated_and_stored_per_individual(db):
    assert semantic.get_config(db) == semantic.SemanticConfig()
    semantic.set_config(db, {"enabled": True, "floor": 0.1, "candidates": 20}, T0)
    assert semantic.get_config(db).floor == 0.1 and semantic.get_config(db).candidates == 20
    for bad in ({"floor": 2}, {"candidates": 0}, {"enabled": "yes"}, {"rerank_template": "x"}, {"nope": 1}, {}):
        with pytest.raises(ValidationError):
            semantic.set_config(db, bad, T0)


def test_status_names_the_effect_and_the_unset_endpoints(db):
    out = semantic.status(db)
    assert out["enabled"] is False and out["effect"].startswith("off")
    assert out["providers"] == {"embed": {"configured": False}, "rerank": {"configured": False}}
    semantic.set_config(db, {"enabled": True}, T0)
    assert semantic.status(db)["effect"] == "on, but no embedding endpoint: lexical ranking"
