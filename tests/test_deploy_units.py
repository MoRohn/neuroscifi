"""Deployment files carry no personal account or home path, and every voice image dependency is pinned."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNITS = sorted((ROOT / "deploy" / "systemd").glob("*.service"))
DOCKER_USER_UNITS = ("neuroscifi-vllm.service", "neuroscifi-voice-asr.service", "neuroscifi-voice-tts.service",
                     "neuroscifi-memory-embed.service", "neuroscifi-memory-rerank.service")
MEMORY_UNITS = {"neuroscifi-memory-embed.service": "embed", "neuroscifi-memory-rerank.service": "rerank"}


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_units_name_no_home_directory(unit):
    for line in unit.read_text().splitlines():
        if line.startswith("#"):
            continue
        assert "/home/" not in line and "/root/" not in line, f"{unit.name}: {line}"


@pytest.mark.parametrize("name", DOCKER_USER_UNITS)
def test_docker_user_units_are_templates(name):
    text = (ROOT / "deploy" / "systemd" / name).read_text()
    assert re.search(r"^User=@NSF_USER@$", text, re.M), "User= must be the @NSF_USER@ placeholder"
    assert re.search(r"^Environment=NSF_REPO=@NSF_REPO@$", text, re.M), "NSF_REPO must be the @NSF_REPO@ placeholder"


def test_install_sh_renders_the_vllm_placeholders():
    text = (ROOT / "deploy" / "install.sh").read_text()
    assert "s#^User=.*#User=$LLM_USER#" in text and "s#^Environment=NSF_REPO=.*#Environment=NSF_REPO=$SOURCE#" in text


@pytest.mark.parametrize("name,service", sorted(MEMORY_UNITS.items()))
def test_memory_units_run_the_deploy_script_in_the_foreground_under_the_voice_units_sandbox(name, service):
    """systemd supervises the wrapper, so it must run attached (`foreground`, a command run.sh has) and stop with
    the script's own stop; the wrapper sandbox is the one verified for the speech units, line for line."""
    text = (ROOT / "deploy" / "systemd" / name).read_text()
    assert f"ExecStart=/usr/bin/env bash ${{NSF_REPO}}/deploy/memory/run.sh foreground {service}\n" in text
    assert f"ExecStop=/usr/bin/env bash ${{NSF_REPO}}/deploy/memory/run.sh stop {service}\n" in text
    script = (ROOT / "deploy" / "memory" / "run.sh").read_text()
    assert re.search(r"^  foreground\)$", script, re.M) and 'exec docker run --rm --init "${ARGS[@]}"' in script

    def sandbox(unit):
        lines = (ROOT / "deploy" / "systemd" / unit).read_text().splitlines()
        return [line for line in lines[lines.index("[Service]"):] if line and not line.startswith("#")
                and not line.startswith(("ExecStart=", "ExecStop=", "TimeoutStartSec="))]
    assert sandbox(name) == sandbox("neuroscifi-voice-asr.service")


def test_install_sh_installs_the_memory_units_only_when_asked_and_renders_their_placeholders(tmp_path):
    import subprocess
    script = str(ROOT / "deploy" / "install.sh")
    plain = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path)], capture_output=True, text=True)
    assert plain.returncode == 0 and "neuroscifi-memory-" not in plain.stdout
    asked = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path), "--memory", "--memory-user",
                            "nsfmodels"], capture_output=True, text=True)
    assert asked.returncode == 0
    for name in MEMORY_UNITS:
        line = next(row for row in asked.stdout.splitlines() if row.startswith("+ sed") and f"/{name} >" in row)
        assert "'s#^User=.*#User=nsfmodels#'" in line and "'s#^Environment=NSF_REPO=.*#Environment=NSF_REPO=" in line
    assert "installed disabled" in asked.stdout and not list(tmp_path.iterdir())      # a dry run writes nothing
    for user in ("root", "two words", ""):
        refused = subprocess.run(["bash", script, "--dry-run", "--memory", "--memory-user", user], capture_output=True,
                                 text=True, env={"PATH": "/usr/bin:/bin"})
        assert refused.returncode == 1 and "--memory" in refused.stderr


def test_the_memory_script_knows_its_services_and_refuses_any_other(tmp_path):
    """`foreground` is what the units run: a wrong service name must fail before docker is touched."""
    import subprocess
    script = str(ROOT / "deploy" / "memory" / "run.sh")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    assert subprocess.run(["bash", "-n", script]).returncode == 0
    for argv in (["foreground"], ["foreground", "all"], ["foreground", "vllm"], ["start", "vllm"], ["nonsense"]):
        done = subprocess.run(["bash", script, *argv], capture_output=True, text=True, env=env)
        assert done.returncode == 1 and done.stderr.startswith("run.sh: "), argv
    assert not (tmp_path / ".config").exists()             # refused before a key or a folder was created


