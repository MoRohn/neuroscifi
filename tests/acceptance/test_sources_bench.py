"""Wave 4 step 0: the source-independence family's worlds, metrics, references and the look's guard.

Nothing here loads the withheld worlds: the guard tests replace sources._withheld with a stand-in that fails if it is
reached, and the withheld file is only hashed (its bytes, never decoded)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import sources, sources_splits_dev as dev, sources_splits_heldout as heldout

ROOT = Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location("bench_sources_test", ROOT / "scripts" / "bench_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sealed(monkeypatch, tmp_path):
    def loader():
        raise AssertionError("the withheld worlds were loaded")
    monkeypatch.setattr(sources, "_withheld", loader)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cognition, "LEDGER", ledger)
    return ledger


def _clean(monkeypatch):
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": False})


# ---- the worlds -------------------------------------------------------------------------------------------------------

def test_the_worlds_files_are_frozen():
    assert sources.digest(sources.DEV_FILE) == sources.DEV_SHA256
    assert sources.digest(Path(heldout.__file__)) == sources.HELDOUT_SHA256
    assert sources.digest(sources.WITHHELD_FILE) == sources.WITHHELD_SHA256       # bytes hashed, never loaded


def test_the_seeds_are_disjoint_from_every_public_split():
    from neuroscifi.acceptance.cognition import cycles_rate_splits, cycles_splits, cycles_splits_s
    mine = set(dev.CYCLES["development"]["seeds"]) | set(heldout.CYCLES["held_out"]["seeds"])
    assert not set(dev.CYCLES["development"]["seeds"]) & set(heldout.CYCLES["held_out"]["seeds"])
    for module in (cycles_splits, cycles_splits_s, cycles_rate_splits):
        for split in module.CYCLES.values():
            assert not mine & set(split["seeds"])
    assert mine == {8301, 8302, 8303, 8401, 8402, 8403, 8404}


def test_every_shape_appears_and_the_graph_is_whole():
    shapes = set()
    for split, spec in (("development", dev.CYCLES["development"]), ("held_out", heldout.CYCLES["held_out"])):
        for world in spec["worlds"]:
            for seed in spec["seeds"]:
                w = dev.generate(world, seed)
                assert sources.world_digest(w) == sources.world_digest(dev.generate(world, seed))
                ids = set()
                for look in w["looks"].values():
                    shapes.add(look["shape"])
                    ids |= {p["id"] for p in look["pages"] + look["unread"]}
                    for p in look["pages"]:
                        assert p["basis"] in ("first_hand", "derived", "echo")
                        assert (p["basis"] == "echo") == (p["roots"] == ["self"])
                        assert p["basis"] != "derived" or p["derives_from"]
                for look in w["looks"].values():
                    for p in look["pages"]:
                        assert set(p["derives_from"]) <= ids
    assert shapes == set(dev.SHAPES)


def test_values_are_compared_as_numbers_not_by_a_version_convention():
    assert sources.norm("v2.4") == sources.norm("2.4.0") == sources.norm("2.4") == "2.4.0"
    assert sources.norm("v2.4.1") == "2.4.1" and sources.norm("9") is None and sources.norm("abc") is None


def test_support_counts_roots_not_pages():
    origin = {"roots": ["A|1.0.0"], "value": "1.0.0", "authoritative": False}
    copies = [{"roots": ["A|1.0.0"], "value": "1.0.0"} for _ in range(3)]
    echo = {"roots": ["self"], "value": "1.0.0"}
    sup, auth = sources.support({"pages": [origin, *copies, echo], "unread": []})
    assert sup == {"1.0.0": {"A|1.0.0"}} and not sources.meets_bar("1.0.0", sup, auth)
    other = {"roots": ["B|1.0.0"], "value": "1.0.0"}
    sup, auth = sources.support({"pages": [*copies, other], "unread": []})
    assert sources.meets_bar("1.0.0", sup, auth)
    release = {"roots": ["P|1.0.0"], "value": "1.0.0", "authoritative": True}
    citing = [{"roots": ["P|1.0.0"], "value": "1.0.0"}]
    sup, auth = sources.support({"pages": citing, "unread": [release]})
    assert sources.meets_bar("1.0.0", sup, auth)          # the release page's root, read or not


# ---- a run ------------------------------------------------------------------------------------------------------------

def _small(cycles=2):
    world = dict(dev.CYCLES["development"]["worlds"][0]) | {"cycles": cycles}
    return dev.generate(world, 8301)


@pytest.mark.parametrize("config", sorted(sources.CONFIGS))
def test_each_configuration_reads_back_only_its_setting(config):
    world = _small()
    before = sources.world_digest(world)
    out = sources.run_world(world, config)
    shipped = sources.run_world(world, "shipped")["config"]
    changed = {k for k in shipped if shipped[k] != out["config"][k]}
    assert changed == set(sources.CONFIGS[config])
    assert all(out["config"][k] == v for k, v in sources.CONFIGS[config].items())
    assert sources.world_digest(world) == before           # the run never changes the world
    assert out["totals"]["looks"] == sum(len(v) for v in world["schedule"].values())


def test_references_bound_the_rules_in_each_direction():
    world = _small(4)
    refs = sources.references(world)
    oracle = refs["oracle"]["totals"]
    assert oracle["wrongly_admitted"] == 0 and oracle["fp"] == 0 and oracle["fn"] == 0
    assert refs["every_page"]["totals"]["tp"] == 0 and refs["same_value"]["totals"]["fn"] == 0
    assert refs["every_page"]["totals"]["wrongly_admitted"] >= oracle["wrongly_admitted"]


def test_run_refuses_the_withheld_split():
    with pytest.raises(cognition.Refused, match="final"):
        sources.run_split(sources.WITHHELD)
    with pytest.raises(ValueError, match="unknown configuration"):
        sources.run_split("development", configs=["shiped"])


# ---- the look's guard: every refusal before the ledger line -------------------------------------------------------------

def test_final_refuses_a_blank_note_and_an_unknown_configuration_before_the_ledger(sealed, monkeypatch):
    _clean(monkeypatch)
    for note in ("", "   ", "\t\n"):
        with pytest.raises(cognition.Refused, match="note"):
            sources.final(note)
    with pytest.raises(cognition.Refused, match="unknown configuration"):
        sources.final("a test", configs=["shiped"])
    with pytest.raises(SystemExit, match="unknown configuration"):
        _script().main(["final", "--note", "a test", "--config", "shiped", "--out", "/nonexistent/x.json"])
    assert not sealed.exists()


@pytest.mark.parametrize("state", [{"git_commit": "0" * 40, "git_dirty": True},
                                   {"git_commit": "0" * 40, "git_dirty": None},
                                   {"git_commit": None, "git_dirty": False}])
def test_final_refuses_a_dirty_or_unverifiable_tree_before_the_ledger(sealed, monkeypatch, state):
    monkeypatch.setattr(cognition, "environment", lambda: state)
    with pytest.raises(cognition.Refused, match="clean"):
        sources.final("a test")
    assert not sealed.exists()


@pytest.mark.parametrize("pin", ["WITHHELD_SHA256", "DEV_SHA256"])
def test_final_refuses_a_changed_worlds_file_before_the_ledger(sealed, monkeypatch, pin):
    _clean(monkeypatch)
    monkeypatch.setattr(sources, pin, "f" * 64)
    with pytest.raises(cognition.Refused, match="SHA-256"):
        sources.final("a test")
    assert not sealed.exists()


def test_the_command_refuses_outputs_it_may_not_write_before_the_ledger(sealed, monkeypatch, tmp_path):
    _clean(monkeypatch)
    bench = _script()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        for out in (tmp_path / "missing" / "o.json", locked / "o.json", tmp_path):
            with pytest.raises(SystemExit, match="cannot be written"):
                bench.main(["final", "--note", "a test", "--out", str(out)])
        with pytest.raises(SystemExit, match="outside this checkout"):
            bench.main(["final", "--note", "a test", "--out", str(ROOT / "look.json")])
        monkeypatch.setattr(cognition, "REPO", tmp_path)
        with pytest.raises(SystemExit, match="own source"):
            bench.main(["final", "--note", "a test", "--out", str(tmp_path / "o.json")])
    finally:
        locked.chmod(0o700)
    assert not sealed.exists() and not (ROOT / "look.json").exists()


def test_the_intended_command_records_its_line_first_and_only_then_loads(sealed, monkeypatch, tmp_path):
    _clean(monkeypatch)
    with pytest.raises(AssertionError, match="withheld worlds were loaded"):
        _script().main(["final", "--note", "a test", "--mechanism", "baseline", "--out", str(tmp_path / "o.json")])
    [entry] = [json.loads(line) for line in sealed.read_text().splitlines()]
    assert entry["families"] == [sources.FINAL_FAMILY] and entry["withheld_sha256"] == sources.WITHHELD_SHA256
    assert entry["configurations"] == ["shipped"] + [f"reference:{r}" for r in sources.REFERENCES]
    assert not (tmp_path / "o.json").exists()
