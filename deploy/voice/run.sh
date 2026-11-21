#!/usr/bin/env bash
# NeuroSciFi Voice Cortex speech services (hybrid path): Whisper ASR (vLLM) and Kokoro TTS, Docker, loopback only.
#
#   deploy/voice/run.sh build               build neuroscifi-voice-{asr,tts} from the digest-pinned vLLM image
#   deploy/voice/run.sh start [asr|tts|all] start detached (restart policy unless-stopped)
#   deploy/voice/run.sh foreground asr|tts  run attached (used by the systemd units; systemd restarts it)
#   deploy/voice/run.sh stop [asr|tts|all]  stop and remove
#   deploy/voice/run.sh status              container state and health
#   deploy/voice/run.sh wait [asr|tts|all] [SECS]  wait until ready (ASR: /health, TTS: /health status ready)
#   deploy/voice/run.sh logs asr|tts        follow a container log
#   deploy/voice/run.sh key                 print the key file path (creating the key if missing); never the key
#   deploy/voice/run.sh print asr|tts       print the docker command (key redacted) and change nothing
#
# A real health check (synthesise a sentence, transcribe it back, compare) is `nsf voice health` (Python).
# Security: binds 127.0.0.1 only; one generated API key (0600, outside git) guards both services; weights are mounted
# read-only; read-only root filesystem; no capabilities; runs as the invoking user; HF access off; no usage stats.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
set -a; . "$HERE/voice.env"; set +a
OVERRIDE="${NSF_VOICE_DIR:-$HOME/.config/neuroscifi/voice}/voice.env"
if [ -f "$OVERRIDE" ]; then set -a; . "$OVERRIDE"; set +a; fi

die() { echo "run.sh: $*" >&2; exit 1; }
KEY_FILE="$NSF_VOICE_DIR/api_key"
ASR_ENV="$NSF_VOICE_DIR/asr.secret.env"
TTS_ENV="$NSF_VOICE_DIR/tts.secret.env"

[[ "$NSF_VOICE_BIND" == "127.0.0.1" || "$NSF_VOICE_BIND" == "::1" ]] || die "NSF_VOICE_BIND must be loopback (got $NSF_VOICE_BIND)"
[[ "$NSF_VOICE_BASE_IMAGE" == *@sha256:* ]] || die "NSF_VOICE_BASE_IMAGE must be pinned by digest"

ensure_key() {
  umask 077
  mkdir -p "$NSF_VOICE_DIR"
  chmod 700 "$NSF_VOICE_DIR"
  if [ ! -s "$KEY_FILE" ]; then
    python3 -c 'import secrets; print("nsfv-" + secrets.token_urlsafe(32))' > "$KEY_FILE.tmp"
    mv "$KEY_FILE.tmp" "$KEY_FILE"
  fi
  chmod 600 "$KEY_FILE"
  printf 'VLLM_API_KEY=%s\n' "$(tr -d '\n' < "$KEY_FILE")" > "$ASR_ENV"
  printf 'NSF_TTS_API_KEY=%s\n' "$(tr -d '\n' < "$KEY_FILE")" > "$TTS_ENV"
  chmod 600 "$ASR_ENV" "$TTS_ENV"
}

check_image() {   # the derived image must sit on the pinned base (its layers are a prefix of the derived image's)
  local image="$1" base derived
  base="$(docker image inspect -f '{{join .RootFS.Layers " "}}' "$NSF_VOICE_BASE_IMAGE" 2>/dev/null)" \
    || die "base image $NSF_VOICE_BASE_IMAGE not present (docker pull it)"
  derived="$(docker image inspect -f '{{join .RootFS.Layers " "}}' "$image" 2>/dev/null)" \
    || die "image $image not built (run: $0 build)"
  [[ "$derived" == "$base "* ]] || die "image $image is not built on $NSF_VOICE_BASE_IMAGE (rebuild: $0 build)"
}

