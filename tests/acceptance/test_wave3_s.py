"""Wave 3 lane S (ADR-061): what counts as an independent source, the individual's own text, and supersession of a
stale item behind a setting (off). Each fix has a test that fails without it (the same staging with the fix off)."""
import hashlib
from pathlib import Path

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv, cycles, cycles_s, cycles_splits, cycles_splits_s
from neuroscifi.instance.errors import Conflict, ValidationError
from neuroscifi.webcortex import claims, knowledge, state
from neuroscifi.webcortex.config import WebConfig

ROOT = Path(__file__).resolve().parents[2]
T0, DAY = adv.T0, adv.DAY


# ---- the worlds -----------------------------------------------------------------------------------------------------

def test_the_worlds_are_frozen_and_were_committed_alone_before_any_mechanism():
    """Committed alone at 920f023, before the fixes, supersession and the harness; never edit: a new world is a new
    file."""
    digest = hashlib.sha256((ROOT / "src/neuroscifi/acceptance/cognition/cycles_splits_s.py").read_bytes()).hexdigest()
    assert digest == "f60e877514da2d1c12dd7c69005d45b70e2b279f584baa8d6d23f4dc0d9871f9"
    mine = {s for split in cycles_splits_s.CYCLES.values() for s in split["seeds"]}
    theirs = {s for split in cycles_splits.CYCLES.values() for s in split["seeds"]}
    assert not mine & theirs
    names = {w["name"] for split in cycles_splits_s.CYCLES.values() for w in split["worlds"]}
    assert not names & {w["name"] for split in cycles_splits.CYCLES.values() for w in split["worlds"]}


def test_a_world_is_the_same_whatever_runs_on_it():
    spec = cycles_splits_s.CYCLES["development"]["worlds"][0]
    assert cycles_splits_s.generate(spec, 241) == cycles_splits_s.generate(spec, 241)
    assert cycles_splits_s.generate(spec, 241) != cycles_splits_s.generate(spec, 242)


# ---- the outlet a sentence credits ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "According to Syndiwire, Velmora 7.3.0 is the latest Velmora release.",
    "Velmora 7.3.0 is now the latest Velmora release, Syndiwire says.",
    "Release roundup: Velmora 7.3.0 is the latest Velmora release (Syndiwire).",
    "Velmora 7.3.0 is the newest Velmora release, via Syndiwire.",
    "Velmora 7.3.0 is the latest Velmora release, as the Syndiwire desk reports.",
    "\"Velmora 7.3.0 is the latest,\" Syndiwire wrote."])
def test_a_sentence_crediting_an_outlet_carries_that_outlets_key(text):
    assert claims.attribution_keys(text) == ["name:syndiwire"]


@pytest.mark.parametrize("text", [
    "Velmora 1.2.0 is the latest Velmora release, the Velmora team says.",      # the subject speaking
    "The Velmora team says Velmora 1.5.0 is the latest release.",
    "Someone says Velmora 1.0 is out.", "Word is that Velmora 7.3.0 is the latest Velmora release.",
    "In 2020 Paris reported 2,100 cases.", "Velmora 1.4.0 is out (Velmora)."])
def test_no_outlet_is_read_where_none_is_credited(text):
    assert claims.attribution_keys(text) == []


def test_without_the_setting_the_stamp_is_what_it_was():
    from neuroscifi.webcortex.types import Document
    docs = [Document(url=f"http://{h}/x", final_url=f"http://{h}/x", canonical_url=f"http://{h}/x", domain=h,
                     title="Velmora", text=t, retrieved_at=T0, retrieval_mode="live", published_at=T0)
            for h, t in (("syndiwire.example", "Velmora 7.3.0 is the latest Velmora release, the Syndiwire desk "
                                               "reports."),
                         ("mirror-one.example", "According to Syndiwire, Velmora 7.3.0 is the latest Velmora release."))]
    found = [c for d in docs for c in claims.extract_claims(d, "What is the latest Velmora release?")]
    off = [dict(c.flags) for c in found]
    claims.stamp_origins(found, docs)
    assert [dict(c.flags) for c in found] == off and len(claims.independence_groups(found)) == 2
    claims.stamp_origins(found, docs, attribution=True)
    assert len(claims.independence_groups(found)) == 1


