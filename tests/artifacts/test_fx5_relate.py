"""Lane FX5 (ADR-081, C1-C3): the relater's structure comparisons are built so that what the code does not read
cannot give an answer. Both security reviews' constructions (fx4_review_pairs: the first review's 28; fx5_pairs: the
second review's 56) and this lane's own adversarial set must never be answered wrongly; pairs whose truth is
"cannot" must get "cannot"; and the honest pairs below must still be answered (the construction is not "always
cannot")."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx4_review_pairs as P4  # noqa: E402
import fx5_pairs as P5  # noqa: E402

from neuroscifi.acceptance.omni.subject import relate  # noqa: E402
from neuroscifi.artifacts import adapters, relate as R, relate_grammar as rg  # noqa: E402

ALL = P4.pairs() + P5.review_pairs() + P5.adversarial_pairs()


@pytest.mark.parametrize("name,files,question,truth", ALL, ids=[p[0] for p in ALL])
def test_never_a_wrong_answer(name, files, question, truth):
    answer = relate(dict(files), question)["answer"]
    if truth is None:
        assert answer is None, name
    else:
        assert answer in (truth, None), name


ANSWERED = ("rv_m15_honest_sha", "rv_m16_honest_sizes", "rv_m18_honest_zip_word", "rv_m20_honest_wrong",
            "rv_l10_honest_named", "rv_t05_honest_ms", "rv_t06_honest_whole_seconds", "rv_t09_honest_wrong",
            "rv_x06_honest_image", "rv_x08_honest_total", "ad_dot_slash_path", "ad_renamed_member",
            "ad_one_digest_spoiled", "ad_all_spoiled_named", "ad_kib_bytes_question", "ad_l_honest",
            "ad_l_honest_swapped", "ad_l_reversed_undirected", "ad_t_honest", "ad_t_whole_second_far",
            "ad_s_honest", "ad_s_honest_wrong")


@pytest.mark.parametrize("name", ("ad_extra_member_listed", "ad_l_honest_wrong", "ad_t_more_listed_far",
                                  "ad2_list_has_all_and_more", "ad2_drawing_shows_text_links",
                                  "ad2_text_lists_more_links", "ad2_every_beep_listed_plus"))
def test_a_difference_on_one_side_only_is_cannot(name):
    """C3: an extra line, link or time on ONE side is a disagreement under one reading of the question and not
    under another ("does the list give the sizes of all the files"): only a difference on both sides is False."""
    files, question, truth = next((f, q, t) for n, f, q, t in ALL if n == name)
    assert relate(dict(files), question)["answer"] is None


@pytest.mark.parametrize("name", ANSWERED)
def test_plain_honest_pairs_are_still_answered(name):
    files, question, truth = next((f, q, t) for n, f, q, t in ALL if n == name)
    assert relate(dict(files), question)["answer"] is truth


def _sides(files):
    return [R.side(adapters.decode(d, name=n), name=n, data=d) for n, d in files.items()]


def test_the_grammar_reads_every_word_or_declines():
    g = rg.GRAMMARS["size"]
    assert rg.parse("Are the byte sizes in the list the sizes of the files in the archive?", g).complete
    for question, unread in (("Apart from a.txt, are the sizes of the files in the list right?", {"apart", "a.txt"}),
                             ("Are any of the sizes of the files in the list wrong?", {"any", "wrong"}),
                             ("Are the compressed sizes of the files in the list right?", {"compressed"}),
                             ("Are the sizes of the files in KiB right in the list?", {"kib"})):
        read = rg.parse(question, g)
        assert not read.complete and unread <= set(read.unread), question
    # what it compares (members, links, sound events) must be named, in the plural or with all / every / each
    assert not rg.parse("Is the list's size right?", g).complete
    assert not rg.parse("Are the sizes of the list and the archive the same?", g).complete
    assert rg.parse("Is the size of every file in the archive right in the list?", g).complete
    # a file is named by its whole name only: no part of a name is read as a word of the question
    assert not rg.parse("Are only the sizes of the files in only.txt right?", g, ("a.zip", "only.txt")).complete
    assert rg.parse("Are the sizes of the files in sizes.txt right?", g, ("a.zip", "sizes.txt")).complete
    assert not rg.parse("Cost?", g).complete and not rg.parse("Check the sizes of the files in the list.", g).complete


def test_only_a_digest_algorithm_named_or_proven_by_a_match_may_disagree():
    kit = P5.KIT
    zipped = P5.zip_of(kit)
    blake = {"k.zip": zipped, "B2SUMS": P5.sums(kit, "blake2b")}
    assert relate(blake, "Do the checksums in B2SUMS match the files in the archive?")["answer"] is None
    named = {"k.zip": zipped, "SHA256SUMS": P5.sums(kit, change=set(kit))}
    assert relate(named, P5.Q_SUMS)["answer"] is False                # the file's name states the algorithm
    unnamed = {"k.zip": zipped, "SUMS": P5.sums(kit, change=set(kit))}
    assert relate(unnamed, P5.Q_SUMS)["answer"] is None               # nothing says which algorithm made it
    one = {"k.zip": zipped, "SUMS": P5.sums(kit, change={"doc/use.txt"})}
    assert relate(one, P5.Q_SUMS)["answer"] is False                  # two agreeing digests prove the algorithm


def test_a_structure_answer_is_specific_only_when_read_completely():
    files = {"k.zip": P5.zip_of(P5.KIT), "list.txt": P5.sizes(P5.KIT)}
    a, b = _sides(files)
    assert R.compare(a, b, P5.Q_KIT).specific is True
    unread = R.compare(a, b, "Are the byte sizes in the list the sizes of the files in the archive, roughly?")
    assert unread.answer is None and not unread.specific
    assert "roughly" in unread.reason or "does not name" in unread.reason
