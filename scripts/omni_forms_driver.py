"""Main session's driver: the form benchmark's case view -> the product planner -> the benchmark's vocabulary.
Written without reading any held-out case. The two vocabularies were written independently; the mapping lives in
the benchmark's thin driver module, `neuroscifi.acceptance.omni.subject` (`choose_form`, FORMS_TO_BENCH,
FORMS_FROM_BENCH), moved there unchanged so that `scripts/bench_omni_forms.py run --family forms --subject
neuroscifi.acceptance.omni.subject:choose_form` runs the planner as a subject.

    python scripts/omni_forms_driver.py development OUT.json     # development only: held-out was looked at once
"""
import json
import sys

try:
    from omni_dev import forms as B          # the development-only kit (lane C)
except ImportError:
    from neuroscifi.acceptance.omni import forms as B
from neuroscifi.acceptance.omni.subject import FORMS_UNMAPPED as UNMAPPED
from neuroscifi.acceptance.omni.subject import choose_form as choose

__all__ = ["UNMAPPED", "choose"]


if __name__ == "__main__":
    split = sys.argv[1]
    if split != "development":
        raise SystemExit("this driver scores the development split only (the held-out split was looked at once)")
    out = {"split": split, "planner": B.score(choose, split)}
    for name, ref in B.REFERENCES.items():
        try:
            out[name] = B.score(ref, split)
        except Exception as e:
            out[name] = {"error": repr(e)}
    out["unmapped"] = sorted(set(map(str, UNMAPPED)))
    with open(sys.argv[2], "w") as handle:
        json.dump(out, handle, indent=1, default=str)

    def row(n, s):
        keys = [k for k in s if not isinstance(s[k], (dict, list))]
        print(n, {k: s[k] for k in keys})
    for n, s in out.items():
        if isinstance(s, dict):
            row(n, s)
    print("unmapped:", out["unmapped"])
