"""Word sense of an ambiguous subject (T1-07; EVIDENCE_AND_PROVENANCE §9).

"When was Rust first released?" read Wikipedia "Rust (programming language)", "Rust (video game)" and "Rust (2024
film)" and pages that never say which Rust they mean; the answer quoted the game. A name can mean several things, so
every source that names the question's subject is given the sense it is about, the question's sense is chosen, and
the sources of every other sense are read but never cited.

A source's sense, first rule that applies (deterministic, no model):
  1. its title is the name with a parenthetical qualifier: "Rust (video game)", "Python (genus)";
  2. its defining sentence: "Rust is a multiplayer survival video game ...", "Mercury is the first planet ...";
     else a head noun within three words after the name in its title ("The Rust Programming Language Forum");
  3. its vocabulary: the distinct SENSES seed terms it contains ("cargo", "compiler", "memory safety" against
     "Steam", "early access", "survival"), plus, once two senses are labelled by 1-2, the words that only the
     pages of one of them use (Facepunch, crate). A vocabulary sense needs VOCAB_MIN points and VOCAB_MARGIN times the next sense's, so a
     page that is about neither stays unassigned (and is kept). With the encyclopedia rate-limited the seed terms
     still separate the senses.
The head nouns of 1-2 map to a sense class (SENSES); a qualifier that names none ("Mercury (mythology)") is a sense
of its own, described as "other meanings".

The question's sense, first rule that applies: (a) its own words name one ("programming", "crate" -> software;
"game", "Steam" -> video game; "snakes" -> animal); (b) a technical question (intent: "released", "version") means
the software sense when the evidence holds one; (c) the dominant encyclopedic sense: the article whose title is
the bare name, else the sense with the most encyclopedia articles, then the most sources. Only senses found in the
evidence are chosen by (b) and (c); a sense the question names is chosen even when no source has it, and then only
unassigned sources remain citable (never every source set aside: the answer is never emptied).
"""
from __future__ import annotations

import re

from . import text as T

