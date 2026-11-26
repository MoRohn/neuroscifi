"""Frozen DEVELOPMENT worlds of the source-independence family (Wave 4 step 0), written and committed ALONE, before
the held-out worlds, the withheld worlds, the harness that scores them and any source rule. Never edit this file: a
change of world is a new file. Rules may be chosen and debugged here; nothing may be chosen on the held-out worlds
(sources_splits_heldout.py) and nothing on the withheld worlds (never read by a lane).

A world is a staged web of fictional software projects whose latest release changes over the cycles, and pages about
them by outlets over time, with a DERIVATION GRAPH that is the ground truth: for every page, whether it reports
first-hand (its outlet observed the value itself, or it is the project's own release page) or derives from which
other page(s), or repeats the individual's own earlier answer (an echo). Everything is written by generate(spec, seed)
BEFORE the individual runs; nothing the individual produces is ever used as truth.

generate(spec, seed) returns:
  subjects   name, birth cycle, true value per cycle 0..cycles ("M.m.p"; no major or minor marks where a value came
             from: wrong values are neighbouring values or far ones, own answers right or wrong)
  schedule   per cycle, the subjects the individual investigates that cycle, in order (births first, then revisits by
             a blind rotation: the subjects looked at longest ago, ties by name); the same in every configuration
  looks      per (subject, cycle) on the schedule: {"shape": the failure shape the look stages, "pages": [...], "unread":
             [...]}; each page {id, host, outlet, path, text, published_cycle, value, basis, derives_from, roots,
             shape, form, canonical}. `unread` holds pages a read page derives from that the individual does not read
             (an origin not found), so the graph is whole. basis: first_hand | derived | echo. roots: the first-hand
             reports the page rests on, "<outlet>|<value>" (one outlet's report of one value is one root however
             often it is read); an echo's root is "self"; derived pages carry the union of their parents' roots.
             authoritative: the page is the project's own release page (its root is authoritative)
  own        per cycle, the individual's own answers: {subject, value, right, text}; the harness stores `text` as the
             model-written answer of that cycle's investigation of the subject (and as an imagined record), as the
             learning-cycles harness does; echo pages repeat it from the next cycle on

The failure shapes (each look stages one; `shape`), and what the truth says of them:
  independent          two or three outlets that each checked the value themselves (plain honest corroboration, the
                       common case; some share the most common wording, as short factual sentences do)
  primary_cited        the project's own release page (read or not) and outlets restating it with a credit to it:
                       one authoritative root
  verified_credit      outlets that checked the value themselves and still credit another outlet of the look:
                       independent despite the credit
  syndicated_credit    a story (true, a neighbouring wrong value or a far wrong value) by one outlet or wire, read or
                       not, and reworded copies crediting it; honest pages with the true value beside a wrong story
  syndicated_nocredit  the same with copies that credit nobody (reworded, or verbatim)
  two_origins          two outlets that each checked the value, and a page naming both as its origins
  extra_origin         copies naming their origin and each an irrelevant second outlet (a contributor, not a source)
  wire_like_host       a wire's story and copies with its dateline, beside an outlet whose host is named like a wire
                       service: independent of the wire; or a copier whose host is named like the wire it copies
  roundup              a round-up page citing several outlets, beside at least one of them
  mirror_dates         an outlet's story and verbatim mirrors with later dates (some carrying a canonical link to the
                       origin, RFC 6596), true or wrong
  correction           an outlet's page after a correction, or after a correction of a correction, beside a copy
                       taken before the last correction and honest pages
  stale                outlets that have not caught up (dated when the old value was true, or re-dated without being
                       updated) beside honest current pages
  echo                 pages repeating the individual's own earlier answer: verbatim, paraphrased, with the number
                       reformatted, crediting a model or not; the individual's value right or wrong; with or without
                       honest pages

WHERE THE CREDIT AND ECHO WORDING COMES FROM (not from any parser's pattern list; no product source was read to
choose it, and no page here was run through the product before this commit):
  - Attribution verbs: Wikipedia, Manual of Style/Words to watch, "Said" (MOS:SAID; fetched 2026-11-26): "said,
    described, wrote, commented, remarked, and according to are almost always neutral"; "stated is usually acceptable";
    words to watch: "reveal, point out, clarify, expose, explain, find, note, observe, insist, speculate, surmise, claim,
    assert, admit, confess, deny, confirm". Used: said, wrote, commented, remarked, according to, stated, noted, observed,
    explained, pointed out, confirmed, claimed.
  - Datelines: the wire convention of the AP Stylebook and the Reuters handbook ("(AP) —", "LONDON (Reuters) -").
  - Republication lines: ProPublica's "Steal Our Stories" ("This story was originally published by ProPublica.";
    fetched 2026-11-26); The Conversation's republishing guidelines ("Read the full article on The Conversation";
    fetched 2026-11-26); the AP credit line "The Associated Press contributed to this report." and the Reuters
    "Additional reporting by ..." line (newsroom conventions, as printed under wire stories).
  - Indirect credit: the Curator's Code (Maria Popova, 2012): "via" and "HT" (hat tip: a pointer, not a source).
  - Corrections: the New York Times form "An earlier version of this article misstated ...", its correction of a
    correction "An earlier version of this correction misstated ...", and Reuters' "(Corrects ... in first paragraph)".
  - Crediting a model: the APA Style blog's in-text citation "(OpenAI, 2023)" for ChatGPT text (as recalled; the page
    fetched empty on 2026-11-26) and MOS:SAID's "according to" and "said" with the model as the speaker.
  - Canonical links: RFC 6596 (the rel="canonical" link relation), not wording.
Which of these forms today's rules read is reported by the harness's author AFTER this commit, from the rules' code.

PROPORTIONS (shape weights per look; echo looks only where an own answer exists, otherwise the draw is repeated
without echo), and why: honest independent corroboration is the commonest case for a release (several outlets report
it), so it is the largest share and every rule is judged on not breaking it; restating the project's own release
notes is the next commonest way release news is written; wire and outlet syndication, with and without credit, is a
large share of online news (studies of online news find much of it rests on agency copy); round-ups, mirrors,
corrections and multi-origin lines are regular but less frequent forms; stale pages are common for anything that
changes; echoes of an assistant's answer exist on forums and Q&A pages at an unknown rate. The real shares for
software-release news are NOT measured anywhere in this project: these are stated, plausible shares, chosen before
any rule and without running any rule on them, and the withheld worlds use different ones.

Seeds: development 8301-8303 (disjoint from every seed in the repository outside the withheld files, checked by grep
before this commit). Streams are keyed by "sources", the world's name, the seed and the part (truth, own answers,
each look), so the worlds do not depend on the order anything is drawn in.

What these worlds cannot show: anything about the open web, real outlets, real syndication rates, a real answer model
or real echoes; every page is a few sentences written from the forms above, read through the real modules.
"""
from __future__ import annotations

