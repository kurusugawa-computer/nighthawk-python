#!/usr/bin/env bash
set -euo pipefail

DEFAULT_MODEL="gpt-5.2"
IMAGE_TAG="litellm-config"

LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-dummy}"

RUN_ID="$(date +%Y%m%d%H%M%S)-$$"
CONTAINER_NAME="litellm-${RUN_ID}"

CTX=""

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  [ -n "$CTX" ] && rm -rf "$CTX"
}
trap cleanup EXIT INT TERM

MODEL="$DEFAULT_MODEL"
CLAUDE_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --model)
      MODEL="${2:?Error: --model requires a value.}"
      shift 2
      ;;
    *)
      CLAUDE_ARGS+=("$1")
      shift
      ;;
  esac
done

if ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
  CTX="$(mktemp -d)"
  docker build -t "$IMAGE_TAG" -f- "$CTX" <<'__EOF__'
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir 'litellm[proxy]'
RUN mkdir -p /config && cat <<'__CFG__' > /config/config.yaml
model_list:
  - model_name: gpt-5.2
    litellm_params:
      model: openai/gpt-5.2
      api_key: os.environ/OPENAI_API_KEY
  - model_name: claude-haiku-4-5-20251001
    litellm_params:
      model: openai/gpt-5-mini
      api_key: os.environ/OPENAI_API_KEY

litellm_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
__CFG__
EXPOSE 4000
ENTRYPOINT ["litellm"]
__EOF__
fi

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  -p "127.0.0.1::4000" \
  -e "LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY" \
  -e "OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
  "$IMAGE_TAG" \
  --config /config/config.yaml \
  --host 0.0.0.0 \
  --port 4000 \
  >/dev/null

HOST_PORT=""
for _ in {1..50}; do
  line="$(docker port "$CONTAINER_NAME" 4000/tcp 2>/dev/null || true)"
  if [ -n "$line" ]; then
    HOST_PORT="${line##*:}"
    break
  fi
  sleep 0.1
done

if [ -z "$HOST_PORT" ]; then
  docker logs "$CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "Error: failed to determine LiteLLM host port." >&2
  exit 1
fi

LITELLM_PROXY_URL="http://127.0.0.1:${HOST_PORT}"

echo -n "Waiting for LiteLLM to be ready"
for _ in {1..60}; do
  if curl -fsS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    "$LITELLM_PROXY_URL/health" >/dev/null 2>&1; then
    echo
    ANTHROPIC_BASE_URL="$LITELLM_PROXY_URL" \
    ANTHROPIC_AUTH_TOKEN="$LITELLM_MASTER_KEY" \
      claude --model "$MODEL" "${CLAUDE_ARGS[@]}"
    exit $?
  fi

  running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    docker logs "$CONTAINER_NAME" || true
    echo "Error: LiteLLM container stopped unexpectedly." >&2
    exit 1
  fi
  printf '.'
  sleep 1
done

docker logs "$CONTAINER_NAME" || true
echo "Error: LiteLLM is not ready: $LITELLM_PROXY_URL" >&2
exit 1