# class -> (label used in the note when no head names it, head nouns of qualifiers and defining sentences, question
# cue words, seed vocabulary). Heads and cues are matched as whole stemmed words or phrases; the label of the head
# that matched names the sense in the note ("Rust also names a video game").
SENSES = {
    "software": ("software", (
        "programming language", "scripting language", "markup language", "query language", "software", "game engine",
        "library", "framework", "compiler", "interpreter", "operating system", "web browser", "text editor",
        "database", "toolkit", "package manager"), (
        "programming", "language", "code", "coding", "compiler", "crate", "cargo", "library", "software",
        "syntax", "interpreter", "programmer", "pip", "npm", "api", "sdk", "toolchain", "open source"), (
        "programming", "programmer", "compiler", "compile", "source code", "syntax", "library", "libraries", "crate",
        "cargo", "memory safety", "type system", "garbage collector", "runtime", "interpreter", "toolchain",
        "open source", "github", "api", "function", "variable", "module", "struct", "iterator", "concurrency",
        "kernel", "pip install", "standard library", "repository", "binary", "debugger", "programming language",
        "webassembly", "dependency", "dependencies", "software")),
    "video game": ("video game", ("video game", "game"), (
        "game", "games", "gaming", "steam", "playstation", "xbox", "nintendo", "multiplayer",
        "early access", "gameplay", "player", "players"), (
        "video game", "gameplay", "player", "players", "multiplayer", "survival", "steam", "early access",
        "playstation", "xbox", "nintendo", "crafting", "raid", "raids", "wipe", "wipes", "dlc", "skin", "skins",
        "trailer", "game")),
    "film": ("film", ("film", "movie", "documentary"), (
        "film", "movie", "cinema", "director", "starring", "box office", "screenplay"), (
        "film", "movie", "director", "directed", "screenplay", "starring", "cast", "box office", "premiere",
        "theatrically", "theatrical", "cinematography", "actor", "actress", "filming", "shooting", "studio")),
    "television series": ("television series", ("television series", "tv series", "sitcom"), (
        "episode", "episodes", "season", "sitcom", "tv", "television"), (
        "episode", "episodes", "season", "aired", "network", "sitcom", "television", "showrunner")),
    "music": ("musical work", ("album", "song", "single", "band", "musician", "singer", "rapper"), (
        "album", "song", "band", "single", "lyrics", "singer"), (
        "album", "song", "band", "single", "track", "chart", "billboard", "vocals", "guitar", "tour", "recorded",
        "lyrics", "record label")),
    "organism": ("organism", ("snake", "genus", "species", "fungus", "fungi", "plant", "animal", "bird", "fish",
                              "reptile", "mammal", "plant disease", "pathogen", "insect"), (
        "snake", "snakes", "species", "animal", "animals", "fungus", "plant", "plants", "genus", "reptile", "bite",
        "venomous", "prey", "pet"), (
        "species", "genus", "snake", "snakes", "prey", "venom", "venomous", "habitat", "constrict", "constriction",
        "reptile", "fungus", "fungal", "spore", "spores", "host plant", "pathogen", "wheat", "infection", "eggs",
        "predator", "nonvenomous")),
    "planet": ("astronomical body", ("planet", "dwarf planet", "moon", "star", "asteroid", "comet", "galaxy"), (
        "planet", "orbit", "orbits", "sun", "solar system", "moon", "spacecraft", "crater", "craters", "telescope",
        "astronomer", "astronomers"), (
        "planet", "orbit", "solar system", "sun", "crater", "craters", "spacecraft", "surface", "atmosphere",
        "telescope", "perihelion", "aphelion", "nasa", "probe", "astronomer", "astronomical", "gravity", "moons")),
    "chemical": ("chemical substance", ("chemical element", "element", "metal", "compound", "oxide", "mineral"), (
        "element", "atomic", "chemical", "metal", "toxic", "boiling", "melting", "symbol", "oxide", "corrosion",
        "iron"), (
        "chemical", "element", "atomic", "metal", "compound", "mineral", "oxide", "oxidation", "iron", "ore",
        "corrosion", "alloy", "electron", "toxic", "poisoning", "periodic table", "liquid", "isotope", "isotopes")),
    "deity": ("deity", ("god", "goddess", "deity"), (
        "god", "goddess", "myth", "mythology", "worship", "deity"), (
        "god", "goddess", "deity", "mythology", "myth", "worship", "temple", "roman", "greek", "messenger")),
    "company": ("company", ("company", "brand", "automobile", "marque", "manufacturer", "car"), (
        "company", "brand", "car", "cars", "ceo", "automaker"), (
        "company", "brand", "subsidiary", "headquartered", "ceo", "automobile", "marque", "manufacturer",
        "acquired", "revenue")),
}
VOCAB_MIN = 4.0          # vocabulary points a page needs before it is given a sense by vocabulary alone
VOCAB_MARGIN = 2.0       # and how many times the next sense's points
LEARNED_WORDS = 40       # most frequent words only the pages labelled by one sense use
LEARNED_WEIGHT = 0.5     # a learned word counts half a seed term
DEFINING_CHARS = 1500    # the defining sentence is looked for this far into a page

_ARTICLE = {True: "an", False: "a"}
_COPULA = r"(?:is|was|are|were)\s+(?:(?:a|an|the|one\s+of\s+the)\s+)"


def _stems(text: str) -> list[str]:
    return [T.stem(t) for t in T.tokens(text or "")]


def _phrase(s: str) -> tuple:
    return tuple(_stems(s))


def _grams(stems: list[str]) -> set:
    return {(t,) for t in stems} | {tuple(stems[i:i + 2]) for i in range(len(stems) - 1)} | \
        {tuple(stems[i:i + 3]) for i in range(len(stems) - 2)}


_HEADS = [(_phrase(h), cls, h) for cls, (_, heads, _, _) in SENSES.items() for h in heads]
_HEADS.sort(key=lambda t: -len(t[0]))          # "video game" before "game"
_CUES = {cls: {_phrase(c) for c in cues} for cls, (_, _, cues, _) in SENSES.items()}
_SEEDS = {cls: {_phrase(s) for s in seeds} for cls, (_, _, _, seeds) in SENSES.items()}


def _head(text: str) -> tuple[str, str] | None:
    """(class, head noun) of the earliest head noun of SENSES in `text` ("a 2018 multiplayer survival video game
    developed by ..." -> ("video game", "video game")), or None."""
    stems = _stems(text)
    for i in range(len(stems)):
        for phrase, cls, head in _HEADS:
            if tuple(stems[i:i + len(phrase)]) == phrase:
                return cls, head
    return None


def _qualifier(title: str, name: str) -> str | None:
    m = re.fullmatch(rf"\s*{re.escape(name)}\s*\((?P<q>[^()]{{1,60}})\)\s*", title or "", re.IGNORECASE)
    return m.group("q").strip() if m else None


