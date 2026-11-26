"""OMNIMIND benchmark, family `learn`: the WITHHELD SIMULATED people (lane ZW). Not real people: each is a hidden,
stable preference over forms per content kind and device, plus habits and a way of reacting (learn_people.py says
what each field means).

DO NOT OPEN, PRINT OR IMPORT THIS FILE unless you are `bench_omni_forms.py final` (COMMON.md, and
docs/quality/OMNIMIND_BENCHMARK_FORMS.md "The withheld split"). Every look is recorded in
docs/quality/evidence/omnimind_final_uses.jsonl before anything is scored; final.py pins this file's SHA-256.

No person here has the full preference map of any development or held-out person (the test compares the maps), and
the reaction habits (how inconsistent, how often they say what they wanted, how often they keep a second choice)
reach values neither split uses. Seeds come from the reserved withheld range. Data only.
"""
from __future__ import annotations

from .learn_people import CYCLES, PER_CYCLE, Person, _CHARTS

PEOPLE = (
    Person("W1", "shaded grids for anything over time, tables for categories, records and schedules, a sentence for "
                 "the rest; speech in the car",
           order=["prose"], by_kind={"series": ["heatmap", "table"], "categories": ["table"], "records": ["table"],
                                     "timeline": ["table", "prose"]},
           by_device={"voice": ["spoken", "prose"]},
           kinds={"series": 4, "categories": 2, "records": 2, "distribution": 1, "network": 1, "timeline": 1,
                  "scalar": 1},
           devices={"console": 3, "pocket": 1, "voice": 1}, accept_second=0.7),
    Person("W2", "steps through processes, draws timelines, says everything else in words; listens on the speaker",
           order=["prose"], by_kind={"process": ["step_through", "prose"], "timeline": ["timeline", "table"]},
           by_device={"voice": ["sonification", "spoken"]},
           kinds={"process": 3, "timeline": 3, "series": 2, "categories": 1, "scalar": 1, "distribution": 1,
                  "sound": 1},
           devices={"console": 2, "pocket": 2, "voice": 2}, replace=0.6),
    Person("W3", "charts at the desk except graphs and trees, which stay tables; raw files for records; tables on "
                 "the phone",
           order=[*_CHARTS, "table", "prose"],
           by_kind={"network": ["table"], "hierarchy": ["table"], "records": ["data_file", "table"]},
           by_device={"voice": ["spoken"], "pocket": ["table", "prose"]},
           kinds={"network": 2, "hierarchy": 2, "records": 2, "series": 2, "relation": 2, "categories": 1,
                  "distribution": 1},
           devices={"console": 3, "pocket": 2, "voice": 1}),
    Person("W4", "tries anything interactive at the desk, wants a sentence on the phone; cannot use sound",
           order=["explorable", "step_through", "table", "prose"], by_device={"pocket": ["prose"]},
           never=["sonification", "spoken"],
           kinds={"process": 3, "series": 2, "distribution": 2, "sound": 2, "categories": 1, "timeline": 1},
           devices={"console": 3, "pocket": 2}, noise=0.05),
    Person("W5", "points for things that vary together, a histogram for a spread, files for records, tables "
                 "otherwise; speech on the speaker",
           order=["table", "prose"],
           by_kind={"relation": ["scatter_chart", "table"], "distribution": ["histogram", "table"],
                    "records": ["data_file", "table"]},
           by_device={"voice": ["spoken"]},
           kinds={"records": 3, "series": 2, "categories": 1, "relation": 2, "distribution": 2, "network": 1,
                  "scalar": 1},
           devices={"console": 2, "pocket": 3, "voice": 1}),
    Person("W6", "a waveform for sound, a line for series, bars for categories, words for the rest; inconsistent, "
                 "seldom says what they wanted, usually keeps a second choice",
           order=["prose"],
           by_kind={"sound": ["waveform_spectrum", "prose"], "series": ["line_chart", "table"],
                    "categories": ["bar_chart", "table"]},
           by_device={"voice": ["spoken"]},
           kinds={"sound": 2, "series": 3, "categories": 2, "scalar": 1, "hierarchy": 1, "relation": 1},
           devices={"console": 2, "pocket": 2, "voice": 1}, noise=0.2, replace=0.2, accept_second=0.85),
)

SEEDS = tuple(range(9590, 9600))
__all__ = ["CYCLES", "PEOPLE", "PER_CYCLE", "SEEDS"]
