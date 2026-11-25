#!/usr/bin/env bash
# NeuroSciFi memory models: a text-embedding model and a reranker, Docker, loopback only.
#
# Two ways to serve them, chosen by NSF_MEMORY_SERVER in memory.env; the ports, the key, the endpoints and what the
# individual is told are the same for both:
#   single  one process holds both models (deploy/memory/server.py, in the same pinned image): one container,
#           `neuroscifi-memory`, serving both ports. `embed` and `rerank` are then two names for that one
#           container: starting either starts it (a running one is left alone), stopping either stops it. With
#           NSF_MEMORY_EGRESS=none (the default) it has no network, and `neuroscifi-memory-ports` (forward.py: no
#           model, no key file; keyed calls and their texts pass through it) publishes its ports; the two start
#           and stop together.
#   vllm    two vLLM servers (pooling runner), one container each: twice the memory, vLLM's own batching.
#
#   deploy/memory/run.sh start [embed|rerank|all]   start detached (restart policy unless-stopped)
#   deploy/memory/run.sh restart [embed|rerank|all] stop, then start (single: the way to apply a changed setting)
#   deploy/memory/run.sh foreground embed|rerank    run attached (used by the systemd units; systemd restarts it)
#   deploy/memory/run.sh stop [embed|rerank|all]    stop and remove
#   deploy/memory/run.sh status                     container state and health
#   deploy/memory/run.sh wait [embed|rerank|all] [SECS]   wait until /health answers (default 600 s), then send one
#                                                   real request so the first lookup does not pay the model's
#                                                   first pass (measured: 1.0 to 1.5 s, then about 20 ms)
#   deploy/memory/run.sh logs embed|rerank          follow a container log
#   deploy/memory/run.sh key                        print the key file path (creating the key if missing)
#   deploy/memory/run.sh print embed|rerank         print the docker command (key redacted) and change nothing
#
# Then tell the individual where they are (off until configured). `nsf setup` does it when both answer; by hand:
#   nsf web memory config --enable --key-file ~/.config/neuroscifi/memory/api_key \
#       --embed-url http://127.0.0.1:18441/v1 --embed-model qwen3-embedding-0.6b \
#       --rerank-url http://127.0.0.1:18442/v1 --rerank-model qwen3-reranker-0.6b
# (--key-file is required: a memory model is never sent the answer model's key.)
# As system services: deploy/systemd/neuroscifi-memory.service (single), or neuroscifi-memory-embed.service and
# -rerank.service (vllm; deploy/install.sh --memory).
# Released when idle, if the owner sets a timeout: nsf resources idle --set embed=15m --set rerank=15m.
#
# Security: as deploy/vllm and deploy/voice. Binds 127.0.0.1 only; one generated API key (0600, outside git);
# weights mounted read-only; read-only root filesystem; no capabilities; runs as the invoking user; Hugging Face
# access off; no usage statistics. Docker GPU access is through CDI (--device nvidia.com/gpu=all).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
set -a; . "$HERE/memory.env"; set +a
OVERRIDE="${NSF_MEMORY_DIR:-$HOME/.config/neuroscifi/memory}/memory.env"
if [ -f "$OVERRIDE" ]; then set -a; . "$OVERRIDE"; set +a; fi

die() { echo "run.sh: $*" >&2; exit 1; }
KEY_FILE="$NSF_MEMORY_DIR/api_key"
ENV_FILE="$NSF_MEMORY_DIR/memory.secret.env"

