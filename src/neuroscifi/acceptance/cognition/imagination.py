"""Lane M's mechanisms in the cognitive benchmark (ADR-052): the counterfactual family's answerer, built from the
product's imagination modules, with one component per mechanism so each can be ablated.

  scm       causal.answer: the linear-Gaussian engine (rungs 1-3 on a declared model; back-door adjustment on a graph)
  front_door  causal.answer's front-door adjustment (ADR-059, Wave 3 lane C): with it off, a graph question whose
            cause a hidden common cause touches is declined, as before Wave 3
  what_if   instance/imagine.what_if: the exact posterior with one reading flipped, on a sandbox copy
  forecast  instance/forecast.forecast: multi-step rollouts from the active graph (horizon 1 stays the graph's own
            one-step prediction, as the baseline answers it)
  action    the push questions, answered only where forecast.action_support holds

Ablating a component makes the answerer say "cannot answer" where only that component could answer, i.e. fall back
to the baseline (TodayAnswerer) there. Retention and adversarial run with the mechanism's settings as a new
individual has them (imagine_what_if, imagine_forecast and imagine_retrospect on, imagine_scenarios off): nothing to
switch on, so activate() is inert.
"""
from __future__ import annotations

from contextlib import nullcontext

from ... import causal
from ...instance import forecast, imagine
from ...instance.errors import InstanceError
from . import counterfactual, register

COMPONENTS = ("scm", "what_if", "forecast", "action", "front_door")
NONE = {"value": None, "sd": None}


class ImaginationAnswerer:
    def __init__(self, ablate=frozenset()):
        self.use = {c: c not in ablate for c in COMPONENTS}
        self._today = counterfactual.TodayAnswerer()

    def activate(self):
        return nullcontext()

    def answer(self, query):
        if "model" in query or "graph" in query:
            if not self.use["scm"]:
                return NONE
            out = causal.answer({k: v for k, v in query.items() if k != "rung"}, front_door=self.use["front_door"])
            return {"value": out["value"], "sd": out["sd"]}
        if "evidence_id" in query:
            if not self.use["what_if"]:
                return NONE
            try:
                out = imagine.what_if(query["db"], query["epoch"], flip=[query["evidence_id"]], detect=False)
            except InstanceError:
                return NONE
            return {"value": out["after"][query["factor"]]["p_inverted"], "sd": 0.0}
        if "context" in query:
            action = query.get("action")
            if action is None and query["horizon"] == 1:
                return self._today.answer(query)
            if not self.use["forecast"] or (action is not None and not self.use["action"]):
                return NONE
            try:
                out = forecast.forecast(query["db"], query["context"], query["state"], query["horizon"],
                                        population=query["population"], action=action)
            except InstanceError:
                return NONE
            return {"value": out["mean"], "sd": out["sd"]}
        return NONE


class _Inert:
    def activate(self):
        return nullcontext()


def _register():
    register("counterfactual", "imagination", lambda ablate: (lambda: ImaginationAnswerer(ablate)),
             components=COMPONENTS,
             description="lane M: linear SCM engine, exact belief what-if on a sandbox, graph rollouts, pushes "
                         "where the recorded inputs identify them")
    for family in ("retention", "adversarial"):
        register(family, "imagination", lambda ablate: _Inert,
                 description="lane M's settings as a new individual has them (what-if, forecast, retrospect on; "
                             "scenario records off)")


_register()
