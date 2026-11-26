"""The form planner (DESIGN §5, ADR-073): properties, not cue words.

Random inputs come from seeded generators, so every run checks the same cases (the counts are in
docs/quality/evidence/omnimind_c.json). The properties: hard constraints are never violated; an explicit form is
honoured whenever it is feasible and explained when not; a scalar stays words; more data never moves the choice
toward words; plans are deterministic; the sentence names the deciding evidence; every form is reachable and none
is chosen for content it cannot carry; combinations only when the second form adds; alternatives differ in kind;
the pre-production check predicts the stated problems; the adjustments seam is bounded and clamped.
"""
import json
import math
import random

import pytest

from neuroscifi.artifacts import forms as F
from neuroscifi.artifacts import make
from neuroscifi.artifacts.types import SHAPE_KINDS, ContentShape, Malformed

ALL = tuple(sorted({f.generator for f in F.FORMS.values() if f.generator}))
N_RANDOM = 600          # random (request, content, context) triples per property
N_MONOTONE = 150        # random shapes grown through 8 sizes each

# Request wordings that name no form and wish for nothing: purpose cues only, of every purpose.
NEUTRAL = ("", "How did it change?", "Compare these.", "What is the value?", "How are they related?",
           "How does it work?", "What if I change the rate?", "Where is it?", "What does it sound like?",
           "Explain this.", "What does it look like?", "How are the values spread?", "Save a copy for my records.",
           "Tell me about it.", "Which one is bigger?", "What happened?")


def shape(kinds, n=0, k=0, t=False, space=0, params=False, duration=0.0, chars=0):
    return ContentShape(tuple(kinds), n, k, t, space, params, duration, chars)


def random_shape(rng):
    kinds = rng.sample(SHAPE_KINDS, rng.choice((1, 1, 1, 2, 2, 3)))
    n = int(10 ** rng.uniform(0, 6.5)) if rng.random() < 0.9 else rng.randint(0, 3)
    space = rng.choice((0, 0, 2, 3))
    if any(k in kinds for k in ("image", "map")):
        space = max(space, 2)
    return shape(kinds, n, rng.choice((0, 1, 1, 2, 3, 5, 8, 30)), rng.random() < 0.5, space, rng.random() < 0.3,
                 rng.choice((0.0, 0.0, 2.0, 40.0, 400.0)), rng.choice((0, 0, 100, 2000, 50_000)))


def random_context(rng):
    shows = None if rng.random() < 0.7 else tuple(rng.sample(F.MEDIA, rng.randint(1, len(F.MEDIA))))
    gens = ALL if rng.random() < 0.5 else tuple(rng.sample(ALL, rng.randint(0, len(ALL))))
    prefs = {}
    if rng.random() < 0.3:
        prefs = {rng.choice(tuple(F.FORMS)): rng.choice((-1.0, -0.5, 0.5, 1.0)) for _ in range(rng.randint(1, 3))}
    return F.Context(device=rng.choice(F.DEVICES), shows=shows,
                     accessibility=tuple(rng.sample(F.ACCESSIBILITY, rng.choice((0, 0, 0, 1, 2)))),
                     preferences=prefs, budget_s=rng.choice((0.05, 1.0, 60.0, 1e6)),
                     budget_bytes=rng.choice((2000, 200_000, 16 << 20, 1 << 40)), generators=gens)


def random_request(rng):
    text = rng.choice(NEUTRAL + ("Show me everything.", "Just tell me briefly.", "Put it in a table.",
                                 "Plot it.", "Make a line chart of it.", "No chart, please, just the trend.",
                                 "Read it aloud.", "Export it as a CSV file.", "Draw a diagram of it.",
                                 "What were the exact figures?", "Animate it.", "What's the highest value in the "
                                 "table of contents?"))
    explicit = rng.choice((None,) * 6 + tuple(F.FORMS) + ("chart", "sound", "3d", "graph"))
    return F.Request(text, explicit_form=explicit)


