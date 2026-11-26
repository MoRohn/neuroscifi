"""ADR-063 (Wave 3, third fix lane): WebConfig.origin_grouping.

"transitive" (the default) must group exactly as 3ce4ae3 did, for every input and in every order of the pages: the
second fix lane had made the one-way rule the shipped behaviour, and the final verification (review3c F1) found that it
made copies of one story count as several sources where they had counted as one. "one_way" is ADR-063's rule with the
edge chosen by content (review3c's prototype): N1's bridge stays closed and F1's copies are one source again.

The scenarios are every one of review3c's probes (p20_n1_purpose: the syndication rule's purpose, N1's bridge, F1's
shapes; p22_credits; p25_label_owner: F2's pages; p10b / p10_h1_variants: N3 and N10) and the second fix lane's three
adversarial pages."""
import itertools

import pytest

from neuroscifi.webcortex import claims as C, state
from neuroscifi.webcortex.config import WebConfig
from neuroscifi.webcortex.types import Document

T0 = 1_800_000_000.0
N = "Velmora"
SQ = f"What is the latest {N} release?"
V = "1.5.0"
R = {"by": f"By now {N} {V} is the latest {N} release.",
     "once": f"Once again {N} {V} is the latest {N} release.",
     "days": f"As of today, {N} {V} is the latest {N} release.",
     "curr": f"As of now, {N} {V} is the latest {N} release.",
     "pres": f"{N} {V} is the latest {N} release.",
     "today": f"Meanwhile {N} {V} is the latest stable {N} release.",
     "yest": f"As of yesterday, {N} {V} is the latest {N} release."}
WIRE = f"{N} 7.3.0 is the latest {N} release, the Syndiwire desk reports."
S = f"{N} {V} is the latest {N} release"


def P(host, sent, *origin_lines, path=None, canonical=None):
    """A page (host, text, path, canonical URL): its origin lines, then one sentence stating the version."""
    return (host, " ".join(origin_lines) + ("\n" if origin_lines else "") + R[sent], path, canonical)


def T(host, text, path=None):
    return (host, text, path, None)


