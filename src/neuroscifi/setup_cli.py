"""`nsf setup`: every one-time step of a multi-device install, in order, each idempotent.

1 individual   create it at --db / $NSF_DB when missing
2 web          turn on web access and Pocket research
3 model        configure the local model when one answers at --model-url (skipped otherwise, or with --no-model)
4 pocket TLS   the private CA and a server certificate for this host's names and every LAN address; the CA exported
               as DER (Android) and PEM (iOS) with its SHA-256 fingerprint
5 brave        store a Brave Search API key, only with --brave-key-file or --brave-key-stdin (never on argv, never
               printed); --brave-daily-cap sets daily_provider_queries
6 push         create the Pocket VAPID key, only with --push

Each step reports "done", "already done" or "skipped" (with --dry-run: "would do"). The run records what it chose
in ~/.config/neuroscifi/setup.json (0600), so `nsf start` runs everything with one command and `nsf add-phone` finds
the Pocket bridge; `--json` also lists the exact command of each service, for running them separately. Every step
reuses the function the matching `nsf` command runs, so the rules (file modes, name constraints, key checks) are the
same.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import sqlite3
import sys
import time

from .instance.errors import InstanceError

DEFAULT_MODEL_URL = "http://127.0.0.1:8811/v1"
DEFAULT_PORT = 8765
MODEL_PROBE_SECONDS = 2.0
BACKGROUND_PRIORITY = 10       # the `priority` of autonomous model calls on a vLLM server (lower is served first)
RENEW_DAYS = 30


class SetupError(Exception):
    pass


def _cap(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer from 0 to 100000, got {text!r}") from None
    if not 0 <= value <= 100_000:
        raise argparse.ArgumentTypeError(f"expected an integer from 0 to 100000 (0 switches Brave off), got {value}")
    return value


def register(sub):
    p = sub.add_parser("setup", help="guided one-time setup: individual, web, model, Pocket HTTPS, optional Brave "
                                     "key and push; prints the commands that start NeuroSciFi",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, help="the individual's database (default $NSF_DB; one of them is required); "
                                           "for real use, a path on an encrypted volume")
    model = p.add_mutually_exclusive_group()
    model.add_argument("--model-url", default=DEFAULT_MODEL_URL,
                       help=f"local model endpoint to probe and configure (default {DEFAULT_MODEL_URL})")
    model.add_argument("--no-model", action="store_true", help="skip the model step")
    p.add_argument("--name", action="append", metavar="HOST",
                   help="host name phones use (repeatable); default: this host's name and NAME.local")
    p.add_argument("--ip", action="append", metavar="ADDR",
                   help="LAN address phones use (repeatable); default: every detected LAN address")
    p.add_argument("--bind", metavar="ADDR", help="address the Pocket bridge listens on (default: the first LAN "
                                                  "address on the certificate)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Pocket bridge port (default {DEFAULT_PORT})")
    p.add_argument("--setup-port", type=int, metavar="PORT",
                   help="plain-HTTP port of the phone setup page, which offers the CA certificate to phones being "
                        "added (default: the Pocket port minus one; 0 turns the page off)")
    p.add_argument("--ca-dir", type=Path, help="CA key folder (default as `nsf pocket tls`: $NSF_POCKET_CA_DIR or "
                                               "~/.config/neuroscifi/pocket-ca)")
    p.add_argument("--pocket-dir", type=Path, help="folder for the server certificate, the exported CA and the push "
                                                   "key (default ~/.config/neuroscifi/pocket)")
    p.add_argument("--pocket-db", type=Path, help="the Pocket pairing database (default: next to the individual, "
                                                  "DB_NAME.pocket.sqlite)")
    brave = p.add_mutually_exclusive_group()
    brave.add_argument("--brave-key-file", metavar="PATH", help="store the Brave Search API key from this file "
                                                                "(0600, owned by you; others are refused)")
    brave.add_argument("--brave-key-stdin", action="store_true", help="store the Brave Search API key read from "
                                                                      "standard input (prompted without echo)")
    p.add_argument("--brave-daily-cap", type=_cap, metavar="N", help="Brave queries per UTC day (default 30; 0 "
                                                                    "switches Brave off)")
    p.add_argument("--push", action="store_true", help="create the Pocket push (VAPID) key and add --push-key to the "
                                                       "bridge command")
    p.add_argument("--dry-run", action="store_true", help="show what would be done; change nothing")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.set_defaults(handler=_main)


# ---- steps -----------------------------------------------------------------------------------------------------

def _step(report, name, status, detail, **extra):
    report["steps"].append({"step": name, "status": status, "detail": detail, **extra})


def _individual(report, a, db_path):
    from .instance import store
    if db_path.exists():
        store.open_existing(db_path).close()          # refuses a file that is not an individual
        _step(report, "individual", "already done", f"{db_path} exists")
        return
    if a.dry_run:
        _step(report, "individual", "would do", f"create {db_path}")
        return
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = store.create(db_path, time.time())
    try:
        instance_id = store.inventory(db)["birth"]["instance_id"]
    finally:
        db.close()
    _step(report, "individual", "done", f"created {db_path}", instance_id=instance_id)


def _web(report, a, db):
    from .webcortex import state
    cfg = state.get_config(db) if db is not None else None
    if cfg is not None and cfg.enabled and cfg.pocket_research:
        _step(report, "web", "already done", "web access and Pocket research are on")
    elif a.dry_run:
        _step(report, "web", "would do", "turn on web access and Pocket research")
    else:
        state.set_config(db, {"enabled": True, "pocket_research": True}, time.time())
        _step(report, "web", "done", "web access and Pocket research are on")


def _model(report, a, db):
    from .inference import config as iconfig
    from .inference.gateway import Gateway, InferenceError
    if a.no_model:
        _step(report, "model", "skipped", "--no-model")
        return
    current = iconfig.get_config(db) if db is not None else None
    if current is not None and current.configured:
        same = current.base_url == a.model_url.rstrip("/")
        _step(report, "model", "already done", f"configured: {current.base_url}"
              + ("" if same else " (kept; change it with nsf llm config --url URL)"), model=current.model or None)
        return
    try:
        cfg = iconfig.InferenceConfig(base_url=iconfig._validate("base_url", a.model_url), max_retries=0,
                                      connect_timeout=MODEL_PROBE_SECONDS, read_timeout=MODEL_PROBE_SECONDS)
        served = Gateway(cfg, key=iconfig.read_key(cfg)).models(retries=0)
    except InferenceError as err:
        hint = ("; put the endpoint's API key in ~/.config/neuroscifi/inference/api_key (0600) and run setup "
                "again") if err.code == "unauthorised" else ""
        _step(report, "model", "skipped", f"no model answered at {a.model_url} ({err.code}){hint}; answers stay "
              "extractive until `nsf llm config --url URL`")
        return
    if not served:
        _step(report, "model", "skipped", f"{a.model_url} serves no model")
        return
    model = served[0]["id"]
    if a.dry_run:
        _step(report, "model", "would do", f"configure {cfg.base_url} with model {model}", model=model)
        return
    changes = {"base_url": cfg.base_url, "model": model}
    # A vLLM server says so in /models. deploy/vllm runs it with priority scheduling, and vLLM accepts the request's
    # `priority` under either policy, so background research is sent behind the owner's questions. Any other
    # server is sent nothing it may not know (nsf llm config --set background_priority=N to change either way).
    vllm = str(served[0].get("owned_by") or "").lower() == "vllm"
    if vllm:
        changes["background_priority"] = BACKGROUND_PRIORITY
    iconfig.set_config(db, changes, time.time())
    _step(report, "model", "done", f"configured {cfg.base_url} with model {model}"
          + (f"; background research is sent with priority {BACKGROUND_PRIORITY}, behind your questions" if vllm else ""),
          model=model)


def _names(a):
    """(names for the certificate, detected or given LAN addresses). Host names, localhost and 127.0.0.1 always."""
    from .pocket import tls
    host = socket.gethostname()
    hosts = a.name or [host, f"{host}.local"]
    lan = a.ip if a.ip else tls.lan_addresses()
    dns, ips = tls._split([*hosts, "localhost", *lan, "127.0.0.1"])
    return dns + [str(ip) for ip in ips], lan


def _tls(report, a, ca_dir, out):
    """Issue (or keep) the server chain. A name the existing CA was not created for is left off with a warning: the
    CA stays, so devices keep trusting it; adding the name needs a new CA that every device installs again."""
    from .pocket import tls
    wanted, lan = _names(a)
    seen = tls.status(out, ca_dir=ca_dir)
    has_ca = (ca_dir / "ca.key").exists() and "ca" in seen
    allowed = set(seen["ca"]["limited_to"]) if has_ca else None
    names = [n for n in wanted if allowed is None or n in allowed]
    outside = [n for n in wanted if n not in names]
    warnings = []
    if outside:
        warnings.append(f"{', '.join(outside)} not on the certificate: the CA was created for "
                        f"{', '.join(sorted(allowed))}. Phones must use one of those names. To add them, replace the "
                        f"CA (every device installs it again): nsf pocket tls init --out {shlex.quote(str(out))} "
                        f"--ca-dir {shlex.quote(str(ca_dir))} --new-ca "
                        + " ".join(f"--name {shlex.quote(n)}" for n in wanted))
    server = seen.get("server") or {}
    valid = (server.get("verifies_against_ca") is True and set(names) <= set(server.get("names") or ())
             and server.get("expires_in_days", 0) > RENEW_DAYS and (out / "privkey.pem").is_file())
    if valid:
        _step(report, "pocket_tls", "already done", f"certificate in {out} covers {', '.join(names)}",
              names=server["names"], lan_addresses=lan, warnings=warnings)
        return server["names"]
    if a.dry_run:
        _step(report, "pocket_tls", "would do", f"{'issue' if has_ca else 'create the CA in ' + str(ca_dir) + ' and issue'}"
              f" a certificate into {out} for {', '.join(names)}", names=names, lan_addresses=lan, warnings=warnings)
        return names
    made = tls.init(out, names, ca_dir=ca_dir)
    warnings += [w for w in made.get("warnings", ()) if "No LAN address" in w]
    _step(report, "pocket_tls", "done", ("created the CA and " if made["ca_created"] else "")
          + f"issued a certificate for {', '.join(made['names'])} (valid until {made['server_not_after'][:10]})",
          names=made["names"], lan_addresses=lan, warnings=warnings)
    return made["names"]


def _export_ca(report, a, ca_dir, pocket_dir):
    """The CA certificate for phones: DER (.crt, Android) and PEM (iPhone and iPad accept both)."""
    from .pocket import tls
    files = {"der": pocket_dir / "neuroscifi-ca.crt", "pem": pocket_dir / "neuroscifi-ca.pem"}
    if not (ca_dir / "ca.pem").is_file():
        _step(report, "ca_export", "would do", f"export the CA to {files['der']} and {files['pem']}",
              files={k: str(v) for k, v in files.items()})
        return None
    before = {k: v.read_bytes() if v.is_file() else None for k, v in files.items()}
    if a.dry_run:
        info = tls.export_ca(None, ca_dir=ca_dir)
        status = "already done" if before["pem"] == info["pem"].encode() and before["der"] else "would do"
    else:
        for kind, path in files.items():
            info = tls.export_ca(path, ca_dir=ca_dir, der=kind == "der")
        status = "already done" if all(before[k] == files[k].read_bytes() for k in files) else "done"
    fingerprint = info["fingerprints"]["sha256"]
    _step(report, "ca_export", status, f"CA for phones: {files['der']} (Android) and {files['pem']}; SHA-256 "
          f"{fingerprint}", files={k: str(v) for k, v in files.items()}, sha256=fingerprint)
    return fingerprint


def _brave(report, a, db):
    from .webcortex import state
    from .webcortex.cli import KEY_SOURCE_MAX, _read_key_source
    from .webcortex.providers import keys
    if a.brave_key_file or a.brave_key_stdin:
        if a.brave_key_stdin and a.dry_run:
            text = None
        elif a.brave_key_file:
            text = _read_key_source(a.brave_key_file)      # the same checks as `nsf web provider set-key`
        elif sys.stdin.isatty():
            import getpass
            text = getpass.getpass("Brave Search API key (not echoed): ")
        else:
            text = sys.stdin.read(KEY_SOURCE_MAX)
        if text is not None:
            try:
                new = keys.normalise_key(text)
            except keys.KeyProblem as exc:
                raise SetupError(f"Brave key not stored: {exc.detail}") from None
            try:
                same = keys.read_key("brave") == new
            except keys.KeyProblem:
                same = False
        if text is not None and same:
            _step(report, "brave", "already done", "key configured (unchanged)", key="configured")
        elif a.dry_run:
            _step(report, "brave", "would do", f"store the key in {keys.key_path('brave')} (0600)")
        else:
            try:
                path = keys.write_key("brave", new)
            except keys.KeyProblem as exc:
                raise SetupError(f"Brave key not stored: {exc.detail}") from None
            _step(report, "brave", "done", f"key configured in {path} (0600; never printed)", key="configured")
    else:
        found = keys.key_state("brave")["key"]
        _step(report, "brave", "already done" if found == "configured" else "skipped",
              f"key {found}" + ("" if found == "configured" else
                                " (optional: nsf setup --brave-key-stdin, or nsf web provider set-key brave --stdin)"),
              key=found)
    if a.brave_daily_cap is not None:
        current = state.get_config(db).daily_provider_queries.get("brave") if db is not None else None
        if current == a.brave_daily_cap:
            _step(report, "brave_cap", "already done", f"daily cap {current}")
        elif a.dry_run:
            _step(report, "brave_cap", "would do", f"set the daily cap to {a.brave_daily_cap}")
        else:
            state.set_config(db, {"daily_provider_queries": {"brave": a.brave_daily_cap}}, time.time())
            _step(report, "brave_cap", "done", f"daily cap {a.brave_daily_cap}"
                  + (" (Brave off)" if a.brave_daily_cap == 0 else ""))


def _push(report, a, path):
    if not a.push:
        _step(report, "push", "skipped", "optional: nsf setup --push")
        return
    if path.exists():
        status = "already done"
    elif a.dry_run:
        status = "would do"
    else:
        status = "done"
    if not a.dry_run:
        try:
            from .pocket import push
            push.load_key(path)       # creates a 0600 key on first use; checks an existing one
        except ImportError:
            raise SetupError("push needs the crypto extra: pip install 'neuroscifi[crypto]'") from None
    _step(report, "push", status, f"VAPID key {path} (0600)")


# ---- the run ---------------------------------------------------------------------------------------------------

def _q(path) -> str:
    return shlex.quote(str(path))


def setup_record_path() -> Path:
    """Where `nsf setup` records its choices for `nsf start` and `nsf add-phone`."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "neuroscifi" / "setup.json"


