"""Visual rule regressions (tests/presence/visual_rules_check.mjs under node): the validator rejects face-like prop
layouts on specs and on finished geometry, the composer re-lays them out, and every generated composition uses one
sanctioned eye mark chosen by activity energy. The server-side mirror of the face rule is checked here directly."""
from pathlib import Path
import shutil
import subprocess

import pytest

from neuroscifi.presence import scene_spec

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_visual_rules_in_the_client_modules():
    out = subprocess.run(["node", str(HERE / "visual_rules_check.mjs")], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "presence visual rule checks passed" in out.stdout


def el(primitive, x, y, size, rotation=0, tone="ink"):
    return {"primitive": primitive, "x": x, "y": y, "size": size, "rotation": rotation, "tone": tone}


def spec(elements):
    return {"schema": scene_spec.SCHEMA_ID, "concept": "odd thing", "body": "present", "eyes": "open", "motion": "pulse",
            "elements": elements}


@pytest.mark.parametrize("elements", [
    # the layout that shipped for "resolving contradictions": two small circles above a frown, a flame below
    [el("node", 58, 30, 12), el("node", 84, 30, 12), el("connector", 71, 52, 26), el("rays", 71, 74, 18, 90, "yellow")],
    [el("dot", 60, 40, 6), el("dot", 78, 40, 6), el("arc", 69, 58, 20, 180)],
    [el("line", 62, 40, 8, 90), el("line", 76, 41, 8, 90), el("wave", 69, 56, 20)],
    [el("ring", 70, 50, 34), el("circle", 63, 46, 6), el("circle", 77, 46, 6)],
])
def test_server_rejects_face_like_specs(elements):
    errors = scene_spec.validate_spec(spec(elements))
    assert any("face" in e for e in errors), errors


@pytest.mark.parametrize("elements", [
    [el("dot", 70, 30, 6), el("dot", 70, 44, 6), el("line", 70, 60, 16)],
    [el("ring", 61, 62, 26), el("ring", 81, 62, 26), el("dot", 71, 62, 8, tone="mint")],
])
def test_server_allows_objects_that_are_not_faces(elements):
    assert scene_spec.validate_spec(spec(elements)) == []
