"""Lane FX's adversarial corpora (ADR-080, `docs/quality/evidence/omnimind_fx.json`) are generated from the
repository: `scripts/omnimind_fx/` builds every item from fixed seeds, and `run_corpus.py` / `corpus_cross.py`
recompute the figures (in a child with a memory cap; not run here: the hostile items are bombs and lying sizes).
This test pins the corpora's shape, so a changed generator cannot silently change what the evidence counts, and
decodes the one class lane FX2's ruling moved (an SVG linking an outside image: answered with its note)."""
from __future__ import annotations

import collections
import importlib
import sys
from pathlib import Path

import pytest

from neuroscifi.artifacts import adapters, outcome

FX = Path(__file__).resolve().parents[2] / "scripts" / "omnimind_fx"


@pytest.fixture(scope="module")
def corpora():
    sys.path.insert(0, str(FX))
    try:
        yield {name: importlib.import_module(name) for name in ("corpus_unknown", "corpus_hostile", "corpus_honest",
                                                                "corpus_cross")}
    finally:
        sys.path.remove(str(FX))


def test_the_corpora_have_the_sizes_the_evidence_counts(corpora):
    assert len(corpora["corpus_unknown"].ITEMS) == 3669
    hostile = collections.Counter(c for c, _ in corpora["corpus_hostile"].ITEMS)
    assert sum(hostile.values()) == 168 and len(hostile) == 44
    assert len(corpora["corpus_honest"].ITEMS) == 19
    cross = corpora["corpus_cross"].build()
    assert len(cross) == 144 and len({kind for kind, *_ in cross}) == 12


def test_the_svg_outside_image_class_is_answered_with_its_note(corpora):
    # lane FX's truth for H6 is "refused"; ADR-080 rule 2 (lane FX2) answers it, with the note, as ruled
    tasks = [t for c, t in corpora["corpus_hostile"].ITEMS if c == "H6_svg_external_image"]
    assert len(tasks) == 3
    for task in tasks:
        (name, data), = task.files.items()
        out = adapters.decode(data, name=name)
        assert outcome.of_decoded(out) == outcome.ANSWERED and outcome.outside("1 x image") in out.notes
