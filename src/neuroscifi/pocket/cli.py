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
    serve.set_defaults(handler=_serve)
    listing = ops.add_parser("list-pairings", help="print paired devices: label, family, sponsor, created (UTC)")
    listing.add_argument("--db", default="mobile-private.sqlite")
    listing.add_argument("--instance-db", metavar="PATH", default=os.environ.get("NSF_DB") or None,
                         help="the individual's database (default: $NSF_DB); the family tags are then the ones the "
                              "console and `nsf web node list` show")
    listing.set_defaults(handler=_list)
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
    server.main(argv)


def _list(a):
    server.main(["--db", a.db, "--list-pairings", *(["--instance-db", a.instance_db] if a.instance_db else [])])


def _erase(a):
    server.main(["--db", a.db, "--erase-pairing", a.label, *(["--family"] if a.family else [])])
