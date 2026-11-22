"""sense.py and its use in synthesis: the word sense of an ambiguous subject (T1-07, R4 live: "When was Rust first
released?" was answered about the Rust video game in 2 of 2 runs although "Rust (programming language)" was read)."""
from datetime import datetime, timezone

import pytest

from neuroscifi.webcortex import claims as C
from neuroscifi.webcortex import intent, orchestrator, sense, synthesis as S
from neuroscifi.webcortex.types import Document

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()
D = 86400.0


def doc(url, title, text, *, kind="page", provider=None):
    return Document(url=url, final_url=url, canonical_url=url, domain=url.split("/")[2], title=title, text=text,
                    retrieved_at=NOW - 60, retrieval_mode="api" if kind == "api" else "live", kind=kind,
                    published_at=NOW - 30 * D, metadata={"provider": provider} if provider else {})


def wiki(title, text):
    return doc("https://en.wikipedia.org/wiki/" + title.replace(" ", "_"), title, text, kind="api",
               provider="wikipedia")


def run(question, docs, **kw):
    assessment = intent.assess(question, now=NOW)
    found = [c for d in docs for c in C.extract_claims(d, question)]
    return S.synthesize(question, assessment, docs, C.cluster(found), now=NOW, **kw)


def cited(report):
    by_n = {s["n"]: s["url"] for s in report["sources"]}
    return {by_n[n] for f in report["findings"] + report["answer"]
            for n in [*(f.get("citations") or []), *(f.get("corroborating") or [])] if n in by_n}


def related(report):
    by_n = {s["n"]: s["url"] for s in report["sources"]}
    return {by_n[n] for f in report["findings"] for n in f.get("related") or [] if n in by_n}


# ---- fixtures shaped like the R4 live evidence (retest/r4/ev/rust_first*.json) ---------------------------------
RUST_LANG = wiki("Rust (programming language)", "Rust (programming language) Rust is a general-purpose programming "
                 "language that emphasizes performance, type safety, concurrency, and memory safety. Software "
                 "developer Graydon Hoare created Rust in 2006 while working at Mozilla Research. The first stable "
                 "release, Rust 1.0, was published in May 2015. This helps manage resources such as file handles, "
                 "network sockets, and locks, since when objects are dropped, the resources associated with them are "
                 "closed or released automatically. In the years following 1.0, improvements were made to the "
                 "toolchain ecosystem and a regular compiler testing and release cycle.")
RUST_GAME = wiki("Rust (video game)", "Rust (video game) Rust is a 2018 multiplayer survival video game developed by "
                 "Facepunch Studios. It was first released in early access in December 2013 and received its full "
                 "release in February 2018. Console versions for PlayStation 4 and Xbox One were released in May "
                 "2021. During its pre-release phase, critics praised the concept and gameplay and by March 2017, "
                 "Rust had sold over five million copies.")
RUST_FILM = wiki("Rust (2024 film)", "Rust (2024 film) Rust is a 2024 American Western film written and directed by "
                 "Joel Souza. Rust was released theatrically in the United States on May 2, 2025 to mixed reviews. "
                 "The film was released on 150 screens, while Decal Releasing handled the home release.")
FORCEWIPE = doc("https://whenisforcewiperust.example/rust-release-date",
                "Rust Release Date & History: When Rust Came Out, Legacy & Every Big Update (2026)",
                "Rust feels like it has always been there. It has been on Steam for almost thirteen years, and it "
                "still wipes every month on the first Thursday for its players. Rust has two release dates, and both "
                "are correct depending on what you mean. Rust was first released on Steam Early Access on December "
                "11, 2013, and the full release followed on February 8, 2018. Since the full release, Rust has "
                "followed a simple rhythm of monthly updates. Rust Console Edition released on May 21, 2021 for PS4 "
                "and Xbox One.")
FACEPUNCH = doc("https://rust.facepunch.example/rust10", "10 years of Rust - Rust",
                "From the early days of 2013 crafting makeshift shelters to the intense raids of today, Rust has "
                "evolved into something truly special for our players. We released an updated version of the Rust "
                "trailer to reflect the latest additions to Rust. The biggest gunplay update since Rust's initial "
                "release added new skins and a new multiplayer monument in 2014.")
