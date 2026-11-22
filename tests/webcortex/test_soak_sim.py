"""The accelerated autonomy soak (scripts/soak_sim.py) over three simulated days: the real scheduler, worker and
SQLite with an injected clock, a provider outage, a daily worker restart and a crash in the middle of a task."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_three_simulated_days_stay_inside_the_budgets_with_no_stuck_leases(tmp_path):
    spec = importlib.util.spec_from_file_location("soak_sim", ROOT / "scripts" / "soak_sim.py")
    sim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sim)
    report = sim.run(days=3, db_dir=tmp_path)
    assert report["checks"] == {name: "ok" for name in ("budget", "reset", "drift", "growth", "leases", "decisions")}
    days = report["days"]
    assert all(b["autonomous"].get("searches", 0) <= 20 and b["autonomous"].get("pages", 0) <= 60
               for b in days.values())
    assert report["faults"]["worker_crash"] == 1 and report["faults"]["provider_search_failures"] > 0
    assert report["worker_starts"] >= 4
