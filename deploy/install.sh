#!/usr/bin/env bash
# Install NeuroSciFi on a Linux host (for example the NVIDIA DGX Spark under DGX OS).
#
# Creates the system user "neuroscifi", a virtual environment under the prefix, installs the package,
# and copies the systemd units. The units are installed DISABLED and are never started or enabled here.
# Safe to run again: existing users, directories, an existing env file and a root-owned venv are reused;
# a venv not owned by root is refused, and an existing env file is only chmod'ed to 0640.
#
#   sudo bash deploy/install.sh [--dry-run] [--source DIR | --wheel FILE] [--wheelhouse DIR]
#                               [--prefix DIR] [--python PYTHON] [--unit-dir DIR] [--web] [--websearch]
#                               [--searxng] [--llm [--llm-user USER]]
#
# --dry-run     print every action and change nothing; allowed without root so the plan can be reviewed
# --source DIR  project folder to install from (default: the folder above this script)
# --wheel FILE  install this wheel instead of the source
# --wheelhouse DIR  install dependencies only from DIR (offline); default is the package index
# --prefix DIR  install root (default /opt/neuroscifi); the units are rewritten to match
# --python P    interpreter for the venv (default python3.12, then python3); must be 3.12 or newer
# --unit-dir D  where unit files go (default /etc/systemd/system)
# --web         also install the Web Cortex extras (.[web]); the Web Cortex units are installed either way
# --websearch   also install the pinned open-webSearch daemon into PREFIX/open-websearch with
#               `npm ci --ignore-scripts --omit=dev` (needs node and npm; no package install scripts run)
# --searxng     also install neuroscifi-searxng.service (ADR-034) and copy deploy/searxng/settings.yml to
#               PREFIX/searxng/settings.yml (the path the unit names); needs docker. Set User= and NSF_DB in a drop-in.
# --llm         also install neuroscifi-vllm.service (ADR-036): vLLM in docker on 127.0.0.1:8811 through
#               deploy/vllm/run.sh in the source folder (NSF_REPO); needs docker and the weights (COGNITIVE_INFERENCE §4)
# --llm-user U  the account the vLLM unit runs as (default: $SUDO_USER); it must be in the docker group and own the
#               weights and key under its home
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$HERE/.." && pwd)"
WHEEL="" WHEELHOUSE="" PREFIX=/opt/neuroscifi PYTHON="" UNIT_DIR=/etc/systemd/system DRY=0 WEB=0 WEBSEARCH=0
SEARXNG=0 LLM=0 LLM_USER="${SUDO_USER:-}"
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
    --web) WEB=1 ;;
    --websearch) WEBSEARCH=1 ;;
    --searxng) SEARXNG=1 ;;
    --llm) LLM=1 ;;
    --llm-user) [ $# -ge 2 ] || die "--llm-user needs a value"; LLM_USER="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

# PREFIX is spliced into unit files with sed, so allow only plain absolute paths.
[[ "$PREFIX" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "--prefix must match ^/[A-Za-z0-9._/-]+\$ (got: $PREFIX)"
case "$PREFIX/" in */../*) die "--prefix must not contain '..'" ;; esac
if [ "$LLM" -eq 1 ]; then
  # LLM_USER and SOURCE are spliced into the vLLM unit with sed, like PREFIX.
  [[ "$LLM_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--llm needs --llm-user USER (a plain account name; got: '$LLM_USER')"
  [ "$LLM_USER" != root ] || die "--llm-user must not be root"
  [[ "$SOURCE" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "--llm: the source folder path has unexpected characters: $SOURCE"
fi

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
if [ -e "$PREFIX/venv" ] || [ -L "$PREFIX/venv" ]; then
  # The service runs code from this venv, so anyone who owns it can run code as the service.
  [ ! -L "$PREFIX/venv" ] && [ -d "$PREFIX/venv" ] && [ "$(stat -c %u "$PREFIX/venv")" = 0 ] \
    || die "$PREFIX/venv exists but is not a real directory owned by root; refusing to reuse it. Inspect it and remove it yourself."
fi
if [ -x "$PREFIX/venv/bin/python" ]; then echo "venv exists (owned by root); reusing"; else act "$PYTHON" -m venv "$PREFIX/venv"; fi
note "install package from $TARGET"
if [ "$WEB" -eq 1 ]; then
  # pip's extras syntax: a source folder becomes "DIR[web]", a wheel "FILE[web]".
  act "$PREFIX/venv/bin/python" -m pip install "${PIPARGS[@]}" "$TARGET[web]"
else
  act "$PREFIX/venv/bin/python" -m pip install "${PIPARGS[@]}" "$TARGET"
fi

# Node.js for the optional open-webSearch daemon. The unit names /usr/bin/node; rewrite it when node lives elsewhere.
NODE="$(command -v node 2>/dev/null || true)"
[ -z "$NODE" ] || NODE="$(readlink -f "$NODE")"
[[ -z "$NODE" || "$NODE" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "node path has unexpected characters: $NODE"
case "$NODE" in
  /home/*|/root/*|/run/user/*)
    # ProtectHome=yes and DynamicUser hide home directories from the daemon, so such a node cannot run there.
    echo "# warning: node is at $NODE (a home directory); the websearch unit keeps /usr/bin/node. Install a system Node.js."
    NODE="" ;;
esac
if [ "$WEBSEARCH" -eq 1 ]; then
  note "open-webSearch daemon into $PREFIX/open-websearch (pinned lock file, no install scripts)"
  if [ -z "$NODE" ] || ! command -v npm >/dev/null 2>&1; then
    [ "$DRY" -eq 1 ] || die "--websearch needs node and npm on PATH"
    echo "# warning: node or npm not found on this host; a real run would stop here"
  fi
  act install -d -m 0755 "$PREFIX/open-websearch"
  act install -m 0644 "$SOURCE/deploy/open-websearch/package.json" "$SOURCE/deploy/open-websearch/package-lock.json" \
      "$PREFIX/open-websearch/"
  act npm ci --ignore-scripts --omit=dev --no-audit --no-fund --prefix "$PREFIX/open-websearch"
  # The honest User-Agent (ADR-032): replace the scrapers' forged browser strings; `nsf web daemon start` refuses
  # an unpatched build unless the owner opts in with `nsf web config --search-browser-ua`.
  act install -m 0644 "$SOURCE/deploy/open-websearch/apply-honest-ua.mjs" "$PREFIX/open-websearch/"
  act "${NODE:-node}" "$PREFIX/open-websearch/apply-honest-ua.mjs" "$PREFIX/open-websearch"
fi

note "environment file"
if [ -e "$CONF_DIR/neuroscifi.env" ]; then
  echo "$CONF_DIR/neuroscifi.env exists; contents left untouched, mode set to 0640"
  act chmod 0640 "$CONF_DIR/neuroscifi.env"
else
  act install -m 0640 -o root -g "$SVC_USER" "$HERE/neuroscifi.env.example" "$CONF_DIR/neuroscifi.env"
fi

note "systemd units into $UNIT_DIR (installed disabled; never enabled or started by this script)"
NODE_SED="s#/usr/bin/node #${NODE:-/usr/bin/node} #"
# install_unit UNIT [SED_EXPR ...]: the unit with PREFIX (and any extra sed expressions) applied, mode 0644.
install_unit() {
  local unit="$1"; shift
  local args=(-e "s#/opt/neuroscifi#$PREFIX#g")
  local expr
  for expr in "$@"; do args+=(-e "$expr"); done
  if [ "$DRY" -eq 1 ]; then
    printf '+ sed'; printf " -e '%s'" "s#/opt/neuroscifi#$PREFIX#g" "$@"; echo " $HERE/systemd/$unit > $UNIT_DIR/$unit (mode 0644)"
  else
    install -d -m 0755 "$UNIT_DIR"
    sed "${args[@]}" "$HERE/systemd/$unit" > "$UNIT_DIR/$unit.tmp"
    chmod 0644 "$UNIT_DIR/$unit.tmp"
    mv -f "$UNIT_DIR/$unit.tmp" "$UNIT_DIR/$unit"
  fi
}
for unit in neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.service neuroscifi-checkpoint.timer \
            neuroscifi-webcortex.service neuroscifi-webcortex-tick.service neuroscifi-webcortex-tick.timer \
            neuroscifi-websearch.service; do
  install_unit "$unit" "$NODE_SED"
done
if [ "$SEARXNG" -eq 1 ] || [ "$LLM" -eq 1 ]; then
  command -v docker >/dev/null 2>&1 || echo "# warning: docker not found on this host; --searxng/--llm units need it"
fi
if [ "$SEARXNG" -eq 1 ]; then
  note "SearXNG (ADR-034): settings template into $PREFIX/searxng, unit neuroscifi-searxng.service"
  act install -d -m 0755 "$PREFIX/searxng"
  act install -m 0644 "$SOURCE/deploy/searxng/settings.yml" "$PREFIX/searxng/settings.yml"
  install_unit neuroscifi-searxng.service
  echo "# set User= (a docker user) and Environment=NSF_DB=... with: systemctl edit neuroscifi-searxng.service"
fi
if [ "$LLM" -eq 1 ]; then
  note "local model server (ADR-036): unit neuroscifi-vllm.service runs $SOURCE/deploy/vllm/run.sh as $LLM_USER"
  if [ "$DRY" -eq 0 ]; then
    getent passwd "$LLM_USER" >/dev/null || die "--llm-user $LLM_USER does not exist"
    [ -f "$SOURCE/deploy/vllm/run.sh" ] || die "no deploy/vllm/run.sh in $SOURCE"
  fi
  id -nG "$LLM_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker \
    || echo "# warning: $LLM_USER is not in the docker group; the unit's docker run would fail"
  install_unit neuroscifi-vllm.service "s#^User=.*#User=$LLM_USER#" "s#^Environment=NSF_REPO=.*#Environment=NSF_REPO=$SOURCE#"
fi
if [ "$UNIT_DIR" = /etc/systemd/system ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  act systemctl daemon-reload
fi

cat <<MSG

== done$([ "$DRY" -eq 1 ] && echo " (dry run: nothing was changed)")
Nothing is enabled or running. Next steps are in docs/operations/RUNBOOK.md:
  1. create the instance:  sudo -u $SVC_USER NSF_DB=$STATE_DIR/instance.sqlite $PREFIX/venv/bin/nsf init
  2. rehearse on synthetic input, take a checkpoint, then start the services deliberately.
Web Cortex stays off until the owner runs \`nsf web config --enable\`; its units (neuroscifi-webcortex.service,
neuroscifi-webcortex-tick.timer, neuroscifi-websearch.service) are installed disabled like the others.
MSG
[ "$SEARXNG" -eq 0 ] || echo "neuroscifi-searxng.service is installed disabled: set User= and NSF_DB (systemctl edit), then enable it."
[ "$LLM" -eq 0 ] || echo "neuroscifi-vllm.service is installed disabled: enable it after the weights are in place (COGNITIVE_INFERENCE.md §5)."