import random

# ---- the cast (fictional; hosts under .example) ---------------------------------------------------------------------
# Outlets that check values themselves: (host, name, their usual opening). No opening credits anyone.
OUTLETS = (
    ("techledger.example", "Tech Ledger", "At the moment, "),
    ("buildnotes.example", "Build Notes", "As things stand, "),
    ("stackdaily.example", "Stack Daily", "Today, "),
    ("opensource-times.example", "Open Source Times", "So far, "),
    ("devpost-weekly.example", "Devpost Weekly", "Right now, "),
    ("codebeat.example", "Codebeat", "At last check, "),
    ("release-radar.example", "Release Radar", "As it stands, "),
    ("the-patch.example", "The Patch", "For now, "),
    ("kernelcast.example", "Kernelcast", "Once more, "),
    ("shipit-review.example", "Shipit Review", "As of now, "),
)
# Wire services (the AP and Reuters by their real names, as copies on the real web name them; the hosts are not theirs)
WIRES = (
    ("apnews.example", "The Associated Press", "AP", "(AP) — "),
    ("reuters.example", "Reuters", "Reuters", "LONDON (Reuters) - "),
    ("corvidwire.example", "Corvid Wire", "Corvid Wire", "(Corvid Wire) — "),
)
# Hosts named like wire services. Independent outlets on such hosts check values themselves; copiers copy a wire.
WIRE_LIKE_INDEPENDENT = (("ap-technews.example", "AP Tech News", "In brief, "),
                         ("bytewire.example", "Bytewire", "Briefly, "))
WIRE_LIKE_COPIERS = (("reutersdigest.example", "Reuters Digest"), ("corvid-wire-mirror.example", "Corvid Wire Mirror"),
                     ("apnews-roundup.example", "AP News Roundup"))
