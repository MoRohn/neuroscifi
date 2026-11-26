"""OMNIMIND benchmark, family `forms`: is the chosen form of a result right? (lane ZF; docs/quality/
OMNIMIND_BENCHMARK_FORMS.md has the design, the measures and what they cannot show.)

The subject is a form chooser: `choose(view) -> str | {"form": str, ...}` (or a plain callable), where `view` is a case
without the examiner's sets: {"id", "request", "explicit", "purpose", "content": [ContentShape dicts], "context"}.
The answer is a form name of the benchmark's vocabulary (FORMS below), or a combination "a+b". The main session's
driver maps the product planner's own names onto these; this module shares no name table, phrase list or code with
product code and imports nothing from `neuroscifi.artifacts`.

Each chosen form is labelled for its case: violation (a hard constraint of the context is broken), else the set the
examiner listed it in (good, fine, too_poor, too_much, unfit), else the label of a combination derived from its parts
(COMBINATION RULE below), else the case's `rest` label. Standard library only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import re

from . import forms_cases

# ---- the vocabulary -----------------------------------------------------------------------------------------------
# needs: alternatives, each a set of capabilities the device must show; cost floor: (seconds, bytes, bytes per value)
# The floors are deliberately low (a lower bound of what any generator needs): a planner whose own estimate is higher
# may exclude more forms, which is never counted against it; a violation is a chosen form whose FLOOR is over budget.
_PIC = (frozenset({"picture"}),)
FORMS = {
    "prose":             {"needs": (frozenset({"text"}), frozenset({"sound"})), "cost": (0.0, 300, 0),
                          "what": "sentences (read, or spoken by a voice device)"},
    "spoken":            {"needs": (frozenset({"sound"}),), "cost": (0.5, 8192, 0),
                          "what": "a short summary written for the ear and spoken"},
    "table":             {"needs": (frozenset({"text"}),), "cost": (0.0, 200, 8), "what": "rows and columns"},
    "data_file":         {"needs": (frozenset({"file"}),), "cost": (0.0, 100, 6),
                          "what": "a file of the data (CSV, JSON) to open elsewhere"},
    "line_chart":        {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "lines over an ordered axis"},
    "bar_chart":         {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "bars per category"},
    "scatter_chart":     {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "points of one measure against another"},
    "histogram":         {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "counts per bin of one measure"},
    "heatmap":           {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "a shaded grid"},
    "timeline":          {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "dated events on a time axis"},
    "node_link":         {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "a node-link diagram (graph or tree)"},
    "flow":              {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "a flow of steps or states"},
    "map":               {"needs": _PIC, "cost": (0.2, 3072, 0), "what": "places on a map"},
    "image_view":        {"needs": _PIC, "cost": (0.2, 3072, 0),
                          "what": "a raster picture shown or derived (crop, edge map, rendered view)"},
    "waveform_spectrum": {"needs": _PIC, "cost": (0.3, 6144, 0),
                          "what": "a sound's waveform and spectrum view, with playback where sound is available"},
    "step_through":      {"needs": (frozenset({"picture", "interaction"}),), "cost": (0.3, 8192, 0),
                          "what": "frames the person steps through at their own pace"},
    "animation":         {"needs": (frozenset({"picture", "motion"}),), "cost": (1.0, 30720, 0),
                          "what": "frames that play by themselves"},
    "explorable":        {"needs": (frozenset({"picture", "interaction"}),), "cost": (0.5, 20480, 0),
                          "what": "a model whose parameters the person varies (sliders)"},
    "sonification":      {"needs": (frozenset({"sound"}),), "cost": (0.3, 16384, 0),
                          "what": "data heard as tones"},
    "solid_viewer":      {"needs": (frozenset({"3d", "interaction"}),), "cost": (0.5, 20480, 0),
                          "what": "a solid or surface the person turns"},
}
TEXTUAL = frozenset({"prose", "spoken", "table", "data_file"})     # parts a screen reader can present
VISUAL = frozenset(f for f, d in FORMS.items()
                   if all(alt & {"picture", "3d"} for alt in d["needs"]))  # parts that are pictures only
LABELS = ("good", "fine", "too_poor", "too_much", "unfit", "violation")
_SET_LABEL = {"good": "good", "fine": "fine", "poor": "too_poor", "much": "too_much", "unfit": "unfit"}
SPLITS = ("development", "held_out")
LABEL = ("OMNIMIND benchmark, family forms: hand-written requests and contexts, judged by the examiner (lane ZF); "
         "offline; no model; a planner's choice is compared with the examiner's sets, written before any planner")


@dataclass(frozen=True)
class Task:
    """One case as the subject sees it (`view`) and as the examiner judged it."""
    id: str
    split: str
    view: dict
    case: dict


def norm(form) -> str:
    """'b + a' -> 'a+b'; a dict answer's "form"; lower case; duplicates dropped."""
    if isinstance(form, dict):
        form = form.get("form")
    parts = sorted({p.strip().lower() for p in str(form or "").split("+") if p.strip()})
    return "+".join(parts)