def _memory_wait(tmp_path, warm_exit=0, health_exit=0, status="running", restarts=("0",)):
    """`deploy/memory/run.sh wait all 30` with a docker that reports the given container status and restart counts
    (in turn; the last repeats), a curl that answers /health with `health_exit`, records every command line it was
    given and what it was fed on standard input, and a sleep that returns at once."""
    import os
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (tmp_path / "restarts").write_text("\n".join(restarts) + "\n")
    (bin_dir / "docker").write_text(
        '#!/usr/bin/env bash\n'
        f'R="{tmp_path}/restarts"\n'
        'case "$*" in\n'
        '  *RestartCount*) head -n1 "$R"; [ "$(wc -l < "$R")" -gt 1 ] && sed -i 1d "$R"; exit 0 ;;\n'
        '  *State.Running*) echo true ;;\n'
        f'  port*) [ -e "{tmp_path}/squatted" ] && exit 0\n'
        '     case "$*" in *embed*) echo 127.0.0.1:18441 ;; *) echo 127.0.0.1:18442 ;; esac ;;\n'
        f'  *) echo {status} ;;\n'
        'esac\n')
    (bin_dir / "curl").write_text(
        '#!/usr/bin/env bash\n'
        f'echo "$*" >> "{tmp_path}/curl.args"\n'
        f'case "$*" in *"-K -"*) cat >> "{tmp_path}/curl.stdin"; exit {warm_exit} ;; esac\n'
        f'exit {health_exit}\n')
    (bin_dir / "sleep").write_text(f'#!/usr/bin/env bash\necho x >> "{tmp_path}/polls"\n')
    for tool in ("docker", "curl", "sleep"):
        (bin_dir / tool).chmod(0o755)
    key_dir = tmp_path / ".config" / "neuroscifi" / "memory"
    key_dir.mkdir(parents=True)
    (key_dir / "api_key").write_text("nsfm-wait-test-key\n")
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    return subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "wait", "all", "30"], env=env,
                          capture_output=True, text=True, timeout=60)


def test_memory_wait_sends_one_real_request_per_model_and_never_puts_the_key_on_a_command_line(tmp_path):
    """Ready means "answered a real request": a model's first request after a start took 1.0 to 1.5 s on the
    reference host and a lookup allows 2 s. The key reaches curl on standard input, not in argv (`ps` shows argv)."""
    done = _memory_wait(tmp_path)
    assert done.returncode == 0 and re.fullmatch(r"embed ready after \ds\nrerank ready after \ds\n", done.stdout)
    args = (tmp_path / "curl.args").read_text()
    assert "http://127.0.0.1:18441/v1/embeddings" in args and "http://127.0.0.1:18442/v1/rerank" in args
    assert '"input":["warm up"]' in args and '"query":"warm up","documents":["warm up"]' in args
    assert "nsfm-wait-test-key" not in args and "Bearer" not in args
    assert (tmp_path / "curl.stdin").read_text().count('header = "Authorization: Bearer nsfm-wait-test-key"') == 2
    assert "nsfm-wait-test-key" not in done.stdout + done.stderr


def test_memory_wait_fails_at_once_on_a_container_that_stopped_or_keeps_restarting(tmp_path):
    """With --restart unless-stopped a crash-looping container keeps existing: only its state or a growing restart
    count shows that it will never answer. The wait must not sit out its limit (the idle policy's reload calls it)."""
    one, two = tmp_path / "one", tmp_path / "two"
    done = _memory_wait(one, health_exit=7, status="restarting")
    assert done.returncode == 1 and "embed container is restarting" in done.stderr
    assert not (one / "polls").exists()                            # on the first look
    done = _memory_wait(two, health_exit=7, restarts=("2", "3"))
    assert done.returncode == 1 and "embed restarted while starting" in done.stderr
    assert not (two / "curl.stdin").exists()                       # and no warm-up request was sent to it


@pytest.mark.parametrize("script,ready", [(("deploy/memory/run.sh", "wait", "embed", "60"), "embed ready after"),
                                          (("deploy/voice/run.sh", "wait", "asr", "60"), "asr ready after"),
                                          (("deploy/vllm/run.sh", "wait", "60"), "healthy after")])
