"""scripts/hardened_run.py (a unit's sandbox as systemd-run properties), deploy/systemd/hardening_probe.py and
scripts/soak_report.py on a fresh instance."""
import importlib.util
import json
from pathlib import Path

from neuroscifi.instance import store

ROOT = Path(__file__).resolve().parent.parent


def load(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hardened_run_passes_the_units_sandbox_and_adapts_only_paths_for_a_user_manager():
    run = load("scripts/hardened_run.py")
    unit = ROOT / "deploy" / "systemd" / "neuroscifi-webcortex.service"
    system = run.properties(unit, user=False)
    user = run.properties(unit, user=True, bind_ro=["/venv"], bind_rw=["/state"])
    assert "ProtectHome=yes" in system and "ProtectHome=tmpfs" in user
    assert {"NoNewPrivileges=yes", "CapabilityBoundingSet=", "SystemCallFilter=@system-service",
            "MemoryDenyWriteExecute=yes", "IPAddressDeny=10.0.0.0/8", "IPAddressDeny=link-local",
            "IPAddressAllow=localhost", "MemoryMax=2G"} <= set(system)
    assert not any(p.startswith(("User=", "Group=", "ExecStart=", "ReadWritePaths=%S")) for p in system)
    assert user[-3:] == ["BindReadOnlyPaths=/venv", "BindPaths=/state", "ReadWritePaths=/state"]


def test_every_long_running_unit_drops_capabilities_and_filters_system_calls():
    run = load("scripts/hardened_run.py")
    for unit in sorted((ROOT / "deploy" / "systemd").glob("*.service")):
        props = set(run.properties(unit, user=False))
        assert {"NoNewPrivileges=yes", "CapabilityBoundingSet=", "SystemCallFilter=@system-service",
                "SystemCallArchitectures=native", "RestrictNamespaces=yes"} <= props, unit.name


def test_the_probe_reports_without_changing_anything(tmp_path):
    probe = load("deploy/systemd/hardening_probe.py").probe(rw=[str(tmp_path)])
    assert probe["write"][str(tmp_path)] == "allowed" and list(tmp_path.iterdir()) == []
    assert set(probe["status"]) >= {"CapBnd", "NoNewPrivs", "Seccomp"}


def test_soak_report_on_a_fresh_instance(tmp_path, capsys):
    report = load("scripts/soak_report.py")
    path = tmp_path / "i.sqlite"
    store.create(path, 1_795_200_000.0).close()
    samples = tmp_path / "samples.jsonl"
    assert report.main(["--db", str(path), "--samples", str(samples), "--append"]) == 0
    assert report.main(["--db", str(path), "--samples", str(samples), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checks"] == {"budget": "ok", "leases": "ok", "ledger": "ok", "decisions": "ok"}
    assert out["trend"]["samples"] == 1 and out["limits"]["searches"] == 20
