#!/usr/bin/env python3
"""TensorRT-LLM against the vLLM answer model on this host, with the answer model restored on every exit path.

The comparison needs a window: the answer model (container `neuroscifi-vllm`, ~44 GB of unified memory) and a
second engine serving the same 31 GB of weights do not fit beside each other. The owner chooses the window; this
script never stops the answer model unless `--yes` is given.

  python scripts/trtllm_compare.py --dry-run                 # checks everything, stops and starts nothing
  python scripts/trtllm_compare.py --yes                     # the real window (see NVIDIA_ASSESSMENT.md §5)
  python scripts/trtllm_compare.py --proxy DIR --yes         # a small model on both engines; the answer model stays

What a real run does, in order:
  1. Preflight: Docker, the pinned TensorRT-LLM image, the model folder, the answer model's container running and
     healthy, `trtllm-serve` accepting every flag used, enough free memory now (`--min-free-gb`).
  2. Captures the answer model's container (`docker inspect`; values of secret-looking variables redacted in the
     written copy, kept unredacted only in memory) and checks that `deploy/vllm/run.sh print` reproduces it, so the
     fallback restore (`run.sh start`) would start the same server.
  3. Baseline: `scripts/bench_inference.py` (and `scripts/bench_pipeline.py` unless `--no-pipeline`) on vLLM.
  4. Stops the answer model (`docker stop`; the container is kept, so `docker start` restores it exactly).
  5. Starts `trtllm-serve` on the same weights with the same limits (max sequence 32768, max batch 8, 4096 tokens
     per step, a KV cache of the same size), loopback only, and waits for /health.
  6. Runs the same harness with the same arguments against it.
  7. Always (normal end, error, Ctrl-C, SIGTERM, SIGHUP): removes the TensorRT-LLM container, starts the answer
     model's own container again (or `run.sh start` if it no longer exists), waits for health, and compares its
     configuration with the capture. SIGKILL cannot be caught: then run `deploy/vllm/run.sh start` by hand.

Results go to `--out-dir` (default ~/.cache/neuroscifi/trtllm-compare/<time>), 0700. Keys are never printed or
written; the TensorRT-LLM server has no key option and listens on 127.0.0.1 only for the length of the window.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "deploy" / "vllm" / "run.sh"
# nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4 (pre-release, 2026-11-18), multi-arch index; arm64 manifest
# sha256:2638789151d53852aef4067eaa8da69093801aaf1a4f68be81d1c4882731ceac. Not the stable 1.3.0 (2026-11-20): its FP8
# block-scale linear layer has a path for SM 120 but not for GB10's SM 121, which fell to a kernel that refuses the
# checkpoint's float scales ("Scale dtype must be Int32", measured on Qwen3-0.6B-FP8, the same 128x128 e4m3 format as
# the answer model). 1.4.0rc4 routes the whole SM 12x family (`is_sm_120f`) and served that model on this host.
# docs/quality/evidence/remaining_trtllm.json.
TRT_IMAGE = ("nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4"
             "@sha256:813f751bc2c7ed867a53915dedde1c153ca94758e931d989e9c25835f9ba1814")
TRT_CONTAINER = "neuroscifi-trtllm-compare"
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.I)
# Fields of `docker inspect` that define the server; a restore must leave all of them as they were.
COMPARED = (("Config", "Image"), ("Config", "Cmd"), ("Config", "Entrypoint"), ("Config", "User"),
            ("Config", "Env"), ("Config", "Healthcheck"), ("HostConfig", "PortBindings"), ("HostConfig", "Binds"),
            ("HostConfig", "ReadonlyRootfs"), ("HostConfig", "Tmpfs"), ("HostConfig", "CapDrop"),
            ("HostConfig", "SecurityOpt"), ("HostConfig", "NanoCpus"), ("HostConfig", "Memory"),
            ("HostConfig", "ShmSize"), ("HostConfig", "PidsLimit"), ("HostConfig", "DeviceRequests"),
            ("HostConfig", "RestartPolicy"), ("Image",), ("Id",))


class Abort(Exception):
    pass


def log(msg):
    print(f"[trtllm-compare {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def sh(cmd, check=True, capture=True, timeout=None):
    res = subprocess.run(cmd, text=True, capture_output=capture, timeout=timeout)
    if check and res.returncode != 0:
        raise Abort(f"{shlex.join(cmd)} failed ({res.returncode}): {(res.stderr or '').strip()[-800:]}")
    return res


def mem_available_gb():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 2**20
    raise Abort("no MemAvailable in /proc/meminfo")


def inspect(name):
    res = sh(["docker", "inspect", name], check=False)
    if res.returncode != 0:
        return None
    return json.loads(res.stdout)[0]


def field(doc, path):
    for key in path:
        doc = (doc or {}).get(key)
    return doc


def redacted(doc):
    """A copy of an inspect document with the values of secret-looking environment variables removed."""
    doc = json.loads(json.dumps(doc))
    env = field(doc, ("Config", "Env")) or []
    doc["Config"]["Env"] = [e.split("=", 1)[0] + "=<redacted>" if SECRET_NAME.search(e.split("=", 1)[0]) else e
                            for e in env]
    return doc


def write_private(path: Path, text: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def vllm_settings():
    """The answer model's container name, URL, model name and the docker run arguments run.sh would use."""
    out = sh(["bash", str(RUN_SH), "print"]).stdout.strip().splitlines()[-1]
    argv = shlex.split(out)
    if argv[:4] != ["docker", "run", "-d", "--restart"]:
        raise Abort(f"unexpected run.sh print output: {out[:120]}")
    name = argv[argv.index("--name") + 1]
    port = argv[argv.index("-p") + 1]                      # 127.0.0.1:8811:8000
    bind, host_port, _ = port.rsplit(":", 2)
    served = argv[argv.index("--served-model-name") + 1]
    return {"argv": argv, "name": name, "bind": bind, "port": int(host_port), "served": served,
            "url": f"http://{bind}:{host_port}/v1"}


