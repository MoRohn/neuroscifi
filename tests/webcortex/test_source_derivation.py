"""ADR-066 (Wave 4 lane S): WebConfig.source_derivation, one source by evidence of derivation (a page naming or linking
another page read in the same task), off by default.

What is tested here: the default leaves every claim and grouping as it was (the full old-vs-new comparison over every
corpus is scripts/compare_source_derivation.py); the rule joins copies that name a read outlet in any wording; it never
joins two groups that each hold a page naming no other page, whatever other pages say (one hostile page cannot merge
honest outlets); it never separates what today's grouping joins (copies cannot look more independent than today); every
order of the pages gives one grouping; the publication-order and same-values conditions; lane E's hook (a stand-in);
bounds on hostile text; only names are kept (never a host or URL), and forget and restore reach them."""
from contextlib import closing
import itertools
import json
import logging
import random
import time

import pytest

from neuroscifi.acceptance.cognition import adversarial as adv, sources
from neuroscifi.acceptance.cognition import sources_splits_dev as dev
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.webcortex import claims as C, state
from neuroscifi.webcortex.config import WebConfig
from neuroscifi.webcortex.types import Document

T0 = 1_800_000_000.0
DAY = 86_400.0
N = "Velmora"
SQ = f"What is the latest {N} release?"
V = "1.5.0"


def doc(host, text, *, published=T0 - DAY, links=(), path=None):
    url = f"http://{host}{path or '/' + N.lower()}"
    return Document(url=url, final_url=url, canonical_url=url, domain=host, title=N, text=text, retrieved_at=T0,
                    retrieval_mode="live", published_at=published, links=list(links))


def read(docs, *, derivation=True, echo=None, question=SQ):
    found = [c for d in docs for c in C.extract_claims(d, question)]
    origins = C.stamp_origins(found, docs, derivation=derivation, echo=echo)
    return found, origins


def partition(found, origins=None):
    """The groups as sets of hosts (order-free; claims of mirrors naming one canonical URL share an id)."""
    hosts = {}
    for c in found:
        hosts.setdefault(c.id, set()).add(c.domain)
    return frozenset(frozenset().union(*(hosts[i] for i in g)) for g in C.independence_groups(found, origins=origins))


def coarser(fine, coarse) -> bool:
    """Every group of `fine` lies inside one group of `coarse`."""
    return all(any(g <= h for h in coarse) for g in fine)


OUTLET_A = doc("techledger.example", f"At the moment, {N} {V} is the latest {N} release.")
OUTLET_B = doc("buildnotes.example", f"As things stand, {N} {V} is the latest {N} release.")


def test_off_by_default_and_a_claim_read_with_it_off_is_left_exactly_as_it_was():
    assert WebConfig().source_derivation is False
    docs = [OUTLET_A, doc("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.")]
    plain = [c for d in docs for c in C.extract_claims(d, SQ)]
    before = [c.to_json() for c in plain]
    C.stamp_origins(plain, docs)
    assert [c.to_json() for c in plain] == before
    marked, _ = read(docs)
    assert all(isinstance(c.flags.get("derivation"), dict) for c in marked)
    C.stamp_origins(marked, docs, derivation=False)
    assert all("derivation" not in c.flags for c in marked)


@pytest.mark.parametrize("copy", [
    f"Today, {N} {V} is the latest {N} release, Tech Ledger observed.",
    f"As Tech Ledger pointed out, {N} {V} is the latest {N} release.",
    f"Right now, {N} {V} is the latest {N} release.\nHT: Tech Ledger",
    f"Right now, {N} {V} is the latest {N} release.\nRead the full article on Tech Ledger.",
    f"So far, {N} {V} is the latest {N} release (Tech Ledger, via a reader).",
])
def test_a_copy_naming_a_read_outlet_in_any_wording_counts_with_it(copy):
    docs = [OUTLET_A, doc("feedmill.example", copy, published=T0)]
    assert len(partition(*read(docs))) == 1
    assert len(partition(*read(docs, derivation=False))) == 2


def test_a_link_to_a_read_page_counts_as_naming_it_and_keeps_no_host_or_url():
    docs = [OUTLET_A, doc("feedmill.example", f"Today, {N} {V} is the latest {N} release.", published=T0,
                          links=["http://techledger.example/velmora"])]
    found, origins = read(docs)
    assert len(partition(found, origins)) == 1
    for c in found:
        for key in c.flags["derivation"]["cites"]:
            assert key.startswith("name:") and "." not in key and "/" not in key


