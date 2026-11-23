"""The console's "Add a phone" (ADR-043): two QR codes for the owner's phone camera, made from what the running Pocket
bridge reports over its owner-only control socket (pocket/server.py `request_invitation`).

Code 1 opens the phone setup page (plain HTTP on the LAN: the CA certificate and the steps, nothing secret). Code 2 is
the Pocket app's https:// URL with the one-use pairing code in its fragment, which a browser never sends to a server;
Pocket reads it, removes it from the address bar and pairs when the person taps Pair. Both are drawn by the console
page from the module matrices returned here, so no image or markup crosses from the bridge into the page.
"""
from __future__ import annotations

from pathlib import Path

QR_ERROR = "m"     # 15 % recovery: a phone camera reads it off a laptop screen at an angle


def pocket_db(server) -> Path:
    """The Pocket pairing database the bridge serves: `nsf console --pocket-db`, else `nsf setup`'s default name next
    to the individual (<name>.pocket.sqlite)."""
    chosen = getattr(server, "pocket_db", None)
    return Path(chosen) if chosen else server.db_path.with_name(server.db_path.stem + ".pocket.sqlite")


def qr_rows(text: str) -> list[str] | None:
    """The QR code of `text` as rows of '0'/'1' (no quiet zone), or None when segno is not installed."""
    try:
        import segno
    except ImportError:
        return None
    code = segno.make(text, error=QR_ERROR, micro=False, boost_error=False)
    return ["".join("1" if bit else "0" for bit in row) for row in code.matrix]


def grouped(code: str) -> str:
    return " ".join(code[i:i + 4] for i in range(0, len(code), 4))


def invite(server, renew=False) -> dict:
    """A new invitation from the running bridge, with both QR codes; {"running": False, ...} when no bridge answers.
    `renew`: the view's automatic renewal of expired codes, which leaves the setup page's window as it is."""
    from ..pocket import server as bridge
    from ..pocket.setup_page import fingerprint_groups
    db = pocket_db(server)
    try:
        reply = bridge.request_invitation(db, renew=renew)
    except OSError:
        return {"running": False, "pocket_db": db.name}
    return {"running": True, "id": reply.get("id"), "expires_in": reply.get("expires_in"),
            "pair_url": reply["pair_url"], "app_url": reply.get("app_url"), "setup_url": reply.get("setup_url"),
            "setup_seconds": reply.get("setup_seconds"), "code": grouped(reply["code"]),
            "fingerprint": fingerprint_groups(reply.get("ca_sha256")),
            "qr": {"pair": qr_rows(reply["pair_url"]),
                   "setup": qr_rows(reply["setup_url"]) if reply.get("setup_url") else None}}


def status(server, ident: str) -> dict:
    """{"state": "used" | "pending" | "expired" | "replaced" | "unknown" | "stopped"} of an id from invite()."""
    from ..pocket import server as bridge
    try:
        return {"state": bridge.invitation_status(pocket_db(server), ident)}
    except OSError:
        return {"state": "stopped"}
