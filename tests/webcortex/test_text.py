"""text.py: tokens, sentences, BM25, shingles, values, normal forms."""
import math

import pytest

from neuroscifi.webcortex import text as T


# ---- tokens ------------------------------------------------------------------------------------------------

def test_tokens_keep_versions_alphanumerics_and_unicode():
    toks = T.tokens("NumPy 2.3.5 needs CUDA13 on an H100; Café déjà-vu, v1.2rc1 and 1,200 users.")
    assert "2.3.5" in toks and "cuda13" in toks and "h100" in toks
    assert "café" in toks and "déjà" in toks and "vu" in toks
    assert "v1.2rc1" in toks and "1200" in toks
    assert all(t == t.lower() for t in toks)


def test_tokens_keep_case_option():
    assert T.tokens("NumPy and ESA", keep_case=True) == ["NumPy", "and", "ESA"]


def test_tokens_empty_and_none():
    assert T.tokens("") == [] and T.tokens(None) == []


def test_stopwords_are_english_function_words():
    for w in ("the", "and", "of", "is", "was", "which", "would"):
        assert w in T.STOPWORDS
    for w in ("numpy", "neuron", "increase", "version"):
        assert w not in T.STOPWORDS


# ---- sentences ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Dr. Smith et al. reported a 3.5% rise in Fig. 2 of the study. The effect held.",
     ["Dr. Smith et al. reported a 3.5% rise in Fig. 2 of the study.", "The effect held."]),
    ("Many tools, e.g. NumPy, use BLAS. Others do not.",
     ["Many tools, e.g. NumPy, use BLAS.", "Others do not."]),
    ("The U.S. agency agreed. It said so.", ["The U.S. agency agreed.", "It said so."]),
    ("Version 2.3.5 is out! Is it stable? Yes.", ["Version 2.3.5 is out!", "Is it stable?", "Yes."]),
    ("See https://example.com/a.b?x=1.5 for details. Next sentence here.",
     ["See https://example.com/a.b?x=1.5 for details.", "Next sentence here."]),
    ("J. Doe wrote the paper. It was cited.", ["J. Doe wrote the paper.", "It was cited."]),
    ("The answer is no. Then it ended.", ["The answer is no.", "Then it ended."]),
    ("It appeared in No. 5 of the series. Fine.", ["It appeared in No. 5 of the series.", "Fine."]),
    ("Pi is 3.14159 roughly. Next.", ["Pi is 3.14159 roughly.", "Next."]),
    ('He said "Stop." Then he left.', ['He said "Stop."', "Then he left."]),
    ("The meeting is on Nov. 3 in Paris. Bring notes.", ["The meeting is on Nov. 3 in Paris.", "Bring notes."]),
    ("lower case after a period. does not split here.", ["lower case after a period. does not split here."]),
])
def test_sentence_splitting_edge_cases(text, expected):
    assert T.sentences(text) == expected


def test_sentences_hard_breaks_on_headings_lists_and_blank_lines():
    text = ("Breaking News\nThe probe arrived today.\n\n- first item here\n- second item\n"
            "This line is soft\nwrapped across lines. Last one.")
    assert T.sentences(text) == ["Breaking News", "The probe arrived today.", "- first item here", "- second item",
                                 "This line is soft wrapped across lines.", "Last one."]


def test_hard_newlines_never_glue_a_heading_to_the_next_block():
    # A heading of more than 10 words without punctuation used to be joined with the next line (soft wrap).
    text = ("Hands on with the brand new Zephyr compiler from the embedded project team\n"
            "The Zephyr compiler version 4.2.0 was released on 17 November 2026.")
    assert T.sentences(text, hard_newlines=True) == [
        "Hands on with the brand new Zephyr compiler from the embedded project team",
        "The Zephyr compiler version 4.2.0 was released on 17 November 2026."]
    assert len(T.sentences(text)) == 1                       # soft mode (PDF/plain text) still joins wrapped lines
    assert T.sentences("one line\n\nother line", hard_newlines=True) == ["one line", "other line"]


@pytest.mark.parametrize("line, title, heading", [
    ("Hands on with the Zephyr compiler", "", True),
    ("Hands on with the Zephyr compiler", "Hands on with the Zephyr compiler", True),
    ("Zephyr compiler 4.2 released", "Zephyr compiler 4.2 released", True),          # equal to the page title
    ("Kayaking The Whole Varnel River From Source To Sea In Three Long Weeks", "", True),   # Title Case
    ("The Zephyr compiler version 4.2.0 was released on 17 November 2026.", "", False),
    ("Nimbus-OS 5 ships with Linux kernel 6.14 and the Wayland session by default", "", False),
    ("Developers report that the compiler now supports more targets", "", False),
    ("", "", False),
])
def test_heading_like(line, title, heading):
    assert T.heading_like(line, title) is heading


def test_sentence_spans_point_into_the_original_text():
    text = "  First one here.  Second one there!\nThird?  "
    spans = T.sentence_spans(text)
    assert [text[a:b] for a, b in spans] == ["First one here.", "Second one there!", "Third?"]


