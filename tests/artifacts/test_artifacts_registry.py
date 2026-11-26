"""The adapter registry and decode chain (DESIGN §3), the spec envelope (§6), and the empty make/check registries."""
import numpy as np
import pytest

from neuroscifi.artifacts import adapters, check, make, spec
from neuroscifi.artifacts import types as T


class _Ep:
    def __init__(self, name, obj):
        self.name, self._obj, self.loaded = name, obj, False

    def load(self):
        self.loaded = True
        return self._obj


class Fake(adapters.Adapter):
    name = "fake-png"
    formats = ("png",)
    families = ("image",)

    def decode(self, data, detected, limits):
        return T.Decoded(detected, T.Raster(np.zeros((1, 1), dtype=np.uint8), "gray", 8), (), False)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(adapters, "_ADAPTERS", [])
    monkeypatch.setattr(adapters, "_EXTERNAL", {})
    monkeypatch.setattr(adapters, "_GAP_HOOKS", [])
    monkeypatch.setattr(adapters, "_DISCOVERED", [])
    return adapters


def test_register_resolve_catalog(registry):
    registry.register(Fake())
    det = T.Detected("image", "png", "image/png", "signature", None, ())
    assert registry.resolve(det).name == "fake-png"
    assert registry.resolve(T.Detected("text", "text", "text/plain", "structure", None, ())) is None
    rows = {r["name"]: r for r in registry.catalog()}
    assert rows["fake-png"] == {"name": "fake-png", "formats": ["png"], "families": ["image"], "available": True,
                                "reason": "ready", "reads": "content", "source": "builtin"}
    assert rows["refuse-pickle"]["available"] is False
    with pytest.raises(ValueError):
        registry.register(Fake())


def test_resolve_prefers_an_available_adapter_and_names_needs(registry):
    class Missing(Fake):
        name = "needs-missing"
        needs = ("module_that_is_not_installed_here",)
    registry.register(Missing())
    out = registry.decode(PNG)
    assert isinstance(out.representation, T.Opaque)
    assert "module_that_is_not_installed_here" in out.representation.needs
    registry.register(Fake())
    assert registry.decode(PNG).representation.kind == "raster"


def test_decode_unknown_is_opaque_and_counted_as_a_gap(registry):
    gaps = []
    remove = registry.on_gap(lambda det, outcome: gaps.append((det.format, outcome)))
    out = registry.decode(bytes(range(256)) * 4)
    assert isinstance(out.representation, T.Opaque) and out.detected.format == "unknown"
    assert gaps == [("unknown", "opaque")]
    remove()
    registry.decode(b"\x00\x01\x02")
    assert len(gaps) == 1


def test_a_failing_gap_hook_does_not_break_decode(registry):
    registry.on_gap(lambda *a: 1 / 0)
    assert isinstance(registry.decode(b"\xff\xfe\x00").representation, T.Opaque)


def test_decode_refuses_oversize_depth_and_pickle(registry):
    with pytest.raises(T.Refused) as e:
        registry.decode(b"x" * 11, limits=T.Limits(max_bytes=10))
    assert e.value.reason == "too_large" and e.value.rule == "max_bytes"
    with pytest.raises(T.Refused):
        registry.decode(b"x", limits=T.Limits(max_depth=-1))
    with pytest.raises(T.Refused) as e:
        registry.decode(b"\x00\x01\x02", name="model.pkl")
    assert e.value.rule == "pickle"
    nested = registry.nested(T.Limits(), used_bytes=100)
    assert nested.max_depth == 3 and nested.max_decoded_bytes == (256 << 20) - 100


def test_adapter_failures_become_named_errors(registry):
    class Crashy(Fake):
        name = "crashy"

        def decode(self, data, detected, limits):
            return data[10 ** 9]   # IndexError from hostile bytes
    registry.register(Crashy())
    with pytest.raises(T.Malformed) as e:
        registry.decode(PNG)
    assert "IndexError" in str(e.value) and "\\x89" not in str(e.value)

    class Liar(Fake):
        name = "liar"
        formats = ("gzip",)

        def decode(self, data, detected, limits):
            return {"not": "decoded"}
    registry.register(Liar())
    with pytest.raises(T.Malformed):
        registry.decode(b"\x1f\x8b\x08\x00")

    class Says(Fake):
        name = "says"
        formats = ("xz",)

        def decode(self, data, detected, limits):
            raise T.Unsupported("xz variant", needs=("lzma filter",))
    registry.register(Says())
    gaps = []
    registry.on_gap(lambda det, outcome: gaps.append(outcome))
    with pytest.raises(T.Unsupported):
        registry.decode(b"\xfd7zXZ\x00rest")
    assert gaps == ["unsupported"]