TUTORIAL = doc("https://w3schools.example/rust", "Rust Tutorial",
               "Rust is a popular programming language used to build everything from web servers to game engines. "
               "Rust is known for being very fast, and is similar to C and C++ in language. Rust code is compiled by "
               "the compiler into a binary, and the standard library ships with the toolchain.")
ANNOUNCE = doc("https://blog.rust-lang.example/2015/05/15/Rust-1.0.html", "Announcing Rust 1.0",
               "Today we are very proud to announce the 1.0 release of Rust, a new programming language aiming to "
               "make it easier to build reliable, efficient systems. The first stable release, Rust 1.0, was "
               "published on May 15, 2015, after years of compiler work. Cargo, the package manager, installs "
               "dependencies and compiles your crate with memory safety guaranteed by the type system.")
GAME_SIDE = {RUST_GAME.url, FORCEWIPE.url, FACEPUNCH.url}
LANG_SIDE = {RUST_LANG.url, TUTORIAL.url, ANNOUNCE.url}


def test_a_technical_first_release_question_is_answered_about_the_software_sense():
    """R4: "When was Rust first released?" read the language, game and film articles and pages that never say
    which Rust they mean; the game's "first released in early access in December 2013" ranked 2nd, "The first stable
    release, Rust 1.0, was published in May 2015." 21st. Now: the software sense is chosen (a technical question),
    the game and film sources are read, never cited, and the 1.0 sentence leads."""
    docs = [RUST_FILM, RUST_LANG, RUST_GAME, FORCEWIPE, FACEPUNCH, TUTORIAL]
    r = run("When was Rust first released?", docs)
    s = r["sense"]
    assert (s["name"], s["sense"], s["label"], s["by"]) == ("Rust", "software", "the programming language",
                                                            "technical question"), s
    assert sorted(s["others"]) == ["a film", "a video game"], s
    assert set(s["set_aside"]) == GAME_SIDE | {RUST_FILM.url}, s
    assert not (cited(r) | related(r)) & (GAME_SIDE | {RUST_FILM.url}), r["findings"]
    assert r["findings"][0]["statement"] == "The first stable release, Rust 1.0, was published in May 2015."
    assert "2013" not in " ".join(f["statement"] for f in r["findings"])
    assert s["note"] == ("NeuroSciFi note: this answer is about Rust, the programming language. Rust also names "
                         f"{' and '.join(s['others'])}; those sources were read, not cited.")
    assert any(u.startswith("Rust also names") and "not cited" in u for u in r["uncertainties"]), r["uncertainties"]
    # every source was read: the other senses are listed as sources, but nothing cites them
    assert {x["url"] for x in r["sources"]} >= GAME_SIDE


def test_senses_never_share_an_answer_cluster():
    """"It was first released in early access in December 2013 ..." (game) and "The first stable release, Rust 1.0,
    was published in May 2015." (language) cluster together on their words; the report keeps only the chosen
    sense's claims in every cluster."""
    docs = [RUST_LANG, RUST_GAME, FORCEWIPE]
    found = [c for d in docs for c in C.extract_claims(d, "When was Rust first released?")]
    mixed = [c for c in C.cluster(found) if {x.source_url for x in c.claims} & GAME_SIDE and
             {x.source_url for x in c.claims} & LANG_SIDE]
    assert mixed            # the evaluation clusters them: "Rust 1.0 ... May 2015" with "... Steam Early Access ..."
    r = run("When was Rust first released?", docs)
    by_id = {x.id: x.source_url for x in found}
    for f in r["findings"]:
        assert not {by_id[i] for i in f["claims"]} & GAME_SIDE, (f, mixed)