# Aggregators and mirrors (they copy)
COPY_HOSTS = (("feedmill.example", "Feedmill"), ("newsgrab.example", "Newsgrab"),
              ("allthe-releases.example", "All The Releases"), ("techclip.example", "Techclip"),
              ("linkpile.example", "Linkpile"), ("syndicast.example", "Syndicast"))
ROUNDUP_HOSTS = (("weekinreleases.example", "Week in Releases"), ("changelog-digest.example", "Changelog Digest"))
# Forums and Q&A pages where the individual's answer is repeated
ECHO_HOSTS = (("askthread.example", "Askthread"), ("devforum-chat.example", "Devforum Chat"),
              ("quickanswers.example", "Quick Answers"), ("blogroll-notes.example", "Blogroll Notes"))

COMMON = "{N} {v} is the latest {N} release."          # the wording many outlets converge on
PRIMARY = "Release notes: {N} {v} is the latest {N} release, available now."
ANSWER = "{N} {v} is the latest {N} release, which brings several fixes."      # the individual's own answer

# Credit forms (sources in the module docstring). {X} is the credited outlet, {S} the statement without its period.
CREDITS = {
    "said": "{S}, {X} said.",
    "according_to": "According to {X}, {S}.",
    "wrote": "{X} wrote that {S}.",
    "commented": "{X} commented that {S}.",
    "remarked": "{S}, {X} remarked.",
    "stated": "{X} stated that {S}.",
    "noted": "As {X} noted, {S}.",
    "observed": "{S}, {X} observed.",
    "explained": "{S}, {X} explained.",
    "pointed_out": "As {X} pointed out, {S}.",
    "confirmed": "{X} confirmed that {S}.",
    "claimed": "{X} claimed that {S}.",
    "originally_published": "This story was originally published by {X}.\n{S}.",
    "read_full": "{S}.\nRead the full article on {X}.",
    "contributed": "{S}.\n{X} contributed to this report.",
    "via": "{S} (via {X}).",
    "hat_tip": "{S}.\nHT: {X}",
}
DATELINE = "dateline"        # the wire's own dateline form (WIRES), a credit form too
# Credits that name the project's own release notes (primary_cited): MOS:SAID verbs with the project as the speaker
PRIMARY_CREDITS = {
    "according_to": "According to the {N} release notes, {S}.",
    "said": "{S}, the {N} project said.",
    "stated": "The {N} project stated that {S}.",
    "noted": "As the {N} release notes noted, {S}.",
}
# A second, irrelevant name on a copy (a contributor, not a source)
EXTRA_LINES = {"additional_reporting": "Additional reporting by {Y}.", "contributed": "{Y} contributed to this report."}
TWO_ORIGIN_LINES = {
    "published_and_contributed": "This story was originally published by {X}.\n{S}.\n{Y} contributed to this report.",
    "both_contributed": "{S}.\n{X} and {Y} contributed to this report.",
}
ROUNDUP_FORMS = {"said_list": "{S}, {L} said.", "according_list": "According to {L}, {S}."}
CORRECTION_LINES = {
    "nyt": "Correction: An earlier version of this article misstated the latest {N} release as {w}.",
    "reuters": "(Corrects version number in first paragraph)",
}
CORRECTION_OF_CORRECTION = "Correction: An earlier version of this correction misstated the latest {N} release as {w}."
# Echo forms. The model credits name a real assistant, as echoes on the web do; nothing is fetched from it.
ECHOES = {
    "verbatim": ANSWER,
    "paraphrase_a": "{N} {v} is the newest {N} release, with several fixes.",
    "paraphrase_b": "{N} {v} is the latest {N} release and fixes several bugs.",
    "reformatted": "{N} {r} is the latest {N} release, which brings several fixes.",
    "apa_model": "{N} {v} is the latest {N} release (OpenAI, 2023).",
    "according_to_model": "According to ChatGPT, {N} {v} is the latest {N} release.",
    "model_said": "{N} {v} is the latest {N} release, ChatGPT said.",
}
ECHO_CREDITS_MODEL = frozenset({"apa_model", "according_to_model", "model_said"})

SHAPES = ("independent", "primary_cited", "verified_credit", "syndicated_credit", "syndicated_nocredit", "two_origins",
          "extra_origin", "wire_like_host", "roundup", "mirror_dates", "correction", "stale", "echo")
