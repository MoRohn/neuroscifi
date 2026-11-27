"""Fix lane F4, item 4 (measurement review, second pass, R2-3 and R2-4): the forms driver never re-plans without an
explicit form the product refused and never drops a name silently; whether a climbing or absolute member name names
a member is the product's decision (`artifacts/outcome.member_path`). Views come from `forms.tasks("development")`."""
import copy

from neuroscifi.acceptance.omni import forms, subject
from neuroscifi.artifacts import outcome


def _view():
    task = next(t for t in forms.tasks("development") if t.view["explicit"] is None)
    return copy.deepcopy(task.view)


def test_an_explicit_form_the_product_refuses_is_its_answer_not_a_replan():
    view = _view()
    view["explicit"] = "no_such_form"
    subject.FORMS_UNMAPPED.clear()
    assert subject.choose_form(view) == ""
    assert subject.FORMS_UNMAPPED == [("explicit", "no_such_form"), ("explicit-refused", "no_such_form")]


def test_an_unmapped_name_is_passed_through_and_recorded():
    view = _view()
    plain = subject.choose_form(copy.deepcopy(view))
    view["context"]["prefer"] = ["no_such_form"]
    view["context"]["avoid"] = ["another_unknown"]
    subject.FORMS_UNMAPPED.clear()
    assert subject.choose_form(view) == plain          # the planner takes the names and ignores what it does not know
    assert subject.FORMS_UNMAPPED == [("prefer", "no_such_form"), ("avoid", "another_unknown")]
    subject.FORMS_UNMAPPED.clear()


def test_a_member_path_is_named_by_the_products_table():
    assert outcome.member_path("./a/b.csv") == "a/b.csv"
    assert outcome.member_path("data.csv") == "data.csv"
    for climbing in ("../x.csv", "a/../../x.csv", "/etc/x.csv"):
        assert outcome.member_path(climbing) is None
