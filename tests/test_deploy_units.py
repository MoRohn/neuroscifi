"""Deployment files carry no personal account or home path, and every voice image dependency is pinned."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNITS = sorted((ROOT / "deploy" / "systemd").glob("*.service"))
DOCKER_USER_UNITS = ("neuroscifi-vllm.service", "neuroscifi-voice-asr.service", "neuroscifi-voice-tts.service",
                     "neuroscifi-memory-embed.service", "neuroscifi-memory-rerank.service",
                     "neuroscifi-memory.service", "neuroscifi-wake.service")
# unit -> the service name its run.sh commands carry ("all": the one server that holds both models)
MEMORY_UNITS = {"neuroscifi-memory-embed.service": "embed", "neuroscifi-memory-rerank.service": "rerank",
                "neuroscifi-memory.service": "all"}
MEMORY_SERVERS = ("single", "vllm")       # deploy/memory/memory.env: NSF_MEMORY_SERVER
# What `deploy/install.sh --memory` installs, by NSF_MEMORY_SERVER (memory.env, or --memory-server).
INSTALLED_BY_MEMORY_FLAG = {"single": ("neuroscifi-memory.service",),
                            "vllm": ("neuroscifi-memory-embed.service", "neuroscifi-memory-rerank.service")}


def _memory_env(tmp_path, server, extra=None):
    """The environment of a run.sh call that serves the models with `server`: the owner's override file, in a
    settings folder of its own (the script reads it after its shipped memory.env)."""
    import os
    folder = tmp_path / ".config" / "neuroscifi" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "memory.env").write_text(f"NSF_MEMORY_SERVER={server}\n")
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), **(extra or {})}


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


def test_install_sh_follows_the_owners_override_of_the_memory_server(tmp_path):
    """F10: the installer read NSF_MEMORY_SERVER from the shipped memory.env only, so with the owner's override
    set to vllm it installed the single server's unit, which then refused to start. It now reads the override in
    the memory account's home as run.sh does: as data, only a plain file that account owns."""
    import getpass
    import os
    import subprocess
    me = getpass.getuser()
    home = tmp_path / "home"
    folder = home / ".config" / "neuroscifi" / "memory"
    folder.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "getent").write_text(f'#!/usr/bin/env bash\necho "$2:x:1:1::{home}:/bin/bash"\n')
    (bin_dir / "getent").chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    script = str(ROOT / "deploy" / "install.sh")

    def installed(user):
        done = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path / "units"), "--memory",
                               "--memory-user", user], capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
        return [name for name in ("neuroscifi-memory.service", "neuroscifi-memory-embed.service",
                                  "neuroscifi-memory-rerank.service") if f"/{name} >" in done.stdout], done.stdout
    for line, server in (("NSF_MEMORY_SERVER=vllm", "vllm"), ("export NSF_MEMORY_SERVER='vllm'  # two", "vllm"),
                         ('NSF_MEMORY_SERVER="single"', "single")):
        (folder / "memory.env").write_text(f"NSF_MEMORY_PORT_NOTE=1\n{line}\n")
        assert installed(me)[0] == list(INSTALLED_BY_MEMORY_FLAG[server]), line
    (folder / "memory.env").write_text("NSF_MEMORY_SERVER=vllm\n")
    units, said = installed("nsfmodels")                  # the file belongs to another account: not read
    assert units == list(INSTALLED_BY_MEMORY_FLAG["single"]) and "is not owned by nsfmodels" in said
    (folder / "memory.env").unlink()
    (tmp_path / "elsewhere.env").write_text("NSF_MEMORY_SERVER=vllm\n")
    (folder / "memory.env").symlink_to(tmp_path / "elsewhere.env")    # a link is never followed as root
    assert installed(me)[0] == list(INSTALLED_BY_MEMORY_FLAG["single"])


