"""The guarded child (`artifacts.guarded`, ruling A of fix lane F1) costs about 0.1 s per decode. The tests in this
folder that are about adapters, the store, the planner or the pipeline run without it; `test_guarded.py` and every
test that asks for the `guard_on` fixture run the real child. Tests outside this folder always run it."""
import pytest

from neuroscifi.artifacts import guarded


@pytest.fixture(autouse=True)
def _guard_off(monkeypatch):
    monkeypatch.setattr(guarded, "ENABLED", False)
    monkeypatch.setattr(guarded, "_VERDICTS", {})      # each test sees the child run (fix lane F4 reuses verdicts)


@pytest.fixture
def guard_on(monkeypatch):
    monkeypatch.setattr(guarded, "ENABLED", True)
