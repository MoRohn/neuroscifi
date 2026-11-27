"""What was learned cannot reach the planner's hard constraints or its proportion rules (OMNIMIND lane L, ADR-078).

Lane C's own property tests (tests/artifacts/test_forms.py, unchanged) are run here with every plan they make given a
RANDOM learned policy the way artifacts/learn.py hands one over (`Learned.context`: preferences in (0, 1] on up to
four forms, the owner's stated preferences winning) and random weight adjustments within the planner's clamps. Their
assertions read the context the test built, so a learned value that broke a hard constraint, made a sentence's worth
elaborate, overrode an explicit form or broke a cost floor would fail them. The counts are in
docs/quality/evidence/omnimind_l.json (scripts/omnimind_l_evidence.py)."""
import random

import pytest

from neuroscifi.artifacts import forms as F, learn

import test_forms as C

CRITERIA = [c for c in F.CRITERIA if c != "feasibility"]
WORDS = ("prose", "spoken")


def random_learned(rng, admitted, *, words=True):
    """A learned table as learn.py hands one over: values in (0, 1], mostly on forms the planner admitted (what a
    person is shown and names), sometimes on any form."""
    pool = sorted(set(admitted) | set(rng.sample(tuple(F.FORMS), 2)))
    names = [n for n in pool if words or n not in WORDS]
    prefs = {n: round(rng.uniform(0.01, 1.0), 4) for n in rng.sample(names, min(len(names), rng.randint(0, 4)))}
    return learn.Learned(("records", "look_up", "console"), "applied" if prefs else "nothing_learned", prefs, "random")


def random_adjustments(rng):
    if rng.random() < 0.5:
        return None
    return {("*", "*", "*"): {c: rng.uniform(-F.MAX_ADJUST, F.MAX_ADJUST) for c in CRITERIA}}


class Learning:
    """F.plan with a random learned policy and random adjustments on every call; counts what was applied. The policy
    is fixed per (request, content kinds, context), as a learned table is fixed per key whatever the content's size."""

    def __init__(self, seed, *, words=True):
        self.seed, self.words, self.real = seed, words, F.plan
        self.plans = self.applied = self.changed = 0

    def __call__(self, request, content=(), context=None, *, adjustments=None):
        context = context or F.Context()
        shapes = (content,) if isinstance(content, F.ContentShape) else tuple(content)
        rng = random.Random(f"{self.seed}|{request}|{[s.kinds for s in shapes]}|{context}")
        adjust = adjustments if adjustments is not None else random_adjustments(rng)
        alone = self.real(request, content, context, adjustments=adjust)
        admitted = [c.forms[0] for c in alone.candidates if c.admitted and len(c.forms) == 1]
        merged = random_learned(rng, admitted, words=self.words).context(context)
        self.plans += 1
        if merged is context:
            return alone
        self.applied += 1
        result = self.real(request, content, merged, adjustments=adjust)
        self.changed += result.chosen != alone.chosen
        return result


@pytest.fixture
def learning(monkeypatch):
    def install(seed, **kw):
        wrapper = Learning(seed, **kw)
        monkeypatch.setattr(F, "plan", wrapper)
        return wrapper
    return install


# (a lane C property test, the fewest choices the learned policies must have changed for the run to prove anything:
# under the sentence's-worth test a learned preference is expected to change nothing, which is the property)
CASES = [(C.test_hard_constraints_are_never_violated_over_random_contexts, 10),
         (C.test_a_scalar_or_a_sentence_stays_words_whatever_the_words_say, 0),
         (C.test_an_explicit_form_is_honoured_whenever_it_is_feasible_and_explained_when_not, 1),
         (C.test_no_answer_breaks_a_cost_floor_or_a_medium_by_construction, 10)]


@pytest.mark.parametrize("test, changed", CASES, ids=[t.__name__ for t, _ in CASES])
def test_lane_c_properties_hold_under_random_learned_policies(learning, test, changed):
    wrapper = learning(78)
    test()
    # not vacuous: learned preferences reached most plans (and changed choices where the planner allows it)
    assert wrapper.plans >= 600 and wrapper.applied > wrapper.plans // 2 and wrapper.changed >= changed, \
        (wrapper.plans, wrapper.applied, wrapper.changed)


def test_more_data_never_moves_the_choice_toward_words_when_nothing_learned_names_words(learning):
    """Monotonicity holds under learned preferences for forms that are not words. A learned preference FOR words is
    the one exception, by design: a person who keeps sentences gets sentences, however large the content (the
    planner's proportion rules still keep a sentence's worth in words the other way round)."""
    wrapper = learning(79, words=False)
    C.test_more_data_never_moves_the_choice_toward_words()
    assert wrapper.applied > 300 and wrapper.changed > 50, vars(wrapper)
