"""The release version is declared once per artefact and the declarations agree (REL-01: a wheel once named the
previous release)."""
from pathlib import Path
import re

import neuroscifi
from neuroscifi.webcortex import mcp

ROOT = Path(__file__).resolve().parents[1]


def test_every_version_declaration_is_0_7_0():
    pyproject = re.search(r'(?m)^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text()).group(1)
    badge = re.search(r"badge/version-([0-9.]+)-", (ROOT / "README.md").read_text()).group(1)
    assert pyproject == neuroscifi.__version__ == mcp.SERVER_INFO["version"] == badge == "0.7.0"


def test_the_web_user_agent_names_the_release():
    """R4 item 6: the 0.7.0 release still sent "NeuroSciFi-WebCortex/0.6". The User-Agent is derived from the package
    version (major.minor); the robots.txt product token stays "NeuroSciFi-WebCortex". The open-webSearch patch script
    carries the same literal (tests/webcortex/test_openwebsearch.py ties it to config.USER_AGENT)."""
    from neuroscifi.webcortex import config, net
    major_minor = ".".join(neuroscifi.__version__.split(".")[:2])
    assert config.USER_AGENT == f"NeuroSciFi-WebCortex/{major_minor} (research agent; honours robots.txt)"
    assert net._product_token(config.USER_AGENT) == "neuroscifi-webcortex"
    script = (ROOT / "deploy" / "open-websearch" / "apply-honest-ua.mjs").read_text()
    assert f'export const USER_AGENT = "{config.USER_AGENT}";' in script