def _hostile_pages(rng, n):
    """Pages on other hosts stating V that name and link the honest outlets in random ways."""
    names = ["Tech Ledger", "Build Notes", "Stack Daily"]
    links = ["http://techledger.example/velmora", "http://buildnotes.example/velmora", "http://stackdaily.example/x"]
    out = []
    for k in range(n):
        said = ", ".join(rng.sample(names, rng.randint(0, 3)))
        text = f"Briefly, {N} {V} is the latest {N} release" + (f", {said} said." if said else ".")
        if rng.random() < 0.3:      # word for word one honest outlet's sentence, naming the other
            text = f"At the moment, {N} {V} is the latest {N} release. Build Notes agrees."
        out.append(doc(f"hostile{k}.example", text, published=T0 - rng.choice((2, 1, 0)) * DAY,
                       links=rng.sample(links, rng.randint(0, 3)), path=f"/h{k}"))
    return out


def test_two_pages_that_name_nothing_are_never_joined_whatever_other_pages_say():
    """One (or several) hostile pages naming and linking honest outlets cannot merge them (review3 H1, review3b N1/N3
    in this rule's terms): a group holding a page that names no other page never moves, and every other group takes
    one edge, so a component holds at most one such group. Checked in every order of the pages."""
    rng = random.Random(66)
    honest = [OUTLET_A, OUTLET_B, doc("stackdaily.example", f"Today, {N} {V} is the latest {N} release.")]
    for trial in range(60):
        pages = honest + _hostile_pages(rng, rng.randint(1, 3))
        orders = list(itertools.permutations(pages)) if len(pages) <= 5 else \
            [rng.sample(pages, len(pages)) for _ in range(30)]
        seen = set()
        for order in orders:
            groups = partition(*read(list(order)))
            seen.add(groups)
            homes = [next(g for g in groups if d.domain in g) for d in honest]
            assert len(set(homes)) == 3, (trial, groups)
        assert len(seen) == 1


def test_one_hostile_page_joins_two_honest_pages_only_when_both_name_its_own_host():
    """Honest outlets, some naming nothing and some naming another outlet (a credit, read or not), and ONE hostile
    page that names and links any of them from any host, including hosts labelled like a credited outlet (review3c
    F2): two honest pages apart without the hostile page are together with it only when both name the hostile page's
    own site label (then the rule counts them with the outlet they both name, as it would that outlet's own page).
    So the hostile page cannot merge honest outlets otherwise, and cannot make copies look more independent than
    today's grouping (checked with the rule off too)."""
    rng = random.Random(4066)
    labels = ["Tech Ledger", "Build Notes", "Stack Daily", "Open Times", "Codebeat"]
    hosts = ["techledger", "buildnotes", "stackdaily", "opentimes", "codebeat"]
    merged = absorbed = 0
    for trial in range(300):
        honest = []
        for k in rng.sample(range(5), rng.randint(2, 4)):
            named = [labels[x] for x in rng.sample(range(5), rng.randint(0, 2)) if x != k]
            text = f"Lately {N} {V} is the latest {N} release" + (f", {' and '.join(named)} said." if named else ".")
            honest.append(doc(f"{hosts[k]}.example", text, published=T0 - rng.randint(1, 3) * DAY))
        h_label = rng.randrange(5)
        named = rng.sample(labels, rng.randint(0, 3))
        hostile = doc(f"{hosts[h_label]}.{rng.choice(('example', 'hostile'))}" if rng.random() < 0.5 else
                      "hostile.example", f"Briefly, {N} {V} is the latest {N} release" +
                      (f", {', '.join(named)} reported." if named else "."), published=T0 - rng.randint(0, 3) * DAY,
                      links=[f"http://{hosts[x]}.example/velmora" for x in rng.sample(range(5), rng.randint(0, 2))],
                      path="/h")
        if hostile.domain in {d.domain for d in honest}:
            continue
        before = partition(*read(honest))
        after = partition(*read(honest + [hostile]))
        own_label = C.site_label(hostile.domain)
        found, _ = read(honest + [hostile])
        cites = {c.domain: set(c.flags["derivation"]["cites"]) for c in found}
        for a, b in itertools.combinations([d.domain for d in honest], 2):
            apart = next(g for g in before if a in g) != next(g for g in before if b in g)
            together = next(g for g in after if a in g) == next(g for g in after if b in g)
            if apart and together:
                assert f"name:{own_label}" in cites[a] and f"name:{own_label}" in cites[b], (trial, a, b)
                merged += 1
            absorbed += apart and next(g for g in after if a in g) == next(g for g in after if hostile.domain in g)
        assert coarser(partition(*read(honest + [hostile], derivation=False)), after)
    assert merged > 0 and absorbed > 0          # the hostile pages do join honest pages here: the check has power