# ---- the cases, and that each hole is there with the fixes off ------------------------------------------------------

def test_every_lane_s_case_passes():
    results = [case() for case in adv.CASES_WAVE3_S]
    assert [r["id"] for r in results if not r["pass"]] == [], [r for r in results if not r["pass"]]
    assert adv.ORIGIN["added_wave3_s"] == tuple(adv.EXPECTED_WAVE3_S)


def test_the_holes_are_there_with_the_fixes_off():
    """The same stagings with attribution_independence and echo_recognition off: the individual's own version, a
    syndicated version and a second row on one origin are all admitted (the holes the fixes close)."""
    assert "with both fixes off: 1 row(s)" in adv.echoed_own_value_is_not_corroboration()["observed"]
    assert "(5 with the attribution rule off)" in adv.reworded_syndicated_copies_count_once()["observed"]
    assert "and 1 new row(s)" in adv.one_origin_never_raises_confidence()["observed"]


def test_a_page_crediting_an_assistant_is_never_evidence_for_its_value():
    with adv._s_individual("model-credit") as (db, _):
        adv._s_look(db, T0, [("askboard.example", "Velmora 3.1.0 is the latest Velmora release, an assistant "
                                                  "reported.", T0 - 3600, None),
                             ("chatlog-digest.example", "\"Velmora 3.1.0 is the latest Velmora release,\" a chatbot "
                                                        "told one reader.", T0 - 3600, None)])
        assert adv._s_rows(db, "3.1.0") == []
    with adv._s_individual("model-credit-off", echo_recognition=False) as (db, _):
        adv._s_look(db, T0, [("askboard.example", "Velmora 3.1.0 is the latest Velmora release, an assistant "
                                                  "reported.", T0 - 3600, None),
                             ("chatlog-digest.example", "\"Velmora 3.1.0 is the latest Velmora release,\" a chatbot "
                                                        "told one reader.", T0 - 3600, None)])
        assert len(adv._s_rows(db, "3.1.0")) == 1


def test_an_echo_that_only_disagrees_is_still_asked_never_decided():
    """An echo of the individual's own value that only contradicts what two outlets say about a held item is not
    removed: the disagreement is asked as before, and no row states the echoed value. (An echo that would lead its
    cluster is removed: test above.)"""
    with adv._s_individual("echo-asked") as (db, _):
        kid = adv._s_item(db)
        adv._s_look(db, T0 + DAY, [], answer="Velmora 9.2.0 is the latest Velmora release.")
        out = adv._s_look(db, T0 + 2 * DAY, [adv._s_page("devdesk.example", adv._OLD, T0 - DAY),
                                             adv._s_page("factline.example", adv._OLD, T0 - DAY),
                                             ("askboard.example", "Someone on the forum says Velmora 9.2.0 is now the "
                                                                  "latest Velmora release.", T0 + 2 * DAY - 3600, None)])
        assert adv._s_rows(db, "9.2.0") == [] and adv._s_live(db, kid)["status"] == "provisional"
        assert out.get("echoes") in (None, []) and any(c.get("knowledge_id") == kid for c in out["contested"])


# ---- supersession: the setting, the review list and the revert -------------------------------------------------------

def test_supersession_ships_off_and_its_values_are_checked():
    assert WebConfig().supersession == "ask" and WebConfig().supersession_min_sources == 2
    assert WebConfig().attribution_independence is True and WebConfig().echo_recognition is True
    with adv._s_individual("settings") as (db, _):
        for bad in ({"supersession": "auto"}, {"supersession_min_sources": 1}, {"supersession_min_sources": 11},
                    {"echo_recognition": "yes"}):
            with pytest.raises(ValidationError):
                state.set_config(db, bad, T0)


