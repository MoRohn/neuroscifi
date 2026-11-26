"""Lane G1's validators, registered by media type (DESIGN §6). Importing this module registers them once; the
registry module (`check/__init__.py`) imports it at the merge.

Each validator re-reads the bytes with this package's own readers. It never imports a generator. With a spec (and,
optionally, source data shaped like the spec's `data`) it also measures carried information for the forms it
knows; for a form it does not know it judges safety and layout only.
"""
from __future__ import annotations

from ..types import ArtifactError
from . import Finding, Verdict, register

SVG_FORMS = ("chart_line", "chart_scatter", "chart_bar", "chart_histogram", "chart_heatmap",
             "chart_small_multiples", "timeline", "graph_nodelink", "graph_tree", "flow_steps", "animation_svg")


def spec_parts(spec, source):
    """(form, data, options) from a Spec, a spec dict, or None; `source` (a dict like `data`) wins over spec.data."""
    if spec is None:
        return None, None, {}
    if isinstance(spec, dict):
        form, data, options = spec.get("spec"), spec.get("data"), spec.get("options") or {}
    else:
        form, data, options = getattr(spec, "spec", None), getattr(spec, "data", None), getattr(spec, "options", {})
    if isinstance(source, dict):
        data = source
    return form, data, options or {}


def check_svg(data: bytes, media_type: str, spec=None, source=None) -> Verdict:
    from .layout import check_layout
    from .svg_read import carried, parse
    from .svg_safety import check_svg as safety
    try:
        parsed = parse(data)
    except ArtifactError as error:
        return Verdict.of((Finding("svg_parse", False, f"{error.reason}: {error}"),))
    technical = [Finding("svg_parse", True, f"parsed: {parsed.n_nodes} elements")]
    technical += safety(parsed)
    t2, style = check_layout(parsed.root)
    technical += t2
    form, sdata, options = spec_parts(spec, source)
    if form in SVG_FORMS and isinstance(sdata, dict):
        try:
            t3, s3 = carried(parsed.root, form, sdata, options)
        except (KeyError, TypeError, ValueError, IndexError) as error:
            t3, s3 = [Finding("values_recovered", False,
                              f"the picture could not be read against the spec ({type(error).__name__})", 0.0)], []
        technical += t3
        style += s3
    return Verdict.of(technical, style)


_REGISTERED: list[tuple[str, str]] = []


def register_all() -> None:
    for media_type, fn, name in (("image/svg+xml", check_svg, "g1.svg"),):
        if (media_type, name) in _REGISTERED:
            continue
        register(media_type, fn, name=name)
        _REGISTERED.append((media_type, name))


register_all()

__all__ = ["SVG_FORMS", "check_svg", "register_all", "spec_parts"]