def triples(seed, n=N_RANDOM):
    rng = random.Random(seed)
    for _ in range(n):
        yield random_request(rng), (random_shape(rng),), random_context(rng)


def words_form(context):
    return "prose" if "text" in F.resolve_display(context).shows else "spoken"


# ---------------------------------------------------------------------------------------------------------------
# Hard constraints, determinism, shape of the plan


def test_hard_constraints_are_never_violated_over_random_contexts():
    for request, content, context in triples(1):
        result = F.plan(request, content, context)
        display = F.resolve_display(context)
        reading = F.read_request(request)
        if not result.chosen:
            assert all(c.excluded for c in result.candidates), "no choice only when nothing is admitted"
            continue
        chosen = result.candidate(result.chosen)
        assert chosen is not None and chosen.excluded is None
        for name in result.chosen:
            form = F.FORMS[name]
            assert set(form.media) <= display.shows, (name, display.shows)
            assert F.feasible_here(form, frozenset(context.generators))
            assert name not in reading.refused or (reading.explicit and name in reading.explicit)
            assert context.preferences.get(name, 0.0) > -1.0
            if "voice_only" in context.accessibility:
                assert set(form.media) <= {"speech", "sound"}
            if "reduced_motion" in context.accessibility:
                assert "motion" not in form.media
        first = F.FORMS[result.chosen[0]]
        if "screen_reader" in context.accessibility and not (first.text_like or {"speech", "sound"} &
                                                             set(first.media)):
            assert len(result.chosen) == 2 and F.FORMS[result.chosen[1]].text_like, result.chosen
        assert chosen.estimates["seconds"] <= context.budget_s
        assert chosen.estimates["bytes"] <= context.budget_bytes


def test_plans_are_deterministic_and_complete():
    for request, content, context in triples(2, 200):
        a, b = F.plan(request, content, context), F.plan(request, content, context)
        assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True)
        assert a.decided_by in F.DECIDED_BY
        assert a.purpose in F.PURPOSES and abs(sum(a.purposes.values()) - 1) < 1e-3
        singles = {c.forms[0] for c in a.candidates if len(c.forms) == 1}
        assert singles == set(F.FORMS), "every form is on the score sheet"
        for cand in a.candidates:
            assert set(cand.scores) == set(F.CRITERIA)
            assert all(0.0 <= v <= 1.0 for v in cand.scores.values()), cand
        assert "not evidence" in a.label


def test_a_plan_does_not_depend_on_unrelated_registry_order(monkeypatch):
    request, content = F.Request("How did it change?"), (shape(["series"], 365, 1, True),)
    context = F.Context(generators=ALL)
    before = F.plan(request, content, context).to_dict()
    monkeypatch.setattr(F, "FORMS", dict(reversed(list(F.FORMS.items()))))
    after = F.plan(request, content, context).to_dict()
    assert before["chosen"] == after["chosen"] and before["decided_by"] == after["decided_by"]
    assert before["reason"] == after["reason"]


# ---------------------------------------------------------------------------------------------------------------
# Explicit requests


def test_an_explicit_form_is_honoured_whenever_it_is_feasible_and_explained_when_not():
    rng = random.Random(3)
    honoured = refused = 0
    for _ in range(N_RANDOM):
        content, context = (random_shape(rng),), random_context(rng)
        name = rng.choice(tuple(F.FORMS))
        result = F.plan(F.Request("", explicit_form=name), content, context)
        # Feasible: admitted on the plan's own score sheet, alone or (for a screen reader) with the text that goes with
        # it; the sheet's exclusions are the hard constraints checked in the test above.
        feasible = any(c.excluded is None for c in result.candidates if c.forms[0] == name and
                       (len(c.forms) == 1 or "screen_reader" in context.accessibility))
        assert result.decided_by == "explicit"
        if feasible:
            honoured += 1
            assert result.chosen[0] == name and result.explicit_honoured is True
            assert "you asked for one" in result.reason
        else:
            refused += 1
            assert result.chosen[:1] != (name,) and result.explicit_honoured is False
            if result.chosen:
                assert "instead of" in result.reason and "because" in result.reason
    assert honoured > 50 and refused > 50


