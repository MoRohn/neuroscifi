"""Resource scheduling: measure memory/GPU/container use, derive a pressure level and apply the audio-priority,
memory-pressure and (opt-in) idle-unload policies (RD-46; docs/architecture/RESOURCE_SCHEDULING.md).

status(db_path, full=True) is what `nsf resources` and GET /api/resources show."""
from __future__ import annotations

from contextlib import closing
import ipaddress
import sqlite3
from urllib.parse import urlsplit

from . import idle, monitor, policy


def inference_endpoint(db_path) -> tuple[str | None, str | None, str | None]:
    """(metrics URL, API key, None) of the instance's configured inference server, or (None, None, why not). The
    key comes from the gateway's own key file (inference.config.read_key); it is only ever sent to a loopback
    server, and never logged."""
    if not db_path:
        return None, None, "no instance database, so the inference endpoint is not known"
    from ..inference import config as iconfig
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)) as db:
            cfg = iconfig.get_config(db)
    except sqlite3.Error as error:
        return None, None, f"the instance database cannot be read ({error})"
    if not cfg.configured:
        return None, None, "the inference endpoint is not configured (nsf llm config --url ...)"
    parts = urlsplit(cfg.base_url)
    host = (parts.hostname or "").lower()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return None, None, f"the inference endpoint {parts.scheme}://{parts.netloc} is not on this host; its server " \
                           "state is not measured here"
    try:
        key = iconfig.read_key(cfg)
    except Exception:          # e.g. a key file readable by everyone: refused there, not sent here
        key = None
    return f"{parts.scheme}://{parts.netloc}/metrics", key, None


def _inference(db_path) -> dict:
    url, key, why = inference_endpoint(db_path)
    if url is None:
        return {"available": False, "reason": why}
    out = monitor.inference_cached(url, key)
    if not out["available"] and db_path and idle.Idle(db_path).unloaded_by_policy("vllm"):
        out = out | {"reason": out["reason"] + "; the idle policy unloaded the local model, the next request starts it"}
    return out


def status(db_path=None, *, full: bool = True, cold_start: bool = False) -> dict:
    """The measured sample, the pressure level with its reasons and thresholds, and what each policy decides now.
    `full` adds the host load, the GPU (temperature, power and throttling included), the containers and the
    inference server's state (/metrics, cached 15 s). `cold_start` also reads each neuroscifi-* container's last
    measured start-to-ready time (docker logs)."""
    s = monitor.sample(full=full)
    gate = policy.background_gate(db_path, sample=s, record=False)      # a display: no shadow verdict (ADR-058)
    engine_ok, engine_why = policy.engine_gate("browser rendering", sample=s)
    out = {"level": s["level"], "reasons": s["reasons"], "thresholds": s["thresholds"], "measured_at": s["at"],
           "memory": s["memory"], "load": s["load"], "gpu": s["gpu"], "containers": s["containers"],
           "inference": _inference(db_path) if full else None,
           "audio_active": gate["audio"],
           "policy": {"background_research": {"allowed": gate["allowed"], "reason": gate["reason"]},
                      "browser_rendering": {"allowed": engine_ok, "reason": engine_why},
                      "background_model_slots": policy.model_slots(db_path),
                      "idle_unload": idle.Idle(db_path).status(s["containers"]) if db_path else
                      {"automatic": False}}}
    if cold_start:
        out["cold_starts"] = monitor.cold_starts()
    return out


__all__ = ["idle", "inference_endpoint", "monitor", "policy", "status"]
