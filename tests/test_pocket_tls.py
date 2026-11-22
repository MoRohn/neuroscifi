"""`nsf pocket tls`: the private CA, the issued chain and a real TLS handshake against the Pocket server."""
from datetime import datetime, timedelta, timezone
from http.client import HTTPSConnection
import json
import socket
import ssl
import threading
import time

import pytest

pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402

from neuroscifi import cli  # noqa: E402
from neuroscifi.pocket import tls  # noqa: E402
from test_mobile import bridge  # noqa: E402

NAMES = ["spark", "spark.local", "localhost", "127.0.0.1"]


@pytest.fixture
def issued(tmp_path):
    ca_dir, out = tmp_path / "ca", tmp_path / "tls"
    return tls.init(out, NAMES, ca_dir=ca_dir), ca_dir, out


def test_init_keeps_the_ca_key_private_and_issues_a_chain_for_every_name(issued):
    result, ca_dir, out = issued
    assert result["ca_created"] and result["names"] == NAMES
    assert (ca_dir / "ca.key").stat().st_mode & 0o777 == 0o600 and ca_dir.stat().st_mode & 0o777 == 0o700
    assert (out / "privkey.pem").stat().st_mode & 0o777 == 0o600 and not (out / "ca.key").exists()
    chain = x509.load_pem_x509_certificates((out / "fullchain.pem").read_bytes())
    ca = x509.load_pem_x509_certificate((ca_dir / "ca.pem").read_bytes())
    assert len(chain) == 2 and chain[1] == ca
    for name in NAMES:
        assert tls.verify(chain[0], ca, name) is True
    assert tls.verify(chain[0], ca, "other.example").startswith("no:")
    assert (chain[0].not_valid_after_utc - chain[0].not_valid_before_utc) <= timedelta(days=398)
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical and {s.value for s in constraints.value.permitted_subtrees
                                     if isinstance(s, x509.DNSName)} == {"spark", "spark.local", "localhost"}


def test_reissue_reuses_the_ca_and_refuses_names_outside_it(issued):
    result, ca_dir, out = issued
    again = tls.init(out, ["spark.local"], ca_dir=ca_dir)
    assert not again["ca_created"] and again["ca_fingerprints"] == result["ca_fingerprints"]
    with pytest.raises(tls.TLSError, match="limited to"):
        tls.init(out, ["elsewhere.example"], ca_dir=ca_dir)
    fresh = tls.init(out, ["elsewhere.example"], ca_dir=ca_dir, new_ca=True)
    assert fresh["ca_created"] and fresh["ca_fingerprints"] != result["ca_fingerprints"]


