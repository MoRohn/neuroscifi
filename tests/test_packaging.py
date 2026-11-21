"""Installed-artefact checks (T3-01, T3-02): every served browser asset is declared as package data, and a listed
asset missing on disk is a 404 rather than a dropped connection. `nsf --version` prints the package version."""
import fnmatch
import http.client
from pathlib import Path
import threading
import tomllib

import pytest

import neuroscifi
from neuroscifi.cli import main
from neuroscifi.console import server as console_server
from neuroscifi.instance import store
from neuroscifi.pocket import server as pocket_server

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "neuroscifi"
T0 = 1_800_000_000.0


def test_package_data_covers_every_asset_directory_on_disk():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    for web in sorted(SRC.glob("*/web")):
        patterns = data.get(f"neuroscifi.{web.parent.name}", [])
        for f in sorted(p for p in web.iterdir() if p.is_file()):
            rel = f.relative_to(web.parent).as_posix()
            assert any(fnmatch.fnmatch(rel, pat) for pat in patterns), f"{rel} is not in package-data"
    for f in sorted((SRC / "webcortex" / "bench").glob("*.json")):
        assert any(fnmatch.fnmatch("bench/" + f.name, p) for p in data["neuroscifi.webcortex"])


def test_every_served_asset_exists_on_disk():
    for module in (console_server, pocket_server):
        for url, (name, _) in module.ASSETS.items():
            assert (module.WEB / name).is_file(), f"{url} -> {name}"


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        return conn.getresponse().status
    finally:
        conn.close()


def test_console_missing_asset_file_is_404(tmp_path, monkeypatch):
    path = tmp_path / "individual.sqlite"
    store.create(path, T0).close()
    monkeypatch.setitem(console_server.ASSETS, "/gone.css", ("does-not-exist.css", "text/css"))
    srv = console_server.ConsoleServer(path, 0, token="t" * 40)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _get(srv.server_port, "/gone.css") == 404
        assert _get(srv.server_port, "/") == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_pocket_missing_asset_file_is_404(tmp_path, monkeypatch):
    monkeypatch.setitem(pocket_server.ASSETS, "/gone.css", ("does-not-exist.css", "text/css"))
    srv = pocket_server.PocketServer(("127.0.0.1", 0), pocket_server.Handler)
    srv.store = pocket_server.Store(tmp_path / "db")
    srv.invitation, srv.pair_lock = "test", threading.Lock()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _get(srv.server_port, "/gone.css") == 404
        assert _get(srv.server_port, "/style.css") == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_nsf_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"nsf {neuroscifi.__version__}"
