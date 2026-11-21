"""Resource scheduling: measure memory/GPU/container use, derive a pressure level and apply the audio-priority,
memory-pressure and idle policies (RD-46; docs/architecture/RESOURCE_SCHEDULING.md).

status(db_path, full=True) is what `nsf resources` and GET /api/resources show."""
from __future__ import annotations

from . import monitor, policy


def status(db_path=None, *, full: bool = True, cold_start: bool = False) -> dict:
    """The measured sample, the pressure level with its reasons and thresholds, and what each policy decides now.
    `cold_start` also reads each neuroscifi-* container's last measured start-to-ready time (docker logs)."""
    s = monitor.sample(full=full)
    gate = policy.background_gate(db_path, sample=s)
    engine_ok, engine_why = policy.engine_gate("browser rendering", sample=s)
    out = {"level": s["level"], "reasons": s["reasons"], "thresholds": s["thresholds"], "measured_at": s["at"],
           "memory": s["memory"], "gpu": s["gpu"], "containers": s["containers"],
           "audio_active": gate["audio"],
           "policy": {"background_research": {"allowed": gate["allowed"], "reason": gate["reason"]},
                      "browser_rendering": {"allowed": engine_ok, "reason": engine_why},
                      "background_model_slots": policy.model_slots(db_path),
                      "idle_unload": {"automatic": False,
                                      "voice": "owner-run: deploy/voice/run.sh stop all (and start all; see "
                                               "docs/architecture/RESOURCE_SCHEDULING.md)",
                                      "vllm": "never unloaded by NeuroSciFi"}}}
    if cold_start:
        out["cold_starts"] = monitor.cold_starts()
    return out


__all__ = ["monitor", "policy", "status"]
