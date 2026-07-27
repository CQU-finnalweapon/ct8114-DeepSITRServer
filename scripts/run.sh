set -eu

PORT="${PORT:-8006}"
CONTAINER_NAME="${CONTAINER_NAME:-ct8114}"
IMAGE_NAME="${IMAGE_NAME:-ct8114:v1}"

docker rm -vf "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d --restart=always \
  --name "$CONTAINER_NAME" \
  -p "${PORT}:${PORT}" \
  -e PORT="$PORT" \
  -e CLANG_TIDY_CHECKS="gjb8114-*,-gjb8114-r-1-3-8,-gjb8114-r-1-7-3,-gjb8114-r-1-7-7" \
  "$IMAGE_NAME" \
  uvicorn server:app --host 0.0.0.0 --port "$PORT"