def test_explicit_forms_named_in_words():
    read = lambda text: F.read_request(F.Request(text))  # noqa: E731
    assert read("Can you put this in a table?").explicit == ("table",)
    assert read("Show it as a line chart").explicit == ("line_chart",)
    assert read("plot the temperatures").explicit == F.CHARTS
    assert read("Please read it aloud").explicit == ("spoken",)
    assert read("export the results").explicit == ("data_file",)
    assert read("What is the highest value in the table?").explicit is None, "names content, not an output"
    assert read("What is on the table of contents?").explicit is None
    assert read("Which chart topped the charts in 1990?").explicit is None
    no = read("No chart please, just the trend")
    assert no.explicit is None and "line_chart" in no.refused and "node_link" in no.refused
    assert read("I don't want a chart").explicit is None and "bar_chart" in read("I don't want a chart").refused
    swap = read("give me a table, not a chart")
    assert swap.explicit == ("table",) and "line_chart" in swap.refused and "table" not in swap.refused
    assert read("No, put it in a table").explicit == ("table",)
    assert read("not now, make a chart").explicit == F.CHARTS


def test_a_family_named_takes_its_best_feasible_member_or_explains():
    series = (shape(["series"], 365, 1, True),)
    result = F.plan(F.Request("Can you make a chart of this?"), series, F.Context(generators=ALL))
    assert result.chosen[0] in F.CHARTS and result.explicit_honoured
    one = F.plan(F.Request("plot it"), (shape(["scalar"], 1, 1),), F.Context(generators=ALL))
    assert one.chosen[0] in F.CHARTS and one.explicit_honoured, "a chart of one value, named, is their call"
    net = F.plan(F.Request("as a line chart"), (shape(["network"], 40),), F.Context(generators=ALL))
    assert net.chosen[0] != "line_chart" and not net.explicit_honoured
    assert "instead of a line chart you asked for" in net.reason and "cannot carry" in net.reason
    none_here = F.plan(F.Request("as a line chart"), series, F.Context(generators=()))
    assert none_here.chosen == ("prose",) and "nothing here can make a line chart yet" in none_here.reason
    voice = F.plan(F.Request("as a line chart"), series, F.Context(device="voice", generators=ALL))
    assert voice.chosen and "this device cannot show pictures" in voice.reason


# ---------------------------------------------------------------------------------------------------------------
# Words stay words where words serve; more data never moves toward words


def test_a_scalar_or_a_sentence_stays_words_whatever_the_words_say():
    rng = random.Random(4)
    wordings = [w for w in NEUTRAL if "sound" not in w] + ["Show me everything.", "In detail please.",
                                                            "Let me explore it.", "How did it change over time?"]
    for _ in range(N_RANDOM):
        context = random_context(rng)
        kinds = rng.choice((["scalar"], ["series"], ["categories"], ["records"], ["prose"], ["scalar", "prose"]))
        n = 1 if "scalar" in kinds else rng.randint(0, 3)
        content = (shape(kinds, n, rng.choice((0, 1)), rng.random() < 0.5, chars=rng.randint(0, 500)),)
        result = F.plan(F.Request(rng.choice(wordings)), content, context)
        candidate = result.candidate((words_form(context),))
        if candidate is not None and candidate.excluded is None:
            assert result.chosen == (words_form(context),), (content, result.chosen, result.reason)


def test_hearing_a_value_is_the_one_exception_and_needs_a_tone():
    one = (shape(["scalar"], 1, 1),)
    assert F.plan(F.Request("What does 440 Hz sound like?"), one, F.Context(generators=ALL)).chosen == ("tone",)
    assert F.plan(F.Request("What does 440 Hz sound like?"), one, F.Context(generators=())).chosen == ("prose",)


