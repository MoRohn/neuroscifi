"""ADR-035 claim verification: categories, verification states and the per-category rules
(docs/knowledge/CLAIM_VERIFICATION.md). One test per rule; expected values are written from the policy, not read
back from the code."""
from datetime import datetime, timezone

import pytest

from neuroscifi.webcortex import claims as C
from neuroscifi.webcortex import synthesis as S
from neuroscifi.webcortex import temporal
from neuroscifi.webcortex.types import Document

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()
D = 86400.0
FV = {"mode": "fact_verification", "freshness_class": "stable"}


def doc(url, text, *, title="", published=NOW - 30 * D, kind="page", mode="live", metadata=None, canonical=None):
    return Document(url=url, final_url=url, canonical_url=canonical or url, domain=url.split("/")[2], title=title,
                    text=text, retrieved_at=NOW - 60, retrieval_mode=mode, kind=kind, published_at=published,
                    metadata=metadata or {}, flags={})


def verdict(statement, docs, assessment=None, *, now=NOW):
    claims = [c for d in docs for c in C.extract_claims(d, statement)]
    report = S.synthesize(statement, dict(assessment or FV, claim=statement), docs, C.cluster(claims), now=now)
    return report["verdict"]


# ---- 1. categories ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("statement,category", [
    ("the Corran Spire is in Harrowgate", "stable"),
    ("Orbix does not support encryption at rest", "stable"),
    ("the Halvorsen Bridge opened in 1987", "historical"),
    ("the Kestrel programming language was created by Ilse Marten", "historical"),
    ("the Tolvane Lighthouse is currently open to visitors", "time_sensitive"),
    ("The latest release of Quillstream is 4.2.0", "time_sensitive"),
    ("Quillstream 5.0.0 is released on 2 March 2027", "time_sensitive"),       # a future date is a plan
    ("the brain of the Ostra marsh newt contains 86 million neurons", "scientific"),
    ("Tessaly has a population of 251,000", "numerical"),
    ("Orbix listens on port 9000 by default", "numerical"),                    # 9000 is a port, not a year
    ("Quillstream has more than 40 contributors", "numerical"),                # a bound, not a comparison
    ("Quillstream is faster than Kafka", "comparative"),
    ("Quillstream is the best streaming library", "interpretive"),
    ("The Hera probe carries two cubesats named Milani and Juventas", "stable"),     # "carries": present
])
def test_claim_categories_are_deterministic(statement, category):
    assert temporal.claim_category(statement, now=NOW)[0] == category
    assert temporal.CATEGORY_FRESHNESS[category] in temporal.FRESHNESS


def test_registry_and_question_class_decide_the_category():
    assert temporal.claim_category("numpy 2.5.5 was released", now=NOW, registry=True)[0] == "registry"
    assert temporal.claim_category("Orbix listens on port 9000", now=NOW, freshness_class="volatile")[0] == \
        "time_sensitive"
    assert temporal.claim_category("the Halvorsen Bridge opened in 1987", now=NOW,
                                   freshness_class="volatile")[0] == "historical"       # a dated past event


# ---- 2. states ------------------------------------------------------------------------------------------------

def test_states_are_an_explicit_enum_with_legacy_labels():
    assert C.VERIFICATION_STATES == ("supported", "disputed", "insufficient", "conflicting",
                                     "temporally_unresolved", "not_evaluated")
    assert C.VerificationState("conflicting").value == "conflicting"
    assert set(C.STATE_TO_LEGACY) == set(C.VERIFICATION_STATES)
    assert set(C.STATE_TO_LEGACY.values()) == set(C.LEGACY_LABELS)


def test_stored_verdicts_are_normalised_on_read():
    assert C.normalise_state("contested") == "conflicting"
    assert C.normalise_state("Supported") == "supported" and C.normalise_state("nonsense") is None
    assert C.verdict_state({"label": "contested"}) == "conflicting"            # a v0.6 report
    assert C.verdict_state({"label": "insufficient", "shape": None}) == "not_evaluated"
    assert C.verdict_state({"label": "insufficient", "shape": "is"}) == "insufficient"
    assert C.verdict_state({"label": "insufficient", "state": "temporally_unresolved"}) == "temporally_unresolved"
    assert C.verdict_state(None) is None