def _next(report, a, paths, cert_names, fingerprint):
    ips = [n for n in cert_names if _is_ip(n) and n not in ("127.0.0.1", "::1")]
    bind = a.bind or next((n for n in ips if ":" not in n), ips[0] if ips else "127.0.0.1")
    setup_port = a.port - 1 if a.setup_port is None else a.setup_port
    db = str(paths["db"])
    serve = ["pocket", "serve", "--bind", bind, "--port", str(a.port), "--cert", str(paths["tls"] / "fullchain.pem"),
             "--key", str(paths["tls"] / "privkey.pem"), "--db", str(paths["pocket_db"]), "--instance-db", db]
    serve += ["--push-key", str(paths["push_key"])] if a.push else []
    # only the certificate's names and addresses in Host (421 otherwise): a page elsewhere cannot reach the bridge or
    # its setup page through DNS rebinding
    for name in cert_names:
        serve += ["--allowed-host", f"[{name}]" if ":" in name else name]
    serve += ["--setup-port", str(setup_port), "--ca-file", str(paths["ca_der"])] if setup_port else []
    services = [{"name": "research", "what": "research worker", "argv": ["web", "work", "--db", db]},
                {"name": "console", "what": "owner console (open the URL written to the file, on this computer)",
                 "argv": ["console", "--db", db, "--url-file", str(paths["console_url"]),
                          "--pocket-db", str(paths["pocket_db"])]},
                {"name": "pocket", "what": "Pocket bridge", "argv": serve}]
    commands = [{"what": s["what"], "command": "nsf " + shlex.join(s["argv"])} for s in services]
    hosts = [n for n in cert_names if not _is_ip(n) and n != "localhost"]
    host = next((n for n in hosts if n.endswith(".local")), hosts[0] if hosts else bind)
    urls = [f"https://{host}:{a.port}"] + ([f"https://{bind}:{a.port}"] if bind not in (host, "127.0.0.1") else [])
    report["next"] = {"start": "nsf start", "add_phone": "nsf add-phone", "commands": commands, "phone": {
        "urls": urls, "setup_url": f"http://{bind}:{setup_port}/" if setup_port else None,
        "ca_file": str(paths["ca_der"]), "ca_file_pem": str(paths["ca_pem"]), "ca_sha256": fingerprint,
        "text": "in the console choose Add a phone (or run nsf add-phone) and scan its two codes with the phone's "
                "camera: the first installs trust in your Spark once per phone, the second pairs it"}}
    if bind == "127.0.0.1" and not a.bind:
        report["next"]["note"] = ("no LAN address was found, so the bridge listens on this computer only; give the "
                                  "address phones use with --ip ADDR")
    record = {"version": 1, "db": db, "pocket_db": str(paths["pocket_db"]),
              "console_url_file": str(paths["console_url"]), "services": services}
    report["record"] = str(setup_record_path())
    if not a.dry_run:
        _write_record(setup_record_path(), record)


