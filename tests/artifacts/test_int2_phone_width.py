"""Wave O-1 integration, item 4: every made SVG scales down to its container (a 360 px phone), keeping its viewBox,
its width and height in user units, and what the reading contract reads (the independent check still recovers the
values). Rendered at 360 px with Chromium once, by hand (scratch folder int2/): 328 px wide inside a 16 px margin."""
import re

import pytest

from g1_golden_specs import SPECS
from neuroscifi.artifacts import check, make

SVG = [form for form in SPECS if make.make(SPECS[form]).media_type == "image/svg+xml"]


@pytest.mark.parametrize("form", SVG)
def test_a_made_svg_scales_to_its_container_and_still_reads(form):
    made = make.make(SPECS[form])
    text = made.bytes.decode()
    root = re.match(r'<svg [^>]*id="(nsf-[0-9a-f]{10})"[^>]*>', text)
    assert root and re.search(r'viewBox="0 0 [0-9.]+ [0-9.]+"', root.group(0))
    assert re.search(r'width="[0-9.]+" height="[0-9.]+"', root.group(0))            # user units, as before
    rule = re.search("#" + root.group(1) + r"\{([^}]*)\}", text).group(1)
    assert "max-width:100%" in rule and "height:auto" in rule
    assert check.check(made.bytes, made.media_type, made.spec).ok


def test_every_svg_form_is_covered():
    assert {"chart.line", "chart.bar", "chart.scatter", "graph.nodelink", "timeline", "animation.svg"} <= set(SVG)
