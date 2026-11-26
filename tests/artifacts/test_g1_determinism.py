"""Lane G1: the same spec gives the same bytes in any process (and, recorded here, on the host that recorded the
digests: aarch64, Python 3.12, numpy as pinned). Every form's fixed spec also passes its independent check."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import g1_support as S
from g1_golden_specs import SPECS
from neuroscifi.artifacts import make

GOLDEN = {
 "chart.line": "5d19ff56eb7cd4c8",
 "chart.scatter": "114f19cafa4c5dcf",
 "chart.bar": "ec7da6982e3c913b",
 "chart.histogram": "13955816e62dcf23",
 "chart.heatmap": "adef018c627798d2",
 "chart.small_multiples": "b48868b2723c437f",
 "timeline": "82f60dd2dd1da575",
 "graph.nodelink": "0a518526716f6ba3",
 "graph.tree": "87e7abcab26e7b9b",
 "flow.steps": "2ffaf8360ab81cee",
 "table.html": "2928adfb0bff6b42",
 "document.html": "89d35ea9245bef7b",
 "document.md": "486269564d0b2816",
 "data.csv": "55dafe2b54e4fdae",
 "data.json": "7fa57a214a2e97af",
 "animation.svg": "f4b84176db37b308"
}
MEDIA = {"table.html": "text/html", "document.html": "text/html", "document.md": "text/markdown",
         "data.csv": "text/csv", "data.json": "application/json"}


def _digest(form):
    return hashlib.sha256(make.make(SPECS[form]).bytes).hexdigest()[:16]


@pytest.mark.parametrize("form", sorted(SPECS))
def test_golden_bytes(form):
    assert _digest(form) == GOLDEN[form], f"{form}: bytes changed (update GOLDEN only for a deliberate change)"


@pytest.mark.parametrize("form", sorted(SPECS))
def test_every_form_passes_its_independent_check(form):
    m = make.make(SPECS[form])
    v = S.verdict(m.bytes, SPECS[form], MEDIA.get(form, "image/svg+xml"))
    assert v.ok, [(f.rule, f.detail) for f in v.technical if not f.passed]


def test_another_process_with_another_hash_seed_makes_the_same_bytes():
    here = Path(__file__).resolve().parent
    code = ("import hashlib, json, sys; sys.path.insert(0, sys.argv[1]);"
            "from g1_golden_specs import SPECS; from neuroscifi.artifacts import make;"
            "from neuroscifi.artifacts.make import _g1;"
            "print(json.dumps({k: hashlib.sha256(make.make(s).bytes).hexdigest()[:16] for k, s in SPECS.items()}))")
    env = {**os.environ, "PYTHONHASHSEED": "12345"}
    out = subprocess.run([sys.executable, "-c", code, str(here)], env=env, capture_output=True, text=True,
                         timeout=300, check=True)
    assert json.loads(out.stdout) == {k: _digest(k) for k in SPECS}


def test_every_g1_form_is_registered_with_a_schema_and_a_generator():
    from neuroscifi.artifacts import spec as spec_mod
    from neuroscifi.artifacts.make import _g1
    rows = {r["form"]: r for r in make.catalog()}
    for form in SPECS:
        assert form in _g1.FORMS and spec_mod.form_schema(form) is not None and rows[form]["available"]
