"""ADR-054: answer monitoring (webcortex/monitoring.py), source dependence (claims.dependence) and the orchestrator's
four settings (answer_monitoring, selective_answering, verification_escalation, adaptive_depth)."""
import copy
from datetime import datetime, timezone

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import claims as C
from neuroscifi.webcortex import monitoring as M
from neuroscifi.webcortex import orchestrator, services as svc_mod, state
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.types import Budget, Claim, Document, Usage

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()


# ---- calibration -------------------------------------------------------------------------------------------------

def test_fit_pools_violators_and_reads_each_block_with_a_beta_1_1_prior():
    # sorted outcomes 0,1,0,1: blocks 0 | 1,0 (pooled: the violator) | 1 -> 1/3, 2/4, 2/3
    table = M.fit([0.0, 1.0, 2.0, 3.0], [0, 1, 0, 1])
    assert table == {"x": [0.0, 1.5, 3.0], "y": [round(1 / 3, 6), 0.5, round(2 / 3, 6)], "n": [1, 2, 1]}
    # four agreeing outcomes read 5/6, never certainty
    assert M.fit([1, 1, 1, 1], [1, 1, 1, 1])["y"] == [round(5 / 6, 6)]


def test_calibrate_is_monotone_interpolated_and_flat_beyond_the_ends():
    table = {"x": [-1.0, 1.0], "y": [0.2, 0.8]}
    assert M.calibrate(-5, table) == 0.2 and M.calibrate(5, table) == 0.8
    assert M.calibrate(0.0, table) == pytest.approx(0.5)
    values = [M.calibrate(s / 10, table) for s in range(-30, 31)]
    assert values == sorted(values)
    assert M.calibrate(0.0, None) == 0.5             # no table: the logistic of the score


def test_threshold_is_the_lowest_with_selective_risk_at_most_the_target():
    conf = [0.9, 0.8, 0.7, 0.6, 0.5]
    ok = [True, True, True, False, False]
    assert M.threshold(conf, ok, 0.10) == 0.7
    assert M.threshold(conf, ok, 0.25) == 0.6       # 1 wrong of 4
    assert M.threshold([0.5], [False], 0.10) is None


# ---- signals, record, wording ------------------------------------------------------------------------------------

def _verdict(state_, conf, sup, con):
    return {"verdict": {"state": state_, "confidence": conf, "statement": "X is 4 m tall",
                        "independent_groups": {"supporting": sup, "contradicting": con}},
            "answer": [], "contradictions": [], "freshness": {"satisfied": True}}


def test_assess_ranks_a_supported_claim_above_a_disputed_one_and_writes_nothing_into_the_report():
    table = {"strata": {"claim": {"x": [-2.0, 2.0], "y": [0.1, 0.9]}}}
    supported, disputed = _verdict("supported", 0.722, 2, 0), _verdict("disputed", 0.278, 0, 2)
    before = copy.deepcopy(supported)
    a, b = M.assess(supported, table=table), M.assess(disputed, table=table)
    assert supported == before                      # a record only
    assert a["claim_probability"] > 0.5 > b["claim_probability"]
    assert a["components"]["state"] == 1.0 and b["components"]["state"] == -1.0
    assert a["decision"] == "answer" and a["note"] is None


def test_the_self_critique_is_one_named_weak_input_and_never_a_gate():
    report = _verdict("supported", 0.722, 2, 0)
    report["reasoning"] = {"status": "model", "answer": [{"text": "a"}, {"text": "b"}], "rejected": [],
                           "critique": {"status": "ok", "flagged": 2, "removed": 0}}
    record = M.assess(report, table={}, selective=True)
    assert record["signals"]["critique"]["flagged"] == 2
    assert record["components"]["critique"] == M.WEIGHTS["critique"] * 1.0
    assert record["decision"] == "answer"           # no threshold: never "not enough evidence" by marks


def test_selective_answering_leads_with_plain_words_and_keeps_the_quotes():
    report = {"answer": [{"type": "quote", "text": "X is 4 m tall.", "confidence": 0.58, "independent_sources": 1,
                          "status": "single_source", "verification": {"state": "insufficient"}}],
              "contradictions": [], "freshness": {"satisfied": True}}
    table = {"strata": {"answer": {"x": [0.0, 2.0], "y": [0.3, 0.9]}}, "threshold": {"confidence": 0.8}}
    off = M.assess(report, table=table)
    assert off["decision"] == "answer" and off["below_threshold"] is True
    record = M.assess(report, table=table, selective=True)
    assert record["decision"] == "not_enough_evidence"
    assert "only one independent source states it" in record["note"]
    quotes = list(report["answer"])
    M.apply_note(report, record)
    assert report["answer"][0]["status"] == "not_enough_evidence" and report["answer"][1:] == quotes
    M.apply_note(report, off | {"decision": "answer"})
    assert len(report["answer"]) == 2                # only below the threshold


