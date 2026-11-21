"""One ADR-035 rule for every verdict (synthesis.decide): the report verdict, the per-claim states the reasoning
guard reads (computed before reasoning) and consolidation decide a statement the same way, syndicated copies
included; the reasoning guard requires state == supported for an unhedged sentence."""
from datetime import datetime, timezone

from neuroscifi.instance import store
from neuroscifi.webcortex import claims as C
from neuroscifi.webcortex import evolution, orchestrator, reasoning, services as svc_mod, state
from neuroscifi.webcortex import synthesis as S
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.types import Document

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()
D = 86400.0
REST = (" Paragraph one of the harbour story reports what the city engineer said about the cables."
        " Paragraph two of the harbour story reports what the ferry operator said about the tolls."
        " Paragraph three of the harbour story reports what the council said about the repainting budget."
        " Paragraph four of the harbour story reports what residents said about the night closures."
        " Paragraph five of the harbour story reports what the port authority said about shipping.")
# a lightly edited copy of one story on another host: the claim sentences differ (no sentence-level duplicate),
# the documents are near-identical (claims.document_origins), so they are ONE independent source
STORY = "The Halvorsen Bridge opened in 1987 as the first fixed crossing of the Varnel estuary." + REST
COPY = "Opened in 1987, the Halvorsen Bridge was the first fixed crossing over the Varnel estuary." + REST
BRIDGE_Q = "When did the Halvorsen Bridge open?"


def doc(url, text, published, title=""):
    return Document(url=url, final_url=url, canonical_url=url, domain=url.split("/")[2], title=title, text=text,
                    retrieved_at=NOW - 60, retrieval_mode="live", kind="page", published_at=published, metadata={},
                    flags={})


def test_a_syndicated_copy_gets_one_state_in_the_report_and_in_consolidation():
    docs = [doc("https://a-news.example/h", STORY, NOW - 30 * D, "Halvorsen Bridge"),
            doc("https://b-daily.example/h", COPY, NOW - 29 * D, "Halvorsen Bridge")]
    found = [c for d in docs for c in C.extract_claims(d, BRIDGE_Q)]
    report = S.synthesize(BRIDGE_Q, {}, docs, C.cluster(found), now=NOW)
    [finding] = report["findings"]
    assert finding["verification"]["state"] == "insufficient"       # one source, one group: not supported
    C.stamp_origins(found, docs)                                     # what the orchestrator stores
    [stored] = [C.score(c, now=NOW, freshness_class="stable") for c in C.cluster(found)]
    # consolidation reads stored clusters without the documents: before the shared rule it counted two groups
    # and promoted the copy to "supported" knowledge while the report said "single source"
    v = evolution.cluster_verdict(stored, now=NOW, freshness_class="stable")
    assert (v["state"], v["independent_groups"]["supporting"]) == ("insufficient", 1)
    assert stored.status == finding["status"] == "single_source"


def test_report_verdict_and_cluster_state_use_the_same_decision():
    docs = [doc("https://a-news.example/h", STORY, NOW - 30 * D), doc("https://b-daily.example/h", COPY, NOW - 29 * D),
            doc("https://c-times.example/h", "The Halvorsen Bridge opened in 1987 after four years of construction.",
                NOW - 10 * D)]
    found = [c for d in docs for c in C.extract_claims(d, BRIDGE_Q)]
    statement = "the Halvorsen Bridge opened in 1987"
    report = S.synthesize(statement, {"mode": "fact_verification", "claim": statement}, docs, C.cluster(found),
                          now=NOW)
    assert report["verdict"]["state"] == "supported" and report["verdict"]["independent_groups"]["supporting"] == 2
    assert {f["verification"]["state"] for f in report["findings"]} == {"supported"}


# ---- the reasoning guard reads the ADR-035 state, computed before reasoning ---------------------------------

LIGHT_Q = "Is the Tolvane Lighthouse currently open to visitors?"
LIGHT = "The Tolvane Lighthouse is open to visitors every day from May to September."


