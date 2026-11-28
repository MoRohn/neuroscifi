"""What kind of content a record holds: the provenance vocabulary (ADR-049, Cognitive Genesis §5.5 and §7.3).

Five kinds, distinguished at the representation level:

EVIDENCE     What was observed or reported from outside the system, and what the evidence pipeline's fixed,
             deterministic rules derive from it: a sensor reading, the owner's answer, a fetched source, a sentence
             extracted from it, the verification state and the posterior those rules compute, and a knowledge item
             (what verified evidence supports). A rule cannot add a fact the evidence does not hold.
INFERENCE    A conclusion that goes beyond the evidence and that no deterministic rule checked: text a model wrote
             about the evidence, a relation or explanation a model or heuristic proposed. Useful as a lead, never a
             fact.
PREDICTION   An expectation about a later observation, with what would settle it: a hypothesis with its testable
             prediction, a forecast with a probability. It is scored against what is later observed, never counted
             as what was observed.
SIMULATION   The output of running a model forward under stated assumptions: a what-if, a scenario, a counterfactual,
             a rollout. True only of the model and its assumptions, never of the world.
SPECULATION  A possibility put forward with neither evidence nor a model behind it: a brainstormed alternative, a
             templated competing explanation.

INFERENCE, SIMULATION and SPECULATION are QUARANTINED: stored only in the imagined store (instance/imagined.py,
schema 17), which no knowledge, claim, verification, calibration, belief or evidence path reads, and which the
database refuses to let any of their reference columns name. PREDICTION keeps its own tables (web_hypotheses,
web_predictions), which are settled by evidence and never become evidence.

`mapping()` is the read-only map of today's record types to these kinds. It retags and rewrites nothing; it documents
what each table already is:
  - claims and their sources are EVIDENCE, as are the instance's evidence journal, consolidated readings, epoch
    streams and datasets. Evidence and datasets with provenance 'simulated' are observations of a simulated
    ENVIRONMENT (ADR-014: an acceptance harness feeding the evidence channels), labelled as such since schema 1; they
    are observations, not content the system imagined, so they are EVIDENCE with that label, never SIMULATION.
  - knowledge items are EVIDENCE in the sense "what verified evidence supports" (provenance verified_conclusion, or
    user_asserted: the owner's own statement); beliefs are computed on read from evidence by the exact rule.
  - hypotheses and predictions are PREDICTION.
  - no existing record is SIMULATION. Two existing fields are model or template text and are named here so nobody
    mistakes them for evidence: the model-written answer stored in a task's report (web_tasks.result "reasoning",
    labelled generated_by model:<name>; INFERENCE) and a hypothesis's template alternatives after a prediction error
    (web_hypotheses.alternatives, labelled "template"; SPECULATION). Both are already labelled and never consolidated
    (consolidate reads only claim clusters); they stay where they are.

The artifact store (ADR-071, schema 20, artifacts/store.py) adds two record types and amends nothing above:
  - a GIVEN artifact (origin 'given') is a file the owner handed over, kept as its bytes: a record of what was given,
    EVIDENCE only in the sense that the owner reported it. What it says is untrusted and unverified; no evidence,
    claim, knowledge or belief path reads it, and no evidence-side reference column may name it (schema 20).
  - a MADE artifact (origin 'made') is content the system produced from a specification by a fixed generator (a
    chart, a sound, a model output). It is its own quarantined class, MADE: never evidence and never knowledge
    whatever it renders, even when every number in it came from evidence (the evidence stays where it is; a picture
    of it is not a second copy of it). It lives only in the artifact store's 'made' rows, every listing labels it
    MADE_NOT_EVIDENCE, no evidence-side reference column may name it, and forgetting any artifact it was made from
    removes it. QUARANTINED stays the imagined store's kinds: MADE is held apart in a store of its own, under the same
    rules (never_evidence()).
"""
from typing import Literal

EVIDENCE = "evidence"
INFERENCE = "inference"
PREDICTION = "prediction"
SIMULATION = "simulation"
SPECULATION = "speculation"

Kind = Literal["evidence", "inference", "prediction", "simulation", "speculation"]
KINDS: tuple[Kind, ...] = (EVIDENCE, INFERENCE, PREDICTION, SIMULATION, SPECULATION)
QUARANTINED: frozenset[str] = frozenset({INFERENCE, SIMULATION, SPECULATION})

# What every listing of quarantined content says beside each record.
NOT_EVIDENCE = "imagined, not evidence"
# The class of what the system made in the artifact store (ADR-071), and what every listing of one says beside it.
MADE = "made"
MADE_NOT_EVIDENCE = "made by the system, not evidence"

# (record, table or field, kind, basis). Read-only: nothing here changes a row.
_MAPPING = (
    ("evidence event", "evidence", EVIDENCE, "a reading through a channel (camera, word, reference); provenance "
     "'simulated' marks an observation of a simulated environment (ADR-014), not imagined content"),
    ("consolidated reading", "evidence_consolidated", EVIDENCE, "what an expired learning event taught (ADR-044)"),
    ("epoch stream", "epoch_streams", EVIDENCE, "observed population states of an epoch"),
    ("dataset", "datasets", EVIDENCE, "population trajectories; provenance 'simulated' marks a simulated "
     "environment, as for evidence"),
    ("belief", "computed on read (belief_revisions keeps summaries)", EVIDENCE,
     "the exact posterior the evidence supports, computed by a fixed rule"),
    ("web source", "web_sources", EVIDENCE, "a document a task read"),
    ("claim", "web_claims", EVIDENCE, "a sentence extracted from a source"),
    ("knowledge item", "web_knowledge", EVIDENCE, "what verified evidence supports (verified_conclusion) or the "
     "owner asserted (user_asserted)"),
    ("hypothesis", "web_hypotheses", PREDICTION, "a provisional statement with a testable prediction"),
    ("prediction", "web_predictions", PREDICTION, "an expectation with criteria, a deadline and a probability"),
    ("model-written answer", "web_tasks.result reasoning", INFERENCE, "labelled generated_by model:<name>; never "
     "consolidated; the guard checks each sentence against cited evidence"),
    ("template alternatives", "web_hypotheses.alternatives", SPECULATION, "labelled template text after a "
     "prediction error; never consolidated"),
    ("imagined record", "imagined", "simulation | speculation | inference", "the quarantined store (schema 17)"),
    ("given artifact", "artifacts (origin 'given')", EVIDENCE, "a file the owner handed over, kept as given "
     "(ADR-071): reported, not verified; no evidence path reads it and no evidence-side column may name it"),
    ("made artifact", "artifacts (origin 'made')", MADE, "what the system produced from a specification (ADR-071): "
     "quarantined in the artifact store, labelled made by the system, never evidence"),
)


def mapping() -> list[dict]:
    """Today's record types and the provenance kind of each (read-only; see the module doc)."""
    return [{"record": record, "table": table, "kind": kind, "basis": basis} for record, table, kind, basis in _MAPPING]


def quarantined(kind) -> bool:
    """Whether content of this kind may only live in the imagined store."""
    return kind in QUARANTINED


def never_evidence(kind) -> bool:
    """Whether content of this kind is never evidence: the imagined kinds and what the system made (ADR-071)."""
    return kind in QUARANTINED or kind == MADE
