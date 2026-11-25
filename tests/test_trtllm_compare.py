"""scripts/trtllm_compare.py: the parts that decide whether the answer model comes back as it was (no Docker)."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trtllm_compare", ROOT / "scripts" / "trtllm_compare.py")
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

IMAGE = "vllm/vllm-openai@sha256:" + "a" * 64
CMD = ["/models/m", "--served-model-name", "m", "--host", "0.0.0.0", "--port", "8000", "--max-model-len", "32768",
       "--max-num-seqs", "8", "--kv-cache-memory-bytes", "13958643712", "--max-num-batched-tokens", "4096"]
PRINT = ("docker run -d --restart unless-stopped --name neuroscifi-vllm --device nvidia.com/gpu=all "
         "-p 127.0.0.1:8811:8000 --user 1000:1000 --read-only --tmpfs /tmp:rw,size=8g,mode=1777 --cap-drop ALL "
         "--security-opt no-new-privileges --cpus 12 --memory 96g --shm-size 8g --pids-limit 4096 "
         "-v /w/m:/models/m:ro -v /c:/cache:rw -e HOME=/cache -e HF_HUB_OFFLINE=1 "
         "--env-file '<NSF_INFERENCE_DIR>/vllm.secret.env' --health-cmd 'curl -fsS -m 5 http://127.0.0.1:8000/health "
         "|| exit 1' --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 20m "
         f"{IMAGE} " + shlex.join(CMD))


def container():
    return {"Id": "f" * 64, "Image": "sha256:" + "b" * 64, "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {"Image": IMAGE, "Cmd": list(CMD), "Env": ["HOME=/cache", "HF_HUB_OFFLINE=1",
                                                                  "VLLM_API_KEY=nsf-secret-value"]},
            "HostConfig": {"PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8811"}]},
                           "ReadonlyRootfs": True, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                           "RestartPolicy": {"Name": "unless-stopped"}, "Memory": 96 * 2**30,
                           "ShmSize": 8 * 2**30, "NanoCpus": 12 * 10**9, "PidsLimit": 4096},
            "Mounts": [{"Source": "/w/m", "Destination": "/models/m", "RW": False},
                       {"Source": "/c", "Destination": "/cache", "RW": True}],
            "NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8811"}]}}}


def test_the_running_container_is_reproduced_by_run_sh():
    assert tc.reproduction_problems(container(), shlex.split(PRINT)) == []


@pytest.mark.parametrize("change, expected", [
    (lambda d: d["Config"].update(Cmd=CMD[:-2]), "model server arguments differ"),
    (lambda d: d["Config"].update(Image="vllm/vllm-openai:nightly"), "image"),
    (lambda d: d["HostConfig"].update(ShmSize=1), "shm size"),
    (lambda d: d["HostConfig"].update(ReadonlyRootfs=False), "ReadonlyRootfs"),
    (lambda d: d["HostConfig"]["PortBindings"]["8000/tcp"][0].update(HostIp="0.0.0.0"), "port"),
    (lambda d: d["Mounts"].pop(), "mount"),
    (lambda d: d["Config"].update(Env=["HOME=/cache", "HF_HUB_OFFLINE=1"]), "VLLM_API_KEY"),
])
def test_any_difference_from_run_sh_is_reported(change, expected):
    doc = container()
    change(doc)
    problems = tc.reproduction_problems(doc, shlex.split(PRINT))
    assert any(expected in p for p in problems), problems


def test_the_written_capture_holds_no_key():
    text = json.dumps(tc.redacted(container()))
    assert "nsf-secret-value" not in text and "VLLM_API_KEY=<redacted>" in text


def test_kv_cache_tokens_match_vllms_sizing(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"num_hidden_layers": 48, "num_key_value_heads": 4,
                                                      "head_dim": 128, "hidden_size": 2048,
                                                      "num_attention_heads": 32}))
    assert tc.kv_tokens(tmp_path, 13958643712) == 141994      # "13 GiB = ~142k tokens" (deploy/vllm/vllm.env)


def test_it_refuses_to_stop_anything_without_yes():
    res = subprocess.run(["python3", str(ROOT / "scripts" / "trtllm_compare.py")], capture_output=True, text=True)
    assert res.returncode == 2 and "--yes" in res.stderr


class FakeDocker:
    """Records docker calls; the answer model's container exists and comes back healthy (running and publishing
    127.0.0.1:8811 once started, unless `publishes` is False)."""

    def __init__(self, gone=False, start_fails=False, unit_stop_fails=(), publishes=True, stop_interrupted=()):
        self.calls, self.gone, self.start_fails, self.unit_stop_fails = [], gone, start_fails, set(unit_stop_fails)
        self.running, self.publishes, self.stop_interrupted = True, publishes, set(stop_interrupted)

    def sh(self, cmd, check=True, capture=True, timeout=None):
        self.calls.append(cmd)
        rc = 1 if (cmd[:2] == ["docker", "start"] and self.start_fails) else 0
        if cmd[:2] == ["docker", "start"] and not rc:
            self.running = True
        if cmd[:2] == ["docker", "stop"]:
            self.running = False
        if cmd[:3] == ["systemctl", "--no-ask-password", "stop"] and cmd[3] in self.stop_interrupted:
            raise tc.Abort("signal SIGTERM")          # the owner's Ctrl-C while systemctl stops the unit
        if cmd[:3] == ["systemctl", "--no-ask-password", "stop"] and cmd[3] in self.unit_stop_fails:
            rc = 1
        if check and rc:
            raise tc.Abort(f"{cmd} failed")
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="Access denied" if rc else "")

    def inspect(self, name):
        if name == "neuroscifi-vllm" and not self.gone:
            doc = container()
            doc["State"]["Running"] = self.running
            if not (self.running and self.publishes):
                doc["NetworkSettings"]["Ports"] = {}
            return doc
        return None


def restoring_run(monkeypatch, tmp_path, fake, *, port_held=False, stopped_services=False):
    monkeypatch.setattr(tc, "sh", fake.sh)
    monkeypatch.setattr(tc, "inspect", fake.inspect)
    monkeypatch.setattr(tc, "mem_available_gb", lambda: 50.0)
    monkeypatch.setattr(tc, "port_open", lambda host, port, timeout=1.0: port_held)   # not this host's real 8811
    monkeypatch.setattr(tc, "port_holder", lambda port: 'LISTEN 0 4096 127.0.0.1:8811 users:(("squatter",pid=9))')
    args = type("A", (), {"out_dir": str(tmp_path), "restore_timeout": 5,
                          "i_stopped_the_services": stopped_services})()
    run = tc.Run(args)
    run.vllm = {"name": "neuroscifi-vllm", "bind": "127.0.0.1", "port": 8811}
    run.original = container()
    run.stopped_vllm = True
    fake.running = False                       # the window stopped it
    return run


def test_restore_starts_the_same_container_and_finds_no_difference(monkeypatch, tmp_path):
    fake = FakeDocker()
    run = restoring_run(monkeypatch, tmp_path, fake)
    run.restore()
    assert ["docker", "start", "neuroscifi-vllm"] in fake.calls
    assert run.restore_report["healthy"] and run.restore_report["differences"] == []
    assert run.stopped_vllm is False


def test_restore_falls_back_to_run_sh_when_the_container_is_gone(monkeypatch, tmp_path):
    fake = FakeDocker(gone=True)
    run = restoring_run(monkeypatch, tmp_path, fake)
    run.restore()
    assert any(c[:1] == ["bash"] and c[-1] == "start" for c in fake.calls)
    assert "run.sh start" in run.restore_report["method"]


def test_an_abort_after_the_stop_still_restores(monkeypatch, tmp_path):
    fake = FakeDocker()
    run = restoring_run(monkeypatch, tmp_path, fake)
    run.started = [tc.TRT_CONTAINER]

    def boom():
        raise tc.Abort("load failed")

    monkeypatch.setattr(tc, "Run", lambda args: run)
    monkeypatch.setattr(run, "run_main", boom)
    code = tc.main(["--yes", "--out-dir", str(tmp_path)])
    assert code == 2
    assert ["docker", "start", "neuroscifi-vllm"] in fake.calls
    assert ["docker", "rm", "-f", tc.TRT_CONTAINER] not in fake.calls   # inspect says it is already gone
    assert json.loads((tmp_path / "result.json").read_text())["steps"][-1]["step"] == "restore"


# ---- review findings of the remaining-items round (M1, I1) -------------------------------------------------------

def test_a_key_given_as_an_argument_is_redacted_everywhere_it_is_written():
    doc = container()
    doc["Config"]["Cmd"] = CMD + ["--api-key", "nsf-secret-value", "--hf-token=hf-secret"]
    doc["Args"] = list(doc["Config"]["Cmd"])
    text = json.dumps(tc.redacted(doc))
    assert "nsf-secret-value" not in text and "hf-secret" not in text
    assert '"--api-key", "<redacted>"' in text and "--hf-token=<redacted>" in text
    assert tc.redacted(doc)["Config"]["Cmd"][:len(CMD)] == CMD            # nothing else (a count of tokens) changes
    assert tc.redact_argv(["--api_key", "x", "--password=y", "--key-file", "/run/k"]) == \
        ["--api_key", "<redacted>", "--password=<redacted>", "--key-file", "/run/k"]
    problems = tc.reproduction_problems(doc, shlex.split(PRINT))          # also in the messages it writes
    assert problems and "nsf-secret-value" not in json.dumps(problems) and "hf-secret" not in json.dumps(problems)


def test_written_files_are_private_and_never_follow_a_link(tmp_path):
    loose = tmp_path / "result.json"
    loose.write_text("old")
    loose.chmod(0o644)
    tc.write_private(loose, "new")
    assert loose.read_text() == "new" and (loose.stat().st_mode & 0o777) == 0o600
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    link = tmp_path / "planted.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        tc.write_private(link, "secret")
    assert target.read_text() == "keep"


def test_the_window_container_is_unprivileged_and_on_its_own_network(tmp_path):
    args = type("A", (), {"out_dir": str(tmp_path), "image": "trt@sha256:" + "c" * 64})()
    run = tc.Run(args)
    run.out.mkdir(exist_ok=True)
    argv = run.trt_args(name="n", model_dir=tmp_path, served="m", port=8812, max_len=32768, max_batch=8,
                        max_tokens=4096, kv_tok=1000, tool_parser=None, ptxas=False, mem_limit="96g")
    line = shlex.join(argv)
    for flag in ("--network neuroscifi-trtllm-compare", "-p 127.0.0.1:8812:8000", f"--user {os.getuid()}:",
                 "--read-only", "--tmpfs /tmp:rw,exec", "--pids-limit 4096", "--cap-drop ALL",
                 "--security-opt no-new-privileges"):
        assert flag in line, flag
    assert "0.0.0.0:" not in line
    assert ((tmp_path / "n.yaml").stat().st_mode & 0o777) == 0o600          # read by the same uid inside


def test_the_callers_are_paused_and_started_again_after_the_restore(monkeypatch, tmp_path):
    fake = FakeDocker()
    run = restoring_run(monkeypatch, tmp_path, fake)
    units = ["neuroscifi-webcortex-tick.timer", "neuroscifi-webcortex.service", "neuroscifi-pocket.service"]
    run.pause_callers(units)
    assert run.paused == units
    run.restore()
    stops = [c[3] for c in fake.calls if c[:3] == ["systemctl", "--no-ask-password", "stop"]]
    starts = [c[3] for c in fake.calls if c[:3] == ["systemctl", "--no-ask-password", "start"]]
    assert stops == units and starts == list(reversed(units)) and run.paused == []
    # the answer model came back before its callers did
    assert fake.calls.index(["docker", "start", "neuroscifi-vllm"]) < \
        fake.calls.index(["systemctl", "--no-ask-password", "start", units[-1]])


def test_a_unit_it_may_not_stop_refuses_the_run_and_restarts_what_it_stopped(monkeypatch, tmp_path):
    fake = FakeDocker(unit_stop_fails={"neuroscifi-console.service"})
    run = restoring_run(monkeypatch, tmp_path, fake)
    run.stopped_vllm, fake.running = False, True                   # nothing stopped yet: the pause comes first
    with pytest.raises(tc.Abort, match="--i-stopped-the-services"):
        run.pause_callers(["neuroscifi-webcortex.service", "neuroscifi-console.service"])
    run.restore()
    assert ["systemctl", "--no-ask-password", "start", "neuroscifi-webcortex.service"] in fake.calls
    assert not any(c[:2] == ["docker", "start"] for c in fake.calls)  # the answer model was never touched
    # the owner's word lets the run go on without that unit
    fake2 = FakeDocker(unit_stop_fails={"neuroscifi-console.service"})
    run2 = restoring_run(monkeypatch, tmp_path, fake2, stopped_services=True)
    run2.pause_callers(["neuroscifi-webcortex.service", "neuroscifi-console.service"])
    assert run2.paused == ["neuroscifi-webcortex.service"]


def test_a_held_port_is_reported_instead_of_retried_blind(monkeypatch, tmp_path):
    fake = FakeDocker(start_fails=True)
    run = restoring_run(monkeypatch, tmp_path, fake, port_held=True)
    run.restore()
    report = run.restore_report
    assert "squatter" in report["port_held_by"] and report["healthy"] is False
    assert not any(c[:1] == ["bash"] for c in fake.calls), "neither run.sh start nor a 30-minute wait"
    assert run.stopped_vllm is True                                  # main() then exits 4


def test_hand_started_neuroscifi_processes_are_found(tmp_path):
    proc = subprocess.Popen(["bash", "-c", "exec -a nsf sleep 30"])
    try:
        import time
        for _ in range(50):
            found = tc.caller_processes()
            if any(p["pid"] == proc.pid for p in found):
                break
            time.sleep(0.05)
        assert any(p["pid"] == proc.pid for p in found)
        assert all(p["pid"] != os.getpid() for p in found)             # never itself
    finally:
        proc.kill()
        proc.wait()


# ---- re-review of the fix round (S1, S2, S6, S7, S8, S9) --------------------------------------------------------

def _starts(fake):
    return [c[3] for c in fake.calls if c[:3] == ["systemctl", "--no-ask-password", "start"]]


def test_the_callers_stay_stopped_while_something_else_holds_the_answer_models_port(monkeypatch, tmp_path):
    """S1: the restore found a squatter on 8811; starting the callers again would send it the key."""
    fake = FakeDocker(start_fails=True)
    run = restoring_run(monkeypatch, tmp_path, fake, port_held=True)
    units = ["neuroscifi-webcortex.service", "neuroscifi-console.service"]
    run.pause_callers(units)
    run.restore()
    assert _starts(fake) == [] and run.paused == []
    withheld = run.results["callers_withheld"]
    assert withheld["units"] == units and "squatter" in withheld["why"]
    assert run.results["steps"][-1]["step"] == "resume-withheld"


def test_the_callers_stay_stopped_when_the_model_is_back_but_does_not_publish_its_port(monkeypatch, tmp_path):
    """S1: healthy by its own wait, yet the container does not publish 127.0.0.1:8811: whatever answers there is not
    the answer model. main() exits 5."""
    fake = FakeDocker(publishes=False)
    run = restoring_run(monkeypatch, tmp_path, fake)
    run.pause_callers(["neuroscifi-pocket.service"])
    run.restore()
    assert run.restore_report["healthy"] and _starts(fake) == []
    assert "does not publish 127.0.0.1:8811" in run.results["callers_withheld"]["why"]
    fake2 = FakeDocker(publishes=False)
    run2 = restoring_run(monkeypatch, tmp_path / "2", fake2)

    def paused_then_done():
        run2.pause_callers(["neuroscifi-pocket.service"])
        return 0

    monkeypatch.setattr(tc, "Run", lambda args: run2)
    monkeypatch.setattr(run2, "run_main", paused_then_done)
    assert tc.main(["--yes", "--out-dir", str(tmp_path / "2")]) == 5


def test_a_signal_while_a_unit_is_stopped_still_starts_it_again(monkeypatch, tmp_path):
    """S9: the unit is listed before systemctl stops it, so an interrupted stop is undone by the restore."""
    fake = FakeDocker(stop_interrupted={"neuroscifi-console.service"})
    run = restoring_run(monkeypatch, tmp_path, fake)
    run.stopped_vllm, fake.running = False, True
    with pytest.raises(tc.Abort, match="signal"):
        run.pause_callers(["neuroscifi-webcortex.service", "neuroscifi-console.service"])
    run.restore()
    assert _starts(fake) == ["neuroscifi-console.service", "neuroscifi-webcortex.service"]


def _proc(root, pid, argv, cgroup):
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (folder / "cgroup").write_text(cgroup)


def test_the_units_own_processes_are_not_counted_as_started_by_hand(monkeypatch, tmp_path):
    """S2: the units run /opt/neuroscifi/venv/bin/nsf, which the kernel shows as [python3, .../nsf, ...]: by their
    cgroup they are the units' (paused by the script), not processes started by hand."""
    root = tmp_path / "proc"
    nsf = ["/opt/neuroscifi/venv/bin/python3", "/opt/neuroscifi/venv/bin/nsf"]
    _proc(root, 101, nsf + ["console", "--db", "x"], "0::/system.slice/neuroscifi-console.service\n")
    _proc(root, 102, nsf + ["web", "work"], "0::/system.slice/neuroscifi-webcortex.service/worker\n")
    _proc(root, 103, ["python3", "-m", "neuroscifi.cli", "pocket", "serve"],
          "12:pids:/system.slice/neuroscifi-pocket.service\n0::/system.slice/neuroscifi-pocket.service\n")
    _proc(root, 201, nsf + ["start"], "0::/user.slice/user-1000.slice/session-3.scope\n")          # a terminal
    _proc(root, 202, nsf + ["web", "work"], "0::/system.slice/someone-elses.service\n")             # not paused
    _proc(root, 203, ["sleep", "30"], "0::/system.slice/neuroscifi-console.service\n")              # not NeuroSciFi
    monkeypatch.setattr(tc, "PROC", root)
    assert sorted(p["pid"] for p in tc.caller_processes()) == [201, 202]
    assert tc.unit_of(102) == "neuroscifi-webcortex.service" and tc.unit_of(201) is None