def _write_record(path: Path, record: dict):
    """Write the setup record (0600, atomically, through a new temporary file of our own); an identical record is left
    untouched. `nsf start` runs the commands in it, so its folder must be ours and closed to others."""
    import stat
    import tempfile
    data = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path.parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise OSError(f"{path.parent} must be a folder of this user that others cannot write")
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".setup.", suffix=".tmp")   # O_EXCL, 0600
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _is_ip(text) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _run(a, report):
    from .instance import store
    from .pocket import tls
    db_path = a.db or (Path(os.environ["NSF_DB"]) if os.environ.get("NSF_DB") else None)
    if db_path is None:
        raise SetupError("name the individual's database: --db PATH, or export NSF_DB=PATH. For real use choose a "
                         "path on an encrypted volume; `nsf doctor --db PATH` checks it")
    db_path = db_path.expanduser().absolute()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    pocket_dir = (a.pocket_dir or config_home / "neuroscifi" / "pocket").expanduser().absolute()
    paths = {"db": db_path, "ca_dir": (a.ca_dir or tls.DEFAULT_CA_DIR).expanduser().absolute(),
             "tls": pocket_dir / "tls", "ca_der": pocket_dir / "neuroscifi-ca.crt",
             "ca_pem": pocket_dir / "neuroscifi-ca.pem", "push_key": pocket_dir / "vapid.pem",
             "pocket_db": (a.pocket_db or db_path.with_name(db_path.stem + ".pocket.sqlite")).expanduser().absolute(),
             "console_url": db_path.with_name(db_path.stem + ".console.url")}
    report["db"] = str(db_path)
    report["paths"] = {k: str(v) for k, v in paths.items()}
    current = "individual"
    try:
        _individual(report, a, db_path)
        db = store.open_existing(db_path) if db_path.exists() else None
        try:
            current = "web"
            _web(report, a, db)
            current = "model"
            _model(report, a, db)
            current = "pocket_tls"
            if not a.dry_run:
                pocket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            names = _tls(report, a, paths["ca_dir"], paths["tls"])
            current = "ca_export"
            fingerprint = _export_ca(report, a, paths["ca_dir"], pocket_dir)
            current = "brave"
            _brave(report, a, db)
            current = "push"
            _push(report, a, paths["push_key"])
        finally:
            if db is not None:
                db.close()
    except (InstanceError, OSError, sqlite3.Error, tls.TLSError) as exc:
        raise SetupError(f"{current}: {exc}") from None
    _next(report, a, paths, names, fingerprint)


