# CLAUDE.md — KnowHub 开发指南

## 项目概述
KnowHub 是一个智能知识管理平台，集成了 AI 摘要、GitHub Stars 管理、微信消息通道、定时报告等功能。

## 技术栈
- **后端**: Python 3.11+, FastAPI, SQLite, Uvicorn
- **前端**: React 19, TypeScript, Vite, Zustand
- **AI**: OpenAI-compatible API (支持多种模型)
- **外部集成**: GitHub API, WeChat (iLink), gitmem0 记忆系统

## 常用命令
```bash
# 后端启动（本地开发）
python3 backend/main.py

# Docker 部署
docker build -t knowhub .
docker run -d --name knowhub --restart unless-stopped -p 8765:8765 -v $(pwd)/backend/data:/app/backend/data knowhub

# 前端构建
cd frontend && npm run build

# 部署到 Docker（⚠️ 必须排除 data 目录，否则会覆盖数据库！）
tar --exclude='backend/data' -cf - -C /root/KnowHub backend | docker cp - knowhub:/app/
docker exec knowhub rm -rf /app/frontend/dist
docker cp frontend/dist/. knowhub:/app/frontend/dist/
docker restart knowhub

# 语法检查
python3 -m py_compile backend/main.py
cd frontend && npx tsc --noEmit
```

## 项目结构
```
backend/
  main.py          # App 初始化、middleware、lifecycle、webhook
  config.py        # 配置管理、密钥处理、EventStream
  database.py      # SQLite 连接、schema migration
  ai_services.py   # AI 摘要、embedding、混合搜索
  file_services.py # 文件处理、微信媒体加解密
  github_stars.py  # GitHub API 客户端、同步引擎、AI 分析
  wechat_agent.py  # WeChat 消息处理、ReAct 工具循环
  reminder_worker.py # 定时任务调度、报告生成
  tools.py         # AI 工具定义（function calling）
  routers/
    items.py       # 文件/文本 CRUD、搜索、相关项
    ask.py         # RAG 问答（/api/ask）
    settings.py    # 系统设置、登录、统计
    github.py      # GitHub Stars/Discover/Categories/Releases
    collections.py # 收藏集 CRUD
    system.py      # 提醒、报告、备份、画廊、知识图谱
    events.py      # SSE 事件流
frontend/
  src/
    App.tsx        # 主应用组件（页面路由、状态管理）
    types.ts       # TypeScript 接口定义
    sanitize.ts    # DOMPurify XSS 防护
    components/
      AIChat.tsx     # AI 对话组件
      READMEView.tsx # GitHub README 渲染
      ErrorBoundary.tsx # 错误边界
```

## 架构要点
- **Router 拆分**: main.py 只做 app 初始化和 middleware，业务逻辑在 routers/ 中
- **多 GitHub 账号**: config.json 中 GITHUB_ACCOUNTS 数组，自动迁移旧 GITHUB_TOKEN
- **安全**: 密码 SHA-256 哈希存储，cookie 存 hash，hmac.compare_digest 常量时间比较
- **XSS 防护**: 所有 dangerouslySetInnerHTML 都经过 DOMPurify sanitize
- **Rate limiting**: 内存级速率限制，/api/ask 20次/分钟，/api/upload 30次/分钟
- **消息分割**: 微信消息 800 字符限制，3 级分割（段落→行→字符）
- **报告调度**: timestamp-first 模式防止重复触发，顺序队列生成

## 代码规范
- Python: 用 `logger = logging.getLogger("knowhub")` 做日志，不用 bare `except:`
- TypeScript: 用 `types.ts` 中的接口，不用 `any`
- 新增 API endpoint 放对应的 router 文件，不要回 main.py
- 敏感配置通过 `get_safe_config()` 返回，不直接暴露
- 数据库操作用 `get_db()` + try/finally 或 `get_db_ctx()` context manager
