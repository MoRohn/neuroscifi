"""The cognitive benchmark (Project Cognitive Genesis, lane Z): a reproducible harness that later mechanisms plug into,
and the baseline of the unchanged system. docs/quality/COGNITIVE_BENCHMARK.md says what each family measures and
what it cannot show; scripts/bench_cognition.py is the command line.

Families (each module's docstring has its design, its interface and its scores):
  anticipation     anticipation.py   regulator     decide(history) -> {"background": "allow" | "hold"}
  info_value       infovalue.py      chooser       choose(view) -> subject id | None
  counterfactual   counterfactual.py answerer      answer(query) -> {"value": float | None, "sd": float | None}
  self_model       selfmodel.py      describer     describe(probe, surfaces) -> value | None [+ surfaces(db, path, now)]
  selective        selective.py      confidence    confidence(item) -> float | None  [+ probability(item)]
  retention        retention.py      (the mechanism switched on: activate())
  adversarial      adversarial.py    (the mechanism switched on: activate())
  withheld         withheld.py       the four interfaces above on worlds no lane may tune on; needs final=True

Registering a mechanism (no edit of any scoring code):

    from neuroscifi.acceptance import cognition

    def make(ablate: frozenset[str]):          # the components switched OFF for an ablation run
        return lambda: MyRegulator(use_trend="trend" not in ablate, use_audio="audio_pattern" not in ablate)

    cognition.register("anticipation", "predictive", make, components=("trend", "audio_pattern"),
                       description="forecast-based holds inside the existing gate")

  - `make(ablate)` returns a zero-argument callable that builds a FRESH agent; the harness calls it once per world
    and seed. The agent implements the family's interface above. Any agent may also provide `activate()`, a
    context manager that switches the mechanism on in the running system for the duration of the family (a setting,
    a flag): retention and adversarial use only that.
  - Register for as many families as the mechanism affects, under the same name; `--mechanism NAME` runs every
    selected family with it and falls back to the baseline (and says so) where it is not registered.
  - Put the register() calls in a module and either append its dotted name to PLUGINS below (one line) or pass
    `--plugin dotted.module` on the command line.
  - An ablation run is `--mechanism NAME --ablate trend` (comma-separated components); an unknown component is an
    error, never silently the full mechanism. A gain counts only when the full mechanism beats the baseline on the
    HELD-OUT split, the ablation removes it, retention holds and the adversarial cases do not regress; the withheld
    family is run once, with --final, at the end.
  - needs_model=True marks a mechanism that calls the answer model; the harness counts every model call
    (inference.gateway.Gateway.chat / embed / rerank) in each family's cost either way.

Every run is offline: connections to anything but loopback are refused (and counted) while a family runs, and the
cost of each family (wall seconds, peak traced Python memory, model calls) is recorded beside its result. Results are
deterministic per seed apart from the cost.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import tracemalloc
from typing import Callable

from . import splits, withheld

FAMILIES = ("anticipation", "info_value", "counterfactual", "self_model", "selective", "retention", "adversarial",
            "withheld")
DEFAULT_FAMILIES = FAMILIES[:-1]
PLUGINS: tuple[str, ...] = ()        # dotted module names whose import registers mechanisms (later lanes append)
REPO = Path(__file__).resolve().parents[4]
LEDGER = REPO / "docs" / "quality" / "evidence" / "genesis_final_uses.jsonl"
BASELINE = REPO / "docs" / "quality" / "evidence" / "genesis_baseline.json"
LABEL = ("cognitive benchmark: synthetic, fictional worlds driven through the real modules; offline (loopback only); "
         "no answer model; simulated, not a measurement of the DGX Spark or of the open web")


class Refused(Exception):
    """A run the harness will not do (the withheld family without final=True, an unknown ablation)."""


@dataclass(frozen=True)
class Mechanism:
    family: str
    name: str
    factory: Callable
    components: tuple[str, ...] = ()
    description: str = ""
    needs_model: bool = False
    reference: bool = False


_REGISTRY: dict[tuple[str, str], Mechanism] = {}


def register(family: str, name: str, factory: Callable, *, components=(), description: str = "",
             needs_model: bool = False, reference: bool = False) -> Mechanism:
    """Add a mechanism for one family (see the module docstring). Re-registering a name replaces it."""
    if family not in FAMILIES or family == "withheld":
        raise ValueError(f"unknown family {family!r} (one of {', '.join(DEFAULT_FAMILIES)})")
    mech = Mechanism(family, name, factory, tuple(components), description, needs_model, reference)
    _REGISTRY[(family, name)] = mech
    return mech


def mechanisms() -> list[Mechanism]:
    return sorted(_REGISTRY.values(), key=lambda m: (FAMILIES.index(m.family), m.name))


def load_plugins(names=()) -> list[str]:
    loaded = []
    for name in (*PLUGINS, *names):
        importlib.import_module(name)
        loaded.append(name)
    return loaded


# ---- baselines and references ----------------------------------------------------------------------------------------

class _Inert:
    """The baseline of retention and adversarial: nothing is switched on."""

    def activate(self):
        return nullcontext()


def _register_baselines():
    from . import anticipation, counterfactual, infovalue, selective, selfmodel
    register("anticipation", "baseline", lambda ablate: anticipation.ReactiveRegulator,
             description="today's reactive gate: resources.policy.background_gate (resources/policy.py:134-145)")
    register("anticipation", "open", lambda ablate: anticipation.OpenRegulator, reference=True,
             description="reference: never adds a hold (the plant's gate still applies)")
    register("info_value", "baseline", lambda ablate: infovalue.BaselineChooser,
             description="executive rule order (core) and curiosity.rank, the timer's path (web)")
    register("info_value", "autonomy", lambda ablate: (lambda: infovalue.BaselineChooser("autonomy")),
             reference=True, description="executive (core) and autonomy.candidates, the idle investigator (web)")
    register("info_value", "oracle", lambda ablate: "oracle", reference=True,
             description="reference bound: greatest known gain per cost; knows which subjects are noise")
    register("info_value", "random", lambda ablate: infovalue.RandomChooser, reference=True,
             description="reference: uniform choice among the subjects")
    register("counterfactual", "baseline", lambda ablate: counterfactual.TodayAnswerer,
             description="the active graph's one-step prediction; nothing else can be answered today")
    register("self_model", "baseline", lambda ablate: selfmodel.TodayDescriber,
             description="today's surfaces: regulation, inventory, resources.status, presence, autonomy.status")
    register("selective", "baseline", lambda ablate: selective.TodayConfidence,
             description="the verdict's and the top quote's confidence, as the pipeline exposes them")
    register("retention", "baseline", lambda ablate: _Inert, description="nothing switched on")
    register("adversarial", "baseline", lambda ablate: _Inert, description="nothing switched on")


_register_baselines()


# ---- guards: offline, cost -------------------------------------------------------------------------------------------

def _loopback(host) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@contextmanager
def _guarded(cost: dict, *, trace_memory: bool = False):
    """No connection outside loopback (refused and counted), model calls counted, resource-threshold environment
    overrides set aside (so the code's own thresholds apply), and the cost measured: wall seconds, the process's peak
    resident set after the family and how much it rose during it (ru_maxrss: process-wide, so it includes every
    earlier family of the run), and with trace_memory the peak of traced Python allocations (tracemalloc; it slows
    the run several times, so it is off by default)."""
    import resource
    from ...inference import gateway
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            cost["refused_connections"] += 1
            raise OSError(f"cognitive benchmark: offline, connection to {address[0]} refused")

    def connect(sock, address):
        check(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return original_connect_ex(sock, address)

    counted = {}
    for name in ("chat", "embed", "rerank"):
        method = getattr(gateway.Gateway, name)
        counted[name] = method

        def wrapper(self, *a, __m=method, **k):
            cost["model_calls"] += 1
            return __m(self, *a, **k)
        setattr(gateway.Gateway, name, wrapper)
    saved = {k: os.environ.pop(k) for k in ("NSF_RESOURCE_ELEVATED_GIB", "NSF_RESOURCE_CRITICAL_GIB")
             if k in os.environ}
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    tracing = tracemalloc.is_tracing()
    if trace_memory:
        if not tracing:
            tracemalloc.start()
        tracemalloc.reset_peak()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    started = time.perf_counter()
    try:
        yield
    finally:
        cost["wall_seconds"] = round(time.perf_counter() - started, 3)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        cost["peak_rss_bytes"], cost["peak_rss_rise_bytes"] = rss, rss - rss_before
        if trace_memory:
            cost["peak_traced_python_bytes"] = tracemalloc.get_traced_memory()[1]
            if not tracing:
                tracemalloc.stop()
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex
        for name, method in counted.items():
            setattr(gateway.Gateway, name, method)
        os.environ.update(saved)


# ---- running ---------------------------------------------------------------------------------------------------------

def _resolve(family: str, name: str) -> tuple[Mechanism, bool]:
    if (family, name) in _REGISTRY:
        return _REGISTRY[(family, name)], False
    return _REGISTRY[(family, "baseline")], True


def _maker(mech: Mechanism, ablate: frozenset):
    unknown = sorted(ablate - set(mech.components))
    if unknown:
        raise Refused(f"{mech.family}/{mech.name} has no component(s) {', '.join(unknown)} "
                      f"(components: {', '.join(mech.components) or 'none'})")
    return mech.factory(frozenset(ablate))


def _activation(make):
    if make == "oracle" or not callable(make):
        return nullcontext()
    agent = make()
    return agent.activate() if hasattr(agent, "activate") else nullcontext()


def run_family(family: str, split: str, make, *, seeds=None, quick: bool = False, baseline_values=None) -> dict:
    from . import adversarial, anticipation, counterfactual, infovalue, retention, selective, selfmodel
    if family == "anticipation":
        return anticipation.run(splits.ANTICIPATION[split], make, seeds=seeds)
    if family == "info_value":
        return infovalue.run(splits.INFO_VALUE[split], make, seeds=seeds)
    if family == "counterfactual":
        return counterfactual.run(splits.COUNTERFACTUAL[split], make, seeds=seeds)
    if family == "self_model":
        return selfmodel.run(splits.SELF_MODEL[split], make, seeds=seeds)
    if family == "selective":
        return selective.run(splits.SELECTIVE[split], make, quick=quick)
    if family == "retention":
        return retention.run(splits.RETENTION, baseline=baseline_values, quick=quick)
    if family == "adversarial":
        return adversarial.run()
    raise ValueError(family)


def _withheld(makers: dict, seeds=None) -> dict:
    from . import anticipation, counterfactual, infovalue, selfmodel
    w = withheld.WITHHELD
    seeds = seeds or w["seeds"]
    return {"anticipation": anticipation.evaluate(w["anticipation"], seeds, makers["anticipation"]),
            "info_value": infovalue.run({"seeds": seeds, **w["info_value"]}, makers["info_value"]),
            "counterfactual": counterfactual.run({"seeds": seeds, **w["counterfactual"]}, makers["counterfactual"]),
            "self_model": selfmodel.run({"seeds": seeds, "states": w["self_model"]}, makers["self_model"])}


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def environment() -> dict:
    commit = dirty = None
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                                timeout=5).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True,
                                    timeout=5).stdout.strip()) if commit else None
    except (OSError, subprocess.SubprocessError):
        pass
    import numpy
    import sqlite3
    return {"git_commit": commit, "git_dirty": dirty, "python": sys.version.split()[0], "numpy": numpy.__version__,
            "sqlite": sqlite3.sqlite_version, "platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "splits_sha256": digest(Path(splits.__file__)), "withheld_sha256": digest(Path(withheld.__file__))}


def _record_final(ledger: Path, entry: dict) -> list[dict]:
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(families=DEFAULT_FAMILIES, *, split: str = "development", mechanism: str = "baseline", ablate=(),
        seeds=None, quick: bool = False, final: bool = False, ledger: Path | None = None,
        baseline_output: dict | None = None, note: str = "", trace_memory: bool = False) -> dict:
    """Run the families for one mechanism (and ablation) on one split. See the module docstring. A final run is
    written to the ledger before it starts, so a run that crashes still counts as a look."""
    if split not in ("development", "held_out"):
        raise ValueError("split must be 'development' or 'held_out'")
    families = list(families)
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        raise ValueError(f"unknown family {', '.join(unknown)}")
    if "withheld" in families and not final:
        raise Refused("the withheld family is reported only with final=True (--final); every such run is recorded "
                      "in the ledger. Do not look at it while developing.")
    ablate = frozenset(a for a in ablate if a)
    env = environment()
    out = {"benchmark": "cognition", "version": 1, "label": LABEL,
           "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "environment": env,
           "split": split, "mechanism": mechanism, "ablate": sorted(ablate), "seeds_override": seeds,
           "quick": quick, "families": {}}
    if final:
        entry = {"at": out["generated_at"], "commit": env["git_commit"], "dirty": env["git_dirty"],
                 "mechanism": mechanism, "ablate": sorted(ablate), "families": families, "note": note}
        out["final_runs"] = _record_final(ledger or LEDGER, entry)
    from . import retention
    baseline_values = retention.recorded(baseline_output)
    makers, fallbacks = {}, {}
    for family in [f for f in families if f != "withheld"] + (
            ["anticipation", "info_value", "counterfactual", "self_model"] if "withheld" in families else []):
        mech, fell_back = _resolve(family, mechanism)
        makers[family] = _maker(mech, ablate if not fell_back else frozenset())
        fallbacks[family] = {"used": mech.name, "fell_back_to_baseline": fell_back,
                             "needs_model": mech.needs_model, "reference": mech.reference}
    for family in families:
        cost = {"wall_seconds": None, "model_calls": 0, "refused_connections": 0}
        if family == "withheld":
            with _guarded(cost, trace_memory=trace_memory):
                result = _withheld(makers, seeds)
            out["families"][family] = {"mechanisms": {f: fallbacks[f] for f in
                                                      ("anticipation", "info_value", "counterfactual", "self_model")},
                                       "result": result, "cost": cost}
            continue
        with _guarded(cost, trace_memory=trace_memory), _activation(makers[family]):
            result = run_family(family, split, makers[family], seeds=seeds, quick=quick,
                                baseline_values=baseline_values)
        out["families"][family] = {"mechanism": fallbacks[family], "result": result, "cost": cost}
    from .scoring import rounded
    return rounded(out)


# ---- comparing two outputs -------------------------------------------------------------------------------------------

HEADLINES = {
    "anticipation": [("summary.breach_ticks_all_channels.mean", False), ("summary.memory.breach_ticks.mean", False),
                     ("summary.memory.mean_lead_ticks.mean", True), ("summary.false_alarm_share.mean", False),
                     ("summary.work_given_up_share.mean", False)],
    "info_value": [("core_mean.realised_bits_per_budget", True), ("core_mean.noise_share", False),
                   ("web_mean.realised_bits_per_budget", True), ("web_mean.noise_share", False),
                   ("web_mean.stale_share", True)],
    "counterfactual": [("summary.coverage.mean", True), ("summary.mean_relative_error.mean", False),
                       ("summary.interval_coverage.mean", True), ("summary.abstention_correct.mean", True)],
    "self_model": [("summary.coverage.mean", True), ("summary.accuracy.mean", True),
                   ("summary.surfaces_used.mean", False)],
    "selective": [("selective.aurc", False), ("selective.coverage_at_risk_0.05", True),
                  ("claim_probability.murphy.brier", False), ("claim_probability.ece_10_bins", False)],
    "retention": [("all_scenarios_pass", True), ("all_retained", True)],
    "adversarial": [("passed", True)],
}


def compare(before: dict, after: dict) -> dict:
    """Headline metrics of two outputs, family by family: {metric: {"before", "after", "better"}}."""
    out = {}
    for family, metrics in HEADLINES.items():
        a = (before.get("families") or {}).get(family)
        b = (after.get("families") or {}).get(family)
        if not a or not b:
            continue
        rows = {}
        for dotted, higher in metrics:
            x, y = _lookup(a["result"], dotted), _lookup(b["result"], dotted)
            better = None
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x != y:
                better = (y > x) if higher else (y < x)
            rows[dotted] = {"before": x, "after": y, "better": better}
        out[family] = rows
    return out


def _lookup(tree, dotted):
    """Value at a dotted path whose keys may themselves contain dots (greedy: the longest key that exists)."""
    parts = dotted.split(".")
    node = tree
    i = 0
    while i < len(parts):
        if not isinstance(node, dict):
            return None
        for j in range(len(parts), i, -1):
            key = ".".join(parts[i:j])
            if key in node:
                node, i = node[key], j
                break
        else:
            return None
    return node


__all__ = ["BASELINE", "DEFAULT_FAMILIES", "FAMILIES", "LEDGER", "Mechanism", "PLUGINS", "Refused", "compare",
           "load_plugins", "mechanisms", "register", "run"]
