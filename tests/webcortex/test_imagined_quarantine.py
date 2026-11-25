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


# ---- by construction: only the modules allowed here touch the store ------------------------------------------------
#
# An ALLOW-LIST (review0 L5): every module under src/neuroscifi is scanned, and a module not named below may neither
# import the store nor carry SQL naming its tables. Each entry gives the reason it is allowed.
#   STORE     the store, its schema and lifecycle (bounds, forgets, sweep, restore) and the owner's own commands:
#             any use. Closed: a mechanism never goes here.
#   WRITERS   a mechanism that records what it imagined: only the write API (WRITE_API), never SQL on the store.
#   DISPLAYS  a surface that lists records, each labelled "imagined, not evidence": only DISPLAY_API, never SQL.
# No evidence path (FORBIDDEN: knowledge, claims, consolidation, verification, calibration, beliefs, retrieval, the
# answer bundle) may be added to any category: those modules may not read the store at all.
#
# How a lane adds itself: put its module (the path under src/neuroscifi) in WRITERS or DISPLAYS with a one-line reason
# saying what it writes or shows, import the store as a module (`from ..instance import imagined`) and use only the
# names its category allows. test_the_allow_lists_are_well_formed checks the entry; the scan below checks the use.
STORE = {
    "instance/imagined.py": "the store itself: records, lineage, bounds and removals",
    "instance/schema.py": "creates the store's tables and triggers (migration 17)",
    "instance/store.py": "a lowered max_imagined_rows or lifetime applies to the records held (imagined.bound)",
    "instance/governance.py": "every forget removes what derives from what it removed; the sweep expires records",
    "instance/checkpoints.py": "a restore replays the store's removals (tombstones, limits, orphans)",
    "instance/cli_imagined.py": "the owner's `nsf imagined list|show|purge|kinds`, every item labelled",
}
WRITERS: dict[str, str] = {}
DISPLAYS: dict[str, str] = {}
WRITE_API = {"record", "observe", "example", "KINDS", "REF_KINDS", "OBSERVATION_KINDS", "MAX_REFS", "NOT_EVIDENCE"}
DISPLAY_API = {"listing", "show", "present", "KINDS", "NOT_EVIDENCE"}
FORBIDDEN = ["webcortex/knowledge.py", "webcortex/claims.py", "webcortex/consolidate.py", "webcortex/synthesis.py",
             "webcortex/evolution.py", "webcortex/predictions.py", "webcortex/semantic.py",
             "webcortex/memory_gate.py", "webcortex/reasoning.py", "webcortex/state.py", "webcortex/temporal.py",
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


def _store_api(source):
    """The names of the store a module uses: attributes of the module bound by `from ... import imagined [as x]`,
    and names imported from it. "*" for any other reference (the module passed around, `instance.imagined`, a plain
    `import`), "SQL" for SQL naming the store's tables: neither is in any category's API."""
    tree = ast.parse(source)
    aliases, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("imagined"):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            aliases |= {a.asname or a.name for a in node.names if a.name == "imagined"}
        elif isinstance(node, ast.Import) and any(a.name.endswith("imagined") for a in node.names):
            names.add("*")
    attributes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            names.add(node.attr)
            attributes.add(id(node.value))
        elif isinstance(node, ast.Attribute) and node.attr == "imagined":
            names.add("*")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in aliases and id(node) not in attributes:
            names.add("*")
    if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and re.search(
            r"\b(?:FROM|JOIN|INTO|UPDATE)\s+imagined", n.value, re.IGNORECASE) for n in ast.walk(tree)):
        names.add("SQL")
    return names


def test_only_an_allowed_module_touches_the_store_and_only_through_its_api():
    wrong = {}
    for file in sorted(SRC.rglob("*.py")):
        path, source = str(file.relative_to(SRC)), file.read_text()
        if path in STORE:
            continue
        used = _store_api(source)
        allowed = WRITE_API if path in WRITERS else DISPLAY_API if path in DISPLAYS else set()
        if not used <= allowed or (not allowed and _store_uses(source)):
            wrong[path] = sorted(used - allowed) or _store_uses(source)
    assert wrong == {}, ("these modules touch the imagined store outside their category: allow a mechanism in WRITERS "
                         "or DISPLAYS with a reason, use only that category's API, or remove the use")