[[ "$NSF_MEMORY_BIND" == "127.0.0.1" || "$NSF_MEMORY_BIND" == "::1" ]] || die "NSF_MEMORY_BIND must be loopback (got $NSF_MEMORY_BIND)"
[[ "$NSF_MEMORY_IMAGE" == *@sha256:* ]] || die "NSF_MEMORY_IMAGE must be pinned by digest"
[[ "${NSF_MEMORY_NETWORK:-}" =~ ^([A-Za-z0-9][A-Za-z0-9_.-]{0,62})?$ ]] || die "NSF_MEMORY_NETWORK must be a docker network name or empty"
case "${NSF_MEMORY_NETWORK:-}" in host|none) die "NSF_MEMORY_NETWORK must not be $NSF_MEMORY_NETWORK (host shares the host's network; none cannot publish the ports)" ;; esac
case "${NSF_MEMORY_SERVER:-}" in single|vllm) ;; *) die "NSF_MEMORY_SERVER must be single or vllm (got ${NSF_MEMORY_SERVER:-nothing})" ;; esac
case "${NSF_MEMORY_EGRESS:-none}" in none|bridge) ;; *) die "NSF_MEMORY_EGRESS must be none or bridge (got $NSF_MEMORY_EGRESS)" ;; esac
NSF_MEMORY_PORTS_CONTAINER="${NSF_MEMORY_PORTS_CONTAINER:-neuroscifi-memory-ports}"
[[ "$NSF_MEMORY_PORTS_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$ ]] || die "NSF_MEMORY_PORTS_CONTAINER must be a container name"
single() { [ "$NSF_MEMORY_SERVER" = single ]; }
# single with NSF_MEMORY_EGRESS=none (the default): the container that holds the models and the key has no network
# at all (`--network none`: no route to anywhere; checked on the reference host, docs/quality/evidence/
# remaining_memory_server.json). It listens on two Unix sockets in SOCKET_DIR; a second container that holds neither
# the models, the device nor the key file (deploy/memory/forward.py) publishes the two ports on loopback and joins
# each connection to its socket. Docker cannot publish a port from a container without a network, nor from an
# `--internal` one, so the forwarder is what makes "no egress" possible for the model server. The forwarder itself
# is on a network with outbound access, and every keyed call crosses it in plain text (the Bearer key and the
# memory texts): it runs only the standard library (`python3 -I -S`), as an unprivileged user, read-only, with no
# capability. bridge: one container, as before.
isolated() { single && [ "${NSF_MEMORY_EGRESS:-none}" = none ]; }
SOCKET_DIR="${NSF_MEMORY_SOCKET_DIR:-$NSF_MEMORY_CACHE_DIR/sockets}"

ensure_key() {
  umask 077
  mkdir -p "$NSF_MEMORY_DIR"
  chmod 700 "$NSF_MEMORY_DIR"
  if [ ! -s "$KEY_FILE" ]; then
    python3 -c 'import secrets; print("nsfm-" + secrets.token_urlsafe(32))' > "$KEY_FILE.tmp"
    mv "$KEY_FILE.tmp" "$KEY_FILE"
  fi
  chmod 600 "$KEY_FILE"
  printf 'VLLM_API_KEY=%s\n' "$(tr -d '\n' < "$KEY_FILE")" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

services() { case "${1:-all}" in embed) echo embed ;; rerank) echo rerank ;; all) echo embed rerank ;; esac; }
known() { case "${1:-all}" in embed|rerank|all) ;; *) die "unknown service: $1 (embed|rerank|all)" ;; esac; }
restarts() { docker inspect -f '{{.RestartCount}}' "$(container "$1")" 2>/dev/null || echo 0; }
container() {
  if single; then echo "$NSF_MEMORY_CONTAINER"
  elif [ "$1" = embed ]; then echo "$NSF_MEMORY_EMBED_CONTAINER"; else echo "$NSF_MEMORY_RERANK_CONTAINER"; fi
}
# The port a service listens on inside its container (single: one process, two ports).
inner_port() { if single && [ "$1" = rerank ]; then echo 8001; else echo 8000; fi; }
# Each container once: single has one for both names (and, isolated, the forwarder that publishes its ports).
containers() {
  if isolated; then echo "$NSF_MEMORY_CONTAINER $NSF_MEMORY_PORTS_CONTAINER"
  elif single; then echo "$NSF_MEMORY_CONTAINER"
  else for s in $(services "${1:-all}"); do container "$s"; done; fi
}
# The container Docker publishes the service's port from.
publisher() { if isolated; then echo "$NSF_MEMORY_PORTS_CONTAINER"; else container "$1"; fi; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
port() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_PORT" || echo "$NSF_MEMORY_RERANK_PORT"; }
model_dir() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_MODEL_DIR" || echo "$NSF_MEMORY_RERANK_MODEL_DIR"; }
served() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_SERVED_NAME" || echo "$NSF_MEMORY_RERANK_SERVED_NAME"; }

# The sockets' folder: the invoking user's only (0700), never a link (the server replaces a stale socket in it and
# refuses anything else at a socket's path).
socket_dir() {
  [ -L "$SOCKET_DIR" ] && die "$SOCKET_DIR is a symbolic link; not used"
  ( umask 077; mkdir -p "$SOCKET_DIR" )
  [ -O "$SOCKET_DIR" ] || die "$SOCKET_DIR is not owned by $(id -un)"
  chmod 700 "$SOCKET_DIR"
}

# One process for both models: the image's own python3 runs server.py (mounted read-only) instead of vLLM. The
# key is a read-only file in the container, not an environment variable (it is in no `docker inspect` output).
# Isolated, PORT_ARGS is the forwarder's container: no device, no model, no key file, the sockets' folder read-only
# (keyed calls still pass through it; see above).
single_args() {
  local embed="$NSF_MEMORY_EMBED_MODEL_DIR" rerank="$NSF_MEMORY_RERANK_MODEL_DIR" health
  local -a where listen
  [ -f "$embed/config.json" ] || die "no embed model at $embed (config.json missing)"
  [ -f "$rerank/config.json" ] || die "no rerank model at $rerank (config.json missing)"
  [ -f "$HERE/server.py" ] || die "deploy/memory/server.py is missing"
  [ "${PRINT_ONLY:-0}" = 1 ] || mkdir -p "$NSF_MEMORY_CACHE_DIR/single"      # `print` creates nothing
  if isolated; then
    [ -f "$HERE/forward.py" ] || die "deploy/memory/forward.py is missing"
    [ "${PRINT_ONLY:-0}" = 1 ] || socket_dir
    where=(--network none -v "$SOCKET_DIR:/run/nsf-memory:rw")
    listen=(--unix-dir /run/nsf-memory)
    health="curl -fsS -m 5 --unix-socket /run/nsf-memory/embed.sock http://localhost/health && curl -fsS -m 5 --unix-socket /run/nsf-memory/rerank.sock http://localhost/health || exit 1"
    # shellcheck disable=SC2046
    PORT_ARGS=(--name "$NSF_MEMORY_PORTS_CONTAINER" $(network_args)
      -p "$NSF_MEMORY_BIND:$NSF_MEMORY_EMBED_PORT:8000" -p "$NSF_MEMORY_BIND:$NSF_MEMORY_RERANK_PORT:8001"
      --user "$(id -u):$(id -g)"
      --read-only --cap-drop ALL --security-opt no-new-privileges
      --cpus 1 --memory 256m --pids-limit 32
      -v "$SOCKET_DIR:/run/nsf-memory:ro" -v "$HERE/forward.py:/opt/neuroscifi/forward.py:ro"
      -e PYTHONDONTWRITEBYTECODE=1 --no-healthcheck
      --entrypoint python3
      "$NSF_MEMORY_IMAGE" -I -S /opt/neuroscifi/forward.py --container --host 0.0.0.0
      --listen 8000=/run/nsf-memory/embed.sock --listen 8001=/run/nsf-memory/rerank.sock)
  else
    # shellcheck disable=SC2207
    where=($(network_args) -p "$NSF_MEMORY_BIND:$NSF_MEMORY_EMBED_PORT:8000" -p "$NSF_MEMORY_BIND:$NSF_MEMORY_RERANK_PORT:8001")
    listen=(--container --host 0.0.0.0 --embed-port 8000 --rerank-port 8001)
    health="curl -fsS -m 5 http://127.0.0.1:8000/health && curl -fsS -m 5 http://127.0.0.1:8001/health || exit 1"
    PORT_ARGS=()
  fi
  ARGS=(--name "$NSF_MEMORY_CONTAINER" "${where[@]}"
    --device nvidia.com/gpu=all
    --user "$(id -u):$(id -g)"
    --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=1g,mode=1777
    --cap-drop ALL --security-opt no-new-privileges
    --cpus "$NSF_MEMORY_CPUS" --memory "$NSF_MEMORY_SINGLE_MEMORY" --shm-size 256m --pids-limit "$NSF_MEMORY_SINGLE_PIDS"
    -v "$embed:/models/$NSF_MEMORY_EMBED_SERVED_NAME:ro" -v "$rerank:/models/$NSF_MEMORY_RERANK_SERVED_NAME:ro"
    -v "$HERE/server.py:/opt/neuroscifi/server.py:ro" -v "$KEY_FILE:/run/neuroscifi/api_key:ro"
    -v "$NSF_MEMORY_CACHE_DIR/single:/cache:rw"
    -e HOME=/cache -e XDG_CACHE_HOME=/cache -e HF_HOME=/cache/hf -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1
    -e DO_NOT_TRACK=1 -e PYTHONDONTWRITEBYTECODE=1 -e TOKENIZERS_PARALLELISM=false
    --health-cmd "$health"
    --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 5m
    --entrypoint python3
    "$NSF_MEMORY_IMAGE" /opt/neuroscifi/server.py "${listen[@]}"
    --embed-model "/models/$NSF_MEMORY_EMBED_SERVED_NAME" --embed-name "$NSF_MEMORY_EMBED_SERVED_NAME"
    --rerank-model "/models/$NSF_MEMORY_RERANK_SERVED_NAME" --rerank-name "$NSF_MEMORY_RERANK_SERVED_NAME"
    --key-file /run/neuroscifi/api_key --max-len "$NSF_MEMORY_MAX_LEN")
}

# The other way of serving holds the same two ports: its containers are removed before this one starts, and
# `stop` and `status` see them too (review F9: after a switch, a container of the other way kept both ports and
# about 4 GiB, and neither command showed it).
# The forwarder of NSF_MEMORY_EGRESS=none holds them too, so it is one of the others whenever it is not in use.
others() {
  if isolated; then echo "$NSF_MEMORY_EMBED_CONTAINER $NSF_MEMORY_RERANK_CONTAINER"
  elif single; then echo "$NSF_MEMORY_EMBED_CONTAINER $NSF_MEMORY_RERANK_CONTAINER $NSF_MEMORY_PORTS_CONTAINER"
  else echo "$NSF_MEMORY_CONTAINER $NSF_MEMORY_PORTS_CONTAINER"; fi
}
other_mode() { if single; then echo vllm; else echo single; fi; }
exists() { docker inspect -f '{{.Name}}' "$1" >/dev/null 2>&1; }
drop_other() {
  local c
  for c in $(others); do docker rm -f "$c" >/dev/null 2>&1 || true; done
}

# A network of the memory containers' own (review L8): a bridge with traffic between its containers off, so no
# other container (SearXNG, a tenant's) can reach them at a container address; the ports are published on loopback
# as before. Docker cannot publish a port from `--network none`, nor from an `--internal` network (checked on the
# reference host: the published port did not answer). So with NSF_MEMORY_EGRESS=none only the forwarder is on this
# network (it holds no key file, but keyed calls and their texts cross it), and the model server is on none; with bridge (and for the vLLM
# pair) the model containers are on it and outbound traffic stays possible, though they make none (offline model
# loading, no telemetry). NSF_MEMORY_NETWORK= (empty) keeps Docker's default bridge.
ensure_network() {
  [ -n "${NSF_MEMORY_NETWORK:-}" ] || return 0
  if docker network inspect "$NSF_MEMORY_NETWORK" >/dev/null 2>&1; then
    # A network of that name that someone else made may let containers reach each other: not used then.
    [ "$(docker network inspect -f '{{index .Options "com.docker.network.bridge.enable_icc"}}' "$NSF_MEMORY_NETWORK" 2>/dev/null)" = false ] \
      || die "the docker network $NSF_MEMORY_NETWORK exists but lets its containers reach each other; remove it (docker network rm $NSF_MEMORY_NETWORK) or name another in NSF_MEMORY_NETWORK"
    return 0
  fi
  docker network create --driver bridge -o com.docker.network.bridge.enable_icc=false \
    --label org.neuroscifi.purpose=memory-models "$NSF_MEMORY_NETWORK" >/dev/null 2>&1 \
    || docker network inspect "$NSF_MEMORY_NETWORK" >/dev/null 2>&1 \
    || die "cannot create the docker network $NSF_MEMORY_NETWORK (NSF_MEMORY_NETWORK= uses the default bridge)"
}
network_args() { if [ -n "${NSF_MEMORY_NETWORK:-}" ]; then echo "--network $NSF_MEMORY_NETWORK"; fi; }

# single: `embed` and `rerank` name one container, and the idle policy may start both at the same moment (two
# reloads after one lookup). One start at a time, and a container that already runs is left alone.
single_start() {
  exec 9>"$NSF_MEMORY_DIR/start.lock"
  flock 9
  if running "$NSF_MEMORY_CONTAINER" && { ! isolated || running "$NSF_MEMORY_PORTS_CONTAINER"; }; then
    echo "$NSF_MEMORY_CONTAINER already runs (embed on $NSF_MEMORY_BIND:$NSF_MEMORY_EMBED_PORT, rerank on $NSF_MEMORY_BIND:$NSF_MEMORY_RERANK_PORT); run.sh restart replaces it"
  else
    single_args; drop_other; ensure_network
    if ! running "$NSF_MEMORY_CONTAINER"; then         # a running model server is kept: only what is missing starts
      docker rm -f "$NSF_MEMORY_CONTAINER" >/dev/null 2>&1 || true
      docker run -d --restart unless-stopped "${ARGS[@]}" >/dev/null
    fi
    if isolated && ! running "$NSF_MEMORY_PORTS_CONTAINER"; then
      docker rm -f "$NSF_MEMORY_PORTS_CONTAINER" >/dev/null 2>&1 || true
      docker run -d --restart unless-stopped "${PORT_ARGS[@]}" >/dev/null
    fi
    echo "started $NSF_MEMORY_CONTAINER: embed on http://$NSF_MEMORY_BIND:$NSF_MEMORY_EMBED_PORT, rerank on http://$NSF_MEMORY_BIND:$NSF_MEMORY_RERANK_PORT (key file $KEY_FILE)$(isolated && echo "; no network: its ports are published by $NSF_MEMORY_PORTS_CONTAINER")"
  fi
  flock -u 9
}

stop_all() {   # $1: embed | rerank | all
  local c
  for c in $(containers "$1"); do
    docker stop -t 20 "$c" >/dev/null 2>&1 || true
    docker rm -f "$c" >/dev/null 2>&1 || true
    echo "stopped $c"
  done
  for c in $(others); do
    exists "$c" || continue
    docker stop -t 20 "$c" >/dev/null 2>&1 || true
    docker rm -f "$c" >/dev/null 2>&1 || true
    echo "removed $c (left by NSF_MEMORY_SERVER=$(other_mode); it held the same ports)"
  done
}

docker_args() {   # $1: embed | rerank
  local s="$1" dir mount
  dir="$(model_dir "$s")"; mount="/models/$(served "$s")"
  [ -f "$dir/config.json" ] || die "no $s model at $dir (config.json missing)"
  mkdir -p "$NSF_MEMORY_CACHE_DIR/$s"
  # shellcheck disable=SC2046
  ARGS=(--name "$(container "$s")" $(network_args) -p "$NSF_MEMORY_BIND:$(port "$s"):8000"
    --device nvidia.com/gpu=all
    --user "$(id -u):$(id -g)"
    --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=2g,mode=1777
    --cap-drop ALL --security-opt no-new-privileges
    --cpus "$NSF_MEMORY_CPUS" --memory "$NSF_MEMORY_MEMORY" --shm-size 1g --pids-limit "$NSF_MEMORY_PIDS"
    -v "$dir:$mount:ro" -v "$NSF_MEMORY_CACHE_DIR/$s:/cache:rw"
    -e HOME=/cache -e XDG_CACHE_HOME=/cache -e HF_HOME=/cache/hf -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1
    -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton
    -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor -e NUMBA_CACHE_DIR=/cache/numba
    --env-file "$ENV_FILE"
    --health-cmd "curl -fsS -m 5 http://127.0.0.1:8000/health || exit 1"
    --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 10m
    "$NSF_MEMORY_IMAGE" "$mount"
    --served-model-name "$(served "$s")"
    --host 0.0.0.0 --port 8000
    --runner pooling
    --gpu-memory-utilization "$NSF_MEMORY_GPU_UTIL"
    --kv-cache-memory-bytes "$NSF_MEMORY_KV_CACHE_BYTES"
    --max-model-len "$NSF_MEMORY_MAX_LEN"
    --max-num-seqs "$NSF_MEMORY_MAX_SEQS"
    --enforce-eager --disable-uvicorn-access-log)
  if [ "$s" = rerank ]; then
    # Qwen3-Reranker is published as a causal LM that answers "yes" or "no"; vLLM serves it as a one-label
    # classifier built from those two tokens (vLLM pooling-models documentation).
    ARGS+=(--hf-overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}')
  fi
}

# One real request to a service that already answers /health. A model's first request after a start is its
# slowest (measured: embedding 1.5 s, reranker 1.05 s, then about 20 ms), and a memory lookup allows each call 2 s.
# The key goes to curl on standard input (-K -), never on a command line that `ps` shows. A failure here does not
# fail the wait: the service is up, and the first lookup falls back to the lexical ranking if it must.
# Whether Docker reports this service's container running and publishing its port on the loopback address. While
# it does, no other process can hold that port, so what answers there is the container. Checked before any request
# that carries the key (review L2: with idle release on, the port is free on a schedule; a process of another
# local account that took it and answered /health was sent the Bearer key by the warm-up request).
owns_port() {   # $1: embed | rerank
  local s="$1" c want
  c="$(publisher "$s")"; want="$NSF_MEMORY_BIND:$(port "$s")"
  running "$c" || return 1
  running "$(container "$s")" || return 1
  docker port "$c" "$(inner_port "$s")/tcp" 2>/dev/null | grep -qxF -e "$want" -e "[$NSF_MEMORY_BIND]:$(port "$s")"
}

warm() {   # $1: embed | rerank
  local s="$1" base body path
  base="http://$NSF_MEMORY_BIND:$(port "$s")/v1"
  if [ "$s" = embed ]; then
    path=embeddings; body="{\"model\":\"$(served "$s")\",\"input\":[\"warm up\"]}"
  else
    path=rerank; body="{\"model\":\"$(served "$s")\",\"query\":\"warm up\",\"documents\":[\"warm up\"]}"
  fi
  [ -s "$KEY_FILE" ] || return 1
  printf 'header = "Authorization: Bearer %s"\n' "$(tr -d '\n' < "$KEY_FILE")" \
    | curl -fsS -m 30 -K - -H 'Content-Type: application/json' -d "$body" "$base/$path" >/dev/null 2>&1
}

# A container that does not exist yet is "not ready", not "gone", for the first seconds of a wait: a systemd unit
# (Type=simple) returns from `systemctl start` before its wrapper has created the container (review F2: the idle
# policy's reload of a stopped unit failed its own wait in 0.04 s while the model came up seconds later).
GONE_GRACE="${NSF_WAIT_GONE_GRACE:-60}"

cmd="${1:-status}"
case "$cmd" in
  start|restart)
    known "${2:-all}"; ensure_key
    [ "$cmd" = restart ] && stop_all "${2:-all}" >/dev/null
    if single; then single_start
    else
      drop_other; ensure_network
      for s in $(services "${2:-all}"); do
        docker_args "$s"
        docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
        docker run -d --restart unless-stopped "${ARGS[@]}" >/dev/null
        echo "started $(container "$s") on http://$NSF_MEMORY_BIND:$(port "$s") (key file $KEY_FILE)"
      done
    fi ;;
  foreground)
    s="${2:-}"
    if single; then
      # One container for both models, so one unit (neuroscifi-memory.service runs `foreground all`). The two
      # per-model units would each replace the other's container: they belong to NSF_MEMORY_SERVER=vllm.
      case "$s" in ""|all) ;; *) die "with NSF_MEMORY_SERVER=single both models run in one container: use neuroscifi-memory.service (foreground all), not the unit of one model" ;; esac
      ensure_key; single_args; drop_other; ensure_network
      if isolated; then
        # The forwarder runs beside the unit's container (removed by `run.sh stop`, the unit's ExecStop).
        docker rm -f "$NSF_MEMORY_PORTS_CONTAINER" >/dev/null 2>&1 || true
        docker run -d --restart unless-stopped "${PORT_ARGS[@]}" >/dev/null
      fi
    else
      [ "$s" = embed ] || [ "$s" = rerank ] || die "foreground needs embed or rerank"
      ensure_key; docker_args "$s"; drop_other; ensure_network
    fi
    docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
    exec docker run --rm --init "${ARGS[@]}" ;;
  stop)
    known "${2:-all}"; stop_all "${2:-all}" ;;
  status)
    for c in $(containers all); do
      # captured first: for a missing container docker prints an empty line before it fails
      if line="$(docker inspect -f '{{.Name}} {{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}}' \
        "$c" 2>/dev/null)"; then echo "$line"; else echo "$c: not running"; fi
    done
    for c in $(others); do
      if line="$(docker inspect -f '{{.Name}} {{.State.Status}}' "$c" 2>/dev/null)"; then
        echo "$line (left by NSF_MEMORY_SERVER=$(other_mode): it holds the same ports; run.sh stop removes it)"
      fi
    done ;;
  wait)
    known "${2:-all}"; limit="${3:-600}"; start=$(date +%s)
    for s in $(services "${2:-all}"); do
      before="$(restarts "$s")"
      until curl -fsS -m 5 "http://$NSF_MEMORY_BIND:$(port "$s")/health" >/dev/null 2>&1; do
        [ $(( $(date +%s) - start )) -ge "$limit" ] && die "$s not ready after ${limit}s"
        if ! state="$(docker inspect -f '{{.State.Status}}' "$(container "$s")" 2>/dev/null)"; then
          [ $(( $(date +%s) - start )) -lt "$GONE_GRACE" ] || die "$s container is gone (none after ${GONE_GRACE}s); see: docker logs $(container "$s")"
          sleep 1; continue
        fi
        case "$state" in restarting|exited|dead) die "$s container is $state; see: docker logs $(container "$s")" ;; esac
        if isolated && [ $(( $(date +%s) - start )) -ge "$GONE_GRACE" ] && ! running "$NSF_MEMORY_PORTS_CONTAINER"; then
          die "$NSF_MEMORY_PORTS_CONTAINER, which publishes the ports, is not running; run.sh restart starts both"
        fi
        [ "$(restarts "$s")" -gt "$before" ] && die "$s restarted while starting; see: docker logs $(container "$s")"
        sleep 3
      done
      owns_port "$s" || die "$s: $NSF_MEMORY_BIND:$(port "$s") answers, but Docker does not report $(container "$s") running and publishing it: another process holds the port. The key was not sent"
      if warm "$s"; then echo "$s ready after $(( $(date +%s) - start ))s"
      else echo "$s ready after $(( $(date +%s) - start ))s (its warm-up request failed; the first lookup will be slower)"; fi
    done ;;
  logs)
    s="${2:-}"; single || [ "$s" = embed ] || [ "$s" = rerank ] || die "logs needs embed or rerank"
    exec docker logs -f "$(container "$s")" ;;
  key) ensure_key; echo "$KEY_FILE" ;;
  print)
    s="${2:-}"; single || [ "$s" = embed ] || [ "$s" = rerank ] || die "print needs embed or rerank"
    ENV_FILE="<NSF_MEMORY_DIR>/memory.secret.env"
    if single; then KEY_FILE="<NSF_MEMORY_DIR>/api_key"; PRINT_ONLY=1 single_args; else docker_args "$s"; fi
    printf 'docker run -d --restart unless-stopped'; printf ' %q' "${ARGS[@]}"; printf '\n'
    if isolated; then
      printf 'docker run -d --restart unless-stopped'; printf ' %q' "${PORT_ARGS[@]}"; printf '\n'
    fi ;;
  *) die "unknown command: $cmd (start|restart|foreground|stop|status|wait|logs|key|print)" ;;
esac
