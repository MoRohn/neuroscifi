"""Frozen variant worlds of Wave 3's fix lane (review3 S-2, S-3, S-4): lane S's worlds (cycles_splits_s.py) with the
pages rewritten in the shapes the review found its two rules do not see, or misread. Written and committed ALONE,
before the harness that scores them; never edit this file: a change of world is a new file. Nothing here was written
from the withheld learning-cycles worlds (never read); the shapes are the review's (findings-bench.md S-2..S-4,
findings-security.md L2), its probes' phrasings reused as they were.

Base worlds: lane S's specs under new names ("sv-..."; every stream is keyed by the world's name, so these are new
worlds) with seeds disjoint from every existing split. Each variant takes the generated world and rewrites pages from
its own keyed stream ("cycles-s-variant", variant, world, seed, look, page): the truth, the looks' kinds and every other
page stay as generated, so a variant and "orig" (no rewrite) are paired on the same world.

Variants (each also the measure of what the fix does NOT catch; none was chosen to flatter a rule):
  orig              no rewrite (the paired reference)
  echo-nocredit     echo pages that credited a model ("an assistant reported", "a chatbot told one reader") take one
                    of lane S's three shapes that credit no one (verbatim, forum, roundup)            (review3 S-2)
  echo-num          every echo page restates the own value in another format ("9.5", "v9.5.0", "version 9.5",
                    "9.5.0 (build 95)") in a sentence that credits no model                           (S-2)
  echo-para         every echo page paraphrases the own value (the extractor may not read these)      (S-2)
  attr-offparser    every reworded wire copy and every one-origin copy that credited its origin inside the sentence
                    credits it in a phrasing claims.attribution_keys does not read: another verb, passive, "per a
                    ... report", "As reported in", h/t, a dash, brackets, a link-only credit, a bare URL, lowercase,
                    German, French, Spanish; and the possessive the review found misread ("<X>'s report:")   (S-3)
  honest-credit-N   a share N % (10, 30, 50) of honest outlet pages credit ANOTHER honest outlet of the same look
                    that states the same value, for a fact they checked themselves ("..., as <X> first reported;
                    we checked the tag.")                                                            (S-4)
  primary-source    a share 30 % of honest outlet pages credit the project's own release page ("according to
                    GitHub", "PyPI says", "per the release notes on GitHub"): every outlet citing it shares one
                    credited key                                                                      (S-4)
"""
from __future__ import annotations

import random
import re

from . import cycles_splits_s as base
from .cycles_splits import OUTLETS

LABEL = {host: label for host, label, _ in OUTLETS}


def _named(spec, name):
    return dict(spec, name=name)


_DEV = {w["name"]: w for w in base.CYCLES["development"]["worlds"]}
_HELD = {w["name"]: w for w in base.CYCLES["held_out"]["worlds"]}
CYCLES = {
    "development": {"seeds": (4401, 4402, 4403),
                    "worlds": (_named(_DEV["sd-mixed"], "svd-mixed"), _named(_DEV["sd-wrong-newer"], "svd-wrong-newer"))},
    "held_out": {"seeds": (4501, 4502, 4503, 4504),
                 "worlds": (_named(_HELD["s-mixed"], "sv-mixed"), _named(_HELD["s-wrong-newer"], "sv-wrong-newer"),
                            _named(_HELD["s-echo"], "sv-echo"))},
}

OFF_PARSER = (
    "{N} {v} is the latest {N} release, {S} notes.",
    "{N} {v} is the latest {N} release, {S} told readers.",
    "{N} {v} is the latest {N} release, {S} claims.",
    "{S} has learned that {N} {v} is the latest {N} release.",
    "Citing {S}, {N} {v} is the latest {N} release.",
    "{N} {v} is the latest {N} release, per a {S} report.",
    "As reported in {S}, {N} {v} is the latest {N} release.",
    "{N} {v} is the latest {N} release (h/t {S}).",
    "{N} {v} is the latest {N} release - {S}",
    "{N} {v} is the latest {N} release [{S}].",
    "{N} {v} is the latest {N} release ({s}.example).",
    "{N} {v} is the latest {N} release. https://{s}.example/{n}",
    "according to {s}, {N} {v} is the latest {N} release.",
    "{S}'s report: {N} {v} is the latest {N} release.",
    "Laut {S} ist {N} {v} die neueste {N}-Version.",
    "Selon {S}, {N} {v} est la dernière version de {N}.",
    "Según {S}, {N} {v} es la última versión de {N}.",
)
ECHO_FREE = ("verbatim", "forum", "roundup")
ECHO_PARA = (
    "Readers on the board say the newest {N} build is {V}.",
    "{N}'s latest release? {V}, apparently.",
    "Latest {N}: {V}.",
    "I asked around: {N} is at {V} now.",
    "\"...the latest {N} release is {V}\" - from a summary someone posted.",
)
PRIMARY = ("{N} {v} is the latest {N} release, according to GitHub.", "{N} {v} is the latest {N} release, PyPI says.",
           "{N} {v} is the latest {N} release, per the release notes on GitHub.")
