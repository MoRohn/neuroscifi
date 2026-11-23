#!/usr/bin/env bash
# NeuroSciFi local model server: vLLM (OpenAI-compatible API) in Docker, loopback only.
#
#   deploy/vllm/run.sh start        start detached (docker restart policy unless-stopped)
#   deploy/vllm/run.sh foreground   run attached (used by neuroscifi-vllm.service; systemd restarts it)
#   deploy/vllm/run.sh stop         stop and remove the container
#   deploy/vllm/run.sh status       container state, health and /v1/models (with the key)
#   deploy/vllm/run.sh wait [SECS]  wait until /health answers (default 1200 s)
#   deploy/vllm/run.sh logs         follow the container log
#   deploy/vllm/run.sh key          print the key file path (creating the key if missing); never prints the key
#   deploy/vllm/run.sh print        print the docker command (key redacted) and change nothing
#
# Settings: deploy/vllm/vllm.env, then ~/.config/neuroscifi/inference/vllm.env (owner overrides, not in git).
# Security: binds 127.0.0.1 only; an API key generated on first use is stored 0600 in NSF_INFERENCE_DIR (outside
# git) and passed through a 0600 env file; model weights are mounted read-only; the root filesystem is read-only;
# no capabilities; runs as the invoking user; Hugging Face access is off (HF_HUB_OFFLINE=1) and vLLM usage
# statistics are disabled. Docker GPU access is through CDI (--device nvidia.com/gpu=all).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
set -a; . "$HERE/vllm.env"; set +a
OVERRIDE="${NSF_INFERENCE_DIR:-$HOME/.config/neuroscifi/inference}/vllm.env"
if [ -f "$OVERRIDE" ]; then set -a; . "$OVERRIDE"; set +a; fi

die() { echo "run.sh: $*" >&2; exit 1; }
KEY_FILE="$NSF_INFERENCE_DIR/api_key"
ENV_FILE="$NSF_INFERENCE_DIR/vllm.secret.env"

[[ "$NSF_VLLM_BIND" == "127.0.0.1" || "$NSF_VLLM_BIND" == "::1" ]] || die "NSF_VLLM_BIND must be loopback (got $NSF_VLLM_BIND)"
[[ "$NSF_VLLM_IMAGE" == *@sha256:* ]] || die "NSF_VLLM_IMAGE must be pinned by digest"
[[ "${NSF_VLLM_SCHEDULING:-fcfs}" == "fcfs" || "$NSF_VLLM_SCHEDULING" == "priority" ]] || die "NSF_VLLM_SCHEDULING must be fcfs or priority (got $NSF_VLLM_SCHEDULING)"
[[ "${NSF_VLLM_BATCHED_TOKENS:-}" =~ ^[0-9]*$ ]] || die "NSF_VLLM_BATCHED_TOKENS must be a number of tokens or empty (got $NSF_VLLM_BATCHED_TOKENS)"