# name -> (attribution_independence, pages)
SCENARIOS = {
    "B1 one copy naming its origin": (False, [
        P("devdesk.example", "by"), P("copyone.example", "days", "This story first appeared on Devdesk.")]),
    "B2 three copies naming one origin": (False, [
        P("devdesk.example", "by"), P("copyone.example", "days", "This story first appeared on Devdesk."),
        P("copytwo.example", "curr", "This article was originally published by Devdesk."),
        P("copythree.example", "pres", "It was republished with permission from Devdesk.")]),
    "B3 chain copy-of-copy": (False, [
        P("devdesk.example", "by"), P("copyone.example", "days", "This story first appeared on Devdesk."),
        P("copytwo.example", "curr", "This story first appeared on Copyone."),
        P("copythree.example", "pres", "This story first appeared on Copytwo.")]),
    "B4 three copies of one unread origin": (False, [
        P("copyone.example", "days", "This story first appeared on Syndicate Wire."),
        P("copytwo.example", "curr", "This article was originally published by Syndicate Wire."),
        P("copythree.example", "pres", "It was republished from Syndicate Wire.")]),
    "C1 copy names its origin and an unread outlet sorting first": (False, [
        P("devdesk.example", "by"),
        P("copyone.example", "days", "This story first appeared on Devdesk. It originally appeared in Aardvark Times.")]),
    "C2 copy names its origin and a read honest outlet sorting first": (False, [
        P("devdesk.example", "by"), P("abcnews.example", "once"),
        P("copyone.example", "days", "This story first appeared on Devdesk. It originally appeared in Abcnews.")]),
    "C3 every copy adds an irrelevant unread second origin": (False, [
        P("devdesk.example", "by"),
        P("copyone.example", "days", "This story first appeared on Devdesk. It was republished from Aardvark Times."),
        P("copytwo.example", "curr", "This article was originally published by Devdesk. It was republished from "
                                     "Aardvark Times."),
        P("copythree.example", "pres", "It was first published by Devdesk. It was republished from Aardvark Times.")]),
    "C3b every copy adds a second origin read but not stating the value": (False, [
        P("devdesk.example", "by"), T("aardvark.example", "Aardvark weather is sunny today."),
        P("copyone.example", "days", "This story first appeared on Devdesk. It was republished from Aardvark."),
        P("copytwo.example", "curr", "This article was originally published by Devdesk. It was republished from "
                                     "Aardvark.")]),
    "C4 two copies naming the origin and each other": (False, [
        P("devdesk.example", "by"),
        P("acopy.example", "days", "This story first appeared on Devdesk. It was republished from Bcopy."),
        P("bcopy.example", "curr", "This story first appeared on Devdesk. It was republished from Acopy.")]),
    "C4b four copies, two mutual pairs": (False, [
        P("devdesk.example", "by"),
        P("acopy.example", "days", "This story first appeared on Devdesk. It was republished from Bcopy."),
        P("bcopy.example", "curr", "This story first appeared on Devdesk. It was republished from Acopy."),
        P("ccopy.example", "pres", "This story first appeared on Devdesk. It was republished from Cdcopy."),
        P("cdcopy.example", "today", "This story first appeared on Devdesk. It was republished from Ccopy.")]),
    "C5 copy names an origin not among the pages read": (False, [
        P("devdesk.example", "by"), P("copyone.example", "days", "This story first appeared on Syndicate Wire.")]),
    "C6 two copies naming each other only": (False, [
        P("devdesk.example", "by"), P("acopy.example", "days", "It was republished from Bcopy."),
        P("bcopy.example", "curr", "It was republished from Acopy.")]),
    "C7 copies of one unread origin, one adding (AP)": (False, [
        P("copyone.example", "days", "This story first appeared in the Regional Times. (AP)"),
        P("copytwo.example", "curr", "This story first appeared in the Regional Times.")]),
    "C7b copies naming {Kappa Wire, Zeta Wire}, {Zeta Wire}, {Zeta Wire}": (False, [
        P("copyone.example", "days", "It was first published by Zeta Wire. It was distributed by Kappa Wire."),
        P("copytwo.example", "curr", "It was first published by Zeta Wire."),
        P("copythree.example", "pres", "It was originally published by Zeta Wire.")]),
    "C8 canonical URL to the origin and an unread attribution sorting first": (False, [
        P("devdesk.example", "by"),
        P("copyone.example", "days", "It was republished from Aardvark Times.",
          canonical="http://devdesk.example/velmora")]),
    "C9 copies of an unread origin, each adding a distinct second origin sorting first": (False, [
        P("copyone.example", "days", "This story first appeared on Zeta Wire. It was republished from Alpha Times."),
        P("copytwo.example", "curr", "This article was originally published by Zeta Wire. It was republished from "
                                     "Beta News."),
        P("copythree.example", "pres", "It was first published by Zeta Wire. It was republished from Gamma Post.")]),
    "C9b the same, the real origin read": (False, [
        P("zetawire.example", "by"),
        P("copyone.example", "days", "This story first appeared on Zeta Wire. It was republished from Alpha Times."),
        P("copytwo.example", "curr", "This article was originally published by Zeta Wire. It was republished from "
                                     "Beta News."),
        P("copythree.example", "pres", "It was first published by Zeta Wire. It was republished from Gamma Post.")]),
    "C9c the same, the second origins sorting after the real one": (False, [
        P("copyone.example", "days", "This story first appeared on Alpha Wire. It was republished from Xray Times."),
        P("copytwo.example", "curr", "This article was originally published by Alpha Wire. It was republished from "
                                     "Yankee News."),
        P("copythree.example", "pres", "It was first published by Alpha Wire. It was republished from Zulu Post.")]),
    "N1 bridge: two honest outlets and a page naming both": (False, [
        P("devdesk.example", "by"), P("factline.example", "once"),
        P("hostile.example", "yest", "This story was first published by Devdesk. It originally appeared in Factline.")]),
    "fix2 case: a page naming two outlets as its origins": (False, [
        P("devdesk.example", "by"), P("factline.example", "once"),
        T("hostile.example", f"As of now, {S}. This story was first published by Devdesk. It originally appeared in "
                             f"Factline.", "/d")]),
    "fix2 case: a copy naming one origin": (False, [
        P("devdesk.example", "by"),
        T("copydesk.example", f"This story first appeared on Devdesk.\nThese days {N} {V} is the newest {N} release.",
          "/c")]),
    "fix2 case: a page carrying two wire credits (attribution on)": (True, [
        T("devdesk.example", f"According to Reuters, {S}."), T("factline.example", f"According to Bloomberg, {S}."),
        T("hostile.example", f"As of now, {N} {V} is the current {N} release. (Reuters) (Bloomberg)", "/w")]),
    "F2 defaults: datelines, honest only": (False, [
        T("devdesk.example", f"(Reuters) By now {S}."), T("factline.example", f"(Bloomberg) Once again {S}.")]),
    "F2 defaults: datelines and a reuters.<tld> page naming Bloomberg": (False, [
        T("devdesk.example", f"(Reuters) By now {S}."), T("factline.example", f"(Bloomberg) Once again {S}."),
        T("reuters.example", f"(Bloomberg) As of now, {S}.", "/h")]),
    "F2 attribution on: credits, honest only": (True, [
        T("devdesk.example", f"According to Reuters, {S}."), T("factline.example", f"According to Bloomberg, {S}.")]),
    "F2 attribution on: credits and a reuters.<tld> page naming Bloomberg": (True, [
        T("devdesk.example", f"According to Reuters, {S}."), T("factline.example", f"According to Bloomberg, {S}."),
        T("reuters.example", f"As of now, {S} (Bloomberg).", "/h")]),
    "p22 a wire page and four crediting copies (attribution on)": (True, [
        T("syndiwire.example", WIRE, "/w"),
        T("mirror-one.example", f"According to Syndiwire, {N} 7.3.0 is the latest {N} release.", "/wire-0"),
        T("mirror-two.example", f"{N} 7.3.0 is now the latest {N} release, Syndiwire says.", "/wire-1"),
        T("mirror-three.example", f"Release roundup: {N} 7.3.0 is the latest {N} release (Syndiwire).", "/wire-2"),
        T("mirror-four.example", f"{N} 7.3.0 is the newest {N} release, via Syndiwire.", "/wire-3")]),
    "p22 the copies without the wire page (attribution on)": (True, [
        T("mirror-one.example", f"According to Syndiwire, {N} 7.3.0 is the latest {N} release.", "/wire-0"),
        T("mirror-two.example", f"{N} 7.3.0 is now the latest {N} release, Syndiwire says.", "/wire-1"),
        T("mirror-three.example", f"Release roundup: {N} 7.3.0 is the latest {N} release (Syndiwire).", "/wire-2"),
        T("mirror-four.example", f"{N} 7.3.0 is the newest {N} release, via Syndiwire.", "/wire-3")]),
    "p22 copies crediting unread Syndiwire and a distinct outlet (attribution on)": (True, [
        T("copyone.example", f"According to Syndiwire, {S}, Alpha Times says."),
        T("copytwo.example", f"As of now {S}, Syndiwire reports, Beta News says."),
        T("copythree.example", f"Once again {S} (via Syndiwire), Gamma Post says.")]),
    "N3 a page crediting both wires and carrying both (attribution on)": (True, [
        T("devdesk.example", f"According to Reuters, {S}."), T("factline.example", f"According to Bloomberg, {S}."),
        T("hostile.example", f"According to Reuters, {S}. (Reuters) (Bloomberg)", "/h")]),
    "N3 honest outlets crediting in the sentence, a page carrying both wires (attribution on)": (True, [
        T("devdesk.example", f"By now {S}, Reuters reports."), T("factline.example", f"Once again {S}, Bloomberg says."),
        T("hostile.example", f"As of now, {S}. (Reuters) (Bloomberg)", "/n")]),
    "N10 X credits Reuters, Y Bloomberg, Z both (attribution on)": (True, [
        T("aaa.example", f"By now {S}, Reuters reports."), T("bbb.example", f"Once again {S}, Bloomberg says."),
        T("ccc.example", f"According to Reuters, {S}, Bloomberg says.")]),
}


