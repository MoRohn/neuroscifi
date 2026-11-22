#!/usr/bin/env python3
"""Open Pocket over HTTPS in Playwright's Chromium and report whether the browser trusts the certificate chain.

    scripts/tls_browser_check.py --url https://HOST:PORT/ --ca CA.pem [--resolve HOST=127.0.0.1] [--mode nss|spki|none]

Never touches the user's browser profiles or the system trust store. Each mode runs Chromium with HOME set to a
fresh temporary folder (so its NSS database ~/.pki/nssdb and its profile are scratch) and ignore_https_errors off:
  nss   the CA is imported into that scratch NSS database (certutil -A -t C,,): Chromium's own verifier builds
        and checks the chain, names and name constraints, as a phone that installed the CA does
  spki  no CA is installed; Chromium gets --ignore-certificate-errors-spki-list with the CA's SPKI hash
  none  nothing is trusted: the page must fail with a certificate error (the negative control)
Prints one JSON object per mode; exit status 1 when a mode does not behave as stated.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def spki_hash(ca: Path) -> str:
    der = subprocess.run(["openssl", "x509", "-in", str(ca), "-pubkey", "-noout"], capture_output=True, check=True,
                         text=True).stdout
    key = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=der.encode(), capture_output=True,
                         check=True).stdout
    return base64.b64encode(hashlib.sha256(key).digest()).decode()


def run(url: str, ca: Path, mode: str, resolve: list[str]) -> dict:
    from playwright.sync_api import Error, sync_playwright
    home = Path(tempfile.mkdtemp(prefix="nsf-tls-chromium-"))
    try:
        args = [f"--host-resolver-rules={', '.join('MAP ' + r.replace('=', ' ') for r in resolve)}"] if resolve \
            else []
        if mode == "nss":
            db = home / ".pki" / "nssdb"
            db.mkdir(parents=True)
            subprocess.run(["certutil", "-N", "-d", f"sql:{db}", "--empty-password"], check=True)
            subprocess.run(["certutil", "-A", "-d", f"sql:{db}", "-n", "NeuroSciFi Pocket CA", "-t", "C,,",
                            "-i", str(ca)], check=True)
        elif mode == "spki":
            args.append(f"--ignore-certificate-errors-spki-list={spki_hash(ca)}")
        out = {"mode": mode, "url": url}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chromium", args=args, env={"HOME": str(home), "PATH": "/usr/bin"})
            try:
                page = browser.new_context(ignore_https_errors=False).new_page()
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    out |= {"loaded": True, "status": response.status if response else None, "title": page.title(),
                            "security": response.security_details() if response else None}
                except Error as exc:
                    out |= {"loaded": False, "error": str(exc).splitlines()[0][:200]}
                out["chromium"] = browser.version
            finally:
                browser.close()
        return out
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True)
    p.add_argument("--ca", type=Path, required=True)
    p.add_argument("--resolve", action="append", default=[], metavar="HOST=ADDRESS")
    p.add_argument("--mode", action="append", choices=("nss", "spki", "none"))
    a = p.parse_args(argv)
    ok = True
    for mode in a.mode or ("nss", "spki", "none"):
        result = run(a.url, a.ca, mode, a.resolve)
        expected = mode != "none"
        result["as_expected"] = result["loaded"] == expected and (not expected or result.get("status") == 200)
        ok &= result["as_expected"]
        print(json.dumps(result, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