VARIANTS = ("orig", "echo-nocredit", "echo-num", "echo-para", "attr-offparser", "honest-credit-10", "honest-credit-30",
            "honest-credit-50", "primary-source")
_VERSION = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")


def _stream(variant, spec, seed, *parts):
    return random.Random(":".join(["cycles-s-variant", variant, spec["name"], str(seed), *map(str, parts)]))


def _value(text):
    m = _VERSION.search(text)
    return m.group(0) if m else None


def _formatted(v, k):
    major, minor, patch = v.split(".")
    return (f"{major}.{minor}", f"v{v}", f"version {major}.{minor}", f"{v} (build {major}{minor})")[k % 4]


def _rewrite(variant, look, page, name, rng):
    """The page's new text (and variant tag), or None to keep it."""
    v = _value(page["text"].split("\n")[-1])
    if v is None:
        return None
    kind, shape = page["kind"], page.get("variant")
    if variant == "echo-nocredit" and kind == "echo" and shape in ("assistant", "quote"):
        free = ECHO_FREE[rng.randrange(len(ECHO_FREE))]
        return base.ECHO_SHAPES[free].format(N=name, v=v), "nocredit:" + free
    if variant == "echo-num" and kind == "echo":
        k = rng.randrange(4)
        return f"{name} {_formatted(v, k)} is the latest {name} release, an observer noted.", f"num{k}"
    if variant == "echo-para" and kind == "echo":
        k = rng.randrange(len(ECHO_PARA))
        return ECHO_PARA[k].format(N=name, V=v), f"para{k}"
    if variant == "attr-offparser" and (kind == "wire_copy" and shape not in ("line", "chain", "bare")
                                        or kind == "copy" and shape == "according"):
        label = "Syndiwire" if kind == "wire_copy" else next(
            (LABEL.get(q["host"]) for q in look["pages"] if q.get("variant") == "origin"), None)
        if not label:
            return None
        k = rng.randrange(len(OFF_PARSER))
        return OFF_PARSER[k].format(N=name, v=v, S=label, s=label.lower().replace(" ", "-"), n=name.lower()), \
            f"off{k}:{shape}"
    if variant.startswith("honest-credit-") and kind == "honest" and shape == "outlet":
        share = int(variant.rsplit("-", 1)[1]) / 100
        others = [q for q in look["pages"] if q is not page and q["kind"] == "honest" and q["host"] in LABEL
                  and _value(q["text"]) == v]
        if others and rng.random() < share:
            q = others[rng.randrange(len(others))]
            return f"{name} {v} is the latest {name} release, as {LABEL[q['host']]} first reported; we checked " \
                   "the tag.", "credits:" + q["host"]
    if variant == "primary-source" and kind == "honest" and shape == "outlet" and rng.random() < 0.3:
        k = rng.randrange(len(PRIMARY))
        return PRIMARY[k].format(N=name, v=v), f"primary{k}"
    return None


def generator(variant):
    """The world generator of one variant: lane S's generate, then the variant's rewrite (see the module doc)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; known: {', '.join(VARIANTS)}")

    def generate(spec, seed):
        world = base.generate(spec, seed)
        if variant == "orig":
            return world
        for (name, c), look in sorted(world["looks"].items()):
            for i, page in enumerate(look["pages"]):
                new = _rewrite(variant, look, page, name, _stream(variant, spec, seed, name, c, i))
                if new is not None:
                    page["text"], page["variant"] = new
        world["variant"] = variant
        return world
    return generate