def _found(pages, attribution, grouping):
    docs = []
    for host, text, path, canonical in pages:
        url = f"http://{host}{path or '/' + N.lower()}"
        docs.append(Document(url=url, final_url=url, canonical_url=canonical or url, domain=host, title=N, text=text,
                             retrieved_at=T0, retrieval_mode="live", published_at=T0 - 86_400.0))
    found = [c for d in docs for c in C.extract_claims(d, SQ) if {"v1.5.0", "v7.3.0"} & set(c.values or ())]
    C.stamp_origins(found, docs, attribution=attribution, grouping=grouping)
    return found


def partition(pages, attribution, grouping="transitive"):
    """The groups as sorted tuples of the hosts (without ".example") of their claims; one host is always one group."""
    found = _found(pages, attribution, grouping)
    host = {c.id: C._domain(c).removesuffix(".example") for c in found}
    return tuple(sorted(tuple(sorted({host[i] for i in g})) for g in C.independence_groups(found)))


def orders(pages):
    return list(itertools.permutations(pages))


def hosts(order):
    return tuple(h.removesuffix(".example") for h, *_ in order)


# The groups 3ce4ae3's independence_groups gives on every scenario in every order of its pages: one grouping in every
# order, except N10 (attribution on), whose grouping depended on the order at 3ce4ae3 (review3b N10), so it is given per
# order (the page hosts in reading order). Obtained once, not by this file's code: review3c's oldgroups.py (the functions
# independence_groups and _groups_with_credits of 3ce4ae3's src/neuroscifi/webcortex/claims.py, exec'd over the claims
# module's namespace) run on these scenarios' claims in all 496 orders (lane fix3's scratch genesis/wave3/
# fix3_expected.py, which also compared the full output lists of that code and of this tree's default: 0 of 496 differ).
EXPECTED_3CE4AE3 = {
    'B1 one copy naming its origin': (('copyone', 'devdesk'),),
    'B2 three copies naming one origin': (('copyone', 'copythree', 'copytwo', 'devdesk'),),
    'B3 chain copy-of-copy': (('copyone', 'copythree', 'copytwo', 'devdesk'),),
    'B4 three copies of one unread origin': (('copyone', 'copythree', 'copytwo'),),
    'C1 copy names its origin and an unread outlet sorting first': (('copyone', 'devdesk'),),
    'C2 copy names its origin and a read honest outlet sorting first': (('abcnews', 'copyone', 'devdesk'),),
    'C3 every copy adds an irrelevant unread second origin': (('copyone', 'copythree', 'copytwo', 'devdesk'),),
    'C3b every copy adds a second origin read but not stating the value': (('copyone', 'copytwo', 'devdesk'),),
    'C4 two copies naming the origin and each other': (('acopy', 'bcopy', 'devdesk'),),
    'C4b four copies, two mutual pairs': (('acopy', 'bcopy', 'ccopy', 'cdcopy', 'devdesk'),),
    'C5 copy names an origin not among the pages read': (('copyone',), ('devdesk',)),
    'C6 two copies naming each other only': (('acopy', 'bcopy'), ('devdesk',)),
    'C7 copies of one unread origin, one adding (AP)': (('copyone', 'copytwo'),),
    'C7b copies naming {Kappa Wire, Zeta Wire}, {Zeta Wire}, {Zeta Wire}': (('copyone', 'copythree', 'copytwo'),),
    'C8 canonical URL to the origin and an unread attribution sorting first': (('copyone', 'devdesk'),),
    'C9 copies of an unread origin, each adding a distinct second origin sorting first':
        (('copyone', 'copythree', 'copytwo'),),
    'C9b the same, the real origin read': (('copyone', 'copythree', 'copytwo', 'zetawire'),),
    'C9c the same, the second origins sorting after the real one': (('copyone', 'copythree', 'copytwo'),),
    'F2 attribution on: credits and a reuters.<tld> page naming Bloomberg': (('devdesk',), ('factline', 'reuters')),
    'F2 attribution on: credits, honest only': (('devdesk',), ('factline',)),
    'F2 defaults: datelines and a reuters.<tld> page naming Bloomberg': (('devdesk', 'factline', 'reuters'),),
    'F2 defaults: datelines, honest only': (('devdesk',), ('factline',)),
    'N1 bridge: two honest outlets and a page naming both': (('devdesk', 'factline', 'hostile'),),
    'N10 X credits Reuters, Y Bloomberg, Z both (attribution on)': {
        ('aaa', 'bbb', 'ccc'): (('aaa', 'ccc'), ('bbb',)),
        ('aaa', 'ccc', 'bbb'): (('aaa', 'bbb', 'ccc'),),
        ('bbb', 'aaa', 'ccc'): (('aaa',), ('bbb', 'ccc')),
        ('bbb', 'ccc', 'aaa'): (('aaa', 'bbb', 'ccc'),),
        ('ccc', 'aaa', 'bbb'): (('aaa', 'bbb', 'ccc'),),
        ('ccc', 'bbb', 'aaa'): (('aaa', 'bbb', 'ccc'),),
    },
    'N3 a page crediting both wires and carrying both (attribution on)': (('devdesk', 'hostile'), ('factline',)),
    'N3 honest outlets crediting in the sentence, a page carrying both wires (attribution on)':
        (('devdesk', 'factline', 'hostile'),),
    'fix2 case: a copy naming one origin': (('copydesk', 'devdesk'),),
    'fix2 case: a page carrying two wire credits (attribution on)': (('devdesk', 'factline', 'hostile'),),
    'fix2 case: a page naming two outlets as its origins': (('devdesk', 'factline', 'hostile'),),
    'p22 a wire page and four crediting copies (attribution on)':
        (('mirror-four', 'mirror-one', 'mirror-three', 'mirror-two', 'syndiwire'),),
    'p22 copies crediting unread Syndiwire and a distinct outlet (attribution on)':
        (('copyone', 'copythree', 'copytwo'),),
    'p22 the copies without the wire page (attribution on)':
        (('mirror-four', 'mirror-one', 'mirror-three', 'mirror-two'),),
}