def tasks(split: str) -> list[Task]:
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    out = []
    for case in forms_cases.ALL[split]:
        view = {"id": case["id"], "request": case["request"], "explicit": case["explicit"],
                "purpose": case["purpose"], "content": [dict(s) for s in case["content"]],
                "context": {k: (dict(v) if isinstance(v, dict) else v) for k, v in case["context"].items()}}
        out.append(Task(case["id"], split, view, case))
    return out


# ---- hard constraints ---------------------------------------------------------------------------------------------

def effective_shows(context: dict) -> frozenset:
    shows = set(context["shows"])
    access = set(context["access"])
    if "voice_only" in access:
        shows &= {"sound"}
    if "no_sound" in access:
        shows.discard("sound")
    if "reduced_motion" in access:
        shows.discard("motion")
    return frozenset(shows)


def n_values(content) -> int:
    return sum(int(s["n_items"]) * max(1, int(s["n_series"])) for s in content)


def violation(form: str, case: dict) -> str | None:
    """Why `form` breaks a hard constraint of the case's context, or None. Unknown names are not violations (they are
    labelled unfit elsewhere)."""
    parts = [p for p in norm(form).split("+") if p in FORMS]
    context = case["context"]
    shows = effective_shows(context)
    for p in parts:
        if not any(alt <= shows for alt in FORMS[p]["needs"]):
            need = " or ".join("+".join(sorted(a)) for a in FORMS[p]["needs"])
            return f"{p} needs {need}; this context shows {'+'.join(sorted(shows)) or 'nothing'}"
    if "screen_reader" in context["access"] and any(p in VISUAL for p in parts) \
            and not any(p in TEXTUAL for p in parts):
        return "a screen-reader user gets a picture with no text-carrying part"
    seconds = sum(FORMS[p]["cost"][0] for p in parts)
    size = sum(FORMS[p]["cost"][1] + FORMS[p]["cost"][2] * n_values(case["content"]) for p in parts)
    budget = context["budget"]
    if seconds > budget["seconds"] or size > budget["bytes"]:
        return (f"cost floor {seconds:g} s / {size} bytes is over the budget {budget['seconds']:g} s / "
                f"{budget['bytes']} bytes")
    return None


def feasible_singles(case: dict) -> list[str]:
    return [f for f in FORMS if violation(f, case) is None]


# ---- labels -------------------------------------------------------------------------------------------------------

def _listed(case: dict, form: str):
    for name, entry in case["sets"].items():
        if form in {norm(f) for f in entry["forms"]}:
            return _SET_LABEL[name], entry["reason"]
    return None


def label(case: dict, form) -> tuple[str, str]:
    """(label, reason) for one chosen form. COMBINATION RULE for a combination the examiner did not list: a too_much
    part makes it too_much; all parts good or fine makes it fine; a good or fine part with too_poor parts makes it fine
    (the rich part carries the content, the poor one captions it); a good or fine part with an unfit part makes it
    too_much (extra material of the wrong kind); otherwise unfit if any part is unfit, else too_poor."""
    form = norm(form)
    if not form:
        return "unfit", "no form was chosen"
    unknown = [p for p in form.split("+") if p not in FORMS]
    if unknown:
        return "unfit", f"not a form of the benchmark's vocabulary: {', '.join(unknown)}"
    why = violation(form, case)
    if why:
        return "violation", why
    hit = _listed(case, form)
    if hit:
        return hit
    parts = form.split("+")
    if len(parts) == 1:
        return _SET_LABEL[case["rest"]] if case["rest"] in _SET_LABEL else case["rest"], "not listed: the case's rest"
    labels = [label(case, p)[0] for p in parts]
    carried = any(x in ("good", "fine") for x in labels)
    if "too_much" in labels:
        return "too_much", "combination with a part that is too much"
    if all(x in ("good", "fine") for x in labels):
        return "fine", "combination of acceptable parts, not listed"
    if carried and "unfit" not in labels:
        return "fine", "combination: an acceptable part carries it"
    if carried:
        return "too_much", "combination: an acceptable part plus one of the wrong kind"
    return ("unfit" if "unfit" in labels else "too_poor"), "combination of parts that do not serve"


