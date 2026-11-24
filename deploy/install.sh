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
#                               [--memory [--memory-user USER] [--memory-server single|vllm]]
#                               [--idle [--idle-user USER] [--db FILE]]
#   sudo bash deploy/install.sh --idle-access [--idle-user USER] [--db FILE] [--dry-run]
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
# --memory      also install the memory models' unit (memory search by meaning; MEMORY_INTEGRATION.md §5.1), in
#               docker on 127.0.0.1:18441 and :18442 through deploy/memory/run.sh in the source folder (NSF_REPO);
#               needs docker and the weights. Which unit follows NSF_MEMORY_SERVER of deploy/memory/memory.env:
#               single (the default) installs neuroscifi-memory.service, one server for both models; vllm installs
#               neuroscifi-memory-embed.service and neuroscifi-memory-rerank.service
# --memory-server single|vllm  overrides that setting for the units installed
# --memory-user U  the account the memory units run as (default: --llm-user, then $SUDO_USER); same conditions
# --idle        also install the idle policy's units (RESOURCE_SCHEDULING.md §3c): neuroscifi-idle.service and .timer
#               (the pass that unloads a model service nobody uses; every timeout is off until the owner sets one)
#               and neuroscifi-wake.service and .path (reload on request, for the console and the worker, which may
#               not start services themselves). Creates the request folder /var/lib/neuroscifi-wake (owner: the idle
#               account, group: neuroscifi, mode 2770, so the services may leave a request and nothing else) and
#               lets the console, worker and Pocket units write there. Then arranges what the two accounts share
#               (--idle-access, below). Set NSF_DB in a drop-in for both services and NSF_WAKE_DIR in
#               /etc/neuroscifi/neuroscifi.env (the closing message says how).
# --idle-user U  the account those units run as (default: --llm-user, then --memory-user, then $SUDO_USER): the one
#               that runs the model services; it must be in the docker group
# --idle-access  only arrange what the service account and the idle account share, and stop (no package, no units):
#               run it again after `nsf init`, because the database does not exist before. It
#                 - adds the idle account to the group neuroscifi (it can then read /etc/neuroscifi as well; it is in
#                   the docker group, which already equals root on this host);
#                 - lets that group enter /var/lib/neuroscifi without listing it (0700 -> 0710);
#                 - lets it read the instance database (0600 -> 0640, with its -wal and -shm), which the idle pass
#                   needs to see whether a research task runs; nothing else in that folder changes;
#                 - makes FILE.resources (the audio beacon, the use markers, the idle policy's state, events and
#                   locks) a folder both write: setgid, 2770, the files in it group-writable;
#                 - installs a polkit rule that lets the idle account start and stop the model services' units, and
#                   nothing else (the idle pass and the wake unit do it without a password).
#               An existing folder or file keeps its owner; only the modes and groups named here change.
# --db FILE     the instance database those steps are about (default /var/lib/neuroscifi/instance.sqlite)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$HERE/.." && pwd)"
WHEEL="" WHEELHOUSE="" PREFIX=/opt/neuroscifi PYTHON="" UNIT_DIR=/etc/systemd/system DRY=0 WEB=0 WEBSEARCH=0
SEARXNG=0 LLM=0 LLM_USER="${SUDO_USER:-}" MEMORY=0 MEMORY_USER="" MEMORY_SERVER="" IDLE=0 IDLE_USER="" ACCESS_ONLY=0
IDLE_DB=""
SVC_USER=neuroscifi
STATE_DIR=/var/lib/neuroscifi
WAKE_DIR=/var/lib/neuroscifi-wake      # beside the state folder, not inside it: the service account owns that one
CONF_DIR=/etc/neuroscifi
POLKIT_DIR=/etc/polkit-1/rules.d
POLKIT_RULE=49-neuroscifi-idle.rules
# The units the idle policy stops and starts (resources/idle.py SERVICES; tests/test_deployment_accounts.py checks
# them). The memory models are one unit or two depending on NSF_MEMORY_SERVER; the rule names all three, so a later
# change of that setting does not need the rule rewritten (start and stop only, on units that exist or not).
IDLE_UNITS="neuroscifi-vllm.service neuroscifi-voice-asr.service neuroscifi-voice-tts.service neuroscifi-searxng.service neuroscifi-memory.service neuroscifi-memory-embed.service neuroscifi-memory-rerank.service"

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
    --memory) MEMORY=1 ;;
    --memory-user) [ $# -ge 2 ] || die "--memory-user needs a value"; MEMORY_USER="$2"; shift ;;
    --memory-server) [ $# -ge 2 ] || die "--memory-server needs a value"; MEMORY_SERVER="$2"; shift ;;
    --idle) IDLE=1 ;;
    --idle-user) [ $# -ge 2 ] || die "--idle-user needs a value"; IDLE_USER="$2"; shift ;;
    --idle-access) ACCESS_ONLY=1 ;;
    --db) [ $# -ge 2 ] || die "--db needs a value"; IDLE_DB="$2"; shift ;;
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

if [ "$MEMORY" -eq 1 ]; then
  # MEMORY_USER and SOURCE are spliced into the memory units with sed, like PREFIX.
  MEMORY_USER="${MEMORY_USER:-$LLM_USER}"
  [[ "$MEMORY_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--memory needs --memory-user USER (a plain account name; got: '$MEMORY_USER')"
  [ "$MEMORY_USER" != root ] || die "--memory-user must not be root"
  [[ "$SOURCE" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "--memory: the source folder path has unexpected characters: $SOURCE"
  if [ -z "$MEMORY_SERVER" ]; then
    # The setting as run.sh resolves it for MEMORY_USER (review F10: the owner's override was ignored, and the unit
    # of the other way then refused to start): the shipped memory.env, then the override in MEMORY_USER's home.
    # Both are read as data, never sourced, and only the forms `NSF_MEMORY_SERVER=x` (optionally `export`ed or
    # quoted) count. The override is read only if it is a plain file owned by MEMORY_USER (this runs as root).
    server_in() {
      sed -nE "s/^[[:space:]]*(export[[:space:]]+)?NSF_MEMORY_SERVER=[\"']?([a-z]*)[\"']?[[:space:]]*(#.*)?$/\\2/p" "$1" 2>/dev/null | tail -n 1
    }
    MEMORY_SERVER="$(server_in "$SOURCE/deploy/memory/memory.env")"
    MEMORY_HOME="$(getent passwd "$MEMORY_USER" 2>/dev/null | cut -d: -f6 || true)"   # an unknown account: none
    OVERRIDE_ENV="$MEMORY_HOME/.config/neuroscifi/memory/memory.env"
    if [ -n "$MEMORY_HOME" ] && [ -f "$OVERRIDE_ENV" ] && [ ! -L "$OVERRIDE_ENV" ]; then
      if [ "$(stat -c %U "$OVERRIDE_ENV" 2>/dev/null)" = "$MEMORY_USER" ]; then
        OWNER_SERVER="$(server_in "$OVERRIDE_ENV")"
        MEMORY_SERVER="${OWNER_SERVER:-$MEMORY_SERVER}"
      else
        echo "# warning: $OVERRIDE_ENV is not owned by $MEMORY_USER; its NSF_MEMORY_SERVER is not read"
      fi
    fi
    MEMORY_SERVER="${MEMORY_SERVER:-single}"
  fi
  case "$MEMORY_SERVER" in single|vllm) ;; *) die "--memory-server must be single or vllm (got: '$MEMORY_SERVER')" ;; esac
fi

if [ "$IDLE" -eq 1 ] || [ "$ACCESS_ONLY" -eq 1 ]; then
  # IDLE_USER, SOURCE and WAKE_DIR are spliced into the idle and wake units with sed, like PREFIX; IDLE_USER also
  # into the polkit rule.
  IDLE_USER="${IDLE_USER:-${LLM_USER:-$MEMORY_USER}}"
  [[ "$IDLE_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--idle needs --idle-user USER (a plain account name; got: '$IDLE_USER')"
  [ "$IDLE_USER" != root ] || die "--idle-user must not be root"
  [ "$IDLE_USER" != "$SVC_USER" ] || die "--idle-user must not be $SVC_USER: that account has no Docker access; name the one that runs the model services"
  [[ "$SOURCE" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "--idle: the source folder path has unexpected characters: $SOURCE"
  IDLE_DB="${IDLE_DB:-$STATE_DIR/instance.sqlite}"
  [[ "$IDLE_DB" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "--db must be a plain absolute path (got: $IDLE_DB)"
  case "$IDLE_DB/" in */../*) die "--db must not contain '..'" ;; esac
  case "$IDLE_DB" in "$STATE_DIR"/*) ;; *) die "--db must be a file in $STATE_DIR (got: $IDLE_DB): the two accounts share that folder's entrance and nothing above it" ;; esac
elif [ -n "$IDLE_DB" ]; then
  die "--db goes with --idle or --idle-access"
fi

if [ "$DRY" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "must run as root (use sudo). Use --dry-run to review the plan without root."
fi
[ "$DRY" -eq 1 ] && echo "# dry run: nothing below is executed"

# Run a command, or print it in a dry run.
act() { if [ "$DRY" -eq 1 ]; then printf '+'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }
note() { echo "== $*"; }

# What the service account and the idle account share (RESOURCE_SCHEDULING.md §3d). Idempotent; an existing folder
# or file keeps its owner, and nothing but the modes and groups named here is changed. $1: "units" also writes the
# polkit rule (it names units, so it is only useful once they are installed).
idle_access() {
  local resources="$IDLE_DB.resources" file
  note "what $SVC_USER (console, worker, Pocket) and $IDLE_USER (idle pass, wake requests) share"
  if [ "$DRY" -eq 0 ]; then
    getent passwd "$SVC_USER" >/dev/null || die "the service account $SVC_USER does not exist: run the installer without --idle-access first"
    getent passwd "$IDLE_USER" >/dev/null || die "--idle-user $IDLE_USER does not exist"
    [ -d "$STATE_DIR" ] || die "$STATE_DIR does not exist: run the installer without --idle-access first"
  fi
  # One group for both: the service account's own. Its units run with PrivateUsers=yes, where only the unit's own
  # user and group are certain to keep their meaning, so what the idle account writes for them carries that group.
  if id -nG "$IDLE_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$SVC_USER"; then
    echo "$IDLE_USER is in the group $SVC_USER already"
  else
    act gpasswd -a "$IDLE_USER" "$SVC_USER"
    echo "# $IDLE_USER can now read $CONF_DIR too (root:$SVC_USER 0750). It is in the docker group, which equals root here."
  fi
  # Everything inside the state folder is done AS $SVC_USER, never as root (review F1). That folder is
  # $SVC_USER's: whatever it holds may be a symbolic link or a hard link to anything on the host, swapped in at
  # any moment, and root's chgrp/chmod would follow it (/etc/shadow, /etc). Run as $SVC_USER, every step is one
  # that account could take anyway, and the kernel refuses the rest: no check here has to win a race. The steps:
  # the entrance (the group may pass through, not list); the database and its write-ahead log's files readable
  # by the group, for the one question "does a research task run" (SQLite gives files it creates later the
  # database's mode); the folder both write, setgid so that what the idle account creates belongs to the group
  # (the code makes its own files group-writable in such a folder and private elsewhere).
  # $STATE_DIR itself is named by root's /var/lib, which $SVC_USER cannot change, so its path is the real folder.
  local as_service=(setpriv --reuid="$SVC_USER" --regid="$SVC_USER" --init-groups --reset-env)
  command -v setpriv >/dev/null 2>&1 || as_service=(runuser -u "$SVC_USER" --)
  # shellcheck disable=SC2016  # the script is for the inner bash; its $1..$4 are its arguments
  local inside='set -u
state="$1" db="$2" group="$3" dry="$4" resources="$2.resources" failed=0
step() { if [ "$dry" = 1 ]; then printf "+ (as %s)" "$group"; printf " %q" "$@"; printf "\n";
         elif ! "$@"; then failed=1; fi; }
plain() {   # $1 must be a plain file or folder of this account, not a link: say so and leave it otherwise
  if [ -L "$1" ]; then echo "refused: $1 is a symbolic link; nothing is done to it or to where it points" >&2; failed=1; return 1; fi
  if [ -e "$1" ] && [ "$(stat -c %u "$1")" != "$(id -u)" ]; then echo "refused: $1 does not belong to $(id -un)" >&2; failed=1; return 1; fi
  return 0
}
step chmod 0710 "$state"
if [ -e "$db" ] || [ -L "$db" ] || [ "$dry" = 1 ]; then
  for f in "$db" "$db-wal" "$db-shm"; do
    if [ -e "$f" ] || [ -L "$f" ] || { [ "$dry" = 1 ] && [ "$f" = "$db" ]; }; then
      plain "$f" && { step chgrp "$group" "$f"; step chmod 0640 "$f"; }
    fi
  done
else
  echo "# $db does not exist yet: create the individual (nsf init), then run this again with --idle-access."
  echo "# Until then the idle pass unloads nothing and says why (nsf resources tick)."
fi
if [ -e "$resources" ] || [ -L "$resources" ]; then
  if plain "$resources"; then
    step chgrp "$group" "$resources"
    step chmod 2770 "$resources"
    if [ "$dry" = 1 ]; then
      echo "+ (as the service account) chgrp $group and chmod g+rw: every plain file directly in $resources"
    else
      find "$resources" -maxdepth 1 -type f -user "$(id -u)" -exec chgrp "$group" {} + -exec chmod g+rw {} + || failed=1
    fi
  fi
else
  step mkdir -m 2770 "$resources"
  step chgrp "$group" "$resources"
  step chmod 2770 "$resources"
fi
exit "$failed"'
  if [ "$DRY" -eq 1 ]; then
    bash -c "$inside" inside "$STATE_DIR" "$IDLE_DB" "$SVC_USER" 1
  elif ! "${as_service[@]}" bash -c "$inside" inside "$STATE_DIR" "$IDLE_DB" "$SVC_USER" 0; then
    die "a step inside $STATE_DIR was refused (above); nothing there was done as root. Look at what $SVC_USER keeps there."
  fi
  # The request folder: the idle account owns it (it removes the requests), the service account's group may create
  # files in it, nobody else may enter. Sticky: a request can be removed by whoever left it and by the folder's
  # owner only, so one service cannot take away another's. An existing folder is left as it is (install -d would
  # reset its mode and owner): the owner may have chosen otherwise. Root acts on this path safely: it is named in
  # root's /var/lib, where neither account can create or swap an entry. The other root actions of this function are
  # as plain: gpasswd edits root's /etc/group, and the polkit rule is written under root's /etc.
  if [ -d "$WAKE_DIR" ]; then
    echo "$WAKE_DIR exists; left as it is ($(stat -c '%U:%G %a' "$WAKE_DIR"))"
  else
    act install -d -m 3770 -o "$IDLE_USER" -g "$SVC_USER" "$WAKE_DIR"
  fi
  if [ "${1:-}" = units ]; then
    # Stopping and starting a system unit needs authorisation; the idle pass and the wake unit run without a
    # terminal (systemctl --no-ask-password). The rule allows this one account these verbs on these units.
    note "polkit rule $POLKIT_DIR/$POLKIT_RULE: $IDLE_USER may start and stop the model services' units"
    local list="" unit
    for unit in $IDLE_UNITS; do list="$list\"$unit\", "; done
    local rule
    rule="// NeuroSciFi (deploy/install.sh --idle): the idle policy's account may start and stop the model services.
polkit.addRule(function(action, subject) {
    var units = [${list%, }];
    var verbs = [\"start\", \"stop\"];
    if (action.id == \"org.freedesktop.systemd1.manage-units\" && subject.user == \"$IDLE_USER\" &&
            units.indexOf(action.lookup(\"unit\")) >= 0 && verbs.indexOf(action.lookup(\"verb\")) >= 0) {
        return polkit.Result.YES;
    }
});"
    if [ "$DRY" -eq 1 ]; then
      echo "+ write $POLKIT_DIR/$POLKIT_RULE (mode 0644):"; printf '%s\n' "$rule" | sed 's/^/    /'
    elif [ -d "$(dirname "$POLKIT_DIR")" ]; then
      install -d -m 0755 "$POLKIT_DIR"
      printf '%s\n' "$rule" > "$POLKIT_DIR/$POLKIT_RULE.tmp"
      chmod 0644 "$POLKIT_DIR/$POLKIT_RULE.tmp"
      mv -f "$POLKIT_DIR/$POLKIT_RULE.tmp" "$POLKIT_DIR/$POLKIT_RULE"
    else
      echo "# warning: no $(dirname "$POLKIT_DIR") on this host (polkit is not installed): $IDLE_USER cannot stop or start a"
      echo "# system unit without it; the idle pass then records each failed unload and unloads nothing."
    fi
  fi
}

if [ "$ACCESS_ONLY" -eq 1 ]; then
  idle_access units
  cat <<MSG

== done$([ "$DRY" -eq 1 ] && echo " (dry run: nothing was changed)")
$IDLE_USER reads $IDLE_DB and shares $IDLE_DB.resources with $SVC_USER.
A session of $IDLE_USER that is already open keeps its old groups; the units read them when they next start.
MSG
  exit 0
fi

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
# A two-account arrangement made earlier (--idle / --idle-access: the request folder or the polkit rule exists) is
# kept by a later run without --idle, an upgrade for example (review F7): the state folder keeps the entrance the
# idle account needs (0710 instead of 0700), and the console, worker and Pocket units keep the request folder among
# their writable paths. To take the arrangement away, see RUNBOOK.md section 1, "The two accounts", step 6.
ARRANGED=0
if [ "$IDLE" -eq 0 ] && { [ -d "$WAKE_DIR" ] || [ -e "$POLKIT_DIR/$POLKIT_RULE" ]; }; then
  ARRANGED=1
  echo "# a two-account arrangement is installed ($WAKE_DIR or $POLKIT_DIR/$POLKIT_RULE exists): it is kept"
fi
STATE_MODE=0700
if [ "$IDLE" -eq 1 ] || [ "$ARRANGED" -eq 1 ]; then STATE_MODE=0710; fi
act install -d -m "$STATE_MODE" -o "$SVC_USER" -g "$SVC_USER" "$STATE_DIR"
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
# With --idle the units that call the model services may also write the wake-request folder (they run under
# ProtectSystem=strict, so every writable path is named). "-": a folder that is missing is not an error.
WAKE_SED="s#^ReadWritePaths=%S/neuroscifi\$#ReadWritePaths=%S/neuroscifi -$WAKE_DIR#"
for unit in neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.service neuroscifi-checkpoint.timer \
            neuroscifi-webcortex.service neuroscifi-webcortex-tick.service neuroscifi-webcortex-tick.timer \
            neuroscifi-websearch.service; do
  case "$(( IDLE | ARRANGED )):$unit" in
    1:neuroscifi-pocket.service|1:neuroscifi-console.service|1:neuroscifi-webcortex.service)
      install_unit "$unit" "$NODE_SED" "$WAKE_SED" ;;
    *) install_unit "$unit" "$NODE_SED" ;;
  esac
done
if [ "$SEARXNG" -eq 1 ] || [ "$LLM" -eq 1 ] || [ "$MEMORY" -eq 1 ]; then
  command -v docker >/dev/null 2>&1 || echo "# warning: docker not found on this host; --searxng/--llm/--memory units need it"
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
if [ "$MEMORY" -eq 1 ]; then
  if [ "$MEMORY_SERVER" = single ]; then MEMORY_UNITS="neuroscifi-memory.service"
  else MEMORY_UNITS="neuroscifi-memory-embed.service neuroscifi-memory-rerank.service"; fi
  note "memory models (MEMORY_INTEGRATION.md §5.1, NSF_MEMORY_SERVER=$MEMORY_SERVER): $MEMORY_UNITS run $SOURCE/deploy/memory/run.sh as $MEMORY_USER"
  if [ "$DRY" -eq 0 ]; then
    getent passwd "$MEMORY_USER" >/dev/null || die "--memory-user $MEMORY_USER does not exist"
    [ -f "$SOURCE/deploy/memory/run.sh" ] || die "no deploy/memory/run.sh in $SOURCE"
  fi
  id -nG "$MEMORY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker \
    || echo "# warning: $MEMORY_USER is not in the docker group; the units' docker run would fail"
  for unit in $MEMORY_UNITS; do
    install_unit "$unit" "s#^User=.*#User=$MEMORY_USER#" "s#^Environment=NSF_REPO=.*#Environment=NSF_REPO=$SOURCE#"
  done
fi
if [ "$IDLE" -eq 1 ]; then
  note "idle policy (RESOURCE_SCHEDULING.md §3c): units neuroscifi-idle.service/.timer and neuroscifi-wake.service/.path run $SOURCE/.venv/bin/nsf as $IDLE_USER"
  if [ "$DRY" -eq 0 ]; then
    getent passwd "$IDLE_USER" >/dev/null || die "--idle-user $IDLE_USER does not exist"
    [ -x "$SOURCE/.venv/bin/nsf" ] || echo "# warning: no $SOURCE/.venv/bin/nsf yet; the idle and wake units run it"
  fi
  id -nG "$IDLE_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker \
    || echo "# warning: $IDLE_USER is not in the docker group; stopping and starting the model services would fail"
  IDLE_SED=("s#@NSF_USER@#$IDLE_USER#g" "s#@NSF_REPO@#$SOURCE#g" "s#@NSF_WAKE_DIR@#$WAKE_DIR#g")
  for unit in neuroscifi-idle.service neuroscifi-idle.timer neuroscifi-wake.service neuroscifi-wake.path; do
    install_unit "$unit" "${IDLE_SED[@]}"
  done
  idle_access units
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
if [ "$IDLE" -eq 1 ]; then
  cat <<MSG
The idle policy's units are installed disabled and do nothing until an idle timeout is set. To use them:
  1. name the individual for both services:  systemctl edit neuroscifi-idle.service neuroscifi-wake.service
       [Service]
       Environment=NSF_DB=$IDLE_DB
  2. let the console, the worker and Pocket ask for a reload: add  NSF_WAKE_DIR=$WAKE_DIR  to $CONF_DIR/neuroscifi.env
     and restart them
  3. systemctl enable --now neuroscifi-idle.timer neuroscifi-wake.path
  4. set a timeout as $IDLE_USER:  nsf resources idle --db $IDLE_DB --set vllm=30m   (RUNBOOK.md, "Idle unloading")
$IDLE_USER now reads $IDLE_DB and shares $IDLE_DB.resources with $SVC_USER (RUNBOOK.md, "The two accounts").
$([ -f "$IDLE_DB" ] || echo "The database does not exist yet: after \`nsf init\`, run  sudo bash deploy/install.sh --idle-access --idle-user $IDLE_USER")
MSG
fi
[ "$MEMORY" -eq 0 ] || echo "$MEMORY_UNITS installed disabled: enable after the weights are in place (RUNBOOK.md, memory search), then run \`nsf setup\` once so the individual uses them. Never enable both kinds: they hold the same two ports."
