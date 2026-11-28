"""Wave O-1 integration, item 1: the production pipeline records the independent check's verdict as lane L's outcome
row (form learning "record" or "apply" only), plans through `learn.plan` so that a learned habit can decide (and the
"why" says so), and `nsf artifact feedback` on a made artifact finds its key from the production record. With
form learning "off" (the default) not a row is written and the production is what it was before."""
from contextlib import closing

import pytest

from neuroscifi.artifacts import forms, learn
from neuroscifi.artifacts import produce as P
from neuroscifi.artifacts import store as A
from neuroscifi.instance import store

from test_produce import T0, temps_csv

REQUEST = "how did it change?"


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        yield db, path
    finally:
        db.close()


def rows(db) -> list:
    return db.execute("SELECT artifact_id, kind, purpose, device, form, outcome, source FROM form_outcomes "
                      "ORDER BY id").fetchall()


def make(db, n=0):
    return P.produce(db, REQUEST, [P.Given(f"temps{n}.csv", temps_csv(60 + n))], now=T0 + n)


def key_for(data: bytes) -> tuple:
    """The key the planner's plan for this content falls under, computed here from the planner and the reader."""
    shape = P.interpret(None, [P.Given("t.csv", data)], P.Limits())[0][0].shape
    return learn.key_of(forms.plan(forms.Request(REQUEST), (shape,), forms.Context()), (shape,), forms.Context())


def test_off_writes_no_row_and_the_production_is_unchanged(individual):
    db, _ = individual
    assert store.config(db).form_learning == "off"
    result = make(db)
    assert result.status == "made" and rows(db) == []
    assert "learning" not in result.record and "learned" not in result.record["plan"]
    alone = P.produce(None, REQUEST, [P.Given("temps0.csv", temps_csv(60))], now=T0)
    # the same planner call, the same plan and the same bytes as a production that cannot learn at all
    assert alone.record["plan"] == result.record["plan"]
    assert alone.spec.spec == result.spec.spec and alone.spec.data == result.spec.data
    stored = A.get(db, result.artifact).index["production"]
    assert "learning" not in stored and "Learned:" not in P.why_text(stored)
    assert "Form learning" not in P.why_text(stored)


def test_record_writes_the_check_verdict_under_the_plans_key_and_applies_nothing(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "record"}, T0)
    result = make(db)
    key = key_for(temps_csv(60))
    assert rows(db) == [(result.artifact, *key, "line_chart", "check_passed", "check")]
    learning = result.record["learning"]
    assert learning["mode"] == "record" and learning["key"] == list(key) and learning["form"] == "line_chart"
    assert learning["status"] == "recording" and "decided" not in learning
    assert result.spec.spec == "chart.line"
    why = P.why_text(A.get(db, result.artifact).index["production"])
    assert "Form learning (record): your reactions are recorded" in why and "Learned:" not in why


def test_apply_lets_a_learned_habit_decide_and_the_why_says_so(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "apply"}, T0)
    first, second = make(db, 0), make(db, 1)
    assert first.spec.spec == second.spec.spec == "chart.line"
    for made in (first, second):
        assert learn.record_outcome(db, made.artifact, "replaced", by="table", now=T0 + 3)["recorded"]
    third = make(db, 2)
    assert third.spec.spec == "table.html", P.why_text(third.record)
    learned = third.record["plan"]["learned"]
    assert learned.startswith("You asked for a table 2 times instead of another form for series on the console")
    assert "from the content alone it would be a line chart" in learned
    assert third.record["learning"]["status"] == "applied" and third.record["learning"]["form"] == "table"
    why = P.why_text(A.get(db, third.artifact).index["production"])
    assert "Planned: A table, because that is what you kept or asked for before" in why
    assert f"Learned: {learned}" in why
    assert rows(db)[-1][4:] == ("table", "check_passed", "check")
    # a choice the planner makes on its own says nothing learned
    store.set_config(db, {"form_learning": "record"}, T0 + 4)
    fourth = make(db, 3)
    assert fourth.spec.spec == "chart.line" and "learned" not in fourth.record["plan"]


def test_feedback_on_a_made_artifact_finds_its_key_from_the_production_record(individual, capsys):
    db, path = individual
    result = make(db)                              # made while form learning was off: no row names it
    assert rows(db) == []
    key = key_for(temps_csv(60))
    db.close()
    from neuroscifi.cli import main
    assert main(["config", "--db", str(path), "--form-learning", "record"]) == 0
    capsys.readouterr()
    assert main(["artifact", "feedback", "--db", str(path), result.artifact, "dismissed"]) == 0
    assert capsys.readouterr().out.startswith("Recorded: you dismissed the line_chart made for")
    with closing(store.open_existing(path)) as again:
        assert rows(again) == [(result.artifact, *key, "line_chart", "dismissed", "owner")]
        # a made artifact whose record names no plan (a bare made file) is still refused with the reason
        from neuroscifi.artifacts import make as M
        from neuroscifi.artifacts.spec import Spec
        spec = Spec.from_dict({"spec": "data.csv", "version": 1, "title": "t", "description": "d",
                               "data": {"columns": [{"name": "x", "kind": "integer", "values": [1, 2]}]},
                               "options": {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}})
        made = M.make(spec)
        bare = A.add_made(again, made, made.spec, now=T0)
        with pytest.raises(Exception, match="nothing records what this artifact was made for"):
            learn.record_outcome(again, bare.id, "kept", now=T0)


def test_a_kept_alternative_and_a_remake_record_their_own_check(individual):
    db, _ = individual
    store.set_config(db, {"form_learning": "record"}, T0)
    result = P.produce(db, REQUEST, [P.Given("temps.csv", temps_csv())], alternatives=3, keep=True, now=T0)
    assert result.kept
    forms_of = {aid: form for aid, _, _, _, form, _, _ in rows(db)}
    assert set(forms_of) == {result.artifact, *result.kept}
    for aid in result.kept:
        assert A.get(db, aid).index["production"]["learning"]["form"] == forms_of[aid]
    again = P.remake(db, result.artifact, options={"width": 500}, now=T0 + 1)
    assert again.status == "made", P.why_text(again.record)
    assert rows(db)[-1][0] == again.artifact and rows(db)[-1][4:] == ("line_chart", "check_passed", "check")