def test_external_adapters_load_only_when_named(registry, monkeypatch):
    class Ext(Fake):
        name = "ext-adapter"
    ep_named, ep_other = _Ep("ext-adapter", Ext), _Ep("unasked", Ext)
    monkeypatch.setattr(adapters, "_DISCOVERED", [ep_named, ep_other])
    rows = {r["name"]: r for r in registry.catalog()}
    assert rows["ext-adapter"]["reason"] == "found, not approved" and not ep_named.loaded
    assert registry.load_external([]) == [] and not ep_named.loaded and not ep_other.loaded
    assert registry.load_external(["ext-adapter"]) == ["ext-adapter"]
    assert ep_named.loaded and not ep_other.loaded
    rows = {r["name"]: r for r in registry.catalog()}
    assert rows["ext-adapter"]["source"] == "external" and rows["ext-adapter"]["available"] is True
    assert rows["unasked"]["reason"] == "found, not approved"
    assert registry.load_external(["ext-adapter"]) == []   # idempotent
    # the owner withdraws the name: the adapter is unloaded and listed as found again, through the public call
    assert registry.unload_external(["something-else"]) == ["ext-adapter"]
    rows = {r["name"]: r for r in registry.catalog()}
    assert rows["ext-adapter"]["reason"] == "found, not approved" and rows["ext-adapter"]["available"] is False
    assert all(a.name != "ext-adapter" for a in adapters._ADAPTERS)
    assert registry.unload_external([]) == []


def test_external_adapter_that_fails_to_load_is_listed(registry, monkeypatch):
    class Bad:
        def load(self):
            raise ImportError("boom")
        name = "bad"
    monkeypatch.setattr(adapters, "_DISCOVERED", [Bad()])
    assert registry.load_external(["bad"]) == []
    rows = {r["name"]: r for r in registry.catalog()}
    assert rows["bad"]["reason"] == "approved, failed to load (ImportError)"


# --- spec ---------------------------------------------------------------------------------------------------------

LINE = {"type": "object", "additionalProperties": False, "required": ["x", "y"],
        "properties": {"x": {"type": "array", "maxItems": 10000, "items": {"type": "number"}},
                       "y": {"type": "array", "maxItems": 10000, "items": {"type": "number"}}}}


@pytest.fixture
def forms(monkeypatch):
    monkeypatch.setattr(spec, "_FORMS", {})
    spec.register_form("line_chart", data=LINE)
    return spec


def _spec(**over):
    base = {"spec": "line_chart", "version": 1, "title": "Rain per month", "description": "Monthly rain in mm/day.",
            "data": {"x": [1, 2, 3], "y": [4.5, 3.0, 1.25]}, "options": {}, "sources": ["request"],
            "made_by": {"generator": "", "version": 0}}
    base.update(over)
    return base


def test_valid_spec_round_trips(forms):
    assert forms.validate(_spec()) == []
    s = forms.Spec.from_dict(_spec())
    assert forms.Spec.from_json(s.to_json()) == s
    assert s.to_json() == forms.Spec.from_dict(_spec()).to_json()   # canonical


@pytest.mark.parametrize("over,needle", [
    ({"title": "a\u202eb"}, "control"),
    ({"title": "a\u2066b"}, "control"),
    ({"description": "bell\x07"}, "control"),
    ({"description": "esc\x1b[31m"}, "control"),
    ({"description": "nul\x00"}, "control"),
    ({"data": {"x": [1], "y": [1], "k\x1b": 1}}, "control"),
    ({"options": {"label": "x" * 5000}}, "longer than"),
    ({"data": {"x": [1], "y": [1], "k" * 5000: 1}}, "long"),
    ({"spec": "pie"}, "no form"),
    ({"spec": "chart.line.extra"}, "does not match"),
    ({"version": 2}, "version"),
    ({"sources": ["../etc"]}, "does not match"),
    ({"sources": ["bad id!"]}, "does not match"),
    ({"made_by": {"generator": "x", "version": -1}}, "below"),
    ({"extra": 1}, "unexpected"),
    ({"data": {"x": ["a"], "y": [1]}}, "expected number"),
    ({"data": {"x": [float("nan")], "y": [1]}}, "finite"),
])
def test_invalid_specs_are_named(forms, over, needle):
    errors = forms.validate(_spec(**over))
    assert errors and any(needle in e for e in errors), errors
    with pytest.raises(T.Malformed):
        forms.Spec.from_dict(_spec(**over))


TEXT_THAT_MENTIONS_THINGS = ("p < 0.05", "a<b", "onset = 3 s", "C:\\data", "src/neuroscifi/cli.py",
                             "https://example.org/report", "<script>", "Speed in km/h", "x < 5 and y > 2",
                             "R&D budget", "javascript:alert(1)", "/etc/passwd", "line one\nline two\ttabbed")


