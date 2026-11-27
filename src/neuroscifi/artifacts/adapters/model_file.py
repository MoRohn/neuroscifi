"""The owner's model file (lane P, ADR-076): a small declarative JSON file that states a model with inputs someone can
vary, so that an explorable model is made only from an explicit model, never guessed from data.

Format `neuroscifi.model/1` (a JSON object, UTF-8):

    {"format": "neuroscifi.model/1",
     "title": "...", "description": "...",                     (optional text)
     "parameters": [{"name", "label", "min", "max", "step", "value", "unit"?}, ...],      1 to 12
     "quantities": [{"name", "label"?, "expr", "unit"?}, ...],                             optional, up to 32
     "dynamics": {"state": [{"name", "label"?, "initial", "update", "unit"?}], "steps", "dt",
                  "time_label"?, "time_unit"?},                                             optional
     "views": [{"kind": "plot" | "table" | "bars", ...}, ...]}                              1 to 6

Everything but `format`, `title` and `description` is exactly the data of the `explorable.model` form (ADR-074, lane
G2): expressions are in that form's small language (`artifacts/expr.py`: numbers, names, + - * / ^, comparisons,
`if(c, a, b)`, a fixed list of functions), parsed into a tree and never evaluated as code. The file is checked with
the form's own schema and with `expr.model_problems` (every name, expression, range and view); a file that fails is
Malformed with the reasons. Detection: a JSON object whose `format` member is "neuroscifi.model/1" (structure; the
file's name is never used). The representation is a `ModelFile`: a `Tree` of the JSON (so every reader of trees still
works) that also carries the checked model and whose shape is a process with parameters, counted by the states its
sliders can be set to.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..types import ContentShape, Decoded, Detected, Limits, Malformed, Tree, _shape, bounded
from . import Adapter

FORMAT_NAME = "nsf_model"
MARKER = "neuroscifi.model/1"
MEDIA_TYPE = "application/vnd.neuroscifi.model+json"
MAX_BYTES = 256 << 10
STATES_CAP = 1_000_000
_MARKER = re.compile(r'"format"\s*:\s*"neuroscifi\.model/1"')


@dataclass(frozen=True, eq=False)
class ModelFile(Tree):
    """A model file: its JSON as a tree, plus the checked `explorable.model` data."""

    model: dict = field(default_factory=dict)
    title: str = ""
    description: str = ""

    def describe(self) -> dict:
        m = self.model
        return bounded({"kind": self.kind, "model": MARKER, "title": self.title[:200],
                        "parameters": [p["name"] for p in m.get("parameters", [])][:12],
                        "quantities": [q["name"] for q in m.get("quantities", [])][:32],
                        "state": [s["name"] for s in (m.get("dynamics") or {}).get("state", [])][:8],
                        "views": [v["kind"] for v in m.get("views", [])][:6]})

    def shape(self) -> ContentShape:
        """A process with parameters. Its dominant count is the number of states the model can be set to: the
        product over its parameters of the slider positions ((max - min) / step + 1), at most 1,000,000."""
        m = self.model
        outputs = len(m.get("quantities", [])) + len((m.get("dynamics") or {}).get("state", []))
        states = 1
        for p in m.get("parameters", []):
            states = min(STATES_CAP, states * (int((p["max"] - p["min"]) / p["step"] + 1e-9) + 1))
        return _shape(("process",), n_items=states, n_series=outputs, has_parameters=True,
                      has_time="dynamics" in m, text_chars=len(self.text()))

    def text(self) -> str:
        return " ".join(t for t in (self.title, self.description) if t)[:4000]


def model_structure(head: bytes, size=None):
    """Detection: (format, reasons, container, "structure") for a model file, or None."""
    try:
        text = head[:65536].decode("utf-8")
    except UnicodeDecodeError:
        return None
    s = text.lstrip("﻿ \t\r\n")
    if s.startswith("{") and _MARKER.search(s[:4096]):
        return FORMAT_NAME, ["structure: a JSON object whose 'format' member is 'neuroscifi.model/1' (a model file)"], \
            None, "structure"
    return None


def install() -> None:
    from .. import detect
    detect.FORMATS.setdefault(FORMAT_NAME, ("tree", MEDIA_TYPE))
    detect.DECLARED.setdefault(MEDIA_TYPE, FORMAT_NAME)
    if model_structure not in detect.SIGNATURES:
        detect.SIGNATURES.append(model_structure)


def read_model(data: bytes) -> tuple[dict, str, str]:
    """(the explorable.model data, title, description) of a model file; Malformed with every reason otherwise."""
    from .. import expr
    from ..make.explorable import SCHEMAS
    from ...inference.jsonschema import validate
    if len(data) > MAX_BYTES:
        raise Malformed(f"a model file is at most {MAX_BYTES} bytes")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Malformed("the model file is not UTF-8 JSON") from None
    if not isinstance(value, dict) or value.get("format") != MARKER:
        raise Malformed(f"a model file is a JSON object with \"format\": \"{MARKER}\"")
    title, description = value.get("title", ""), value.get("description", "")
    if not isinstance(title, str) or not isinstance(description, str) or len(title) > 200 or len(description) > 2000:
        raise Malformed("title and description are text (at most 200 and 2000 characters)")
    model = {k: v for k, v in value.items() if k not in ("format", "title", "description")}
    problems = validate(model, SCHEMAS["explorable.model"]["data"], "$")
    if not problems:
        problems = expr.model_problems(model)
    if problems:
        raise Malformed("the model file cannot be run: " + "; ".join(problems[:8]))
    return model, title, description


class ModelFileAdapter(Adapter):
    name = "model_file"
    formats = (FORMAT_NAME,)
    families = ("tree",)
    reads = "content"

    def decode(self, data: bytes, detected: Detected, limits: Limits) -> Decoded:
        from .jsontree import to_tree
        from ..safe import Deadline
        model, title, description = read_model(data)
        tree, partial = to_tree(json.loads(data.decode("utf-8")), limits, Deadline(limits.max_seconds))
        rep = ModelFile(tree.root, model=model, title=title, description=description)
        return Decoded(detected, rep, ("a model file: its expressions are parsed, never run as code",), partial)


def register_all() -> None:
    from . import register
    install()
    register(ModelFileAdapter())


__all__ = ["FORMAT_NAME", "MARKER", "ModelFile", "ModelFileAdapter", "install", "model_structure", "read_model",
           "register_all"]
