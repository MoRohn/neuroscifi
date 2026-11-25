"""Lane W's mechanism for the self-description family: every probe answered from ONE surface, the self-description
record (neuroscifi.self_description.build: `nsf self`, GET /api/inventory?self=1, the Pocket self_status route, the
MCP self_status tool and the voice command read the same record).

Registered as self_model/"self_description". Components (switched off for an ablation with --ablate):
  uncertainty  the record's uncertain.beliefs section (least certain factor of the current epoch)
  decisions    the record's decisions.executive_now section (the executive's decision for the current epoch now)
  tools        the record's can.tools section (authorised model tools)
  capabilities the record's can.capabilities and resources sections (models loaded, memory level, audio)
With a component off the describer says "cannot say" for the probes that section answers, so the ablation shows what
each section adds. Every answer names its source as "self" (one surface).

consistency: each record is also checked against the existing surfaces at the same state_version
(self_description.consistency); the describer keeps the results in `checks` for scripts/bench_workspace.py.
"""
from __future__ import annotations

from ... import self_description
from .. import cognition

COMPONENTS = ("uncertainty", "decisions", "tools", "capabilities")
# probe -> (component it needs or None, dotted field, key path inside the value)
PROBES = {
    "running_tasks": (None, "doing.tasks", ("running",)),
    "queued_tasks": (None, "doing.tasks", ("queued",)),
    "open_owner_questions": (None, "doing.questions", ("open",)),
    "autonomy_enabled": (None, "can.limits.autonomy", ("enabled",)),
    "autonomous_searches_left": (None, "can.limits.autonomy", ("remaining", "autonomous", "searches")),
    "memory_level": ("capabilities", "resources", ("level",)),
    "writes_allowed": (None, "can.limits.writes", ("allowed",)),
    "audio_active": ("capabilities", "resources", ("audio_active",)),
    "background_research_allowed": (None, "can.limits.background_research", ("allowed",)),
    "local_model_unloaded": ("capabilities", "can.capabilities.models", ("local_services", "vllm", "unloaded")),
    "uncertain_about": ("uncertainty", "uncertain.beliefs", ("least_certain",)),
    "last_decision": ("decisions", "decisions.executive_now", ("decision",)),
    "journal_row_limit": (None, "can.limits.journal", ("max_evidence_rows",)),
    "authorised_model_tools": ("tools", "can.tools", ("model_tools",)),
    "researching_now": (None, "doing.tasks", ("running",)),
}


class RecordDescriber:
    def __init__(self, ablate=frozenset()):
        self.ablate = frozenset(ablate)
        self.checks = []

    def surfaces(self, db, path, now):
        record = self_description.build(db, path, now)
        self.checks.append(self_description.consistency(db, path, record, now))
        return {"self": record}

    def describe(self, probe, surfaces):
        component, dotted, keys = PROBES[probe]
        if component in self.ablate:
            return None
        node = self_description.value(surfaces.get("self") or {}, dotted)
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        if node is None:
            return None
        if probe == "authorised_model_tools":
            return sorted(t["name"] for t in node if t.get("granted_by_default"))
        if probe == "researching_now":
            return node > 0
        if probe == "autonomous_searches_left":
            return int(node)
        if probe == "local_model_unloaded":
            return bool(node)
        return node

    def source(self, probe):
        return "self"


def make(ablate):
    return lambda: RecordDescriber(ablate)


cognition.register("self_model", "self_description", make, components=COMPONENTS,
                   description="lane W: one self-description record (self_description.build) answers every probe")