common_args() {   # $1: /tmp mount options (default noexec)
  ARGS+=(--device nvidia.com/gpu=all
    --user "$(id -u):$(id -g)"
    --read-only --tmpfs "/tmp:${1:-rw,noexec,nosuid,nodev,size=2g,mode=1777}"
    --cap-drop ALL --security-opt no-new-privileges
    --pids-limit "$NSF_VOICE_PIDS"
    -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1)
}

asr_args() {
  [ -f "$NSF_VOICE_ASR_MODEL_DIR/config.json" ] || die "no ASR model at $NSF_VOICE_ASR_MODEL_DIR"
  mkdir -p "$NSF_VOICE_CACHE_DIR/asr"
  ARGS=(--name "$NSF_VOICE_ASR_CONTAINER" -p "$NSF_VOICE_BIND:$NSF_VOICE_ASR_PORT:8000"
    --cpus "$NSF_VOICE_ASR_CPUS" --memory "$NSF_VOICE_ASR_MEMORY" --shm-size 2g)
  common_args
  ARGS+=(-v "$NSF_VOICE_ASR_MODEL_DIR:/models/whisper:ro" -v "$NSF_VOICE_CACHE_DIR/asr:/cache:rw"
    -e HOME=/cache -e XDG_CACHE_HOME=/cache -e HF_HOME=/cache/hf -e VLLM_CACHE_ROOT=/cache/vllm
    -e TRITON_CACHE_DIR=/cache/triton -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor -e NUMBA_CACHE_DIR=/cache/numba
    --env-file "$ASR_ENV"
    --health-cmd "curl -fsS -m 5 http://127.0.0.1:8000/health || exit 1"
    --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 10m
    "$NSF_VOICE_ASR_IMAGE" /models/whisper
    --served-model-name "$NSF_VOICE_ASR_SERVED_NAME"
    --host 0.0.0.0 --port 8000
    --gpu-memory-utilization "$NSF_VOICE_ASR_GPU_UTIL"
    --kv-cache-memory-bytes "$NSF_VOICE_ASR_KV_CACHE_BYTES"
    --max-num-seqs "$NSF_VOICE_ASR_MAX_SEQS"
    --enforce-eager --disable-uvicorn-access-log)
}

tts_args() {
  [ -f "$NSF_VOICE_TTS_MODEL_DIR/kokoro-v1_0.pth" ] || die "no TTS model at $NSF_VOICE_TTS_MODEL_DIR"
  mkdir -p "$NSF_VOICE_CACHE_DIR/tts"
  ARGS=(--name "$NSF_VOICE_TTS_CONTAINER" -p "$NSF_VOICE_BIND:$NSF_VOICE_TTS_PORT:8000"
    --cpus "$NSF_VOICE_TTS_CPUS" --memory "$NSF_VOICE_TTS_MEMORY" --shm-size 1g)
  # phonemizer copies libespeak-ng.so into a temporary directory and loads it from there: on a noexec /tmp the load
  # fails, misaki has no out-of-vocabulary fallback and Kokoro silently drops every unknown word ("NeuroSciFi",
  # "agentic", "techcrunch"). The TTS /tmp is exec (still nosuid, nodev, size-bounded, root filesystem read-only).
  common_args "rw,exec,nosuid,nodev,size=2g,mode=1777"
  [ "$NSF_VOICE_TTS_DEVICE" = cpu ] && ARGS+=(-e CUDA_VISIBLE_DEVICES=)
  ARGS+=(-v "$NSF_VOICE_TTS_MODEL_DIR:/models/kokoro-82m:ro" -v "$NSF_VOICE_CACHE_DIR/tts:/cache:rw"
    -e HOME=/cache -e XDG_CACHE_HOME=/cache -e HF_HOME=/cache/hf -e NUMBA_CACHE_DIR=/cache/numba
    -e NSF_TTS_MODEL_DIR=/models/kokoro-82m -e NSF_TTS_DEVICE="$NSF_VOICE_TTS_DEVICE"
    -e NSF_TTS_THREADS="$NSF_VOICE_TTS_THREADS" -e NSF_TTS_PORT=8000
    --env-file "$TTS_ENV"
    --health-cmd "python3 -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status==200 else 1)\""
    --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 5m
    "$NSF_VOICE_TTS_IMAGE")
}

