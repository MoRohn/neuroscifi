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
PLUGINS: tuple[str, ...] = (       # dotted module names whose import registers mechanisms (later lanes append)
    "neuroscifi.acceptance.cognition.instinct",
    "neuroscifi.acceptance.cognition.imagination",
    "neuroscifi.acceptance.cognition.knowledge",
    "neuroscifi.acceptance.cognition.monitoring",
    "neuroscifi.acceptance.cognition.selfrecord",)
REPO = Path(__file__).resolve().parents[4]
LEDGER_NAME = "docs/quality/evidence/genesis_final_uses.jsonl"
LEDGER = REPO / LEDGER_NAME
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
    register("counterfactual", "zero", lambda ablate: counterfactual.ZeroAnswerer, reference=True,
             description="reference: 0 with sd 1e6 for everything (the Wave 1 review's trivial mechanism)")
    register("self_model", "baseline", lambda ablate: selfmodel.TodayDescriber,
             description="today's surfaces: regulation, inventory, resources.status, presence, autonomy.status")
    register("selective", "baseline", lambda ablate: selective.TodayConfidence,
             description="the verdict's and the top quote's confidence, as the pipeline exposes them")
    register("selective", "verdict_word", lambda ablate: selective.VerdictWord, reference=True,
             description="reference: P(claim true) 0.95 / 0.05 / 0.5 from the verdict's state word alone (Wave 1 "
                         "review B5), constant answer confidence")
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
    # the baseline switches nothing on: it reads the cached corpus run, as the recorded baseline costs did (review0b N9)
    return _switched_on(agent) if hasattr(agent, "activate") and not isinstance(agent, _Inert) else nullcontext()


@contextmanager
def _switched_on(agent):
    """agent.activate() for one family, with the offline corpus run that selective and retention share (cached per
    process) dropped on the way in and out: a run cached without the mechanism is never read with it on, nor one made
    with it read by a later family (review0 M3: retention under a mechanism read the baseline's run)."""
    from . import selective
    selective._CACHE.clear()
    try:
        with agent.activate():
            yield
    finally:
        selective._CACHE.clear()


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
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=5)
        commit = (head.stdout.strip() or None) if head.returncode == 0 else None
        # the ledger of final runs is a record, not code: a run's own line (appended before it scores) must not make
        # the next run of the same look read as dirty (review1c)
        if commit:
            status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True,
                                    timeout=5)
            # review3 0-1: clean only when git itself said so (a status that fails or times out is not "clean")
            dirty = True if status.returncode != 0 else any(line[3:].strip('"') != LEDGER_NAME
                                                            for line in status.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        dirty = True if commit else None
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
        seeds=None, quick: bool = False, final: bool = False, baseline_output: dict | None = None, note: str = "",
        trace_memory: bool = False) -> dict:
    """Run the families for one mechanism (and ablation) on one split. See the module docstring. A final run is
    written to the repository's ledger (LEDGER; there is no other) before it starts, so a run that crashes still
    counts as a look; the protocol (docs/quality/COGNITIVE_BENCHMARK.md) commits that line with the result."""
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
        # review3 (Q7): a look is taken on a clean, committed tree only, as cycles.final and bench_wave1.final require
        if env["git_dirty"] is not False or not env["git_commit"]:
            raise Refused("a final run needs a clean, committed tree (and git must say so), so the ledger line names "
                          "the code it measured")
        entry = {"at": out["generated_at"], "commit": env["git_commit"], "dirty": env["git_dirty"],
                 "mechanism": mechanism, "ablate": sorted(ablate), "families": families, "note": note}
        out["final_runs"] = _record_final(LEDGER, entry)
    from . import retention
    baseline_values = retention.recorded(baseline_output, quick=quick)
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


# ---- the supplementary wide-seeds run (Wave 1 review B1, B2, B6) -------------------------------------------------------

# Twelve further seeds (the ones the Wave 1 review used). SUPPLEMENTARY: the frozen split's own three seeds stay the
# official numbers; this run is reported beside them, never instead. Disjoint from every split's and the withheld
# family's seeds (tests/acceptance/test_cognition_bench.py checks it).
WIDE_SEEDS = tuple(range(5001, 5013))
SEEDED_FAMILIES = ("anticipation", "info_value", "counterfactual", "self_model")    # the families a seed changes


def wide_seeds(family: str, split: str, mechanism: str, *, reference: str = "baseline", seeds=WIDE_SEEDS,
               ablate=(), contexts=None) -> dict:
    """The family on `split`'s worlds once per seed in `seeds`, for `mechanism` and for `reference`; per HEADLINES
    metric: each one's mean and per-seed standard deviation, and the paired difference (mechanism - reference, seed by
    seed) with how many seeds it was better, worse or equal in. `contexts` = (mechanism's, reference's) zero-argument
    context-manager factories (a settings regime), default none. The withheld family is never run here."""
    if family not in SEEDED_FAMILIES:
        raise ValueError(f"{family} has no seeds (one of {', '.join(SEEDED_FAMILIES)})")
    from .scoring import spread
    make_m, make_r = contexts or (nullcontext, nullcontext)
    values: dict = {"mechanism": {}, "reference": {}}
    for seed in seeds:
        for side, name, ctx, abl in (("mechanism", mechanism, make_m, ablate), ("reference", reference, make_r, ())):
            with ctx():
                result = run([family], split=split, mechanism=name, ablate=abl, seeds=(seed,))
            values[side][seed] = result["families"][family]["result"]
    metrics = {}
    for dotted, higher in HEADLINES[family]:
        m = [headline_value(values["mechanism"][s], dotted) for s in seeds]
        r = [headline_value(values["reference"][s], dotted) for s in seeds]
        pairs = [(x, y) for x, y in zip(m, r) if isinstance(x, (int, float)) and isinstance(y, (int, float))]
        diffs = [x - y for x, y in pairs]
        row = {"mechanism": spread(m), "reference": spread(r), "paired_difference": spread(diffs),
               "per_seed": {str(s): {"mechanism": x, "reference": y} for s, x, y in zip(seeds, m, r)}}
        if higher is not None:
            row["seeds_better"] = sum(1 for d in diffs if (d > 0 if higher else d < 0))
            row["seeds_worse"] = sum(1 for d in diffs if (d < 0 if higher else d > 0))
            row["seeds_equal"] = sum(1 for d in diffs if d == 0)
        metrics[dotted] = row
    from .scoring import rounded
    return rounded({"supplementary": "wide seeds: reported beside the split's own seeds, which stay the official "
                                     "numbers", "family": family, "split": split, "mechanism": mechanism,
                    "reference": reference, "ablate": sorted(ablate), "seeds": list(seeds), "metrics": metrics})


