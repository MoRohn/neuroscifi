"""Main session's driver: the form benchmark's case view -> the product planner -> the benchmark's vocabulary.
Written without reading any held-out case. The two vocabularies were written independently; the mapping is here."""
import json, sys
try:
    from omni_dev import forms as B          # the development-only kit (lane C)
except ImportError:
    from neuroscifi.acceptance.omni import forms as B
from neuroscifi.artifacts import forms as P
from neuroscifi.artifacts.types import ContentShape

TO_BENCH = {"prose": "prose", "spoken": "spoken", "table": "table", "document": "prose", "data_file": "data_file",
            "line_chart": "line_chart", "bar_chart": "bar_chart", "scatter_chart": "scatter_chart",
            "histogram": "histogram", "heatmap": "heatmap", "small_multiples": "line_chart",
            "timeline": "timeline", "node_link": "node_link", "tree_diagram": "node_link", "flow_steps": "flow",
            "step_through": "step_through", "explorable": "explorable", "animation": "animation",
            "sonification": "sonification", "tone": "sonification", "sound_view": "waveform_spectrum",
            "solid_view": "solid_viewer", "surface_view": "solid_viewer", "image": "image_view",
            "image_sequence": "animation"}
FROM_BENCH = {"prose": "prose", "spoken": "spoken", "table": "table", "data_file": "data_file",
              "line_chart": "line_chart", "bar_chart": "bar_chart", "scatter_chart": "scatter_chart",
              "histogram": "histogram", "heatmap": "heatmap", "timeline": "timeline", "node_link": "node_link",
              "flow": "flow_steps", "image_view": "image", "waveform_spectrum": "sound_view",
              "step_through": "step_through", "animation": "animation", "explorable": "explorable",
              "sonification": "sonification", "solid_viewer": "solid_view"}        # "map": the planner has no such form
MEDIA = {"text": ("text",), "picture": ("picture",), "sound": ("sound", "speech"), "interaction": ("interactive",),
         "motion": ("motion",), "file": ("file",), "3d": ("3d",)}
GENERATORS = tuple(sorted({f.generator for f in P.FORMS.values() if f.generator}))
UNMAPPED = []


def choose(view):
    x = view["context"]
    shows = []
    for m in x["shows"]:
        shows.extend(MEDIA[m])
    access = [a for a in x["access"] if a in P.ACCESSIBILITY]
    if "no_sound" in x["access"]:
        shows = [m for m in shows if m not in ("sound", "speech")]
    prefs = {}
    for name in x["prefer"]:
        if name in FROM_BENCH: prefs[FROM_BENCH[name]] = 1.0
        else: UNMAPPED.append(("prefer", name))
    for name in x["avoid"]:
        if name in FROM_BENCH: prefs[FROM_BENCH[name]] = -1.0
        else: UNMAPPED.append(("avoid", name))
    explicit = view["explicit"]
    if explicit is not None:
        parts = [FROM_BENCH.get(p) for p in explicit.split("+")]
        if None in parts:
            UNMAPPED.append(("explicit", explicit)); explicit = None
        else:
            explicit = "+".join(parts)
    purpose = view["purpose"] if view["purpose"] in P.PURPOSES else None
    if view["purpose"] and purpose is None: UNMAPPED.append(("purpose", view["purpose"]))
    try:
        req = P.Request(text=view["request"], explicit_form=explicit, purpose=purpose)
    except Exception:                            # e.g. a combination the planner's Request does not take
        UNMAPPED.append(("explicit-refused", str(view["explicit"]))); req = P.Request(text=view["request"], purpose=purpose)
    content = tuple(ContentShape(kinds=tuple(c["kinds"]), n_items=c["n_items"], n_series=c["n_series"],
                                 has_time=c["has_time"], has_space=c["has_space"],
                                 has_parameters=c["has_parameters"], duration_s=c["duration_s"],
                                 text_chars=c["text_chars"]) for c in view["content"])
    ctx = P.Context(device=x["device"], shows=tuple(dict.fromkeys(shows)), accessibility=tuple(access),
                    preferences=prefs, budget_s=x["budget"]["seconds"], budget_bytes=int(x["budget"]["bytes"]),
                    generators=GENERATORS)
    plan = P.plan(req, content, ctx)
    return "+".join(TO_BENCH[f] for f in plan.chosen)


if __name__ == "__main__":
    split = sys.argv[1]
    out = {"split": split, "planner": B.score(choose, split)}
    for name, ref in B.REFERENCES.items():
        try: out[name] = B.score(ref, split)
        except Exception as e: out[name] = {"error": repr(e)}
    out["unmapped"] = sorted(set(map(str, UNMAPPED)))
    json.dump(out, open(sys.argv[2], "w"), indent=1, default=str)
    def row(n, s):
        keys = [k for k in s if not isinstance(s[k], (dict, list))]
        print(n, {k: s[k] for k in keys})
    for n, s in out.items():
        if isinstance(s, dict): row(n, s)
    print("unmapped:", out["unmapped"])