def test_sentences_empty():
    assert T.sentences("") == [] and T.sentences("   \n  ") == []


# ---- BM25 --------------------------------------------------------------------------------------------------

def test_bm25_ranks_the_relevant_document_first():
    docs = [T.tokens("numpy release notes for version 2.3"), T.tokens("pandas dataframe tutorial"),
            T.tokens("the weather in paris today")]
    scores = T.BM25(docs).scores(T.tokens("numpy release"))
    assert scores[0] > 0 and scores[1] == 0 and scores[2] == 0


def test_bm25_rare_terms_weigh_more_than_common_terms():
    docs = [["common", "rare"], ["common"], ["common"], ["common", "x"]]
    bm = T.BM25(docs)
    assert bm.idf["rare"] > bm.idf["common"] > 0
    assert bm.scores(["rare"])[0] > bm.scores(["common"])[0]


def test_bm25_length_normalisation_and_saturation():
    short, long_ = ["neuron", "spike"], ["neuron"] + ["filler"] * 30
    scores = T.BM25([short, long_]).scores(["neuron"])
    assert scores[0] > scores[1]
    sat = T.BM25([["a"] * 1, ["a"] * 10]).scores(["a"])
    assert sat[1] < 10 * sat[0]                         # term-frequency saturation (k1)


def test_bm25_duplicate_query_terms_count_once_and_empty_corpus():
    bm = T.BM25([["numpy"], ["other"]])
    assert bm.scores(["numpy", "numpy"]) == bm.scores(["numpy"])
    assert T.BM25([]).scores(["x"]) == []
    assert T.BM25_K1 == 1.5 and T.BM25_B == 0.75


# ---- shingles / jaccard ------------------------------------------------------------------------------------

def test_shingles_and_jaccard():
    a = T.shingles("the quick brown fox jumps over the lazy dog", k=3)
    assert "the quick brown" in a and len(a) == 7
    assert T.shingles("two words", k=5) == {"two words"}
    assert T.shingles("", k=3) == set()
    assert T.jaccard(a, a) == 1.0
    assert T.jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)
    assert T.jaccard(set(), {1}) == 0.0 and T.jaccard(set(), set()) == 0.0


# ---- values ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("NumPy 2.3.5 was released on 3 November 2026 with a 42% speedup.", ["v2.3.5", "2026-11-03", "42%"]),
    ("Revenue rose to $1.2 billion, up 12 percent, in 2025.", ["USD 1200000000", "12%", "2025"]),
    ("We recorded 24 neurons from 6 mice (n = 24).", ["24 neurons", "6 mice", "24"]),
    ("Upgrade to version 2.4 or v1.2rc1; 2.3.5rc1 is out [12].", ["v2.4", "v1.2rc1", "v2.3.5rc1"]),
    ("It costs 30 USD and 5 million euros.", ["USD 30", "EUR 5000000"]),
    ("Dated Nov 3, 2026 and 2026-11-04T10:00Z.", ["2026-11-03", "2026-11-04"]),
    ("Unemployment fell 0.3 percentage points.", ["0.3 pp"]),
    ("It runs on cuda13 and an H100 during COVID-19 and came 3rd.", []),
    ("About 1,200 users and 3.5 kg.", ["1200 users", "3.5 kg"]),
    ("A dose of 10 mg/kg was used.", ["10 mg/kg"]),
])
def test_values_extraction(text, expected):
    assert T.values(text) == expected


def test_value_kind_classification():
    assert T.value_kind("2026-11-03")[0] == "date"
    assert T.value_kind("2026")[:2] == ("year", "year")
    assert T.value_kind("v2.3.5") == ("version", "version", "2.3.5")
    assert T.value_kind("USD 30")[:2] == ("money", "money:USD")
    assert T.value_kind("42%") == ("percent", "percent", 42.0)
    assert T.value_kind("24 mice")[:2] == ("quantity", "quantity:mice")
    assert T.value_kind("3.5") == ("number", "number:dec", 3.5)
    assert T.value_kind("412") == ("number", "number:int", 412.0)
    assert T.value_kind("412 km") == ("quantity", "quantity:length", 412000.0)
    assert T.value_kind("412 kilometres") == T.value_kind("412 km")          # any spelling of the unit
    assert T.value_kind("9800 km²")[:2] == ("quantity", "quantity:area")


@pytest.mark.parametrize("a, b, conflict", [
    (["42%"], ["12%"], "different values: 42% vs 12%"),
    (["v2.3"], ["v2.4"], "different versions: v2.3 vs v2.4"),
    (["2026-11-03"], ["2026-11-05"], "different dates: 2026-11-03 vs 2026-11-05"),
    (["42%"], ["42.2%"], None),                          # within 1 % relative
    (["v2.3.5"], ["v2.3.5"], None),
    (["2026-11-03"], ["2026-11-03"], None),
    (["24 mice"], ["12 mg"], None),                      # different units are not comparable
    (["USD 5", "2024"], ["USD 6", "2025"], None),        # a series over periods, not a conflict
    (["USD 5", "2024"], ["USD 6", "2024"], "different values: USD 5 vs USD 6"),
    (["42%", "10%"], ["42%"], None),                     # overlap = compatible
    ([], ["42%"], None),
])
def test_values_conflict(a, b, conflict):
    assert T.values_conflict(a, b) == conflict


