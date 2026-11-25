#!/usr/bin/env python3
"""The memory models' two ports, forwarded to the Unix sockets of deploy/memory/server.py.

run.sh (NSF_MEMORY_EGRESS=none, the default) starts the model server in a container without a network: it holds
the models, the device and the API key, and has no route to anywhere, so nothing in it can open a connection out.
Docker publishes a port only from a container with a network, so this small process, in a container of its own,
listens on the two ports Docker publishes on the host's loopback address and joins each connection to the
server's socket for that port, byte for byte, in both directions:

  forward.py --container --listen 8000=/run/nsf-memory/embed.sock --listen 8001=/run/nsf-memory/rerank.sock

It holds no model, no device and no key file, reads no request (the server parses, authenticates and limits
everything), and logs nothing about a connection. It is not a place where nothing of value passes, though: every
keyed call crosses it in plain text, the `Authorization: Bearer` key and the individual's memory texts with it,
and its container is on a network with outbound access (Docker publishes ports only from such a network). The
"no egress" guarantee is the model server's; for the forwarder it rests on this file and the interpreter that runs
it, which is why run.sh starts it as `python3 -I -S` (the standard library only: no site-packages, no `.pth` hook
of the image, no PYTHON* variable, not the current folder). A client's end of sending is passed on to the server (a half
close). Once the server has ended its side (an answer with `Connection: close`, its 503, a head too slow, the
door's eviction of the oldest waiting connection), nothing the client sends can be answered any more: the
connection is not joined any longer, and what the client still sends is read and discarded for at most
DRAIN_SECONDS and DRAIN_BYTES before it is closed, so a client in the middle of a large body still reads its 503
instead of a reset (what the server's own drainer does when it is reached directly).

At most MAX_CONNECTIONS at once. When they are all taken, a new connection closes the oldest one being drained
(the server is done with it); only when none is being drained is the new one closed. A client the server has done
with therefore never holds the forwarder's room: the connections that remain joined are the ones the server
holds, and the server bounds those itself (its door, its slots, its drainer: a few hundred). The standard library
only.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import signal
import sys
import time

MAX_CONNECTIONS = 1024     # at once, both ports together (the server holds a few hundred at most)
CONNECT_TIMEOUT = 5.0      # seconds to reach the server's socket
CHUNK = 65536
DRAIN_SECONDS = 2.0        # after the server ended its side: what the client still sends is read and discarded
DRAIN_BYTES = (4 << 20) + 32_768   # this long and this much at most (server.py: its largest request)
SERVER_AFTER_CLIENT_SECONDS = 300.0   # after the client ended its side, the server's to answer and end its own
BROKEN_SECONDS = 1.0       # after one side broke, the other's to end before both are closed


def log(message):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), message, flush=True)


class Forwarder:
    def __init__(self, routes: dict[int, str], host: str, *, max_connections=MAX_CONNECTIONS):
        self.routes, self.host, self.max = routes, host, max_connections
        self.connections: dict = {}     # in the order they came: key -> {"writer": client side, "draining": bool}
        self.servers: list = []

    @property
    def open(self) -> int:
        return len(self.connections)

    @property
    def draining(self) -> int:
        return sum(1 for c in self.connections.values() if c["draining"])

    def _room(self) -> bool:
        """Room for one more: the oldest connection being drained makes it, if the forwarder is full."""
        if len(self.connections) < self.max:
            return True
        oldest = next((key for key, c in self.connections.items() if c["draining"]), None)
        if oldest is None:
            return False
        self.connections.pop(oldest)["writer"].transport.abort()
        return True

    async def start(self):
        for port, path in self.routes.items():
            server = await asyncio.start_server(lambda r, w, path=path: self.join(r, w, path), self.host, port,
                                                reuse_address=True, backlog=128)
            self.servers.append(server)
        return [s.sockets[0].getsockname()[1] for s in self.servers]

    async def close(self):
        for server in self.servers:
            server.close()
        for server in self.servers:
            await server.wait_closed()

    async def join(self, client_r, client_w, path):
        if not self._room():
            client_w.transport.abort()
            return
        key = object()
        entry = self.connections[key] = {"writer": client_w, "draining": False}
        server_w = up = down = None
        try:
            try:
                server_r, server_w = await asyncio.wait_for(asyncio.open_unix_connection(path), CONNECT_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                return                              # the server is not up (yet): the client sees the connection end
            up = asyncio.ensure_future(_pump(client_r, server_w))
            down = asyncio.ensure_future(_pump(server_r, client_w))
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            if up.done() and not down.done():
                # The client has sent all it will (the server answers within its own deadlines), or sending to the
                # server failed (it closed: its end follows at once) or the client's side broke (nothing to wait
                # for: the server's answer could not be delivered).
                broke = up.exception() is not None
                await asyncio.wait({down}, timeout=BROKEN_SECONDS if broke else SERVER_AFTER_CLIENT_SECONDS)
            if down.done() and not down.cancelled() and down.exception() is None:
                # The server has said all it will say on this connection: no longer joined; the client's rest is
                # read and discarded (bounded), and the connection can be closed for a newcomer meanwhile.
                if not up.done():
                    up.cancel()
                await asyncio.gather(up, return_exceptions=True)
                server_w.close()
                entry["draining"] = True
                await _drain(client_r)
        finally:
            self.connections.pop(key, None)
            for task in (up, down):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (up, down) if t is not None), return_exceptions=True)
            for writer in (client_w, server_w):
                if writer is not None:
                    writer.close()


async def _pump(reader, writer):
    """Copy until the reader's end, then end the writer's sending side (its reading side stays open)."""
    while True:
        data = await reader.read(CHUNK)
        if not data:
            break
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof() and not writer.transport.is_closing():
        writer.write_eof()


async def _drain(reader):
    """Read and discard what the client still sends, until it ends, DRAIN_BYTES or DRAIN_SECONDS."""
    loop = asyncio.get_running_loop()
    deadline, left = loop.time() + DRAIN_SECONDS, DRAIN_BYTES
    while left > 0:
        wait = deadline - loop.time()
        if wait <= 0:
            return
        try:
            data = await asyncio.wait_for(reader.read(CHUNK), wait)
        except (OSError, asyncio.TimeoutError):
            return
        if not data:
            return
        left -= len(data)


def parse_routes(values) -> dict[int, str]:
    routes = {}
    for value in values:
        port, sep, path = value.partition("=")
        if not sep or not port.isdigit() or not 0 < int(port) < 65536 or not os.path.isabs(path):
            raise SystemExit(f"forward.py: --listen wants PORT=/absolute/socket/path (got {value!r})")
        routes[int(port)] = path
    if not routes:
        raise SystemExit("forward.py: nothing to forward (--listen PORT=PATH)")
    return routes


def check_bind(host, container) -> str:
    """Outside a container only a loopback address; --container (run.sh) allows any: there Docker's publish rule
    decides who can reach the port (as server.check_bind)."""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and not container:
        raise SystemExit(f"forward.py: refusing to listen on {host}: only a loopback address outside a container")
    return host


async def _main(routes, host):
    forwarder = Forwarder(routes, host)
    ports = await forwarder.start()
    log("forwarding " + ", ".join(f"{host}:{p} -> {path}" for p, path in zip(ports, routes.values())))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await stop.wait()
    await forwarder.close()
    log("stopped")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--listen", action="append", default=[], metavar="PORT=PATH",
                    help="a TCP port and the Unix socket its connections are joined to (repeatable)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--container", action="store_true", help="allow a non-loopback address (run.sh, inside Docker)")
    a = ap.parse_args(argv)
    routes = parse_routes(a.listen)
    asyncio.run(_main(routes, check_bind(a.host, a.container)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
