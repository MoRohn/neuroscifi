"""Console Research view with model reasoning, in a real Chromium under the real CSP (Lane G #6). The console's
embedded worker runs research offline (FixtureWeb + CorpusProvider) and its configured model endpoint is a fake
OpenAI-compatible server on 127.0.0.1 that answers deterministically from the evidence it is given. Covered: the
model answer block with its label and generated_by, a self-check flag that did not remove the sentence, the verdict
state + basis + category (ADR-035), and the degraded label when the model is down."""
import json
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from neuroscifi.inference import config as iconfig
from neuroscifi.instance import store
from test_console_research import NOW, QUESTION, Research, ask, enable_web, finish, go

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "inference"))
from fake_openai import FakeOpenAI  # noqa: E402

LABEL_DEGRADED = "Model-written summary unavailable — showing the extractive summary quoted from the sources"


def evidence_of(body):
    text = body["messages"][-1]["content"]
    return json.loads(text.split("<evidence>", 1)[1].split("</evidence>", 1)[0])


def reply(body):
    """Answer: the first supported claim restated (its sources are the claim's own, reasoning.compose). Critique: flag
    that sentence (the guard already passed it, so the flag must not remove it)."""
    if body["messages"][0]["content"].startswith("You check an answer"):
        return {"content": json.dumps({"issues": [{"sentence": 1, "problem": "overstated", "note": "test flag"}],
                                       "gaps": ["an independent benchmark"]})}
    ev = evidence_of(body)
    claim = next(c for c in ev["claims"] if c["state"] in ("corroborated", "supported"))
    return {"content": json.dumps({"sentences": [{"claims": [claim["id"]], "text": claim["text"]}], "gaps": []})}


class Replies(list):
    """FakeOpenAI pops a reply per request after recording it: compute it from that request."""

    def __init__(self, fake):
        super().__init__()
        self.fake = fake

    def __bool__(self):
        return True

    def pop(self, index=-1):
        return reply(self.fake.requests[-1])


@pytest.fixture
def model():
    fake = FakeOpenAI(models=("fake-model",))
    fake.replies = Replies(fake)
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def research(tmp_path, model):
    key = tmp_path / "api_key"
    key.write_text("test-key\n")
    key.chmod(0o600)
    r = Research(tmp_path)
    db = store.open_existing(r.path)
    try:
        # critique_restatements: the fake model restates the claim word for word, which the critique no longer reads
        # by default (reasoning.restates); the flag test below needs it judged, as 0.7.0 did
        iconfig.set_config(db, {"base_url": model.url, "key_file": str(key), "model": "fake-model",
                                "max_retries": 0, "read_timeout": 10.0, "critique_restatements": True}, NOW)
    finally:
        db.close()
    yield r
    r.close()


def run(page, research):
    page.goto(research.url)
    go(page, "research", "Research")
    enable_web(page)
    ask(page, QUESTION, "fact_verification")
    finish(page)
    return page.locator("#report")


def test_model_answer_label_flag_and_verdict_fields(research, model, new_page):
    page = new_page(1280, 900)
    report = run(page, research)
    label = report.locator("#reasoningLabel")
    expect(label).to_have_attribute("data-generated-by", "model:fake-model")
    expect(label).to_contain_text("Model-written summary")
    expect(label).to_contain_text("Written by fake-model from the collected evidence")
    expect(label).to_contain_text("single-source statements are marked")
    expect(label).not_to_contain_text("verified")   # T4-02: the guard checks citations, it does not verify claims
    answer = report.locator("#modelAnswer li")
    expect(answer).to_have_count(1)
    expect(answer.first.locator("p.generated")).to_contain_text("4.2.0")
    claim = next(c for c in evidence_of(model.requests[0])["claims"] if c["state"] in ("corroborated", "supported"))
    # the model writes no source ids: the sentence cites every source carrying its claim (reasoning.compose)
    expect(answer.first.locator(".answermeta a.cite")).to_have_count(len(claim["sources"]))
    expect(answer.first).to_contain_text("self-check flagged: overstated (not confirmed by the checks)")
    expect(report.locator("#modelGaps")).to_contain_text("an independent benchmark")
    expect(report.locator("h4", has_text="Extractive summary (quoted evidence)")).to_be_visible()
    # ADR-035 fields of the verdict, from the report itself
    basis = report.locator("#verdictBasis")
    expect(basis).to_contain_text(re.compile(r"State: \w"))
    expect(basis).to_contain_text("basis:")
    expect(basis).to_contain_text("category:")
    state = basis.get_attribute("data-state")
    assert state in ("supported", "disputed", "conflicting", "insufficient", "temporally_unresolved", "not_evaluated")
    expect(report.locator("#verdictText")).to_be_visible()
    # the model saw the evidence as untrusted data; both calls were made against the fake endpoint only
    assert len(model.requests) == 2 and '"untrusted": true' in model.requests[0]["messages"][1]["content"]
    assert page.evaluate("window.__pwned") is None
    assert not page.problems, page.problems


def test_a_word_for_word_answer_is_shown_without_a_critique_call_by_default(research, model, new_page):
    """The default: a sentence that is its cited claim word for word is not sent to the critic, so the page shows it
    with no self-check flag, and one model call was made."""
    db = store.open_existing(research.path)
    try:
        iconfig.set_config(db, {"critique_restatements": False}, NOW)
    finally:
        db.close()
    page = new_page(1280, 900)
    report = run(page, research)
    expect(report.locator("#reasoningLabel")).to_have_attribute("data-generated-by", "model:fake-model")
    answer = report.locator("#modelAnswer li")
    expect(answer).to_have_count(1)
    expect(answer.first.locator("p.generated")).to_contain_text("4.2.0")
    expect(answer.first).not_to_contain_text("self-check flagged")
    assert len(model.requests) == 1
    assert not page.problems, page.problems


def test_model_down_shows_the_degraded_label(research, model, new_page):
    model.stop()
    page = new_page(1280, 900)
    report = run(page, research)
    label = report.locator("#reasoningLabel")
    expect(label).to_have_attribute("data-generated-by", "template")
    expect(label).to_contain_text("Extractive summary (template)")
    expect(label).to_contain_text(LABEL_DEGRADED)
    expect(report.locator("#modelAnswer")).to_have_count(0)
    expect(report.locator("blockquote.quote").first).to_contain_text("4.2.0")   # the extractive answer stands
    expect(report.locator("#verdictBasis")).to_contain_text("State:")
    assert not page.problems, page.problems