WEIGHTS = {"independent": 26, "primary_cited": 12, "verified_credit": 6, "syndicated_credit": 12,
           "syndicated_nocredit": 8, "two_origins": 5, "extra_origin": 5, "wire_like_host": 4, "roundup": 5,
           "mirror_dates": 5, "correction": 4, "stale": 6, "echo": 10}

CYCLES = {
    "development": {
        "seeds": (8301, 8302, 8303),
        "worlds": (
            # every shape at the stated weights
            {"name": "src-dev-mixed", "subjects": 10, "cycles": 10, "cycle_hours": 24.0, "arrivals": 1,
             "revisits": 4, "change": 0.12, "retract": 0.02, "weights": WEIGHTS, "story_true": 0.55,
             "story_neighbour": 0.30, "convergent": 0.25, "primary_read": 0.5, "origin_read": 0.5,
             "own_per_cycle": 2, "own_right": 0.4, "own_neighbour": 0.4, "echo_with_honest": 0.5},
            # copies and echoes heavier, honest corroboration lighter
            {"name": "src-dev-copies", "subjects": 10, "cycles": 10, "cycle_hours": 24.0, "arrivals": 1,
             "revisits": 4, "change": 0.10, "retract": 0.02,
             "weights": WEIGHTS | {"independent": 14, "syndicated_credit": 18, "syndicated_nocredit": 12, "echo": 16,
                                   "extra_origin": 7, "mirror_dates": 7},
             "story_true": 0.45, "story_neighbour": 0.35, "convergent": 0.3, "primary_read": 0.4, "origin_read": 0.4,
             "own_per_cycle": 3, "own_right": 0.35, "own_neighbour": 0.45, "echo_with_honest": 0.4},
        ),
    },
}

_FIRST = ("Quor", "Bel", "Tam", "Vex", "Ondr", "Pir", "Saln", "Ulm", "Wex", "Zor", "Clav", "Drin", "Elv", "Fask",
          "Grev", "Hult", "Istr", "Jorv", "Kemb", "Lisk", "Mav", "Norv", "Ostr", "Prall")
_LAST = ("vane", "dris", "ola", "eth", "umbra", "ilex", "orra", "ant", "ique", "ovar", "esse", "ynth")


def stream(spec, seed, *parts) -> random.Random:
    """A random stream keyed by this family, the world's name, the seed and the part."""
    return random.Random(":".join(["sources", spec["name"], str(seed), *map(str, parts)]))


def value_str(v) -> str:
    return "{}.{}.{}".format(*v)


def reformatted(v) -> str:
    """The same number written another way: "2.4" for 2.4.0, "v2.4.1" otherwise."""
    return f"{v[0]}.{v[1]}" if v[2] == 0 else "v{}.{}.{}".format(*v)


def _names(rng, n):
    out = []
    while len(out) < n:
        name = rng.choice(_FIRST) + rng.choice(_LAST)
        if name not in out:
            out.append(name)
    return out


def _neighbour(rng, v):
    """A plausible wrong value: next patch, next minor, or the previous minor."""
    k = rng.randrange(3)
    if k == 0:
        return (v[0], v[1], v[2] + 1)
    if k == 1 or v[1] == 0:
        return (v[0], v[1] + 1, 0)
    return (v[0], v[1] - 1, 0)


def _far(rng, v):
    """An implausible wrong value: a far major."""
    return (v[0] + rng.randint(3, 6), rng.randint(0, 20), 0)


def _statement(template, name, v, prefix=""):
    text = template.format(N=name, v=value_str(v))
    return prefix + text if prefix else text


def _bare(text):
    return text[:-1] if text.endswith(".") else text


def _fill(template, statement, name, **names):
    """A form with the statement (without its period) in it. Inside a sentence an opening ("Today, ...") is
    lower-cased; a statement that starts with the project's name keeps it."""
    s = _bare(statement)
    inside = not (template.startswith("{S}") or "\n{S}" in template)
    if inside and not s.startswith(name):
        s = s[0].lower() + s[1:]
    return template.format(S=s, N=name, **names)