def test_the_allow_lists_are_well_formed():
    """Every entry names a real module and says why; no module is in two categories; no evidence path in any."""
    lists = {"STORE": STORE, "WRITERS": WRITERS, "DISPLAYS": DISPLAYS}
    seen = [m for allowed in lists.values() for m in allowed]
    assert len(seen) == len(set(seen)), "a module is in two categories"
    for name, allowed in lists.items():
        for module, reason in allowed.items():
            assert (SRC / module).is_file(), f"{name}: {module} does not exist"
            assert isinstance(reason, str) and len(reason.split()) >= 4, f"{name}: {module} needs a reason"
    for module in FORBIDDEN:
        assert (SRC / module).is_file(), module
        assert module not in seen, f"{module} is an evidence path: it may never read the imagined store"
    assert not (WRITE_API | DISPLAY_API) & {"purge", "purge_orphans", "expire", "bound", "replay", "section", "*",
                                            "SQL"}


def test_the_scan_would_see_a_use():
    for source in ("from ..instance import imagined\n", "from neuroscifi.instance.imagined import record\n",
                   "import neuroscifi.instance.imagined\n", "from .. import instance\ninstance.imagined.listing\n",
                   "X = 'SELECT id FROM imagined WHERE kind=?'\n", "X = 'DELETE FROM imagined_refs'\n"):
        assert _store_uses(source), source
    assert _store_uses("X = 'the imagined kinds are quarantined'\n") == []
    assert _store_api("from ..instance import imagined\nimagined.record(db)\nimagined.listing(db, 0)\n") == {
        "record", "listing"}
    assert _store_api("from ..instance import imagined as i\nf(i)\n") == {"*"}
    assert _store_api("from ..instance.imagined import purge\n") == {"purge"}
    assert _store_api("from .. import instance\ninstance.imagined.show\n") == {"*"}
    assert _store_api("X = 'SELECT payload FROM imagined'\n") == {"SQL"}
    assert _store_api("from ..instance import imagined\nimagined.listing(db, 0)\n") <= DISPLAY_API
    assert not _store_api("from ..instance import imagined\nimagined.listing(db, 0)\n") <= WRITE_API


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


def test_a_page_titled_like_an_imagined_id_is_consolidated_like_any_other(db):
    """review0 M2: the triggers matched an id anywhere in a JSON column, so a web page whose title (copied into
    web_knowledge.sources) or quoted text (web_knowledge.contradicting) read "imagined:<32 hex>" aborted the whole
    consolidation of its task, every time. Only reference columns are tested now."""
    title = "imagined:" + "deadbeef" * 4
    first = two_sources()
    for c in first.claims:
        c.title = title
    tid, out = consolidated(db, [first])
    assert len(out["provisional"]) == 1
    kid = out["provisional"][0]
    assert {s["title"] for s in knowledge.row(db, kid)["sources"]} == {title}
    again = two_sources(hosts=("c.example", "d.example"))
    for c in again.claims:
        c.title = title.upper()
    _, more = consolidated(db, [again], T0 + DAY)                  # the strengthening update rewrites the sources
    assert more["strengthened"] == [kid] and len(knowledge.row(db, kid)["sources"]) == 4
    against = two_sources(text=f"NumPy 2.4.0 ({title}) is the latest release of the NumPy library.",
                          hosts=("e.example", "f.example"))
    _, contested = consolidated(db, [against], T0 + 2 * DAY)       # quoted in `contradicting`, beside the claim id
    assert [c["knowledge_id"] for c in contested["contested"]] == [kid]
    assert title in knowledge.row(db, kid)["contradicting"][0]["text"]


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