def test_install_sh_installs_the_memory_units_only_when_asked_and_renders_their_placeholders(tmp_path):
    import subprocess
    script = str(ROOT / "deploy" / "install.sh")
    plain = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path)], capture_output=True, text=True)
    assert plain.returncode == 0 and "neuroscifi-memory-" not in plain.stdout
    shipped = re.search(r"^NSF_MEMORY_SERVER=(\w+)", (ROOT / "deploy" / "memory" / "memory.env").read_text(), re.M)[1]
    for server, extra in ((shipped, []), ("single", ["--memory-server", "single"]), ("vllm", ["--memory-server", "vllm"])):
        asked = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path), "--memory", "--memory-user",
                                "nsfmodels", *extra], capture_output=True, text=True)
        assert asked.returncode == 0, asked.stderr
        for name in INSTALLED_BY_MEMORY_FLAG[server]:
            line = next(row for row in asked.stdout.splitlines() if row.startswith("+ sed") and f"/{name} >" in row)
            assert "'s#^User=.*#User=nsfmodels#'" in line and "'s#^Environment=NSF_REPO=.*#Environment=NSF_REPO=" in line
        other = INSTALLED_BY_MEMORY_FLAG["vllm" if server == "single" else "single"]
        assert not any(f"/{name} >" in asked.stdout for name in other)          # never both kinds
        assert "installed disabled" in asked.stdout and not list(tmp_path.iterdir())  # a dry run writes nothing
    bad = subprocess.run(["bash", script, "--dry-run", "--memory", "--memory-user", "nsfmodels", "--memory-server",
                          "both"], capture_output=True, text=True)
    assert bad.returncode == 1 and "--memory-server" in bad.stderr
    for user in ("root", "two words", ""):
        refused = subprocess.run(["bash", script, "--dry-run", "--memory", "--memory-user", user], capture_output=True,
                                 text=True, env={"PATH": "/usr/bin:/bin"})
        assert refused.returncode == 1 and "--memory" in refused.stderr


@pytest.mark.parametrize("server,refused", [
    ("vllm", (["foreground"], ["foreground", "all"], ["foreground", "vllm"], ["start", "vllm"], ["nonsense"])),
    ("single", (["foreground", "vllm"], ["foreground", "embed"], ["foreground", "rerank"], ["start", "vllm"],
                ["restart", "both"], ["stop", "x"], ["nonsense"]))])
def test_the_memory_script_knows_its_services_and_refuses_any_other(tmp_path, server, refused):
    """`foreground` is what the units run: a wrong service name must fail before docker is touched. (With one
    server for both models `foreground` and `foreground all` are that server; with two, each needs its name.)"""
    import subprocess
    script = str(ROOT / "deploy" / "memory" / "run.sh")
    env = _memory_env(tmp_path, server) | {"PATH": "/usr/bin:/bin"}
    folder = tmp_path / ".config" / "neuroscifi" / "memory"
    assert subprocess.run(["bash", "-n", script]).returncode == 0
    for argv in refused:
        done = subprocess.run(["bash", script, *argv], capture_output=True, text=True, env=env)
        assert done.returncode == 1 and done.stderr.startswith("run.sh: "), argv
        if server == "single" and argv in (["foreground", "embed"], ["foreground", "rerank"]):
            # the installer's per-model units would each replace the other's one container: refused, by name
            assert "use neuroscifi-memory.service (foreground all)" in done.stderr
    assert sorted(f.name for f in folder.iterdir()) == ["memory.env"]      # refused before a key was created


def test_the_memory_script_refuses_a_server_it_does_not_know(tmp_path):
    import subprocess
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "status"], capture_output=True,
                          text=True, env=_memory_env(tmp_path, "triton"))
    assert done.returncode == 1 and "NSF_MEMORY_SERVER must be single or vllm (got triton)" in done.stderr


