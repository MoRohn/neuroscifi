"""intent.assess / plan / refine: deterministic, explainable classification and query planning."""
import json
import re

import pytest

from neuroscifi.webcortex import intent
from neuroscifi.webcortex.types import FRESHNESS_CLASSES, MODES, QUERY_KINDS, Query, QueryPlan, SearchOutcome, \
    SearchResult

NOW = 1795132800.0   # 2026-11-20T00:00:00Z
DAY = 86400.0
FRESH = {"status": "accepted", "stale": False, "contested": False}
ANY = object()

S1 = "What are the most important developments in open-source AI inference this week?"
S2 = ("Research recent evidence on dendritic computation and determine whether it affects NeuroSciFi's learning "
      "architecture.")
S3 = "Check whether the libraries we use have newer stable releases or important security advisories."
S4 = ("Monitor research related to predictive coding and update my research knowledge when meaningful developments "
      "occur.")
S5 = "These two sources disagree. Determine what can actually be established."

# (question, known, needs_web, mode, freshness_class, recency_window)
TABLE = [
    (S1, (), True, "quick_lookup", "breaking", 7 * DAY),
    (S2, (), True, "scientific", "scientific", 90 * DAY),
    (S3, (), True, "technical", "release", None),
    (S4, (), True, "continuous", "scientific", None),
    (S5, (), True, "fact_verification", "stable", None),
    ("What is 2+2?", [{"statement": "2+2 equals 4", **FRESH}], False, "quick_lookup", "stable", None),
    ("What is 2+2?", (), False, "quick_lookup", "stable", None),
    ("Define entropy", [{"statement": "Entropy measures the number of microstates", **FRESH}], False,
     "quick_lookup", "stable", None),
    ("Define entropy", (), True, "quick_lookup", "stable", None),
    ("Define entropy", [{"statement": "Entropy measures disorder", "status": "accepted", "stale": True,
                         "contested": False}], True, "quick_lookup", "stable", None),
    ("What is the capital of France?", [{"statement": "Paris is the capital of France", **FRESH}], False,
     "quick_lookup", "stable", None),
    ("What is the capital of France?", [{"statement": "Paris is the capital of France", "status": "accepted",
                                         "stale": False, "contested": True}], True, "quick_lookup", "stable", None),
    ("Who won the election today?", (), True, "quick_lookup", "breaking", DAY),
    ("Is it true that coffee causes dehydration?", (), True, "fact_verification", "stable", None),
    ("Fact check: the Eiffel Tower grows in summer", (), True, "fact_verification", "stable", None),
    ("Compare Rust and Go for web servers", (), True, "comparative", "stable", None),
    ("What is the difference between TCP and UDP?", (), True, "comparative", "stable", None),
    ("Which is better, Postgres or MySQL?", (), True, "comparative", "stable", None),
    ("Is PyTorch better than TensorFlow for research?", (), True, "comparative", "stable", None),
    ("Notify me when numpy releases a new version", (), True, "continuous", "release", None),
    ("Keep track of developments in quantum error correction", (), True, "continuous", "scientific", None),
    ("What changed in the Python 3.14 changelog since last month?", (), True, "change_detection", "release",
     31 * DAY),
    ("Investigate the history of the transistor in depth", (), True, "deep_research", "stable", None),
    ("Research neuroscience of sleep deeply", (), True, "deep_research", "scientific", None),
    ("What is the latest version of numpy?", (), True, "technical", "release", None),
    ("Are there any CVE advisories for zstandard==0.25.0?", (), True, "technical", "release", None),
    ("Find peer-reviewed studies on intermittent fasting", (), True, "scientific", "scientific", None),
    ("What did arXiv preprints say about sparse autoencoders this month?", (), True, "scientific", "scientific",
     31 * DAY),
    ("Summarize the news from November", (), True, "quick_lookup", "volatile", 31 * DAY),
    ("Who is Ada Lovelace?", [{"statement": "Ada Lovelace wrote the first published algorithm", **FRESH}], False,
     "quick_lookup", "stable", None),
    ("Tell me about KubernetesOperatorSDK", [{"statement": "Kubernetes schedules containers", **FRESH}], True,
     "quick_lookup", "stable", None),
    ("calculate 12 * (3 + 4)", (), False, "quick_lookup", "stable", None),
    ("What happened in AI in 2026?", (), True, "quick_lookup", "volatile", ANY),
    ("Is the New York subway open?", [{"statement": "The New York subway runs 24/7", **FRESH}], False,
     "quick_lookup", "stable", None),
]


