"""`nsf console`: the loopback owner console. There is deliberately no option to bind another address."""
import argparse
import os
from pathlib import Path
import signal
import stat
import sys

from ..instance import store
from ..instance.errors import InstanceError
from . import server


STALE_PREFIX = "http://127.0.0.1:"


def _is_stale_url_file(path):
    """A leftover of an earlier console: a regular file we own, not a symlink, holding a console URL."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return False
        return os.read(fd, len(STALE_PREFIX)).decode("ascii", "replace") == STALE_PREFIX
    finally:
        os.close(fd)


def _write_url_file(path, url):
    """Publish the URL atomically with mode 0600. Replace only a stale console URL file; refuse anything else."""
    path = Path(path)
    stale = os.path.lexists(path)
    if stale and not _is_stale_url_file(path):
        raise FileExistsError(path)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(url + "\n")
        if stale:
            os.replace(temp, path)
        else:
            os.link(temp, path)  # fails rather than overwrite a file that appeared meanwhile
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _remove_url_file(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _serve(args):
    try:
        console = server.ConsoleServer(store.resolve_db(args), args.port, web_worker=args.web_worker)
    except InstanceError as error:
        print(f"nsf: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"nsf: cannot listen on {server.BIND}:{args.port}: {error.strerror}", file=sys.stderr)
        return 2
    written = False
    try:
        if args.url_file is not None:
            try:
                signal.signal(signal.SIGTERM, signal.default_int_handler)  # service stop runs the cleanup below
            except ValueError:
                pass  # not the main thread
            try:
                _write_url_file(args.url_file, console.url)
            except FileExistsError:
                print(f"nsf: {args.url_file} exists and is not a stale console URL file; the console never "
                      f"overwrites other files", file=sys.stderr)
                return 2
            written = True
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
        if written:
            _remove_url_file(args.url_file)


def register(sub):
    p = sub.add_parser("console", help="loopback owner console (web UI and JSON API on 127.0.0.1)")
    store.add_db_argument(p)
    p.add_argument("--port", type=int, default=server.DEFAULT_PORT, help="loopback port (0 picks a free one)")
    p.add_argument("--url-file", type=Path, default=None,
                   help="write the URL with its session token to this file (mode 0600) instead of stdout; removed at shutdown; "
                        "only a stale console URL file is replaced")
    worker = p.add_mutually_exclusive_group()
    worker.add_argument("--web-worker", action="store_true",
                        help="also run queued research tasks in the console process (a development console with "
                             "network access; the service unit leaves this to neuroscifi-webcortex.service)")
    worker.add_argument("--no-web-worker", dest="web_worker", action="store_false",
                        help=argparse.SUPPRESS)  # the default since 0.6; kept so older command lines still work
    p.set_defaults(handler=_serve)
