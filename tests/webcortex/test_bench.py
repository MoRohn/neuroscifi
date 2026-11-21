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
                "completion", "checks", "memory", "injection", "liveness", "budgets", "probes", "extraction"):
        assert key in m, key
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
