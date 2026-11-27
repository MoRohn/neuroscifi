"""The pipeline's promises (fix lane F1, ruling D): a planned form with no builder falls through to the next
admitted form that has one, and the record says so (code review M4); the planner is not changed."""
from neuroscifi.artifacts import produce as P
from neuroscifi.artifacts import produce_builders as B

import test_produce as base


def test_a_planned_form_without_a_builder_falls_through_and_says_so(monkeypatch):
    first = P.produce(None, "show me the temperature over time", [P.Given("temps.csv", base.temps_csv())])
    planned = first.record["plan"]["chosen"][0]
    assert first.status == "made"
    monkeypatch.setattr(B, "BUILDERS", {k: v for k, v in B.BUILDERS.items() if k != planned})
    second = P.produce(None, "show me the temperature over time", [P.Given("temps.csv", base.temps_csv())])
    assert second.status == "made", second.record.get("candidates")
    made = [c for c in second.record["candidates"] if c.get("fallback")]
    assert made and made[0]["forms"][0] != planned and "no builder" in made[0]["fallback"]


def test_the_decode_is_given_only_the_time_that_is_left(monkeypatch):
    seen = []
    real = P._decode
    monkeypatch.setattr(P, "_decode", lambda data, name, limits: seen.append(limits.max_seconds) or real(data, name,
                                                                                                         limits))
    ctx = P.forms.Context(device="cli", budget_s=3.0)
    P.produce(None, "show me the temperature over time", [P.Given("temps.csv", base.temps_csv())], context=ctx)
    assert seen and all(0 < s <= 3.0 for s in seen)
