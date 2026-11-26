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
 "chart_line": "66516c15c737b95f",
 "chart_scatter": "bd28d660caa121ad",
 "chart_bar": "ed17b1ddfc9404c7",
 "chart_histogram": "03c29ab8b044800e",
 "chart_heatmap": "0252193876e14398",
 "chart_small_multiples": "a1db2c97840a60af",
 "timeline": "82f60dd2dd1da575",
 "graph_nodelink": "ff767cd59c293101",
 "graph_tree": "ac0e84601fc3f9ff",
 "flow_steps": "4ea92f74b41ea09c",
 "table_html": "84e0aaa9304e78a1",
 "document_html": "8b0b1151af90ad35",
 "document_md": "486269564d0b2816",
 "data_csv": "55dafe2b54e4fdae",
 "data_json": "7fa57a214a2e97af",
 "animation_svg": "ad3887409919f4c6"
}
MEDIA = {"table_html": "text/html", "document_html": "text/html", "document_md": "text/markdown",
         "data_csv": "text/csv", "data_json": "application/json"}


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
