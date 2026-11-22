"""Report, from inside a service, which sandbox restrictions the kernel actually enforces on it.

Run it under the same properties as a unit and compare with what the unit asks for
(docs/operations/HARDENING_VERIFICATION.md):

    systemd-run [--user] --wait --pipe --unit nsf-probe -p ProtectSystem=strict ... python3 hardening_probe.py \
        [--rw PATH] [--allow HOST:PORT] [--deny HOST:PORT]

Standard library only, read-mostly: every attempt that would change something targets a path the unit should
refuse, a throwaway file under --rw, or an anonymous mapping. Prints one JSON object; exit status 0.
"""
from __future__ import annotations

import argparse
import errno
import json
import mmap
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys


def _status() -> dict:
    out = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in ("Uid", "Gid", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp", "Seccomp_filters"):
            out[key] = value.split()[0] if key in ("Uid", "Gid") else value.strip()
    return out


def _try(fn) -> str:
    try:
        fn()
        return "allowed"
    except OSError as exc:
        return f"refused ({errno.errorcode.get(exc.errno, exc.errno)})"


def _write(path: str):
    def attempt():
        target = Path(path) / f".nsf-hardening-probe-{os.getpid()}"
        target.write_text("probe")
        target.unlink()
    return attempt


def _connect(target: str):
    host, _, port = target.rpartition(":")

    def attempt():
        with socket.create_connection((host, int(port)), timeout=3):
            pass
    return attempt


def _socket(family, kind=socket.SOCK_STREAM):
    def attempt():
        socket.socket(family, kind).close()
    return attempt


def _wx_mapping():
    # An anonymous mapping that is writable and executable at once: refused under MemoryDenyWriteExecute.
    m = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    m.close()


def _child(call: str) -> str:
    """One libc call in a child interpreter: a seccomp filter may kill the caller (SIGSYS) instead of failing it."""
    code = ("import ctypes, os, sys\nlibc = ctypes.CDLL(None, use_errno=True)\n"
            f"rc = {call}\nsys.exit(0 if rc == 0 else ctypes.get_errno() or 255)")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=10)
    if done.returncode == 0:
        return "allowed"
    if done.returncode < 0:
        return f"killed ({signal.Signals(-done.returncode).name})"
    return f"refused ({errno.errorcode.get(done.returncode, done.returncode)})"


def _visible(path: str) -> str:
    p = Path(path)
    try:
        return f"visible ({len(list(p.iterdir()))} entries)" if p.is_dir() else ("visible" if p.exists() else "absent")
    except OSError as exc:
        return f"inaccessible ({errno.errorcode.get(exc.errno, exc.errno)})"


def _cgroup_limits() -> dict:
    rel = Path("/proc/self/cgroup").read_text().strip().split("::")[-1]
    base = Path("/sys/fs/cgroup") / rel.lstrip("/")
    out = {"cgroup": rel}
    for name in ("memory.max", "pids.max", "cpu.weight", "io.weight"):
        try:
            out[name] = (base / name).read_text().strip()
        except OSError as exc:
            out[name] = f"unavailable ({errno.errorcode.get(exc.errno, exc.errno)})"
    return out


def probe(rw=(), allow=(), deny=()) -> dict:
    CLONE_NEWUSER = 0x10000000
    report = {"status": _status(), "pid1_visible": Path("/proc/1/cmdline").exists(),
              "proc_pids_visible": sum(1 for p in Path("/proc").iterdir() if p.name.isdigit()),
              "cgroup": _cgroup_limits()}
    report["write"] = {p: _try(_write(p)) for p in ("/usr", "/etc", "/var/tmp", "/tmp", *rw)}
    home = os.environ.get("HOME") or "/home"
    report["visible"] = {p: _visible(p) for p in ("/home", "/root", "/run/user", home, "/dev/nvme0n1", "/dev/sda",
                                                   "/dev/kmsg", "/proc/kcore")}
    report["tmp_entries"] = len(list(Path("/tmp").iterdir()))
    report["kernel_tunable_write"] = _try(lambda: Path("/proc/sys/kernel/domainname").write_text("probe"))
    report["memory_write_execute"] = _try(_wx_mapping)
    report["new_user_namespace"] = _child(f"libc.unshare({CLONE_NEWUSER})")
    report["set_clock"] = _child("libc.clock_settime(0, ctypes.byref((ctypes.c_long * 2)(0, 0)))")
    report["realtime_scheduler"] = _child("libc.sched_setscheduler(0, 1, ctypes.byref(ctypes.c_int(1)))")
    report["socket_families"] = {"AF_INET": _try(_socket(socket.AF_INET)), "AF_UNIX": _try(_socket(socket.AF_UNIX)),
                                 "AF_NETLINK": _try(_socket(socket.AF_NETLINK, socket.SOCK_RAW)),
                                 "AF_PACKET": _try(_socket(socket.AF_PACKET, socket.SOCK_RAW))}
    report["connect"] = {t: _try(_connect(t)) for t in (*allow, *deny)}
    report["expect_connect_allowed"], report["expect_connect_refused"] = list(allow), list(deny)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rw", action="append", default=[], help="a path the unit should be able to write")
    parser.add_argument("--allow", action="append", default=[], help="HOST:PORT the unit should reach")
    parser.add_argument("--deny", action="append", default=[], help="HOST:PORT the unit should not reach")
    a = parser.parse_args(argv)
    print(json.dumps(probe(a.rw, a.allow, a.deny), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
