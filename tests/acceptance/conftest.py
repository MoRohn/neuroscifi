"""Acceptance tests: one collection hook (lane ZW3, 2026-11-27).

The owner opened the first two withheld splits of the families unknown, hostile and cross as development cases
(WAVE3.md, decision 1), so `acceptance/omni/final.py` now refuses a look at them (`final.Spent`). Two tests of lane ZW2's
file exercise exactly such a look; lane ZW3 may not open that file, so they are marked here, by name, as expected to
fail with `final.Spent` and nothing else (strict: if one passes, or fails for another reason, the run fails). The
refusal itself is tested in test_omni_final.py; the third split's look in test_omni_withheld3.py.
"""
import pytest

SPENT_LOOKS = {
    "test_omni_withheld2.py::test_check_refuses_for_the_second_split",
    "test_omni_withheld2.py::test_a_withheld2_look_records_first_and_reports_summaries_only",
}


def pytest_collection_modifyitems(config, items):
    from neuroscifi.acceptance.omni import final
    for item in items:
        if f"{item.path.name}::{item.name}" in SPENT_LOOKS:
            item.add_marker(pytest.mark.xfail(strict=True, raises=final.Spent,
                                              reason="withheld2 is spent for unknown, hostile and cross: final "
                                                     "refuses the look this test takes (WAVE3.md)"))
