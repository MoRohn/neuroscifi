"""Inference tests run offline against fake_openai.FakeOpenAI on 127.0.0.1; none contacts a real model."""
import pytest

from fake_openai import FakeOpenAI, make_gateway


@pytest.fixture
def fake():
    server = FakeOpenAI().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "api_key"
    path.write_text("test-key\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def gateway(fake, key_file, tmp_path):
    return make_gateway(fake, key_file, tmp_path)