def _memory_wait(tmp_path, warm_exit=0, health_exit=0, status="running", restarts=("0",), server="vllm"):
    """`deploy/memory/run.sh wait all 30` with a docker that reports the given container status and restart counts
    (in turn; the last repeats), a curl that answers /health with `health_exit`, records every command line it was
    given and what it was fed on standard input, and a sleep that returns at once. `server`: two vLLM containers
    (each publishes its port 8000), or the one container of the single server (8000 embeddings, 8001 reranker)."""
    import os
    import subprocess
    published = ('case "$*" in *embed*) echo 127.0.0.1:18441 ;; *) echo 127.0.0.1:18442 ;; esac' if server == "vllm"
                 else 'case "$*" in "port neuroscifi-memory 8000/tcp") echo 127.0.0.1:18441 ;; '
                      '"port neuroscifi-memory 8001/tcp") echo 127.0.0.1:18442 ;; esac')
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
        f'     {published} ;;\n'
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
    env = _memory_env(tmp_path, server, {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    (tmp_path / ".config" / "neuroscifi" / "memory" / "api_key").write_text("nsfm-wait-test-key\n")
    return subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "wait", "all", "30"], env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("server", MEMORY_SERVERS)
def test_memory_wait_sends_one_real_request_per_model_and_never_puts_the_key_on_a_command_line(tmp_path, server):
    """Ready means "answered a real request": a model's first request after a start took 1.0 to 1.5 s on the
    reference host and a lookup allows 2 s. The key reaches curl on standard input, not in argv (`ps` shows argv)."""
    done = _memory_wait(tmp_path, server=server)
    assert done.returncode == 0 and re.fullmatch(r"embed ready after \ds\nrerank ready after \ds\n", done.stdout)
    args = (tmp_path / "curl.args").read_text()
    assert "http://127.0.0.1:18441/v1/embeddings" in args and "http://127.0.0.1:18442/v1/rerank" in args
    assert '"input":["warm up"]' in args and '"query":"warm up","documents":["warm up"]' in args
    assert "nsfm-wait-test-key" not in args and "Bearer" not in args
    assert (tmp_path / "curl.stdin").read_text().count('header = "Authorization: Bearer nsfm-wait-test-key"') == 2
    assert "nsfm-wait-test-key" not in done.stdout + done.stderr


@pytest.mark.parametrize("server", MEMORY_SERVERS)
def test_memory_wait_fails_at_once_on_a_container_that_stopped_or_keeps_restarting(tmp_path, server):
    """With --restart unless-stopped a crash-looping container keeps existing: only its state or a growing restart
    count shows that it will never answer. The wait must not sit out its limit (the idle policy's reload calls it)."""
    one, two = tmp_path / "one", tmp_path / "two"
    done = _memory_wait(one, health_exit=7, status="restarting", server=server)
    assert done.returncode == 1 and "embed container is restarting" in done.stderr
    assert not (one / "polls").exists()                            # on the first look
    done = _memory_wait(two, health_exit=7, restarts=("2", "3"), server=server)
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


def test_the_wake_units_watch_one_folder_run_only_the_wake_command_and_share_the_idle_units_sandbox():
    path = (ROOT / "deploy" / "systemd" / "neuroscifi-wake.path").read_text()
    unit = (ROOT / "deploy" / "systemd" / "neuroscifi-wake.service").read_text()
    assert "PathExistsGlob=@NSF_WAKE_DIR@/*.wake\n" in path and "Unit=neuroscifi-wake.service\n" in path
    assert 'resources wake --db "${NSF_DB}" --dir "@NSF_WAKE_DIR@"' in unit and "Type=oneshot\n" in unit

    def sandbox(text):
        return text[text.index("NoNewPrivileges=yes"):]
    assert sandbox(unit) == sandbox((ROOT / "deploy" / "systemd" / "neuroscifi-idle.service").read_text())
    # a stuck entry is the command's to handle (it holds 30 s): no trigger limit that would stop the watch for good
    assert "TriggerLimit" not in "".join(line for line in path.splitlines() if not line.startswith("#"))


def test_install_sh_installs_the_idle_and_wake_units_only_when_asked_with_their_request_folder(tmp_path):
    """Second-pass review S2: both were installed by hand, and nothing created the request folder with modes that
    let the service user ask and the docker user serve."""
    import subprocess
    script = str(ROOT / "deploy" / "install.sh")
    plain = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path)], capture_output=True, text=True)
    assert plain.returncode == 0
    assert "neuroscifi-idle" not in plain.stdout and "neuroscifi-wake" not in plain.stdout     # also: no wake folder
    asked = subprocess.run(["bash", script, "--dry-run", "--unit-dir", str(tmp_path), "--idle", "--idle-user",
                            "nsfmodels"], capture_output=True, text=True)
    assert asked.returncode == 0, asked.stderr
    out = asked.stdout
    # the folder: the serving account owns it, the service user's group may create files in it, setgid, nobody else;
    # sticky, so a request is removed by whoever left it or by the owner
    assert "+ install -d -m 3770 -o nsfmodels -g neuroscifi /var/lib/neuroscifi-wake\n" in out
    sed = {name: next(row for row in out.splitlines() if row.startswith("+ sed") and f"/{name} >" in row)
           for name in ("neuroscifi-idle.service", "neuroscifi-idle.timer", "neuroscifi-wake.service",
                        "neuroscifi-wake.path", "neuroscifi-console.service", "neuroscifi-webcortex.service",
                        "neuroscifi-pocket.service", "neuroscifi-checkpoint.service")}
    for name in ("neuroscifi-idle.service", "neuroscifi-wake.service", "neuroscifi-wake.path"):
        assert "'s#@NSF_USER@#nsfmodels#g'" in sed[name] and "'s#@NSF_WAKE_DIR@#/var/lib/neuroscifi-wake#g'" in sed[name]
        assert "'s#@NSF_REPO@#" in sed[name]
    grant = "'s#^ReadWritePaths=%S/neuroscifi$#ReadWritePaths=%S/neuroscifi -/var/lib/neuroscifi-wake#'"
    for name in ("neuroscifi-console.service", "neuroscifi-webcortex.service", "neuroscifi-pocket.service"):
        assert grant in sed[name]                                  # the units that call the model services
    assert grant not in sed["neuroscifi-checkpoint.service"]
    assert "NSF_WAKE_DIR=/var/lib/neuroscifi-wake" in out and "systemctl enable --now neuroscifi-idle.timer " \
        "neuroscifi-wake.path" in out and "installed disabled" in out
    assert not list(tmp_path.iterdir())                            # a dry run writes nothing
    for user in ("root", "two words", ""):
        refused = subprocess.run(["bash", script, "--dry-run", "--idle", "--idle-user", user], capture_output=True,
                                 text=True, env={"PATH": "/usr/bin:/bin"})
        assert refused.returncode == 1 and "--idle" in refused.stderr
    # what those expressions make of the templates: no placeholder left, and the grant lands on the units' own line
    units = ROOT / "deploy" / "systemd"
    for name in ("neuroscifi-idle.service", "neuroscifi-wake.service", "neuroscifi-wake.path"):
        text = (units / name).read_text().replace("@NSF_USER@", "nsfmodels").replace("@NSF_REPO@", "/srv/nsf") \
            .replace("@NSF_WAKE_DIR@", "/var/lib/neuroscifi-wake")
        assert "@NSF_" not in "".join(line for line in text.splitlines() if not line.startswith("#")), name
    for name in ("neuroscifi-console.service", "neuroscifi-webcortex.service", "neuroscifi-pocket.service"):
        assert re.search(r"^ReadWritePaths=%S/neuroscifi$", (units / name).read_text(), re.M), name