def test_more_data_never_moves_the_choice_toward_words():
    rng = random.Random(5)
    checked = 0
    for _ in range(N_MONOTONE):
        base = random_shape(rng)
        context = F.Context(device=rng.choice(("console", "pocket", "cli", "agent", "voice")),
                            budget_s=1e9, budget_bytes=1 << 60,
                            generators=ALL if rng.random() < 0.6 else tuple(rng.sample(ALL, rng.randint(0, 10))))
        request = F.Request(rng.choice(NEUTRAL))
        words = {("prose",), ("spoken",)}
        left_words = False
        for n in (1, 2, 4, 8, 30, 200, 5000, 2_000_000):
            grown = ContentShape(base.kinds, n, base.n_series, base.has_time, base.has_space, base.has_parameters,
                                 base.duration_s, base.text_chars)
            chosen = F.plan(request, (grown,), context).chosen
            if left_words:
                assert chosen not in words, (base, n, request.text, context.device)
                checked += 1
            elif chosen and chosen not in words:
                left_words = True
    assert checked > 100


def test_same_words_different_content_different_forms():
    context = F.Context(generators=ALL)
    ask = F.Request("How did it change?")
    assert F.plan(ask, (shape(["series"], 2, 1, True),), context).chosen == ("prose",)
    assert F.plan(ask, (shape(["series"], 365, 1, True),), context).chosen == ("line_chart",)
    steps = F.Request("How do I do it?")
    assert F.plan(steps, (shape(["sequence"], 4),), context).chosen == ("prose",)
    assert F.plan(steps, (shape(["sequence"], 15),), context).chosen[0] in ("flow_steps", "step_through")
    what_if = F.Request("What if I change it?")
    assert F.plan(what_if, (shape(["process"], 30, 2, params=True),), context).chosen[0] == "explorable"
    assert F.plan(what_if, (shape(["scalar"], 1, 1),), context).chosen == ("prose",)


def test_same_content_different_words_or_context():
    series = (shape(["series"], 365, 1, True),)
    assert F.plan(F.Request("Just tell me briefly"), series, F.Context(generators=ALL)).chosen == ("prose",)
    voice = F.plan(F.Request(""), series, F.Context(device="voice", generators=ALL))
    assert voice.chosen == ("spoken",) and voice.decided_by == "context", "data as pitch only when hearing it is asked"
    heard = F.plan(F.Request("What does it sound like?"), series, F.Context(device="voice", generators=ALL))
    assert heard.chosen[0] == "sonification"
    reader = F.plan(F.Request(""), series, F.Context(accessibility=("screen_reader",), generators=ALL))
    assert reader.chosen[0] == "line_chart" and reader.chosen[1] in ("prose", "table")
    assert reader.decided_by == "context"


# ---------------------------------------------------------------------------------------------------------------
# The sentence


_CONTEXT_WORDS = ("this device", "screen reader", "nothing here can make", "budget", "you said you never",
                  "reduced motion", "only speech and sound", "carries too little", "would add nothing",
                  "preferences you have stated", "accessibility needs", "cannot carry", "only when it is asked")


def test_the_sentence_names_the_deciding_evidence():
    seen = set()
    for request, content, context in triples(6):
        result = F.plan(request, content, context)
        reason = result.reason
        assert reason.endswith(".") and ". " not in reason.replace("e.g. ", ""), reason
        assert len(reason) < 400
        seen.add(result.decided_by)
        if not result.chosen:
            assert reason.startswith("No form can be given")
        elif result.decided_by == "explicit":
            assert "you asked for" in reason
        elif result.decided_by == "words":
            assert "you asked" in reason or "you said" in reason
        elif result.decided_by == "context":
            assert any(w in reason for w in _CONTEXT_WORDS), reason
        else:
            assert any(ch.isdigit() for ch in reason) or "single value" in reason or "nothing to show" in reason \
                or "a picture" in reason or "code" in reason or "few sentences" in reason, reason
    assert seen == set(F.DECIDED_BY)