services() { case "${1:-all}" in asr) echo asr ;; tts) echo tts ;; all) echo asr tts ;; *) die "unknown service: $1" ;; esac; }
container() { [ "$1" = asr ] && echo "$NSF_VOICE_ASR_CONTAINER" || echo "$NSF_VOICE_TTS_CONTAINER"; }
port() { [ "$1" = asr ] && echo "$NSF_VOICE_ASR_PORT" || echo "$NSF_VOICE_TTS_PORT"; }
image() { [ "$1" = asr ] && echo "$NSF_VOICE_ASR_IMAGE" || echo "$NSF_VOICE_TTS_IMAGE"; }

cmd="${1:-status}"
case "$cmd" in
  build)
    docker image inspect "$NSF_VOICE_BASE_IMAGE" >/dev/null 2>&1 || docker pull "$NSF_VOICE_BASE_IMAGE"
    docker build -f "$HERE/Dockerfile.asr" -t "$NSF_VOICE_ASR_IMAGE" "$HERE"
    docker build -f "$HERE/Dockerfile.tts" -t "$NSF_VOICE_TTS_IMAGE" "$HERE"
    for s in asr tts; do check_image "$(image $s)"; echo "$(image $s) $(docker image inspect -f '{{.Id}}' "$(image $s)")"; done ;;
  start)
    ensure_key
    for s in $(services "${2:-all}"); do
      check_image "$(image "$s")"; "${s}_args"
      docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
      docker run -d --restart unless-stopped "${ARGS[@]}" >/dev/null
      echo "started $(container "$s") on http://$NSF_VOICE_BIND:$(port "$s")"
    done ;;
  foreground)
    s="${2:-}"; [ "$s" = asr ] || [ "$s" = tts ] || die "foreground needs asr or tts"
    ensure_key; check_image "$(image "$s")"; "${s}_args"
    docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
    exec docker run --rm --init "${ARGS[@]}" ;;
  stop)
    for s in $(services "${2:-all}"); do
      docker stop -t 20 "$(container "$s")" >/dev/null 2>&1 || true
      docker rm -f "$(container "$s")" >/dev/null 2>&1 || true
      echo "stopped $(container "$s")"
    done ;;
  status)
    for s in asr tts; do
      docker inspect -f '{{.Name}} {{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}}' \
        "$(container "$s")" 2>/dev/null || echo "$(container "$s"): not running"
    done ;;
  wait)
    limit="${3:-900}"; start=$(date +%s)
    for s in $(services "${2:-all}"); do
      until curl -fsS -m 5 "http://$NSF_VOICE_BIND:$(port "$s")/health" >/dev/null 2>&1; do
        [ $(( $(date +%s) - start )) -ge "$limit" ] && die "$s not ready after ${limit}s"
        docker inspect "$(container "$s")" >/dev/null 2>&1 || die "$s container exited; see: docker logs $(container "$s")"
        sleep 3
      done
      echo "$s ready after $(( $(date +%s) - start ))s"
    done ;;
  logs) exec docker logs -f "$(container "${2:?logs needs asr or tts}")" ;;
  key) ensure_key; echo "$KEY_FILE" ;;
  print)
    s="${2:-}"; [ "$s" = asr ] || [ "$s" = tts ] || die "print needs asr or tts"
    ASR_ENV="<NSF_VOICE_DIR>/asr.secret.env"; TTS_ENV="<NSF_VOICE_DIR>/tts.secret.env"; "${s}_args"
    printf 'docker run -d --restart unless-stopped'; printf ' %q' "${ARGS[@]}"; printf '\n' ;;
  *) die "unknown command: $cmd (build|start|foreground|stop|status|wait|logs|key|print)" ;;
esac