def explicit_feasible(case: dict) -> bool | None:
    return None if not case["explicit"] else violation(case["explicit"], case) is None


# ---- reference choosers (inside the benchmark: no product code) ----------------------------------------------------

RICHNESS = ("solid_viewer", "explorable", "animation", "step_through", "map", "heatmap", "node_link", "timeline",
            "flow", "scatter_chart", "line_chart", "histogram", "bar_chart", "waveform_spectrum", "image_view",
            "sonification", "table", "data_file", "spoken", "prose")
# "content shape only": the dominant content kind decides, no words (the ten-line table the brief asks for)
SHAPE_ONLY = {
    "scalar": "prose", "prose": "prose", "code": "prose", "records": "table",
    "series": "line_chart", "timeline": "timeline", "sequence": "flow", "process": "explorable",
    "categories": "bar_chart", "distribution": "histogram", "relation": "scatter_chart", "surface": "heatmap",
    "hierarchy": "node_link", "network": "node_link", "map": "map", "solid": "solid_viewer",
    "image": "image_view", "sound": "waveform_spectrum",
}


def _first_feasible(case, order):
    for f in order:
        if violation(f, case) is None:
            return f
    return "prose"


def always_prose(view):
    return "prose"


def richest_feasible(view):
    return _first_feasible(view, RICHNESS)


def shape_only(view):
    kind = view["content"][0]["kinds"][0]
    return _first_feasible(view, (SHAPE_ONLY[kind], "table", "prose", "spoken"))


def first_good(task: Task):
    """Not a reference: the examiner's own first good form (a scoring check; must score 1)."""
    return task.case["sets"]["good"]["forms"][0]


REFERENCES = {
    "today": ("always prose: today's system, which answers every request with sentences", always_prose),
    "richest": ("always the richest form the context can carry (RICHNESS order), whatever the request",
                richest_feasible),
    "random": ("a uniformly random form among those the context can carry: the exact expectation", None),
    "shape_only": ("content shape only: the dominant content kind decides through SHAPE_ONLY, no words", shape_only),
}


# ---- scoring --------------------------------------------------------------------------------------------------------

def _frac(k, n):
    return {"k": round(k, 3), "n": n, "share": round(k / n, 4) if n else None}


def _pick(subject, task):
    """[(form, weight)]: one answer, or for the random reference the uniform distribution over feasible forms."""
    if subject == "random":
        options = feasible_singles(task.case)
        return [(f, 1.0 / len(options)) for f in options]
    if subject == "first_good":
        return [(first_good(task), 1.0)]
    chooser = getattr(subject, "choose", subject)
    return [(norm(chooser(task.view)), 1.0)]


def _resolve(subject):
    if isinstance(subject, str) and subject in REFERENCES:
        return REFERENCES[subject][1] or subject
    return subject


def score(subject, split: str, *, detail: bool = False) -> dict:
    """The measures for one subject on one split, each with its denominator. `subject`: a chooser object or callable,
    or a reference name (REFERENCES, plus "first_good")."""
    subject = _resolve(subject)
    rows = []
    for task in tasks(split):
        for form, w in _pick(subject, task):
            lab, why = label(task.case, form)
            rows.append((task, form, w, lab, why))
    n = len(tasks(split))
    count = Counter()
    for _, _, w, lab, _ in rows:
        count[lab] += w
    bad = count["too_poor"] + count["too_much"] + count["unfit"] + count["violation"]
    out = {"label": LABEL, "split": split, "n_cases": n,
           "good": _frac(count["good"], n), "good_or_fine": _frac(count["good"] + count["fine"], n),
           "bad": {**_frac(bad, n), "too_poor": _frac(count["too_poor"], n), "too_much": _frac(count["too_much"], n),
                   "unfit": _frac(count["unfit"], n), "violation": _frac(count["violation"], n)},
           "hard_constraints_violated": _frac(count["violation"], n)}
    # explicit requests: a feasible one is honoured when the chosen form is it (alone or in a combination); an
    # infeasible one is handled when the chosen form is one the examiner judged good for it
    ef = [r for r in rows if r[0].case["explicit"] and explicit_feasible(r[0].case)]
    ei = [r for r in rows if r[0].case["explicit"] and not explicit_feasible(r[0].case)]
    n_ef = len({r[0].id for r in ef})
    n_ei = len({r[0].id for r in ei})
    honoured = sum(w for t, f, w, _, _ in ef if t.case["explicit"] in f.split("+"))
    handled = sum(w for _, _, w, lab, _ in ei if lab == "good")
    out["explicit"] = {"feasible_honoured": _frac(honoured, n_ef), "infeasible_handled": _frac(handled, n_ei),
                       "infeasible_chosen_anyway": _frac(sum(w for t, f, w, _, _ in ei
                                                             if t.case["explicit"] in f.split("+")), n_ei)}
    out["slices"] = _slices(rows)
    out["pairs"] = _pairs(rows)
    if detail:
        out["cases"] = [{"id": t.id, "form": f, "weight": round(w, 4), "label": lab, "why": why}
                        for t, f, w, lab, why in rows]
    return out


