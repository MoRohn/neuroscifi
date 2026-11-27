"""Learning the form of a result from outcomes (OMNIMIND lane L, ADR-078; artifacts/learn.py, schema 21).

Outcomes come only from outside the system (the owner's reaction, the independent check's verdict); a key acts only
from the stated minimum of evidence; what was learned never moves a hard constraint or makes a sentence's worth
elaborate (more of that under random policies: test_learn_properties.py); it forgets with its artifact, with the
owner's reset and through every restore; it explains itself; it is the owner's alone and runs nothing unasked. Made
artifacts here are real ones (lane G1's bar chart generator, checked by the independent validator)."""
from contextlib import closing
import json
import sqlite3
import threading

import pytest

from neuroscifi.artifacts import forms as F, learn, make, store as A
from neuroscifi.artifacts.types import ContentShape, Malformed
from neuroscifi.instance import checkpoints, governance, schema, store

T0 = 1_800_000_000.0
ALL = tuple(sorted({f.generator for f in F.FORMS.values() if f.generator}))
KEY = {"kind": "categories", "purpose": "compare", "device": "console"}
SHAPE = ContentShape(("categories",), 16, 1, False, 0, False, 0.0, 0)
CONSOLE = F.Context(generators=ALL)


def bars(title="Rain", n=16, **options):
    return {"spec": "chart.bar", "version": 1, "title": title, "description": "", "options": options,
            "data": {"categories": [f"c{i}" for i in range(n)], "x": {"label": "Month"}, "y": {"label": "Rain"},
                     "series": [{"name": "Lisbon", "values": list(range(n))}]},
            "sources": ["request"], "made_by": {"generator": "", "version": 0}}


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        yield db, path
    finally:
        db.close()


def made_chart(db, now=T0, sources=(), **spec_kw):
    made = make.make(bars(**spec_kw))
    return A.add_made(db, made, made.spec, list(sources), now=now)


def shown(db, form="bar_chart", key=KEY, now=T0):
    """A made artifact and its make-time check row, as the production pipeline records it."""
    record = made_chart(db, now=now)
    learn.record_outcome(db, record.id, "check_passed", form=form, now=now, **key)
    return record.id


def rows(db):
    return db.execute("SELECT count(*) FROM form_outcomes").fetchone()[0]


def chosen(db, shape=SHAPE, context=CONSOLE, request=F.Request()):
    return learn.plan(db, request, (shape,), context)[0].chosen


# ---- outcomes come from outside, and only while the owner allows it ------------------------------------------------

def test_off_records_nothing_and_changes_nothing(individual):
    db, _ = individual
    assert store.config(db).form_learning == "off"
    record = made_chart(db)
    assert learn.record_outcome(db, record.id, "check_passed", form="bar_chart", **KEY)["recorded"] is False
    assert learn.record_outcome(db, record.id, "kept")["recorded"] is False
    assert rows(db) == 0
    assert learn.adjustments_for(db, **KEY).status == "off"
    assert chosen(db) == F.plan(F.Request(), (SHAPE,), CONSOLE).chosen == ("bar_chart",)