def reproduction_problems(doc, argv):
    """Where `run.sh print` would not start the container `doc` describes (empty: it reproduces it)."""
    problems = []
    opts, i, image_at = {}, 3, None                       # argv[3] is --restart
    flags_with_value = {"--restart", "--name", "--device", "-p", "--user", "--tmpfs", "--security-opt", "--cpus",
                        "--memory", "--shm-size", "--pids-limit", "-v", "-e", "--env-file", "--health-cmd",
                        "--health-interval", "--health-timeout", "--health-retries", "--health-start-period",
                        "--cap-drop"}
    while i < len(argv):
        a = argv[i]
        if a in flags_with_value:
            opts.setdefault(a, []).append(argv[i + 1])
            i += 2
        elif a.startswith("-"):
            opts.setdefault(a, []).append(True)
            i += 1
        else:
            image_at = i
            break
    if image_at is None:
        return ["no image in run.sh print"]
    image, cmd = argv[image_at], argv[image_at + 1:]
    if field(doc, ("Config", "Image")) != image:
        problems.append(f"image: container {field(doc, ('Config', 'Image'))} vs run.sh {image}")
    if (field(doc, ("Config", "Cmd")) or []) != cmd:
        problems.append("model server arguments differ: container "
                        f"{field(doc, ('Config', 'Cmd'))} vs run.sh {cmd}")
    bind, hport, cport = opts["-p"][0].rsplit(":", 2)
    pb = field(doc, ("HostConfig", "PortBindings")) or {}
    if pb.get(f"{cport}/tcp") != [{"HostIp": bind, "HostPort": hport}]:
        problems.append(f"port: container {pb} vs run.sh {opts['-p'][0]}")
    mounts = {(m["Source"], m["Destination"], m["RW"]) for m in doc.get("Mounts", [])}
    for v in opts.get("-v", []):
        src, dst, mode = v.split(":")
        if (src, dst, mode == "rw") not in mounts:
            problems.append(f"mount {v} not on the container")
    if len(mounts) != len(opts.get("-v", [])):
        problems.append(f"container has {len(mounts)} mounts, run.sh {len(opts.get('-v', []))}")
    want_env = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in opts.get("-e", [])}
    have_env = dict(e.split("=", 1) for e in field(doc, ("Config", "Env")) or [])
    for k, v in want_env.items():
        if have_env.get(k) != v:
            problems.append(f"environment {k} differs")
    if "VLLM_API_KEY" not in have_env:
        problems.append("the container has no VLLM_API_KEY (run.sh passes it through its env file)")
    checks = [("ReadonlyRootfs", True, "--read-only" in opts),
              ("CapDrop", ["ALL"], (opts.get("--cap-drop") or [None])[0] == "ALL"),
              ("SecurityOpt", ["no-new-privileges"], opts.get("--security-opt") == ["no-new-privileges"]),
              ("RestartPolicy", "unless-stopped", opts.get("--restart") == ["unless-stopped"])]
    for key, want, flag_ok in checks:
        have = field(doc, ("HostConfig", key))
        have = have.get("Name") if isinstance(have, dict) else have
        if have != want or not flag_ok:
            problems.append(f"{key}: container {have!r}, run.sh {'agrees' if flag_ok else 'differs'}")
    gib = {"g": 2**30, "m": 2**20}
    mem = opts["--memory"][0]
    if field(doc, ("HostConfig", "Memory")) != int(mem[:-1]) * gib[mem[-1].lower()]:
        problems.append(f"memory limit differs ({mem})")
    shm = opts["--shm-size"][0]
    if field(doc, ("HostConfig", "ShmSize")) != int(shm[:-1]) * gib[shm[-1].lower()]:
        problems.append(f"shm size differs ({shm})")
    if field(doc, ("HostConfig", "NanoCpus")) != int(float(opts["--cpus"][0]) * 1e9):
        problems.append("cpus differ")
    if field(doc, ("HostConfig", "PidsLimit")) != int(opts["--pids-limit"][0]):
        problems.append("pids limit differs")
    return problems


