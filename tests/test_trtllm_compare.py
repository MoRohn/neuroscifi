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
                       {"Source": "/c", "Destination": "/cache", "RW": True}]}


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
    """Records docker calls; the answer model's container exists and comes back healthy."""

    def __init__(self, gone=False, start_fails=False, unit_stop_fails=()):
        self.calls, self.gone, self.start_fails, self.unit_stop_fails = [], gone, start_fails, set(unit_stop_fails)

    def sh(self, cmd, check=True, capture=True, timeout=None):
        self.calls.append(cmd)
        rc = 1 if (cmd[:2] == ["docker", "start"] and self.start_fails) else 0
        if cmd[:3] == ["systemctl", "--no-ask-password", "stop"] and cmd[3] in self.unit_stop_fails:
            rc = 1
        if check and rc:
            raise tc.Abort(f"{cmd} failed")
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="Access denied" if rc else "")

    def inspect(self, name):
        if name == "neuroscifi-vllm" and not self.gone:
            return container()
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
    run.stopped_vllm = False                                       # nothing stopped yet: the pause comes first
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