def _slice_of(rows, keep):
    sel = [r for r in rows if keep(r[0].case)]
    n = len({r[0].id for r in sel})
    g = sum(w for _, _, w, lab, _ in sel if lab == "good")
    gf = sum(w for _, _, w, lab, _ in sel if lab in ("good", "fine"))
    return {"n": n, "good": round(g, 3), "good_or_fine": round(gf, 3),
            "good_share": round(g / n, 4) if n else None}


def prose_right(case) -> bool:
    return any(p in ("prose", "spoken") for p in case["sets"]["good"]["forms"])


def _slices(rows) -> dict:
    out = {"cue": {}, "tag": {}, "device": {}, "kind": {}}
    for cue in ("content", "words", "conflict", "explicit"):
        out["cue"][cue] = _slice_of(rows, lambda c, cue=cue: cue in c["tags"])
    for tag in ("less", "access", "budget", "prefs", "ex72", "counter72"):
        out["tag"][tag] = _slice_of(rows, lambda c, tag=tag: tag in c["tags"])
    for dev in ("console", "pocket", "voice", "cli", "agent"):
        out["device"][dev] = _slice_of(rows, lambda c, dev=dev: c["context"]["device"] == dev)
    kinds = sorted({c["content"][0]["kinds"][0] for c in (r[0].case for r in rows)})
    for kind in kinds:
        out["kind"][kind] = _slice_of(rows, lambda c, kind=kind: c["content"][0]["kinds"][0] == kind)
    out["prose_is_right"] = _slice_of(rows, prose_right)
    out["prose_is_not_right"] = _slice_of(rows, lambda c: not prose_right(c))
    return out


def _pairs(rows) -> dict:
    """Both members good: for cue pairs (same words, different content) and twins (same content, different words).
    A chooser that ignores the words can get at most one member of most twins right; one that ignores the content, at
    most one member of most cue pairs."""
    good = defaultdict(float)
    for t, _, w, lab, _ in rows:
        if lab == "good":
            good[t.id] += w
    out = {}
    for key in ("pair", "twin"):
        groups = defaultdict(list)
        for t in {r[0].id: r[0] for r in rows}.values():
            ref = t.case[key]
            if ref:
                groups[ref[0] if key == "pair" else ref].append(t.id)
        both = sum(good[a] * good[b] for a, b in (sorted(v) for v in groups.values()))
        out[key] = {"n": len(groups), "both_good": round(both, 3),
                    "share": round(both / len(groups), 4) if groups else None}
    return out


# ---- the wording discipline: statistics over the tables -----------------------------------------------------------

_TOKEN = re.compile(r"[a-z][a-z']+")


def tokens(text: str) -> set[str]:
    return {t.strip("'") for t in _TOKEN.findall(text.lower()) if len(t.strip("'")) >= 3}


def _good_forms(case) -> set[str]:
    return {norm(f) for f in case["sets"]["good"]["forms"]}


def _features(case, coarse: bool):
    def bucket(n):
        return 0 if n <= 1 else 1 if n <= 7 else 2 if n <= 60 else 3 if n <= 1000 else 4
    shapes = []
    for s in case["content"]:
        if coarse:
            shapes.append((s["kinds"][0], bucket(s["n_items"]), s["n_series"] > 1, s["has_parameters"]))
        else:
            shapes.append(tuple(sorted((k, v if not isinstance(v, list) else tuple(v)) for k, v in s.items())))
    ctx = case["context"]
    return (tuple(shapes), ctx["device"], ctx["shows"], ctx["access"], ctx["prefer"], ctx["avoid"],
            ctx["budget"]["seconds"], ctx["budget"]["bytes"])


