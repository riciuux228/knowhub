#!/bin/bash
# 重建 base 镜像（仅在 requirements.txt / gitmem0 / Dockerfile.base 变动时执行）
# 普通代码改动直接 docker-compose build 即可，几秒完成
set -e
echo "🔨 构建 landrop-base 镜像（含所有重量级依赖）..."
docker build -f Dockerfile.base -t landrop-base .
echo "✅ landrop-base 构建完成！之后代码改动只需 docker-compose build 即可。"