def test_a_network_of_the_same_name_with_traffic_between_containers_on_is_refused(monkeypatch, tmp_path):
    """S6: an existing network of the window's name is used only as the script makes it (bridge, ICC off)."""
    answers = {}

    def sh(cmd, check=True, capture=True, timeout=None):
        out = answers.get("inspect", "")
        rc = 0 if out else 1
        if cmd[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(tc, "sh", sh)
    run = tc.Run(type("A", (), {"out_dir": str(tmp_path)})())
    answers["inspect"] = 'bridge {"com.docker.network.bridge.enable_icc":"true"}'
    with pytest.raises(tc.Abort, match="traffic between its containers on"):
        run.ensure_network()
    answers["inspect"] = "bridge {}"                       # Docker's default for a bridge: ICC on
    with pytest.raises(tc.Abort):
        run.ensure_network()
    answers["inspect"] = 'bridge {"com.docker.network.bridge.enable_icc":"false"}'
    run.ensure_network()
    assert run.network_created is False                    # not ours to remove
    answers["inspect"] = ""                                # none: created with ICC off
    run.ensure_network()
    assert run.network_created is True


def test_the_proxy_vllm_server_is_on_the_windows_network_with_a_pids_limit(monkeypatch, tmp_path):
    """S7: the small vLLM server of the proxy is as confined as the TensorRT-LLM one."""
    model = tmp_path / "small"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"hidden_size": 64, "num_attention_heads": 4,
                                                    "num_hidden_layers": 2, "num_key_value_heads": 2}))
    calls = []

    def sh(cmd, check=True, capture=True, timeout=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if cmd[:3] != ["docker", "network", "inspect"] else 1,
                                           stdout="", stderr="")
    monkeypatch.setattr(tc, "sh", sh)
    monkeypatch.setattr(tc, "inspect", lambda name: None)
    monkeypatch.setattr(tc, "mem_available_gb", lambda: 64.0)
    args = type("A", (), {"out_dir": str(tmp_path / "out"), "proxy": str(model), "image": "trt@sha256:" + "c" * 64,
                          "port": 8812, "proxy_min_free_gb": 20.0, "proxy_mem_gb": 8.0, "dry_run": False,
                          "proxy_engines": "vllm", "health_timeout": 1, "scenarios": ""})()
    run = tc.Run(args)
    monkeypatch.setattr(run, "trt_probe", lambda image: {"tool_parsers": None, "ptxas_in_image": False,
                                                         "missing_flags": []})

    def unhealthy(name, base, timeout):
        raise tc.Abort("not healthy")
    monkeypatch.setattr(run, "wait_health", unhealthy)
    monkeypatch.setattr(run, "save_log", lambda name: None)
    assert run.run_proxy() == 0
    started = next(c for c in calls if c[:2] == ["docker", "run"] and "neuroscifi-trtllm-proxy-vllm" in c)
    line = shlex.join(started)
    assert f"--network {tc.TRT_NETWORK}" in line and f"--pids-limit {tc.TRT_PIDS}" in line
    assert "-p 127.0.0.1:8813:8000" in line


