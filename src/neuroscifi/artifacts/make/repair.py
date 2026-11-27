"""Bounded repair of a specification from a checker's findings: named rules, no model, never a change of the data.

`repair(spec, verdict) -> spec | None` maps specific failed findings (by their rule names, the checkers' stated
vocabulary) to specific changes of the specification's options or form, records each change in
`options.history` ({round, rule, change}), and returns the new specification (a Spec if given a Spec, else a
dict), or None when no rule applies or three rounds have already been made. The rules, in order:

- tick_labels_overlap_x: rotate the x labels; if already rotated, make the picture 50 % wider (labels are never cut
  by a repair: a reader reads a category from its label's text).
- tick_labels_overlap_y / text_inside_canvas: make the picture 25 % larger (width; height for the y axis).
- too_many_ticks: keep every second tick (x_tick_every doubles).
- series_distinguishable (line or scatter): draw one panel per series (chart.small_multiples, same data).
- points_exceed_pixels (line): aggregate into one bin per four pixels of width with the min-max rule (stated in the chart's
  description).
- bars_from_zero (bar): start the value axis at zero again.

`repair_loop(spec, make, check)` runs make -> check -> repair for at most three rounds and returns the last
(made, verdict, spec); the caller passes its own make and check, so this module imports no checker.
"""
from __future__ import annotations

import copy

from ..spec import Spec, form_schema

MAX_ROUNDS = 3
CHARTS_XY = ("chart.line", "chart.scatter")


def _rounds(options: dict) -> int:
    return max((h.get("round", 0) for h in options.get("history", [])), default=0)


def _allowed(form: str, key: str) -> bool:
    schema = form_schema(form)
    if schema is None:
        return False
    props = schema["options"].get("properties") or {}
    return key in props


def repair(spec, verdict):
    as_spec = isinstance(spec, Spec)
    value = copy.deepcopy(spec.to_dict() if as_spec else spec)
    options = value.setdefault("options", {})
    done = _rounds(options)
    if done >= MAX_ROUNDS:
        return None
    failed = {f.rule for f in list(verdict.technical) + list(verdict.style) if not f.passed}
    form = value["spec"]
    changes: list[tuple[str, str]] = []

    def setopt(key, val, rule, words):
        if _allowed(value["spec"], key) and options.get(key) != val:
            options[key] = val
            changes.append((rule, words))
            return True
        return False

    if "tick_labels_overlap_x" in failed:
        if options.get("x_labels") != "rotated" and _allowed(form, "x_labels"):
            setopt("x_labels", "rotated", "tick_labels_overlap_x", "x labels rotated by 45 degrees")
        elif _allowed(form, "width"):           # labels are read as their text: widen rather than cut them
            w = min(2400, int(options.get("width", 640) * 1.5))
            setopt("width", w, "tick_labels_overlap_x", f"picture made wider ({w} px) so the labels fit")
    if "tick_labels_overlap_y" in failed and _allowed(form, "height"):
        h = int(options.get("height", 400) * 1.25)
        setopt("height", min(2400, h), "tick_labels_overlap_y", f"picture made taller ({min(2400, h)} px)")
    if "text_inside_canvas" in failed and _allowed(form, "width"):
        w = int(options.get("width", 640) * 1.25)
        setopt("width", min(2400, w), "text_inside_canvas", f"picture made wider ({min(2400, w)} px)")
    if "too_many_ticks" in failed:
        every = min(50, int(options.get("x_tick_every", 1)) * 2)
        setopt("x_tick_every", every, "too_many_ticks", f"every {every}th tick kept")
    if "series_distinguishable" in failed and form in CHARTS_XY:
        value["spec"] = "chart.small_multiples"
        for key in [k for k in options if not _allowed("chart.small_multiples", k)]:
            options.pop(key)
        changes.append(("series_distinguishable", f"one panel per series instead of one {form} (same data)"))
    if "points_exceed_pixels" in failed and form == "chart.line" and "aggregate" not in options:
        bins = max(2, int(options.get("width", 640)) // 4)      # two points per bin, at most one per 2 px
        setopt("aggregate", {"rule": "min_max", "bins": bins}, "points_exceed_pixels",
               f"aggregated into {bins} bins (min and max of each, stated in the description)")
    if "bars_from_zero" in failed and form == "chart.bar":
        options.pop("y_min", None)
        setopt("y_from_zero", True, "bars_from_zero", "value axis starts at zero")
    if not changes:
        return None
    history = list(options.get("history", []))
    for rule, words in changes:
        history.append({"round": done + 1, "rule": rule, "change": words})
    options["history"] = history[-12:]
    if value["data"] != (spec.data if as_spec else spec["data"]):
        raise RuntimeError("a repair changed the data")           # cannot happen: only options and form change
    return Spec.from_dict(value) if as_spec else value


def repair_loop(spec, make, check, rounds: int = MAX_ROUNDS):
    """make -> check -> repair, at most `rounds` repairs. Returns (made, verdict, spec) of the last round."""
    current = spec
    made = make(current)
    verdict = check(made, current)
    for _ in range(rounds):
        if verdict.ok and not [f for f in verdict.style if not f.passed]:
            break
        nxt = repair(current, verdict)
        if nxt is None:
            break
        current = nxt
        made = make(current)
        verdict = check(made, current)
    return made, verdict, current


__all__ = ["MAX_ROUNDS", "repair", "repair_loop"]