@pytest.mark.parametrize("sup,con,sw,cw,mean,auth,state", [
    (2, 0, 1.6, 0.0, 0.722, False, "supported"),
    (2, 0, 1.4, 0.0, 0.706, False, "insufficient"),        # below the 0.72 floor
    (1, 0, 0.8, 0.0, 0.643, False, "insufficient"),        # one source
    (1, 0, 0.8, 0.0, 0.643, True, "supported"),            # one direct authoritative record
    (0, 1, 0.0, 0.8, 0.357, False, "disputed"),
    (1, 2, 0.8, 1.6, 0.43, False, "disputed"),             # 1.6 >= 1.5 x 0.8 from more groups
    (1, 2, 0.8, 1.1, 0.48, False, "conflicting"),          # more groups, but not 1.5 x the weight
    (1, 1, 0.6, 0.8, 0.47, False, "conflicting"),          # one against one is never decided
    (3, 1, 2.4, 0.8, 0.70, True, "conflicting"),           # a contradiction blocks "supported", even with authority
])
def test_verification_state_rules(sup, con, sw, cw, mean, auth, state):
    assert C.verification_state(sup_groups=sup, con_groups=con, sup_weight=sw, con_weight=cw, mean=mean,
                                authoritative=auth) == state


# ---- 3. rules on real synthesis ----------------------------------------------------------------------------

SPIRE = "the Corran Spire is in Harrowgate"
SPIRE_TEXTS = ["The Corran Spire is a lattice tower on the Weir Field in Harrowgate.",
               "The Corran Spire in Harrowgate has stood on the Weir Field since 1911."]


def test_credible_undated_sources_support_a_stable_fact():
    docs = [doc("https://wiki.example/spire", SPIRE_TEXTS[0], published=None),
            doc("https://travel.example/spire", SPIRE_TEXTS[1], published=None)]
    v = verdict(SPIRE, docs)
    assert (v["category"], v["state"], v["label"], v["basis"]) == ("stable", "supported", "supported",
                                                                    "corroboration")
    assert v["confidence"] == pytest.approx(2.6 / 3.6, abs=1e-3)
    assert verdict(SPIRE, docs[:1])["state"] == "insufficient"                 # one ordinary source is not enough


def test_numerical_claims_keep_the_undated_discount():
    statement = "the Varnel River is 412 km long"
    docs = [doc("https://wiki.example/v", "The Varnel River is 412 km long.", published=None),
            doc("https://atlas.example/v", "The Varnel River is 412 kilometres long and drains a wide basin.",
                published=None)]
    v = verdict(statement, docs)
    assert v["category"] == "numerical" and v["state"] == "insufficient"
    assert v["confidence"] == pytest.approx(2.2 / 3.2, abs=1e-3)


def test_time_sensitive_claims_need_evidence_that_establishes_the_timeframe():
    statement = "the Tolvane Lighthouse is currently open to visitors"
    texts = ["The Tolvane Lighthouse is open to visitors from May to September.",
             "The Tolvane Lighthouse is open to visitors every day in summer."]
    undated = [doc("https://wiki.example/t", texts[0], published=None),
               doc("https://heritage.example/t", texts[1], published=None)]
    v = verdict(statement, undated, {"mode": "fact_verification", "freshness_class": "volatile"})
    assert (v["category"], v["state"], v["label"]) == ("time_sensitive", "temporally_unresolved", "insufficient")
    assert v["timeframe"]["undated"] == 2 and v["timeframe"]["timely"] == 0
    old = [doc(d.url, d.text, published=NOW - 400 * D) for d in undated]      # dated, but not about now
    assert verdict(statement, old, {"mode": "fact_verification", "freshness_class": "volatile"})["state"] == \
        "temporally_unresolved"
    fresh = [doc(d.url, d.text, published=NOW - 3600) for d in undated]
    v = verdict(statement, fresh, {"mode": "fact_verification", "freshness_class": "volatile"})
    assert v["state"] == "supported" and v["timeframe"]["timely"] == 2


