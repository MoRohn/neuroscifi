"""`nsf pocket`: the phone interface bridge."""
from . import server


def register(sub):
    pocket = sub.add_parser("pocket", help="phone interface bridge")
    ops = pocket.add_subparsers(dest="pocket_command", required=True, metavar="ACTION")
    serve = ops.add_parser("serve", help="run the bridge")
    serve.add_argument("--db", default="mobile-private.sqlite")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--cert")
    serve.add_argument("--key")
    serve.set_defaults(handler=_serve)
    listing = ops.add_parser("list-pairings", help="print paired device labels")
    listing.add_argument("--db", default="mobile-private.sqlite")
    listing.set_defaults(handler=_list)
    erase = ops.add_parser("erase-pairing", help="remove a paired device")
    erase.add_argument("label")
    erase.add_argument("--db", default="mobile-private.sqlite")
    erase.set_defaults(handler=_erase)


def _serve(a):
    argv = ["--db", a.db, "--bind", a.bind, "--port", str(a.port)]
    if a.cert:
        argv += ["--cert", a.cert]
    if a.key:
        argv += ["--key", a.key]
    server.main(argv)


def _list(a):
    server.main(["--db", a.db, "--list-pairings"])


def _erase(a):
    server.main(["--db", a.db, "--erase-pairing", a.label])
