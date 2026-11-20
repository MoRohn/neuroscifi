#!/usr/bin/env bash
# Install NeuroSciFi on a Linux host (for example the NVIDIA DGX Spark under DGX OS).
#
# Creates the system user "neuroscifi", a virtual environment under the prefix, installs the package,
# and copies the systemd units. The units are installed DISABLED and are never started or enabled here.
# Safe to run again: existing users, directories, an existing env file and the venv are reused.
#
#   sudo bash deploy/install.sh [--dry-run] [--source DIR | --wheel FILE] [--wheelhouse DIR]
#                               [--prefix DIR] [--python PYTHON] [--unit-dir DIR]
#
# --dry-run     print every action and change nothing; allowed without root so the plan can be reviewed
# --source DIR  project folder to install from (default: the folder above this script)
# --wheel FILE  install this wheel instead of the source
# --wheelhouse DIR  install dependencies only from DIR (offline); default is the package index
# --prefix DIR  install root (default /opt/neuroscifi); the units are rewritten to match
# --python P    interpreter for the venv (default python3.12, then python3); must be 3.12 or newer
# --unit-dir D  where unit files go (default /etc/systemd/system)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$HERE/.." && pwd)"
WHEEL="" WHEELHOUSE="" PREFIX=/opt/neuroscifi PYTHON="" UNIT_DIR=/etc/systemd/system DRY=0
SVC_USER=neuroscifi
STATE_DIR=/var/lib/neuroscifi
CONF_DIR=/etc/neuroscifi

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "install.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --source) [ $# -ge 2 ] || die "--source needs a value"; SOURCE="$2"; shift ;;
    --wheel) [ $# -ge 2 ] || die "--wheel needs a value"; WHEEL="$2"; shift ;;
    --wheelhouse) [ $# -ge 2 ] || die "--wheelhouse needs a value"; WHEELHOUSE="$2"; shift ;;
    --prefix) [ $# -ge 2 ] || die "--prefix needs a value"; PREFIX="$2"; shift ;;
    --python) [ $# -ge 2 ] || die "--python needs a value"; PYTHON="$2"; shift ;;
    --unit-dir) [ $# -ge 2 ] || die "--unit-dir needs a value"; UNIT_DIR="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

if [ "$DRY" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "must run as root (use sudo). Use --dry-run to review the plan without root."
fi
[ "$DRY" -eq 1 ] && echo "# dry run: nothing below is executed"

# Run a command, or print it in a dry run.
act() { if [ "$DRY" -eq 1 ]; then printf '+'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }
note() { echo "== $*"; }

if [ -z "$PYTHON" ]; then
  for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
if [ -n "$PYTHON" ] && command -v "$PYTHON" >/dev/null 2>&1; then
  "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "$PYTHON is older than Python 3.12"
elif [ "$DRY" -eq 0 ]; then
  die "no Python 3.12 or newer found; install python3.12 or pass --python"
else
  echo "# warning: no python3.12 found on this host; a real run would stop here"
  PYTHON=python3.12
fi

if [ -n "$WHEEL" ]; then
  [ "$DRY" -eq 1 ] || [ -f "$WHEEL" ] || die "wheel not found: $WHEEL"
  TARGET="$WHEEL"
else
  [ "$DRY" -eq 1 ] || [ -f "$SOURCE/pyproject.toml" ] || die "no pyproject.toml in $SOURCE"
  TARGET="$SOURCE"
fi
PIPARGS=(--disable-pip-version-check)
[ -z "$WHEELHOUSE" ] || PIPARGS+=(--no-index --find-links "$WHEELHOUSE")

note "service user $SVC_USER"
if getent passwd "$SVC_USER" >/dev/null; then
  echo "user $SVC_USER exists; reusing"
else
  act useradd --system --user-group --no-create-home --home-dir "$STATE_DIR" \
      --shell /usr/sbin/nologin --comment "NeuroSciFi service" "$SVC_USER"
fi

note "directories"
act install -d -m 0755 "$PREFIX"
act install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$STATE_DIR"
act install -d -m 0750 -o root -g "$SVC_USER" "$CONF_DIR"
act install -d -m 0750 -o root -g "$SVC_USER" "$CONF_DIR/tls"

note "virtual environment $PREFIX/venv (owned by root; the service cannot modify its own code)"
if [ -x "$PREFIX/venv/bin/python" ]; then echo "venv exists; reusing"; else act "$PYTHON" -m venv "$PREFIX/venv"; fi
note "install package from $TARGET"
act "$PREFIX/venv/bin/python" -m pip install "${PIPARGS[@]}" "$TARGET"

note "environment file"
if [ -e "$CONF_DIR/neuroscifi.env" ]; then
  echo "$CONF_DIR/neuroscifi.env exists; left untouched"
else
  act install -m 0640 -o root -g "$SVC_USER" "$HERE/neuroscifi.env.example" "$CONF_DIR/neuroscifi.env"
fi

note "systemd units into $UNIT_DIR (installed disabled; never enabled or started by this script)"
for unit in neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.service neuroscifi-checkpoint.timer; do
  if [ "$DRY" -eq 1 ]; then
    echo "+ sed 's#/opt/neuroscifi#$PREFIX#g' $HERE/systemd/$unit > $UNIT_DIR/$unit (mode 0644)"
  else
    install -d -m 0755 "$UNIT_DIR"
    sed "s#/opt/neuroscifi#$PREFIX#g" "$HERE/systemd/$unit" > "$UNIT_DIR/$unit.tmp"
    chmod 0644 "$UNIT_DIR/$unit.tmp"
    mv -f "$UNIT_DIR/$unit.tmp" "$UNIT_DIR/$unit"
  fi
done
if [ "$UNIT_DIR" = /etc/systemd/system ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  act systemctl daemon-reload
fi

cat <<MSG

== done$([ "$DRY" -eq 1 ] && echo " (dry run: nothing was changed)")
Nothing is enabled or running. Next steps are in docs/operations/RUNBOOK.md:
  1. create the instance:  sudo -u $SVC_USER NSF_DB=$STATE_DIR/instance.sqlite $PREFIX/venv/bin/nsf init
  2. rehearse on synthetic input, take a checkpoint, then start the services deliberately.
MSG