def test_with_the_rule_on_a_newer_value_from_independent_outlets_replaces_the_item_and_is_marked():
    with adv._s_individual("rule-on", **adv.RULE_ON) as (db, _):
        kid = adv._s_item(db)
        out = adv._s_refresh(db, T0 + 3 * DAY)
        new = next(s["new"] for s in out["superseded"] if s.get("by") == "rule")
        row = knowledge.row(db, new)
        assert row["verification"]["decided_by"] == "rule" and row["status"] == "provisional"
        assert row["verification"]["supersession"]["replaced"] == kid
        assert knowledge.row(db, kid)["status"] == "superseded" and knowledge.row(db, kid)["superseded_by"] == new
        assert row["confidence"] <= 1 and out["answered_by_rule"]
        # the new version is asked for acceptance like every new item
        assert db.execute("SELECT count(*) FROM pending WHERE scope=? AND resolved_at IS NULL",
                          (f"web:knowledge:{new}",)).fetchone()[0] == 1


def test_revert_refuses_what_the_rule_did_not_make_and_a_second_revert():
    with adv._s_individual("revert-refusals", **adv.RULE_ON) as (db, _):
        kid = adv._s_item(db)
        with pytest.raises(Conflict):
            knowledge.revert(db, kid, T0 + DAY)
        out = adv._s_refresh(db, T0 + 3 * DAY)
        new = next(s["new"] for s in out["superseded"] if s.get("by") == "rule")
        knowledge.revert(db, new, T0 + 3 * DAY + 1)
        with pytest.raises(Conflict):
            knowledge.revert(db, new, T0 + 3 * DAY + 2)
        listed = knowledge.replacements(db)
        assert [(r["id"], r["reverted"]) for r in listed] == [(new, True)]


def test_the_owner_commands_list_and_revert(capsys):
    import json
    from neuroscifi.cli import main
    with adv._s_individual("cli", **adv.RULE_ON) as (db, path):
        adv._s_item(db)
        out = adv._s_refresh(db, T0 + 3 * DAY)
        new = next(s["new"] for s in out["superseded"] if s.get("by") == "rule")
        capsys.readouterr()
        assert main(["web", "replacements", "--db", str(path)]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in listed["replacements"]] == [new] and "revert" in listed["note"]
        assert main(["web", "revert", str(new), "--db", str(path)]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "rejected"
        assert knowledge.row(db, new)["status"] == "rejected"


# ---- the harness -----------------------------------------------------------------------------------------------------

def test_the_harness_forces_only_the_settings_under_test_and_reads_them_back():
    spec = cycles_splits_s.CYCLES["development"]["worlds"][0]
    seen = {}
    for name in ("baseline", "fixes", "supersede:K2", "supersede:K2-no-independence", "shipped"):
        out = cycles_s.run_world(spec, 241, name, generate=cycles_splits_s.generate, cycles_=2)
        seen[name] = {k: out["config"][k] for k in ("attribution_independence", "echo_recognition", "supersession",
                                                    "supersession_min_sources", "idle_recheck")}
        assert set(out["s"]) >= set(cycles_s.COUNTS)
    assert seen["baseline"]["attribution_independence"] is False and seen["baseline"]["supersession"] == "ask"
    assert seen["fixes"]["attribution_independence"] is True and seen["fixes"]["echo_recognition"] is True
    assert seen["supersede:K2"]["supersession"] == "independent" and seen["supersede:K2"]["supersession_min_sources"] == 2
    assert seen["shipped"] == {k: getattr(WebConfig(), k) for k in seen["shipped"]}


def test_the_withheld_look_configurations_read_back_as_named():
    known = cycles.configurations() | cycles.EXTRA
    spec = cycles_splits.CYCLES["development"]["worlds"][0]
    off = cycles.run_world(spec, 41, known["lane-s:fixes-off"], cycles=1)["config"]
    rule = cycles.run_world(spec, 41, known["lane-s:supersede-K2"], cycles=1)["config"]
    assert (off["attribution_independence"], off["echo_recognition"], off["supersession"]) == (False, False, "ask")
    assert (rule["attribution_independence"], rule["supersession"], rule["supersession_min_sources"]) == \
        (True, "independent", 2)


def test_the_census_sees_the_syndication_shapes_only_with_the_rule():
    out = cycles_s.census("lane-s", "development", seeds=(241,))["census"]
    assert out["attribution_on"]["wire_split"] < out["attribution_off"]["wire_split"]
    assert out["attribution_on"]["origin_split"] == 0 < out["attribution_off"]["origin_split"]
    assert out["attribution_on"]["honest_merged"] == out["attribution_off"]["honest_merged"]
