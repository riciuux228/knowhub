# ============================================
# knowhub: 代码层（改动频繁，几秒构建完成）
# 依赖 knowhub-base 镜像（需先构建: docker build -f Dockerfile.base -t knowhub-base .）
# ============================================

# Build frontend
FROM node:20-alpine AS build-stage
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install --legacy-peer-deps
COPY frontend/ ./
RUN chmod +x node_modules/.bin/* && npm run build

# Runtime: 基于预构建的 base 镜像
FROM knowhub-base
WORKDIR /app

# 只复制代码文件（变动频繁但构建快）
COPY backend/ ./backend/
COPY --from=build-stage /app/frontend/dist ./frontend/dist

CMD ["sh", "-c", "python -m gitmem0.auto daemon & sleep 3 && uvicorn backend.main:app --host 0.0.0.0 --port 8765 --workers 2"]
