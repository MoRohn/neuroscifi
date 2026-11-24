"""The answer model's key never reaches a command line, and goes only to the container that publishes the port
(open-items round, lane D), as the memory models' key since review L2."""
import os
from pathlib import Path
import re
import subprocess


from test_resources_idle import MIN, fake, memory_host, spool, _unload_idle      # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[1]
INSTALL = str(ROOT / "deploy" / "install.sh")


# ---- the answer model's key --------------------------------------------------------------------------------------

def _vllm_status(tmp_path, *, publishes):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *State.Running*) echo true ;;\n'
        f'  port*) {"echo 127.0.0.1:8811" if publishes else "exit 0"} ;;\n'
        '  *) echo "/neuroscifi-vllm running health=healthy started=T" ;;\n'
        "esac\n")
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{tmp_path}/curl.args"\n'
        f'case "$*" in *"-K -"*) cat >> "{tmp_path}/curl.stdin" ;; esac\n'
        'echo \'{"data": []}\'\n')
    for tool in ("docker", "curl"):
        (bin_dir / tool).chmod(0o755)
    key_dir = tmp_path / ".config" / "neuroscifi" / "inference"
    key_dir.mkdir(parents=True)
    (key_dir / "api_key").write_text("nsfv-status-test-key\n")
    return subprocess.run(["bash", str(ROOT / "deploy" / "vllm" / "run.sh"), "status"], capture_output=True, text=True,
                          env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}, timeout=60)


def test_vllm_status_never_puts_the_key_on_a_command_line(tmp_path):
    """`ps` shows every account each process's arguments; the key went there as `-H "Authorization: Bearer …"`."""
    done = _vllm_status(tmp_path, publishes=True)
    assert done.returncode == 0, done.stderr
    args = (tmp_path / "curl.args").read_text()
    assert "http://127.0.0.1:8811/v1/models" in args and "-K -" in args
    assert "nsfv-status-test-key" not in args and "Bearer" not in args
    assert (tmp_path / "curl.stdin").read_text() == 'header = "Authorization: Bearer nsfv-status-test-key"\n'
    assert "nsfv-status-test-key" not in done.stdout + done.stderr


def test_vllm_status_sends_the_key_only_to_the_container_that_publishes_the_port(tmp_path):
    done = _vllm_status(tmp_path, publishes=False)
    assert done.returncode == 0 and "the key was not sent" in done.stdout
    assert not (tmp_path / "curl.args").exists() and not (tmp_path / "curl.stdin").exists()


def test_no_deploy_script_or_unit_passes_a_key_as_an_argument():
    for path in [*(ROOT / "deploy").rglob("*.sh"), *(ROOT / "deploy" / "systemd").glob("*.service")]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(r"(curl|wget)\b.*(-H|--header)\s+[\"']?Authorization", line), f"{path.name}:{n}"
            assert not re.search(r"--api-key[ =]", line), f"{path.name}:{n}"