def test_a_map_on_too_little_development_data_shows_no_number():
    """Wave 1 review A6: the shipped fact-check map was one block of 15 items, so every fact check, an
    "insufficient" verdict too, read 0.941. A map below MIN_ITEMS or MIN_BLOCKS is not applied: no number, the
    plain note instead; one with enough data is applied."""
    small = {"strata": {"verdict": {"x": [1.2], "y": [0.941176], "n": [15]}}}
    supported, insufficient = _verdict("supported", 0.722, 2, 0), _verdict("insufficient", 0.6, 1, 0)
    for report in (supported, insufficient):
        record = M.assess(report, table=small)
        assert record["confidence"] is None and record["confidence_note"] == M.NOT_ENOUGH_DATA
        assert record["calibration"]["applied"] is False
    enough = {"strata": {"verdict": {"x": [0.0, 1.0, 2.0], "y": [0.3, 0.6, 0.9], "n": [10, 10, 10]}}}
    record = M.assess(supported, table=enough)
    assert record["confidence"] is not None and record["confidence_note"] is None
    assert M.assess(insufficient, table=enough)["confidence"] < record["confidence"]


def test_claims_use_the_unfitted_score_whatever_map_is_shipped():
    """Wave 1 review B5: the fitted claim map scored worse than the unfitted score on development (and held-out)."""
    report = _verdict("supported", 0.722, 2, 0)
    plain = M.assess(report, table={})
    mapped = M.assess(report, table={"strata": {"claim": {"x": [-2.0, 2.0], "y": [0.1, 0.9], "n": [40, 40]}}})
    assert plain["claim_probability"] == mapped["claim_probability"] == round(1 / (1 + 2.718281828459045 **
                                                                                   -plain["score"]), 6)


def test_the_models_wording_never_moves_the_confidence():
    """Wave 1 review A5: at identical evidence, the guard refusing sentences or the model abstaining changes neither
    the score nor the selective decision; both are shown in the record."""
    report = _verdict("supported", 0.722, 2, 0)
    worded = copy.deepcopy(report)
    worded["reasoning"] = {"status": "model", "answer": [], "rejected": [{"reasons": ["no citation"]}] * 3,
                           "abstained": True}
    a, b = M.assess(report, table={}, selective=True), M.assess(worded, table={}, selective=True)
    assert a["score"] == b["score"] and a["claim_probability"] == b["claim_probability"]
    assert a["decision"] == b["decision"] == "answer" and b["components"]["guard"] == 0.0
    assert b["signals"]["guard"]["abstained"] is True and b["sufficiency"]["shown"]
    assert b["sufficiency"]["enough"] is True


def test_a_fact_check_is_held_back_by_sufficiency_not_by_a_number():
    """Wave 1 review A6: with selective answering on, an "insufficient" fact check is held back (the old fixed 0.941
    never was), a verified one is answered, whatever threshold the file holds."""
    table = {"strata": {"verdict": {"x": [1.2], "y": [0.941176], "n": [15]}}, "threshold": {"confidence": 0.49}}
    held = M.assess(_verdict("insufficient", 0.6, 1, 0), table=table, selective=True)
    assert held["decision"] == "not_enough_evidence" and held["selective_basis"] == "sufficiency"
    assert "only one independent source states it" in held["note"]
    assert M.assess(_verdict("supported", 0.722, 2, 0), table=table, selective=True)["decision"] == "answer"


def test_a_monitoring_failure_is_logged_and_leaves_the_report_alone(monkeypatch):
    """Wave 1 review A13: a display-only record must not add a line to what the owner reads."""
    events = []

    class Run:
        documents, clusters, usage, budget, depth_stopped, escalated = (), (), Usage(), None, None, None

        class cfg:
            selective_answering = False

        def event(self, kind, detail, *a):
            events.append((kind, detail))

    def boom(*a, **k):
        raise RuntimeError("broken record")
    monkeypatch.setattr(M, "assess", boom)
    report = {"answer": [], "limitations": ["already here"]}
    orchestrator._monitor(Run(), report)
    assert report == {"answer": [], "limitations": ["already here"]}
    assert events == [("error", {"stage": "monitoring", "error": "RuntimeError", "detail": "broken record"})]


def test_escalation_needs_a_low_confidence_and_room_in_the_tasks_own_budget():
    record = {"below_threshold": True, "statement": "X is 4 m tall", "confidence": 0.3, "threshold": 0.5,
              "sufficiency": {"why": ["only one independent source states it"]}}
    budget = Budget(max_queries=3, max_requests=20, max_pages=3, max_seconds=45)
    assert M.escalation(record, usage=Usage(pages=1, queries=1, requests=4), budget=budget, elapsed=5)["statement"]
    assert M.escalation(record, usage=Usage(pages=3, queries=1, requests=4), budget=budget, elapsed=5) is None
    assert M.escalation(record, usage=Usage(pages=1, queries=3, requests=4), budget=budget, elapsed=5) is None
    assert M.escalation(record, usage=Usage(pages=1, queries=1, requests=4), budget=budget, elapsed=40) is None
    assert M.escalation(record | {"below_threshold": False}, usage=Usage(), budget=budget, elapsed=0) is None


