"""The committed lane K evidence (docs/quality/evidence/omnimind_k.json) is what the code produces now, and it shows
no false identification."""
import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]


def test_evidence_is_current_and_clean():
    env = {**os.environ, "PYTHONPATH": str(BASE / "src")}
    result = subprocess.run([sys.executable, str(BASE / "scripts/omnimind_k_evidence.py"), "--check"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((BASE / "docs/quality/evidence/omnimind_k.json").read_text())
    assert evidence["corpus"]["wrong"] == [] and evidence["conflicting_hints_and_adversarial"]["wrong"] == []
    assert evidence["truncated_prefixes"]["false_identifications"] == []
    assert set(evidence["random_bytes"]["outcomes"]) <= {"unknown", "text"}
    assert all(n <= 3 for n in evidence["random_bytes"]["text_lengths"])