def _dev_looks(limit=None):
    spec = dev.CYCLES["development"]
    n = 0
    for world in spec["worlds"]:
        for seed in spec["seeds"]:
            g = dev.generate(world, seed)
            for (name, c), look in sorted(g["looks"].items()):
                now = sources.T0 + c * world["cycle_hours"] * 3600.0
                docs = [sources._document(p, now, world, name) for p in look["pages"]]
                if docs:
                    yield name, docs
                    n += 1
                    if limit and n >= limit:
                        return


def test_the_rule_never_separates_what_today_joins_and_every_order_gives_one_grouping():
    """On every look of the development worlds (development material): the rule's groups are unions of today's groups
    (so copies never look more independent than today), and 6 random orders of the pages give one grouping."""
    rng = random.Random(8301)
    joined = 0
    for name, docs in _dev_looks():
        q = f"What is the latest {name} release?"
        today = partition(*read(docs, derivation=False, question=q))
        rule = partition(*read(docs, question=q))
        assert coarser(today, rule), (name, today, rule)
        joined += len(rule) < len(today)
        for _ in range(6):
            assert partition(*read(rng.sample(docs, len(docs)), question=q)) == rule
    assert joined > 20          # the rule is exercised on these worlds


def test_a_page_never_derives_from_a_page_stated_as_published_after_it():
    early = doc("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.", published=T0 - 3 * DAY)
    late = doc("techledger.example", f"At the moment, {N} {V} is the latest {N} release.", published=T0 - DAY)
    assert len(partition(*read([late, early]))) == 2
    undated = doc("techledger.example", f"At the moment, {N} {V} is the latest {N} release.", published=None)
    assert len(partition(*read([undated, early]))) == 1


def test_a_page_naming_an_outlet_that_states_other_values_is_not_joined_with_it():
    other = doc("techledger.example", f"At the moment, {N} 1.4.0 is the latest {N} release.")
    copy = doc("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.", published=T0)
    assert len(partition(*read([other, copy]))) == 2


def test_a_host_named_like_a_cited_outlet_absorbs_no_honest_outlet_that_names_nothing():
    """review3c F2 in this rule's terms: a page on a host labelled like the outlet an honest copy names takes that copy;
    it cannot join two honest outlets that name nothing, and no host is trusted by its name."""
    pages = [OUTLET_B, doc("stackdaily.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said."),
             doc("techledger.hostile", f"Once again {N} {V} is the latest {N} release, Build Notes said.")]
    groups = partition(*read(pages))
    assert next(g for g in groups if "buildnotes.example" in g) != next(g for g in groups if "stackdaily.example" in g)


def test_a_page_the_echo_hook_marks_counts_for_nothing_and_a_failing_hook_is_logged(caplog):
    docs = [OUTLET_A, doc("askthread.example", f"{N} {V} is the latest {N} release, which brings several fixes.")]
    found, origins = read(docs, echo=lambda c: c.domain == "askthread.example")
    assert len(C.independence_groups(found, origins=origins)) == 1
    scored = [C.score(c, now=T0, freshness_class="stable", origins=origins) for c in C.cluster(found)]
    assert sum(c.independent_sources for c in scored) == 1
    assert {c.domain for x in scored for c in x.claims} == {"techledger.example", "askthread.example"}  # still shown

    def broken(claim):
        raise RuntimeError(f"{N} {V} secret")
    with caplog.at_level(logging.WARNING):
        found, origins = read(docs, echo=broken)
    assert len(C.independence_groups(found, origins=origins)) == 2
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)
    assert not any("secret" in r.getMessage() or N in r.getMessage() for r in caplog.records)


def test_a_failing_derivation_grouping_is_logged_and_the_grouping_without_it_decides(monkeypatch, caplog):
    docs = [OUTLET_A, doc("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.")]
    found, origins = read(docs)

    def broken(*_a, **_k):
        raise ValueError(f"{N} {V}")
    monkeypatch.setattr(C, "_derivation_groups", broken)
    with caplog.at_level(logging.WARNING):
        assert len(C.independence_groups(found, origins=origins)) == 2
    assert any("ValueError" in r.getMessage() and N not in r.getMessage() for r in caplog.records)