# ---- source dependence ------------------------------------------------------------------------------------------

def _claim(url, text="The Halvorsen Bridge opened in 1987 across the estuary."):
    return Claim(id=url, text=text + " " + url, source_url=url, domain=url.split("/")[2], title="", retrieved_at=NOW)


def _doc(url, links=()):
    return Document(url=url, final_url=url, canonical_url=url, domain=url.split("/")[2], title="", text="",
                    retrieved_at=NOW, retrieval_mode="live", links=list(links))


def test_dependence_sees_one_family_of_sites_without_changing_independence_groups():
    claims = [_claim("http://docs.kestrel.example/a", "Kestrel was created in 2019 by Ilse Marten."),
              _claim("http://blog.kestrel.example/b", "In 2019 Ilse Marten started the Kestrel language.")]
    assert len(C.independence_groups(claims)) == 2   # verification counts two groups, as before
    dep = C.dependence(claims)
    assert dep["groups"] == 2 and dep["effective_groups"] == 1 and dep["one_family"] is True
    assert dep["families"] == [["kestrel", ["blog.kestrel.example", "docs.kestrel.example"]]]


def test_dependence_sees_a_source_that_cites_another():
    a = _claim("http://news-a.example/x", "The ferry now runs every 45 minutes on weekdays.")
    b = _claim("http://ferry-co.example/timetable", "Weekday ferries run every 45 minutes from Monday.")
    c = _claim("http://other.example/y", "Since Monday the weekday ferry runs every 45 minutes.")
    docs = [_doc(a.source_url, ["http://ferry-co.example/timetable"]), _doc(b.source_url), _doc(c.source_url)]
    dep = C.dependence([a, b, c], docs)
    assert dep["groups"] == 3 and dep["effective_groups"] == 2 and dep["circular"] is True
    assert dep["citations"] == [["news-a.example", "ferry-co.example"]]
    assert C.dependence([a, b, c])["circular"] is False       # without the documents no link is seen


def test_verified_curve_counts_a_statement_when_its_second_independent_group_is_read():
    class Cl:
        def __init__(self, members):
            self.claims, self.supporting, self.status = members, [m.id for m in members], "corroborated"
    a, b = _claim("http://a.example/1"), _claim("http://b.example/2", "Opened in 1987, the Halvorsen Bridge spans it.")
    docs = [_doc("http://a.example/1"), _doc("http://x.example/3"), _doc("http://b.example/2")]
    assert M.verified_curve(docs, [Cl([a, b])]) == [0, 0, 1]


# ---- the orchestrator -------------------------------------------------------------------------------------------

STATEMENT = "The Halvorsen Bridge opened in 1987 as the first fixed crossing of the Varnel estuary"
PAGES = {("a-news.example", "/h"): ("Halvorsen Bridge", "Halvorsen Bridge open 1987",
                                    STATEMENT + ". It carries four lanes and a cycle path"),
         # found only by a search for the statement itself (no question word in its title or snippet)
         ("estuary-history.example", "/c"): ("Estuary crossings", "Varnel estuary crossings since 1987",
                                             STATEMENT + ". The crossing replaced a ferry that ran since 1890")}


def _run(tmp_path, web, config, monkeypatch=None, table=None):
    for (host, path), (title, _, text) in PAGES.items():
        body = "".join(f"<p>{p.strip()}.</p>" for p in text.split(". ") if p.strip())
        web.add(host, path, f"<html><head><title>{title}</title><meta property='article:published_time' "
                            f"content='2026-10-21T08:00:00Z'></head><body><article><h1>{title}</h1>{body}"
                            "</article></body></html>")
    if table is not None:
        monkeypatch.setattr(M, "calibration", lambda: table)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "individual.sqlite"
    db = store.create(path, NOW)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, **config}, NOW)
        corpus = CorpusProvider([{"url": f"http://{h}{p}", "title": t, "snippet": s}
                                 for (h, p), (t, s, _) in PAGES.items()])
        services = svc_mod.build(db, path, fixture=web, providers=[corpus], clock=lambda: NOW)
        try:
            # fact_verification: 6 queries, so one is left for an escalation after the planned searches
            task = orchestrator.submit(db, "Is it true that the Halvorsen Bridge opened in 1987?", NOW,
                                       mode="fact_verification")
            done = orchestrator.run(db, task["id"], services)
            events = state.events(db, done["id"])
        finally:
            services.close()
        return done, events
    finally:
        db.close()


