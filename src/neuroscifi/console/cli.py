"""`nsf console`: the loopback owner console. There is deliberately no option to bind another address."""
import os
from pathlib import Path
import sys

from ..instance import store
from ..instance.errors import InstanceError
from . import server


def _write_url_file(path, url):
    """Create the file 0600 and refuse an existing one, so the token never lands in a shared file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(url + "\n")


def _serve(args):
    try:
        console = server.ConsoleServer(store.resolve_db(args), args.port)
    except InstanceError as error:
        print(f"nsf: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"nsf: cannot listen on {server.BIND}:{args.port}: {error.strerror}", file=sys.stderr)
        return 2
    try:
        if args.url_file is not None:
            try:
                _write_url_file(args.url_file, console.url)
            except FileExistsError:
                print(f"nsf: {args.url_file} already exists; the console never overwrites a URL file",
                      file=sys.stderr)
                return 2
            print(f"Console URL (with its one-time session token) written to {args.url_file}", flush=True)
        else:
            print(f"Open the owner console: {console.url}", flush=True)
        try:
            console.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        console.server_close()


def register(sub):
    p = sub.add_parser("console", help="loopback owner console (web UI and JSON API on 127.0.0.1)")
    store.add_db_argument(p)
    p.add_argument("--port", type=int, default=server.DEFAULT_PORT, help="loopback port (0 picks a free one)")
    p.add_argument("--url-file", type=Path, default=None,
                   help="write the URL with its session token to this new file (mode 0600) instead of stdout")
    p.set_defaults(handler=_serve)