def test_the_ca_key_is_refused_inside_git_and_next_to_the_service_files(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(tls.TLSError, match="git working tree"):
        tls.init(tmp_path / "out", NAMES, ca_dir=tmp_path / "repo" / "ca")
    with pytest.raises(tls.TLSError, match="must differ"):
        tls.init(tmp_path / "same", NAMES, ca_dir=tmp_path / "same")
    with pytest.raises(tls.TLSError, match="host name"):
        tls.init(tmp_path / "out", ["127.0.0.1"], ca_dir=tmp_path / "ca")
    with pytest.raises(tls.TLSError, match="825"):
        tls.init(tmp_path / "out", NAMES, ca_dir=tmp_path / "ca", days=900)


def test_a_world_readable_ca_key_is_refused(issued):
    _, ca_dir, out = issued
    (ca_dir / "ca.key").chmod(0o644)
    with pytest.raises(tls.TLSError, match="chmod 600"):
        tls.init(out, ["spark"], ca_dir=ca_dir)


def test_export_and_status_show_fingerprints_and_never_a_key(issued, tmp_path, capsys):
    result, ca_dir, out = issued
    der = tmp_path / "neuroscifi-ca.crt"
    assert cli.main(["pocket", "tls", "export-ca", "--ca-dir", str(ca_dir), "--file", str(der), "--der"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["fingerprints"] == result["ca_fingerprints"] and b"PRIVATE" not in der.read_bytes()
    assert x509.load_der_x509_certificate(der.read_bytes()).subject.rfc4514_string() == exported["subject"]
    assert cli.main(["pocket", "tls", "status", "--ca-dir", str(ca_dir), "--out", str(out)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["server"]["verifies_against_ca"] is True and status["ca"]["key_mode"] == "0o600"
    assert status["server"]["names"] == NAMES and status["server"]["chain_length"] == 2
    later = tls.status(out, ca_dir=ca_dir, now=datetime.now(timezone.utc) + timedelta(days=400))
    assert later["server"]["verifies_against_ca"].startswith("no:")     # expired: the phone would refuse it too


def test_pocket_serves_the_issued_chain_and_a_client_trusting_only_the_ca_accepts_it(issued, tmp_path):
    _, ca_dir, out = issued
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(out / "fullchain.pem", out / "privkey.pem")
    server = bridge.PocketServer(("127.0.0.1", 0), bridge.Handler, tls=ctx)
    server.store = bridge.Store(tmp_path / "db")
    server.invitation, server.pair_lock = "test", threading.Lock()
    server.invitation_deadline = time.monotonic() + 30
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        trusting = ssl.create_default_context(cafile=str(ca_dir / "ca.pem"))
        conn = HTTPSConnection("localhost", server.server_port, context=trusting, timeout=5)
        conn.connect()
        assert conn.sock.getpeercert()["subject"] == ((("commonName", "spark"),),)
        conn.request("POST", "/api/pair", json.dumps({"code": "test"}), {"Content-Type": "application/json"})
        response = conn.getresponse()
        assert response.status == 201 and response.getheader("Strict-Transport-Security")
        conn.close()
        bare = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)          # verifies, but does not trust the private CA
        bare.load_default_certs()
        with pytest.raises(ssl.SSLCertVerificationError):
            HTTPSConnection("localhost", server.server_port, context=bare, timeout=5).request("GET", "/")
        with pytest.raises(ssl.SSLCertVerificationError, match="mismatch|match"):
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=5) as raw:
                trusting.wrap_socket(raw, server_hostname="elsewhere.example").close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_serve_hint_is_the_whole_command(tmp_path):
    """R6 setup (c): the printed hint left out --port, --db and --instance-db."""
    out = tls.init(tmp_path / "tls", ["spark.local", "192.168.1.20"], ca_dir=tmp_path / "ca")
    serve = out["serve"]
    for part in ("--bind 192.168.1.20", "--port 8765", f"--cert {tmp_path / 'tls' / 'fullchain.pem'}",
                 f"--key {tmp_path / 'tls' / 'privkey.pem'}", "--db ", "--instance-db ", "[--push-key "):
        assert part in serve, (part, serve)
    assert "optional" in out["serve_notes"] and "warnings" not in out      # a LAN address is on the certificate


def test_names_without_a_lan_address_get_a_warning_with_the_machines_addresses(tmp_path, monkeypatch, capsys):
    """R6 setup (d): the CA is limited to the names given at creation; adding the LAN IP later means --new-ca and
    reinstalling it on every device. Without a LAN address among the names, init says so and suggests them."""
    monkeypatch.setattr(tls, "lan_addresses", lambda: ["192.168.1.20", "fd00::5"])
    assert cli.main(["pocket", "tls", "init", "--out", str(tmp_path / "tls"), "--ca-dir", str(tmp_path / "ca"),
                     "--name", "spark.local"]) == 0
    captured = capsys.readouterr()
    (warning,) = json.loads(captured.out)["warnings"]
    assert "limited to spark.local" in warning and "--new-ca" in warning
    assert "This machine's LAN addresses: 192.168.1.20, fd00::5." in warning
    assert "nsf pocket tls init --out OUT --name spark.local --name 192.168.1.20 --name fd00::5." in warning
    assert captured.err.startswith("nsf pocket tls: warning: No LAN address is among the names")
    again = tls.init(tmp_path / "tls2", ["spark.local"], ca_dir=tmp_path / "ca")       # the CA exists now
    assert again["warnings"][0].endswith(" --new-ca.")


def test_lan_addresses_leave_out_loopback_and_container_bridges(monkeypatch):
    import subprocess
    listing = ("2: lo    inet 127.0.0.1/8 scope host lo\n"
               "5: wlP9s9    inet 192.168.68.60/22 brd 192.168.71.255 scope global dynamic wlP9s9\n"
               "5: wlP9s9    inet6 fdd3::f228/64 scope global dynamic\n"
               "9: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
               "10: br-eb56be2bdc75    inet 172.18.0.1/16 scope global br-eb56be2bdc75\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, listing, ""))
    assert tls.lan_addresses() == ["192.168.68.60", "fdd3::f228"]