def test_the_example_sentence_of_the_brief():
    result = F.plan(F.Request("How did it change over the year?"), (shape(["series"], 365, 1, True),),
                    F.Context(generators=ALL))
    assert result.chosen == ("line_chart",) and result.decided_by == "content"
    assert result.reason.startswith("A line chart, because there are 365 values")
    assert "a sentence could carry only about 7" in result.reason


# ---------------------------------------------------------------------------------------------------------------
# Reachability and carrying


def _probe_inputs():
    kinds_sets = [[k] for k in SHAPE_KINDS] + [["records", "series"], ["records", "relation"], ["network", "prose"],
                                               ["image", "sequence"], ["sound", "series"], ["prose", "hierarchy"],
                                               ["records", "categories"], ["process", "surface"]]
    for kinds in kinds_sets:
        for n in (1, 14, 400, 30_000):
            for k in (1, 8):
                for t in (False, True):
                    yield shape(kinds, n, k, t, 2 if {"image", "map"} & set(kinds) else
                                3 if {"solid", "surface"} & set(kinds) else 0, "process" in kinds,
                                5.0 if "sound" in kinds else 0.0, 30_000 if "prose" in kinds else 0)


def test_every_registered_form_is_reachable_unasked_and_none_carries_nothing():
    reached = set()
    wordings = ("", "How did it change?", "What does it sound like?", "Save a copy for my records.",
                "How does it work?", "How are they related?", "What does it look like?")
    for content in _probe_inputs():
        for text in wordings:
            for device in ("console", "voice"):
                context = F.Context(device=device, generators=ALL)
                result = F.plan(F.Request(text), (content,), context)
                display = F.resolve_display(context)
                for name in result.chosen:
                    assert F.FORMS[name].carried(content, display) > 0, (name, content)
                reached.update(result.chosen)
    assert reached >= set(F.FORMS), sorted(set(F.FORMS) - reached)


def test_each_form_declares_what_it_needs():
    for name, form in F.FORMS.items():
        assert form.rule and form.label and form.media and form.kinds
        assert form.generator is None or form.generator in ALL
        assert form.fallback is None or form.fallback in F.FORMS
        assert form.cost_class in ("none", "low", "medium", "high")
    assert F.FORMS["line_chart"].devices == ("console", "pocket")
    assert F.FORMS["spoken"].devices == ("console", "pocket", "voice")
    assert F.FORMS["data_file"].devices == ("console", "pocket", "cli", "agent")
    assert F.FORMS["prose"].generator is None and F.FORMS["spoken"].generator is None


def test_carried_follows_the_stated_counting_rules():
    console = F.resolve_display(F.Context())
    prose, table, line = F.FORMS["prose"], F.FORMS["table"], F.FORMS["line_chart"]
    assert prose.carried(shape(["series"], 7, 1)) == 1.0
    assert prose.carried(shape(["series"], 70, 1)) == pytest.approx(0.1)
    assert F.FORMS["spoken"].carried(shape(["series"], 30, 1)) == pytest.approx(0.1)
    assert table.carried(shape(["records"], 2000, 10)) == 1.0
    assert table.carried(shape(["records"], 200_000, 10)) == pytest.approx(0.3 + 0.7 * 20_000 / 2_000_000)
    limit = 2 * console.plot_px
    assert line.carried(shape(["series"], limit, 6)) == 1.0
    assert line.carried(shape(["series"], limit, 12)) == pytest.approx(0.5)
    assert line.carried(shape(["series"], 10 * limit, 1)) == pytest.approx(0.6 + 0.4 / 10)
    assert line.carried(shape(["scalar"], 1, 1)) == 0.0
    assert F.FORMS["explorable"].carried(shape(["process"], 10)) == 0.0
    assert F.FORMS["explorable"].carried(shape(["process"], 10, params=True)) == 1.0
    assert F.FORMS["node_link"].carried(shape(["records", "network"], 40)) == 1.0, "records ignored beside a kind"


