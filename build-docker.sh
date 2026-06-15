#!/usr/bin/env bash
# 构建 ct8114 Docker 镜像并导出为 tar.gz
#
# 用法:   bash build-docker.sh [版本号]
# 示例:   bash build-docker.sh v1.0.1
# 默认:   bash build-docker.sh  →  版本号 v1.0.0
#
# 输出文件: ../ct8114-docker-<版本号>.tar.gz
#
# 说明:
#   基础镜像 ghcr.io/gjb8114/clang-tidy-gjb8114:latest
#   已内置 Linux 版 codetidy 二进制，无需把 Windows 的 codetidy.exe 打包进来。
#   docker-compose.yml 默认使用 ANALYSIS_ENGINE=dcab_http（调用外部 DeepSITRServer）；
#   若只想用内置 codetidy，将 ANALYSIS_ENGINE 改为 codetidy 即可。

set -e

VERSION="${1:-v1.0.0}"
IMAGE_NAME="ct8114"
TAG="${IMAGE_NAME}:${VERSION}"
OUTPUT_FILE="../ct8114-docker-${VERSION}.tar.gz"

echo "=== 构建 Docker 镜像: ${TAG} ==="
docker build -t "${TAG}" -f dockerfile .

# 同时打一个 :v1 的别名，方便 docker-compose.yml 直接使用
docker tag "${TAG}" "${IMAGE_NAME}:v1"

echo ""
echo "=== 导出镜像: ${OUTPUT_FILE} ==="
docker save "${TAG}" | gzip > "${OUTPUT_FILE}"

echo ""
echo "=== 完成 ==="
echo "镜像:   ${TAG}"
echo "文件:   ${OUTPUT_FILE}"
SIZE=$(du -sh "${OUTPUT_FILE}" 2>/dev/null | cut -f1 || echo "unknown")
echo "大小:   ${SIZE}"
echo ""
echo "在目标机器上加载:"
echo "  docker load < ${OUTPUT_FILE}"
echo ""
echo "快速启动 (单容器):"
echo "  docker run -d -p 8000:8000 --name ct8114 \\"
echo "    -e ANALYSIS_ENGINE=codetidy \\"
echo "    ${TAG}"
echo ""
echo "使用 docker-compose (推荐，含 UniPortal 卷挂载):"
echo "  docker compose up -d"