@pytest.mark.parametrize("question,known,needs_web,mode,freshness,window", TABLE,
                         ids=[f"{i:02d}-{t[0][:40]}" for i, t in enumerate(TABLE)])
def test_classification_table(question, known, needs_web, mode, freshness, window):
    a = intent.assess(question, now=NOW, known=known)
    assert a["needs_web"] is needs_web, a["reasons"]
    assert a["mode"] == mode, a["why"]
    assert a["freshness_class"] == freshness, a["why"]
    if window is not ANY:
        assert a["recency_window"] == window, a["why"]
    assert a["mode"] in MODES and a["freshness_class"] in FRESHNESS_CLASSES
    assert set(a["kinds"]) <= set(QUERY_KINDS) and a["kinds"][-1] == "web"
    assert a["reasons"] and all(isinstance(r, str) and r for r in a["reasons"])
    assert {"mode", "freshness_class", "kinds", "recency_window"} <= set(a["why"])
    json.dumps(a)
    assert intent.assess(question, now=NOW, known=known) == a       # deterministic


def test_table_size():
    assert len(TABLE) >= 25


def test_scenario_reasons_and_kinds():
    a = intent.assess(S1, now=NOW)
    assert "recency requested: this week" in a["reasons"] and a["kinds"] == ["news", "web"]
    a = intent.assess(S2, now=NOW)
    assert a["kinds"] == ["academic", "web"] and "unfamiliar entity: NeuroSciFi" in a["reasons"]
    assert any(r.startswith("scientific question") for r in a["reasons"])
    a = intent.assess(S3, now=NOW)
    assert a["kinds"] == ["technical", "web"] and any("no packages listed" in n for n in a["notes"])
    a = intent.assess(S4, now=NOW)
    assert "continuous monitoring requested" in a["reasons"] and "academic" in a["kinds"]
    a = intent.assess(S5, now=NOW)
    assert any(r.startswith("claim requires verification") for r in a["reasons"])


def test_needs_web_false_explains_itself():
    a = intent.assess("Define entropy", now=NOW, known=[{"statement": "Entropy measures disorder", **FRESH}])
    assert a["reasons"] == ["stable definitional question answered by 1 fresh, uncontested knowledge item(s)"]
    a = intent.assess("What is 2+2?", now=NOW)
    assert a["reasons"] == ["pure arithmetic: computed locally, no web needed"]


def test_rejected_or_irrelevant_knowledge_does_not_count():
    known = [{"statement": "Paris is the capital of France", "status": "rejected", "stale": False,
              "contested": False}]
    assert intent.assess("What is the capital of France?", now=NOW, known=known)["needs_web"] is True
    known = [{"statement": "Bananas are yellow", **FRESH}]
    assert intent.assess("What is the capital of France?", now=NOW, known=known)["needs_web"] is True


def test_stale_and_contested_reasons():
    known = [{"statement": "numpy 2.3.5 is the latest release", "status": "accepted", "stale": True,
              "contested": False}]
    a = intent.assess("Which numpy release is current?", now=NOW, known=known)
    assert a["needs_web"] and any(r.startswith("knowledge stale: numpy 2.3.5") for r in a["reasons"])


def test_extraction_fields():
    a = intent.assess("Is it true that the Great Wall of China is visible from space?", now=NOW)
    assert a["claim"] == "the Great Wall of China is visible from space"
    assert a["entities"] == ["Great Wall", "China"]
    a = intent.assess("Should I use PyTorch vs JAX for research workloads?", now=NOW)
    assert a["comparison"] == ["PyTorch", "JAX"]
    a = intent.assess("Compare open-webSearch and SearXNG", now=NOW)
    assert a["comparison"] == ["open-webSearch", "SearXNG"]
    a = intent.assess("Read https://example.org/a.html and https://arxiv.org/abs/2611.01234, then summarise",
                      now=NOW)
    assert a["urls"] == ["https://example.org/a.html", "https://arxiv.org/abs/2611.01234"]
    assert any(r.startswith("URL given: https://example.org/a.html") for r in a["reasons"])
    a = intent.assess("Check pypi:requests, npm:@scope/pkg and github:pallets/flask plus zstandard==0.25.0",
                      now=NOW)
    assert a["packages"] == [{"ecosystem": "PyPI", "name": "zstandard", "version": "0.25.0"},
                             {"ecosystem": "PyPI", "name": "requests", "version": None},
                             {"ecosystem": "npm", "name": "@scope/pkg", "version": None},
                             {"ecosystem": "github", "name": "pallets/flask", "version": None}]


