"""WITHHELD worlds of the source-independence family (Wave 4 step 0). Written and committed ALONE, after the
development and held-out worlds and before the harness and any source rule. Never edit this file; its SHA-256 is
pinned in the harness (sources.WITHHELD_SHA256) and in a test, and the development file it builds on is pinned there
too.

NO LANE READS, PRINTS, IMPORTS OR RUNS THIS FILE. It is loaded only by sources.final(), which records the look in the
ledger before it loads anything. Its purpose: worlds no rule was written or tuned on, with different proportions from
development and held-out and with shapes and surface forms those splits do not have, so that a source rule's one
look shows what it does on what its author did not see.

The wording of the forms added here comes, like development's, from documented sources and not from any parser:
Creative Commons' recommended attribution ("TASL": title, author, source, licence), the "Source:" credit line of
newsroom style, attribution in other languages as newspapers print it ("laut", "selon", "según"), the MLA Style
Center's works-cited form for generative-AI text (fetched 2026-11-26), and press-release pickup without credit.
"""
from __future__ import annotations

from .sources_splits_dev import (ANSWER, COMMON, ECHO_HOSTS, OUTLETS, PRIMARY, SHAPES, WEIGHTS, _Look, _bare, _far,
                                 _honest, _names, _neighbour, _story_value, _statement, build_look, stream, value_str)

OTHER_CREDITS = {
    "source_line": "{S}.\nSource: {X}",
    "cc_tasl": "{S}.\n\"{N} release news\" by {X} is licensed under CC BY 4.0.",
    "laut": "{S}, laut {X}.",
    "selon": "{S}, selon {X}.",
    "segun": "{S}, según {X}.",
}
MLA_ECHO = "{N} {v} is the latest {N} release.\n\"What is the latest {N} release\" prompt. ChatGPT, 13 Feb. version, " \
           "OpenAI, 8 Mar. 2023, chat.openai.com/chat."
PARTNERS = (("m-techledger.example", "Tech Ledger"), ("stackdaily-partners.example", "Stack Daily"),
            ("codebeat-amp.example", "Codebeat"), ("radar-syndication.example", "Release Radar"))
PRESS_HOSTS = (("prwire-hub.example", "PR Wire Hub"), ("launchnews.example", "Launch News"),
               ("dev-announce.example", "Dev Announce"), ("softpress.example", "Softpress"))
NEW_SHAPES = ("same_outlet_two_hosts", "press_release_pickup", "syndicated_other_forms", "echo_cited")


def build(spec, seed, subject, c, shape, own_value):
    if shape not in NEW_SHAPES:
        return build_look(spec, seed, subject, c, shape, own_value)
    rng = stream(spec, seed, "look", subject["name"], c)
    name, truth, since = subject["name"], subject["truth"][c], subject["since"][c]
    look = _Look(name, c, shape)
    outlets = list(OUTLETS)
    rng.shuffle(outlets)
    if shape == "same_outlet_two_hosts":
        story = _story_value(rng, spec, truth)
        k = rng.randrange(len(PARTNERS))
        partner_host, label = PARTNERS[k]
        main = next(o for o in OUTLETS if o[1] == label)
        first = look.page(main[0], main[1], _statement(COMMON, name, story, main[2]), story, basis="first_hand",
                          published=since, form="outlet")
        look.page(partner_host, label, _statement(COMMON, name, story, OUTLETS[rng.randrange(len(OUTLETS))][2]), story,
                  basis="derived", parents=[first], published=since, form="partner_host")
        if story != truth:
            _honest(look, rng, spec, name, truth, [o for o in outlets if o[1] != label][:rng.randint(0, 2)], since)
    elif shape == "press_release_pickup":
        prim = look.page(f"{name.lower()}-project.example", f"{name} project", PRIMARY.format(N=name, v=value_str(truth)),
                         truth, basis="first_hand", published=since, form="release_page", authoritative=True,
                         read=rng.random() < spec["primary_read"])
        hosts = list(PRESS_HOSTS)
        rng.shuffle(hosts)
        for h in hosts[:rng.randint(2, 3)]:
            look.page(h[0], h[1], f"{name} announced a new version. " + _statement(COMMON, name, truth), truth,
                      basis="derived", parents=[prim], published=since, form="press_release")
    elif shape == "syndicated_other_forms":
        story = _story_value(rng, spec, truth)
        o = outlets.pop()
        origin = look.page(o[0], o[1], _statement(COMMON, name, story, o[2]), story, basis="first_hand",
                           published=since, form="outlet", read=rng.random() < spec["origin_read"])
        hosts = [("feedmill.example", "Feedmill"), ("newsgrab.example", "Newsgrab"), ("techclip.example", "Techclip"),
                 ("linkpile.example", "Linkpile"), ("syndicast.example", "Syndicast")]
        rng.shuffle(hosts)
        for h in hosts[:rng.randint(2, 4)]:
            form = rng.choice(sorted(OTHER_CREDITS))
            stated = _bare(_statement(COMMON, name, story, OUTLETS[rng.randrange(len(OUTLETS))][2]))
            look.page(h[0], h[1], OTHER_CREDITS[form].format(S=stated, X=o[1], N=name), story, basis="derived",
                      parents=[origin], published=since, form=form)
        if story != truth:
            _honest(look, rng, spec, name, truth, outlets[:rng.randint(0, 2)], since)
    else:                                                                        # echo_cited
        hosts = list(ECHO_HOSTS)
        rng.shuffle(hosts)
        for h in hosts[:rng.randint(1, 2)]:
            look.page(h[0], h[1], MLA_ECHO.format(N=name, v=value_str(own_value)), own_value, basis="echo",
                      published=c, form="mla_model")
        if rng.random() < spec["echo_with_honest"]:
            _honest(look, rng, spec, name, truth, outlets[:rng.randint(1, 2)], since)
    return look.out()


