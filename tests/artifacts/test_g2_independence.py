"""Lane G2: a check never imports from `make` (DESIGN §6: validators re-read bytes with their own readers)."""
import ast
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "artifacts" / "check"
G2_CHECKS = sorted(p for p in CHECK.glob("*.py") if p.stem in {"_g2", "wav", "meshes", "png", "explorable", "browser"})


def _imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
            names.extend("." * node.level + (node.module or "") + "." + a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def test_the_g2_checks_exist():
    assert {p.stem for p in G2_CHECKS} >= {"_g2", "wav"}


@pytest.mark.parametrize("path", G2_CHECKS, ids=lambda p: p.stem)
def test_no_check_imports_from_make(path):
    for name in _imports(path):
        assert ".make" not in name and not name.startswith("..make") and "artifacts.make" not in name, (path, name)