def test_text_that_mentions_markup_urls_or_paths_is_accepted(forms):
    for ok in TEXT_THAT_MENTIONS_THINGS:
        assert spec.forbidden(ok) is None, ok
    value = _spec(title="<script>", description="p < 0.05; see https://example.org/report; C:\\data")
    assert forms.validate(value) == []
    forms.register_form("graph.nodelink", data={"type": "object", "properties": {
        "labels": {"type": "array", "items": {"type": "string"}}}})
    nodelink = _spec(spec="graph.nodelink", title="a<b", description="onset = 3 s",
                     data={"labels": list(TEXT_THAT_MENTIONS_THINGS)})
    assert forms.validate(nodelink) == []
    assert forms.Spec.from_dict(nodelink).data["labels"][3] == "C:\\data"


def test_spec_bounds(forms):
    huge = _spec(description="", options={}, data={"x": [], "y": [], "note": "a " * 2000})
    assert forms.validate(huge)   # string over MAX_STRING
    deep = {"a": 1}
    for _ in range(20):
        deep = {"d": deep}
    assert any("deeper" in e for e in forms.validate(_spec(data=deep)))
    many = _spec(data={"x": [1.0] * 9000, "y": [1.0] * 9000})
    assert forms.validate(many) == []   # data arrays do not count toward the envelope bound
    assert forms.validate("not a dict")


def test_register_form_checks_schemas(monkeypatch):
    monkeypatch.setattr(spec, "_FORMS", {})
    with pytest.raises(ValueError):
        spec.register_form("Bad Name", data={})
    with pytest.raises(ValueError):
        spec.register_form("x", data={"oneOf": []})
    for bad in ("chart.", ".line", "chart.line.extra", "Chart.line", "chart-line", "a" * 25):
        with pytest.raises(ValueError):
            spec.register_form(bad, data={})
    spec.register_form("x", data={"type": "object"})
    with pytest.raises(ValueError):
        spec.register_form("x", data={"type": "object"})
    for good in ("timeline", "chart.line", "graph.nodelink", "sound.sonify", "explorable.model"):
        spec.register_form(good, data={"type": "object"})
    assert spec.forms() == ["chart.line", "explorable.model", "graph.nodelink", "sound.sonify", "timeline", "x"]


# --- make / check --------------------------------------------------------------------------------------------------


def test_make_registry(forms, monkeypatch):
    monkeypatch.setattr(make, "_GENERATORS", {})
    assert make.catalog() == []
    with pytest.raises(T.Unsupported) as e:
        make.make(_spec())
    assert e.value.reason == "missing_capability"

    class Gen(make.Generator):
        name = "csv-line"
        version = 3

        def make(self, s, limits):
            return make.Made(("x,y\n" + "".join(f"{a},{b}\n" for a, b in zip(s.data["x"], s.data["y"]))).encode(),
                             "text/csv", s, ())
    make.register("line_chart", Gen())
    made = make.make(forms.Spec.from_dict(_spec()))
    assert made.bytes == b"x,y\n1,4.5\n2,3.0\n3,1.25\n"
    assert made.spec.made_by == {"generator": "csv-line", "version": 3}
    assert made.bytes == make.make(_spec()).bytes
    assert make.catalog() == [{"form": "line_chart", "generator": "csv-line", "version": 3, "available": True,
                               "reason": "ready"}]
    with pytest.raises(T.Malformed):
        make.make(_spec(title="bad\x1b[31m"))
    forms.register_form("chart.line", data=LINE)   # a dotted form name, as DESIGN writes them
    make.register("chart.line", Gen())
    assert make.make(_spec(spec="chart.line")).bytes == made.bytes
    assert {r["form"] for r in make.catalog()} == {"line_chart", "chart.line"}
    with pytest.raises(T.Refused):
        make.make(_spec(), T.Limits(max_bytes=4))


def test_check_registry(monkeypatch):
    monkeypatch.setattr(check, "_VALIDATORS", {})
    v = check.check(b"x", "text/csv")
    assert not v.ok and v.technical[0].rule == "validator_available"

    def good(data, media_type, spec, source):
        return check.Verdict.of((check.Finding("parses", True, "parsed"),), (check.Finding("wide", False, "too wide"),))
    check.register("text/csv", good)
    v = check.check(b"x", "text/csv; charset=utf-8")
    assert v.ok and len(v.style) == 1   # style findings never decide ok

    def crash(data, media_type, spec, source):
        raise KeyError("x")
    check.register("text/csv", crash)
    v = check.check(b"x", "text/csv")
    assert not v.ok and "KeyError" in v.technical[-1].detail
    assert check.Verdict.of((), ()).ok is False
