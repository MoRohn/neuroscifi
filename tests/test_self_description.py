"""The self-description record (self_description.py, ADR-053): one structured record, every field with its source and
age, served by the console, `nsf self`, MCP, voice and Pocket; it agrees with the existing surfaces at the same
state_version; Pocket's family scope never carries the owner's questions, decisions, beliefs or tools; building it
writes nothing; it claims nothing but functions."""
from contextlib import closing
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from neuroscifi import self_description as sd
from neuroscifi.acceptance.cognition import selfmodel, splits
from neuroscifi.instance import evidence, store
from neuroscifi.webcortex import orchestrator, state

ROOT = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, T0)) as db:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    return path


def fields(record, prefix=""):
    """(path, field) for every leaf field of the record."""
    for key, node in record.items():
        if not isinstance(node, dict):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if "source" in node and "value" in node:
            yield path, node
        elif key != "answers":
            yield from fields(node, path)


def staged(spec_name):
    for spec in splits.SELF_MODEL["development"]["states"]:
        if spec["name"] == spec_name:
            return spec
    raise KeyError(spec_name)


def test_every_field_names_its_source_and_age_and_the_five_questions_resolve(individual):
    with closing(store.connect(individual)) as db:
        record = sd.build(db, individual, T0 + 10)
    assert record["schema"] == sd.SCHEMA and record["scope"] == "owner"
    found = dict(fields(record))
    assert len(found) >= 20
    for path, node in found.items():
        assert isinstance(node["source"], str) and node["source"], path
        assert set(node) == {"value", "source", "at", "age_seconds"}, path
        assert node["age_seconds"] is None or node["age_seconds"] >= 0, path
    for question, paths in record["answers"].items():
        assert paths, question
        for p in paths:
            assert p in found or any(f.startswith(p + ".") for f in found), (question, p)   # a field or a group
    json.dumps(record, allow_nan=False)


@pytest.mark.parametrize("name", ["busy afternoon", "quiet night"])
def test_the_record_answers_every_probe_of_a_staged_state_from_one_surface(name):
    """All 15 probes of the benchmark's staged states, from the record alone (no other surface consulted)."""
    from neuroscifi.acceptance.cognition import selfrecord
    spec = staged(name)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "s.sqlite"
        db = store.create(path, selfmodel.T0)
        with closing(db), selfmodel.staged_memory(spec["memory_level"]):
            now = selfmodel.T0 + 3600
            selfmodel.stage(db, path, spec, 41, now)
            describer = selfrecord.RecordDescriber()
            surfaces = describer.surfaces(db, path, now + 2)
            assert set(surfaces) == {"self"}
            truth = selfmodel.truth(spec, store.config(db).max_evidence_rows)
            for probe in selfmodel.PROBES:
                assert selfmodel._equal(describer.describe(probe, surfaces), truth[probe]), probe
            assert describer.checks[0]["comparable"] and describer.checks[0]["mismatches"] == []


def test_an_ablated_section_cannot_answer_its_probes(individual):
    from neuroscifi.acceptance.cognition import selfrecord
    with closing(store.connect(individual)) as db:
        evidence.start_epoch(db, "e1", T0)
        surfaces = {"self": sd.build(db, individual, T0 + 1)}
    full, ablated = selfrecord.RecordDescriber(), selfrecord.RecordDescriber({"uncertainty", "decisions", "tools"})
    for probe in ("uncertain_about", "last_decision", "authorised_model_tools"):
        assert full.describe(probe, surfaces) is not None and ablated.describe(probe, surfaces) is None


def test_consistent_with_the_existing_surfaces_and_not_comparable_after_a_change(individual):
    with closing(store.connect(individual)) as db:
        orchestrator.submit(db, "What changed in release 4 of the staged package?", T0, origin="console")
        record = sd.build(db, individual, T0 + 5)
        check = sd.consistency(db, individual, record, T0 + 5)
        assert check["comparable"] and check["checks"] >= 10 and check["mismatches"] == []
        orchestrator.submit(db, "What changed in release 5 of the staged package?", T0 + 6, origin="console")
        later = sd.consistency(db, individual, record, T0 + 7)
        assert later["comparable"] is False and later["checks"] == 0


def test_a_tampered_record_is_caught(individual):
    with closing(store.connect(individual)) as db:
        record = sd.build(db, individual, T0 + 5)
        record["doing"]["questions"]["value"]["open"] += 1
        check = sd.consistency(db, individual, record, T0 + 5)
    assert [m["field"] for m in check["mismatches"]] == ["doing.questions.open"]


def test_building_writes_nothing(individual):
    with closing(store.connect(individual)) as db:
        evidence.start_epoch(db, "e1", T0)
        before = (store.state_version(db), db.total_changes)
        sd.build(db, individual, T0 + 5)
        assert (store.state_version(db), db.total_changes) == before