def test_a_superseded_current_claim_is_disputed_by_timely_evidence():
    statement = "the Marrow Creek ferry currently runs every 30 minutes"
    docs = [doc("https://ostra-lines.example/t", "The Marrow Creek ferry runs every 30 minutes throughout the day.",
                published=NOW - 600 * D),
            doc("https://ostranews.example/n", "The Marrow Creek ferry runs every 45 minutes since 18 November 2026, "
                "because one vessel is in dry dock.", published=NOW - 3600)]
    v = verdict(statement, docs, {"mode": "fact_verification", "freshness_class": "volatile"})
    assert v["state"] == "disputed" and v["timeframe"] == dict(v["timeframe"], timely=1, outside_window=1)


def test_an_official_primary_source_suffices_for_a_narrow_fact():
    statement = "Fennick was created by Oda Haraldsen"
    official = doc("https://fennick-project.example/about", "Fennick was created by Oda Haraldsen, who still leads "
                   "the project.", published=None)
    v = verdict(statement, [official])
    assert (v["category"], v["state"], v["basis"]) == ("historical", "supported", "official_source")
    blog = doc("https://buildnotes.example/f", "Fennick was created by Oda Haraldsen, who still leads the project.",
               published=None)
    assert verdict(statement, [blog])["state"] == "insufficient"               # not the subject's own site
    rival = doc("https://forum.example/f", "Fennick was not created by Oda Haraldsen, the forum moderators say.")
    assert verdict(statement, [official, rival])["state"] != "supported"       # authority never outvotes evidence
    size = "Fennick has 120 contributors"
    numbers = doc("https://fennick-project.example/team", "Fennick has 120 contributors on its main repository.",
                  published=None)
    assert verdict(size, [numbers])["state"] == "insufficient"                 # numbers are not "narrow facts"


def test_official_source_detection():
    claim = C.extract_claims(doc("https://docs.kestrel-lang.example/about", "Kestrel is a statically typed "
                                 "programming language created in 2019."), "Kestrel")[0]
    assert C.official_source(claim, frozenset({"kestrel"}))
    assert not C.official_source(claim, frozenset({"kestrel", "bird"}))
    other = C.extract_claims(doc("https://kestrel-birds.example/a", "Kestrel is a statically typed programming "
                                 "language created in 2019."), "Kestrel")[0]
    assert not C.official_source(other, frozenset({"kestrel"}))
    assert C.site_label("news.bbc.co.uk") == "bbc" and C.site_label("a.b.example") == "b"


WIRE = ("The Marrow Creek ferry is operated by Ostra Lines, the regional transport company.\n"
        "The crossing takes eleven minutes and carries up to 60 cars.")
COPY = ("The Marrow Creek ferry is operated by Ostra Lines, the regional transport company, which took over the "
        "route this summer.\nThe crossing takes eleven minutes and carries up to 60 cars.\n"
        "This story was originally published by Coastline Wire.")


def test_a_syndicated_copy_counts_as_one_independent_source():
    statement = "the Marrow Creek ferry is operated by Ostra Lines"
    docs = [doc("https://coastline-wire.example/s", WIRE), doc("https://harbourtimes.example/s", COPY)]
    assert C.origin_keys(docs[1]) == ["name:coastlinewire"] and C.origin_keys(docs[0]) == []
    v = verdict(statement, docs)
    assert v["state"] == "insufficient" and v["independent_groups"]["supporting"] == 1
    assert any("copies of another source" in r for r in v["reasons"])
    independent = doc("https://ferryguide.example/m", "The Marrow Creek ferry is operated by Ostra Lines.")
    assert verdict(statement, docs + [independent])["state"] == "supported"


