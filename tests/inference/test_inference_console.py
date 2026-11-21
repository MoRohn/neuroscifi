"""Console: GET /api/llm/status keeps the console's token and Host checks and reports explicit health states; the
CLI `nsf llm` reports the same states."""
import http.client
import json
import threading

import pytest

from neuroscifi.cli import main
from neuroscifi.console import server
from neuroscifi.inference import config as iconfig
from neuroscifi.instance import store

T0 = 1_795_200_000.0
TOKEN = "test-token-" + "y" * 32


@pytest.fixture
def console(tmp_path):
    path = tmp_path / "individual.sqlite"
    store.create(path, T0).close()
    srv = server.ConsoleServer(path, 0, token=TOKEN, clock=lambda: T0 + 10)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, path
    srv.shutdown()
    srv.server_close()


def get(srv, route, token=TOKEN, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=30)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if host:
        headers["Host"] = host
    try:
        conn.request("GET", route, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"null")
    finally:
        conn.close()


def test_llm_status_requires_the_token_and_loopback_host(console):
    srv, _ = console
    status, body = get(srv, "/api/llm/status", token=None)
    assert status in (401, 403) and "health" not in (body or {})
    assert get(srv, "/api/llm/status", host="evil.example")[0] in (400, 403, 421)


def test_llm_status_states(console, fake, key_file):
    srv, path = console
    status, body = get(srv, "/api/llm/status")
    assert status == 200 and body["health"]["state"] == "not_configured" and body["usage_24h"] is None
    db = store.open_existing(path)
    try:
        iconfig.set_config(db, {"base_url": fake.url, "key_file": str(key_file), "model": "fake-model"}, T0)
    finally:
        db.close()
    fake.replies.append({"content": "ready"})
    status, body = get(srv, "/api/llm/status?probe=1")
    assert status == 200 and body["health"]["state"] == "ready" and body["health"]["probe"]["reply"] == "ready"
    assert body["config"]["key_present"] is True and "test-key" not in json.dumps(body)
    assert body["usage_24h"]["calls"] == 1
    assert get(srv, "/api/llm/status?verbose=1")[0] == 400
    fake.stop()
    assert get(srv, "/api/llm/status")[1]["health"]["state"] == "unavailable"


def test_cli_llm_status_and_ask(tmp_path, fake, key_file, capsys):
    db_path = tmp_path / "cli.sqlite"
    store.create(db_path, T0).close()
    assert main(["llm", "status", "--db", str(db_path)]) == 0
    assert json.loads(capsys.readouterr().out)["health"]["state"] == "not_configured"
    assert main(["llm", "config", "--db", str(db_path), "--url", fake.url, "--key-file", str(key_file)]) == 0
    capsys.readouterr()
    fake.replies.append({"content": "Hello from the fake model."})
    assert main(["llm", "ask", "--db", str(db_path), "--stream", "hi"]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["generated_by"] == "model:fake-model" and "not checked against evidence" in out["label"]
    assert "Hello from the fake model." in captured.err
    assert main(["llm", "usage", "--db", str(db_path)]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["calls"] == 1
    assert main(["llm", "config", "--db", str(db_path), "--set", "max_retries=9"]) == 2
