"""The committed record of OMNIMIND's held-out runs (docs/quality/evidence/omnimind_heldout/, fix lane F2, review
finding B2): summaries only, every file named in its README with the sentence on when the rules were written, and
the breakdown of "right" reproducible from the result files."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import omnimind_heldout_record as record  # noqa: E402


def test_the_record_holds_ids_outcomes_and_summaries_only():
    assert record.check(record.RECORD) == []


def test_the_check_catches_file_bytes_request_text_and_a_foreign_key(tmp_path):
    shutil.copy(record.RECORD / "made.json", tmp_path / "made.json")
    data = json.loads((tmp_path / "made.json").read_text())
    data["splits"]["held_out"]["families"]["made"]["failures"] = {"x": "Please chart my bank statement for March"}
    data["splits"]["held_out"]["families"]["made"]["tasks"]["made/held_out/not-a-case/make:line"] = "right"
    (tmp_path / "made.json").write_text(json.dumps(data))
    problems = record.check(tmp_path)
    assert any("bank statement" in p for p in problems) and any("not a held-out task id" in p for p in problems)


def test_the_readme_names_every_file_and_says_the_order_is_not_in_git():
    text = (record.RECORD / "README.md").read_text(encoding="utf-8")
    for path in record.RECORD.glob("*.json"):
        assert f"`{path.name}`" in text, path.name
    assert "cannot\nbe verified from git" in text or "cannot be verified from git" in text
    for commit in ("c5d3230", "c1f5f2f", "7d9038c", "505b55b"):
        assert commit in text
    assert "DISCLOSURE: lane P saw" in text and 'answered "cannot" instead of refused' in text


def test_the_breakdown_of_right_is_reproducible_from_the_result_files():
    got = record.breakdown()
    assert got == json.loads((ROOT / "docs" / "quality" / "evidence" / "omnimind_right_breakdown.json").read_text())
    for split, total in (("development", 512), ("held_out", 510)):
        rows = got["splits"][split]["right_by_kind"]
        assert sum(r["right"] for r in rows.values()) == got["splits"][split]["right"] == total