def test_entities():
    assert intent.extract_entities("What does NeuroSciFi's executive do with PyTorch and the open-websearch "
                                   "daemon on the DGX Spark?") == ["NeuroSciFi", "PyTorch", "open-websearch",
                                                                    "DGX Spark"]
    assert intent.extract_entities("What are open-source, peer-reviewed results?") == []
    assert intent.extract_entities('Find "predictive coding" papers by Karl Friston') == ["predictive coding",
                                                                                         "Karl Friston"]


def test_sites_and_language():
    a = intent.assess("Latest LLM quantization papers on arXiv and nature.com", now=NOW)
    assert a["sites"] == ["nature.com", "arxiv.org"]
    a = intent.assess("Как работает трансформер?", now=NOW)
    assert a["language"] == "ru" and any("nothing is translated" in n for n in a["notes"])
    a = intent.assess("最新的人工智能新闻是什么？", now=NOW)
    assert a["language"] == "zh" and a["needs_web"]
    assert intent.assess("config.py and setup.sh", now=NOW)["sites"] == []


def test_month_and_year_windows():
    a = intent.assess("What happened in AI in 2026?", now=NOW)
    assert a["recency_window"] == NOW - 1767225600.0           # since 2026-01-01
    a = intent.assess("What did the Fed decide in March?", now=NOW)
    assert a["recency_window"] == NOW - 1772323200.0           # since 2026-03-01
    assert intent.assess("May I ask what a neuron is?", now=NOW)["recency_window"] is None


# ---- plan ----------------------------------------------------------------------------------------------------------

def texts(plan, kind=None):
    return [q.text for q in plan.queries if kind is None or q.kind == kind]


def test_plan_breaking_news():
    a = intent.assess(S1, now=NOW)
    p = intent.plan(S1, a, now=NOW)
    assert [(q.text, q.kind) for q in p.queries] == [("open-source AI inference", "news"),
                                                     ("open-source AI inference 2026", "news")]
    assert all(q.since == NOW - 7 * DAY for q in p.queries)
    assert p.queries[0].purpose == "question" and p.queries[1].purpose.startswith("expansion: year")


def test_plan_scientific_decomposes_and_routes_academic():
    a = intent.assess(S2, now=NOW)
    p = intent.plan(S2, a, now=NOW)
    assert p.subquestions == ("Research recent evidence on dendritic computation", "determine whether it affects")
    # the self-referential clause is local context (F8): it is answered from local docs, never searched
    assert texts(p)[:2] == ["dendritic computation", "dendritic computation"]
    assert "dendritic computation" in texts(p, "academic") and "dendritic computation review" in texts(p, "academic")
    assert all(q.since == NOW - 90 * DAY for q in p.queries)
    assert not any("neuroscifi" in q.text.lower() or "architecture" in q.text.lower() for q in p.queries)
    assert any(n.startswith("local context: 1 reference") for n in p.notes)
    assert p.max_sources == 12


@pytest.mark.parametrize("question", [
    S2, S2.replace("NeuroSciFi's", "our"), "How does dendritic computation compare to our system's learning rule?",
    "Compare predictive coding with NeuroSciFi", "Is it true that NeuroSciFi uses dendritic computation?",
    "Which of the libraries we use depend on numpy?", "What does Quillbase say about dendritic spikes?"])
def test_plan_and_refine_never_search_the_individuals_own_system(question):
    a = intent.assess(question, now=NOW)
    p = intent.plan(question, a, now=NOW, local_terms=["Quillbase"])
    r = intent.refine(p, outcome(0), a, now=NOW, local_terms=["Quillbase"])
    sent = [q.text for q in p.queries] + [q.text for q in (r.queries if r else ())]
    assert sent, p.notes
    for text in sent:
        words = set(re.findall(r"[\w']+", text.lower()))
        assert "neuroscifi" not in text.lower() and "quillbase" not in text.lower() and not words & {"our", "we"}
    assert any(n.startswith("local context:") for n in p.notes)