ensure_key() {
  umask 077
  mkdir -p "$NSF_INFERENCE_DIR"
  chmod 700 "$NSF_INFERENCE_DIR"
  if [ ! -s "$KEY_FILE" ]; then
    python3 -c 'import secrets; print("nsf-" + secrets.token_urlsafe(32))' > "$KEY_FILE.tmp"
    mv "$KEY_FILE.tmp" "$KEY_FILE"
  fi
  chmod 600 "$KEY_FILE"
  printf 'VLLM_API_KEY=%s\n' "$(tr -d '\n' < "$KEY_FILE")" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

docker_args() {
  local mount="/models/$NSF_VLLM_SERVED_NAME"
  [ -f "$NSF_VLLM_MODEL_DIR/config.json" ] || die "no model at $NSF_VLLM_MODEL_DIR (config.json missing)"
  mkdir -p "$NSF_VLLM_CACHE_DIR"
  ARGS=(--name "$NSF_VLLM_CONTAINER"
    --device nvidia.com/gpu=all
    -p "$NSF_VLLM_BIND:$NSF_VLLM_PORT:8000"
    --user "$(id -u):$(id -g)"
    --read-only --tmpfs /tmp:rw,size=8g,mode=1777
    --cap-drop ALL --security-opt no-new-privileges
    --cpus "$NSF_VLLM_CPUS" --memory "$NSF_VLLM_MEMORY" --shm-size "$NSF_VLLM_SHM" --pids-limit "$NSF_VLLM_PIDS"
    -v "$NSF_VLLM_MODEL_DIR:$mount:ro"
    -v "$NSF_VLLM_CACHE_DIR:/cache:rw"
    -e HOME=/cache -e XDG_CACHE_HOME=/cache -e HF_HOME=/cache/hf -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1
    -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton
    -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor
    --env-file "$ENV_FILE"
    --health-cmd "curl -fsS -m 5 http://127.0.0.1:8000/health || exit 1"
    --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 20m
    "$NSF_VLLM_IMAGE"
    "$mount"
    --served-model-name "$NSF_VLLM_SERVED_NAME"
    --host 0.0.0.0 --port 8000
    --gpu-memory-utilization "$NSF_VLLM_GPU_UTIL"
    --kv-cache-memory-bytes "$NSF_VLLM_KV_CACHE_BYTES"
    --max-model-len "$NSF_VLLM_MAX_LEN"
    --max-num-seqs "$NSF_VLLM_MAX_SEQS"
    --enable-auto-tool-choice --tool-call-parser "$NSF_VLLM_TOOL_PARSER"
    --enable-prompt-tokens-details
    --disable-uvicorn-access-log)
  [ -n "${NSF_VLLM_SCHEDULING:-}" ] && ARGS+=(--scheduling-policy "$NSF_VLLM_SCHEDULING")
  [ -n "${NSF_VLLM_BATCHED_TOKENS:-}" ] && ARGS+=(--max-num-batched-tokens "$NSF_VLLM_BATCHED_TOKENS")
  # shellcheck disable=SC2206
  [ -n "${NSF_VLLM_EXTRA_ARGS:-}" ] && ARGS+=($NSF_VLLM_EXTRA_ARGS)
  return 0
}

url() { echo "http://$NSF_VLLM_BIND:$NSF_VLLM_PORT"; }

cmd="${1:-status}"
case "$cmd" in
  start)
    ensure_key; docker_args
    docker rm -f "$NSF_VLLM_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --restart unless-stopped "${ARGS[@]}"
    echo "started $NSF_VLLM_CONTAINER on $(url) (key file $KEY_FILE); wait with: $0 wait" ;;
  foreground)
    ensure_key; docker_args
    docker rm -f "$NSF_VLLM_CONTAINER" >/dev/null 2>&1 || true
    exec docker run --rm --init "${ARGS[@]}" ;;
  stop)
    docker stop -t 30 "$NSF_VLLM_CONTAINER" >/dev/null 2>&1 || true
    docker rm -f "$NSF_VLLM_CONTAINER" >/dev/null 2>&1 || true
    echo "stopped $NSF_VLLM_CONTAINER" ;;
  status)
    docker inspect -f '{{.Name}} {{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}}' \
      "$NSF_VLLM_CONTAINER" 2>/dev/null || echo "$NSF_VLLM_CONTAINER: not running"
    if [ -s "$KEY_FILE" ]; then
      curl -fsS -m 5 -H "Authorization: Bearer $(tr -d '\n' < "$KEY_FILE")" "$(url)/v1/models" || echo "models: unavailable"
      echo
    fi ;;
  wait)
    # Fails at once when the container is crash-looping: with --restart unless-stopped it keeps existing, so only
    # its state (restarting / exited / dead) or a growing restart count shows that it will never become healthy.
    limit="${2:-1200}"; start=$(date +%s)
    restarts="$(docker inspect -f '{{.RestartCount}}' "$NSF_VLLM_CONTAINER" 2>/dev/null || echo 0)"
    until curl -fsS -m 5 "$(url)/health" >/dev/null 2>&1; do
      [ $(( $(date +%s) - start )) -ge "$limit" ] && die "not healthy after ${limit}s"
      state="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$NSF_VLLM_CONTAINER" 2>/dev/null)" \
        || die "container exited; see: docker logs $NSF_VLLM_CONTAINER"
      case "${state% *}" in
        restarting|exited|dead) die "container is ${state% *} (it stopped while starting); see: docker logs $NSF_VLLM_CONTAINER" ;;
      esac
      [ "${state#* }" -gt "$restarts" ] && die "container restarted $(( ${state#* } - restarts )) time(s) while starting; see: docker logs $NSF_VLLM_CONTAINER"
      sleep 5
    done
    echo "healthy after $(( $(date +%s) - start ))s" ;;
  logs) exec docker logs -f "$NSF_VLLM_CONTAINER" ;;
  key) ensure_key; echo "$KEY_FILE" ;;
  print)
    ENV_FILE="<NSF_INFERENCE_DIR>/vllm.secret.env"; docker_args
    printf 'docker run -d --restart unless-stopped'; printf ' %q' "${ARGS[@]}"; printf '\n' ;;
  *) die "unknown command: $cmd (start|foreground|stop|status|wait|logs|key|print)" ;;
esac