def test_the_senses_separate_by_vocabulary_when_the_encyclopedia_is_rate_limited():
    """R4: when Wikipedia was rate-limited only the game pages stated a release. No page has a qualified title: the
    tutorial's defining sentence and the seed vocabulary (cargo, compiler, crate, memory safety against Steam, early
    access, players, raids) still separate them."""
    docs = [FORCEWIPE, FACEPUNCH, TUTORIAL, ANNOUNCE]
    r = run("When was Rust first released?", docs,
            failures=[{"stage": "discovering", "provider": "wikipedia", "code": "rate_limited"}])
    s = r["sense"]
    assert s["sense"] == "software" and set(s["set_aside"]) == {FORCEWIPE.url, FACEPUNCH.url}, s
    assert s["sources"][TUTORIAL.url] == "software" and s["sources"][ANNOUNCE.url] == "software"
    assert not cited(r) & GAME_SIDE
    assert "2015" in r["findings"][0]["statement"], r["findings"][:3]
    assert s["others"] == ["a video game"]


def test_a_named_sense_sets_aside_other_sense_pages_found_by_the_web_search():
    """R4 live retest (rust_game, Wikipedia rate-limited): the web search for "Rust video game first released" also
    read The Rust Programming Language Forum and a Rust video-capture crate; both are the software sense."""
    forum = doc("https://users.rust-lang.example/t/rust-video-introduction/64549", "Rust Video Introduction? - The "
                "Rust Programming Language Forum", "We have an event here at work every week, where software "
                "engineers watch some video to learn something new, and there was one about Rust last week.")
    crate = doc("https://lib.rs.example/crates/rscam", "rscam — Rust video library // Lib.rs",
                "Rust wrapper for the video4linux capture API, first released in 2015 as a small crate.")
    r = run("When was the Rust video game first released?", [FORCEWIPE, FACEPUNCH, forum, crate])
    s = r["sense"]
    assert (s["sense"], s["by"], set(s["set_aside"])) == ("video game", "question", {forum.url, crate.url}), s
    assert cited(r) and cited(r) <= GAME_SIDE


def test_a_question_that_names_the_sense_is_answered_about_it_without_a_note():
    docs = [RUST_FILM, RUST_LANG, RUST_GAME, FORCEWIPE, FACEPUNCH, TUTORIAL, ANNOUNCE]
    r = run("When was the Rust video game first released?", docs)
    s = r["sense"]
    assert (s["sense"], s["by"], s["note"]) == ("video game", "question", None), s
    assert set(s["set_aside"]) == LANG_SIDE | {RUST_FILM.url}
    assert cited(r) and cited(r) <= GAME_SIDE, cited(r)
    top = r["findings"][0]["statement"]
    assert "2013" in top and ("2018" in top or "first released" in top), r["findings"][:3]
    assert not any("2015" in f["statement"] for f in r["findings"])


# ---- other ambiguous names: the mechanism is not Rust-specific -------------------------------------------------
PY_LANG = wiki("Python (programming language)", "Python (programming language) Python is a high-level, general-"
               "purpose programming language. Van Rossum first released it in 1991 as Python 0.9.0. Python 2.0 was "
               "released in 2000 and introduced list comprehensions.")
PY_GENUS = wiki("Python (genus)", "Python (genus) Python is a genus of constricting snakes in the Pythonidae family "
                "native to the tropics and subtropics of the Eastern Hemisphere. The largest Python species can grow "
                "to lengths of more than 6 metres. A Python kills its prey by constriction and swallows it whole.")
PY_KEEPER = doc("https://reptiles.example/python-care", "Keeping a Python at home",
                "Wild species of Python live in forests and grasslands, where they hunt prey such as rodents and "
                "birds. Python eggs are incubated by the female, and the habitat of a captive Python must stay warm. "
                "An adult Python can grow longer than 5 metres in the wild.")


def test_python_the_language_and_python_the_snake():
    r = run("When was Python first released?", [PY_GENUS, PY_KEEPER, PY_LANG])
    s = r["sense"]
    assert (s["sense"], s["by"], s["others"]) == ("software", "technical question", ["a genus"]), s
    assert s["sources"][PY_KEEPER.url] == "organism"          # unlabelled: by vocabulary
    assert cited(r) == {PY_LANG.url}
    assert r["findings"][0]["statement"] == "Van Rossum first released it in 1991 as Python 0.9.0."
    assert "Python also names a genus" in s["note"]

    r = run("How long do Python snakes grow?", [PY_GENUS, PY_KEEPER, PY_LANG])
    s = r["sense"]
    assert (s["sense"], s["by"]) == ("organism", "question") and s["set_aside"] == [PY_LANG.url], s
    assert cited(r) and PY_LANG.url not in cited(r)


