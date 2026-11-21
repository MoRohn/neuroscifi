"""Deployment files carry no personal account or home path, and every voice image dependency is pinned."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNITS = sorted((ROOT / "deploy" / "systemd").glob("*.service"))
DOCKER_USER_UNITS = ("neuroscifi-vllm.service", "neuroscifi-voice-asr.service", "neuroscifi-voice-tts.service")


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


def test_tts_image_pins_every_package():
    text = (ROOT / "deploy" / "voice" / "Dockerfile.tts").read_text()
    run = text[text.index("RUN pip install"):text.index("&& python3 -c")]
    for token in re.findall(r'"([^"]+)"|\s(regex)\s', run):
        spec = token[0] or token[1]
        assert "==" in spec or "@ https://" in spec, f"unpinned: {spec}"
    assert re.search(r'"regex==\d', run), "regex must be pinned"
    wheel = re.search(r'en_core_web_sm @ (\S+?)"', run).group(1)
    assert re.search(r"#sha256=[0-9a-f]{64}$", wheel), "the spaCy model wheel must be pinned by sha256"