class _Look:
    """Pages of one look and their derivation graph."""

    def __init__(self, name, cycle, shape):
        self.name, self.cycle, self.shape = name, cycle, shape
        self.pages, self.unread = [], []
        self.n = 0

    def page(self, host, outlet, text, v, *, basis, parents=(), published, form, read=True, canonical=None,
             authoritative=False, root_outlet=None):
        self.n += 1
        pid = f"{self.name.lower()}-{self.cycle}-{self.n}"
        if basis == "first_hand":
            roots = [f"{root_outlet or outlet}|{value_str(v)}"]
        elif basis == "echo":
            roots = ["self"]
        else:
            roots = sorted({r for p in parents for r in p["roots"]})
        page = {"id": pid, "host": host, "outlet": outlet, "path": f"/{self.name.lower()}/{self.cycle}/{self.n}",
                "text": text, "published_cycle": published, "value": value_str(v), "basis": basis,
                "derives_from": [p["id"] for p in parents], "roots": roots, "shape": self.shape, "form": form,
                "canonical": canonical, "authoritative": authoritative}
        (self.pages if read else self.unread).append(page)
        return page

    def url(self, page):
        return f"http://{page['host']}{page['path']}"

    def out(self):
        return {"shape": self.shape, "pages": self.pages, "unread": self.unread}


def _outlet_text(rng, spec, name, v, outlet):
    """An outlet's own statement: the common wording with probability `convergent`, else its usual opening."""
    return COMMON.format(N=name, v=value_str(v)) if rng.random() < spec["convergent"] else \
        _statement(COMMON, name, v, outlet[2])


def _story_value(rng, spec, truth):
    roll = rng.random()
    if roll < spec["story_true"]:
        return truth
    if roll < spec["story_true"] + spec["story_neighbour"]:
        return _neighbour(rng, truth)
    return _far(rng, truth)


def _honest(look, rng, spec, name, v, outlets, since):
    for o in outlets:
        look.page(o[0], o[1], _outlet_text(rng, spec, name, v, o), v, basis="first_hand", published=since,
                  form="outlet")


