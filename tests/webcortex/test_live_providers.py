"""Live end-to-end check of the keyed general provider (Brave Search API, ADR-034 amendment 1) through `nsf web ask`
and `nsf web search`, on a temporary individual.

Runs only with NSF_LIVE=1 and a usable Brave key (`nsf web provider set-key brave --from-file PATH`):

    NSF_LIVE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/webcortex/test_live_providers.py

It spends at most about a dozen queries of the key's daily cap (quick_lookup budgets: 3 planned queries per question;
the cap and `nsf web provider status` count them like any other use). Nothing is written outside the temporary
individual: Brave's terms allow only transient storage of its results, so this check asserts and keeps no evidence
file.
"""
import json

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import store
from neuroscifi.webcortex import state
from neuroscifi.webcortex.providers import keys

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(keys.key_state("brave")["key"] != "configured",
                                 reason="no usable Brave key: nsf web provider set-key brave --from-file PATH")]

GENERAL = ("When was the Rust programming language first released?",
           "What is the boiling point of ethanol at sea level?")
NEWS_ASK = "What happened in space exploration news this week?"
NEWS_SEARCH = "space telescope news"


def nsf(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    assert keys.read_key("brave") not in captured.out + captured.err      # the key is never printed
    return code, (json.loads(captured.out) if code == 0 else captured.err)


@pytest.fixture
def db(tmp_path, capsys):
    path = str(tmp_path / "live.sqlite")
    assert nsf(capsys, "init", "--db", path)[0] == 0
    assert nsf(capsys, "web", "config", "--enable", "--db", path)[0] == 0
    code, out = nsf(capsys, "web", "provider", "status", "--db", path)
    assert code == 0 and out["providers"]["brave"]["key"] == "configured", out
    assert out["routing"]["web"][0] == "brave" and out["routing"]["news"][0] == "brave"
    if out["providers"]["brave"]["state"] == "rate_limited":
        pytest.skip(f"Brave is rate-limited or today's cap is used: {out['providers']['brave']}")
    return path


def _task(path, task_id):
    db = store.connect(path)
    try:
        return state.task(db, task_id)
    finally:
        db.close()


def _check_answered_through_brave(path, out):
    assert out["status"] in ("completed", "partial"), out.get("error")
    task = _task(path, out["id"])
    attempts = [a for a in task["checkpoint"].get("attempts") or [] if a.get("provider") == "brave"]
    assert any(a["ok"] and a["n"] > 0 for a in attempts), attempts
    assert not any((r.get("metadata") or {}).get("transient") for r in task["checkpoint"].get("results") or [])
    assert (task["result"] or {}).get("sources"), task["result"]
    return attempts


@pytest.mark.parametrize("question", GENERAL)
def test_general_questions_are_discovered_through_brave(capsys, db, question):
    code, out = nsf(capsys, "web", "ask", question, "--mode", "quick_lookup", "--db", db)
    assert code == 0, out
    _check_answered_through_brave(db, out)


def test_news_is_searched_on_the_brave_news_endpoint(capsys, db):
    code, out = nsf(capsys, "web", "search", NEWS_SEARCH, "--kind", "news", "--limit", "5", "--db", db)
    assert code == 0, out
    brave = [r for r in out["results"] if r["provider"] == "brave"]
    assert brave and all(r["engine"] == "news" for r in brave), out["attempts"]
    assert any(r["published_at"] for r in brave)                          # page_age dates the news results
    code, out = nsf(capsys, "web", "ask", NEWS_ASK, "--mode", "quick_lookup", "--db", db)
    assert code == 0, out
    _check_answered_through_brave(db, out)
    code, status = nsf(capsys, "web", "provider", "status", "--db", db)
    assert code == 0 and status["providers"]["brave"]["used_today"] >= 2
    assert status["providers"]["brave"]["since_ok"] is not None
