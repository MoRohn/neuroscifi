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