# ---- comparing two outputs -------------------------------------------------------------------------------------------

# (dotted metric, higher is better; None = reported, never "better" or "worse")
HEADLINES = {
    "anticipation": [("summary.breach_ticks_all_channels.mean", False), ("summary.memory.breach_ticks.mean", False),
                     ("summary.memory.avoided_share.mean", True),
                     ("summary.memory.mean_lead_ticks.mean", True), ("summary.false_alarm_share.mean", False),
                     ("summary.work_given_up_share.mean", False)],
    "info_value": [("core_mean.realised_bits_per_budget", True), ("core_mean.noise_share", False),
                   ("web_mean.realised_bits_per_budget", True), ("web_mean.noise_share", False),
                   ("web_mean.stale_share", True)],
    # the interval score is over the questions each answerer answered: comparable only between answerers that
    # answered the same ones (answered_share beside it), so it is never called better or worse here
    # `summary` pools the groups (counterfactual.POOLED: reported, not a claim); a decline of an answerable question,
    # front door included, is not within tolerance. The strict tolerance and the largest error are always beside it.
    "counterfactual": [("summary.answered_within_tolerance.mean", True),
                       ("summary.answered_within_strict_tolerance.mean", True), ("summary.interval_score.mean", None),
                       ("summary.answered_within_tolerance_without_front_door.mean", None),
                       ("summary_by_group.handed_equations.answered_within_tolerance.mean", True),
                       ("summary_by_group.belief_what_if.answered_within_tolerance.mean", True),
                       ("summary_by_group.learned_graph.answered_within_tolerance.mean", True),
                       ("summary_by_group.learned_graph.answered_within_strict_tolerance.mean", True),
                       ("summary_by_group.learned_graph.max_relative_error.max", None),
                       ("summary_by_group.learned_graph.interval_score.mean", None),
                       ("summary_by_group.front_door.front_door_declined.mean", None),
                       ("summary_by_group.front_door.front_door_within_tolerance.mean", True),
                       ("summary.answered_share.mean", None), ("summary.mean_relative_error.mean", False),
                       ("summary.interval_coverage.mean", None), ("summary.declined_confounded_share.mean", None)],
    "self_model": [("summary.coverage.mean", True), ("summary.accuracy.mean", True),
                   ("summary_original_key.accuracy.mean", True), ("summary_without_plumbing.accuracy.mean", True),
                   ("summary.surfaces_used.mean", False)],
    "selective": [("selective.aurc", False), ("selective.coverage_at_risk_0.05", True),
                  ("claim_probability.items", None), ("claim_probability.murphy.brier", False),
                  ("claim_probability.murphy.reliability", False), ("claim_probability.murphy.resolution", True)],
    "retention": [("all_scenarios_pass", True), ("all_retained", True)],
    "adversarial": [("passed", True), ("by_origin.original.passed", True), ("by_origin.added_wave1.passed", True),
                    ("by_origin.restored.passed", True)],
}
# Metrics renamed by the Wave 1 review (B3) so the old meaning cannot be mistaken for the headline: an output written
# before the rename (the recorded baseline) is read under its old name.
RENAMED = {"summary.answered_share.mean": "summary.coverage.mean",
           "summary.declined_confounded_share.mean": "summary.abstention_correct.mean"}


def headline_value(result, dotted):
    """A HEADLINES metric of one family result; an output recorded before a rename is read under the old name."""
    value = _lookup(result, dotted)
    if value is None and dotted in RENAMED:
        value = _lookup(result, RENAMED[dotted])
    return value


def compare(before: dict, after: dict) -> dict:
    """Headline metrics of two outputs, family by family: {metric: {"before", "after", "better"}}. `before` may be
    the recorded baseline file: its run of the same split as `after` is used."""
    if "families" not in before:
        before = before.get(after.get("split", "development")) or {}
    out = {}
    for family, metrics in HEADLINES.items():
        a = (before.get("families") or {}).get(family)
        b = (after.get("families") or {}).get(family)
        if not a or not b:
            continue
        rows = {}
        for dotted, higher in metrics:
            x, y = headline_value(a["result"], dotted), headline_value(b["result"], dotted)
            better = None
            if higher is not None and isinstance(x, (int, float)) and isinstance(y, (int, float)) and x != y:
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


__all__ = ["BASELINE", "DEFAULT_FAMILIES", "FAMILIES", "LEDGER", "Mechanism", "PLUGINS", "Refused",
           "SEEDED_FAMILIES", "WIDE_SEEDS", "compare", "headline_value", "load_plugins", "mechanisms", "register",
           "run", "wide_seeds"]