HIGH = {"strata": {}, "threshold": {"confidence": 0.99, "target_risk": 0.1}, "fitted_on": "test"}


def test_switched_off_the_report_carries_no_record(tmp_path, web):
    done, _ = _run(tmp_path, web, {"answer_monitoring": False})
    assert "monitoring" not in done["result"]


def test_defaults_record_only():
    from neuroscifi.webcortex.config import WebConfig
    cfg = WebConfig()
    assert cfg.answer_monitoring is True
    assert not (cfg.selective_answering or cfg.verification_escalation or cfg.adaptive_depth)


def test_answer_monitoring_adds_the_record_and_changes_nothing_else(tmp_path, web):
    done, _ = _run(tmp_path, web, {"answer_monitoring": True})
    record = done["result"]["monitoring"]
    assert record["kind"] == "verdict" and 0.0 < record["claim_probability"] < 1.0
    assert record["confidence"] is None and record["confidence_note"] == M.NOT_ENOUGH_DATA   # the shipped maps
    assert not any("monitoring" in line for line in done["result"].get("limitations") or [])
    assert record["depth"]["pages"] == done["usage"]["pages"]
    assert record["decision"] == "answer"
    assert all(a.get("status") != "not_enough_evidence" for a in done["result"]["answer"])


def test_selective_answering_adds_the_plain_line_below_the_threshold(tmp_path, web, monkeypatch):
    done, _ = _run(tmp_path, web, {"selective_answering": True}, monkeypatch, HIGH)
    first = done["result"]["answer"][0]
    assert first["status"] == "not_enough_evidence" and first["text"].startswith("Not enough evidence")
    assert done["result"]["monitoring"]["decision"] == "not_enough_evidence"


def test_verification_escalation_reads_one_more_independent_source_inside_the_budget(tmp_path, web, monkeypatch):
    plain, _ = _run(tmp_path / "a", web, {"answer_monitoring": True}, monkeypatch, HIGH)
    done, events = _run(tmp_path / "b", web, {"answer_monitoring": True, "verification_escalation": True},
                        monkeypatch, HIGH)
    hosts = {s["domain"] for s in done["result"]["sources"]}
    assert "estuary-history.example" not in {s["domain"] for s in plain["result"]["sources"]}
    assert "estuary-history.example" in hosts
    assert done["usage"]["pages"] == plain["usage"]["pages"] + 1 <= done["budget"]["max_pages"]
    assert done["result"]["monitoring"]["depth"]["escalated"]["read"] == "http://estuary-history.example/c"
    assert any(e["kind"] == "decision" and "escalation" in (e["detail"] or {}) for e in events)


def test_no_escalation_without_room_in_the_budget(tmp_path, web, monkeypatch):
    table = dict(HIGH)
    monkeypatch.setattr(M, "ESCALATION_MIN_SECONDS", 10_000.0)      # more time than any budget has
    done, _ = _run(tmp_path, web, {"answer_monitoring": True, "verification_escalation": True}, monkeypatch, table)
    assert done["result"]["monitoring"]["depth"]["escalated"] is None
    assert "estuary-history.example" not in {s["domain"] for s in done["result"]["sources"]}


def _corpus_task(tmp_path, qid, config):
    from neuroscifi.webcortex.bench import corpus as BC
    from neuroscifi.webcortex.bench import offline

    class _Configured(offline._Bench):
        def _individual(self, path):
            db = super()._individual(path)
            state.set_config(db, config, BC.T0)
            return db

    question = next(q["question"] for q in BC.QUESTIONS if q["id"] == qid)
    web = BC.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    try:
        tmp_path.mkdir(parents=True, exist_ok=True)
        bench = _Configured(tmp_path, web, True)
        try:
            rec = bench.run_task(qid, question)
            return rec, state.events(bench.db, rec["task_id"])
        finally:
            bench.close()
    finally:
        web.stop()


def test_adaptive_depth_stops_reading_once_pages_stop_adding_verified_statements(tmp_path):
    full, _ = _corpus_task(tmp_path / "off", "F08", {"adaptive_depth": False})
    short, events = _corpus_task(tmp_path / "on", "F08", {"adaptive_depth": True})
    assert short["usage"]["pages"] < full["usage"]["pages"] <= full["budget"]["max_pages"]
    assert short["report"]["verdict"]["state"] == full["report"]["verdict"]["state"] == "supported"
    stopped = short["report"]["monitoring"]["depth"]["stopped_early"]
    assert stopped["after_pages"] == short["usage"]["pages"] and stopped["verified"] >= 1
    assert any(e["kind"] == "decision" and "adaptive_depth" in (e["detail"] or {}) for e in events)
    assert full["report"]["monitoring"]["depth"]["stopped_early"] is None