def test_a_model_run_by_its_unit_refuses_the_window(monkeypatch, tmp_path):
    """S8: neuroscifi-vllm.service (Restart=on-failure, docker run --rm) would start the model again mid-window."""
    monkeypatch.setattr(tc, "unit_state", lambda unit: "active" if unit == tc.VLLM_UNIT else "inactive")
    monkeypatch.setattr(tc, "mem_available_gb", lambda: 50.0)
    monkeypatch.setattr(tc, "kv_tokens", lambda model_dir, kv_bytes: 1000)
    monkeypatch.setattr(tc, "sh", lambda cmd, check=True, capture=True, timeout=None:
                        subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""))
    args = type("A", (), {"out_dir": str(tmp_path / "out"), "image": "trt@sha256:" + "c" * 64, "port": 8812,
                          "min_free_gb": 12.0, "need_gb": 48.0, "i_stopped_the_services": False, "dry_run": True})()
    run = tc.Run(args)
    run.out.mkdir(parents=True)
    run.vllm = {"name": "neuroscifi-vllm", "bind": "127.0.0.1", "port": 8811, "served": "m",
                "url": "http://127.0.0.1:8811/v1"}
    monkeypatch.setattr(run, "preflight", lambda: (container(), [], "healthy"))
    monkeypatch.setattr(run, "trt_probe", lambda image: {"tool_parsers": None, "ptxas_in_image": False,
                                                         "missing_flags": []})
    monkeypatch.setattr(run, "callers", lambda: {"units": [], "processes": []})
    assert run.run_main() == 3
    assert any(tc.VLLM_UNIT in b and "systemctl stop" in b for b in run.results["blockers"])

