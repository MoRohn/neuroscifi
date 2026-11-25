"""Lane I's mechanisms in the cognitive benchmark (Project Cognitive Genesis; ADR-051). Each adapter runs the product
code on the benchmark's staged worlds; nothing here re-implements a mechanism.

anticipation / instinct       resources.forecast.assess, the predictive regulation policy.background_gate consults
                              when the owner's predictive_regulation is on, on the plant's history (each observation
                              becomes the internal-state sample the research worker records: time, available and
                              total memory, running background tasks, the audio flag). Components: trend (Holt's
                              trend; off = the level alone), footprint (memory per running background task; off = 0),
                              voice (the regular voice-session pattern). The plant's constants (task memory, length,
                              breach lines) are declared by the benchmark (anticipation.py), not measured on the host.
info_value / instinct         core: the executive with the rule version information-v1 (instance/information.py),
                              adopted on the staged individual with store.set_config as an owner would; its `act`
                              names the camera it means, which is read. web: webcortex.curiosity.rank (the timer's
                              path) with the staged individual's curiosity_learning_progress on.
                              Components: information_rule, learning_progress.
info_value / instinct-idle    the same with web through autonomy.candidates (the idle investigator's path).
retention, adversarial        activate(): every behaviour-changing piece on in the running process for the family
                              (predictive regulation, learning progress, information-v1), minus the ablated ones.
"""
from __future__ import annotations

from contextlib import contextmanager

from . import infovalue, register

COMPONENTS_ANTICIPATION = ("trend", "footprint", "voice")
COMPONENTS_INFO = ("information_rule", "learning_progress")


def history_sample(o) -> dict:
    """An anticipation observation as the internal-state sample the worker records (internal_state.sample)."""
    return {"at": o["seconds"], "available_bytes": o["memory"]["available_bytes"],
            "total_bytes": o["memory"]["total_bytes"], "background_running": o["background_running"],
            "audio": bool(o["audio"])}


class PredictiveRegulator:
    def __init__(self, ablate=frozenset()):
        self.ablate = frozenset(ablate)

    def decide(self, history):
        from ...resources import forecast
        verdict = forecast.assess([history_sample(o) for o in history], now=history[-1]["seconds"],
                                  use_trend="trend" not in self.ablate, use_footprint="footprint" not in self.ablate,
                                  use_voice="voice" not in self.ablate)
        return {"background": "hold" if verdict["hold"] else "allow"}

    def activate(self):
        return switched_on(self.ablate)


class InstinctChooser:
    def __init__(self, ablate=frozenset(), web="curiosity"):
        self.ablate = frozenset(ablate)
        self.core = infovalue.ExecutiveChooser()
        self.web = infovalue.CuriosityChooser() if web == "curiosity" else infovalue.AutonomyChooser()
        self.staged = set()

    def _stage(self, view):
        """Switch the mechanism on in the staged individual, once, as its owner would."""
        if id(view["db"]) in self.staged:
            return
        self.staged.add(id(view["db"]))
        if view["world"] == "core" and "information_rule" not in self.ablate:
            from ...instance import store
            store.set_config(view["db"], {"executive_rule": "information-v1"}, view["now"])
        if view["world"] == "web":      # set either way: the ablation is the setting off, whatever the default
            from ...webcortex import state
            state.set_config(view["db"], {"curiosity_learning_progress": "learning_progress" not in self.ablate},
                             view["now"])

    def choose(self, view):
        self._stage(view)
        if view["world"] == "web":
            return self.web.choose(view)
        from ...instance import executive
        d = executive.decide(view["db"], view["epoch"], view["now"], record=False)
        scope = d.get("scope") or ""
        if d["decision"] == "act" and scope.startswith("camera:"):     # information-v1 names the camera it means
            return scope if scope in view["subjects"] else None
        return self.core.choose(view)

    def activate(self):
        return switched_on(self.ablate)


@contextmanager
def switched_on(ablate=frozenset()):
    """Every behaviour-changing piece of lane I on in this process (minus the ablated), restored afterwards: the
    setting readers return 'on' for every individual the family creates."""
    from ...instance import executive
    from ...resources import policy
    from ...webcortex import curiosity
    saved = (executive.rule_version, curiosity.learning_progress_on, policy.predictive_on)
    if "information_rule" not in ablate:
        executive.rule_version = lambda db: "information-v1"
    curiosity.learning_progress_on = lambda db: "learning_progress" not in ablate
    if not {"trend", "footprint", "voice"} <= set(ablate):
        policy.predictive_on = lambda db_path: bool(db_path)
    try:
        yield
    finally:
        executive.rule_version, curiosity.learning_progress_on, policy.predictive_on = saved


class _Switched:
    def __init__(self, ablate):
        self.ablate = ablate

    def activate(self):
        return switched_on(self.ablate)


ALL = COMPONENTS_ANTICIPATION + COMPONENTS_INFO
register("anticipation", "instinct", lambda ablate: (lambda: PredictiveRegulator(ablate)),
         components=COMPONENTS_ANTICIPATION,
         description="lane I: resources.forecast (Holt trend of memory headroom, background-task footprint, voice "
                     "pattern) adding holds inside the existing gate")
register("info_value", "instinct", lambda ablate: (lambda: InstinctChooser(ablate)), components=COMPONENTS_INFO,
         description="lane I: executive rule information-v1 (core); curiosity learning progress, timer path (web)")
register("info_value", "instinct-idle", lambda ablate: (lambda: InstinctChooser(ablate, web="autonomy")),
         components=COMPONENTS_INFO,
         description="lane I: as instinct, web through autonomy.candidates (the idle investigator)")
for _family in ("retention", "adversarial"):
    register(_family, "instinct", lambda ablate: (lambda: _Switched(ablate)), components=ALL,
             description="lane I: predictive regulation, learning progress and information-v1 switched on")

__all__ = ["InstinctChooser", "PredictiveRegulator", "history_sample", "switched_on"]
