"""Runs the presence client checks (tests/presence/client_check.mjs) under node: geometry, planner tiers, validator,
interpreter arbitration and motion-engine continuity, without a browser."""
from pathlib import Path
import shutil
import subprocess

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_presence_client_modules():
    out = subprocess.run(["node", str(HERE / "client_check.mjs")], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "presence client checks passed" in out.stdout