def _blind_oracle(cases, coarse: bool) -> dict:
    """The best ONE form per group of cases that are identical once the words (and the named form) are removed: an
    upper bound on any chooser that does not read the words."""
    groups = defaultdict(list)
    for c in cases:
        groups[_features(c, coarse)].append(c)
    right = 0
    for members in groups.values():
        options = {f for c in members for f in c["sets"]["good"]["forms"]}
        right += max(sum(norm(f) in {norm(g) for g in c["sets"]["good"]["forms"]} for c in members)
                     for f in options)
    return {"groups": len(groups), "cases": len(cases), "best_good": right,
            "share": round(right / len(cases), 4)}


def wording(min_requests: int = 6) -> dict:
    """The statistics the brief asks for: words per request, cue-word purity (for each word in at least
    `min_requests` requests, the largest share of those requests for which one same whole form is good: what the
    best rule "this word -> that form" would score on them), the
    words-blind oracle at two granularities, the examiner's cue tags, the pairs and twins, and a words-only chooser
    (nearest development request by shared words) scored on held-out."""
    cases = [c for split in SPLITS for c in forms_cases.ALL[split]]
    lengths = [len(c["request"].split()) for c in cases]
    by_word = defaultdict(list)
    for c in cases:
        for t in tokens(c["request"]):
            by_word[t].append(c)
    base = Counter(f for c in cases for f in _good_forms(c))
    purity = []
    for word, members in by_word.items():
        if len(members) < min_requests:
            continue
        counts = Counter(f for c in members for f in _good_forms(c))
        form, k = counts.most_common(1)[0]
        purity.append({"word": word, "requests": len(members), "form": form, "share": round(k / len(members), 3),
                       "form_base_rate": round(base[form] / len(cases), 3)})
    purity.sort(key=lambda r: (-r["share"], -r["requests"], r["word"]))
    cue = Counter(next(t for t in c["tags"] if t in ("content", "words", "conflict", "explicit")) for c in cases)
    dev, held = forms_cases.DEVELOPMENT, forms_cases.HELD_OUT
    nearest_good = 0
    for c in held:
        words = tokens(c["request"])
        best = max(dev, key=lambda d: (len(words & tokens(d["request"])) /
                                       max(1, len(words | tokens(d["request"]))), d["id"]))
        guess = norm(best["sets"]["good"]["forms"][0])
        nearest_good += label(c, guess)[0] == "good"
    return {"requests": len(cases), "distinct_requests": len({c["request"] for c in cases}),
            "words_per_request": {"mean": round(sum(lengths) / len(lengths), 2), "min": min(lengths),
                                  "max": max(lengths)},
            "distinct_words": len(by_word),
            "cue_word_purity": {"min_requests": min_requests, "words": len(purity),
                                "max_share": purity[0]["share"] if purity else None, "top": purity[:12]},
            "cue_tags": dict(sorted(cue.items())),
            "content_and_context_decide_by_tag": round(cue["content"] / len(cases), 4),
            "words_blind_oracle": {"exact_features": _blind_oracle(cases, False),
                                   "coarse_features": _blind_oracle(cases, True)},
            "pairs_same_cue_different_answer": len({c["pair"][0] for c in cases if c["pair"]}),
            "twins_same_content_different_answer": len({c["twin"] for c in cases if c["twin"]}),
            "words_only_nearest_development_request_on_held_out": _frac(nearest_good, len(held))}


def table_stats() -> dict:
    """Counts the brief sets bounds on, per split."""
    out = {}
    for split in SPLITS:
        cases = forms_cases.ALL[split]
        kinds = sorted({k for c in cases for s in c["content"] for k in s["kinds"]})
        out[split] = {
            "cases": len(cases), "prose_right": sum(prose_right(c) for c in cases),
            "prose_alone_right": sum("prose" in c["sets"]["good"]["forms"] for c in cases),
            "explicit": sum(bool(c["explicit"]) for c in cases),
            "explicit_infeasible": sum(c["explicit"] is not None and not explicit_feasible(c) for c in cases),
            "explicit_less": sum("less" in c["tags"] for c in cases),
            "access": dict(Counter(a for c in cases for a in c["context"]["access"])),
            "budget": sum("budget" in c["tags"] for c in cases),
            "devices": dict(Counter(c["context"]["device"] for c in cases)),
            "content_kinds": kinds, "pairs": len({c["pair"][0] for c in cases if c["pair"]}),
            "twins": len({c["twin"] for c in cases if c["twin"]}),
            "ex72": sum("ex72" in c["tags"] for c in cases), "counter72": sum("counter72" in c["tags"] for c in cases)}
    return out


def baseline() -> dict:
    return {split: {name: {"what": REFERENCES[name][0], **score(name, split)} for name in REFERENCES}
            for split in SPLITS}
