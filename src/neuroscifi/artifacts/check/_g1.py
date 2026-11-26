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


HTML_FORMS = ("table_html", "document_html")
MD_FORMS = ("document_md",)
DATA_FORMS = ("data_csv", "data_json")


def _foreign(form, mine) -> bool:
    """A spec of a form this lane does not make: leave the verdict to that form's own validator."""
    return form is not None and form not in mine


def check_html(data: bytes, media_type: str, spec=None, source=None) -> Verdict:
    from .documents import check_html as run
    form, sdata, options = spec_parts(spec, source)
    if _foreign(form, HTML_FORMS):
        return Verdict((), (), False)
    technical, style = run(data, form, sdata if form == "table_html" or isinstance(source, dict) else None, options)
    return Verdict.of(technical, style)


def check_markdown(data: bytes, media_type: str, spec=None, source=None) -> Verdict:
    from .documents import check_markdown as run
    form, _sdata, _options = spec_parts(spec, source)
    if _foreign(form, MD_FORMS):
        return Verdict((), (), False)
    technical, style = run(data)
    return Verdict.of(technical, style)


def check_csv(data: bytes, media_type: str, spec=None, source=None) -> Verdict:
    from .datafiles import check_csv as run
    form, sdata, _options = spec_parts(spec, source if isinstance(source, dict) else None)
    if _foreign(form, DATA_FORMS):
        return Verdict((), (), False)
    return Verdict.of(run(data, source if source is not None and not isinstance(source, dict) else sdata))


def check_json(data: bytes, media_type: str, spec=None, source=None) -> Verdict:
    from .datafiles import check_json as run
    form, sdata, _options = spec_parts(spec, source if isinstance(source, dict) else None)
    if _foreign(form, DATA_FORMS):
        return Verdict((), (), False)
    return Verdict.of(run(data, source if source is not None and not isinstance(source, dict) else sdata))


_REGISTERED: list[tuple[str, str]] = []
VALIDATORS = (("image/svg+xml", check_svg, "g1.svg"), ("text/html", check_html, "g1.html"),
              ("text/markdown", check_markdown, "g1.markdown"), ("text/csv", check_csv, "g1.csv"),
              ("application/json", check_json, "g1.json"))


def register_all() -> None:
    for media_type, fn, name in VALIDATORS:
        if (media_type, name) in _REGISTERED:
            continue
        register(media_type, fn, name=name)
        _REGISTERED.append((media_type, name))


register_all()

__all__ = ["DATA_FORMS", "HTML_FORMS", "MD_FORMS", "SVG_FORMS", "VALIDATORS", "check_csv", "check_html",
           "check_json", "check_markdown", "check_svg", "register_all", "spec_parts"]
