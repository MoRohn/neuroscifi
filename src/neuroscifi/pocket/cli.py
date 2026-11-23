"""`nsf pocket`: the phone interface bridge."""
import os

from . import server, tls


def register(sub):
    pocket = sub.add_parser("pocket", help="phone interface bridge")
    ops = pocket.add_subparsers(dest="pocket_command", required=True, metavar="ACTION")
    serve = ops.add_parser("serve", help="run the bridge")
    serve.add_argument("--db", default="mobile-private.sqlite")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--cert")
    serve.add_argument("--key")
    serve.add_argument("--allowed-host", action="append", metavar="NAME",
                       help="accept only this Host (name or name:port); repeatable; others get 421")
    serve.add_argument("--invitation-file", metavar="PATH",
                       help="write the one-use code to this 0600 file instead of stdout; only a stale invitation "
                            "left by an earlier bridge is replaced")
    serve.add_argument("--instance-db", metavar="PATH",
                       help="the individual's database; with `nsf web config` pocket_research on, paired devices "
                            "may queue and read research tasks of their own family (the Spark's worker runs them)")
    serve.add_argument("--push-key", metavar="PATH",
                       help="offer Web Push to paired devices that opt in (ADR-040): the Spark's VAPID private key, "
                            "created as a new 0600 file on first start; keep it outside git (needs --instance-db and "
                            "the crypto extra)")
    serve.add_argument("--push-contact", metavar="URI",
                       help="the VAPID contact (mailto: or https:) the push service sees")
    serve.add_argument("--setup-port", type=int, metavar="PORT",
                       help="also serve the phone setup page (ADR-043) on this plain-HTTP port: the CA certificate "
                            "and step-by-step instructions only, for 15 minutes after each new invitation (needs "
                            "--cert and --ca-file)")
    serve.add_argument("--ca-file", metavar="PATH",
                       help="the CA certificate (DER or PEM, never a key) the setup page offers to phones")
    serve.set_defaults(handler=_serve)
    listing = ops.add_parser("list-pairings", help="print paired devices: label, family, sponsor, created (UTC)")
    listing.add_argument("--db", default="mobile-private.sqlite")
    listing.add_argument("--instance-db", metavar="PATH", default=os.environ.get("NSF_DB") or None,
                         help="the individual's database (default: $NSF_DB); the family tags are then the ones the "
                              "console and `nsf web node list` show")
    listing.set_defaults(handler=_list)
    invite = ops.add_parser("invite", help="ask the running bridge for a new one-use pairing code (5 minutes), for "
                                           "another family, without a restart (owner only: a 0600 local socket)")
    invite.add_argument("--db", default="mobile-private.sqlite", help="the Pocket database the bridge serves")
    invite.set_defaults(handler=_invite)
    erase = ops.add_parser("erase-pairing", help="remove a paired device")
    erase.add_argument("label")
    erase.add_argument("--family", action="store_true",
                       help="also remove every device in the label's pairing family (linked phones and tablets)")
    erase.add_argument("--db", default="mobile-private.sqlite")
    erase.set_defaults(handler=_erase)
    tls.register(ops)


def _serve(a):
    argv = ["--db", a.db, "--bind", a.bind, "--port", str(a.port)]
    if a.cert:
        argv += ["--cert", a.cert]
    if a.key:
        argv += ["--key", a.key]
    for name in a.allowed_host or ():
        argv += ["--allowed-host", name]
    if a.invitation_file:
        argv += ["--invitation-file", a.invitation_file]
    if a.instance_db:
        argv += ["--instance-db", a.instance_db]
    if a.push_key:
        argv += ["--push-key", a.push_key]
    if a.push_contact:
        argv += ["--push-contact", a.push_contact]
    if a.setup_port is not None:
        argv += ["--setup-port", str(a.setup_port)]
    if a.ca_file:
        argv += ["--ca-file", a.ca_file]
    server.main(argv)


def _list(a):
    server.main(["--db", a.db, "--list-pairings", *(["--instance-db", a.instance_db] if a.instance_db else [])])


def _invite(a):
    import sys
    try:
        reply = server.request_invitation(a.db)
    except (OSError, ValueError) as error:
        print(f"nsf pocket invite: no running Pocket bridge answered for {a.db} ({getattr(error, 'strerror', None) or error}). "
              f"Start it with `nsf pocket serve --db {a.db} ...` (its first code is printed at start).", file=sys.stderr)
        return 1
    minutes = max(1, int(reply.get("expires_in") or server.INVITATION_SECONDS) // 60)
    print(f"One-use pairing code ({minutes} minutes): {reply['code']}")
    print("Enter it on the new device; it replaces any earlier terminal code. Devices already paired are unaffected.")
    return 0


def _erase(a):
    server.main(["--db", a.db, "--erase-pairing", a.label, *(["--family"] if a.family else [])])
