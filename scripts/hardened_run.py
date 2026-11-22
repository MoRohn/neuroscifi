#!/usr/bin/env python3
"""Run a command as a transient systemd unit under the sandbox of one of deploy/systemd/*.service.

    scripts/hardened_run.py UNIT --name NAME [--user] [--bind-ro PATH] [--bind-rw PATH] [--setenv K=V]
                            [--wait | --service] [--print] -- COMMAND...

The [Service] sandbox and resource directives of deploy/systemd/UNIT.service are passed as `-p` properties, so
the transient unit runs under the same hardening as the installed one (docs/operations/HARDENING_VERIFICATION.md).
Paths are the only difference: ReadWritePaths=%S/neuroscifi becomes the --bind-rw paths, and with --user (a user
manager, no root) the code is not in /opt but in a source tree and a virtualenv, so:
  - User=/Group= are dropped (a user manager runs everything as its user);
  - ProtectHome=yes becomes ProtectHome=tmpfs with the --bind-ro paths (the venv and its interpreter) mounted
    read-only into the empty /home: the same effect (no home directory is visible) except the code;
  - PrivateTmp=yes stays; --bind-ro/--bind-rw paths under /tmp are bind-mounted into the private /tmp.
--wait runs a oneshot and pipes its output here; --service starts a long-running service and returns;
--on-calendar SPEC starts a transient timer (NAME.timer) that runs the command as a oneshot.
--print shows the systemd-run command and runs nothing.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = (
    "NoNewPrivileges", "RemoveIPC", "PrivateUsers", "CapabilityBoundingSet", "AmbientCapabilities", "ProtectSystem",
    "ProtectHome", "PrivateTmp", "PrivateDevices", "PrivateNetwork", "ProtectKernelTunables", "ProtectKernelModules",
    "ProtectKernelLogs", "ProtectControlGroups", "ProtectClock", "ProtectHostname", "ProtectProc", "ProcSubset",
    "RestrictAddressFamilies", "RestrictNamespaces", "RestrictRealtime", "RestrictSUIDSGID", "LockPersonality",
    "MemoryDenyWriteExecute", "SystemCallArchitectures", "SystemCallFilter", "SystemCallErrorNumber",
    "IPAddressAllow", "IPAddressDeny", "MemoryMax", "TasksMax", "UMask", "Nice", "CPUWeight", "IOWeight",
    "IOSchedulingClass", "KeyringMode", "DevicePolicy", "PrivateIPC", "PrivateMounts",
)


def directives(unit: Path) -> list[tuple[str, str]]:
    """(key, value) of the sandbox directives in the [Service] section, in file order (repeated keys kept)."""
    out, section = [], None
    for raw in unit.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            section = line
            continue
        key, sep, value = line.partition("=")
        if section == "[Service]" and sep and key in SANDBOX:
            out.append((key, value))
    return out


def properties(unit: Path, *, user: bool, bind_ro=(), bind_rw=()) -> list[str]:
    props = []
    for key, value in directives(unit):
        if user and key == "ProtectHome" and value == "yes":
            value = "tmpfs"
        if key in ("IPAddressAllow", "IPAddressDeny"):    # systemd-run takes one address (or keyword) per -p
            props += [f"{key}={item}" for item in value.split()]
            continue
        props.append(f"{key}={value}")
    for path in bind_ro:
        props.append(f"BindReadOnlyPaths={path}")
    for path in bind_rw:
        props += [f"BindPaths={path}", f"ReadWritePaths={path}"]
    return props


def command(a) -> list[str]:
    unit = ROOT / "deploy" / "systemd" / (a.unit if a.unit.endswith(".service") else f"{a.unit}.service")
    cmd = ["systemd-run", "--user"] if a.user else ["systemd-run"]
    cmd += ["--unit", a.name, "--collect", "--quiet"]
    if a.wait:
        cmd += ["--wait", "--pipe"]
    elif a.on_calendar:
        cmd += ["--on-calendar", a.on_calendar, "--timer-property", "AccuracySec=1s"]
    else:
        cmd += ["-p", "Type=exec"]
    for prop in properties(unit, user=a.user, bind_ro=a.bind_ro, bind_rw=a.bind_rw):
        cmd += ["-p", prop]
    for prop in a.property:
        cmd += ["-p", prop]
    for env in a.setenv:
        cmd += ["--setenv", env]
    if a.workdir:
        cmd += ["--working-directory", a.workdir]
    return cmd + list(a.command)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("unit", help="unit name in deploy/systemd, e.g. neuroscifi-console")
    p.add_argument("--name", required=True, help="transient unit name, e.g. nsf-test-console")
    p.add_argument("--user", action="store_true", help="the calling user's manager (no root)")
    p.add_argument("--bind-ro", action="append", default=[], metavar="PATH")
    p.add_argument("--bind-rw", action="append", default=[], metavar="PATH")
    p.add_argument("--setenv", action="append", default=[], metavar="K=V")
    p.add_argument("-p", "--property", action="append", default=[], metavar="KEY=VALUE",
                   help="an extra property, e.g. User=neuroscifi (system manager only)")
    p.add_argument("--workdir")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--wait", action="store_true", help="run to completion, output here")
    mode.add_argument("--service", action="store_true", help="start a long-running service and return")
    mode.add_argument("--on-calendar", metavar="SPEC", help="a transient timer running the command as a oneshot")
    p.add_argument("--print", action="store_true", help="print the command only")
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        p.error("a COMMAND is required after --")
    split = argv.index("--")
    a = p.parse_args(argv[:split])
    a.command = argv[split + 1:]
    if not a.command:
        p.error("a COMMAND is required after --")
    cmd = command(a)
    if a.print:
        print(shlex.join(cmd))
        return 0
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