@pytest.mark.parametrize("text,keys", [
    ("TESSALY (Reuters) - The port reopened on Monday.", ["name:reuters"]),
    ("The port reopened on Monday, by Associated Press staff.", ["name:ap"]),
    ("This article first appeared on Harbour Times and is republished with permission.", ["name:harbourtimes"]),
    ("The port reopened on Monday after repairs.", []),
])
def test_origin_attribution_cues(text, keys):
    assert C.origin_keys(doc("https://x.example/a", text)) == keys


def test_canonical_url_and_near_identical_documents_join_origins():
    copy = doc("https://mirror.example/a", WIRE, canonical="https://coastline-wire.example/s")
    copy = Document(**{**copy.__dict__, "domain": "mirror.example"})
    assert C.origin_keys(copy) == ["site:coastline-wire.example"]
    story = " ".join(f"Paragraph {i} of the ferry story reports what the operator said about route {i}." for i in
                     range(8))
    a, b = doc("https://one.example/a", story), doc("https://two.example/b", story + " Edited by the desk.")
    origins = C.document_origins([a, b])
    assert origins[a.url] == origins[b.url]
    short = [doc("https://one.example/s", "The ferry is operated by Ostra Lines."),
             doc("https://two.example/s", "The ferry is operated by Ostra Lines.")]
    assert C.document_origins(short) == {}               # short reports are compared sentence by sentence only


def test_comparable_contradictions_are_conflicting_not_a_fabricated_consensus():
    statement = "the Tolvane Lighthouse is 41 metres tall"
    docs = [doc("https://wiki.example/t", "The Tolvane Lighthouse is 41 metres tall.", published=None),
            doc("https://heritage.example/t", "The Tolvane Lighthouse is 38 metres tall, measured to the lantern.")]
    v = verdict(statement, docs)
    assert (v["state"], v["label"]) == ("conflicting", "contested")
    assert any("no consensus is assumed" in r for r in v["reasons"])


def test_disputed_needs_contradiction_that_outweighs_support():
    statement = "the Varnel River is 520 km long"
    support = doc("https://kayak.example/v", "The Varnel River is 520 km long, and we paddled all of it.")
    against = [doc("https://atlas.example/v", "The Varnel River is 412 km long."),
               doc("https://wiki.example/v", "The Varnel River is 412 kilometres long and drains a wide basin.")]
    assert verdict(statement, [support] + against)["state"] == "disputed"
    assert verdict(statement, [support, against[0]])["state"] == "conflicting"


def test_missing_and_unexaminable_evidence():
    v = verdict("the Tolvane Lighthouse was designed by Hanne Sollie",
                [doc("https://wiki.example/t", "The Tolvane Lighthouse was built in 1874 on Skerry Point.")])
    assert v["state"] == "insufficient"                                        # evidence read, none bears on it
    v = verdict("Quillstream is the best streaming library",
                [doc("https://blog.example/q", "Quillstream is the best streaming library we have used.")])
    assert (v["state"], v["label"], v["category"]) == ("not_evaluated", "insufficient", "interpretive")
    assert v["citations"] == [] and v["supporting"] == []


# ---- 4. ADR-033 as amended: what a registry record establishes ------------------------------------------------

def registry(versions, *, requested=None, requires_python=">=3.10", latest="2.5.5"):
    md = {"provider": "pypi", "package": "corvane", "ecosystem": "PyPI", "latest": latest,
          "released_at": NOW - 20 * D, "requires_python": requires_python, "versions_recent": versions,
          "summary": "Connection pooling", "provider_text": f"Latest release {latest} uploaded 2026-10-30."}
    if requested:
        md.update(requested)
    return doc("https://pypi.org/project/corvane/", f"corvane {latest} (PyPI)\n{md['provider_text']} Connection "
               "pooling", kind="api", mode="api", title=f"corvane {latest} (PyPI)", published=NOW - 20 * D,
               metadata=md)


