"""OMNIMIND benchmark, family `learn`: SIMULATED people (development and held-out). Not real people: each is a hidden,
stable preference over forms per content kind and device, plus habits (which kinds and devices they use) and how they
react (how often they are inconsistent, how often they say what they would have wanted).

FROZEN: tests/acceptance/test_omni_forms_bench.py pins this file's SHA-256. Data only. The held-out people have
preference profiles that no development person has (the test checks it on the full preference map).

Fields:
  order       forms in the person's order of preference, used where no more specific order applies
  by_device   {device: order} overriding `order` on that device
  by_kind     {kind: order} overriding both for that content kind
  never       forms the person cannot use: always rejected (e.g. sound for someone who cannot hear it)
  kinds       {content kind: weight} of their requests;   devices   {device: weight}
  noise       chance a reaction is the opposite of their preference (people are inconsistent)
  replace     chance that, rejecting a form, they say which form they wanted (else they just dismiss it)
  accept_second  chance they keep their second choice
"""
from __future__ import annotations

_CHARTS = ["line_chart", "bar_chart", "histogram", "scatter_chart", "node_link", "timeline", "heatmap",
           "waveform_spectrum"]


def Person(pid, label, *, order, by_device=None, by_kind=None, never=(), kinds, devices, noise=0.1, replace=0.8,
           accept_second=0.5):
    return {"id": pid, "label": label, "order": tuple(order), "by_device": {k: tuple(v) for k, v in
                                                                            (by_device or {}).items()},
            "by_kind": {k: tuple(v) for k, v in (by_kind or {}).items()}, "never": tuple(never),
            "kinds": dict(kinds), "devices": dict(devices), "noise": noise, "replace": replace,
            "accept_second": accept_second}


DEVELOPMENT = (
    Person("D1", "reads charts wherever there is one; a single value stays a sentence",
           order=[*_CHARTS, "table", "prose"], by_device={"voice": ["spoken", "prose"]},
           by_kind={"scalar": ["prose"]},
           kinds={"series": 4, "categories": 3, "scalar": 2, "distribution": 2, "network": 1, "relation": 2,
                  "timeline": 1, "hierarchy": 1},
           devices={"console": 3, "pocket": 2, "voice": 1}),
    Person("D2", "prefers tables to charts", order=["table", "prose"], by_device={"voice": ["spoken"]},
           kinds={"series": 3, "categories": 3, "records": 2, "relation": 2, "scalar": 1, "distribution": 2,
                  "timeline": 1, "network": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}),
    Person("D3", "wants the simplest thing, always", order=["prose", "table"],
           by_device={"voice": ["spoken", "prose"]},
           kinds={"series": 2, "categories": 2, "scalar": 2, "process": 2, "hierarchy": 1, "timeline": 1,
                  "distribution": 1, "records": 1, "sound": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}),
    Person("D4", "cannot use sound; charts on the console, tables on the phone",
           order=[*_CHARTS, "table", "prose"], by_device={"pocket": ["table", "prose"]},
           never=["sonification", "spoken"],
           kinds={"series": 3, "distribution": 2, "sound": 2, "categories": 2, "timeline": 1, "relation": 1,
                  "network": 1, "process": 1},
           devices={"console": 2, "pocket": 2}),
    Person("D5", "charts at the desk, a sentence on the phone, speech in the kitchen",
           order=[*_CHARTS, "table"], by_device={"pocket": ["prose", "table"], "voice": ["spoken"]},
           kinds={"series": 3, "categories": 2, "relation": 2, "network": 1, "scalar": 1, "distribution": 2,
                  "process": 1, "records": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}),
    Person("D6", "likes to explore: anything interactive, else a chart; hears data on the speaker",
           order=["explorable", "step_through", *_CHARTS, "table", "prose"],
           by_device={"voice": ["sonification", "spoken"]},
           kinds={"process": 3, "series": 3, "network": 2, "distribution": 1, "hierarchy": 1, "categories": 2,
                  "timeline": 1, "sound": 1},
           devices={"console": 3, "pocket": 1, "voice": 1}),
)

HELD_OUT = (
    Person("H1", "wants the raw data to work with", order=["data_file", "table", "prose"],
           by_device={"voice": ["spoken"]},
           kinds={"records": 3, "series": 3, "categories": 2, "relation": 2, "distribution": 2, "timeline": 1,
                  "network": 1},
           devices={"console": 3, "pocket": 1, "voice": 1}),
    Person("H2", "listens to data where possible, otherwise words", order=["sonification", "prose"],
           by_device={"voice": ["sonification", "spoken"]},
           kinds={"series": 3, "distribution": 2, "categories": 2, "scalar": 1, "sound": 1, "relation": 1,
                  "records": 1, "process": 1},
           devices={"console": 2, "pocket": 1, "voice": 2}),
    Person("H3", "a chart for numbers over time or a spread, words for everything else", order=["prose"],
           by_kind={"series": ["line_chart"], "distribution": ["histogram"], "process": ["line_chart"]},
           by_device={"voice": ["spoken"]},
           kinds={"series": 3, "distribution": 2, "categories": 2, "network": 2, "process": 1, "relation": 1,
                  "timeline": 1, "scalar": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}),
    Person("H4", "tables on the phone, interactive things at the desk",
           order=["explorable", "step_through", "node_link", "timeline", "heatmap", "line_chart", "bar_chart",
                  "histogram", "scatter_chart", "waveform_spectrum", "table"],
           by_device={"pocket": ["table", "prose"], "voice": ["spoken"]},
           kinds={"process": 2, "series": 3, "categories": 2, "network": 2, "timeline": 1, "distribution": 1,
                  "records": 1, "hierarchy": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}),
    Person("H5", "tables at the desk, charts on the phone, and never anything to fiddle with",
           order=["table", "prose"], by_device={"pocket": [*_CHARTS, "table"], "voice": ["spoken"]},
           never=["explorable", "step_through"],
           kinds={"process": 2, "series": 3, "categories": 2, "hierarchy": 1, "relation": 1, "distribution": 1,
                  "network": 1, "scalar": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}),
    Person("H6", "tables at the desk, a sentence on the phone, files for records; inconsistent, and seldom says "
                 "what they wanted",
           order=["table", "prose"], by_device={"pocket": ["prose"], "voice": ["spoken"]},
           by_kind={"records": ["data_file", "table"]}, noise=0.25, replace=0.4,
           kinds={"series": 3, "categories": 2, "records": 2, "distribution": 2, "relation": 1, "timeline": 1,
                  "network": 1},
           devices={"console": 2, "pocket": 2}),
)

ALL = {"development": DEVELOPMENT, "held_out": HELD_OUT}
SEEDS = {"development": tuple(range(1, 11)), "held_out": tuple(range(101, 111))}
CYCLES = 8
PER_CYCLE = 10