# ---------------------------------------------------------------------------------------------------------------
# Combinations


def test_a_combination_only_when_the_second_form_adds_what_the_first_lacks():
    for request, content, context in triples(7):
        result = F.plan(request, content, context)
        for cand in result.candidates:
            if len(cand.forms) != 2 or cand.excluded:
                continue
            first = result.candidate(cand.forms[:1])
            forced = "screen_reader" in context.accessibility
            gain_carried = cand.scores["carried"] - first.scores["carried"]
            gain_precision = cand.scores["precision"] - first.scores["precision"]
            assert forced or gain_carried >= F.COMBO_CARRIED_GAIN - 1e-9 or gain_precision > 0, cand
            assert forced or first.scores["carried"] >= F.COMBO_PRIMARY_CARRIED


def test_a_chart_with_its_table_for_exact_values_and_a_diagram_with_sentences():
    context = F.Context(generators=ALL)
    exact = F.plan(F.Request("How did sales change and what were the exact figures?"),
                   (shape(["series"], 12, 1, True),), context)
    assert exact.chosen == ("line_chart", "table") and exact.decided_by == "words"
    network = F.plan(F.Request(""), (shape(["network", "prose"], 30, chars=1500),), context)
    assert network.chosen == ("node_link", "prose")


# ---------------------------------------------------------------------------------------------------------------
# Divergence and convergence


def test_difference_separates_kinds_not_decoration():
    for a in F.CHARTS:
        for b in F.CHARTS:
            assert F.difference(a, b) < F.KIND_DIFFERENCE
    assert F.difference("line_chart", "timeline") < F.KIND_DIFFERENCE
    assert F.difference("node_link", "tree_diagram") < F.KIND_DIFFERENCE
    for a, b in (("line_chart", "table"), ("line_chart", "sonification"), ("table", "prose"),
                 ("explorable", "line_chart"), ("solid_view", "table"), ("data_file", "table")):
        assert F.difference(a, b) >= F.KIND_DIFFERENCE, (a, b)
    for a in F.FORMS:
        assert F.difference(a, a) == 0
        for b in F.FORMS:
            assert F.difference(a, b) == F.difference(b, a) and 0 <= F.difference(a, b) <= 1


def test_alternatives_differ_in_kind_start_with_the_choice_and_are_ranked():
    for request, content, context in triples(8, 200):
        result = F.plan(request, content, context)
        alts = F.alternatives(result, 4)
        assert len(alts) <= 4
        if result.chosen:
            assert alts[0].forms == result.chosen
        for i, a in enumerate(alts):
            assert a.excluded is None
            for b in alts[:i]:
                assert F.difference(a.forms[0], b.forms[0]) >= F.KIND_DIFFERENCE
        assert [a.total for a in alts[1:]] == sorted((a.total for a in alts[1:]), reverse=True)
    series = F.plan(F.Request(""), (shape(["series"], 365, 1, True),), F.Context(generators=ALL))
    kinds = [F.FORMS[a.forms[0]].family for a in F.alternatives(series, 3)]
    assert kinds[0] == "chart" and "chart" not in kinds[1:]
    assert F.alternatives(series, 0) == ()


# ---------------------------------------------------------------------------------------------------------------
# Pre-production check


def _codes(form, content, **context):
    return {p.code for p in F.foresee(form, content, F.Context(**context)).problems}


