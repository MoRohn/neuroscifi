"""Web questions that go stale (plan §5 item 12): an open web question older than
config.web_question_stale_days stays open and answerable and no longer counts toward max_open_web_questions."""
import pytest

from neuroscifi.instance import executive, store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import knowledge, state
from test_consolidate import DAY, T0, open_db


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def ask_n(db, n, now, prefix="web:knowledge:"):
    with store.write(db):
        return [knowledge.open_question(db, f"{prefix}{900 + i}", f"Is statement {i} right?", now) for i in range(n)]


def test_ten_ignored_questions_stop_new_ones_only_until_they_are_stale(db):
    cfg = state.get_config(db)
    assert cfg.max_open_web_questions == 10 and cfg.web_question_stale_days == 30.0
    ask_n(db, 10, T0)
    month = 30 * DAY
    for now, room in ((T0 + 1, False), (T0 + month - 1, False), (T0 + month, False), (T0 + month + 1, True)):
        load = knowledge.web_question_load(db, now)
        assert load["room"] is room and knowledge.question_room(db, None, now) is room, now
        assert load["open"] == 10 and load["cap"] == 10 and load["stale_after_days"] == 30.0
        assert load["stale"] == (10 if room else 0) and load["counted"] == 10 - load["stale"]
    with store.write(db):
        assert knowledge.ask(db, "web:knowledge:1", "A new question?", T0 + month) is None        # budget spent
        new = knowledge.ask(db, "web:knowledge:1", "A new question?", T0 + month + 1)
    assert isinstance(new, int)
    assert knowledge.web_question_load(db, T0 + month + 2) == {
        "open": 11, "stale": 10, "counted": 1, "cap": 10, "stale_after_days": 30.0, "room": True}


def test_a_stale_question_stays_open_listed_and_answerable(db):
    ids = ask_n(db, 2, T0, prefix="web:contested:t:")
    now = T0 + 31 * DAY
    stale = knowledge.stale_web_questions(db, now)
    assert [q["id"] for q in stale] == ids and stale[0]["age_days"] == 31.0
    assert stale[0]["scope"] == "web:contested:t:900" and stale[0]["question"] == "Is statement 0 right?"
    assert knowledge.stale_web_questions(db, T0 + 29 * DAY) == []
    assert db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0] == 2     # still open
    done = executive.resolve(db, ids[0], "the first value holds", now)
    assert done["id"] == ids[0] and done["resolved_at"] == now
    assert [q["id"] for q in knowledge.stale_web_questions(db, now)] == ids[1:]
    assert knowledge.web_question_load(db, now)["open"] == 1


def test_stale_days_zero_is_the_old_rule_and_no_clock_counts_everything(db):
    ask_n(db, 10, T0)
    year = T0 + 365 * DAY
    assert knowledge.question_room(db, None, year) is True
    assert knowledge.question_room(db) is False                      # a caller that gives no time: all count
    state.set_config(db, {"web_question_stale_days": 0}, T0)
    assert knowledge.question_room(db, None, year) is False
    assert knowledge.web_question_load(db, year) == {"open": 10, "stale": 0, "counted": 10, "cap": 10,
                                                     "stale_after_days": 0, "room": False}
    assert knowledge.stale_web_questions(db, year) == []
    with store.write(db):
        assert knowledge.ask(db, "web:knowledge:1", "A new question?", year) is None
        # an open question of the same subject is updated whatever the budget says
        assert knowledge.ask(db, "web:knowledge:900", "More about statement 0", year) is not None


def test_an_epoch_question_is_never_a_web_question(db):
    with store.write(db):
        db.execute("INSERT INTO epochs(name,created_at) VALUES('e1',?)", (T0,))
        db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES('e1','ask','camera:c1','q',?)",
                   (T0,))
    assert knowledge.web_question_load(db, T0 + 400 * DAY)["open"] == 0
    assert knowledge.stale_web_questions(db, T0 + 400 * DAY) == []


@pytest.mark.parametrize("bad", [-1, 3651, "30", True, float("nan"), float("inf"), None, [30]])
def test_stale_days_setting_is_validated(db, bad):
    with pytest.raises(ValidationError):
        state.set_config(db, {"web_question_stale_days": bad}, T0)


def test_stale_days_setting_accepts_days_and_older_settings_take_the_default(db):
    assert state.set_config(db, {"web_question_stale_days": 7}, T0).web_question_stale_days == 7.0
    assert state.set_config(db, {"web_question_stale_days": 0.5}, T0).web_question_stale_days == 0.5
    from neuroscifi.webcortex.config import WebConfig
    assert WebConfig.from_json('{"max_open_web_questions": 4}').web_question_stale_days == 30.0


def test_the_per_task_question_cap_is_unchanged_by_staleness(db):
    from test_consolidate import claim, cluster, consolidated
    ask_n(db, 10, T0 - 40 * DAY)                                    # ten stale questions: they block nothing now
    texts = [f"The Zephyr compiler release {n} targets {40 + n} platforms in total." for n in range(6)]
    clusters = [cluster([claim(f"http://{h}/z{n}", t if i == 0 else f"As the changelog read by reviewer {i} has it, {t}",
                               key=f"zephyr compiler release platforms r{n}") for i, h in enumerate(("a.example",
                                                                                                    "b.example"))])
                for n, t in enumerate(texts)]
    _, out = consolidated(db, clusters, objective="How many platforms does each Zephyr compiler release target?")
    cap = state.get_config(db).max_web_questions_per_task
    assert len(out["provisional"]) == 6
    own = db.execute("SELECT count(*) FROM pending WHERE scope LIKE 'web:knowledge:%' AND created_at=?",
                     (T0,)).fetchone()[0]
    assert own == cap and out["questions_deferred"] == 6 - cap and out["review_question"] is not None