# origin_grouping "one_way" (ADR-063, off by default) on the same scenarios: one grouping in every order. Computed with
# this lane's code and checked against review3c's statements: N1's bridge, the second fix lane's bridge page, C2 and
# N3/N10 keep the honest outlets apart; F1's shapes (C4, C4b, C7, C7b, C9 and p22's credit form) are one source again
# (under 2c48865's edge choice they were 2-3); F2's reuters.<tld> page no longer owns the name "reuters", so the two
# honest outlets stay apart (2c48865: one group). OPEN with "one_way" (as under 2c48865; 3ce4ae3 kept 2 groups): in "N3
# a page crediting both wires and carrying both" the page repeats devdesk's sentence word for word (a near-duplicate,
# joined with devdesk transitively) and adds "(Bloomberg)", so the joined group takes its one edge to the Bloomberg
# key, where factline meets it: the two honest outlets are one group (ADR-063, "What stays open").
ONE_WAY = {
    'B1 one copy naming its origin': (('copyone', 'devdesk'),),
    'B2 three copies naming one origin': (('copyone', 'copythree', 'copytwo', 'devdesk'),),
    'B3 chain copy-of-copy': (('copyone', 'copythree', 'copytwo', 'devdesk'),),
    'B4 three copies of one unread origin': (('copyone', 'copythree', 'copytwo'),),
    'C1 copy names its origin and an unread outlet sorting first': (('copyone', 'devdesk'),),
    'C2 copy names its origin and a read honest outlet sorting first': (('abcnews', 'copyone'), ('devdesk',)),
    'C3 every copy adds an irrelevant unread second origin': (('copyone', 'copythree', 'copytwo', 'devdesk'),),
    'C3b every copy adds a second origin read but not stating the value': (('copyone', 'copytwo', 'devdesk'),),
    'C4 two copies naming the origin and each other': (('acopy', 'bcopy', 'devdesk'),),
    'C4b four copies, two mutual pairs': (('acopy', 'bcopy', 'ccopy', 'cdcopy', 'devdesk'),),
    'C5 copy names an origin not among the pages read': (('copyone',), ('devdesk',)),
    'C6 two copies naming each other only': (('acopy', 'bcopy'), ('devdesk',)),
    'C7 copies of one unread origin, one adding (AP)': (('copyone', 'copytwo'),),
    'C7b copies naming {Kappa Wire, Zeta Wire}, {Zeta Wire}, {Zeta Wire}': (('copyone', 'copythree', 'copytwo'),),
    'C8 canonical URL to the origin and an unread attribution sorting first': (('copyone', 'devdesk'),),
    'C9 copies of an unread origin, each adding a distinct second origin sorting first':
        (('copyone', 'copythree', 'copytwo'),),
    'C9b the same, the real origin read': (('copyone', 'copythree', 'copytwo', 'zetawire'),),
    'C9c the same, the second origins sorting after the real one': (('copyone', 'copythree', 'copytwo'),),
    'N1 bridge: two honest outlets and a page naming both': (('devdesk', 'hostile'), ('factline',)),
    'fix2 case: a page naming two outlets as its origins': (('devdesk', 'hostile'), ('factline',)),
    'fix2 case: a copy naming one origin': (('copydesk', 'devdesk'),),
    'fix2 case: a page carrying two wire credits (attribution on)': (('devdesk',), ('factline', 'hostile')),
    'F2 defaults: datelines, honest only': (('devdesk',), ('factline',)),
    'F2 defaults: datelines and a reuters.<tld> page naming Bloomberg': (('devdesk',), ('factline', 'reuters')),
    'F2 attribution on: credits, honest only': (('devdesk',), ('factline',)),
    'F2 attribution on: credits and a reuters.<tld> page naming Bloomberg': (('devdesk',), ('factline', 'reuters')),
    'p22 a wire page and four crediting copies (attribution on)':
        (('mirror-four', 'mirror-one', 'mirror-three', 'mirror-two', 'syndiwire'),),
    'p22 the copies without the wire page (attribution on)':
        (('mirror-four', 'mirror-one', 'mirror-three', 'mirror-two'),),
    'p22 copies crediting unread Syndiwire and a distinct outlet (attribution on)':
        (('copyone', 'copythree', 'copytwo'),),
    'N3 a page crediting both wires and carrying both (attribution on)': (('devdesk', 'factline', 'hostile'),),
    'N3 honest outlets crediting in the sentence, a page carrying both wires (attribution on)':
        (('devdesk',), ('factline', 'hostile')),
    'N10 X credits Reuters, Y Bloomberg, Z both (attribution on)': (('aaa',), ('bbb', 'ccc')),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_default_groups_exactly_as_3ce4ae3_in_every_order(name):
    attribution, pages = SCENARIOS[name]
    assert WebConfig().origin_grouping == "transitive"
    expected = EXPECTED_3CE4AE3[name]
    for order in orders(pages):
        want = expected[hosts(order)] if isinstance(expected, dict) else expected
        assert partition(list(order), attribution) == want, (name, hosts(order))


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_one_way_groups_as_recorded_in_every_order(name):
    attribution, pages = SCENARIOS[name]
    assert {partition(list(order), attribution, "one_way") for order in orders(pages)} == {ONE_WAY[name]}, name


def test_every_scenario_has_its_recorded_groups():
    assert set(EXPECTED_3CE4AE3) == set(SCENARIOS) == set(ONE_WAY)
    assert sum(len(orders(pages)) for _, pages in SCENARIOS.values()) == 496


COPIES = ("copyone", "copytwo", "copythree", "acopy", "bcopy", "ccopy", "cdcopy")


def test_what_the_setting_decides_on_the_known_shapes():
    """At the default a page naming two honest outlets as its origins joins them (review3b N1, open); "one_way" keeps
    them apart and still counts the copies of one story naming several origins as one source (review3c F1)."""
    for bridge in ("N1 bridge: two honest outlets and a page naming both",
                   "fix2 case: a page naming two outlets as its origins"):
        assert EXPECTED_3CE4AE3[bridge] == (("devdesk", "factline", "hostile"),)
        assert len(ONE_WAY[bridge]) == 2 and not any({"devdesk", "factline"} <= set(g) for g in ONE_WAY[bridge])
    for name in [n for n in SCENARIOS if n.startswith(("B", "C4", "C7", "C9"))] + [
            "p22 copies crediting unread Syndiwire and a distinct outlet (attribution on)"]:
        with_copies = [g for g in ONE_WAY[name] if set(g) & set(COPIES)]
        assert len(with_copies) == 1, (name, ONE_WAY[name])
    # F2: with datelines, the reuters.<tld> page joins the two honest outlets at the default (open, as at 3ce4ae3);
    # with credits (attribution on) 3ce4ae3 kept them apart and 2c48865 joined them. "one_way" keeps them apart in both.
    f2 = "F2 defaults: datelines and a reuters.<tld> page naming Bloomberg"
    assert len(EXPECTED_3CE4AE3[f2]) == 1 and len(ONE_WAY[f2]) == 2
    f2 = "F2 attribution on: credits and a reuters.<tld> page naming Bloomberg"
    assert len(EXPECTED_3CE4AE3[f2]) == 2 and len(ONE_WAY[f2]) == 2


# ---- the setting and the mark ---------------------------------------------------------------------------------------

def test_at_the_default_stamping_leaves_every_claim_as_it_was():
    _, pages = SCENARIOS["N1 bridge: two honest outlets and a page naming both"]
    found = _found(pages, False, "transitive")
    assert all("origin_grouping" not in (c.flags or {}) for c in found)
    marked = _found(pages, False, "one_way")
    assert all(c.flags["origin_grouping"] == "one_way" for c in marked)
    C.stamp_origins(marked, [], grouping="transitive")         # turned off again: the next stamp removes the mark
    assert all("origin_grouping" not in (c.flags or {}) for c in marked)


def test_one_claim_read_without_the_setting_and_the_default_rule_decides():
    _, pages = SCENARIOS["N1 bridge: two honest outlets and a page naming both"]
    found = _found(pages, False, "one_way")
    assert len(C.independence_groups(found)) == 2
    C.stamp_origins(found[-1:], [], grouping="transitive")
    assert len(C.independence_groups(found)) == 1


@pytest.mark.parametrize("failure", ["raise", "garbage"])
def test_a_failing_one_way_grouping_is_logged_and_the_default_rule_decides(monkeypatch, caplog, failure):
    _, pages = SCENARIOS["N1 bridge: two honest outlets and a page naming both"]
    found = _found(pages, False, "one_way")

    def broken(*args):
        if failure == "raise":
            raise RuntimeError("staged failure about Velmora")
        return "staged-garbage"
    monkeypatch.setattr(C, "_one_way", broken)
    groups = C.independence_groups(found)
    assert len(groups) == 1 and sorted(groups[0]) == sorted(c.id for c in found)
    logged = [r.getMessage() for r in caplog.records if "one-way origin grouping failed" in r.getMessage()]
    assert logged and not any("Velmora" in m for m in logged)
    assert all(c.flags["origin_grouping"] == "one_way" for c in found)        # the claims themselves are not changed


def test_the_setting_is_validated_and_a_damaged_value_reads_as_the_default(tmp_path):
    import json
    from neuroscifi.instance import store
    from neuroscifi.instance.errors import ValidationError
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        assert state.get_config(db).origin_grouping == "transitive"
        state.set_config(db, {"origin_grouping": "one_way"}, T0)
        assert state.get_config(db).origin_grouping == "one_way"
        with pytest.raises(ValidationError):
            state.set_config(db, {"origin_grouping": "one-way"}, T0)
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='webcortex'").fetchone()[0])
        with store.write(db):
            db.execute("UPDATE meta SET value=? WHERE key='webcortex'", (json.dumps(raw | {"origin_grouping": 1}),))
        assert state.get_config(db).origin_grouping == "transitive"
        assert any("origin_grouping" in p for p in state.setting_problems(db))
    finally:
        db.close()