def _stale_report():
    docs = [doc("https://wiki.example/t", LIGHT, NOW - 400 * D, "Tolvane Lighthouse"),
            doc("https://heritage.example/t", LIGHT[:-1] + ", the trust says.", NOW - 500 * D, "Visiting Tolvane")]
    found = [c for d in docs for c in C.extract_claims(d, LIGHT_Q)]
    return S.synthesize(LIGHT_Q, {"freshness_class": "volatile"}, docs, C.cluster(found), now=NOW)


def test_a_time_sensitive_claim_backed_only_by_stale_sources_must_be_hedged():
    report = _stale_report()
    [finding] = report["findings"]
    # two agreeing sources: the legacy label says "corroborated"; ADR-035 says nothing establishes "now"
    assert finding["status"] == "corroborated"
    assert (finding["verification"]["state"], finding["verification"]["category"]) == \
        ("temporally_unresolved", "time_sensitive")
    b = reasoning.bundle(report, LIGHT_Q)
    assert [c["state"] for c in b["claims"]] == ["temporally_unresolved"]
    plain = {"text": LIGHT, "sources": [1, 2], "claims": ["C1"]}
    hedged = {"text": "According to two older sources, the Tolvane Lighthouse is open to visitors every day from "
                      "May to September, but this may be out of date.", "sources": [1, 2], "claims": ["C1"]}
    ok, bad = reasoning.guard([plain, hedged], b)
    assert [x["text"] for x in ok] == [hedged["text"]] and ok[0]["states"] == {"C1": "temporally_unresolved"}
    assert "states C1 (temporally_unresolved) as fact without hedging" in bad[0]["reasons"]
    assert any("current state" in g for g in reasoning.rule_gaps(report))


def test_the_model_never_upgrades_and_a_legacy_label_is_never_supported():
    report = _stale_report()
    report["findings"][0].pop("verification")        # a report written before states were computed
    b = reasoning.bundle(report, LIGHT_Q)
    assert b["claims"][0]["state"] == "not_evaluated"     # "corroborated" counts sources, it is not a state
    ok, bad = reasoning.guard([{"text": "Verified: " + LIGHT, "sources": [1, 2], "claims": ["C1"]}], b)
    assert ok == [] and any("verified or confirmed" in r for r in bad[0]["reasons"])
    assert reasoning.SUPPORTED_STATES == frozenset({"supported"})


# ---- end to end: the orchestrator stores what the report and consolidation both decide ------------------------

def test_orchestrator_report_and_consolidation_agree_on_a_syndicated_copy(tmp_path, web):
    pages = {("a-news.example", "/h"): ("Halvorsen Bridge", "2026-10-21T08:00:00Z", STORY),
             ("b-daily.example", "/h"): ("Halvorsen Bridge", "2026-10-22T08:00:00Z", COPY)}
    for (host, path), (title, date, text) in pages.items():
        body = "".join(f"<p>{p.strip()}.</p>" for p in text.split(".") if p.strip())
        web.add(host, path, f"<html><head><title>{title}</title><meta property='article:published_time' "
                            f"content='{date}'></head><body><article><h1>{title}</h1>{body}</article></body></html>")
    path = tmp_path / "individual.sqlite"
    db = store.create(path, NOW)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, NOW)
        corpus = CorpusProvider([{"url": f"http://{h}{p}", "title": t, "snippet": x[:160]}
                                 for (h, p), (t, _, x) in pages.items()])
        services = svc_mod.build(db, path, fixture=web, providers=[corpus], clock=lambda: NOW)
        try:
            task = orchestrator.submit(db, BRIDGE_Q, NOW)
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        bridge = [f for f in done["result"]["findings"] if "1987" in f["statement"]]
        assert bridge and {f["verification"]["state"] for f in bridge} == {"insufficient"}, bridge
        stored = {v["key"]: v["state"] for v in state.task(db, done["id"])["checkpoint"]["verification"]}
        assert {stored[f["key"]] for f in bridge} == {"insufficient"}, stored
        assert db.execute("SELECT count(*) FROM web_knowledge WHERE statement LIKE '%1987%'").fetchone()[0] == 0
    finally:
        db.close()
