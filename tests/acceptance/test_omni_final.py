"""The look machinery of the OMNIMIND benchmark's withheld split (lane ZW): acceptance/omni/final.py and the `final`
subcommands of scripts/bench_omni.py and scripts/bench_omni_forms.py.

Proves each refusal (no note, a blank note, an output inside the checkout or not writable, a dirty or uncommitted tree,
a changed withheld file, `run` on the withheld split), that the ledger line is written BEFORE anything is loaded or
scored, that a look's result is summaries only, that nothing but final.py imports a withheld module, and pins each
withheld file's SHA-256. It reads the withheld files' bytes only to hash them and never prints their contents; it
never writes the real ledger (every look here uses a temporary one), and never opens another programme's withheld
files (any path whose name contains "withheld" is skipped by the import scan)."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import final, subject

ROOT = Path(__file__).resolve().parents[2]
OMNI = ROOT / "src" / "neuroscifi" / "acceptance" / "omni"

# The withheld files as lane ZW committed them. Do not update these to make a run pass: a changed withheld file has
# been seen or edited, and every look at it stops being comparable.
PINNED = {
    "withheld_forms.py": "587712dc30fd8f89e4b9577d08480fe8523a1e0b81df3cc4f7b3818c9f346504",
    "withheld_cases.py": "f55d51098ca03ea3ecdedc6b234cd803fa1cb9b02d56a334dcd0fbdebcaefeef",
    "withheld_cross.py": "ef8601bc5090deb2ad6c80c480a27038a25b9c181cc4865a2652b1a8a4104846",
    "withheld_learn.py": "511264dc08ecf02d6b796ee9b12df656f1ff48f8749fda54f53b154b3a1b4cfc",
}
# The spent withheld files opened as development cases by the owner's decision of 2026-11-27 (lane FX3, ADR-081):
# only scripts/omnimind_spent.py may import them besides final.py. withheld_cases.py is NOT among them (it also holds
# the first split's other five families).
SPENT = {"withheld_cross", "withheld2_cross", "withheld2_unknown_hostile"}
CLEAN = {"git_commit": "0" * 40, "git_dirty": False}
FIELDS = {"at", "commit", "dirty", "family", "subject", "note", "script", "withheld_file", "withheld_sha256"}
# Lines written since fix lane F2 also carry the drivers' SHA-256 (review finding B4).
FIELDS_WITH_DRIVERS = FIELDS | {"drivers"}


@pytest.fixture(autouse=True)
def drivers_pinned_as_they_are(monkeypatch, request):
    """The machinery tests below look through today's drivers: pin them as they are (the tracked pin file is the
    main session's to write). The driver-pin tests (test_driver_pin_*) see the real pins."""
    if not request.node.name.startswith("test_driver_pin_"):
        monkeypatch.setattr(final, "pinned_drivers", lambda path=None: final.driver_hashes())