def test_one_claim_read_without_the_setting_and_todays_grouping_decides():
    docs = [OUTLET_A, doc("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.")]
    found, origins = read(docs)
    plain, _ = read([doc("stackdaily.example", f"Today, {N} {V} is the latest {N} release.")], derivation=False)
    assert len(C.independence_groups(found + plain, origins=origins)) == 3


def test_the_name_reader_is_bounded_on_hostile_text():
    words = " ".join(f"Alpha{i} Beta{i}" for i in range(60_000))          # about 1 MB of capitalised words
    hostile = doc("hostile.example", f"{N} {V} is the latest {N} release. {words} Tech Ledger said.")
    started = time.perf_counter()
    cites = C.derivation_cites([OUTLET_A, hostile])
    assert time.perf_counter() - started < 5.0
    assert cites.get(hostile.url) is None          # the name after DERIVATION_SCAN_WORDS words is not read
    many = [doc(f"o{i}-site.example", f"{N} {V} is the latest {N} release.", path=f"/{i}") for i in range(40)]
    names = " ".join(f"O{i} Site said." for i in range(40))
    roundup = doc("roundup.example", f"{N} {V} is the latest {N} release. {names}", path="/r")
    assert len(C.derivation_cites(many + [roundup])[roundup.url]) == C.DERIVATION_MAX_CITES


def test_derivation_joins_is_display_only_and_absent_at_the_default():
    docs = [OUTLET_A, doc("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.")]
    assert C.derivation_joins(read(docs, derivation=False)[0]) is None
    out = C.derivation_joins(read(docs)[0])
    assert out["groups_today"] == 2 and out["groups"] == 1
    assert out["cited"] == [{"page": "http://feedmill.example/velmora", "names": "techledger",
                             "same_source": "uncertain"}]


def test_the_setting_is_validated_and_a_damaged_value_reads_as_off(tmp_path):
    from neuroscifi.instance.errors import ValidationError
    db = store.create(tmp_path / "s.sqlite", T0)
    with closing(db):
        assert state.get_config(db).source_derivation is False
        state.set_config(db, {"source_derivation": True}, T0)
        assert state.get_config(db).source_derivation is True
        with pytest.raises(ValidationError):
            state.set_config(db, {"source_derivation": "yes"}, T0)
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='webcortex'").fetchone()[0])
        with store.write(db):
            db.execute("UPDATE meta SET value=? WHERE key='webcortex'", (json.dumps(raw | {"source_derivation": 1}),))
        assert state.get_config(db).source_derivation is False
        assert any("source_derivation" in p for p in state.setting_problems(db))


def _flags_text(db):
    return " ".join(f or "" for (f,) in db.execute("SELECT flags FROM web_claims"))


def test_forget_and_restore_reach_what_the_rule_keeps(tmp_path):
    """The rule keeps one thing: on each claim, the normalised names its own page names among the other pages read
    (flags["derivation"]["cites"]), never a host, URL or text of another page. Forgetting the naming page removes its
    claims and their names; forgetting the named page leaves no host or URL of it in any claim; a checkpoint restored
    after the forget replays it."""
    pages = [("techledger.example", f"At the moment, {N} {V} is the latest {N} release.", T0 - DAY, "/a"),
             ("feedmill.example", f"Today, {N} {V} is the latest {N} release, Tech Ledger said.", T0 - DAY, "/b")]
    with adv._s_individual("w4s-forget", **adv._DERIVATION) as (db, path):
        adv._s_look(db, T0, pages)
        assert '"derivation"' in _flags_text(db) and "techledger" in _flags_text(db)
        made = checkpoints.create(db, path, tmp_path / "checkpoints", T0 + 1)
        governance.forget_source(db, "techledger.example", T0 + 2)
        text = _flags_text(db)
        assert "techledger.example" not in text and "http://techledger" not in text
        governance.forget_source(db, "feedmill.example", T0 + 3)
        assert '"derivation"' not in _flags_text(db)
        copy = tmp_path / "restored.sqlite"
        checkpoints.restore(db, made["id"], copy, T0 + 4)
        with closing(store.connect(copy)) as restored:
            assert '"derivation"' not in _flags_text(restored)
            assert restored.execute("SELECT count(*) FROM web_claims WHERE domain IN ('techledger.example', "
                                    "'feedmill.example')").fetchone()[0] == 0
