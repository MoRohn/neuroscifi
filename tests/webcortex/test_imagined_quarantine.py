"""The imagined store (ADR-049) seen from the Web Cortex: nothing imagined is evidence, knowledge, a claim, a
verification, a calibration settlement or an answer's citation; retrieval never returns it; a web forget removes what
was imagined from what it forgot, and a restore never brings it back.

A canary record (CANARY in its payload and question) is planted beside real knowledge in each test: whatever the
evidence paths produce must be what they produce without it."""
import ast
import json
from pathlib import Path
import re
import sqlite3

import pytest

import neuroscifi
from neuroscifi.instance import checkpoints, governance, imagined, memory
from neuroscifi.webcortex import evolution, knowledge, predictions, reasoning, semantic
from test_consolidate import DAY, T0, consolidated, open_db, two_sources
from test_semantic_memory import Models

CANARY = "zorblaxian"
SRC = Path(neuroscifi.__file__).parent


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


def plant(db, derived_from=(), seed=1, now=T0):
    """An imagined record that reads like the knowledge it sits beside, plus the canary."""
    args = imagined.example(seed, derived_from=derived_from, question=f"what if numpy {CANARY} is the latest release?")
    args["payload"] = {"statement": f"NumPy 9.9.9 {CANARY} is the latest release of the NumPy library.",
                       "key": "numpy latest version"}
    return imagined.record(db, now=now, **args)


def dump(db):
    """Every row of every table but the imagined store's, as text."""
    tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'imagined%'"
                                       " AND name NOT LIKE 'sqlite_%'")]
    return "\n".join(repr(db.execute(f"SELECT * FROM {t}").fetchall()) for t in tables)


# ---- by construction: the evidence code never reads the store -----------------------------------------------------

# The knowledge, claims, consolidation, verification, calibration, belief, evidence and retrieval paths.
EVIDENCE_PATHS = ["webcortex/knowledge.py", "webcortex/claims.py", "webcortex/consolidate.py", "webcortex/synthesis.py",
                  "webcortex/evolution.py", "webcortex/predictions.py", "webcortex/semantic.py",
                  "webcortex/memory_gate.py", "webcortex/reasoning.py", "webcortex/curiosity.py",
                  "webcortex/autonomy.py", "webcortex/orchestrator.py", "webcortex/state.py", "webcortex/temporal.py",
                  "instance/beliefs.py", "instance/evidence.py", "instance/executive.py", "instance/memory.py",
                  "instance/datasets.py", "instance/graphs.py", "instance/learner.py", "instance/recall.py"]


