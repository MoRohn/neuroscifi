"""The offline Web Cortex benchmark in --quick mode: structure and safety invariants, never accuracy thresholds.

Accuracy numbers are measurements reported in docs/web-cortex/TEST_RESULTS.md; this test only asserts what must
hold whatever they are (SSRF blocked, robots respected, injected text never in claims or answers, budgets kept,
the live flag honest, cancel/resume behaviour) and that two quick runs produce identical metrics.
"""
import json

import pytest

from neuroscifi.webcortex import bench
from neuroscifi.webcortex.bench import corpus as C
from neuroscifi.webcortex.bench.implications import implications


@pytest.fixture(scope="module")
def result():
    return bench.run(quick=True, repeat=2)


def test_corpus_covers_the_uat_defect_classes():
    assert set(C.SUBJECT) == set(C.BY_ID)                                      # every page has a gold subject
    assert C.FACT_PAIRS and all(next(q for q in C.QUESTIONS if q["id"] == t)["gold_label"] == "supported" and
                                next(q for q in C.QUESTIONS if q["id"] == f)["gold_label"] == "disputed"
                                for t, f in C.FACT_PAIRS)
    assert any(q["category"] == "latest" and q.get("package") for q in C.QUESTIONS)
    assert any("verify=False" in p for a in C.ARTICLES for p in a["paras"])
    assert {a["date_via"] for a in C.ARTICLES} >= {"og_epoch", "last_modified"}
    assert any("ignore the directions given above" in s["canaries"] for s in C.INJECTIONS)
    assert all(w["snippet"] in " ".join(C.BY_ID[w["page"]]["paras"]) for w in C.WIKI_SNIPPETS)


def test_citation_precision_is_per_cited_source():
    """RC1 UAT: the legacy metric counted a quote as supported when ANY cited source contained it, so a statement
    cited to [Zig, Rust, Mojo] pages passed. Every cited source must contain the quoted claim."""
    from neuroscifi.webcortex.bench.offline import citation_rows, score_citations
    texts = {1: "Zig is a general-purpose programming language.", 2: "Rust is a general-purpose programming "
             "language.", 3: "Notes: \u201cZig is a general\u2011purpose programming language\u201d , they say."}
    report = {"sources": [{"n": n, "url": f"http://x.example/{n}"} for n in (1, 2, 3)],
              "answer": [{"type": "template", "status": "summary", "text": "3 sources read", "citations": [1, 2, 3]},
                         {"type": "quote", "text": "Zig is a general-purpose programming language.",
                          "citations": [1, 2, 3]},
                         {"type": "quote", "text": "Rust is a general-purpose programming language.", "citations": [2]},
                         {"type": "quote", "text": "Uncited.", "citations": []}]}
    rows = citation_rows(report, lambda src: texts[src["n"]])
    assert [(r["item"], r["citation"], r["support"]) for r in rows] == [
        (1, 1, "verbatim"), (1, 2, None), (1, 3, "normalised"), (2, 2, "verbatim"), (3, None, None)]
    score = score_citations([("t", rows)])
    assert score["supported"] == 2 and score["quote_items"] == 3          # legacy: any source suffices
    pcs = score["per_cited_source"]
    assert (pcs["supported_citations"], pcs["citations"]) == (3, 5) and pcs["precision"] == 0.6
    assert pcs["items_every_source_supports"] == 1                       # only the Rust item


def test_corpus_meets_the_declared_size():
    assert len(C.ARTICLES) + len(C.SPECIAL) >= 40 and len(C.hosts()) >= 15
    cats = [q["category"] for q in C.QUESTIONS]
    assert cats.count("authoritative") >= 10 and cats.count("time") >= 6
    assert sum(p["conflict"] for p in C.PAIRS) >= 6 and sum(not p["conflict"] for p in C.PAIRS) >= 4
    assert len(C.INJECTIONS) + len([s for s in C.SPECIAL if s["kind"] != "injection"]) >= 8
    assert len(C.MEMORY) >= 10 and sum(1 for a in C.ARTICLES if a["paras"]) >= 12
    assert any(q.get("gold_satisfied") is False for q in C.QUESTIONS)