def test_wait_treats_a_container_that_does_not_exist_yet_as_not_ready_then_as_gone(tmp_path, script, ready):
    """Review F2: `systemctl start` of a Type=simple unit returns before the wrapper has created the container, and
    the wait that the idle policy runs next died at once with "container is gone", so every reload of a stopped
    unit was recorded as failed. A missing container is now waited for (60 s), then it is gone."""
    import os
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "n").write_text("0")
    (bin_dir / "docker").write_text(                      # no such container for the first three looks, then running
        '#!/usr/bin/env bash\n'
        f'N="{tmp_path}/n"\n'
        'case "$*" in\n'
        '  *"{{.State.Status}}"*) c=$(cat "$N"); echo $((c+1)) > "$N"; [ "$c" -lt "${APPEARS_AFTER:-3}" ] && exit 1\n'
        '     case "$*" in *RestartCount*) echo "running 0" ;; *) echo running ;; esac ;;\n'
        '  *RestartCount*) echo 0 ;;\n'
        '  port*) echo "127.0.0.1:18441" ;;\n'
        '  *) echo true ;;\n'
        'esac\n')
    (bin_dir / "curl").write_text(                         # /health answers once the container has been seen twice
        '#!/usr/bin/env bash\n'
        f'case "$*" in *"-K -"*) cat > /dev/null; exit 0 ;; esac\n'
        f'[ "$(cat "{tmp_path}/n")" -ge 5 ]\n')
    (bin_dir / "sleep").write_text(f'#!/usr/bin/env bash\necho x >> "{tmp_path}/polls"\n')
    for tool in ("docker", "curl", "sleep"):
        (bin_dir / tool).chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    done = subprocess.run(["bash", str(ROOT / script[0]), *script[1:]], env=env, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0 and ready in done.stdout, done.stderr
    assert (tmp_path / "polls").read_text().count("x") >= 3            # it kept looking while there was none
    (tmp_path / "n").write_text("0")
    gone = subprocess.run(["bash", str(ROOT / script[0]), *script[1:]], capture_output=True, text=True, timeout=60,
                          env=env | {"APPEARS_AFTER": "1000000", "NSF_WAIT_GONE_GRACE": "0"})
    assert gone.returncode == 1 and "none after 0s" in gone.stderr


def test_memory_status_prints_one_line_per_service_running_or_not(tmp_path):
    import os
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(                      # embed runs; for rerank docker prints an empty line and fails
        '#!/usr/bin/env bash\n'
        'case "$*" in *neuroscifi-memory-embed*) echo "/neuroscifi-memory-embed running health=healthy started=T" ;;\n'
        '  *) echo; exit 1 ;; esac\n')
    (bin_dir / "docker").chmod(0o755)
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "status"], capture_output=True, text=True,
                          env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}, timeout=60)
    assert done.returncode == 0 and done.stdout.splitlines() == [
        "/neuroscifi-memory-embed running health=healthy started=T", "neuroscifi-memory-rerank: not running"]


def test_memory_wait_never_sends_the_key_to_a_port_the_container_does_not_publish(tmp_path):
    """Review L2: something answers /health on the port, and Docker does not report the container publishing it
    (another account's process took the free port). The wait fails and no request carried the key."""
    tmp_path.joinpath("squatted").write_text("")
    done = _memory_wait(tmp_path)
    assert done.returncode == 1 and "another process holds the port. The key was not sent" in done.stderr
    assert not (tmp_path / "curl.stdin").exists()
    assert "nsfm-wait-test-key" not in (tmp_path / "curl.args").read_text() + done.stdout + done.stderr


def test_memory_wait_does_not_fail_when_only_the_warm_up_request_fails(tmp_path):
    done = _memory_wait(tmp_path, warm_exit=22)
    assert done.returncode == 0
    assert len(re.findall(r"ready after \ds \(its warm-up request failed; the first lookup will be slower\)",
                          done.stdout)) == 2


def test_tts_image_pins_every_package():
    text = (ROOT / "deploy" / "voice" / "Dockerfile.tts").read_text()
    run = text[text.index("RUN pip install"):text.index("&& python3 -c")]
    for token in re.findall(r'"([^"]+)"|\s(regex)\s', run):
        spec = token[0] or token[1]
        assert "==" in spec or "@ https://" in spec, f"unpinned: {spec}"
    assert re.search(r'"regex==\d', run), "regex must be pinned"
    wheel = re.search(r'en_core_web_sm @ (\S+?)"', run).group(1)
    assert re.search(r"#sha256=[0-9a-f]{64}$", wheel), "the spaCy model wheel must be pinned by sha256"