def defining(text: str, name: str) -> str | None:
    """The noun phrase a page defines `name` by within its first DEFINING_CHARS characters ("Rust is a
    general-purpose programming language that ..." -> "general-purpose programming language that ..."), or None."""
    m = re.search(rf"(?<![\w-]){re.escape(name)}(?:\s*\([^()]{{1,60}}\))?(?:,[^,.]{{1,80}},)?\s+{_COPULA}"
                  rf"(?P<np>[^.;:!?]{{1,160}})", (text or "")[:DEFINING_CHARS], re.IGNORECASE)
    return m.group("np") if m else None


def source_sense(title: str, text: str, name: str, *, encyclopedia: bool = False) -> dict | None:
    """{"sense", "head", "by"} of one source from its title or defining sentence (rules 1-2 of the module
    docstring), or None. A qualifier that names no SENSES head is a sense of its own ("qualifier:mythology") on an
    encyclopedia article only: the "(Contract)" of "Rust Software Engineer (Contract)" is no sense."""
    qual = _qualifier(title, name)
    if qual:
        found = _head(qual)
        if found:
            return {"sense": found[0], "head": found[1], "by": "title"}
        if encyclopedia:
            return {"sense": "qualifier:" + " ".join(T.tokens(qual))[:40], "head": None, "by": "title"}
    np = defining(text, name)
    found = _head(" ".join(np.split()[:12])) if np else None
    if found:
        return {"sense": found[0], "head": found[1], "by": "defining sentence"}
    for m in re.finditer(rf"(?<![\w-]){re.escape(name)}(?P<after>(?:\s+[\w'’-]+){{1,3}})", title or "", re.IGNORECASE):
        found = _head(m.group("after"))         # "The Rust Programming Language Forum", "rscam — Rust video library"
        if found:
            return {"sense": found[0], "head": found[1], "by": "title"}
    return None


def vocabulary(text: str, learned: dict | None = None) -> dict:
    """Vocabulary points of a text per sense class: distinct seed terms, plus LEARNED_WEIGHT per distinct learned
    word (words only the labelled pages of that sense use)."""
    grams = _grams(_stems(text))
    words = {g[0] for g in grams if len(g) == 1}
    points = {cls: float(len(seeds & grams)) for cls, seeds in _SEEDS.items()}
    for cls, vocab in (learned or {}).items():
        points[cls] = points.get(cls, 0.0) + LEARNED_WEIGHT * len(vocab & words)
    return points


def _learned(labelled: dict, texts: dict, name: str) -> dict:
    """{class: the LEARNED_WORDS most frequent words that the pages labelled with that sense use and no page of
    another sense does}; nothing while fewer than two senses are labelled (every word of one sense would count
    for it, "players" and "release" included)."""
    if len(set(labelled.values())) < 2:
        return {}
    skip = set(_stems(name))
    counts: dict[str, dict[str, int]] = {}
    for url, sense in labelled.items():
        c = counts.setdefault(sense, {})
        for t in _stems(texts[url]):
            if len(t) > 3 and t.isalpha() and t not in T.STOPWORDS and t not in skip:
                c[t] = c.get(t, 0) + 1
    out = {}
    for sense, c in counts.items():
        others = set().union(*(set(o) for s, o in counts.items() if s != sense))
        mine = sorted((t for t in c if t not in others), key=lambda t: (-c[t], t))
        out[sense] = set(mine[:LEARNED_WORDS])
    return out


def question_cue(question: str, name: str = "") -> str | None:
    """The sense class a question's own words name ("the Rust video game" -> "video game"), or None when they name
    none or more than one."""
    skip = set(_stems(name))
    stems = [t for t in _stems(question) if t not in skip]
    grams = _grams(stems)
    hits = {cls for cls, cues in _CUES.items() if cues & grams}
    return hits.pop() if len(hits) == 1 else None


def label(sense: str, head: str | None) -> str:
    """"a video game", "an album": the head noun that labelled the sense, else the class label."""
    word = head or SENSES.get(sense, (None,))[0]
    if not word:
        return "other meanings"
    return f"{_ARTICLE[word[0] in 'aeiou']} {word}"


def assign(name: str, sources: list[dict]) -> dict:
    """{url: {"sense", "head", "by"}} for the sources that name `name`; `sources` are {"url", "title", "text",
    "encyclopedia"} (module docstring, rules 1-3). Unassigned sources are absent."""
    out = {}
    for s in sources:
        found = source_sense(s["title"], s["text"], name, encyclopedia=s.get("encyclopedia", False))
        if found:
            out[s["url"]] = found
    texts = {s["url"]: f"{s['title']} {s['text']}" for s in sources}
    learned = _learned({u: v["sense"] for u, v in out.items()}, texts, name)
    for s in sources:
        if s["url"] in out:
            continue
        points = sorted(vocabulary(texts[s["url"]], learned).items(), key=lambda kv: -kv[1])
        (best, top), second = points[0], points[1][1] if len(points) > 1 else 0.0
        if top >= VOCAB_MIN and top >= VOCAB_MARGIN * second:
            out[s["url"]] = {"sense": best, "head": None, "by": "vocabulary"}
    return out