def test_foresee_predicts_the_stated_problems_from_counts():
    assert "too_many_series" in _codes("line_chart", shape(["series"], 100, 9, True))
    assert "too_many_series" not in _codes("line_chart", shape(["series"], 100, 6, True))
    assert "points_per_pixel" in _codes("line_chart", shape(["series"], 100_000, 1, True))
    assert "too_many_categories" in _codes("bar_chart", shape(["categories"], 40, 1), device="pocket")
    assert "too_many_categories" not in _codes("bar_chart", shape(["categories"], 40, 1))
    assert "label_overlap" in _codes("bar_chart", shape(["categories"], 30, 1, chars=30 * 25))
    assert "label_overlap" in _codes("node_link", shape(["network"], 140, chars=140 * 20))
    assert "sound_too_long" in _codes("sonification", shape(["series"], 1000, 1))
    assert "sound_too_long" not in _codes("sonification", shape(["series"], 100, 1))
    assert "table_beyond_screen" in _codes("table", shape(["records"], 31, 2))
    assert "table_beyond_screen" not in _codes("table", shape(["records"], 30, 2))
    assert "table_beyond_screen" in _codes("table", shape(["records"], 16, 2), device="pocket")
    assert "animation_too_long" in _codes("animation", shape(["process"], 600))
    assert "too_many_steps" in _codes("flow_steps", shape(["sequence"], 25))


def test_foresee_estimates_are_labelled_and_grow_with_the_content():
    for name in F.FORMS:
        small = F.foresee(name, shape(["series", "records"], 10, 2, True))
        large = F.foresee(name, shape(["series", "records"], 100_000, 2, True))
        assert small.label == large.label == "estimate"
        assert large.estimates["bytes"] >= small.estimates["bytes"] >= 0
        assert large.estimates["seconds"] >= small.estimates["seconds"] >= 0


def test_the_planner_rejects_or_adapts_with_the_check():
    context = F.Context(generators=ALL)
    many = F.plan(F.Request(""), (shape(["series"], 365, 30, True),), context)
    assert many.candidate(("line_chart",)).excluded and "small multiples" in many.candidate(("line_chart",)).excluded
    assert many.chosen[0] != "line_chart"
    only_line = F.plan(F.Request(""), (shape(["series"], 365, 30, True),), F.Context(generators=("chart.line",)))
    assert only_line.candidate(("line_chart",)).excluded is None, "no switch target here: adapt, not reject"
    assert only_line.chosen == ("line_chart",)
    long = F.plan(F.Request(""), (shape(["series"], 200_000, 1, True),), context)
    assert long.chosen == ("line_chart",) and "downsample" in long.candidate(("line_chart",)).adaptations


# ---------------------------------------------------------------------------------------------------------------
# Feasibility from make.catalog() at call time


class _FakeGenerator(make.Generator):
    def __init__(self, name):
        self.name = name


def test_feasibility_is_read_from_the_generator_catalog_at_call_time(monkeypatch):
    monkeypatch.setattr(make, "_GENERATORS", {})
    series = (shape(["series"], 365, 1, True),)
    assert F.plan(F.Request(""), series, F.Context()).chosen == ("prose",)
    make.register("chart_line", _FakeGenerator("chart.line"))
    result = F.plan(F.Request(""), series, F.Context())
    assert result.chosen == ("line_chart",)
    monkeypatch.setattr(_FakeGenerator, "available", lambda self: (False, "needs something"))
    assert F.plan(F.Request(""), series, F.Context()).chosen == ("prose",)


# ---------------------------------------------------------------------------------------------------------------
# The learning seam (no learning in this wave)


def test_adjustments_none_and_empty_are_the_default():
    for request, content, context in triples(9, 100):
        a = F.plan(request, content, context).to_dict()
        b = F.plan(request, content, context, adjustments={}).to_dict()
        c = F.plan(request, content, context, adjustments=None).to_dict()
        assert a == b == c