def test_memory_status_prints_one_line_per_service_running_or_not(tmp_path):
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(                      # embed runs; for rerank docker prints an empty line and fails
        '#!/usr/bin/env bash\n'
        'case "$*" in *neuroscifi-memory-embed*) echo "/neuroscifi-memory-embed running health=healthy started=T" ;;\n'
        '  *) echo; exit 1 ;; esac\n')
    (bin_dir / "docker").chmod(0o755)
    import os
    env = _memory_env(tmp_path, "vllm", {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    script = str(ROOT / "deploy" / "memory" / "run.sh")
    done = subprocess.run(["bash", script, "status"], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0 and done.stdout.splitlines() == [
        "/neuroscifi-memory-embed running health=healthy started=T", "neuroscifi-memory-rerank: not running"]
    env = _memory_env(tmp_path, "single", {"PATH": f"{bin_dir}:{os.environ['PATH']}"})      # one container, one line
    done = subprocess.run(["bash", script, "status"], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0 and done.stdout.splitlines() == [     # and the other way's leftover, flagged (F9)
        "neuroscifi-memory: not running",
        "/neuroscifi-memory-embed running health=healthy started=T (left by NSF_MEMORY_SERVER=vllm: it holds the "
        "same ports; run.sh stop removes it)"]


def _logging_docker(tmp_path, existing=(), icc="false"):
    """A docker that records every command line and knows the containers and networks in `existing`; an existing
    network reports `icc` as its enable_icc option."""
    import os
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    known = " ".join(existing)
    (bin_dir / "docker").write_text(
        '#!/usr/bin/env bash\n'
        f'echo "$*" >> "{tmp_path}/docker.log"\n'
        'name="${@: -1}"\n'
        f'case "$*" in "network inspect -f "*enable_icc*) case " {known} " in *" $name "*) echo "{icc}"; exit 0 ;; esac;'
        ' exit 1 ;; esac\n'
        'case "$1 $2" in\n'
        f'  "inspect "*|"network inspect") case " {known} " in *" $name "*) echo "/$name running"; exit 0 ;; esac; exit 1 ;;\n'
        'esac\n'
        'exit 0\n')
    (bin_dir / "docker").chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


@pytest.mark.parametrize("server,leftovers", [("single", ["neuroscifi-memory-embed", "neuroscifi-memory-rerank"]),
                                              ("vllm", ["neuroscifi-memory"])])
def test_stop_also_removes_what_the_other_way_of_serving_left_running(tmp_path, server, leftovers):
    """F9: after a switch, the other way's containers kept both ports (restart unless-stopped) and `stop` did not
    see them."""
    import subprocess
    path = _logging_docker(tmp_path, existing=leftovers)
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "stop", "all"], capture_output=True,
                          text=True, env=_memory_env(tmp_path, server, {"PATH": path}), timeout=60)
    assert done.returncode == 0, done.stderr
    log = (tmp_path / "docker.log").read_text()
    for name in leftovers:
        assert f"rm -f {name}" in log and f"removed {name} (left by NSF_MEMORY_SERVER=" in done.stdout


@pytest.mark.parametrize("server", MEMORY_SERVERS)
def test_the_memory_containers_run_on_a_network_of_their_own(tmp_path, server):
    """L8: on Docker's default bridge any other container (SearXNG, a tenant's) could reach the memory server at
    its container address. A bridge of its own with traffic between containers off; ports stay on loopback."""
    import subprocess
    models = tmp_path / "models"
    for name in NAMES_MEMORY:
        (models / name).mkdir(parents=True)
        (models / name / "config.json").write_text("{}")
    path = _logging_docker(tmp_path)
    env = _memory_env(tmp_path, server, {"PATH": path})
    with open(tmp_path / ".config" / "neuroscifi" / "memory" / "memory.env", "a") as fh:
        fh.write(f"NSF_MEMORY_EMBED_MODEL_DIR={models}/qwen3-embedding-0.6b\n"
                 f"NSF_MEMORY_RERANK_MODEL_DIR={models}/qwen3-reranker-0.6b\n")
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "start", "all"], capture_output=True,
                          text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    log = (tmp_path / "docker.log").read_text().splitlines()
    create = [line for line in log if line.startswith("network create")]
    assert create == ["network create --driver bridge -o com.docker.network.bridge.enable_icc=false "
                      "--label org.neuroscifi.purpose=memory-models neuroscifi-memory"]
    runs = [line for line in log if line.startswith("run ")]
    assert runs and all(" --network neuroscifi-memory " in line for line in runs)
    assert all("127.0.0.1:1844" in line and "0.0.0.0:" not in line for line in runs)
    with open(tmp_path / ".config" / "neuroscifi" / "memory" / "memory.env", "a") as fh:
        fh.write("NSF_MEMORY_NETWORK=\n")                                   # the way back: Docker's default bridge
    (tmp_path / "docker.log").unlink()
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "start", "all"], capture_output=True,
                          text=True, env=env, timeout=60)
    log = (tmp_path / "docker.log").read_text()
    assert done.returncode == 0 and "network create" not in log and "--network" not in log
    with open(tmp_path / ".config" / "neuroscifi" / "memory" / "memory.env", "a") as fh:
        fh.write("NSF_MEMORY_NETWORK=host\n")                               # a name, but never host or none:
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "start", "all"], capture_output=True,
                          text=True, env=env, timeout=60)
    assert done.returncode == 1 and "NSF_MEMORY_NETWORK" in done.stderr


