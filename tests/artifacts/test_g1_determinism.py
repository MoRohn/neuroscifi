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
 "chart.line": "172131d23f5756b4",
 "chart.scatter": "90fdb8af9f2c3b49",
 "chart.bar": "41095d266f68e21a",
 "chart.histogram": "e67f28e696d4dd7b",
 "chart.heatmap": "9e727cf365a05384",
 "chart.small_multiples": "f8780c9a798cbcc7",
 "timeline": "ebe38cac47e2a876",
 "graph.nodelink": "dae6b0b18e6a16bc",
 "graph.tree": "00ea79f333f4e8c3",
 "flow.steps": "12d3ec257a6b51e5",
 "table.html": "2928adfb0bff6b42",
 "document.html": "786ad9826fc8a353",
 "document.md": "486269564d0b2816",
 "data.csv": "c00d60865b36d978",
 "data.json": "7fa57a214a2e97af",
 "animation.svg": "ba91eb3f1e852638"
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