def build_look(spec, seed, subject, c, shape, own_value):
    """The pages of one look (see the module docstring); every draw from the look's own stream."""
    rng = stream(spec, seed, "look", subject["name"], c)
    name, truth, since = subject["name"], subject["truth"][c], subject["since"][c]
    look = _Look(name, c, shape)
    outlets = list(OUTLETS)
    rng.shuffle(outlets)
    copies = list(COPY_HOSTS)
    rng.shuffle(copies)
    if shape == "independent":
        _honest(look, rng, spec, name, truth, outlets[:rng.randint(2, 3)], since)
    elif shape == "primary_cited":
        primary_host = f"{name.lower()}-project.example"
        prim = look.page(primary_host, f"{name} project", PRIMARY.format(N=name, v=value_str(truth)), truth,
                         basis="first_hand", published=since, form="release_page", authoritative=True,
                         read=rng.random() < spec["primary_read"])
        for o in outlets[:rng.randint(2, 3)]:
            form = rng.choice(sorted(PRIMARY_CREDITS))
            text = _fill(PRIMARY_CREDITS[form], _statement(COMMON, name, truth, o[2]), name)
            look.page(o[0], o[1], text, truth, basis="derived", parents=[prim], published=since, form=form)
    elif shape == "verified_credit":
        chosen = outlets[:rng.randint(2, 3)]
        for i, o in enumerate(chosen):
            text = _outlet_text(rng, spec, name, truth, o)
            form = "outlet"
            if i > 0:                               # checked it themselves, and credit the first outlet anyway
                form = rng.choice(sorted(CREDITS))
                text = _fill(CREDITS[form], text, name, X=chosen[0][1])
            look.page(o[0], o[1], text, truth, basis="first_hand", published=since, form=form)
    elif shape in ("syndicated_credit", "syndicated_nocredit", "extra_origin"):
        story = _story_value(rng, spec, truth)
        wire = rng.random() < 0.5
        if wire:
            w = WIRES[rng.randrange(len(WIRES))]
            origin = look.page(w[0], w[1], w[3] + _statement(COMMON, name, story), story, basis="first_hand",
                               published=since, form="wire", read=rng.random() < spec["origin_read"])
            credit_name = w[2]
        else:
            o = outlets.pop()
            origin = look.page(o[0], o[1], _outlet_text(rng, spec, name, story, o), story, basis="first_hand",
                               published=since, form="outlet", read=rng.random() < spec["origin_read"])
            credit_name = o[1]
        others = [o for o in outlets]
        for i, h in enumerate(copies[:rng.randint(2, 4) if shape != "extra_origin" else rng.randint(2, 3)]):
            reword = OUTLETS[rng.randrange(len(OUTLETS))][2]
            stated = _statement(COMMON, name, story, reword)
            if shape == "syndicated_nocredit":
                verbatim = rng.random() < 0.3
                text = origin["text"].split("\n")[-1] if verbatim else stated
                form = "verbatim" if verbatim else "reworded"
            else:
                form = DATELINE if wire and rng.random() < 0.3 else rng.choice(sorted(CREDITS))
                text = (w[3] + _statement(COMMON, name, story)) if form == DATELINE else \
                    _fill(CREDITS[form], stated, name, X=credit_name)
                if shape == "extra_origin":
                    extra = rng.choice(sorted(EXTRA_LINES))
                    text += "\n" + EXTRA_LINES[extra].format(Y=others[i % len(others)][1])
                    form += "+" + extra
            look.page(h[0], h[1], text, story, basis="derived", parents=[origin], published=since, form=form)
        if story != truth:                     # honest pages beside a wrong story
            _honest(look, rng, spec, name, truth, outlets[:rng.randint(0, 2)], since)
    elif shape == "two_origins":
        x, y = outlets[0], outlets[1]
        px = look.page(x[0], x[1], _outlet_text(rng, spec, name, truth, x), truth, basis="first_hand",
                       published=since, form="outlet")
        py = look.page(y[0], y[1], _outlet_text(rng, spec, name, truth, y), truth, basis="first_hand",
                       published=since, form="outlet")
        form = rng.choice(sorted(TWO_ORIGIN_LINES))
        stated = _statement(COMMON, name, truth, OUTLETS[rng.randrange(len(OUTLETS))][2])
        look.page(copies[0][0], copies[0][1], _fill(TWO_ORIGIN_LINES[form], stated, name, X=x[1], Y=y[1]), truth,
                  basis="derived", parents=[px, py], published=since, form=form)
    elif shape == "wire_like_host":
        w = WIRES[rng.randrange(len(WIRES))]
        wire_page = look.page(w[0], w[1], w[3] + _statement(COMMON, name, truth), truth, basis="first_hand",
                              published=since, form="wire", read=rng.random() < spec["origin_read"])
        ind = WIRE_LIKE_INDEPENDENT[rng.randrange(len(WIRE_LIKE_INDEPENDENT))]
        look.page(ind[0], ind[1], _outlet_text(rng, spec, name, truth, ind), truth, basis="first_hand",
                  published=since, form="wire_like_independent")
        cop = WIRE_LIKE_COPIERS[rng.randrange(len(WIRE_LIKE_COPIERS))]
        look.page(cop[0], cop[1], w[3] + _statement(COMMON, name, truth, ""), truth, basis="derived",
                  parents=[wire_page], published=since, form="wire_like_copier+dateline")
        for h in copies[:rng.randint(0, 1)]:
            look.page(h[0], h[1], w[3] + _statement(COMMON, name, truth, OUTLETS[rng.randrange(len(OUTLETS))][2]),
                      truth, basis="derived", parents=[wire_page], published=since, form=DATELINE)
    elif shape == "roundup":
        cited = outlets[:rng.randint(2, 3)]
        reads = [rng.random() < 0.5 for _ in cited]
        if not any(reads):
            reads[rng.randrange(len(cited))] = True
        parents = [look.page(o[0], o[1], _outlet_text(rng, spec, name, truth, o), truth, basis="first_hand",
                             published=since, form="outlet", read=r) for o, r in zip(cited, reads)]
        names = [o[1] for o in cited]
        listed = names[0] + " and " + names[1] if len(names) == 2 else f"{names[0]}, {names[1]} and {names[2]}"
        form = rng.choice(sorted(ROUNDUP_FORMS))
        h = ROUNDUP_HOSTS[rng.randrange(len(ROUNDUP_HOSTS))]
        look.page(h[0], h[1], "Weekly releases. " + _fill(ROUNDUP_FORMS[form], _statement(COMMON, name, truth), name,
                                                            L=listed), truth, basis="derived", parents=parents, published=c, form=form)
    elif shape == "mirror_dates":
        story = _story_value(rng, spec, truth)
        o = outlets.pop()
        origin = look.page(o[0], o[1], _outlet_text(rng, spec, name, story, o), story, basis="first_hand",
                           published=since, form="outlet", read=rng.random() < 0.6)
        for i, h in enumerate(copies[:rng.randint(1, 3)]):
            canonical = f"http://{o[0]}{origin['path']}" if rng.random() < 0.5 else None
            look.page(h[0], h[1], origin["text"], story, basis="derived", parents=[origin],
                      published=min(c, since + 1 + i), form="mirror+canonical" if canonical else "mirror",
                      canonical=canonical)
        if story != truth:
            _honest(look, rng, spec, name, truth, outlets[:rng.randint(0, 2)], since)
    elif shape == "correction":
        o = outlets.pop()
        wrong = _neighbour(rng, truth)
        twice = rng.random() < 0.4               # a correction of a correction: right, "corrected" wrongly, right again
        body = _statement(COMMON, name, truth, o[2])
        if twice:
            text = body + "\n" + CORRECTION_OF_CORRECTION.format(N=name, w=value_str(wrong))
            form = "correction_of_correction"
        else:
            line = rng.choice(sorted(CORRECTION_LINES))
            text = (CORRECTION_LINES[line] + "\n" + body) if line == "reuters" else \
                body + "\n" + CORRECTION_LINES[line].format(N=name, w=value_str(wrong))
            form = "correction_" + line
        look.page(o[0], o[1], text, truth, basis="first_hand", published=c, form=form)
        earlier = look.page(o[0], o[1], _statement(COMMON, name, wrong, o[2]), wrong, basis="first_hand",
                            published=since, form="before_correction", read=False)
        h = copies[0]
        look.page(h[0], h[1], earlier["text"], wrong, basis="derived", parents=[earlier], published=since,
                  form="copy_before_correction")
        _honest(look, rng, spec, name, truth, outlets[:rng.randint(1, 2)], since)
    elif shape == "stale":
        prev = subject["truth"][since - 1]
        prev_since = subject["since"][since - 1]
        for o in outlets[:rng.randint(1, 2)]:
            look.page(o[0], o[1], _outlet_text(rng, spec, name, prev, o), prev, basis="first_hand",
                      published=prev_since, form="lagging")
        rest = outlets[2:]
        if rng.random() < 0.5:
            o = rest.pop()
            look.page(o[0], o[1], _outlet_text(rng, spec, name, prev, o), prev, basis="first_hand", published=c,
                      form="redated_stale")
        _honest(look, rng, spec, name, truth, rest[:rng.randint(1, 2)], since)
    elif shape == "echo":
        hosts = list(ECHO_HOSTS)
        rng.shuffle(hosts)
        for h in hosts[:rng.randint(1, 3)]:
            form = rng.choice(sorted(ECHOES))
            text = ECHOES[form].format(N=name, v=value_str(own_value), r=reformatted(own_value))
            look.page(h[0], h[1], text, own_value, basis="echo", published=c, form=form)
        if rng.random() < spec["echo_with_honest"]:
            _honest(look, rng, spec, name, truth, outlets[:rng.randint(1, 2)], since)
    else:
        raise ValueError(f"unknown shape {shape!r}")
    return look.out()