MERCURY_PLANET = wiki("Mercury (planet)", "Mercury (planet) Mercury is the first planet from the Sun and the "
                      "smallest in the Solar System. Mercury orbits the Sun at an average distance of about 58 million "
                      "kilometres. Its surface is heavily cratered and similar in appearance to the Moon.")
MERCURY_ELEMENT = wiki("Mercury (element)", "Mercury (element) Mercury is a chemical element; it has symbol Hg and "
                       "atomic number 80. Mercury is the only metal that is liquid at standard temperature and "
                       "pressure. Mercury is toxic, and mercury poisoning can result from exposure to its vapour.")
MERCURY_PROBE = doc("https://space.example/messenger", "What MESSENGER found",
                    "The MESSENGER spacecraft mapped the craters on Mercury and measured its thin atmosphere. "
                    "Mercury completes one orbit around the Sun every 88 days, and NASA plans more probe missions "
                    "to study its surface.")


def test_mercury_the_planet_and_mercury_the_element():
    r = run("How far is Mercury from the Sun?", [MERCURY_ELEMENT, MERCURY_PLANET, MERCURY_PROBE])
    s = r["sense"]
    assert (s["name"], s["sense"], s["by"]) == ("Mercury", "planet", "question"), s
    assert s["set_aside"] == [MERCURY_ELEMENT.url] and s["sources"][MERCURY_PROBE.url] == "planet"
    assert MERCURY_ELEMENT.url not in cited(r) and MERCURY_PLANET.url in cited(r)

    r = run("What is the atomic number of Mercury?", [MERCURY_ELEMENT, MERCURY_PLANET, MERCURY_PROBE])
    s = r["sense"]
    assert (s["sense"], s["by"]) == ("chemical", "question"), s
    assert set(s["set_aside"]) == {MERCURY_PLANET.url, MERCURY_PROBE.url}
    assert cited(r) == {MERCURY_ELEMENT.url}
    assert "atomic number 80" in r["findings"][0]["statement"]


def test_go_the_language_and_go_the_board_game():
    lang = wiki("Go (programming language)", "Go (programming language) Go is a high-level general purpose "
                "programming language that is statically typed and compiled. It was designed at Google in 2007 by "
                "Robert Griesemer, Rob Pike, and Ken Thompson. Go was publicly announced in November 2009, and "
                "version 1.0 was released in March 2012.")
    game = wiki("Go (game)", "Go (game) Go is an abstract strategy board game for two players in which the aim is to "
                "fence off more territory than the opponent. The game was invented in China more than 2,500 years "
                "ago and is the oldest board game continuously played to the present day.")
    r = run("When was Go first released?", [game, lang])
    s = r["sense"]
    assert (s["sense"], s["others"]) == ("software", ["a game"]), s
    assert cited(r) == {lang.url}
    assert "version 1.0 was released in March 2012" in r["findings"][0]["statement"], r["findings"][:3]


def test_one_sense_in_the_evidence_is_no_separation():
    r = run("When was Python first released?", [PY_LANG, doc("https://docs.python.example/history", "History of the "
                                                              "software", "Python was created by Guido van Rossum "
                                                              "and first released in February 1991 as a compiler-free "
                                                              "interpreter with a standard library.")])
    assert r["sense"] is None
    assert not any("also names" in u for u in r["uncertainties"])


def test_a_named_sense_that_no_source_holds_never_empties_the_answer():
    r = run("When was the Rust film first released?", [RUST_LANG, RUST_GAME])
    assert r["sense"] is None and r["findings"]


def test_a_comparison_is_not_separated():
    assert sense.separate("Compare Rust and Go", {"mode": "comparative", "comparison": ["Rust", "Go"]},
                          [RUST_LANG, RUST_GAME], ["Rust", "Go"], mentions=S.mentions_name) is None


