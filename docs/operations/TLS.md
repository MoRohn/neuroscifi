# A trusted TLS chain for Pocket

Pocket needs HTTPS on any address other than loopback ([ENVIRONMENT §4](ENVIRONMENT.md#4-tls-certificate-and-key)). A self-signed certificate fails in the phone's browser before Pocket is reached. `nsf pocket tls` instead creates a private certificate authority (CA). You install that CA once on each phone, and Pocket serves a certificate the CA issued.

## 1. Issue

```sh
pip install 'neuroscifi[crypto]'                 # cryptography==50.0.2
nsf pocket tls init --out /etc/neuroscifi/tls \
  --name spark --name spark.local --name 192.168.1.20      # every name and address the phone types
```

- **The CA** is an ECDSA P-256 key in `--ca-dir` (default `~/.config/neuroscifi/pocket-ca`; folder 0700, `ca.key` 0600) and `ca.pem`, valid 10 years.
  - `init` refuses a CA folder inside a git working tree, and an `--out` equal to the CA folder.
  - It refuses a CA key that other users can read.
  - Run it as the owner, not as the service user. The service never needs the CA key.
- **Name constraints.** The CA is limited (critical X.509 name constraints) to the names given at its creation. If its key leaked, it could not mint a certificate that any client accepts for another site.
  - Reissuing for a subset of those names reuses the CA.
  - A new name needs `--new-ca`, and every phone must then install the new CA.
  - So list every name and the LAN address now. When no LAN address is among the names, `init` warns on stderr and in `warnings`: the CA is limited to those names, and it lists this machine's LAN addresses (`ip -o addr show scope global`, container bridges left out) with the `init` command that adds them (`--new-ca` when the CA already exists).
- **`--out`** gets only what the service reads:
  - `fullchain.pem`: the server certificate, then the CA.
  - `privkey.pem`: 0600; make it `root:neuroscifi 0640` as in ENVIRONMENT §4.
  - `ca.pem`.
- **The server certificate** lasts 397 days (`--days`, at most 825, the iOS limit). It carries `serverAuth` and the names as subjectAltName, which is what Chrome, Android and iOS check; they ignore the common name.
- **Serve hint.** `serve` in the output is the whole command: `nsf pocket serve --bind ADDRESS --port 8765 --cert OUT/fullchain.pem --key OUT/privkey.pem --db /path/on/encrypted-volume/mobile.sqlite --instance-db "$NSF_DB" [--push-key ~/.config/neuroscifi/pocket/vapid.pem]` (`--bind` is the LAN address on the certificate when there is one); `serve_notes` says what each part is for, and that `--push-key` is optional. At start the bridge prints `Listening on https://ADDRESS:8765` and the URL for each name on the certificate.
- **Renew:** run `init` again with the same names before expiry, then `sudo systemctl restart neuroscifi-pocket.service`. `nsf pocket tls status --out DIR` shows the days left and whether the chain verifies.

Then set `NSF_POCKET_BIND` and `NSF_POCKET_TLS_ARGS=--cert /etc/neuroscifi/tls/fullchain.pem --key /etc/neuroscifi/tls/privkey.pem` (ENVIRONMENT §4).

## 2. Install the CA on a phone

Export the CA certificate (never a key) and note its SHA-256 fingerprint. Copy the file to the phone over a channel you trust (USB, AirDrop, a paired messenger to yourself), not a public link.

```sh
nsf pocket tls export-ca --file neuroscifi-ca.crt --der     # Android
nsf pocket tls export-ca --file neuroscifi-ca.pem           # iOS (PEM or DER both work)
```

**Android (13–15; wording varies by vendor):**

1. Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → **CA certificate** → "Install anyway" → choose `neuroscifi-ca.crt`.
2. Check: Trusted credentials → **User** tab → "NeuroSciFi Pocket CA (…)". Tap it and compare the SHA-256 fingerprint with the one `export-ca` printed.
3. Chrome on Android trusts user CAs for websites. An installed home-screen Pocket (PWA) uses Chrome's trust, so it trusts the CA too. Android shows a "network may be monitored" notice for any user CA; that is expected.

**iOS / iPadOS (16–18):**

1. Open the file (AirDrop, Files or Mail). iOS says "Profile Downloaded".
2. Settings → General → VPN & Device Management → the downloaded profile → Install (passcode).
3. **Required second step:** Settings → General → About → **Certificate Trust Settings** → turn on full trust for "NeuroSciFi Pocket CA". Without it Safari still refuses the site.
4. Check: open `https://spark.local:8765/` in Safari. A lock and no warning means the chain is trusted. Compare the fingerprint under Settings → General → VPN & Device Management → the profile → More Details.

**Removing:**

- Android: Trusted credentials → User → the CA → Remove.
- iOS: VPN & Device Management → the profile → Remove Profile.

Do this when you replace the CA or give the phone away.

## 3. Verified on the reference host (2026-11-22)

- **Issue.** `nsf pocket tls init` issued a chain for `spark`, `spark.local`, `localhost`, `127.0.0.1` and the host's LAN address. Pocket served it under the `neuroscifi-pocket` sandbox ([HARDENING_VERIFICATION §4.4](HARDENING_VERIFICATION.md#44-pocket-neuroscifi-pocket-sandbox-https)).
- **OpenSSL** (`openssl s_client -verify_return_error -CAfile ca.pem -verify_hostname NAME`):
  - Every name returned `Verify return code: 0 (ok)` over TLS 1.3 with ECDSA P-256.
  - So did the address (`-verify_ip 127.0.0.1`), and `openssl verify -CAfile ca.pem fullchain.pem` returned OK.
  - Negative controls: a name outside the certificate gave `62 (hostname mismatch)`, and the system store alone gave `19 (self-signed certificate in certificate chain)`.
  - `s_client` exits 1 even after a good handshake because the HTTP/1.0 server closes without a TLS close_notify. The verify code is the result.
- **curl** `--cacert ca.pem`: `HTTP/1.0 200 OK` with `Strict-Transport-Security`.
- **Chromium 156** (Playwright), `scripts/tls_browser_check.py`. Each run uses a scratch `HOME`, `ignore_https_errors` is off, and the user's profiles and the system store are untouched:
  - `nss`: the CA was imported into a scratch NSS database (`certutil -A -t C,,`). Pocket loaded (200, TLS 1.3, issuer "NeuroSciFi Pocket CA"). This is Chromium's own chain, name and constraint verification, as on a phone that installed the CA.
  - `spki`: no CA installed, `--ignore-certificate-errors-spki-list=<CA SPKI>`. Loaded (200).
  - `none`: nothing trusted. `net::ERR_CERT_AUTHORITY_INVALID`.
  - `nss` with a name outside the certificate: `net::ERR_CERT_COMMON_NAME_INVALID`.
- **Pairing** over the trusted chain returned 201.
- **Tests:** `tests/test_pocket_tls.py` covers the modes, the chain for every name, the name constraints, the refusals (git tree, shared folder, IP only, over 825 days, a readable CA key), export and status, and a real TLS handshake with Pocket for a client that trusts only the CA.

**Owner-side:** installing the CA on a physical Android phone and iPhone, and the steps in section 2, have not been performed. No handset is attached to this host.

## 4. Alternatives: a publicly trusted certificate

A public CA needs no installation on the phone. Both options keep Pocket off the public internet.

- **Tailscale (`tailscale cert`).** On a tailnet with MagicDNS and HTTPS enabled, `tailscale cert spark.TAILNET.ts.net` gets a Let's Encrypt certificate for the machine's tailnet name. Put the files in `/etc/neuroscifi/tls` and bind Pocket to the tailnet address (100.x).
  - Applies when phone and Spark are both on the tailnet, which also gives the "VPN between phone and host" that ENVIRONMENT §4 recommends.
  - Trade-offs: the machine name is published in the public Certificate Transparency logs; the certificate lasts 90 days, so renew it from a timer and restart Pocket; Tailscale coordination is a third-party dependency.
- **Let's Encrypt DNS-01** (for example `certbot certonly --dns-<provider> -d pocket.example.org` or `lego`). Needs a domain you control and a DNS provider API token.
  - The name resolves only on your LAN: a private A record, or split-horizon DNS on the router. No inbound port is opened, because DNS-01 never contacts the host.
  - Applies when you own a domain. Trade-offs: the name appears in CT logs; the DNS API token on the host can change your DNS, so scope it to one zone or record; 90-day renewal plus a Pocket restart.
  - A `.local` name or a bare IP address cannot get a public certificate. Phones must then use the public name.
- **HTTP-01** does not apply: it needs port 80 reachable from the internet, which this design forbids.

**Recommendation:**

- Tailscale when the phone is used away from home: one dependency, and no CA on the phone.
- Otherwise the private CA, which is fully offline and has no CT exposure, for a LAN-only household.
- DNS-01 only when a domain already exists.
