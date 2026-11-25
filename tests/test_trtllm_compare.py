"""scripts/trtllm_compare.py: the parts that decide whether the answer model comes back as it was (no Docker)."""
from __future__ import annotations

import importlib.util
import json
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

    def __init__(self, gone=False, start_fails=False):
        self.calls, self.gone, self.start_fails = [], gone, start_fails

    def sh(self, cmd, check=True, capture=True, timeout=None):
        self.calls.append(cmd)
        rc = 1 if (cmd[:2] == ["docker", "start"] and self.start_fails) else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

    def inspect(self, name):
        if name == "neuroscifi-vllm" and not self.gone:
            return container()
        return None


def restoring_run(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(tc, "sh", fake.sh)
    monkeypatch.setattr(tc, "inspect", fake.inspect)
    monkeypatch.setattr(tc, "mem_available_gb", lambda: 50.0)
    args = type("A", (), {"out_dir": str(tmp_path), "restore_timeout": 5})()
    run = tc.Run(args)
    run.vllm = {"name": "neuroscifi-vllm"}
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