RECORD = registry([{"version": "2.5.5", "yanked": False}, {"version": "2.5.4", "yanked": True},
                   {"version": "2.6.0rc1", "yanked": False}])
TECH = {"mode": "technical", "freshness_class": "release"}


def check(question, docs):
    claims = [c for d in docs for c in C.extract_claims(d, question)]
    return S.synthesize(question, TECH, docs, C.cluster(claims), now=NOW)["verdict"]


def test_publication_is_still_settled_by_the_registry_record():
    v = check("Was corvane 2.5.5 released?", [RECORD])
    assert (v["state"], v["category"], v["aspect"], v["basis"]) == ("supported", "registry", "published",
                                                                     "registry_record")


@pytest.mark.parametrize("question,state", [
    ("Is corvane 2.5.5 stable?", "supported"),               # final release, explicitly not yanked
    ("Is corvane 2.5.4 stable?", "disputed"),                # yanked
    ("Is corvane 2.6.0rc1 stable?", "disputed"),             # pre-release marker
    ("Is corvane 2.5.4 not stable?", "supported"),
])
def test_stable_is_decided_by_pre_release_markers_and_yank_flags(question, state):
    v = check(question, [RECORD])
    assert v["aspect"] == "stable" and v["state"] == state, v


def test_publication_alone_never_supports_stable_compatible_secure_or_recommended():
    published_only = registry([{"version": "2.5.5"}])        # published; no yank flag, no advisory data
    for question in ("Is corvane 2.5.5 stable?", "Is corvane 2.5.5 compatible with Python 3.12?",
                     "Is corvane 2.5.5 secure?", "Is corvane 2.5.5 recommended?"):
        v = check(question, [published_only])
        assert v["state"] != "supported" and v["label"] != "supported", (question, v)
    assert check("Was corvane 2.5.5 released?", [published_only])["state"] == "supported"
    assert check("Is corvane 2.5.5 recommended?", [RECORD])["state"] == "not_evaluated"
    assert check("Is corvane 2.5.5 compatible with Python 3.12?", [RECORD])["state"] == "insufficient"
    assert check("Is corvane 2.5.5 secure?", [RECORD])["state"] == "insufficient"


def test_declared_metadata_and_advisories_can_refute():
    v = check("Is corvane 2.5.5 compatible with Python 3.9?", [RECORD])
    assert v["state"] == "disputed" and "excludes Python 3.9" in v["reasons"][0]
    osv = doc("https://osv.dev/vulnerability/GHSA-xxxx-yyyy-zzzz", "Pool exhaustion in corvane", kind="api",
              mode="api", title="Pool exhaustion", metadata={
                  "provider": "osv", "id": "GHSA-xxxx-yyyy-zzzz", "package": "corvane", "ecosystem": "PyPI",
                  "summary": "Pool exhaustion", "fixed_versions": ["2.5.6"], "queried_version": None,
                  "affected_ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.5.6"}]}]})
    v = check("Is corvane 2.5.5 secure?", [RECORD, osv])
    assert v["state"] == "disputed" and "GHSA-xxxx-yyyy-zzzz" in v["reasons"][0]
    assert check("Is corvane 2.5.5 vulnerable?", [RECORD, osv])["state"] == "supported"
    fixed = check("Is corvane 2.5.6 secure?", [RECORD, osv])
    assert fixed["state"] == "insufficient"                  # fixed is not "secure": absence proves nothing


def test_helpers():
    assert S.prerelease("2.6.0rc1") and S.prerelease("1.0.0-beta.1") and S.prerelease("2.5.5.dev1")
    assert not S.prerelease("2.5.5") and not S.prerelease("2.5.5.post1")
    assert S._admits(">=3.10,<4", "3.12") is True and S._admits(">=3.10", "3.9") is False
    assert S._admits("~=3.10", "3.11") is True and S._admits("weird", "3.11") is None
