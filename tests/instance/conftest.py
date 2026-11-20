"""Shared fixtures for instance tests. Owned by the architect; workstreams add their own helpers locally."""
import pytest

from neuroscifi.instance import store

T0 = 1_800_000_000.0  # fixed clock origin for deterministic tests


@pytest.fixture
def instance(tmp_path):
    """A fresh individual. Yields (db, path); the connection is closed afterwards."""
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        yield db, path
    finally:
        db.close()
