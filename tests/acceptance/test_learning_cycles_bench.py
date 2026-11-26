"""Lane F's learning-cycles experiment (acceptance/cognition/cycles.py; scripts/bench_learning_cycles.py): frozen
worlds, a deterministic 3-cycle run, nothing learned and still true lost over the cycles (consolidation, the owner's
forget, re-checks), and confidence that never rises from the individual's own outputs or from copies of one source."""
import hashlib
import json
from contextlib import closing
from pathlib import Path

from neuroscifi.acceptance.cognition import cycles, cycles_splits
from neuroscifi.instance import imagined, store
from neuroscifi.webcortex import orchestrator, state
from neuroscifi.webcortex.types import Document

ROOT = Path(__file__).resolve().parents[2]
T0 = cycles.T0
DAY = cycles.DAY
DEV = cycles_splits.CYCLES["development"]["worlds"][0]


def test_the_worlds_are_frozen_before_the_harness():
    """Committed before the harness (b3de5d2, wordings amended on development at 7df0136); a change here would let a
    setting be tuned on held-out."""
    digest = hashlib.sha256((ROOT / "src/neuroscifi/acceptance/cognition/cycles_splits.py").read_bytes()).hexdigest()
    assert digest == "b7678ea35c6fdbe0c611114e38f08d614e80e276c777cf1c4e00909df36d366c"
    dev, held = (cycles_splits.CYCLES[s] for s in ("development", "held_out"))
    assert not set(dev["seeds"]) & set(held["seeds"])
    assert all(w["cycles"] >= 10 for s in (dev, held) for w in s["worlds"])


def test_the_world_is_written_by_the_generator_before_the_run():
    one, two = cycles_splits.generate(DEV, 41), cycles_splits.generate(DEV, 41)
    assert one == two
    # every value a page states comes from the generator's own versions: truth 1.x, wire 7.x, echo of own output 9.x
    majors = {p["text"].split(" is the latest")[0].rsplit(" ", 1)[1].split(".")[0]
              for look in one["looks"].values() for p in look["pages"]}
    assert majors <= {"1", "7", "9"}


def test_a_three_cycle_run_is_deterministic_and_admits_nothing_self_generated():
    for mechanisms in (frozenset(), frozenset(cycles.MECHANISMS)):
        one, two = (cycles.run_world(DEV, 41, mechanisms, cycles=3) for _ in range(2))
        one.pop("wall_seconds"), two.pop("wall_seconds")
        assert one == two
        assert len(one["series"]) == 4 and one["tasks"]["owner"] > 0
        assert one["self_admitted"] == one["wire_admitted"] == one["one_origin_rises"] == 0


def test_the_no_loop_reference_runs_no_autonomous_research():
    out = cycles.run_world(DEV, 41, frozenset({cycles.NO_LOOP}), cycles=4)
    assert out["tasks"]["owner"] > 0
    assert out["tasks"]["refresh"] == out["tasks"]["recheck"] == out["tasks"]["curiosity"] == 0


def test_nothing_learned_and_still_true_is_lost_over_the_cycles():
    """Directive §7 / brief 4a: over 8 cycles, through consolidation, the owner forgetting one honest outlet and the
    wire's site (cycle 6), re-check marks, the idle re-check and reflections, every subject held correctly at cycle 3
    whose truth did not change after it is still held correctly at the end."""
    for mechanisms in (frozenset(), frozenset(cycles.MECHANISMS)):
        out = cycles.run_world(DEV, 41, mechanisms, cycles=8)
        assert out["retention"]["base"] > 0
        assert out["retention"]["lost"] == out["retention"]["regressed"] == 0, out["retention"]


def test_the_references_come_from_the_world_generator_alone(monkeypatch):
    """Lane Y (H4): the decay has references from the same worlds and budget, computed without any product code (the
    consolidation is made to fail: the references never reach it)."""
    from neuroscifi.webcortex import consolidate
    monkeypatch.setattr(consolidate, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("product code")))
    out = cycles.references(DEV, 41)
    world = cycles_splits.generate(DEV, 41)
    changes = sum(1 for s in world["subjects"] for c in range(s["born"] + 1, DEV["cycles"] + 1)
                  if s["truth"][c] != s["truth"][c - 1])
    assert out["changes_per_cycle"] == changes / DEV["cycles"]
    series = out["series"]
    assert set(series) == set(cycles.REFERENCES) and all(len(s) == DEV["cycles"] + 1 for s in series.values())
    assert series["snapshot"][0] == series["oracle"][0] == series["round_robin_perfect"][0] == 1.0

    def mean(s):
        return sum(s[1:]) / len(s[1:])
    assert mean(series["oracle"]) >= mean(series["round_robin_perfect"]) >= mean(series["snapshot"])
    assert mean(series["round_robin_perfect"]) >= mean(series["round_robin"])


def test_a_look_teaches_the_blind_reference_its_majority_and_a_tie_teaches_nothing():
    pages = [{"kind": "honest", "text": "Vel 1.5.0 is the latest Vel release."},
             {"kind": "lag", "text": "As of now, Vel 1.4.0 is the latest Vel release."},
             {"kind": "wire", "text": "Vel 7.2.0 is the latest Vel release, the Syndiwire desk reports."}]
    world = {"looks": {("Vel", 1): {"pages": pages}, ("Vel", 2): {"pages": pages + [dict(pages[0])]}}}
    assert cycles._look_majority(world, "Vel", 1) is None              # one honest, one lagging: a tie
    assert cycles._look_majority(world, "Vel", 2) == 5
    assert cycles._look_majority(world, "Vel", 3) is None              # no look