def test_outcomes_are_the_owners_reactions_and_the_checks_verdicts_only(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "record"}, T0)
    aid = shown(db)
    given = A.add(db, b"a note\n", name="note.txt", now=T0)
    with pytest.raises(Malformed, match="only a form the system made"):
        learn.record_outcome(db, given.id, "kept")
    with pytest.raises(A.NotHeld):
        learn.record_outcome(db, "artifact-" + "f" * 32, "kept")
    for bad in (dict(outcome="liked"), dict(outcome="replaced"), dict(outcome="kept", by="table"),
                dict(outcome="replaced", by="a_hologram")):
        with pytest.raises(Malformed):
            learn.record_outcome(db, aid, bad.pop("outcome"), **bad)
    bare = made_chart(db)                        # made outside a plan: nothing says what it answered
    with pytest.raises(Malformed, match="nothing records what this artifact was made for"):
        learn.record_outcome(db, bare.id, "kept")
    # the key is the planner's vocabulary, never free text (no name, no request words can be stored)
    for field, value in (("kind", "Ottoline"), ("purpose", "because"), ("device", "kitchen"), ("form", "<svg>")):
        with pytest.raises(Malformed):
            learn.record_outcome(db, aid, "check_passed", **(KEY | {"form": "bar_chart", field: value}))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO form_outcomes(artifact_id,kind,purpose,device,form,outcome,source,at) VALUES"
                   "(?,?,?,?,?,?,?,?)", (aid, "Ottoline Q", "compare", "console", "bar_chart", "kept", "owner", T0))
    with pytest.raises(sqlite3.IntegrityError, match="names a made artifact"):
        db.execute("INSERT INTO form_outcomes(artifact_id,kind,purpose,device,form,outcome,source,at) VALUES"
                   "(?,?,?,?,?,?,?,?)", (given.id, "categories", "compare", "console", "bar_chart", "kept", "owner",
                                         T0))
    with pytest.raises(sqlite3.IntegrityError, match="never rewritten"):
        db.execute("UPDATE form_outcomes SET outcome='kept'")
    out = learn.record_outcome(db, aid, "replaced", by="table", now=T0 + 1)
    assert out["recorded"] and out["key"] == ["categories", "compare", "console"] and out["form"] == "bar_chart"
    assert {r[0] for r in db.execute("SELECT source FROM form_outcomes")} == {"check", "owner"}
    # no evidence-side table can name an artifact; this table is on the artifact side and holds ids only
    assert "form_outcomes" not in schema._NO_IMAGINED


