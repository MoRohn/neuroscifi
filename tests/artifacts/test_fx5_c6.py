"""Lane FX5, ruling C6 (ADR-081), as kept after the owner's decision that verdicts are display-only: (a) a source
holding text a reader does not see never leads to a stored link on any path, and the production's sources reach the
relater with their decoder's format and notes (so its hidden-text checks apply there); (b) with no question, a word
in a sentence never selects an aggregate, and a stated number is compared only with a measured number its own
sentence names. The second review's delta constructions (p05-p16) are rebuilt here with fx5_pairs' builders, beside
this lane's own."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx5_pairs as P  # noqa: E402

from neuroscifi.artifacts import produce as PR, relate as R, relate_cli, store as A  # noqa: E402
from neuroscifi.instance import store  # noqa: E402

T0 = 1_800_000_000.0
CSV = b"Item,Cost (EUR)\nRope,95\nPegs,25\nTarp,40\n"                     # 160 EUR in all
SAID = "The total cost was 160 EUR."
Q = "Is the total cost in the memo the total of the costs in the table?"


def _html(body: str) -> bytes:
    return f"<!doctype html><html><body>{body}</body></html>".encode()


HIDDEN = {   # a memo whose only total is text a reader does not see; what is shown is "Costs for the shed."
    "docx_vanish": ("memo.docx", P.docx([[("Costs for the shed.", False)], [(SAID, True)]])),          # review p05
    "html_opacity": ("memo.html", _html(f'<p>Costs for the shed.</p><p style="opacity:0">{SAID}</p>')),  # p06
    "md_display_none": ("memo.md", f'# Shed\n\nCosts for the shed.\n\n<div style="display:none">\n\n{SAID}\n\n'
                                   f'</div>\n'.encode()),                                              # p07
    "pdf_render_mode_3": ("memo.pdf", P.pdf([SAID], render_mode=3)),                                   # p08
    "odt_display_none": ("memo.odt", P.odt([("Costs for the shed.", False), (SAID, True)])),          # p09
    "html_font_size_0": ("memo.html", _html(f'<p>Costs for the shed.</p><p style="font-size:0">{SAID}</p>')),
    "md_reference_comment": ("memo.md", f"# Shed\n\nCosts for the shed.\n\n[//]: # ({SAID})\n".encode()),
    "pdf_white": ("memo.pdf", P.pdf([SAID], colour="1 1 1 rg")),
    "text_bidi": ("memo.txt", f"Costs for the shed.\n‮{SAID}\n".encode()),
    "rtf_hidden_group": ("memo.rtf", b"{\\rtf1\\ansi Costs for the shed.\\par {\\v " + SAID.encode() + b"}\\par}"),
}


def _held(db, files):
    return [A.add(db, data, name=name, now=T0).id for name, data in files.items()]


def _verdicts(db):
    return db.execute("SELECT src, relation, dst_id FROM artifact_links WHERE relation IN ('consistent_with', "
                      "'contradicts')").fetchall()


@pytest.mark.parametrize("form", sorted(HIDDEN))
def test_unseen_text_never_records_a_link_on_any_path(tmp_path, form):
    name, memo = HIDDEN[form]
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, {"costs.csv": CSV, name: memo})
    out = relate_cli.relate(db, a, b, question=Q, now=T0)                       # the command, with a question
    assert out["answer"] is None or R.NOT_RECORDED in out["because"]
    made = PR.produce(db, "a document of both", [a, b], form="document", now=T0 + 1)   # the production, none
    assert made.status == "made"
    assert _verdicts(db) == []
    db.close()


def test_the_production_passes_its_sources_format(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    a, b = _held(db, {"costs.csv": CSV, "memo.txt": (SAID + "\n").encode()})
    pieces, _ = PR.interpret(db, [a, b], PR.Limits())
    assert [p.decoded_as[0] for p in pieces] == ["csv", "text"]
    sides = [R.side(PR._as_decoded(p), name=p.label) for p in pieces]
    assert [s.fmt for s in sides] == ["csv", "text"]
    db.close()


def test_the_html_readers_hidden_element_note_reaches_the_production_side():
    data = _html(f'<p>Costs for the shed.</p><p hidden>{SAID}</p>')
    from neuroscifi.artifacts import adapters
    dec = adapters.decode(data, name="memo.html")
    piece = PR.B.Piece("artifact-x", "memo.html", dec.representation, "html", dec.representation.shape(),
                       decoded_as=("html", tuple(dec.notes), False))
    side = R.side(PR._as_decoded(piece), name="memo.html", data=data)
    assert side.fmt == "html" and side.source_notes == tuple(dec.notes)


# ---- (b) with no question --------------------------------------------------------------------------------------

@pytest.mark.parametrize("memo", [
    b"The total cost of last year's regatta was 160 EUR.\n",                    # review p12: "last" is not an aggregate
    b"The total cost of last year's regatta was 975 EUR.\n",                    # review p12b
    b"Our largest boat cost 95 EUR to repaint.\n",                              # own: "largest" names another thing
    b"The average member paid 40 EUR this spring.\n",                           # own: "average" of members, not rows
    b"In all we spent 160 EUR.\n",                                              # own: "in all" with no question
])
def test_no_question_no_aggregate_from_a_word(memo):
    from neuroscifi.artifacts import adapters
    a = R.side(adapters.decode(CSV, name="costs.csv"), name="costs.csv", data=CSV)
    b = R.side(adapters.decode(memo, name="memo.txt"), name="memo.txt", data=memo)
    rel = R.compare(a, b, None)
    assert rel.answer is None


def test_no_question_a_number_its_sentence_names_is_still_compared():
    from neuroscifi.artifacts import adapters
    sound = P.wav([0.5, 1.5], total=3.0)
    for note, answer in ((b"The recording lasts 3 seconds.\n", True), (b"The recording lasts 5 seconds.\n", False)):
        a = R.side(adapters.decode(sound, name="s.wav"), name="s.wav", data=sound)
        b = R.side(adapters.decode(note, name="n.txt"), name="n.txt", data=note)
        rel = R.compare(a, b, None)
        assert rel.answer is answer and R.NOT_RECORDED in rel.because()           # shown; never recorded
