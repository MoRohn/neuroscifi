"""Random benchmark views through the driver (scripts/omni_forms_driver.py), each answer judged by the benchmark's
own hard-constraint rule `violation(form, case)`; prints how many views were drawn, how many answers broke a
constraint, after how many views the first did, and the classes (form and constraint).

    PYTHONPATH=src:<folder holding omni_dev> python scripts/omni_forms_violation_check.py [N] [SEED]

Random views: content shapes of every kind and size, every device, random subsets of what it shows, access needs,
budgets down to zero bytes and seconds, explicit forms (single and combined), preferences, purposes known and not.
"""
import collections
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omni_forms_driver as DR  # noqa: E402

try:
    from omni_dev import forms as BM      # the development-only kit
except ImportError:
    from neuroscifi.acceptance.omni import forms as BM  # noqa: F401
KINDS = ("scalar series categories distribution relation hierarchy network sequence timeline process surface solid "
         "map image sound prose code records").split()
SHOW = ("text", "picture", "sound", "interaction", "motion", "3d", "file")
DEV = {"console": SHOW, "pocket": SHOW, "voice": ("sound",), "cli": ("text", "file"), "agent": ("text", "file")}
ACCESS = ("voice_only", "screen_reader", "reduced_motion", "no_sound")
WORDS = ("", "How did it change?", "Is it going up?", "Show me everything", "Put it in a table", "plot it",
         "read it aloud", "What does it sound like?", "Just tell me", "Export it as a file", "Animate it",
         "Which is the largest?", "What if I change the rate?", "Where are they?")
def shape(r):
    ks = r.sample(KINDS, r.choice((1, 1, 2, 3)))
    sp = r.choice((0, 0, 2, 3))
    if {"map", "image"} & set(ks): sp = max(sp, 2)
    return {"kinds": tuple(ks), "n_items": r.choice((1, 2, 4, 7, 30, 365, 5000, 200000)), "n_series": r.choice((1, 1, 2, 6, 30)),
            "has_time": r.random() < .5, "has_space": sp, "has_parameters": r.random() < .3,
            "duration_s": r.choice((0.0, 12.0, 180.0)), "text_chars": r.choice((0, 0, 400, 9000))}
def view(r):
    dev = r.choice(tuple(DEV))
    shows = tuple(sorted(DEV[dev] if r.random() < .6 else r.sample(SHOW, r.randint(1, len(SHOW)))))
    names = tuple(BM.FORMS)
    ctx = {"device": dev, "shows": shows, "access": tuple(r.sample(ACCESS, r.choice((0, 0, 1, 2)))),
           "prefer": tuple(r.sample(names, r.choice((0, 0, 0, 1)))), "avoid": tuple(r.sample(names, r.choice((0, 0, 1)))),
           "budget": {"seconds": r.choice((0.0, 0.01, 0.05, 0.25, 0.3, 0.6, 1.0, 1.5, 60.0)), "bytes": r.choice((0, 100, 300, 301, 900, 3072, 4000, 8192, 9000, 25000, 1 << 23))}}
    explicit = r.choice((None,) * 5 + names + ("line_chart+prose", "bar_chart+table"))
    return {"id": "r", "request": r.choice(WORDS), "explicit": explicit,
            "purpose": r.choice((None, None, "learn", "decide", "wonder", "understand", "compare")),
            "content": [shape(r) for _ in range(r.choice((1, 1, 1, 2)))], "context": ctx}
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
r = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
first, classes, ex = None, collections.Counter(), {}
for i in range(N):
    v = view(r)
    form = DR.choose(v)
    why = BM.violation(form, {"context": v["context"], "content": v["content"]})
    if not BM.norm(form):
        why = None  # no form: not a violation of a constraint (the benchmark labels it unfit)
    if why:
        if first is None: first = i + 1
        key = form + " :: " + why.split(";")[0].split(" over the budget")[0][:60]
        classes[form + " " + ("budget" if "budget" in why else "media" if "needs" in why else "reader")] += 1
        ex.setdefault(classes.most_common()[0][0], (form, why, v["context"], v["explicit"], v["request"], [c["kinds"] for c in v["content"]]))
print("views", N, "violations", sum(classes.values()), "first at", first)
for k, c in classes.most_common(12): print(c, k)
for k, e in list(ex.items())[:6]: print(e)
