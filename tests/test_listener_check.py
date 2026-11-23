"""Whose process would accept a connection to a loopback port (resources.monitor: listening, accepting, stranger;
resources.idle.listener_refusal). Second-pass review S1 and S7: the first version compared the port alone, on any
address; trusted or refused the unidentifiable uid 65534; applied to every loopback endpoint; and parsed both socket
tables on every request. The tables here have the kernel's own shape (/proc/net/tcp and tcp6)."""
import ipaddress
import os
import socket
import struct
import sys

import pytest

from neuroscifi.resources import idle, monitor

HEAD4 = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
HEAD6 = ("  sl  local_address                         remote_address                        st tx_queue rx_queue tr "
         "tm->when retrnsmt   uid  timeout inode\n")
ME = os.getuid()
STRANGER = ME + 4242


def kernel(address) -> str:
    """An address as /proc/net/tcp prints it: each 32-bit word of the network-order bytes as this machine's integer."""
    packed = ipaddress.ip_address(address).packed
    return "".join(f"{word:08X}" for word in struct.unpack(f"={len(packed) // 4}I", packed))


def line(n, address, port, uid, state="0A", mapped=False):
    local = kernel(f"::ffff:{address}") if mapped else kernel(address)
    remote = "0" * len(local)
    return (f"{n:4d}: {local}:{port:04X} {remote}:0000 {state} 00000000:00000000 00:00000000 00000000 {uid:5d}"
            f"        0 {1000 + n} 1 0000000000000000 100 0 0 10 0\n")


@pytest.fixture
def proc(tmp_path):
    """write(v4 lines, v6 lines) -> the folder; every call is a different folder, so nothing is read from a cache."""
    count = [0]

    def write(v4=(), v6=()):
        count[0] += 1
        folder = tmp_path / f"net{count[0]}"
        folder.mkdir()
        (folder / "tcp").write_text(HEAD4 + "".join(v4))
        (folder / "tcp6").write_text(HEAD6 + "".join(v6))
        return folder
    return write


def table(folder):
    return lambda port: monitor.listeners(port, folder)


def test_the_address_column_is_read_in_the_kernels_byte_order(proc):
    if sys.byteorder == "little":                                # what this host's kernel really prints
        assert monitor._proc_address("0100007F") == "127.0.0.1"
        assert monitor._proc_address("3500007F") == "127.0.0.53"
        assert monitor._proc_address("00000000000000000000000001000000") == "::1"
        assert monitor._proc_address("0000000000000000FFFF00000100007F") == "127.0.0.1"      # IPv4-mapped
    for address in ("127.0.0.1", "127.0.0.53", "172.17.0.1", "0.0.0.0", "::1", "::", "fe80::1", "2001:db8::7"):
        assert monitor._proc_address(kernel(address)) == address
    assert monitor._proc_address(kernel("::ffff:10.1.2.3")) == "10.1.2.3"
    for bad in ("", "0100007", "0100007G", "0" * 31, "0" * 33, "zz" * 16):
        assert monitor._proc_address(bad) is None
    folder = proc([line(0, "127.0.0.1", 18441, 0), line(1, "127.0.0.53", 53, 973), line(2, "172.17.0.1", 53, 973),
                   line(3, "127.0.0.1", 50000, ME, state="01"), "garbage\n", "   9: nonsense:zz x 0A\n"],
                  [line(0, "::1", 18441, STRANGER), line(1, "::", 8811, ME), line(2, "127.0.0.1", 8812, 7, mapped=True)])
    assert monitor.listening(folder) == {18441: [("127.0.0.1", 0), ("::1", STRANGER)],
                                         53: [("127.0.0.53", 973), ("172.17.0.1", 973)],
                                         8811: [("::", ME)], 8812: [("127.0.0.1", 7)]}
    assert monitor.listeners(53, folder) == [("127.0.0.53", 973), ("172.17.0.1", 973)]
    assert monitor.listeners(50000, folder) == []                # a connection, not a listener
    assert monitor.listener_uids(18441, folder) == {0, STRANGER}  # any address: the older reading, still available
    assert monitor.listeners(53, folder / "nowhere") is None and monitor.listening(folder / "nowhere") is None