def _print_human(report):
    print(f"nsf setup{' --dry-run' if report['dry_run'] else ''}: {report.get('db', '')}")
    for i, step in enumerate(report["steps"], 1):
        print(f"  {i}. {step['step']:<11} {step['status']:<13} {step['detail']}")
        for warning in step.get("warnings") or ():
            print(f"     warning: {warning}")
    nxt = report.get("next")
    if not nxt:
        return
    print("\nNeuroSciFi is set up. Next:")
    print("  1. Start it:     nsf start")
    print("                   (runs the research worker, the owner console and Pocket, and opens the console)")
    print("  2. Add a phone:  in the console choose \"Add a phone\" (or run: nsf add-phone) and scan its two codes")
    print("                   with the phone's camera. Code 1 makes the phone trust your Spark, once per phone;")
    print("                   code 2 pairs it. No typing, no files to copy.")
    if nxt.get("note"):
        print(f"  Note: {nxt['note']}.")
    print("\nTo run the services separately (for example as system services), use these commands:")
    for item in nxt["commands"]:
        print(f"  {item['what']}:\n    {item['command']}")


def _main(a):
    report = {"ok": True, "dry_run": bool(a.dry_run), "steps": []}
    try:
        _run(a, report)
    except SetupError as exc:
        report.update(ok=False, error=str(exc))
        if a.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        print(f"nsf setup: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0