def choose(question: str, name: str, assigned: dict, sources: list[dict], *, technical: bool) -> tuple[str, str]:
    """(sense, how) the question means (module docstring, rules a-c)."""
    cue = question_cue(question, name)
    if cue:
        return cue, "question"
    present = {v["sense"] for v in assigned.values()}
    if technical and "software" in present:
        return "software", "technical question"
    by_url = {s["url"]: s for s in sources}
    for url, v in assigned.items():
        s = by_url.get(url) or {}
        if s.get("encyclopedia") and T.collapse(s.get("title") or "").lower() == name.lower():
            return v["sense"], "encyclopedia article of the bare name"

    def weight(sense):
        urls = [u for u, v in assigned.items() if v["sense"] == sense]
        return (sum(1 for u in urls if (by_url.get(u) or {}).get("encyclopedia")), len(urls), sense)
    return max(present, key=weight), "most sources"


_PLAIN_NAME = re.compile(r"[^\W_][\w .+#'’-]{0,39}")
NOTE_LEAD = "NeuroSciFi note: "


def _join(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def separate(question: str, assessment: dict | None, documents, names, *, mentions) -> dict | None:
    """The sense separation of a report, or None when no named subject of the question has two senses among the
    sources read: {"name", "sense", "label" ("the programming language"), "by" (how the question's sense was
    chosen), "others" (["a video game", "a film"]), "sources" ({url: sense}), "set_aside" (urls of the other
    senses: read, never cited), "note" (said to the reader unless the question itself named the sense)}.
    `mentions(text, name)`: the text names `name` on its own (synthesis.mentions_name). A comparison is not
    separated (each side may be its own sense)."""
    from . import claims as _claims
    assessment = assessment or {}
    if assessment.get("mode") == "comparative" or assessment.get("comparison"):
        return None
    technical = assessment.get("mode") == "technical" or "technical" in (assessment.get("kinds") or [])
    seen = set()
    for name in names or []:
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        sources, registry = [], {}
        # written as the question writes it: "Go" is not the verb "go" (a lower-case name is matched in any case)
        cased = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", 0 if name != name.lower() else re.IGNORECASE)
        for d in documents:
            url = d.canonical_url or d.final_url or d.url
            if not any(cased.search(t) and mentions(t, name) for t in (d.title or "", d.text or "")):
                continue
            if _claims.registry_record(d):          # a package registry's record is software
                registry[url] = {"sense": "software", "head": None, "by": "registry record"}
                continue
            sources.append({"url": url, "title": d.title or "", "text": d.text or "",
                            "encyclopedia": _claims.encyclopedia(d)})
        assigned = assign(name, sources) | registry
        if len({v["sense"] for v in assigned.values()}) < 2:
            continue
        chosen, how = choose(question, name, assigned, sources, technical=technical)
        aside = {u for u, v in assigned.items() if v["sense"] != chosen}
        if not any((d.canonical_url or d.final_url or d.url) not in aside for d in documents):
            continue                    # never every source: the answer is not emptied by a misread sense
        heads = {}
        for v in assigned.values():
            if v.get("head") and v["sense"] not in heads:
                heads[v["sense"]] = v["head"]
        others = list(dict.fromkeys(label(v["sense"], heads.get(v["sense"])) for v in assigned.values()
                                    if v["sense"] != chosen))
        known = [o for o in others if o != "other meanings"] + (["other meanings"] if "other meanings" in others
                                                                  else [])
        mine = label(chosen, heads.get(chosen)).split(" ", 1)[1] if chosen in SENSES or heads.get(chosen) else None
        note = None
        if how != "question" and _PLAIN_NAME.fullmatch(name) and mine:
            note = (f"{NOTE_LEAD}this answer is about {name}, the {mine}. {name} also names {_join(known)}; those "
                    f"sources were read, not cited.")
        return {"name": name, "sense": chosen, "label": f"the {mine}" if mine else None, "by": how,
                "others": known, "sources": {u: v["sense"] for u, v in sorted(assigned.items())},
                "set_aside": sorted(aside), "note": note}
    return None