def test_only_the_socket_that_would_accept_the_connection_counts(proc):
    """The reviewer's case, as this host shows it: a resolver of another account on 127.0.0.53:53 and 172.17.0.1:53
    does not hold 127.0.0.1:53."""
    folder = proc([line(0, "127.0.0.53", 53, 973), line(1, "127.0.0.54", 53, 973), line(2, "172.17.0.1", 53, 973)])
    assert monitor.stranger(["127.0.0.1"], 53, table=table(folder)) is None
    assert "127.0.0.53:53" in monitor.stranger(["127.0.0.53"], 53, table=table(folder))
    # binding the service's port somewhere else does not turn the service off
    folder = proc([line(0, "127.0.0.1", 8811, 0), line(1, "192.168.1.20", 8811, STRANGER)],
                  [line(0, "::1", 8811, STRANGER)])
    assert monitor.stranger(["127.0.0.1"], 8811, table=table(folder)) is None
    assert "[::1]:8811" in monitor.stranger(["::1"], 8811, table=table(folder))            # there it would answer
    assert "[::1]:8811" in monitor.stranger(["127.0.0.1", "::1"], 8811, table=table(folder))     # localhost: either


def test_exact_address_then_the_familys_wildcard_then_the_dual_stack_wildcard(proc):
    entries = [("127.0.0.1", 1), ("0.0.0.0", 2), ("::", 3), ("::1", 4)]
    assert monitor.accepting(entries, ["127.0.0.1"]) == {"127.0.0.1": {1}}
    assert monitor.accepting(entries[1:], ["127.0.0.1"]) == {"127.0.0.1": {2}}
    assert monitor.accepting(entries[2:], ["127.0.0.1"]) == {"127.0.0.1": {3}}       # a dual-stack socket serves IPv4
    assert monitor.accepting(entries[3:], ["127.0.0.1"]) == {}
    assert monitor.accepting(entries, ["::1"]) == {"::1": {4}}
    assert monitor.accepting(entries[:3], ["::1"]) == {"::1": {3}}
    assert monitor.accepting(entries[:2], ["::1"]) == {}                              # 0.0.0.0 never serves IPv6
    assert monitor.accepting(entries, ["::ffff:127.0.0.1", "[::1]", "not an address"]) == \
        {"127.0.0.1": {1}, "::1": {4}}
    folder = proc([line(0, "0.0.0.0", 8811, STRANGER)])
    assert "127.0.0.1:8811" in monitor.stranger(["127.0.0.1"], 8811, table=table(folder))
    folder = proc([], [line(0, "::", 8811, STRANGER)])
    assert "uid " + str(STRANGER) in monitor.stranger(["127.0.0.1"], 8811, table=table(folder))
    folder = proc([], [line(0, "127.0.0.1", 8811, STRANGER, mapped=True)])                 # ::ffff:127.0.0.1
    assert "127.0.0.1:8811" in monitor.stranger(["127.0.0.1"], 8811, table=table(folder))
    folder = proc([line(0, "127.0.0.1", 8811, 0), line(1, "0.0.0.0", 8811, STRANGER)])     # the proxy answers first
    assert monitor.stranger(["127.0.0.1"], 8811, table=table(folder)) is None


def test_who_is_the_service_and_the_account_that_cannot_be_identified(proc, tmp_path, monkeypatch):
    key = tmp_path / "api_key"
    key.write_text("k")
    for uid in (0, ME):
        assert monitor.stranger(["127.0.0.1"], 8811, table=lambda port, uid=uid: [("127.0.0.1", uid)]) is None
    refusal = monitor.stranger(["127.0.0.1"], 8811, table=lambda port: [("127.0.0.1", STRANGER)], what="the local model")
    assert refusal.startswith(f"127.0.0.1:8811 on this computer is held by a process of another account "
                              f"(uid {STRANGER}), not by the local model") and "NSF_LISTENER_CHECK=0" in refusal
    # the key file's owner is the service
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda path, *a, **k: type("S", (), {"st_uid": STRANGER})() if path == key
                        else real(path, *a, **k))
    assert monitor.stranger(["127.0.0.1"], 8811, key_file=key, table=lambda port: [("127.0.0.1", STRANGER)]) is None
    # uid 65534: not refused, and never trusted through the key file
    monkeypatch.setattr(os, "stat", lambda path, *a, **k: type("S", (), {"st_uid": 65534})() if path == key
                        else real(path, *a, **k))
    assert monitor.stranger(["127.0.0.1"], 8811, key_file=key, table=lambda port: [("127.0.0.1", 65534)]) is None
    assert monitor.stranger(["127.0.0.1"], 8811, table=lambda port: [("127.0.0.1", 65534)]) is None
    both = monitor.stranger(["127.0.0.1"], 8811, key_file=key,
                            table=lambda port: [("127.0.0.1", 65534), ("127.0.0.1", STRANGER)])
    assert f"uid {STRANGER})" in both and "65534" not in both