# ---- the parts --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("title,text,expected", [
    ("Rust (video game)", "", ("video game", "video game", "title")),
    ("Rust (2024 film)", "", ("film", "film", "title")),
    ("Python (genus)", "", ("organism", "genus", "title")),
    ("Rust Tutorial", "Rust is a popular programming language used to build game engines.",
     ("software", "programming language", "defining sentence")),
    ("Mercury", "Mercury is the first planet from the Sun.", ("planet", "planet", "defining sentence")),
    ("Monty Python", "Monty Python, also known as the Pythons, were a British comedy troupe.", None),
    ("Rust Software Engineer (Contract) | G-Research", "We are hiring engineers.", ("software", "software", "title")),
    ("Rust Video Introduction? - The Rust Programming Language Forum", "Is there a video?",
     ("software", "programming language", "title")),
    ("Bevy, a Rust game engine", "Bevy is a data-driven engine.", ("software", "game engine", "title")),
    ("Rust on Steam", "Buy now.", None),
])
def test_source_sense_from_title_qualifier_and_defining_sentence(title, text, expected):
    name = next(n for n in ("Rust", "Python", "Mercury") if n in title)
    got = sense.source_sense(title, text, name)
    assert (got and (got["sense"], got["head"], got["by"])) == (expected or None)


def test_an_encyclopedia_qualifier_outside_the_lexicon_is_a_sense_of_its_own():
    got = sense.source_sense("Mercury (mythology)", "", "Mercury", encyclopedia=True)
    assert got == {"sense": "qualifier:mythology", "head": None, "by": "title"}
    assert sense.label(got["sense"], None) == "other meanings"


@pytest.mark.parametrize("question,expected", [
    ("When was the Rust video game first released?", "video game"),
    ("When was Rust first released?", None),
    ("Which crate does Rust use for async?", "software"),
    ("How long do Python snakes grow?", "organism"),
    ("How far is Mercury from the Sun?", "planet"),
    ("What is the atomic number of Mercury?", "chemical"),
    ("Is the Rust game on Steam written in the Rust programming language?", None),     # two senses: undecided
])
def test_question_cue(question, expected):
    assert sense.question_cue(question, "Rust") == expected


@pytest.mark.parametrize("text,expected", [
    ("The first stable release, Rust 1.0, was published in May 2015.", True),
    ("Van Rossum first released it in 1991 as Python 0.9.0.", True),
    ("Go was publicly announced in November 2009, and version 1.0 was released in March 2012.", True),
    ("In the years following 1.0, improvements were made to a regular compiler testing and release cycle.", False),
    ("Its first goal would be to own and take financial responsibility for trademarks.", False),
    ("Since the full release, Rust has followed a simple rhythm.", False),
])
def test_first_release_cue(text, expected):
    assert S.first_release(text) is expected


def test_an_encyclopedia_query_for_a_named_sense_does_not_add_software():
    q = "When was the Rust video game first released?"
    plan = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert [x.text for x in plan.queries if x.kind == "encyclopedia"] == ["Rust video game"]
    q = "When was Rust first released?"
    plan = intent.plan(q, intent.assess(q, now=NOW), now=NOW)
    assert [x.text for x in plan.queries if x.kind == "encyclopedia"] == ["Rust software"]


def test_the_reader_is_told_which_sense_was_answered_beside_a_verdict_note():
    note = "NeuroSciFi note: this answer is about Rust, the programming language. Rust also names a video game; " \
           "those sources were read, not cited."
    report = {"sense": {"note": note}, "reasoning": {"status": "model"}}
    orchestrator._sense_note(report, "owner")
    assert report["reasoning"]["note"] == {"text": note, "by": "rules"}
    report = {"sense": {"note": note}, "reasoning": {"note": {"text": "NeuroSciFi note: the evidence read does not "
                                                                       "establish whether X.", "by": "rules"}}}
    orchestrator._sense_note(report, "confirmed")
    assert report["reasoning"]["note"]["text"] == ("NeuroSciFi note: the evidence read does not establish whether "
                                                   "X. This answer is about Rust, the programming language. Rust "
                                                   "also names a video game; those sources were read, not cited.")
    report = {"sense": {"note": note}, "reasoning": {"status": "model"}}
    orchestrator._sense_note(report, "autonomous")
    assert "note" not in report["reasoning"]