def test_strip_local_keeps_the_outside_topic():
    assert intent.strip_local("Is NeuroSciFi affected by CVE-2026-1234?") == ("Is affected by CVE-2026-1234?", 1)
    assert intent.strip_local("compare dendrites to our system's learning rule now") == \
        ("compare dendrites to now", 1)
    assert intent.strip_local("the libraries we use and numpy", ["x"]) == ("and numpy", 1)
    assert intent.strip_local("hourly journals in Ourense") == ("hourly journals in Ourense", 0)


def test_plan_technical_with_packages():
    a = intent.assess(S3, now=NOW)
    a["packages"] = [{"ecosystem": "PyPI", "name": "numpy", "version": "2.3.5"},
                     {"ecosystem": "PyPI", "name": "zstandard", "version": "0.25.0"},
                     {"ecosystem": "npm", "name": "open-websearch", "version": "2.2.0"}]
    p = intent.plan(S3, a, now=NOW)
    assert texts(p, "technical") == ["pypi:numpy", "osv:PyPI:numpy@2.3.5", "pypi:zstandard",
                                     "osv:PyPI:zstandard@0.25.0", "npm:open-websearch",
                                     "osv:npm:open-websearch@2.2.0"]
    assert all(q.since is None for q in p.queries if q.kind == "technical")
    assert len(p.queries) == 8
    assert "stable releases security advisories" in texts(p, "web")      # "the libraries we use" is local


def test_plan_technical_expansions_without_packages():
    q = "What is new in the httpx library?"
    p = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert texts(p) == ["httpx library", "httpx library release notes", "httpx library changelog"]


def test_plan_monitor_drops_instruction_clause():
    p = intent.plan(S4, intent.assess(S4, now=NOW), now=NOW)
    assert texts(p, "web")[0] == "predictive coding" and "predictive coding" in texts(p, "academic")
    assert any("sub-question 2 has no searchable topic" in n for n in p.notes)


def test_plan_without_topic_says_so():
    p = intent.plan(S5, intent.assess(S5, now=NOW), now=NOW)
    assert p.queries == () and any("no searchable topic" in n for n in p.notes)


def test_plan_comparison_and_claim():
    q = "Compare PyTorch vs JAX for research workloads"
    p = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert texts(p) == ["PyTorch vs JAX", "PyTorch workloads", "JAX workloads"]
    q = "Is it true that coffee causes dehydration?"
    p = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert texts(p) == ["coffee causes dehydration", "coffee causes dehydration fact check"]


def test_plan_site_and_language():
    q = "Latest LLM quantization papers on arxiv.org"
    p = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    site = [x for x in p.queries if x.site]
    assert [(x.text, x.site) for x in site] == [("LLM quantization papers", "arxiv.org")]
    assert all("arxiv.org" not in x.text for x in p.queries)
    q = "Как работает трансформер?"
    p = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert texts(p) == ["Как работает трансформер"] and p.queries[0].language == "ru"


def test_plan_is_deduped_capped_and_deterministic():
    q = "Compare numpy and numpy"
    a = intent.assess(q, now=NOW)
    p = intent.plan(q, a, now=NOW)
    keys = [(x.text.lower(), x.kind, x.site, x.since) for x in p.queries]
    assert len(keys) == len(set(keys))
    for question, *_ in TABLE:
        a = intent.assess(question, now=NOW)
        p1, p2 = intent.plan(question, a, now=NOW), intent.plan(question, a, now=NOW)
        assert p1 == p2 and len(p1.queries) <= 8
        assert all(x.kind in QUERY_KINDS and x.purpose for x in p1.queries)


# ---- refine --------------------------------------------------------------------------------------------------------

def outcome(n_results, ok=True):
    results = [SearchResult(url=f"https://r.org/{i}", title="t", snippet="", provider="p") for i in range(n_results)]
    attempts = [{"provider": "p", "engine": None, "query": "q", "ok": ok, "n": n_results if ok else 0,
                 "error": None if ok else "timeout", "latency": 0.1}]
    return SearchOutcome(results=results, attempts=attempts, failures=[] if ok else attempts)