def test_the_latest_reaction_to_an_artifact_is_the_one_that_counts(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    aid = shown(db)
    learn.record_outcome(db, aid, "dismissed", now=T0 + 1)
    learn.record_outcome(db, aid, "kept", now=T0 + 2)               # the owner changed their mind
    policy = learn.tally(db, tuple(KEY.values())).policy(tuple(KEY.values()))
    assert dict(policy.observed) == {"check_passed": 1, "kept": 1} and dict(policy.tally) == {"bar_chart": 1}


def test_a_key_needs_the_stated_minimum_of_evidence(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    first = shown(db)
    learn.record_outcome(db, first, "dismissed", now=T0 + 1)          # one reaction: evidence 1 of 2
    learned = learn.adjustments_for(db, **KEY)
    assert learned.status == "not_enough" and "not enough yet (1 of 2)" in learned.words
    assert chosen(db) == ("bar_chart",)
    second = shown(db, now=T0 + 2)
    learn.record_outcome(db, second, "replaced", by="table", now=T0 + 3)   # a replacement says what was wanted: 2
    learned = learn.adjustments_for(db, **KEY)
    assert learned.status == "applied" and dict(learned.preferences) == {"table": 1.0}
    assert learned.adjustments is None                                     # the planner's weights are not learned
    plan, _ = learn.plan(db, F.Request(), (SHAPE,), CONSOLE)
    assert plan.chosen == ("table",) and "learned from your reactions" in plan.reason
    assert "you have stated" not in plan.reason and "without what was learned: a bar chart" in plan.notes
    # "record" keeps the rows and applies nothing
    store.set_config(db, {"form_learning": "record"}, T0 + 4)
    assert learn.adjustments_for(db, **KEY).status == "recording" and chosen(db) == ("bar_chart",)


def test_a_failed_check_counts_against_a_form_and_a_passed_one_counts_nothing(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    for i in range(3):
        learn.record_outcome(db, made_chart(db, now=T0 + i).id, "check_passed", form="bar_chart", now=T0 + i, **KEY)
    policy = learn.tally(db).policy(tuple(KEY.values()))
    assert policy.evidence == 0 and not policy.enough
    record = made_chart(db, now=T0 + 5)
    learn.record_outcome(db, record.id, "check_failed", form="table", now=T0 + 5, **KEY)
    learn.record_outcome(db, made_chart(db, now=T0 + 6).id, "check_failed", form="table", now=T0 + 6, **KEY)
    policy = learn.tally(db).policy(tuple(KEY.values()))
    assert policy.evidence == 2 and dict(policy.tally) == {"bar_chart": 0, "table": -2} and not policy.preferences


# ---- what was learned cannot reach the hard constraints or the proportion rules ------------------------------------

def _teach(db, form, key, n=2):
    for i in range(n):
        aid = shown(db, form="prose" if form != "prose" else "table", key=key, now=T0 + i)
        learn.record_outcome(db, aid, "replaced", by=form, now=T0 + i)


def test_a_hard_constraint_holds_whatever_was_learned(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    _teach(db, "bar_chart", {"kind": "categories", "purpose": "compare", "device": "voice"})
    assert learn.adjustments_for(db, "categories", "compare", "voice").preferences == {"bar_chart": 1.0}
    assert chosen(db, context=F.Context(device="voice", generators=ALL)) == ("spoken",)    # a speaker shows no picture
    _teach(db, "bar_chart", KEY)
    assert chosen(db) == ("bar_chart",)
    assert chosen(db, context=F.Context(generators=tuple(g for g in ALL if g != "chart.bar"))) != ("bar_chart",)
    assert "bar_chart" not in chosen(db, context=F.Context(accessibility=("voice_only",), generators=ALL))


def test_a_sentence_worth_stays_words_whatever_was_learned(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    scalar = {"kind": "scalar", "purpose": "look_up", "device": "console"}
    _teach(db, "bar_chart", scalar, n=4)
    assert learn.adjustments_for(db, **scalar).preferences == {"bar_chart": 1.0}
    assert chosen(db, shape=ContentShape(("scalar",), 1, 1, False, 0, False, 0.0, 0)) == ("prose",)
    three = {"kind": "categories", "purpose": "compare", "device": "console"}
    _teach(db, "bar_chart", three, n=4)
    assert chosen(db, shape=ContentShape(("categories",), 3, 1, False, 0, False, 0.0, 0)) == ("prose",)
    assert chosen(db, request=F.Request("Just tell me briefly.")) == ("prose",)


def test_the_owners_stated_preferences_win(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    _teach(db, "bar_chart", KEY)
    _teach(db, "bar_chart", {"kind": "categories", "purpose": "compare", "device": "console"})
    learned = learn.adjustments_for(db, **KEY)
    family_no = F.Context(preferences={"chart": -1.0}, generators=ALL)
    assert learned.context(family_no) is family_no                   # a family the owner refused gets nothing learned
    assert "bar_chart" not in chosen(db, context=family_no)
    likes_words = F.Context(preferences={"prose": 0.5}, generators=ALL)
    assert learned.context(likes_words) is likes_words               # any positive stated preference: nothing added
    unrelated = F.Context(preferences={"sonification": -1.0}, generators=ALL)
    assert dict(learned.context(unrelated).preferences) == {"bar_chart": 1.0, "sonification": -1.0}
    assert all(0 < v <= 1 for v in learned.preferences.values())     # never 0 or below: nothing learned excludes


# ---- forgetting, the reset and restore -----------------------------------------------------------------------------

def test_an_artifacts_outcomes_go_with_it_whatever_forgot_it(individual, tmp_path):
    db, path = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    source = A.add(db, b"Met Ottoline-zq7 at the lab.\n", name="note.txt", now=T0)
    derived = made_chart(db, sources=[source.id])
    learn.record_outcome(db, derived.id, "check_passed", form="bar_chart", **KEY)
    learn.record_outcome(db, derived.id, "replaced", by="table")
    kept = shown(db)
    learn.record_outcome(db, kept, "kept")
    alone = shown(db)
    learn.record_outcome(db, alone, "dismissed")
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    assert rows(db) == 6
    A.forget_artifact(db, alone, T0 + 2)                              # the artifact itself
    assert rows(db) == 4
    governance.forget_partner(db, "Ottoline-zq7", T0 + 3)             # a root its source names: the cascade
    assert rows(db) == 2
    assert {a for (a,) in db.execute("SELECT DISTINCT artifact_id FROM form_outcomes")} == {kept}
    restored = tmp_path / "restored.sqlite"
    result = checkpoints.restore(db, archive["id"], restored, T0 + 4)
    assert result["artifacts_removed"] == 3 and "form_outcomes_removed" not in result
    with closing(store.open_existing(restored)) as copy:
        assert {a for (a,) in copy.execute("SELECT DISTINCT artifact_id FROM form_outcomes")} == {kept}
    policy = learn.tally(db).policy(tuple(KEY.values()))                  # only the kept one counts now
    assert dict(policy.observed) == {"check_passed": 1, "kept": 1} and not policy.enough


def test_the_reset_clears_everything_and_every_restore_replays_it(individual, tmp_path):
    db, path = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    _teach(db, "table", KEY)
    before = checkpoints.create(db, path, tmp_path / "cps", T0 + 10)
    out = learn.reset(db, T0 + 11)
    assert out["outcomes_removed"] == 4 and out["checkpoints_stale"] == [before["id"]] and rows(db) == 0
    assert learn.adjustments_for(db, **KEY).status == "not_enough" and chosen(db) == ("bar_chart",)
    assert db.execute("SELECT target_id FROM deletion_log WHERE target_kind='form_outcomes'").fetchall() == [("all",)]
    later = shown(db, now=T0 + 12)
    learn.record_outcome(db, later, "kept", now=T0 + 12)
    after = checkpoints.create(db, path, tmp_path / "cps", T0 + 13)
    old_copy, new_copy = tmp_path / "old.sqlite", tmp_path / "new.sqlite"
    result = checkpoints.restore(db, before["id"], old_copy, T0 + 14)
    assert result["form_outcomes_removed"] == 4
    assert "form_outcomes_removed" not in checkpoints.restore(db, after["id"], new_copy, T0 + 15)
    with closing(store.open_existing(old_copy)) as copy:
        assert rows(copy) == 0 and copy.execute(
            "SELECT count(*) FROM deletion_log WHERE target_kind='form_outcomes'").fetchone()[0] == 1
    with closing(store.open_existing(new_copy)) as copy:
        assert rows(copy) == 2                                    # what came after the reset stays
    # the archive the reset made stale is rewritten in place without the rows, and is valid again
    learn.reset(db, T0 + 16)
    done = checkpoints.after_forget(db, T0 + 17)
    assert {r["id"] for r in done["checkpoints_rewritten"]} >= {after["id"]} and done["checkpoints_stale"] == []
    again = tmp_path / "again.sqlite"
    checkpoints.restore(db, after["id"], again, T0 + 18)
    with closing(store.open_existing(again)) as copy:
        assert rows(copy) == 0


def test_the_table_is_bounded(individual, monkeypatch):
    db, _ = individual
    monkeypatch.setattr(learn, "MAX_OUTCOMES", 5)
    store.set_config(db, {"form_learning": "record"}, T0)
    aid = shown(db)
    for i in range(9):
        learn.record_outcome(db, aid, "kept", now=T0 + i)
    assert rows(db) == 5
    assert db.execute("SELECT min(at) FROM form_outcomes").fetchone()[0] == T0 + 4


# ---- the owner's alone; nothing unasked ----------------------------------------------------------------------------

def test_no_family_or_agent_scope_reaches_it(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    aid = shown(db)
    for viewer in ("family", "agent"):
        for call in (lambda: learn.record_outcome(db, aid, "kept", viewer=viewer),
                     lambda: learn.adjustments_for(db, viewer=viewer, **KEY),
                     lambda: learn.plan(db, F.Request(), (SHAPE,), CONSOLE, viewer=viewer),
                     lambda: learn.report(db, viewer=viewer), lambda: learn.reset(db, T0, viewer=viewer),
                     lambda: learn.gap_report(db, viewer=viewer)):
            with pytest.raises(A.StoreRefused) as caught:
                call()
            assert caught.value.rule == "scope"
    assert rows(db) == 1


def test_quiet_holds_nothing_because_nothing_runs_unasked(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply", "quiet": True}, T0)
    threads = threading.active_count()
    aid = shown(db)
    assert learn.record_outcome(db, aid, "kept")["recorded"]           # the owner's own command works when quiet
    learn.plan(db, F.Request(), (SHAPE,), CONSOLE)
    learn.report(db)
    learn.gap_report(db)
    assert threading.active_count() == threads


# ---- it explains itself; the commands -----------------------------------------------------------------------------

def test_the_report_says_what_was_observed_what_it_changed_and_what_is_learned(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    _teach(db, "table", KEY)
    one = shown(db, key={"kind": "series", "purpose": "change_over_time", "device": "pocket"}, form="line_chart")
    learn.record_outcome(db, one, "dismissed")
    report = learn.report(db)
    assert report["mode"] == "apply" and report["rows"] == 6 and report["min_evidence"] == 2
    assert "no model weight is updated" in report["what_is_learned"]["model_weights"]
    words = {tuple(k["key"]): k["words"] for k in report["keys"]}
    assert words[("categories", "compare", "console")] == (
        "categories for how they compare on the console: observed 2 check passed, 2 replaced; prefers a table, "
        "where the planner allows it")
    assert words[("series", "change_over_time", "pocket")].endswith("not enough yet (1 of 2), so nothing changes")


def test_the_commands(individual, capsys):
    db, path = individual
    aid = shown(db)
    db.close()
    from neuroscifi.cli import main
    assert main(["artifact", "feedback", "--db", str(path), aid, "kept"]) == 1
    assert "Nothing was recorded: form learning is off" in capsys.readouterr().out
    assert main(["config", "--db", str(path), "--form-learning", "record"]) == 0
    capsys.readouterr()
    with closing(store.open_existing(path)) as again:
        learn.record_outcome(again, aid, "check_passed", form="bar_chart", **KEY)
    assert main(["artifact", "feedback", "--db", str(path), aid, "replaced:table"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Recorded: you replaced the bar_chart made for categories on the console (you wanted table)")
    for bad in ((aid, "loved"), (aid, "replaced"), (aid, "replaced:hologram"), (aid,), ("--reset", aid)):
        assert main(["artifact", "feedback", "--db", str(path), *bad]) == 2
        assert "Traceback" not in capsys.readouterr().err
    assert main(["form", "learned", "--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Form learning records your reactions and uses nothing learned")
    assert "model weights (none: no model weight is updated anywhere" in out and "prefers a table" in out
    assert main(["form", "learned", "--db", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["keys"][0]["preferences"] == {"table": 1.0}
    assert main(["artifact", "gaps", "--db", str(path), "--report"]) == 0
    assert capsys.readouterr().out.startswith("What is missing most, from what is already recorded (read-only")
    assert main(["artifact", "gaps", "--db", str(path)]) == 0                   # without --report: as before
    assert capsys.readouterr().out.strip() == "Every file so far was read by an adapter here."
    assert main(["artifact", "feedback", "--db", str(path), "--reset"]) == 0
    assert capsys.readouterr().out.startswith("Removed 2 recorded reaction(s)")


# ---- gap detection: read-only, from records that exist -------------------------------------------------------------

def test_the_gap_report_reads_what_is_recorded_and_changes_nothing(individual, monkeypatch):
    db, _ = individual
    store.set_config(db, {"form_learning": "record"}, T0)
    unread = [A.add(db, b"\x00\x13NSFX" + bytes(range(200)) * 3 + bytes([i]), name=f"x{i}.bin", now=T0).id
              for i in range(2)]
    monkeypatch.setattr(A, "_check_summary", lambda data, media, spec: {"ok": False, "failed": ["bars_from_zero"],
                                                                        "warnings": []})
    failing = made_chart(db, title="Failing")
    monkeypatch.undo()
    history = [{"round": 1, "rule": "labels_overlap", "change": "labels rotated"}]
    repaired = [made_chart(db, title=f"Repaired {i}", history=history).id for i in range(2)]
    words = made_chart(db, title="Words")
    learn.record_outcome(db, words.id, "check_passed", kind="series", purpose="change_over_time", device="console",
                         form="prose")
    A.link(db, repaired[0], "contradicts", "artifact", repaired[1], "test.reader", 1.0)
    before = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
              ("artifacts", "artifact_links", "artifact_events", "artifact_gaps", "form_outcomes", "deletion_log")}
    report = learn.gap_report(db)
    lines = {line["gap"]: line for line in report["lines"]}
    gaps = {g["format"] for g in A.gaps(db)}
    assert lines["not_decoded"]["subject"] in gaps and set(lines["not_decoded"]["evidence"]) == set(unread)
    assert lines["check_failed"]["count"] == 1 and lines["check_failed"]["evidence"] == [failing.id]
    assert "bars_from_zero 1" in lines["check_failed"]["words"]
    assert lines["repair_recurs"]["subject"] == "labels_overlap" and lines["repair_recurs"]["count"] == 2
    assert set(lines["repair_recurs"]["evidence"]) == set(repaired)
    assert lines["words_for_shown_content"]["subject"] == "series"
    assert lines["words_for_shown_content"]["evidence"] == [words.id]
    assert lines["relations_inconsistent"]["evidence"] == [f"{repaired[0]} {repaired[1]}"]
    assert "nothing records them" in lines["could_not_make_well"]["words"]
    assert report["label"] == "read-only: nothing acts on this report"
    assert before == {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}


# ---- when a habit decided, it says so (the main session's condition on the preferences path) ----------------------

def test_a_learned_preference_that_flips_the_choice_says_so_and_one_that_does_not_says_nothing(individual, capsys):
    db, path = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    for i, reaction in enumerate(("kept", "kept", "kept", "dismissed")):        # a table was made and judged 4 times
        aid = shown(db, form="table", now=T0 + i)
        learn.record_outcome(db, aid, reaction, now=T0 + i)
    aid = shown(db, now=T0 + 5)                                                 # and once a bar chart replaced by one
    learn.record_outcome(db, aid, "replaced", by="table", now=T0 + 5)
    plan, learned = learn.plan(db, F.Request(), (SHAPE,), CONSOLE)
    assert F.plan(F.Request(), (SHAPE,), CONSOLE).chosen == ("bar_chart",) and plan.chosen == ("table",)
    assert learned.decided == (
        "You kept a table 3 of 4 times it was made and asked for a table 1 time instead of another form for "
        "categories on the console: that habit, not the content, decided this form (from the content alone it would "
        "be a bar chart).")
    # the same key's preference where the planner already chooses the preferred form: no sentence
    store.set_config(db, {"form_learning": "record"}, T0 + 6)
    learn.reset(db, T0 + 6)
    store.set_config(db, {"form_learning": "apply"}, T0 + 7)
    for i in range(2):
        learn.record_outcome(db, shown(db, now=T0 + 8 + i), "kept", now=T0 + 8 + i)
    plan, learned = learn.plan(db, F.Request(), (SHAPE,), CONSOLE)
    assert learned.status == "applied" and plan.chosen == ("bar_chart",) and learned.decided is None
    # nor while nothing is applied
    store.set_config(db, {"form_learning": "record"}, T0 + 10)
    assert learn.plan(db, F.Request(), (SHAPE,), CONSOLE)[1].decided is None
    # `nsf form plan --db` prints it
    store.set_config(db, {"form_learning": "apply"}, T0 + 11)
    learn.reset(db, T0 + 11)
    for i in range(2):
        aid = shown(db, now=T0 + 12 + i)
        learn.record_outcome(db, aid, "replaced", by="table", now=T0 + 12 + i)
    db.close()
    from neuroscifi.cli import main
    shape = json.dumps({"kinds": ["categories"], "n_items": 16, "n_series": 1})
    argv = ["form", "plan", "--request", "", "--shape", shape, "--assume-available"]
    assert main(argv) == 0 and "Learned:" not in capsys.readouterr().out      # no individual read: as before
    assert main([*argv, "--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Form: A table" in out and ("Learned: You asked for a table 2 times instead of another form for categories "
                                       "on the console: that habit, not the content, decided this form") in out
    assert main([*argv, "--db", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["learned"]["decided"].startswith("You asked for a table 2 times")
