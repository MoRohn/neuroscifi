"""The owner's model file (lane P, ADR-076): detected by its marker only, checked with the explorable form's schema and
the expression language's own rules, never run as code, and bounded."""
import json

import pytest

from neuroscifi.artifacts import adapters, detect
from neuroscifi.artifacts.adapters.model_file import MAX_BYTES, ModelFile, read_model
from neuroscifi.artifacts.types import Malformed

MODEL = {"format": "neuroscifi.model/1", "title": "Spring",
         "parameters": [{"name": "k", "label": "Stiffness", "min": 1, "max": 10, "step": 1, "value": 4}],
         "quantities": [{"name": "w", "label": "Frequency", "expr": "sqrt(k)"}],
         "views": [{"kind": "table", "items": ["w"]}]}


def test_the_marker_and_only_the_marker_identifies_a_model_file():
    data = json.dumps(MODEL).encode()
    found = detect.detect(data, name="anything.txt")
    assert found.format == "nsf_model" and found.basis == "structure"
    assert detect.detect(json.dumps({"format": "other", "parameters": []}).encode(), name="m.nsfmodel").format == "json"
    decoded = adapters.decode(data, name="spring.json")
    rep = decoded.representation
    assert isinstance(rep, ModelFile) and rep.kind == "tree" and rep.model["parameters"] == MODEL["parameters"]
    shape = rep.shape()
    assert shape.kinds == ("process",) and shape.has_parameters and shape.n_items == 10


@pytest.mark.parametrize("change, reason", [
    ({"parameters": [{"name": "k", "label": "K", "min": 5, "max": 1, "step": 1, "value": 3}]}, "min must be below max"),
    ({"quantities": [{"name": "w", "expr": "__import__('os')"}]}, "cannot be run"),
    ({"quantities": [{"name": "w", "expr": "k +* 2"}]}, "cannot be run"),
    ({"views": []}, "cannot be run"),
    ({"title": 5}, "title and description are text"),
])
def test_a_model_that_cannot_run_is_malformed_with_its_reason(change, reason):
    with pytest.raises(Malformed, match=reason):
        read_model(json.dumps({**MODEL, **change}).encode())


def test_a_model_file_is_bounded_and_must_be_utf8_json():
    with pytest.raises(Malformed, match="at most"):
        read_model(json.dumps({**MODEL, "description": "x" * 10}).encode() + b" " * MAX_BYTES)
    with pytest.raises(Malformed, match="UTF-8 JSON"):
        read_model(b'{"format": "neuroscifi.model/1", ' + b"\xff\xfe")