def _draw_shape(rng, weights, *, echo_ok, stale_ok):
    names = [s for s in SHAPES if weights.get(s) and (echo_ok or s != "echo") and (stale_ok or s != "stale")]
    return rng.choices(names, weights=[weights[s] for s in names])[0]


def generate(spec: dict, seed: int, *, build=None, shapes=SHAPES) -> dict:
    """The whole world of one run, written before the individual runs (see the module docstring). `build` and
    `shapes` let another worlds file add shapes to this generator without changing what it writes here."""
    build = build or build_look
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
                    history.pop()                 # a release pulled: the truth goes back to the previous one
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
        revisit = [s["name"] for s in older[:spec["revisits"]]] if c >= 1 else []
        schedule[c] = births + revisit
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
            srng = stream(spec, seed, "shape", name, c)
            stale_ok = s["since"][c] > s["born"] and s["truth"][s["since"][c] - 1] != s["truth"][c]
            shape = _draw_shape(srng, {k: v for k, v in spec["weights"].items() if k in shapes},
                                echo_ok=name in own_now, stale_ok=stale_ok)
            looks[(name, c)] = build(spec, seed, s, c, shape, own_now.get(name))
            last[name] = c
        for item in own[c]:                       # an echo can repeat it from the next cycle on
            own_now[item["subject"]] = tuple(int(x) for x in item["value"].split("."))
    return {"spec": spec, "seed": seed,
            "subjects": [{"name": s["name"], "born": s["born"], "truth": {c: value_str(v) for c, v in s["truth"].items()},
                          "since": dict(s["since"])} for s in subjects],
            "schedule": schedule, "looks": looks, "own": own}