def test_the_check_is_never_why_a_call_fails_and_the_owner_can_switch_it_off(proc):
    held = lambda port: [("127.0.0.1", STRANGER)]                                     # noqa: E731
    assert monitor.stranger(["127.0.0.1"], 8811, table=held) is not None
    for environ in ({"NSF_LISTENER_CHECK": "0"}, {"NSF_LISTENER_CHECK": " 0 "}):
        assert monitor.stranger(["127.0.0.1"], 8811, table=held, environ=environ) is None
    for environ in ({}, {"NSF_LISTENER_CHECK": "1"}, {"NSF_LISTENER_CHECK": ""}, {"NSF_LISTENER_CHECK": "off"}):
        assert monitor.stranger(["127.0.0.1"], 8811, table=held, environ=environ) is not None

    def broken(port):
        raise OSError("no /proc here")
    for surprise in (broken, lambda port: None, lambda port: [], lambda port: [("x", 5)], lambda port: [(1,)],
                     lambda port: "text", lambda port: [("127.0.0.1", "root")]):
        assert monitor.stranger(["127.0.0.1"], 8811, table=surprise) is None
    assert monitor.stranger([], 8811, table=held) is None and monitor.stranger(["nonsense"], 8811, table=held) is None
    assert monitor.stranger(["127.0.0.1"], "not a port", table=held) is None


def test_only_this_deployments_own_service_ports_are_checked():
    """Review S1 (b): a server the owner runs on its own port and account (Ollama, a unit with User=, rootless
    Docker) was refused although it worked before the check existed."""
    held = lambda port: [("127.0.0.1", STRANGER)]                                     # noqa: E731
    ports = {name: idle.service_port(name) for name in ("vllm", "asr", "embed", "rerank")}
    assert all(isinstance(port, int) for port in ports.values())
    for name, port in ports.items():
        assert idle.managed_service(port) == name
        refusal = idle.listener_refusal(["127.0.0.1"], port, table=held)
        assert refusal and f"127.0.0.1:{port}" in refusal and idle.SERVICES[name].label in refusal
    for port in (11434, 8000, 53, 1, 65535):                    # Ollama's, anyone's
        assert idle.managed_service(port) is None
        assert idle.listener_refusal(["127.0.0.1"], port, table=held) is None
    assert idle.managed_service("x") is None and idle.managed_service(None) is None
    assert idle.listener_refusal(["127.0.0.1"], ports["vllm"], table=held, environ={"NSF_LISTENER_CHECK": "0"}) is None


def test_one_parse_of_the_tables_serves_a_second_of_checks(proc, monkeypatch):
    """Review S7: measured 4.7 ms a call on the reference host, four times per memory lookup."""
    folder = proc([line(0, "127.0.0.1", 18441, 0)])
    clock = [1000.0]
    monkeypatch.setattr(monitor.time, "monotonic", lambda: clock[0])
    assert monitor.listeners(18441, folder) == [("127.0.0.1", 0)]
    (folder / "tcp").write_text(HEAD4 + line(0, "127.0.0.1", 18441, STRANGER))
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: opened.append(str(path)) or real_open(path, *a, **k))
    clock[0] += 0.9
    assert monitor.listeners(18441, folder) == [("127.0.0.1", 0)] and monitor.listeners(18442, folder) == []
    assert opened == []                                          # still the same parse, for every port
    assert monitor.listening(folder, fresh=True)[18441] == [("127.0.0.1", STRANGER)]
    clock[0] += monitor.LISTEN_CACHE_SECONDS + 0.01
    opened.clear()
    assert monitor.listeners(18441, folder) == [("127.0.0.1", STRANGER)] and len(opened) == 2
    clock[0] -= 50                                               # a clock that went back does not keep a stale parse
    opened.clear()
    monitor.listeners(18441, folder)
    assert len(opened) == 2


def test_a_real_listening_socket_of_this_process_is_found_with_its_address_and_owner():
    """No fixture: the kernel's own table, for sockets bound just now."""
    def bound(family, address):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((address, 0))
            sock.listen(1)
        except OSError:
            sock.close()
            return None
        return sock
    cases = [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "127.0.0.2"), (socket.AF_INET, "0.0.0.0"),
             (socket.AF_INET6, "::1")]
    for family, address in cases:
        sock = bound(family, address)
        if sock is None:
            continue                                             # no such address on this host (IPv6 off)
        try:
            port = sock.getsockname()[1]
            table = monitor.listening(fresh=True)
            if table is None:
                pytest.skip("/proc/net/tcp is not readable here")
            assert (address, ME) in table.get(port, []), (address, table.get(port))
            assert monitor.stranger([address], port) is None     # this process is the service
            if address == "127.0.0.2":                           # and it does not hold 127.0.0.1's port
                assert monitor.accepting(table[port], ["127.0.0.1"]) == {}
        finally:
            sock.close()