def test_each_run_counts_what_the_mechanisms_did_and_whether_it_changed_a_launch_or_a_held_item():
    """Lane Y (M1): a mechanism that acted but changed nothing investigated or held is 'not exercised by this world',
    which needs these counts beside the metric."""
    configs = {"baseline": frozenset(), "only:hypothesis_fields": frozenset({"hypothesis_fields"}),
               "reference:no-loop": frozenset({cycles.NO_LOOP})}
    result = cycles.run_split("development", configs=configs, cycles=3, seeds=(41,))
    rows = {name: result["configurations"][name]["worlds"][DEV["name"]]["per_seed"][0] for name in configs}
    assert rows["baseline"]["changed_launch_cycles"] == rows["baseline"]["changed_held_cycles"] == 0
    assert rows["only:hypothesis_fields"]["activity.hypothesis_detail_rows"] > 0
    assert rows["baseline"]["activity.hypothesis_detail_rows"] == 0
    assert rows["only:hypothesis_fields"]["changed_launch_cycles"] == 0      # it wrote rows; nothing read them
    assert rows["reference:no-loop"]["changed_launch_cycles"] > 0             # no autonomous task at all


def test_the_verdict_rule_reads_paired_seeds_and_calls_display_only_mechanisms_no_evidence():
    result = cycles.run_split("development", cycles=1, seeds=(41,))
    verdicts = cycles.verdicts(result)
    assert set(verdicts) == set(cycles.MECHANISMS) | {"all"}
    for m in ("hypothesis_fields", "hypothesis_abandon"):
        assert verdicts[m]["identical_to_baseline"] and verdicts[m]["verdict"] == "no evidence"
    assert "reference:no-loop" in result["configurations"]


# ---- brief 4b: confidence never rises from the individual's own outputs or from copies of one source --------------

def _doc(host, text, published):
    url = f"http://{host}/velmora"
    return Document(url=url, final_url=url, canonical_url=url, domain=host, title="Velmora", text=text,
                    retrieved_at=published + 600.0, retrieval_mode="live", published_at=published)


def _task(db, now):
    return orchestrator.submit(db, "What is the latest Velmora release?", now, mode="fact_verification",
                               origin="console")


def _item(db):
    return db.execute("SELECT id, confidence, supporting, verification FROM web_knowledge WHERE status IN "
                      "('provisional','accepted','stale') AND statement LIKE '%Velmora%'").fetchall()


def _majors(db):
    return {cycles._major_minor(json.loads(v or "[]"))[0] for (v,) in db.execute(
        "SELECT claim_values FROM web_knowledge") if cycles._major_minor(json.loads(v or "[]"))}


def test_confidence_never_rises_from_own_outputs_or_from_copies_of_one_source(tmp_path):
    with closing(store.create(tmp_path / "f.sqlite", T0 - DAY, note="lane F test")) as db:
        state.set_config(db, {"enabled": True}, T0 - DAY)
        honest = [_doc("relnotes.example", "Velmora 1.6.0 is the latest Velmora release.", T0 - DAY),
                  _doc("weekly-builds.example", "As of now, Velmora 1.6.0 is the latest Velmora release.", T0 - DAY)]
        cycles.investigate(db, _task(db, T0), honest, T0)
        [(kid, first, supporting, verification)] = _item(db)
        assert first > 0
        # (a) ten rounds of the individual's own outputs: a stored model answer restating the item or asserting a
        # version no source states, and imagined inferences saying the same; no page is read
        for n in range(10):
            now = T0 + (n + 1) * 3600.0
            for text in ("Velmora 1.6.0 is the latest Velmora release.", "Velmora 9.4.0 is the latest Velmora release."):
                imagined.record(db, "inference", payload={"text": text}, assumptions=["own output"],
                                uncertainty="own output", mechanism="lane-f-test", mechanism_version="1",
                                question="What is the latest Velmora release?", now=now)
                cycles.investigate(db, _task(db, now), [], now, answer=text)
        assert _item(db) == [(kid, first, supporting, verification)]
        assert 9 not in _majors(db)
        # (b) ten rounds of one outlet's story and three copies of it on hosts never seen before (one origin)
        story = "As of today, Velmora 1.6.0 is the latest Velmora release."
        seen = []
        for n in range(10):
            now = T0 + DAY + n * 3600.0
            docs = [_doc("pkgwatch.example", story, T0 - DAY)] + [
                _doc(f"copy{n}-{i}.example", "This story first appeared on Pkgwatch.\n" + story, T0 - DAY)
                for i in range(3)]
            task = _task(db, now)
            single = cycles.one_origin_confidence(db, task, docs[0], now)
            cycles.investigate(db, task, docs, now)
            [(same, conf, _s, _v)] = _item(db)
            assert same == kid and conf <= max(first, single) + 1e-9
            seen.append(conf)
        assert len(set(seen)) == 1, seen          # repeated exposure adds nothing after the first
        # (c) ten rounds of a wrong wire story and its copies, alone: never knowledge
        wire = "Velmora 7.2.0 is the latest Velmora release, the Syndiwire desk reports."
        for n in range(10):
            now = T0 + 2 * DAY + n * 3600.0
            docs = [_doc("syndiwire.example", wire, now - 3600.0)] + [
                _doc(f"wirecopy{n}-{i}.example", "This story first appeared on Syndiwire.\n" + wire, now - 3600.0)
                for i in range(4)]
            cycles.investigate(db, _task(db, now), docs, now)
        assert 7 not in _majors(db)
        assert _item(db)[0][0] == kid and _item(db)[0][1] == seen[-1]
