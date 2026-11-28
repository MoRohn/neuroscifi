"""Lane B1: what the planner is handed (ContentShape of decoded artifacts, through K's single rule), and that every
B1 adapter claims a name lane K's `detect` can return (detection itself is K's and tested there)."""
from neuroscifi.artifacts import detect, shapes_b1
from neuroscifi.artifacts.adapters import _b1

import _b1_support
from _b1_support import make_zip

b1reg = _b1_support.b1reg      # the fixture


def test_shapes_named_by_the_brief(b1reg):
    dated = b1reg.decode(b"day,value\n2024-01-01,3\n2024-01-02,4\n", name="d.csv").representation.shape()
    assert "series" in dated.kinds and dated.has_time and dated.n_series == 1
    cats = b1reg.decode(b"fruit,count\napple,3\npear,5\n", name="c.csv").representation.shape()
    assert "categories" in cats.kinds and not cats.has_time
    calls = b1reg.decode(b"def a():\n    b()\n\ndef b():\n    pass\n", name="m.py").representation.shape()
    assert calls.kinds == ("network",)
    outline = b1reg.decode(b"# A\n\n## B\n\ntext\n", name="o.md").representation.shape()
    assert "hierarchy" in outline.kinds and "prose" in outline.kinds


def test_content_shapes_of_a_bundle_reach_its_members(b1reg):
    data = make_zip([("t.csv", b"day,value\n2024-01-01,3\n2024-01-02,4\n"), ("n.md", b"# T\n\ntext\n")])
    shapes = shapes_b1.content_shapes(b1reg.decode(data, name="a.zip").representation)
    assert shapes[0].kinds == ("hierarchy",)
    assert any("series" in s.kinds for s in shapes[1:]) and any("prose" in s.kinds for s in shapes[1:])
    assert len(shapes_b1.content_shapes(b1reg.decode(data, name="a.zip").representation, limit=2)) == 2


def test_every_b1_adapter_is_reachable_through_detect():
    for cls in _b1.adapter_classes():
        reachable = [f for f in cls.formats if f in detect.FORMATS]
        assert reachable, cls.name
