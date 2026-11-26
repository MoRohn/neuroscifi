"""OMNIMIND benchmark, lane ZF, family `learn`: SIMULATED people (the label everywhere), held-out profiles absent from
development, the reaction model by hand, the reference learners and determinism. The freeze of learn_people.py is
pinned in test_omni_forms_bench.py with the other lane ZF tables."""
import json
import random

import pytest

from neuroscifi.acceptance.omni import learn, learn_people


def _person(**kw):
    base = dict(order=["table", "prose"], kinds={"series": 1}, devices={"console": 1}, noise=0.0, replace=1.0,
                accept_second=0.0)
    base.update(kw)
    return learn_people.Person("T", "test", **base)


def test_learn_is_labelled_simulated():
    for module in (learn, learn_people):
        assert "SIMULATED" in module.__doc__
    assert "SIMULATED" in learn.LABEL and "not benefit to a real person".upper() in learn.LABEL.upper()
    assert "SIMULATED" in learn.score("never_learns", "development")["label"]


def test_learn_held_out_profiles_are_new():
    dev = {json.dumps(learn.profile(p), sort_keys=True) for p in learn_people.DEVELOPMENT}
    for person in learn_people.HELD_OUT:
        assert json.dumps(learn.profile(person), sort_keys=True) not in dev, person["id"]
    assert not {p["id"] for p in learn_people.DEVELOPMENT} & {p["id"] for p in learn_people.HELD_OUT}
    assert not set(learn_people.SEEDS["development"]) & set(learn_people.SEEDS["held_out"])


def test_learn_preferences_by_hand():
    p = _person(by_device={"pocket": ["prose"]}, by_kind={"records": ["data_file"]}, never=["sonification"])
    assert learn.preference(p, "series", "console") == ("table", "prose")
    assert learn.preference(p, "series", "pocket") == ("prose", None)
    assert learn.preference(p, "records", "console")[0] == "data_file"
    assert learn.preference(p, "process", "console")[0] == "prose"           # table is not a candidate there
    listener = _person(order=["sonification", "prose"], never=["sonification"])
    assert learn.preference(listener, "series", "voice")[0] != "sonification"


def test_learn_reactions_by_hand():
    p = _person(never=["sonification"])
    rng = random.Random(0)
    assert learn.react(p, "series", "console", "table", rng)["outcome"] == "kept"
    assert learn.react(p, "series", "console", "line_chart", rng) == {"outcome": "replaced", "by": "table"}
    assert learn.react(p, "series", "console", "no_such_form", rng)["outcome"] != "kept"
    quiet = _person(replace=0.0)
    assert learn.react(quiet, "series", "console", "line_chart", rng) == {"outcome": "dismissed", "by": None}
    noisy = _person(noise=1.0, never=["sonification"])
    assert learn.react(noisy, "series", "console", "table", rng)["outcome"] != "kept"
    assert learn.react(noisy, "series", "console", "line_chart", rng)["outcome"] == "kept"
    # a form the person cannot use is never kept, however inconsistent they are
    assert all(learn.react(noisy, "series", "console", "sonification", random.Random(i))["outcome"] != "kept"
               for i in range(50))


def test_learn_the_case_hides_the_preference_and_streams_are_shared():
    seen = []

    class Spy(learn.NeverLearns):
        def choose(self, case):
            seen.append(case)
            return super().choose(case)
    learn.run_person(Spy, learn_people.DEVELOPMENT[0], 1)
    assert set(seen[0]) == {"person", "index", "cycle", "kind", "device", "candidates", "content"}
    assert len(seen) == learn_people.CYCLES * learn_people.PER_CYCLE
    p = learn_people.HELD_OUT[0]
    assert learn.stream(p, 101) == learn.stream(p, 101) != learn.stream(p, 102)


@pytest.mark.parametrize("split", learn.SPLITS)
def test_learn_references_order(split):
    r = {name: learn.score(name, split) for name in learn.REFERENCES}
    assert r["never_learns"] == learn.score("never_learns", split)                   # deterministic
    late = {k: v["late_kept"]["mean"] for k, v in r.items()}
    assert late["oracle"] >= max(v for k, v in late.items() if k != "oracle")
    # the family can show learning: the reference learner beats the non-learner by more than the visible gain
    visible = 2 * 2 ** 0.5 * r["never_learns"]["late_kept"]["sd_over_seeds"]
    assert late["key_majority"] - late["never_learns"] > visible
    # and leaves room: it is visibly below the oracle over the whole run
    whole = {k: v["all_kept"]["mean"] for k, v in r.items()}
    assert whole["oracle"] - whole["key_majority"] > 2 * 2 ** 0.5 * r["key_majority"]["all_kept"]["sd_over_seeds"]
    assert r["oracle"]["denominator_per_cycle"] == len(learn_people.ALL[split]) * len(learn_people.SEEDS[split]) * \
        learn_people.PER_CYCLE


def test_learn_factory_subjects():
    a = learn.score(learn.KeyMajority, "development")
    b = learn.score("key_majority", "development")
    assert a == b