def test_refine_broadens_thin_results():
    a = intent.assess(S2, now=NOW)
    p = intent.plan(S2, a, now=NOW)
    r = intent.refine(p, outcome(1), a, now=NOW)
    assert r is not None and r.notes[0].startswith("refinement: only 1 result(s)")
    assert all(q.since is None and q.site is None for q in r.queries) and len(r.queries) <= 4
    assert r.queries[0].text == "dendritic computation" and {q.kind for q in r.queries} <= {"web", "academic"}
    assert not any("NeuroSciFi" in q.text for q in r.queries)
    assert all(q.purpose.startswith("refinement") for q in r.queries)
    assert intent.refine(r, outcome(0), a, now=NOW) is None              # only one refinement round


def test_refine_when_all_failed_and_none_when_enough():
    a = intent.assess(S1, now=NOW)
    p = intent.plan(S1, a, now=NOW)
    r = intent.refine(p, outcome(0, ok=False), a, now=NOW)
    assert r.notes[0].startswith("refinement: every provider failed")
    # quick_lookup may send 3 queries; the plan used 2, so the refinement round has exactly 1 left (W-20)
    assert [(q.text, q.kind) for q in r.queries] == [("open-source AI inference", "news")]
    r = intent.refine(p, outcome(0, ok=False), a, now=NOW, remaining=2)
    assert [(q.text, q.kind) for q in r.queries] == [("open-source AI inference", "news"),
                                                     ("open-source AI inference", "web")]
    assert intent.refine(p, outcome(5), a, now=NOW) is None


def test_refine_returns_none_when_nothing_broader_exists():
    p = QueryPlan(queries=(Query("dendrites", "web"),))
    assert intent.refine(p, outcome(0), {"entities": []}, now=NOW) is None


@pytest.mark.parametrize("question, cls", [
    ("Is it true that the Varnel River is 520 km long?", "stable"),
    ("Is the Varnel River 520 km long?", "stable"),
    ("Is it true that the Brightwater dam is 186 metres tall?", "stable"),
    ("Is it true that numpy 2.4 was released?", "release"),
    ("Is it true that dendritic spikes double neuron capacity?", "scientific"),
    ("Is it true that the bridge reopened this week?", "breaking"),
    ("Monitor the Varnel River length", "volatile")])
def test_stable_fact_checks_are_not_volatile(question, cls):
    """A fact check without recency, technical or scientific cues is about a stable fact: a volatile 2-day TTL
    age-discounted every dated source and kept two agreeing groups below 'supported' (benchmark F09)."""
    a = intent.assess(question, now=NOW)
    assert a["freshness_class"] == cls, a["why"]
    assert a["needs_web"] is True


def test_related_knowledge_that_does_not_cover_the_question_does_not_answer_it():
    """Benchmark A10 (full run): fresh knowledge about Orbix's port answered "Does Orbix support encryption at
    rest?" from memory, because one shared name counted as relevant. An item must state most of the question's
    topical terms to answer it without the web."""
    port = {"id": 1, "statement": "By default, the Orbix server listens on port 7420 for client connections.",
            "status": "provisional", "stale": False, "contested": False}
    a = intent.assess("Does Orbix support encryption at rest?", now=NOW, known=[port])
    assert a["needs_web"] is True and a["memory_items"] == []
    assert any("do not cover the question's terms" in r for r in a["reasons"])
    b = intent.assess("Which port does the Orbix server listen on?", now=NOW, known=[port])
    assert b["needs_web"] is False and b["memory_items"] == [1]


# ---- UAT RC1 regressions (W-11 / C-03 plain package names, W-20 planning hygiene) ---------------------------------

def test_plain_package_name_with_a_release_cue_queries_the_registries():
    q = "What is the latest stable release of numpy?"
    a = intent.assess(q, now=NOW)
    assert a["mode"] == "technical"
    assert [(p["ecosystem"], p["name"], p["version"], p["inferred"]) for p in a["packages"]] == [
        ("PyPI", "numpy", None, True), ("npm", "numpy", None, True)]
    p = intent.plan(q, a, now=NOW)
    assert texts(p, "technical") == ["pypi:numpy", "npm:numpy"]        # no version, no advisory cue: no osv
    q = "Any security advisories for zstandard 0.25.0?"
    p = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert "osv:PyPI:zstandard@0.25.0" in texts(p, "technical")
    q = "What is the latest release of the requests Python library?"     # the question names the ecosystem
    assert [(x["ecosystem"], x["name"]) for x in intent.assess(q, now=NOW)["packages"]] == [("PyPI", "requests")]


