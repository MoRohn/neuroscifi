"""Family 6a, retention: does everything the system could do before still work, as well, after a mechanism is added?

The frozen suite (splits.RETENTION): acceptance scenarios at the reduced sizes tests/acceptance uses (each returns
its own pass/fail checks and metrics; acceptance/scenarios.py) and key metrics of the offline Web Cortex benchmark
(the same run selective.py keeps). A mechanism is switched on for the whole suite through its optional
`activate()` context manager, so the suite measures the system WITH the mechanism.

Scores: every scenario passes; every offline metric is retained = at least the baseline value recorded in
docs/quality/evidence/genesis_baseline.json minus its tolerance (all frozen at 0: no loss is accepted). Without a
recorded baseline (the first run, which produces it) "retained" is None. What it cannot show: retention of
capabilities that no test or benchmark exercises; the scenarios are simulated (their own label says so).
"""
from __future__ import annotations

from .. import scenarios
from . import selective


def _at(tree, path):
    node = tree
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if isinstance(node, bool):
        return 1.0 if node else 0.0
    return node


def recorded(baseline_output: dict | None) -> dict | None:
    """{metric path: value} from a harness output that ran this family, or from the recorded baseline file (its
    development run), else None."""
    if isinstance(baseline_output, dict) and "development" in baseline_output:
        baseline_output = baseline_output["development"]
    try:
        rows = baseline_output["families"]["retention"]["result"]["offline_metrics"]
    except (KeyError, TypeError):
        return None
    return {path: row["value"] for path, row in rows.items()}


def run(spec: dict, *, baseline: dict | None = None, quick: bool = False) -> dict:
    """baseline: {metric path: value} (recorded(...) of the baseline output), or None."""
    out_scenarios = {}
    for item in spec["scenarios"]:
        result = getattr(scenarios, item["name"])(**item["kwargs"])
        out_scenarios[item["name"]] = {"pass": result["pass"],
                                       "failed_checks": sorted(k for k, v in result["checks"].items() if not v)}
    metrics = selective.corpus_run(quick=quick)["metrics"]
    offline = {}
    for m in spec["offline_metrics"]:
        value = _at(metrics, m["path"])
        before = baseline.get(m["path"]) if baseline is not None else None
        retained = None
        if before is not None and value is not None:
            retained = value >= before - m["tolerance"] if m["higher_is_better"] else value <= before + m["tolerance"]
        offline[m["path"]] = {"value": value, "baseline": before, "retained": retained}
    checks = [v["pass"] for v in out_scenarios.values()] + [v["retained"] for v in offline.values()
                                                            if v["retained"] is not None]
    return {"scenarios": out_scenarios, "offline_metrics": offline, "quick_offline_run": quick,
            "all_scenarios_pass": all(v["pass"] for v in out_scenarios.values()),
            "all_retained": all(checks) if baseline is not None else None}
