#!/usr/bin/env bash
# NeuroSciFi memory models: a text-embedding model and a reranker (vLLM pooling runner), Docker, loopback only.
#
#   deploy/memory/run.sh start [embed|rerank|all]   start detached (restart policy unless-stopped)
#   deploy/memory/run.sh stop [embed|rerank|all]    stop and remove
#   deploy/memory/run.sh status                     container state and health
#   deploy/memory/run.sh wait [embed|rerank|all] [SECS]   wait until /health answers (default 600 s)
#   deploy/memory/run.sh logs embed|rerank          follow a container log
#   deploy/memory/run.sh key                        print the key file path (creating the key if missing)
#   deploy/memory/run.sh print embed|rerank         print the docker command (key redacted) and change nothing
#
# Then tell the individual where they are (off until configured):
#   nsf web memory config --embed-url http://127.0.0.1:18441/v1 --rerank-url http://127.0.0.1:18442/v1
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

services() { case "${1:-all}" in embed) echo embed ;; rerank) echo rerank ;; all) echo embed rerank ;; *) die "unknown service: $1" ;; esac; }
container() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_CONTAINER" || echo "$NSF_MEMORY_RERANK_CONTAINER"; }
port() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_PORT" || echo "$NSF_MEMORY_RERANK_PORT"; }
model_dir() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_MODEL_DIR" || echo "$NSF_MEMORY_RERANK_MODEL_DIR"; }
served() { [ "$1" = embed ] && echo "$NSF_MEMORY_EMBED_SERVED_NAME" || echo "$NSF_MEMORY_RERANK_SERVED_NAME"; }

docker_args() {   # $1: embed | rerank
  local s="$1" dir mount
  dir="$(model_dir "$s")"; mount="/models/$(served "$s")"
  [ -f "$dir/config.json" ] || die "no $s model at $dir (config.json missing)"
  mkdir -p "$NSF_MEMORY_CACHE_DIR/$s"
  ARGS=(--name "$(container "$s")" -p "$NSF_MEMORY_BIND:$(port "$s"):8000"
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

cmd="${1:-status}"
case "$cmd" in
  start)
    ensure_key
    for s in $(services "${2:-all}"); do
      docker_args "$s"
      docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
      docker run -d --restart unless-stopped "${ARGS[@]}" >/dev/null
      echo "started $(container "$s") on http://$NSF_MEMORY_BIND:$(port "$s") (key file $KEY_FILE)"
    done ;;
  stop)
    for s in $(services "${2:-all}"); do
      docker stop -t 20 "$(container "$s")" >/dev/null 2>&1 || true
      docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
      echo "stopped $(container "$s")"
    done ;;
  status)
    for s in embed rerank; do
      docker inspect -f '{{.Name}} {{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}}' \
        "$(container "$s")" 2>/dev/null || echo "$(container "$s"): not running"
    done ;;
  wait)
    limit="${3:-600}"; start=$(date +%s)
    for s in $(services "${2:-all}"); do
      until curl -fsS -m 5 "http://$NSF_MEMORY_BIND:$(port "$s")/health" >/dev/null 2>&1; do
        [ $(( $(date +%s) - start )) -ge "$limit" ] && die "$s not ready after ${limit}s"
        state="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$(container "$s")" 2>/dev/null)" \
          || die "$s container exited; see: docker logs $(container "$s")"
        [[ "$state" == restarting* || "${state##* }" -gt 2 ]] && die "$s keeps restarting; see: docker logs $(container "$s")"
        sleep 3
      done
      echo "$s ready after $(( $(date +%s) - start ))s"
    done ;;
  logs)
    s="${2:-}"; [ "$s" = embed ] || [ "$s" = rerank ] || die "logs needs embed or rerank"
    exec docker logs -f "$(container "$s")" ;;
  key) ensure_key; echo "$KEY_FILE" ;;
  print)
    s="${2:-}"; [ "$s" = embed ] || [ "$s" = rerank ] || die "print needs embed or rerank"
    ENV_FILE="<NSF_MEMORY_DIR>/memory.secret.env"; docker_args "$s"
    printf 'docker run -d --restart unless-stopped'; printf ' %q' "${ARGS[@]}"; printf '\n' ;;
  *) die "unknown command: $cmd (start|stop|status|wait|logs|key|print)" ;;
esac