def http_ok(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def kv_tokens(model_dir: Path, kv_bytes: int):
    """Tokens a KV cache of `kv_bytes` holds for this model at 16-bit (as vLLM sizes it)."""
    c = json.loads((model_dir / "config.json").read_text())
    head_dim = c.get("head_dim") or c["hidden_size"] // c["num_attention_heads"]
    per_token = 2 * c["num_hidden_layers"] * c["num_key_value_heads"] * head_dim * 2
    return kv_bytes // per_token


class Run:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out_dir).expanduser()
        self.vllm = None
        self.original = None          # unredacted inspect document, in memory only
        self.stopped_vllm = False
        self.started = []             # containers this run started
        self.restore_report = None
        self.results = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": []}

    # -- helpers ------------------------------------------------------------------------------------------------
    def step(self, name, **data):
        entry = {"step": name, "at": time.strftime("%H:%M:%S"), "mem_available_gb": round(mem_available_gb(), 1),
                 **data}
        self.results["steps"].append(entry)
        log(f"{name} {json.dumps(data)[:300] if data else ''}")

    def save(self):
        write_private(self.out / "result.json", json.dumps(self.results, indent=1, default=str) + "\n")

    def trt_probe(self, image):
        """Whether the image is present and its `trtllm-serve serve` accepts every flag this script uses."""
        if sh(["docker", "image", "inspect", image], check=False).returncode != 0:
            raise Abort(f"image {image} is not pulled (docker pull {image})")
        # The package loads libcuda at import, so even --help needs the GPU device (no model, no memory to speak of).
        helptext = sh(["docker", "run", "--rm", "--network", "none", "--device", "nvidia.com/gpu=all",
                       "--entrypoint", "trtllm-serve", image, "serve", "--help"], timeout=300).stdout
        used = ["--host", "--port", "--max_batch_size", "--max_seq_len", "--max_num_tokens",
                "--extra_llm_api_options", "--tool_parser"]
        missing = [f for f in used if f not in helptext]
        tool_parsers = re.findall(r"--tool_parser\s+\[([^\]]+)\]", helptext)
        version = sh(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python3", image, "-c",
                      "import importlib.metadata as m; print(m.version('tensorrt_llm'))"],
                     check=False, timeout=300).stdout.strip().splitlines()
        ptxas = sh(["docker", "run", "--rm", "--network", "none", "--entrypoint", "test", image, "-x",
                    "/usr/local/cuda/bin/ptxas"], check=False, timeout=120).returncode == 0
        return {"missing_flags": missing, "tool_parsers": tool_parsers[0] if tool_parsers else None,
                "tensorrt_llm": version[-1] if version else None, "ptxas_in_image": ptxas}

    def trt_args(self, *, name, model_dir, served, port, max_len, max_batch, max_tokens, kv_tok, tool_parser,
                 ptxas, mem_limit):
        cfg = self.out / f"{name}.yaml"
        write_private(cfg, "\n".join([
            "kv_cache_config:",
            f"  max_tokens: {kv_tok}",
            "  enable_block_reuse: true",
            "  free_gpu_memory_fraction: 0.9",
            "cuda_graph_config:",
            "  enable_padding: true",
            f"  max_batch_size: {max_batch}",
            # JSON-schema answers (the product's structured reasoning call) need a grammar backend; without one
            # TensorRT-LLM ignores response_format (proxy: 0 of 8 valid). vLLM's default backend is also xgrammar.
            "guided_decoding_backend: xgrammar",
            "print_iter_log: false", ""]))
        os.chmod(cfg, 0o644)                      # read inside the container
        mount = f"/models/{served}"
        argv = ["docker", "run", "-d", "--name", name, "--device", "nvidia.com/gpu=all",
                "-p", f"127.0.0.1:{port}:8000", "--ipc", "private", "--shm-size", "8g",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", mem_limit,
                "-v", f"{model_dir}:{mount}:ro", "-v", f"{cfg}:/config/extra.yaml:ro",
                "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1", "-e", "HOME=/tmp",
                "-e", "PYTORCH_ALLOC_CONF=expandable_segments:True"]
        if ptxas:
            argv += ["-e", "TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas"]
        argv += ["--entrypoint", "trtllm-serve", self.args.image, "serve", mount,
                 "--host", "0.0.0.0", "--port", "8000",
                 "--max_batch_size", str(max_batch), "--max_seq_len", str(max_len),
                 "--max_num_tokens", str(max_tokens), "--extra_llm_api_options", "/config/extra.yaml"]
        if tool_parser:
            argv += ["--tool_parser", tool_parser]
        return argv

    def wait_health(self, name, base, timeout):
        start = time.time()
        while time.time() - start < timeout:
            state = field(inspect(name), ("State", "Status"))
            if state in (None, "exited", "dead"):
                raise Abort(f"{name} is {state or 'gone'} while starting; log in {self.out}/{name}.log")
            if http_ok(base + "/health"):
                return round(time.time() - start, 1)
            time.sleep(5)
        raise Abort(f"{name} not healthy after {timeout}s")

    def save_log(self, name):
        res = sh(["docker", "logs", "--tail", "400", name], check=False)
        write_private(self.out / f"{name}.log", (res.stdout or "") + (res.stderr or ""))

    def bench(self, label, url, model, key_file, scenarios):
        out = self.out / f"inference-{label}.json"
        cmd = [sys.executable, str(ROOT / "scripts" / "bench_inference.py"), "--url", url, "--model", model,
               "--label", label, "--runs", str(self.args.runs), "--out", str(out)]
        if scenarios:
            cmd += ["--scenarios", scenarios]
        if key_file:
            cmd += ["--key-file", key_file]
        t0 = time.time()
        res = subprocess.run(cmd, text=True, capture_output=True)
        entry = {"label": label, "exit": res.returncode, "seconds": round(time.time() - t0), "file": str(out)}
        if res.returncode != 0:
            entry["stderr"] = res.stderr[-1500:]
        if not self.args.no_pipeline:
            pout = self.out / f"pipeline-{label}.json"
            cmd = [sys.executable, str(ROOT / "scripts" / "bench_pipeline.py"), "--url", url, "--model", model,
                   "--label", label, "--out", str(pout)] + (["--key-file", key_file] if key_file else []) \
                + (["--quick"] if self.args.quick_pipeline else [])
            t1 = time.time()
            pres = subprocess.run(cmd, text=True, capture_output=True)
            entry["pipeline"] = {"exit": pres.returncode, "seconds": round(time.time() - t1), "file": str(pout)}
            if pres.returncode != 0:
                entry["pipeline"]["stderr"] = pres.stderr[-1500:]
        self.step("bench", **entry)
        return entry

    # -- restore ------------------------------------------------------------------------------------------------
    def restore(self):
        for name in self.started:
            if inspect(name) is not None:
                self.save_log(name)
                sh(["docker", "rm", "-f", name], check=False)
        self.started = []
        if not self.stopped_vllm:
            return
        name = self.vllm["name"]
        log(f"restoring {name}")
        report = {"method": None}
        if inspect(name) is not None:
            report["method"] = "docker start (the same container)"
            res = sh(["docker", "start", name], check=False)
            if res.returncode != 0:
                report["start_error"] = res.stderr.strip()[-400:]
                report["method"] = "run.sh start (docker start failed)"
                sh(["bash", str(RUN_SH), "start"], check=False)
        else:
            report["method"] = "run.sh start (the container was gone)"
            sh(["bash", str(RUN_SH), "start"], check=False)
        t0 = time.time()
        wait = sh(["bash", str(RUN_SH), "wait", str(self.args.restore_timeout)], check=False)
        report["healthy"] = wait.returncode == 0
        report["wait_seconds"] = round(time.time() - t0, 1)
        after = inspect(name)
        report["differences"] = [".".join(p) for p in COMPARED
                                 if field(after, p) != field(self.original, p)] if after else ["container missing"]
        if report["method"].startswith("run.sh"):
            report["note"] = "a new container (new Id) from run.sh; compare the other fields"
        self.stopped_vllm = not report["healthy"]
        self.restore_report = report
        self.step("restore", **report)
        if not report["healthy"]:
            log(f"ANSWER MODEL NOT HEALTHY: run `bash {RUN_SH} status` and `docker logs {name}`")

    # -- the two modes ------------------------------------------------------------------------------------------
    def preflight(self):
        self.out.mkdir(parents=True, exist_ok=True)
        os.chmod(self.out, 0o700)
        sh(["docker", "version", "--format", "{{.Server.Version}}"])
        self.vllm = vllm_settings()
        doc = inspect(self.vllm["name"])
        if doc is None:
            raise Abort(f"container {self.vllm['name']} does not exist; nothing to compare against")
        self.original = doc
        write_private(self.out / "vllm-inspect.redacted.json", json.dumps(redacted(doc), indent=1) + "\n")
        problems = reproduction_problems(doc, self.vllm["argv"])
        health = field(doc, ("State", "Health", "Status"))
        self.step("capture", container=self.vllm["name"], id=doc["Id"][:12], running=doc["State"]["Running"],
                  health=health, reproduced_by_run_sh=not problems, problems=problems)
        return doc, problems, health

    def run_main(self):
        doc, problems, health = self.preflight()
        model_dir = Path(next(m["Source"] for m in doc["Mounts"] if m["Destination"].startswith("/models/")))
        cmd = doc["Config"]["Cmd"]
        opt = lambda flag, default=None: cmd[cmd.index(flag) + 1] if flag in cmd else default  # noqa: E731
        limits = {"max_len": int(opt("--max-model-len")), "max_batch": int(opt("--max-num-seqs")),
                  "max_tokens": int(opt("--max-num-batched-tokens", 8192)),
                  "kv_bytes": int(opt("--kv-cache-memory-bytes"))}
        limits["kv_tokens"] = kv_tokens(model_dir, limits["kv_bytes"])
        probe = self.trt_probe(self.args.image)
        parser = "qwen3" if probe["tool_parsers"] and "qwen3" in probe["tool_parsers"].split("|") else None
        argv = self.trt_args(name=TRT_CONTAINER, model_dir=model_dir, served=self.vllm["served"],
                             port=self.args.port, max_len=limits["max_len"], max_batch=limits["max_batch"],
                             max_tokens=limits["max_tokens"], kv_tok=limits["kv_tokens"], tool_parser=parser,
                             ptxas=probe["ptxas_in_image"], mem_limit="96g")
        mem = mem_available_gb()
        self.step("plan", limits=limits, trt_probe=probe, trt_run=shlex.join(argv), mem_available_now_gb=round(mem, 1),
                  min_free_gb=self.args.min_free_gb, need_after_stop_gb=self.args.need_gb)
        blockers = list(problems)
        if probe["missing_flags"]:
            blockers.append(f"trtllm-serve lacks {probe['missing_flags']}")
        if not doc["State"]["Running"] or health != "healthy":
            blockers.append(f"{self.vllm['name']} is not running and healthy ({health})")
        if mem < self.args.min_free_gb:
            blockers.append(f"MemAvailable {mem:.1f} GB < --min-free-gb {self.args.min_free_gb}")
        if sh(["docker", "inspect", TRT_CONTAINER], check=False).returncode == 0:
            blockers.append(f"a container named {TRT_CONTAINER} already exists")
        self.results["blockers"] = blockers
        if self.args.dry_run:
            self.step("dry-run", would_run=not blockers, blockers=blockers)
            print(json.dumps({"dry_run": True, "blockers": blockers, "trt_run": shlex.join(argv),
                              "limits": limits, "probe": probe, "out_dir": str(self.out)}, indent=1))
            return 0 if not blockers else 3
        if blockers:
            raise Abort("refusing to start: " + "; ".join(blockers))
        key_file = str(Path(os.environ.get("NSF_INFERENCE_DIR", Path.home() / ".config/neuroscifi/inference"))
                       / "api_key")
        if not self.args.skip_baseline:
            self.bench("vllm", self.vllm["url"], self.vllm["served"], key_file, self.args.scenarios)
        log(f"stopping {self.vllm['name']} (the answer model is down from here until the restore)")
        self.stopped_vllm = True
        self.results["downtime_started"] = time.time()
        sh(["docker", "stop", "-t", "30", self.vllm["name"]])
        time.sleep(10)
        mem = mem_available_gb()
        if mem < self.args.need_gb:
            raise Abort(f"after stopping vLLM MemAvailable is {mem:.1f} GB < --need-gb {self.args.need_gb}")
        self.started.append(TRT_CONTAINER)
        sh(argv)
        t_load = self.wait_health(TRT_CONTAINER, f"http://127.0.0.1:{self.args.port}", self.args.health_timeout)
        self.step("trtllm-ready", seconds=t_load, mem_available_gb_loaded=round(mem_available_gb(), 1))
        self.save_log(TRT_CONTAINER)
        self.bench("trtllm", f"http://127.0.0.1:{self.args.port}/v1", self.vllm["served"], None,
                   self.args.scenarios)
        return 0

    def run_proxy(self):
        """A small model on both engines in turn, each capped; the answer model is never touched."""
        self.out.mkdir(parents=True, exist_ok=True)
        os.chmod(self.out, 0o700)
        model_dir = Path(self.args.proxy).resolve()
        served = model_dir.name
        mem = mem_available_gb()
        if mem < self.args.proxy_min_free_gb:
            raise Abort(f"MemAvailable {mem:.1f} GB < {self.args.proxy_min_free_gb} GB: proxy skipped")
        vimg = sh(["docker", "inspect", "-f", "{{.Config.Image}}", "neuroscifi-vllm"], check=False).stdout.strip() \
            or vllm_image_from_env()
        probe = self.trt_probe(self.args.image)
        self.step("proxy-plan", model=served, vllm_image=vimg, trt_probe=probe, mem_available_gb=round(mem, 1))
        if self.args.dry_run:
            return 0
        total = int(Path("/proc/meminfo").read_text().split()[1]) / 2**20
        kv_bytes = 2 * 2**30
        engines = [
            ("vllm", "neuroscifi-trtllm-proxy-vllm", self.args.port + 1, [
                "docker", "run", "-d", "--name", "neuroscifi-trtllm-proxy-vllm", "--device", "nvidia.com/gpu=all",
                "-p", f"127.0.0.1:{self.args.port + 1}:8000", "--user", f"{os.getuid()}:{os.getgid()}",
                # exec: Triton and Inductor load the kernels they compile from this cache (Docker's tmpfs is noexec).
                "--read-only", "--tmpfs", "/tmp:rw,exec,size=4g,mode=1777", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", "12g", "--shm-size", "2g",
                "-v", f"{model_dir}:/models/{served}:ro", "-e", "HOME=/tmp", "-e", "HF_HUB_OFFLINE=1",
                "-e", "VLLM_NO_USAGE_STATS=1", "-e", "VLLM_CACHE_ROOT=/tmp/vllm", "-e", "TRITON_CACHE_DIR=/tmp/triton",
                "-e", "TORCHINDUCTOR_CACHE_DIR=/tmp/inductor",
                vimg, f"/models/{served}", "--served-model-name", served, "--host", "0.0.0.0", "--port", "8000",
                "--gpu-memory-utilization", f"{min(0.9, self.args.proxy_mem_gb / total):.3f}",
                "--kv-cache-memory-bytes", str(kv_bytes), "--max-model-len", "8192", "--max-num-seqs", "8",
                "--max-num-batched-tokens", "4096", "--enable-auto-tool-choice", "--tool-call-parser", "hermes",
                "--enable-prompt-tokens-details"]),
            ("trtllm", "neuroscifi-trtllm-proxy-trt", self.args.port + 2, None),
        ]
        parser = "qwen3" if probe["tool_parsers"] and "qwen3" in probe["tool_parsers"].split("|") else None
        engines[1] = ("trtllm", "neuroscifi-trtllm-proxy-trt", self.args.port + 2, self.trt_args(
            name="neuroscifi-trtllm-proxy-trt", model_dir=model_dir, served=served, port=self.args.port + 2,
            max_len=8192, max_batch=8, max_tokens=4096, kv_tok=kv_tokens(model_dir, kv_bytes), tool_parser=parser,
            ptxas=probe["ptxas_in_image"], mem_limit="12g"))
        wanted = [e for e in self.args.proxy_engines.split(",") if e]
        for label, name, port, argv in [e for e in engines if e[0] in wanted]:
            if inspect(name) is not None:
                raise Abort(f"container {name} already exists")
            before = mem_available_gb()
            self.started.append(name)
            sh(argv)
            try:
                t = self.wait_health(name, f"http://127.0.0.1:{port}", self.args.health_timeout)
                self.step(f"proxy-{label}-ready", seconds=t, mem_used_gb=round(before - mem_available_gb(), 1))
                self.bench(f"proxy-{label}", f"http://127.0.0.1:{port}/v1", served, None,
                           self.args.scenarios or "short,evidence,repeat,structured,concurrency,mixed,reasoning,tools")
            except Abort as exc:
                self.step(f"proxy-{label}-failed", error=str(exc))
            finally:
                self.save_log(name)
                sh(["docker", "rm", "-f", name], check=False)
                self.started.remove(name)
        return 0


