"""Lane R: the cross family's product subject (`subject.relate`) is a thin driver over artifacts.relate."""
import ast
import pathlib

from neuroscifi.acceptance.omni import cross, subject


def test_the_relate_subject_answers_with_reasons_on_development():
    tasks = [t for t in cross.tasks("development") if t.id in ("xd01+", "xd01-", "xd25+", "xd25-")]
    for t in tasks:
        reply = subject.relate(dict(t.files), t.question)
        assert reply["answer"] is t.truth and reply["because"]


def test_an_unreadable_file_or_a_third_file_is_cannot():
    assert subject.relate({"a.jsonl": b"not json at all\n", "b.csv": b"x,y\n1,2\n"}, "agree?")["answer"] is None
    assert subject.relate({"a": b"1", "b": b"2", "c": b"3"}, "agree?")["answer"] is None


def test_the_driver_imports_the_product_lazily_and_decides_nothing():
    source = pathlib.Path(subject.__file__).read_text()
    fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "relate")
    names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert {"decode", "side", "compare", "answer", "because"} <= names
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(fn))
    assert not any(isinstance(n, ast.Compare) and any(isinstance(c, ast.Constant) and isinstance(c.value, str)
                                                      and c.value in cross.KINDS for c in n.comparators)
                   for n in ast.walk(fn))