def _store_uses(source):
    """Imports of the imagined store and SQL naming its tables, in one module's source."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
                (node.module or "").endswith("imagined") or any(a.name == "imagined" for a in node.names)):
            found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import) and any(a.name.endswith("imagined") for a in node.names):
            found.append("import ...imagined")
        elif isinstance(node, ast.Attribute) and node.attr == "imagined":
            found.append(".imagined")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(
                r"\b(?:FROM|JOIN|INTO|UPDATE)\s+imagined", node.value, re.IGNORECASE):
            found.append(node.value[:60])
    return found


@pytest.mark.parametrize("path", EVIDENCE_PATHS)
def test_no_evidence_path_reads_or_writes_the_imagined_store(path):
    assert _store_uses((SRC / path).read_text()) == [], path


def test_the_scan_would_see_a_use():
    for source in ("from ..instance import imagined\n", "from neuroscifi.instance.imagined import record\n",
                   "import neuroscifi.instance.imagined\n", "from .. import instance\ninstance.imagined.listing\n",
                   "X = 'SELECT id FROM imagined WHERE kind=?'\n", "X = 'DELETE FROM imagined_refs'\n"):
        assert _store_uses(source), source
    assert _store_uses("X = 'the imagined kinds are quarantined'\n") == []


def test_consolidation_and_calibration_are_the_same_with_imagined_records_beside_them(tmp_path):
    def run(path, with_canary):
        path.mkdir()
        opened = open_db(path)
        db = next(opened)
        if with_canary:
            plant(db, seed=1)
            plant(db, seed=2)
        tid, out = consolidated(db, [two_sources()])
        kid = out["provisional"][0]
        pid = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0)["id"]
        if with_canary:
            plant(db, derived_from=[("knowledge", kid), ("task", tid), ("prediction", pid)], seed=3)
        _, again = consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + DAY)
        result = (out, again, evolution.calibration(db), knowledge.query(db, "numpy latest release", T0 + DAY))
        text = dump(db)
        opened.close()
        return json.loads(json.dumps(result, default=str).replace(tid, "TASK")), text
    (a, _), (b, text) = run(tmp_path / "a", False), run(tmp_path / "b", True)
    assert a[2] == b[2] and a[3]["items"] == b[3]["items"]
    assert [x.get("provisional") for x in a[:2]] == [x.get("provisional") for x in b[:2]]
    assert CANARY not in text                      # no evidence-side row of the file holds imagined text


# ---- retrieval never returns it -----------------------------------------------------------------------------------

def test_lexical_retrieval_and_memory_never_return_an_imagined_record(db):
    _, out = consolidated(db, [two_sources()])
    rid = plant(db, derived_from=[("knowledge", out["provisional"][0])])
    for topic in (CANARY, "numpy latest release", "what if numpy is the latest release", rid):
        for cached in (True, False):
            found = json.dumps(knowledge.query(db, topic, T0 + 1, cached=cached)["items"])
            assert CANARY not in found and rid not in found, topic
    assert "imagined" not in memory.KINDS
    with pytest.raises(Exception):
        memory.retrieve(db, "imagined")
    for kind in memory.KINDS:
        found = json.dumps(memory.retrieve(db, kind, limit=1000), default=str)
        assert CANARY not in found and rid not in found, kind


def test_semantic_retrieval_never_shows_an_imagined_record_to_a_model(db, monkeypatch):
    pair = [Models("embedder"), Models("reranker")]
    monkeypatch.setattr(semantic, "_gateways", lambda db: tuple(pair))
    semantic.set_config(db, {"enabled": True}, T0)
    _, out = consolidated(db, [two_sources()])
    plant(db, derived_from=[("knowledge", out["provisional"][0])])
    found = knowledge.query(db, "what if numpy is the latest release", T0 + 1)
    assert found["retrieval"]["mode"] == "semantic" and pair[0].seen
    assert CANARY not in json.dumps(found) and not any(CANARY in t for m in pair for t in m.seen)


# ---- the answer model's evidence and the guard ----------------------------------------------------------------------

Q = "What is the latest version of the Zephyr compiler?"
FACT = "The Zephyr compiler version 4.2.0 was released on 17 November 2026."
IMAGINED = f"The Zephyr compiler version 5.0.0 {CANARY} will be released in 2027."


def report():
    supported = {"state": "supported", "category": "historical", "basis": "corroboration"}
    return {"question": Q, "as_of": T0,
            "findings": [{"statement": FACT, "status": "corroborated", "confidence": 0.8, "citations": [1, 2],
                          "verification": supported},
                         {"statement": IMAGINED, "status": "corroborated", "confidence": 0.9, "citations": [1, 3],
                          "verification": supported, "provenance": "simulation"}],
            "sources": [{"n": n, "title": f"Source {n}", "domain": f"s{n}.example", "published_at": T0 - DAY,
                         "retrieved_at": T0, "kind": "page"} for n in (1, 2)]
            + [{"n": 3, "title": f"Imagined {CANARY}", "domain": "imagined", "kind": "page",
                "provenance": "speculation"}],
            "verdict": None}


def test_the_bundle_never_holds_quarantined_content():
    b = reasoning.bundle(report(), Q)
    assert [c["text"] for c in b["claims"]] == [FACT] and [s["id"] for s in b["sources"]] == [1, 2]
    assert CANARY not in json.dumps(b)
    only = report()
    only["findings"] = only["findings"][1:]
    only["answer"] = [{"text": IMAGINED, "citations": [3], "status": "corroborated", "provenance": "inference"}]
    assert reasoning.bundle(only, Q)["claims"] == []


def test_a_separate_imagined_section_never_reaches_the_model_and_cannot_be_cited(db):
    rid = plant(db)
    b = reasoning.bundle(report(), Q) | {"imagined": imagined.section([imagined.show(db, rid)])}
    assert b["imagined"]["label"] == "imagined, not evidence"
    sent = json.dumps(reasoning.messages(b))
    assert CANARY not in sent and rid not in sent
    ok, bad = reasoning.guard([{"text": "NumPy 9.9.9 is the latest release of the NumPy library.", "sources": [1],
                                "claims": [rid]}], b)
    assert ok == [] and "unknown claim" in " ".join(bad[0]["reasons"])


def test_the_guard_never_resolves_a_citation_to_quarantined_content():
    b = reasoning.bundle(report(), Q)
    b["claims"].append({"id": "C2", "state": "supported", "sources": [3], "text": IMAGINED, "provenance": "simulation"})
    b["sources"].append({"id": 3, "title": "Imagined", "provenance": "simulation"})
    ok, bad = reasoning.guard([{"text": IMAGINED, "sources": [3], "claims": ["C2"]}], b)
    assert ok == [] and any("unknown claim" in r for r in bad[0]["reasons"]) \
        and any("unknown source" in r for r in bad[0]["reasons"])
    ok, _ = reasoning.guard([{"text": FACT, "sources": [1, 2], "claims": ["C1"]}], b)
    assert len(ok) == 1                              # evidence beside it is judged as before


# ---- web forgets cover it, restores never bring it back -----------------------------------------------------------

def test_forgetting_a_task_a_knowledge_item_or_a_source_removes_what_was_imagined_from_it(db):
    tid, _ = consolidated(db, [two_sources()])
    claim = db.execute("SELECT id FROM web_claims WHERE task_id=? LIMIT 1", (tid,)).fetchone()[0]
    other, out = consolidated(db, [two_sources(key="orbix latest version", hosts=("x.example", "y.example"),
                                               text="Orbix 3.1 is the latest release of Orbix.")], T0 + 1)
    kid = out["provisional"][0]
    by_task = plant(db, derived_from=[("task", tid)], seed=1)
    by_claim = plant(db, derived_from=[("claim", claim)], seed=2)
    by_knowledge = plant(db, derived_from=[("knowledge", kid)], seed=3)
    source = db.execute("SELECT id FROM web_sources WHERE domain='x.example'").fetchone()
    other_claim = db.execute("SELECT id FROM web_claims WHERE domain='x.example'").fetchone()[0]
    by_source = plant(db, derived_from=[("claim", other_claim)] + ([("source", source[0])] if source else []), seed=4)
    unrelated = plant(db, derived_from=[("task", other)], seed=5)
    assert governance.forget_research(db, tid, T0 + 2)["effects"]["imagined_removed"] == 2
    held = lambda: {i["id"] for i in imagined.listing(db, T0 + 3)["items"]}     # noqa: E731
    assert held() == {by_knowledge, by_source, unrelated} and not held() & {by_task, by_claim}
    assert governance.forget_knowledge(db, kid, T0 + 4)["effects"]["imagined_removed"] == 1
    assert governance.forget_source(db, "x.example", T0 + 5)["effects"]["imagined_removed"] == 1
    assert held() == {unrelated}


def test_a_restore_never_brings_back_what_was_imagined_from_a_forgotten_task(db, tmp_path):
    tid, _ = consolidated(db, [two_sources()])
    rid = plant(db, derived_from=[("task", tid)])
    path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    governance.forget_research(db, tid, T0 + 2)
    out = checkpoints.restore(db, cp["id"], tmp_path / "restored.sqlite", T0 + 3)
    assert out["imagined_removed"] == 1 and out["web_removed"]["tasks"] == 1
    copy = sqlite3.connect(tmp_path / "restored.sqlite")
    try:
        assert copy.execute("SELECT count(*) FROM imagined WHERE id=?", (rid,)).fetchone()[0] == 0
    finally:
        copy.close()
    assert CANARY.encode() not in (tmp_path / "restored.sqlite").read_bytes()


def test_a_hypothesis_or_prediction_forgotten_with_its_task_takes_its_imagined_records(db):
    tid, out = consolidated(db, [two_sources()])
    pid = predictions.record(db, out["provisional"][0], "still holds", T0 + DAY, T0)["id"]
    plant(db, derived_from=[("prediction", pid)])
    assert governance.forget_knowledge(db, out["provisional"][0], T0 + 1)["effects"]["imagined_removed"] == 1
    assert imagined.listing(db, T0 + 2)["items"] == []