def test_each_withheld_file_is_pinned_here_and_in_final():
    for name, sha in PINNED.items():
        assert hashlib.sha256((OMNI / name).read_bytes()).hexdigest() == sha, name
    assert final.WITHHELD_SHA256 == PINNED
    assert set(final.FILE_OF.values()) == set(PINNED)
    assert set(final.FILE_OF) == set(omni.FAMILIES) | {"forms", "cross", "learn"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node, (node.module or ""), [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield node, a.name, []


def test_nothing_but_final_imports_a_withheld_module():
    """Every Python file of the source, the scripts and the tests (except those whose name contains "withheld", which
    this lane may not open when they are another programme's) is scanned: only final.py may import withheld_*, and
    only inside its functions, never at module level."""
    scanned = 0
    for folder in ("src", "scripts", "tests"):
        for path in (ROOT / folder).rglob("*.py"):
            if "withheld" in path.name or "node_modules" in path.parts:
                continue
            scanned += 1
            for node, module, names in _imports(path):
                parts = module.split(".") + [n.split(".")[-1] for n in names]
                if not any(p.startswith(("withheld_", "withheld2_")) for p in parts):
                    continue
                if path == ROOT / "scripts" / "omnimind_spent.py":
                    # the owner's decision of 2026-11-27 (WAVE3, ADR-081): the SPENT cases of unknown, hostile and
                    # cross are development cases; this script may import exactly those three files, nothing else
                    spent = {p for p in parts if p.startswith(("withheld_", "withheld2_"))}
                    assert spent <= SPENT, (module, names)
                    continue
                assert path == OMNI / "final.py", (str(path.relative_to(ROOT)), module, names)
    assert scanned > 100
    top = ast.parse((OMNI / "final.py").read_text(encoding="utf-8")).body
    for node in top:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + (getattr(node, "module", "") or "").split(".")
            assert not any(n.startswith(("withheld_", "withheld2_")) for n in names)


def test_the_withheld_modules_import_no_product_code():
    for name in PINNED:
        for node, module, names in _imports(OMNI / name):
            assert not module.startswith("neuroscifi") and getattr(node, "level", 1) <= 1, (name, module)


def _script(name, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          timeout=300, cwd=ROOT)


def _ledger_bytes():
    return final.LEDGER.read_bytes() if final.LEDGER.exists() else b""


def test_run_refuses_the_withheld_split():
    before = _ledger_bytes()
    done = _script("bench_omni.py", "run", "--split", "withheld", "--out", "/tmp/never-written.json")
    assert done.returncode == 2 and "refused" in done.stderr and "final" in done.stderr
    done = _script("bench_omni_forms.py", "run", "--family", "forms", "--split", "withheld")
    assert done.returncode == 2 and "refused" in done.stderr and "final" in done.stderr
    with pytest.raises(ValueError):
        omni.check_split("withheld")
    assert "withheld" not in omni.SPLITS
    assert _ledger_bytes() == before


@pytest.mark.parametrize("script,family,subj", [("bench_omni.py", "honest", "today"),
                                                ("bench_omni_forms.py", "forms", "today")])
def test_final_refuses_before_recording(tmp_path, script, family, subj):
    before = _ledger_bytes()
    base = ["final", "--family", family, "--subject", subj]
    done = _script(script, *base, "--out", str(tmp_path / "x.json"))                         # no note at all
    assert done.returncode == 2 and "--note" in done.stderr
    done = _script(script, *base, "--note", "   ", "--out", str(tmp_path / "x.json"))        # a blank note
    assert done.returncode == 2 and "refused" in done.stderr and "note" in done.stderr
    inside = ROOT / "omni_final_should_not_exist.json"
    done = _script(script, *base, "--note", "test", "--out", str(inside))                   # inside the checkout
    assert done.returncode == 2 and "inside the checkout" in done.stderr and not inside.exists()
    done = _script(script, *base, "--note", "test", "--out", str(tmp_path / "missing" / "x.json"))
    assert done.returncode == 2 and "cannot be written" in done.stderr
    assert not (tmp_path / "x.json").exists()
    assert _ledger_bytes() == before


def test_forms_final_refuses_a_subject_that_is_not_a_reference_of_every_family(tmp_path):
    before = _ledger_bytes()
    done = _script("bench_omni_forms.py", "final", "--family", "forms", "--family", "cross", "--subject",
                   "shape_only", "--note", "test", "--out", str(tmp_path / "x.json"))
    assert done.returncode == 2 and "reference" in done.stderr
    assert _ledger_bytes() == before


def test_check_refuses_a_dirty_or_uncommitted_tree_and_a_changed_file(monkeypatch):
    for env in ({"git_commit": "abc", "git_dirty": True}, {"git_commit": None, "git_dirty": False},
                {"git_commit": "abc", "git_dirty": None}):
        with pytest.raises(final.Refused, match="clean and committed"):
            final.check("a note", ["forms"], env)
    with pytest.raises(final.Refused, match="note"):
        final.check("", ["forms"], CLEAN)
    with pytest.raises(final.Refused, match="family"):
        final.check("a note", ["nonsense"], CLEAN)
    final.check("a note", ["forms", "intake", "cross", "learn"], CLEAN)                   # the pinned files pass
    monkeypatch.setitem(final.WITHHELD_SHA256, "withheld_cases.py", "0" * 64)
    with pytest.raises(final.Refused, match="changed"):
        final.check("a note", ["hostile"], CLEAN)
    final.check("a note", ["forms"], CLEAN)                                               # another file is unaffected


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo, check=True,
                   capture_output=True)


def test_environment_reads_git_and_excepts_only_the_ledger(tmp_path):
    assert final.environment(tmp_path)["git_commit"] is None                                # not a repository
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("a\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "a")
    env = final.environment(tmp_path)
    assert env["git_commit"] and env["git_dirty"] is False
    ledger = tmp_path / final.LEDGER_NAME
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}\n")
    assert final.environment(tmp_path)["git_dirty"] is False                               # the ledger is a record
    (tmp_path / "b.txt").write_text("b\n")
    assert final.environment(tmp_path)["git_dirty"] is True                                # anything else is not
    (tmp_path / "b.txt").unlink()
    (tmp_path / "a.txt").write_text("changed\n")
    assert final.environment(tmp_path)["git_dirty"] is True


def test_out_problem():
    assert "inside the checkout" in final.out_problem(str(ROOT / "x.json"))
    assert "cannot be written" in final.out_problem("/no/such/directory/x.json")
    assert final.out_problem(str(Path("/tmp") / "omni-final-out-probe.json")) is None


def test_the_ledger_line_is_written_before_anything_is_loaded(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"

    def boom(subject):
        raise RuntimeError("scoring started")
    monkeypatch.setattr(final, "score_forms", boom)
    with pytest.raises(RuntimeError, match="scoring started"):
        final.look(["forms"], "today", "today", "a crash still counts", script="test", ledger=ledger, env=CLEAN)
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert len(lines) == 1 and set(lines[0]) == FIELDS_WITH_DRIVERS
    assert lines[0]["drivers"] == final.driver_hashes() and len(lines[0]["drivers"]) == 2
    assert lines[0]["family"] == "forms" and lines[0]["note"] == "a crash still counts"
    assert lines[0]["withheld_sha256"] == PINNED["withheld_forms.py"] and lines[0]["commit"] == CLEAN["git_commit"]
    with pytest.raises(final.Refused):                                                     # a refusal writes nothing
        final.look(["forms"], "today", "today", "", script="test", ledger=ledger, env=CLEAN)
    with pytest.raises(final.Refused):
        final.look(["forms"], "today", "today", "x", script="test", ledger=ledger, env={"git_commit": "a",
                                                                                         "git_dirty": True})
    assert len(ledger.read_text().splitlines()) == 1


def test_a_look_is_one_line_per_family_and_summaries_only(tmp_path):
    """References only (and the harness's own refusing probe): never a product subject."""
    ledger = tmp_path / "ledger.jsonl"
    out = final.look(["forms", "cross", "learn"], "today", "today", "test of the machinery", script="test",
                     ledger=ledger, env=CLEAN)
    assert out["ledger_lines"] == 3 and [e["family"] for e in out["ledger_entries"]] == ["forms", "cross", "learn"]
    forms_out, cross_out, learn_out = (out["families"][f] for f in ("forms", "cross", "learn"))
    assert forms_out["n_cases"] >= 150 and forms_out["hard_constraints_violated"]["k"] == 0
    assert cross_out["n_pairs"] >= 36 and cross_out["answered"]["k"] == 0                   # today cannot relate
    assert "SIMULATED" in learn_out["label"] and learn_out["denominator_per_cycle"] >= 600
    z = final.look(["honest", "hostile"], subject.get("probe:refuse"), "probe:refuse", "test of the machinery",
                   script="test", ledger=ledger, env=CLEAN)
    assert z["ledger_lines"] == 5
    assert z["families"]["hostile"]["right"] == z["families"]["hostile"]["n"] >= 30           # every refusal is right
    text = json.dumps([out, z])
    assert "/withheld/" not in text                                                        # no task id
    for key in ("tasks", "failures", "cases"):
        assert all(key not in v for v in z["families"].values())
    assert all(t.case["request"] not in text and f'"{t.id}"' not in text for t in final.forms_tasks())


def test_the_tracked_ledger_is_well_formed():
    if not final.LEDGER.exists():
        pytest.skip("no look recorded yet")
    for line in final.LEDGER.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry.get("split") == "withheld2":                      # lane ZW2's split (test_omni_withheld2.py)
            assert set(entry) == FIELDS_WITH_DRIVERS | {"split"} and entry["note"].strip() and entry["dirty"] is False
            assert entry["withheld_sha256"] == final.WITHHELD2_SHA256.get(entry["withheld_file"], "absent")
            continue
        assert set(entry) in (FIELDS, FIELDS_WITH_DRIVERS) and entry["note"].strip() and entry["dirty"] is False
        assert entry["withheld_file"] in PINNED


def test_driver_pin_a_look_refuses_a_driver_not_pinned_or_changed_after_its_pin(tmp_path):
    """Review finding B4: a look names the driver it went through; an unpinned or changed driver is refused before
    the ledger line."""
    now = final.driver_hashes()
    assert set(now) == set(final.DRIVERS) and all(v and len(v) == 64 for v in now.values())
    with pytest.raises(final.Refused, match="not pinned"):
        final.check("a note", ["forms"], CLEAN, pins={})
    with pytest.raises(final.Refused, match="not pinned"):
        final.check("a note", ["forms"], CLEAN, pins=dict.fromkeys(final.DRIVERS))
    changed = dict(now, **{final.DRIVERS[0]: "0" * 64})
    with pytest.raises(final.Refused, match="changed after it was pinned"):
        final.check("a note", ["forms"], CLEAN, pins=changed)
    final.check("a note", ["forms"], CLEAN, pins=now)                                      # pinned as it is: passes
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(final.Refused):
        final.look(["forms"], "today", "today", "x", script="test", ledger=ledger, env=CLEAN, pins=changed)
    assert not ledger.exists()                                                             # nothing recorded


def test_driver_pin_the_tracked_file_and_the_pin_driver_command():
    data = json.loads(final.DRIVER_PINS.read_text(encoding="utf-8"))
    assert set(data["pins"]) == set(final.DRIVERS)
    assert all(v is None or (len(v) == 64 and int(v, 16) >= 0) for v in data["pins"].values())
    assert final.pinned_drivers() == data["pins"]
    assert final.pinned_drivers(ROOT / "no-such-file.json") == {}
    if not all(data["pins"].values()):                                  # until the main session pins: no look at all
        with pytest.raises(final.Refused, match="not pinned"):
            final.check("a note", ["forms"], CLEAN)
    done = _script("bench_omni.py", "pin-driver")
    assert done.returncode == 0, done.stderr
    printed = json.loads(done.stdout)
    assert printed["pins"] == final.driver_hashes() and set(printed) == set(data) - {"status"}