def test_adjustments_are_bounded_clamped_and_never_break_hard_constraints():
    with pytest.raises(Malformed):
        F.plan(F.Request(""), (), adjustments={("series", "look_up", "console"): {"novelty": 0.1}})
    with pytest.raises(Malformed):
        F.plan(F.Request(""), (), adjustments={("nonsense", "look_up", "console"): {"clarity": 0.1}})
    with pytest.raises(Malformed):
        F.plan(F.Request(""), (), adjustments={("series", "look_up", "console", "x"): {"clarity": 0.1}})
    with pytest.raises(Malformed):
        F.plan(F.Request(""), (), adjustments={("series", "*", f"console{i}"): {} for i in range(300)})
    content = (shape(["series"], 365, 1, True),)
    huge = F.plan(F.Request(""), content, F.Context(generators=ALL),
                  adjustments={("series", "*", "*"): {"clarity": 50.0}})
    capped = F.plan(F.Request(""), content, F.Context(generators=ALL),
                    adjustments={("series", "*", "*"): {"clarity": F.MAX_ADJUST}})
    assert huge.weights == capped.weights and huge.weights != F.plan(F.Request(""), content).weights
    assert abs(sum(huge.weights.values()) - 1) < 1e-9 and huge.weights["feasibility"] == 0
    rng = random.Random(10)
    for request, content, context in triples(11, 200):
        adjustments = {(rng.choice(("*",) + SHAPE_KINDS), rng.choice(("*",) + F.PURPOSES),
                        rng.choice(("*",) + F.DEVICES)): {c: rng.uniform(-1, 1) for c in F.CRITERIA}
                       for _ in range(rng.randint(1, 5))}
        result = F.plan(request, content, context, adjustments=adjustments)
        display = F.resolve_display(context)
        for name in result.chosen:
            assert set(F.FORMS[name].media) <= display.shows
            assert F.feasible_here(F.FORMS[name], frozenset(context.generators))


def test_a_weight_shift_can_move_a_close_choice():
    content, context = (shape(["categories"], 12, 1),), F.Context(generators=ALL)
    default = F.plan(F.Request("Compare them"), content, context)
    shifted = F.plan(F.Request("Compare them"), content, context,
                     adjustments={("categories", "compare", "console"): {"precision": 0.1, "clarity": -0.1}})
    assert default.chosen != shifted.chosen or default.weights != shifted.weights
    assert {default.chosen[0], shifted.chosen[0]} <= {"bar_chart", "table"}


# ---------------------------------------------------------------------------------------------------------------
# Inputs are checked; registry


def test_inputs_are_checked():
    with pytest.raises(Malformed):
        F.Context(device="television")
    with pytest.raises(Malformed):
        F.Context(accessibility=("telepathy",))
    with pytest.raises(Malformed):
        F.Context(preferences={"table": math.nan})
    with pytest.raises(Malformed):
        F.Request("x", purpose="world peace")
    with pytest.raises(Malformed):
        F.Request("x", explicit_form="hologram")
    with pytest.raises(Malformed):
        F.plan(F.Request(""), ("not a shape",))
    assert isinstance(Malformed("x"), ValueError)


def test_a_new_form_is_one_registration():
    form = F.Form("bullet_list", "a bulleted list", "words", "list", ("text",), {"categories": 1, "sequence": 1},
                  {"values": 20, "items": 20}, 0.0, "up to 20 bullets", None, 0.9, 0.0, 0.08, 1, "none", None,
                  text_like=True)
    F.register_form(form)
    try:
        with pytest.raises(Malformed):
            F.register_form(form)
        result = F.plan(F.Request("", explicit_form="bullet_list"), (shape(["sequence"], 6),))
        assert result.chosen == ("bullet_list",) and result.explicit_honoured
    finally:
        F.unregister_form("bullet_list")
    assert "bullet_list" not in F.FORMS
    with pytest.raises(Malformed):
        F.register_form(F.Form("Bad Name", "x", "x", "x", ("text",), {}, {}, 0.0, "", None, 0.5, 0, 0, 0, "none",
                               None))


def test_nothing_shown_when_nothing_can_be_shown():
    result = F.plan(F.Request(""), (shape(["series"], 10, 1),),
                    F.Context(device="cli", accessibility=("voice_only",), generators=ALL))
    assert result.chosen == () and result.reason.startswith("No form can be given")
    assert "No form" in F.describe(result)