def vllm_image_from_env():
    for line in (ROOT / "deploy" / "vllm" / "vllm.env").read_text().splitlines():
        if line.startswith("NSF_VLLM_IMAGE="):
            return line.split("=", 1)[1]
    raise Abort("no NSF_VLLM_IMAGE")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="check and plan; stop and start nothing")
    ap.add_argument("--yes", action="store_true", help="really stop the answer model for the window")
    ap.add_argument("--proxy", metavar="MODEL_DIR", help="compare the engines on this small model instead")
    ap.add_argument("--image", default=TRT_IMAGE)
    ap.add_argument("--port", type=int, default=8812, help="TensorRT-LLM's loopback port (proxy uses +1, +2)")
    ap.add_argument("--out-dir", default=str(Path.home() / ".cache/neuroscifi/trtllm-compare"
                                             / time.strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--runs", type=int, default=8, help="bench_inference.py --runs")
    ap.add_argument("--scenarios", default="", help="bench_inference.py --scenarios (default all)")
    ap.add_argument("--no-pipeline", action="store_true", help="skip scripts/bench_pipeline.py")
    ap.add_argument("--quick-pipeline", action="store_true", help="bench_pipeline.py --quick (7 questions)")
    ap.add_argument("--skip-baseline", action="store_true", help="do not re-measure vLLM before the window")
    ap.add_argument("--min-free-gb", type=float, default=12.0, help="refuse to begin below this MemAvailable")
    ap.add_argument("--need-gb", type=float, default=48.0,
                    help="abort (and restore) if less is available after stopping vLLM")
    ap.add_argument("--health-timeout", type=int, default=2400, help="seconds for TensorRT-LLM to load")
    ap.add_argument("--restore-timeout", type=int, default=1800, help="seconds for vLLM to be healthy again")
    ap.add_argument("--proxy-mem-gb", type=float, default=8.0)
    ap.add_argument("--proxy-min-free-gb", type=float, default=20.0)
    ap.add_argument("--proxy-engines", default="vllm,trtllm", help="which engines the proxy runs, in this order")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.yes:
        ap.error("this stops the answer model (or, with --proxy, starts two small servers): pass --yes, "
                 "or --dry-run to check")
    run = Run(args)

    def on_signal(signum, _frame):
        raise Abort(f"signal {signal.Signals(signum).name}")

    caught = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {s: signal.getsignal(s) for s in caught}
    for s in caught:
        signal.signal(s, on_signal)
    code = 1
    try:
        code = run.run_proxy() if args.proxy else run.run_main()
    except Abort as exc:
        run.step("abort", error=str(exc))
        code = 2
    finally:
        # The restore itself must not be cut short by a second Ctrl-C.
        for s in caught:
            signal.signal(s, signal.SIG_IGN)
        try:
            run.restore()
        finally:
            if "downtime_started" in run.results:
                run.results["downtime_seconds"] = round(time.time() - run.results.pop("downtime_started"))
            if run.out.exists():
                run.save()
                log(f"results in {run.out}")
            for s in caught:
                signal.signal(s, previous[s])
    if run.stopped_vllm:
        return 4
    return code


if __name__ == "__main__":
    sys.exit(main())
