"""`nsf bench` output shape and `nsf doctor` checks."""
import argparse
import json

from neuroscifi.acceptance import bench, doctor
from neuroscifi.acceptance.cli import register
from neuroscifi.instance import store

T0 = 1_800_000_000.0


def _cli(argv):
    parser = argparse.ArgumentParser(prog="nsf")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.handler(args)


def _statuses(report):
    return {check["name"]: check["status"] for check in report["checks"]}


def test_bench_reports_percentiles_and_host(capsys):
    assert _cli(["bench", "--events", "20"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["simulated"] is True and out["events"] == 20 and "not Spark" in out["label"]
    for key in ("observe_ms", "decide_ms", "loop_ms"):
        stats = out[key]
        assert stats["n"] == 20 and 0 < stats["p50"] <= stats["p95"] <= stats["p99"] <= stats["max"]
    assert set(out["host"]) >= {"platform", "cpu_count", "python", "numpy"}
    assert sum(out["decisions"].values()) == 20


def test_percentiles_of_nothing_are_null():
    assert bench.percentiles([])["p50"] is None


def _instance(tmp_path):
    directory = tmp_path / "home"
    directory.mkdir(mode=0o700)
    path = directory / "i.sqlite"
    store.create(path, T0).close()
    return path


def test_doctor_passes_a_good_database(tmp_path, capsys):
    path = _instance(tmp_path)
    report = doctor.run(path)
    statuses = _statuses(report)
    assert not doctor.failed(report), report
    for name in ("python", "numpy", "zstandard", "sqlite", "json", "file_mode", "parent_directory", "instance",
                 "journal_mode", "schema_version", "integrity", "free_space", "filesystem", "encryption"):
        assert name in statuses
    for name in ("file_mode", "parent_directory", "journal_mode", "schema_version", "integrity"):
        assert statuses[name] == "ok", name
    # Encryption is either proven by the kernel or reported as unknown (or memory-backed: a tmpfs /tmp); never
    # asserted without evidence.
    encryption = next(c for c in report["checks"] if c["name"] == "encryption")
    assert encryption["status"] == "ok" and "dm-crypt" in encryption["detail"] or \
        encryption["status"] == "warn" and encryption["detail"].startswith(("unknown", "memory-backed"))
    assert _cli(["doctor", "--db", str(path)]) == 0
    capsys.readouterr()


def test_doctor_flags_a_world_readable_database(tmp_path, capsys):
    path = _instance(tmp_path)
    path.chmod(0o644)
    report = doctor.run(path)
    assert _statuses(report)["file_mode"] in ("warn", "fail")
    assert doctor.failed(report)
    assert _cli(["doctor", "--db", str(path)]) == 1
    capsys.readouterr()


def test_doctor_flags_a_missing_database_and_world_readable_directory(tmp_path):
    assert doctor.failed(doctor.run(tmp_path / "absent.sqlite"))
    path = _instance(tmp_path)
    path.parent.chmod(0o755)
    assert _statuses(doctor.run(path))["parent_directory"] == "warn"


def test_doctor_without_database_checks_runtime_only():
    report = doctor.run()
    assert {c["name"] for c in report["checks"]} == {"python", "numpy", "zstandard", "sqlite", "json"}
    assert all(c["status"] in ("ok", "warn", "fail") for c in report["checks"])


def test_doctor_checks_the_public_cache_file(tmp_path):
    """S-05: `nsf doctor --db` checks the public web cache next to the instance; a corrupt one is reported (warn:
    it is disposable and rebuilt on its next use), an absent one is fine."""
    path = _instance(tmp_path)
    assert _statuses(doctor.run(path))["public_cache"] == "ok"
    cache = path.with_name(f"{path.stem}.webcache.sqlite")
    cache.write_bytes(b"not a database" * 512)
    check = next(c for c in doctor.run(path)["checks"] if c["name"] == "public_cache")
    assert check["status"] == "warn" and "corrupt" in check["detail"]


def test_doctor_reports_memory_backed_storage_and_never_claims_encryption_without_evidence(tmp_path, monkeypatch):
    path = _instance(tmp_path)
    monkeypatch.setattr(doctor, "_mount", lambda p: {"mount_point": "/tmp", "device": "0:59", "fstype": "tmpfs",
                                                     "source": "tmpfs"})
    encryption = next(c for c in doctor.run(path)["checks"] if c["name"] == "encryption")
    assert encryption["status"] == "warn" and encryption["detail"].startswith("memory-backed (tmpfs")
    monkeypatch.setattr(doctor, "_mount", lambda p: {"mount_point": "/", "device": "0:34", "fstype": "btrfs",
                                                     "source": "/nonexistent/device"})
    encryption = next(c for c in doctor.run(path)["checks"] if c["name"] == "encryption")
    assert encryption["status"] == "warn" and encryption["detail"].startswith("unknown")
    monkeypatch.setattr(doctor, "_mount", lambda p: None)
    encryption = next(c for c in doctor.run(path)["checks"] if c["name"] == "encryption")
    assert encryption["status"] == "warn" and encryption["detail"].startswith("unknown")


def test_swap_is_ok_only_when_it_is_ram_or_absent(monkeypatch):
    header = "Filename\tType\tSize\tUsed\tPriority\n"
    assert doctor.swap_check(header)["status"] == "ok"
    zram = doctor.swap_check(header + "/dev/zram0 partition 127848444 5012824 100\n")
    assert zram["status"] == "ok" and "zram" in zram["detail"]
    disk = doctor.swap_check(header + "/dev/nonexistent-swap partition 1000 0 -2\n")
    assert disk["status"] == "warn" and "not shown to be encrypted" in disk["detail"]
    monkeypatch.setattr(doctor, "_mount", lambda p: {"mount_point": "/", "device": "0:1", "fstype": "ext4",
                                                     "source": "/dev/nonexistent"})
    swapfile = doctor.swap_check(header + "/swapfile file 1000 0 -2\n")
    assert swapfile["status"] == "warn" and "file not shown" in swapfile["detail"]


def test_extra_paths_are_reported_and_a_missing_one_is_unknown(tmp_path):
    report = doctor.run(None, [tmp_path, tmp_path / "absent"])
    names = {c["name"]: c for c in report["checks"]}
    assert f"encryption:{tmp_path}" in names and "swap" in names
    assert names[f"encryption:{tmp_path / 'absent'}"]["detail"].startswith("unknown")
    assert names[f"encryption:{tmp_path}"]["status"] in ("ok", "warn")