def test_result_structure(result):
    assert result["benchmark"] == "webcortex-offline" and result["quick"] is True
    for key in ("environment", "corpus", "metrics", "timing", "resources", "regression", "safety"):
        assert key in result
    m = result["metrics"]
    for key in ("authoritative", "time_sensitive", "date_extraction", "verification", "contradictions", "citations",
                "completion", "checks", "memory", "injection", "liveness", "budgets", "probes", "extraction", "uat"):
        assert key in m, key
    pcs = m["citations"]["per_cited_source"]
    assert {"citations", "supported_citations", "precision", "items", "items_every_source_supports"} <= set(pcs)
    assert pcs["supported_citations"] <= pcs["citations"] and pcs["items"] == m["citations"]["quote_items"]
    for key in ("fact_check_discrimination", "number_checks", "latest_version", "cross_subject", "code_literals",
                "snippet_records", "dating", "package_routing", "top_on_subject"):
        assert key in m["uat"], key
    # the quick subset exercises the plain-package route to the fixture PyPI (structure, not an accuracy bar)
    assert m["uat"]["package_routing"]["questions"] == 1
    assert set(m["verification"]["confusion"]["labels"]) == {"supported", "disputed", "contested", "insufficient"}
    assert {"scrapling", "trafilatura"} <= set(m["extraction"]["mean"])
    assert len(result["timing"]) == len(result["resources"]) == 2
    json.dumps(result)     # the evidence file must serialise


def test_safety_invariants_hold(result):
    safety = result["safety"]
    assert safety["all_hold"], safety
    probes = result["metrics"]["probes"]["probes"]
    ssrf = [p for p in probes if p["group"] == "ssrf"]
    assert ssrf and all(p["outcome"] == "blocked_policy" for p in ssrf)
    # direct probes never leave the client; a redirect probe reaches only the public redirecting page itself
    assert all(p["requests_reaching_server"] == (1 if p["id"].startswith("ssrf_redirect") else 0) for p in ssrf)
    assert next(p for p in probes if p["id"] == "robots_disallowed")["outcome"] == "blocked_robots"


def test_injected_text_never_reaches_claims_or_answers(result):
    inj = result["metrics"]["injection"]
    assert inj["acquired"] >= 1, "the quick corpus must exercise at least one injection page"
    assert inj["leaks_into_claims_or_answers"] == [] and inj["flagged"] == inj["acquired"]


def test_budgets_respected_and_live_flag_honest(result):
    m = result["metrics"]
    assert m["budgets"]["violations"] == []
    for tight in m["checks"]["tight_budgets"]:
        assert tight["within"], tight
    assert m["liveness"]["violations"] == []


def test_cancel_and_resume(result):
    checks = result["metrics"]["checks"]
    assert checks["cancel_queued"]["status"] == "cancelled" and checks["cancel_queued"]["requests_made"] == 0
    assert checks["cancel_running"]["status"] == "cancelled" and checks["cancel_running"]["knowledge_written"] == 0
    resume = checks["crash_resume"]
    assert resume["crashed"] and resume["documents_checkpointed"] >= 1
    assert resume["refetched_checkpointed_pages"] == 0 and resume["checkpointed_sources_served_from"] == ["cache"]


def test_two_quick_runs_are_identical(result):
    assert result["regression"]["identical"], result["regression"]["differences"]


def test_main_writes_the_evidence_file(tmp_path):
    out = tmp_path / "bench.json"
    assert bench.main(["--quick", "--repeat", "1", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["metrics"]["probes"]["all_contained"] is True


def test_implications_are_proposals_with_citations_on_both_sides(tmp_path):
    (tmp_path / "docs/architecture").mkdir(parents=True)
    (tmp_path / "Architecture.md").write_text("# Learning\n\nTest distinct dendritic branch rules on one graph.\n")
    (tmp_path / "docs/architecture/REQUIREMENTS_MATRIX.md").write_text(
        "| GRA-12 | Compare branch-specific plasticity; dendritic rules. | p.6 | R | Research agenda | — | DEFERRED |\n")
    report = {"sources": [{"n": 1, "url": "https://example.org/paper"}],
              "findings": [{"statement": "Dendritic spikes make plasticity compartment specific.", "citations": [1],
                            "status": "single_source"}]}
    out = implications(report, tmp_path)
    assert out["proposals"] and all(p["status"] == "proposal_only" for p in out["proposals"])
    first = out["proposals"][0]
    assert first["web_sources"] == ["https://example.org/paper"]
    assert any(a.startswith("docs/architecture/REQUIREMENTS_MATRIX.md:1") for a in first["repo_anchors"])
    assert implications({"sources": [], "findings": []}, tmp_path)["proposals"] == []


def test_consolidation_reports_the_configuration_the_run_used(result):
    # RC2 L5: the report hard-coded auto_provisional_min_confidence 0.75 while the WebConfig default is 0.72.
    from neuroscifi.webcortex.config import WebConfig
    cfg = WebConfig()
    assert result["metrics"]["consolidation"]["config"] == {
        "auto_provisional_min_sources": cfg.auto_provisional_min_sources,
        "auto_provisional_min_confidence": cfg.auto_provisional_min_confidence}