# Benchmark A02/F02 shape (F1): one page writes the unit out, another abbreviates it.
VARNEL_412 = "The Varnel River is 412 kilometres long and drains a basin of about 9,800 square kilometres."
VARNEL_520 = "the Varnel River is 520 km long"


def test_unit_spellings_normalise_so_the_same_quantity_compares():
    assert T.values(VARNEL_412) == ["412 km", "9800 km²"]
    assert T.values(VARNEL_520) == ["520 km"]
    assert T.values("It is 412 kilometers or 412 kilometre long.") == ["412 km"]
    assert T.values("A discharge of 96 cubic metres per second, 5 seconds, 16 gigabytes.") == \
        ["96 m³/s", "5 s", "16 gb"]
    assert T.values_conflict(T.values(VARNEL_412), T.values(VARNEL_520)) == "different values: 412 km vs 520 km"
    assert T.values_conflict(T.values("With a length of 412 km, the Varnel is long."), T.values(VARNEL_412)) is None
    assert T.values_conflict(["1.2 km"], ["1200 m"]) is None                  # converted within one dimension
    assert T.values_conflict(["9800 km²"], ["520 km"]) is None                # area is not length


@pytest.mark.parametrize("a, b, conflict", [
    (["412", "9800"], ["520 km"], "different values: 412 vs 520 km"),    # the only plain number of that magnitude
    (["412"], ["412 km"], None),                                         # same number, unit left implicit
    (["24", "9800"], ["520 km"], None),                                  # no plain number of that magnitude
    (["412", "450"], ["520 km"], None),                                  # two candidates: ambiguous
    (["412"], ["520 km", "980 km²"], None),                              # two quantities of that magnitude
    (["412 km"], ["520"], "different values: 412 km vs 520"),            # symmetric
])
def test_plain_number_compares_with_a_quantity_by_magnitude(a, b, conflict):
    assert T.values_conflict(a, b) == conflict


@pytest.mark.parametrize("text, expected", [
    ("Estimates range between 400 and 520 km.", ["400 km", "520 km"]),
    ("Estimates range 400–520 km.", ["400 km", "520 km"]),
    ("A dose from 5 to 10 mg.", ["5 mg", "10 mg"]),
    ("It took 3 or 4 days, up 5 to 10%.", ["3 days", "4 days", "5%", "10%"]),
    ("Nimbus-OS 5 and 512 MB of memory.", ["5", "512 mb"]),             # "and" joins only after "between"
])
def test_a_coordinated_plain_number_takes_the_following_unit(text, expected):
    assert T.values(text) == expected


# Benchmark C4/F07 shape (F2): "Nimbus-OS 5" is shared, the kernel versions differ.
NIMBUS_614 = "Nimbus-OS 5 ships with Linux kernel 6.14 and the Wayland session by default."
NIMBUS_612 = "Nimbus-OS 5 ships with Linux kernel 6.12, according to the beta images we tested."


def test_a_shared_incidental_number_does_not_mask_a_conflict():
    assert T.values(NIMBUS_614) == ["5", "6.14"] and T.values(NIMBUS_612) == ["5", "6.12"]
    assert T.values_conflict(T.values(NIMBUS_614), T.values(NIMBUS_612)) == "different values: 6.14 vs 6.12"
    assert T.values_conflict(["5", "6.14"], ["5", "6.14"]) is None
    assert T.values_conflict(["5", "v6.14"], ["5", "v6.12"]) == "different versions: v6.14 vs v6.12"
    assert T.values_conflict(["4"], ["4.5"]) == "different values: 4 vs 4.5"     # no shared kind: compared
    assert T.values_conflict(["42.1%"], ["42.3%"]) == "different values: 42.1% vs 42.3%"   # same precision
    # the time-series rule still holds: different years and different values are a series
    assert T.values_conflict(["5", "6.14", "2025"], ["5", "6.12", "2026"]) is None
    assert T.values_conflict(["5", "6.14", "2026"], ["5", "6.12", "2026"]) == "different values: 6.14 vs 6.12"


# ---- normal forms ------------------------------------------------------------------------------------------

def test_normalise_masks_numbers_and_versions_keeps_order():
    assert T.normalise("NumPy 2.3.5 was released on the 3rd, with 42 new functions!") == \
        "numpy <ver> released 3rd <num> new functions"
    assert T.normalise("  The   QUICK, brown fox.  ") == "quick brown fox"
    assert T.normalise("e.g. this") == ""


def test_stem_merges_simple_inflections():
    assert T.stem("released") == T.stem("release") == T.stem("releases")
    assert T.stem("studies") == T.stem("study")
    assert T.stem("cuda13") == "cuda13" and T.stem("class") == "class"


def test_tolerance_constants_documented():
    assert T.NUMERIC_TOLERANCE == 0.01 and T.DATE_TOLERANCE == 86400.0
    assert math.isclose(T.BM25([["a"]]).idf["a"], math.log(1 + 0.5 / 1.5))