ALL_SHAPES = SHAPES + NEW_SHAPES
_W = {"independent": 20, "primary_cited": 8, "verified_credit": 8, "syndicated_credit": 8, "syndicated_nocredit": 10,
      "two_origins": 4, "extra_origin": 4, "wire_like_host": 5, "roundup": 4, "mirror_dates": 6, "correction": 5,
      "stale": 8, "echo": 8, "same_outlet_two_hosts": 6, "press_release_pickup": 6, "syndicated_other_forms": 8,
      "echo_cited": 5}
assert set(_W) == set(ALL_SHAPES) and set(WEIGHTS) == set(SHAPES)
_BASE = {"cycles": 12, "cycle_hours": 24.0, "arrivals": 1, "revisits": 4, "retract": 0.03, "primary_read": 0.45,
         "origin_read": 0.45, "echo_with_honest": 0.45}

SPLIT = "withheld"
CYCLES = {
    SPLIT: {
        "seeds": (8501, 8502, 8503, 8504),
        "worlds": (
            _BASE | {"name": "src-wh-a", "subjects": 12, "change": 0.14, "weights": _W, "story_true": 0.5,
                     "story_neighbour": 0.35, "convergent": 0.3, "own_per_cycle": 2, "own_right": 0.35,
                     "own_neighbour": 0.45},
            _BASE | {"name": "src-wh-b", "subjects": 12, "change": 0.10,
                     "weights": _W | {"independent": 12, "syndicated_nocredit": 14, "syndicated_other_forms": 12,
                                      "echo": 10, "echo_cited": 8, "same_outlet_two_hosts": 8},
                     "story_true": 0.4, "story_neighbour": 0.4, "convergent": 0.35, "own_per_cycle": 3,
                     "own_right": 0.3, "own_neighbour": 0.5},
        ),
    },
}


def _draw(rng, weights, *, echo_ok, stale_ok):
    names = [x for x in ALL_SHAPES if weights.get(x) and (echo_ok or x not in ("echo", "echo_cited"))
             and (stale_ok or x != "stale")]
    return rng.choices(names, weights=[weights[x] for x in names])[0]


def generate(spec: dict, seed: int) -> dict:
    """The withheld world of one run: development's subjects, schedule and own answers, drawn as there, with the
    shapes above (development's generate draws only development's shapes, so it is written out here)."""
    n = spec["cycles"]
    total = spec["subjects"] + spec["arrivals"] * n
    names = _names(stream(spec, seed, "names"), total)
    subjects = []
    for i, name in enumerate(names):
        rng = stream(spec, seed, "truth", name)
        born = 0 if i < spec["subjects"] else 1 + (i - spec["subjects"]) // spec["arrivals"]
        v = (rng.randint(1, 6), rng.randint(0, 15), rng.randint(0, 4))
        history = [v]
        truth, since, start = {}, {}, born
        for c in range(0, n + 1):
            if c > born:
                roll = rng.random()
                if roll < spec["change"]:
                    v = (v[0], v[1], v[2] + 1) if rng.random() < 0.3 else (v[0], v[1] + 1, 0)
                    history.append(v)
                    start = c
                elif roll < spec["change"] + spec["retract"] and len(history) > 1:
                    history.pop()
                    v = history[-1]
                    start = c
            if c >= born:
                truth[c], since[c] = v, start
        subjects.append({"name": name, "born": born, "truth": truth, "since": since})
    by_name = {s["name"]: s for s in subjects}
    schedule, looks, own, last, own_now = {}, {}, {}, {}, {}
    for c in range(0, n + 1):
        births = sorted(s["name"] for s in subjects if s["born"] == c)
        older = sorted((s for s in subjects if s["born"] < c), key=lambda s: (last[s["name"]], s["name"]))
        schedule[c] = births + ([s["name"] for s in older[:spec["revisits"]]] if c >= 1 else [])
        orng = stream(spec, seed, "own", c)
        own[c] = []
        if c >= 1:
            for name in orng.sample(schedule[c], min(spec["own_per_cycle"], len(schedule[c]))):
                t = by_name[name]["truth"][c]
                roll = orng.random()
                value = t if roll < spec["own_right"] else _neighbour(orng, t) \
                    if roll < spec["own_right"] + spec["own_neighbour"] else _far(orng, t)
                own[c].append({"subject": name, "value": value_str(value), "right": value == t,
                               "text": ANSWER.format(N=name, v=value_str(value))})
        for name in schedule[c]:
            s = by_name[name]
            stale_ok = s["since"][c] > s["born"] and s["truth"][s["since"][c] - 1] != s["truth"][c]
            shape = _draw(stream(spec, seed, "shape", name, c), spec["weights"], echo_ok=name in own_now,
                          stale_ok=stale_ok)
            looks[(name, c)] = build(spec, seed, s, c, shape, own_now.get(name))
            last[name] = c
        for item in own[c]:
            own_now[item["subject"]] = tuple(int(x) for x in item["value"].split("."))
    return {"spec": spec, "seed": seed,
            "subjects": [{"name": s["name"], "born": s["born"], "truth": {c: value_str(v) for c, v in s["truth"].items()},
                          "since": dict(s["since"])} for s in subjects],
            "schedule": schedule, "looks": looks, "own": own}