def test_comparison_sides_do_not_swallow_the_predicate():
    for q in ("numpy vs pandas latest release dates", "Compare the latest release dates of numpy and pandas"):
        a = intent.assess(q, now=NOW)
        assert a["comparison"] == ["numpy", "pandas"] and a["mode"] == "comparative"
        p = intent.plan(q, a, now=NOW)
        assert texts(p, "technical") == ["pypi:numpy", "npm:numpy", "pypi:pandas", "npm:pandas"]
        assert {"numpy release dates", "pandas release dates"} <= set(texts(p, "web"))
    assert intent.assess("Should I use PyTorch vs JAX for research workloads?", now=NOW)["comparison"] == \
        ["PyTorch", "JAX"]


def test_package_inference_keeps_common_words_and_local_context_out():
    assert intent.assess(S3, now=NOW)["packages"] == []                      # "the libraries we use" is local
    assert intent.assess("Is there an update for the weather today?", now=NOW)["packages"] == []
    assert intent.assess("What is the latest version of Quillstream?", now=NOW)["packages"] == []
    q = "What is the latest stable release of quillbase?"                    # an owner's private term
    a = intent.assess(q, now=NOW)
    assert not any("quillbase" in x.text.lower() for x in intent.plan(q, a, now=NOW, local_terms=["Quillbase"]).queries)
    assert intent.assess("Notify me when numpy releases a new version", now=NOW)["packages"] == []   # monitor


def test_no_query_is_only_stopwords_or_temporal_words():
    q = "What has changed on https://quotes.toscrape.com/ since last week?"
    a = intent.assess(q, now=NOW)
    p = intent.plan(q, a, now=NOW)
    assert all(intent.searchable_text(x.text) for x in p.queries)
    assert not any(x.text.split()[0].lower() == "last" for x in p.queries)
    assert not intent.searchable_text("last 2026") and not intent.searchable_text("the past week")
    assert intent.searchable_text("pypi:numpy") and intent.searchable_text("Lumen-7 launch")


def test_refine_never_overshoots_the_mode_query_budget():
    q = "What changed in the Quillstream changelog since last week?"         # change_detection: 2 queries
    a = intent.assess(q, now=NOW)
    p = intent.plan(q, a, now=NOW)
    assert a["mode"] == "change_detection" and len(p.queries) == 2
    assert intent.refine(p, outcome(0, ok=False), a, now=NOW) is None
    assert intent.refine(p, outcome(0, ok=False), a, now=NOW, remaining=0) is None
    r = intent.refine(p, outcome(0, ok=False), a, now=NOW, remaining=1)
    assert r is not None and len(r.queries) == 1


def test_query_text_uses_the_statement_words_not_stems():
    # W-20: a record template searches for the record's subject, never for its frame ("PyPI reports ...")
    assert intent.query_text("PyPI reports requests latest version 2.34.2 [3]") == "requests"
    assert intent.query_text('OpenAlex record: "Yes, the human brain has around 86 billion neurons", dated '
                             '2025-02-06.') == "Yes human brain around 86 billion neurons"
    assert intent.query_text("GitHub lists release v2.5.5 of numpy/numpy, published 2026-11-15.") == "numpy v2.5.5"
    assert intent.query_text("Latest release 2.5.5 uploaded 2026-11-15; requires Python >=3.12.") == ""
    assert intent.query_text("The Varnel River is 412 kilometres long.") == "Varnel River 412 kilometres long"
    assert intent.query_text("last week") == ""
    assert "NeuroSciFi" not in intent.query_text("NeuroSciFi's learning rule uses dendritic gating")


@pytest.mark.parametrize("text, eco", [
    ("latest numpy version for data science", "PyPI"), ("pip install numpy latest", "PyPI"),
    ("latest numpy in a jupyter notebook", "PyPI"), ("latest left-pad for node", "npm"),
    ("latest numpy for javascript", "npm"), ("latest stable numpy", None), ("numpy python vs javascript", None),
])
def test_ecosystem_hint(text, eco):
    # RT-W-04: the question's own wording decides the registry before the records do.
    assert intent.ecosystem_hint(text) == eco
