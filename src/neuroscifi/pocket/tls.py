"""`nsf pocket tls`: a private certificate authority for Pocket, so phones get a trusted TLS chain.

init     creates (or reuses) a local CA and issues a server certificate for the host names and addresses the phone
         uses. The CA key stays in --ca-dir (0700 folder, 0600 key; refused inside a git working tree); --out gets
         only what the service reads: fullchain.pem (server, then CA), privkey.pem (0600) and ca.pem.
export-ca writes the CA certificate (never a key) as PEM or DER for installing on a phone, with its fingerprints.
status   shows the CA and server certificates: names, validity, fingerprints, whether the chain verifies.

The CA is limited to the names it was created for (critical X.509 name constraints), so its key cannot mint a
certificate any client would accept for another site. Keys are ECDSA P-256; the server certificate lasts 397 days
(within the 825-day limit iOS applies to every TLS server certificate) and carries serverAuth and the names as
subjectAltName, which is all Chrome, Android and iOS check (they ignore the common name).
Needs the `crypto` extra (cryptography); docs/operations/TLS.md has the phone steps.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys

from .. import paths

DEFAULT_CA_DIR =Path(os.environ.get("NSF_POCKET_CA_DIR") or paths.home() / ".config" / "neuroscifi" / "pocket-ca")
SERVER_DAYS = 397
CA_DAYS = 3650


class TLSError(Exception):
    pass


def _crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError as exc:
        raise TLSError("`nsf pocket tls` needs the cryptography package: pip install 'neuroscifi[crypto]'") from exc
    return x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID


def _write(path: Path, data: bytes, mode: int) -> None:
    """Write atomically with `mode` from the first byte (no window where a key is readable by others)."""
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def inside_git(path: Path) -> Path | None:
    """The git working tree containing `path`, or None."""
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / ".git").exists():
            return parent
    return None


def _split(names) -> tuple[list[str], list]:
    dns, ips = [], []
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            ips.append(ipaddress.ip_address(name))
        except ValueError:
            if any(c in name for c in "/:@ ") or len(name) > 253:
                raise TLSError(f"not a host name or IP address: {name!r}") from None
            dns.append(name.lower().rstrip("."))
    return list(dict.fromkeys(dns)), list(dict.fromkeys(ips))


_VIRTUAL = ("docker", "br-", "veth", "virbr", "cni", "flannel", "podman", "lxc", "vmnet")


def lan_addresses() -> list[str]:
    """This machine's LAN addresses, best effort, for a suggestion only: IPv4 first, from `ip -o addr show scope
    global` (container and virtual bridges left out), else the address a UDP socket would leave by (no packet is
    sent)."""
    import subprocess
    found = []
    try:
        lines = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True,
                               timeout=2).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        lines = []
    for line in lines:
        parts = line.split()
        if len(parts) > 3 and parts[2] in ("inet", "inet6") and not parts[1].startswith(_VIRTUAL):
            found.append((parts[2] == "inet6", parts[3].split("/")[0]))
    if not found:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))          # TEST-NET-1: routing only, nothing is sent
                found.append((False, probe.getsockname()[0]))
        except OSError:
            pass
    return list(dict.fromkeys(addr for _, addr in sorted(found, key=lambda x: x[0])
                              if not ipaddress.ip_address(addr).is_loopback))


def _lan_warning(dns, ips, created) -> str | None:
    """R6 setup (d): the CA is limited to the names it was created for; without a LAN address among them a phone on
    the LAN can only use a host name it resolves. Say so, and how to add the address."""
    if any(not ip.is_loopback for ip in ips):
        return None
    lan = lan_addresses()
    names = dns + [str(ip) for ip in ips]
    add = " ".join(f"--name {n}" for n in names) + " " + (" ".join(f"--name {a}" for a in lan[:2]) or "--name LAN_IP")
    return (f"No LAN address is among the names: this CA and certificate are limited to {', '.join(names)}, so a phone "
            "must reach the Spark by one of those names. To add the LAN address later the CA must be replaced "
            "(--new-ca) and reinstalled on every device. "
            + (f"This machine's LAN addresses: {', '.join(lan)}. " if lan else "")
            + f"To include it now: nsf pocket tls init --out OUT {add.strip()}"
            + ("" if created else " --new-ca") + ".")


def default_names() -> list[str]:
    host = socket.gethostname()
    return [host, f"{host}.local", "localhost", "127.0.0.1", "::1"]


def fingerprints(cert) -> dict:
    """SHA-256 of the certificate (what a phone shows) and of its public key (Chrome's SPKI pin)."""
    _, hashes, serialization, *_ = _crypto()
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    sha = cert.fingerprint(hashes.SHA256()).hex().upper()
    return {"sha256": ":".join(sha[i:i + 2] for i in range(0, len(sha), 2)),
            "spki_sha256_base64": base64.b64encode(hashlib.sha256(spki).digest()).decode()}


def _load_ca(ca_dir: Path):
    x509, _, serialization, *_ = _crypto()
    key_path, cert_path = ca_dir / "ca.key", ca_dir / "ca.pem"
    if key_path.stat().st_mode & 0o077:
        raise TLSError(f"{key_path} is readable by other users; chmod 600 it")
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    return key, x509.load_pem_x509_certificate(cert_path.read_bytes())


def _new_ca(dns, ips, now, days, label):
    x509, hashes, serialization, ec, _, NameOID = _crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NeuroSciFi"),
                      x509.NameAttribute(NameOID.COMMON_NAME, f"NeuroSciFi Pocket CA ({label})"[:64])])
    permitted = [x509.DNSName(d) for d in dns] + [
        x509.IPAddress(ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}")) for ip in ips]
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None),
                           critical=True)
            .add_extension(ski, critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def _constraints(ca_cert) -> tuple[set, set]:
    x509, *_ = _crypto()
    try:
        nc = ca_cert.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return set(), set()
    subtrees = nc.permitted_subtrees or []
    return ({s.value for s in subtrees if isinstance(s, x509.DNSName)},
            {s.value.network_address for s in subtrees if isinstance(s, x509.IPAddress)})


def _issue(ca_key, ca_cert, dns, ips, now, days):
    x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID = _crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    sans = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ip) for ip in ips]
    cn = (dns or [str(ip) for ip in ips])[0]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn[:64])]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return key, cert


def init(out: Path, names, *, ca_dir: Path = DEFAULT_CA_DIR, days=SERVER_DAYS, ca_days=CA_DAYS, new_ca=False,
         now=None) -> dict:
    """Create the CA when missing (or with new_ca), then issue fullchain.pem / privkey.pem / ca.pem into `out`."""
    x509, hashes, serialization, *_ = _crypto()
    now = now or datetime.now(timezone.utc)
    dns, ips = _split(names or default_names())
    if not dns:
        raise TLSError("at least one host name is needed (iOS and Android match the name the phone types)")
    if not 1 <= days <= 825:
        raise TLSError("--days must be between 1 and 825 (iOS refuses longer server certificates)")
    ca_dir, out = Path(ca_dir), Path(out)
    tree = inside_git(ca_dir)
    if tree is not None:
        raise TLSError(f"the CA directory {ca_dir} is inside the git working tree {tree}; keep the CA key out of git")
    if out.resolve() == ca_dir.resolve():
        raise TLSError("--out must differ from --ca-dir: the service reads --out and must never read the CA key")
    _private_dir(ca_dir)
    created = new_ca or not (ca_dir / "ca.key").exists()
    if created:
        ca_key, ca_cert = _new_ca(dns, ips, now, ca_days, socket.gethostname())
        _write(ca_dir / "ca.key", ca_key.private_bytes(serialization.Encoding.PEM,
                                                       serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()), 0o600)
        _write(ca_dir / "ca.pem", ca_cert.public_bytes(serialization.Encoding.PEM), 0o644)
    else:
        ca_key, ca_cert = _load_ca(ca_dir)
        allowed_dns, allowed_ips = _constraints(ca_cert)
        outside = sorted(set(dns) - allowed_dns) + sorted(str(ip) for ip in set(ips) - allowed_ips)
        if outside:
            raise TLSError(f"the existing CA is limited to {sorted(allowed_dns) + sorted(map(str, allowed_ips))}; "
                           f"{', '.join(outside)} would be refused by every client. Use --new-ca (phones must then "
                           "install the new CA)")
    warning = _lan_warning(dns, ips, created)
    key, cert = _issue(ca_key, ca_cert, dns, ips, now, days)
    _private_dir(out)
    _write(out / "privkey.pem", key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()), 0o600)
    pem = serialization.Encoding.PEM
    _write(out / "fullchain.pem", cert.public_bytes(pem) + ca_cert.public_bytes(pem), 0o644)
    _write(out / "ca.pem", ca_cert.public_bytes(pem), 0o644)
    bind = next((str(ip) for ip in ips if not ip.is_loopback), "LAN_IP")
    # R6 setup (c): the whole command, with the optional push key in brackets
    serve = (f"nsf pocket serve --bind {bind} --port 8765 --cert {out / 'fullchain.pem'} --key {out / 'privkey.pem'} "
             "--db /path/on/encrypted-volume/mobile.sqlite --instance-db \"$NSF_DB\" "
             "[--push-key ~/.config/neuroscifi/pocket/vapid.pem]")
    report = {"ca_created": created, "ca_dir": str(ca_dir), "out": str(out),
              "files": {"fullchain": str(out / "fullchain.pem"), "privkey": str(out / "privkey.pem"),
                        "ca": str(out / "ca.pem")},
              "names": dns + [str(ip) for ip in ips], "server_not_after": cert.not_valid_after_utc.isoformat(),
              "ca_not_after": ca_cert.not_valid_after_utc.isoformat(), "ca_fingerprints": fingerprints(ca_cert),
              "serve": serve,
              "serve_notes": "--bind: an address on the certificate (phones open https://NAME:8765 for a NAME "
                             "above); --db: the Pocket database, on an encrypted volume; --instance-db: the "
                             "individual's database, for Research (with nsf web config --enable --pocket-research) "
                             "and node enrolment; --push-key (optional): Web Push, the VAPID key is created as a "
                             "0600 file on first start (needs --instance-db)"}
    if warning:
        report["warnings"] = [warning]
    return report


def export_ca(dest: Path | None, *, ca_dir: Path = DEFAULT_CA_DIR, der=False) -> dict:
    x509, _, serialization, *_ = _crypto()
    cert = x509.load_pem_x509_certificate((Path(ca_dir) / "ca.pem").read_bytes())
    data = cert.public_bytes(serialization.Encoding.DER if der else serialization.Encoding.PEM)
    out = {"subject": cert.subject.rfc4514_string(), "not_after": cert.not_valid_after_utc.isoformat(),
           "fingerprints": fingerprints(cert), "format": "der" if der else "pem"}
    if dest is not None:
        _write(Path(dest), data, 0o644)
        out["written"] = str(dest)
    else:
        out["pem"] = cert.public_bytes(serialization.Encoding.PEM).decode()
    return out


def status(out: Path | None, *, ca_dir: Path = DEFAULT_CA_DIR, now=None) -> dict:
    x509, *_ = _crypto()
    now = now or datetime.now(timezone.utc)
    report = {"ca_dir": str(ca_dir)}
    ca_cert = None
    ca_pem = Path(ca_dir) / "ca.pem"
    if ca_pem.is_file():
        ca_cert = x509.load_pem_x509_certificate(ca_pem.read_bytes())
        dns, ips = _constraints(ca_cert)
        key = Path(ca_dir) / "ca.key"
        report["ca"] = {"subject": ca_cert.subject.rfc4514_string(),
                        "not_after": ca_cert.not_valid_after_utc.isoformat(),
                        "limited_to": sorted(dns) + sorted(map(str, ips)), "fingerprints": fingerprints(ca_cert),
                        "key_mode": oct(key.stat().st_mode & 0o777) if key.exists() else None}
    if out is not None and (Path(out) / "fullchain.pem").is_file():
        chain = x509.load_pem_x509_certificates((Path(out) / "fullchain.pem").read_bytes())
        leaf = chain[0]
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(n) for n in san.get_values_for_type(x509.DNSName)] + \
                [str(n) for n in san.get_values_for_type(x509.IPAddress)]
        report["server"] = {"names": names, "not_after": leaf.not_valid_after_utc.isoformat(),
                            "expires_in_days": (leaf.not_valid_after_utc - now).days,
                            "fingerprints": fingerprints(leaf), "chain_length": len(chain)}
        if ca_cert is not None and names:
            report["server"]["verifies_against_ca"] = verify(leaf, ca_cert, names[0], now=now)
    return report


def verify(leaf, ca_cert, name, *, now=None) -> bool | str:
    """True when `leaf` chains to `ca_cert` for `name` under the web PKI rules (cryptography's verifier)."""
    x509, *_ = _crypto()
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
    try:
        subject = x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        subject = x509.DNSName(name)
    builder = PolicyBuilder().store(Store([ca_cert])).time(now or datetime.now(timezone.utc))
    try:
        builder.build_server_verifier(subject).verify(leaf, [])
    except VerificationError as exc:
        return f"no: {exc}"
    return True


def register(ops) -> None:
    tls = ops.add_parser("tls", help="private CA and server certificate for HTTPS on the LAN (needs [crypto])")
    tsub = tls.add_subparsers(dest="tls_command", required=True, metavar="TLS_ACTION")
    p = tsub.add_parser("init", help="create the CA (once) and issue a server certificate into --out")
    p.add_argument("--out", type=Path, required=True, help="folder for fullchain.pem, privkey.pem and ca.pem")
    p.add_argument("--name", action="append", metavar="HOST_OR_IP",
                   help="a host name or address phones use (repeatable); default: this host's name, NAME.local, "
                        "localhost, 127.0.0.1, ::1")
    p.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR, help=f"CA key folder (default {DEFAULT_CA_DIR})")
    p.add_argument("--days", type=int, default=SERVER_DAYS)
    p.add_argument("--new-ca", action="store_true", help="replace the CA (every phone must install the new one)")
    p.set_defaults(handler=_init)
    p = tsub.add_parser("export-ca", help="write the CA certificate for installing on a phone")
    p.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    p.add_argument("--file", type=Path, help="write here (default: print the PEM)")
    p.add_argument("--der", action="store_true", help="DER (.crt/.cer), what Android's installer expects")
    p.set_defaults(handler=_export)
    p = tsub.add_parser("status", help="show the CA and server certificates")
    p.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    p.add_argument("--out", type=Path, help="folder holding fullchain.pem")
    p.set_defaults(handler=_status)


def _run(fn):
    try:
        print(json.dumps(fn(), indent=2, sort_keys=True))
    except (TLSError, OSError) as exc:
        print(f"nsf pocket tls: {exc}", file=sys.stderr)
        return 2
    return 0


def _init(a):
    def run():
        out = init(a.out, a.name, ca_dir=a.ca_dir, days=a.days, new_ca=a.new_ca)
        for warning in out.get("warnings", ()):
            print(f"nsf pocket tls: warning: {warning}", file=sys.stderr)
        return out
    return _run(run)


def _export(a):
    return _run(lambda: export_ca(a.file, ca_dir=a.ca_dir, der=a.der))


def _status(a):
    return _run(lambda: status(a.out, ca_dir=a.ca_dir))