def test_uncertain_and_next_step_come_from_the_beliefs_and_the_executive(individual):
    with closing(store.connect(individual)) as db:
        evidence.start_epoch(db, "e1", T0)
        for i in range(4):            # no reference readings: the mechanism stays undetermined
            evidence.observe(db, {"id": f"c{i}", "epoch": "e1", "channel": "camera", "root": "cam", "action": 0,
                                  "bit": 0}, T0 + i)
        record = sd.build(db, individual, T0 + 10)
    assert sd.value(record, "uncertain.beliefs")["least_certain"] == "mechanism"
    now = sd.value(record, "decisions.executive_now")
    assert now["decision"] == "test_reference" and "p_inverted" in now["reason"]
    assert any("least certain about the mechanism" in s for s in sd.short(record))


def test_family_scope_carries_only_what_pocket_may_see(individual):
    with closing(store.connect(individual)) as db:
        mine = orchestrator.submit(db, "Is the Okapi compiler 2.0 out yet?", T0, origin="pocket", family="fam-a")
        other = orchestrator.submit(db, "Is the Zephyr compiler 4.2 out yet?", T0 + 1, origin="pocket",
                                    family="fam-b")
        owner = orchestrator.submit(db, "Where does the okapi live?", T0 + 2, origin="console")
        db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(NULL,'ask','web:x','?',?)",
                   (T0,))
        record = sd.build(db, individual, T0 + 5, scope="family", family="fam-a")
        ids = [t["id"] for t in sd.value(record, "doing.tasks")["items"]]
        assert ids == [mine["id"]]
        for absent in ("knows", "uncertain", "decisions"):
            assert absent not in record
        assert "questions" not in record["doing"] and "tools" not in record["can"]
        assert [x["task_id"] for x in sd.value(record, "recent")] == [mine["id"]]
        state.set_config(db, {"pocket_recent": True}, T0 + 6)     # the owner shares their own questions
        shared = sd.value(sd.build(db, individual, T0 + 7, scope="family", family="fam-a"), "recent")
        assert [x["task_id"] for x in shared] == [owner["id"], mine["id"]]
        assert [x["openable"] for x in shared] == [False, True] and other["id"] not in {x["task_id"] for x in shared}
        assert [x["task_id"] for x in sd.value(sd.build(db, individual, T0 + 8), "recent")] == [
            owner["id"], other["id"], mine["id"]]
        with pytest.raises(ValueError):
            sd.build(db, individual, T0, scope="family")


def test_no_claim_of_experience_in_what_it_says():
    banned = re.compile(r"\b(?:conscious|consciousness|sentien\w*|aware|awareness|feel\w*|emotion\w*|mood)\b", re.I)
    source = (ROOT / "src" / "neuroscifi" / "self_description.py").read_text()
    words = [s for s in re.findall(r'"([^"\n]{12,})"', source)]
    assert not [w for w in words if banned.search(w)]
    assert banned.search("it is aware") and not banned.search("software")


def test_nsf_self_prints_the_record_and_short_sentences(individual):
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin", "HOME": str(individual.parent)}
    full = subprocess.run([sys.executable, "-m", "neuroscifi.cli", "self", "--db", str(individual)],
                          capture_output=True, text=True, timeout=60, env=env)
    assert full.returncode == 0, full.stderr
    assert json.loads(full.stdout)["schema"] == sd.SCHEMA
    short = subprocess.run([sys.executable, "-m", "neuroscifi.cli", "self", "--short", "--db", str(individual)],
                           capture_output=True, text=True, timeout=60, env=env)
    assert short.returncode == 0 and json.loads(short.stdout)["short"]


def test_the_voice_command_reads_a_short_version(individual):
    from neuroscifi.voice import bridge, commands
    for text in ("what are you doing", "What are you working on right now?", "status report", "what's your status"):
        assert commands.parse(text).kind == "self_status", text
    assert commands.parse("what are you researching about okapis today").kind == "research"
    owner = bridge.Principal(name="owner", owner=True, research=True)
    with closing(store.connect(individual)) as db:
        orchestrator.submit(db, "Where does the okapi live?", T0, origin="console")
        reply = bridge.Bridge(lambda: T0 + 5).handle(db, owner, {}, "what are you doing")
    assert reply.intent == "self_status" and reply.source == "template" and reply.speech
    assert "1 queued" in reply.text and reply.task_id is None
    pocket = bridge.Principal(name="pocket:x", owner=False, research=True, family=None)
    with closing(store.connect(individual)) as db:
        refused = bridge.Bridge(lambda: T0).handle(db, pocket, {}, "what are you doing")
    assert "can't tell which device" in refused.text