NAMES_MEMORY = ("qwen3-embedding-0.6b", "qwen3-reranker-0.6b")


@pytest.mark.parametrize("icc,ok", [("false", True), ("true", False), ("", False)])
def test_an_existing_network_of_that_name_is_used_only_if_its_containers_cannot_reach_each_other(tmp_path, icc, ok):
    """Security re-review: a network named neuroscifi-memory that someone else created was reused without a look at
    its options."""
    import subprocess
    models = tmp_path / "models"
    for name in NAMES_MEMORY:
        (models / name).mkdir(parents=True)
        (models / name / "config.json").write_text("{}")
    path = _logging_docker(tmp_path, existing=["neuroscifi-memory"], icc=icc)
    env = _memory_env(tmp_path, "single", {"PATH": path})
    with open(tmp_path / ".config" / "neuroscifi" / "memory" / "memory.env", "a") as fh:
        fh.write(f"NSF_MEMORY_EMBED_MODEL_DIR={models}/qwen3-embedding-0.6b\n"
                 f"NSF_MEMORY_RERANK_MODEL_DIR={models}/qwen3-reranker-0.6b\n")
    done = subprocess.run(["bash", str(ROOT / "deploy" / "memory" / "run.sh"), "start", "all"], capture_output=True,
                          text=True, env=env, timeout=60)
    log = (tmp_path / "docker.log").read_text()
    assert "network create" not in log
    if ok:
        assert done.returncode == 0 and " --network neuroscifi-memory " in log
    else:
        assert done.returncode == 1 and "lets its containers reach each other" in done.stderr
        assert not any(line.startswith("run ") for line in log.splitlines())


@pytest.mark.parametrize("server", MEMORY_SERVERS)
def test_memory_wait_never_sends_the_key_to_a_port_the_container_does_not_publish(tmp_path, server):
    """Review L2: something answers /health on the port, and Docker does not report the container publishing it
    (another account's process took the free port). The wait fails and no request carried the key."""
    tmp_path.joinpath("squatted").write_text("")
    done = _memory_wait(tmp_path, server=server)
    assert done.returncode == 1 and "another process holds the port. The key was not sent" in done.stderr
    assert not (tmp_path / "curl.stdin").exists()
    assert "nsfm-wait-test-key" not in (tmp_path / "curl.args").read_text() + done.stdout + done.stderr


@pytest.mark.parametrize("server", MEMORY_SERVERS)
def test_memory_wait_does_not_fail_when_only_the_warm_up_request_fails(tmp_path, server):
    done = _memory_wait(tmp_path, warm_exit=22, server=server)
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
