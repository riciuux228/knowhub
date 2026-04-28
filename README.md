<p align="center">
  <img src="frontend/public/favicon.svg" width="80" alt="KnowHub Logo">
</p>

<h1 align="center">KnowHub</h1>

<p align="center">
  <strong>你的智能知识管理平台</strong><br>
  多通道采集 · AI 自动整理 · 语义搜索 · 知识图谱
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-green?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-19-blue?logo=react" alt="React">
  <img src="https://img.shields.io/badge/TypeScript-5.6-blue?logo=typescript" alt="TypeScript">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="MIT License">
</p>

---

## 为什么选择 KnowHub？

你是否遇到过这些问题：
- 收藏的 GitHub 仓库太多，找不到想要的那个？
- 看到好文章想保存，但存哪里都记不住？
- 微信群里有人分享了重要内容，转眼就找不到了？
- 想问 AI 一个问题，但它不了解你的知识背景？

KnowHub 就是为了解决这些问题而生的。它把你的所有知识碎片汇聚在一起，用 AI 帮你自动整理、打标签、写摘要，还能用自然语言搜索和问答。

## 功能亮点

### 多通道知识采集
- **浏览器**: 通过 Web UI 上传文件或粘贴文本
- **微信**: 发消息给 KnowHub 机器人，自动存入知识库
- **文件夹监控**: 把文件扔进 `Drop_To_Brain/` 文件夹，自动处理归档
- **API**: 提供完整的 REST API，可以对接任何工具

### AI 智能增强
- 自动摘要：上传文件后 AI 自动生成摘要
- 智能打标：自动识别内容类型并打上标签
- 语义搜索：用自然语言搜索你的知识库
- RAG 问答：基于你的知识库回答问题

### GitHub Stars 管理
- 多账号支持：同时管理多个 GitHub 账号的 Stars
- AI 分析：自动为每个仓库生成摘要、标签、适用平台
- 智能分类：按 Web/移动/桌面/AI 等类别自动归类
- Release 追踪：订阅仓库，自动追踪新版本发布
- 趋势发现：查看 GitHub Trending、热门仓库、按主题搜索

### 知识图谱
- 可视化展示知识之间的关联
- 基于向量相似度自动发现相关知识
- 交互式图谱，点击节点查看详情

### 定时报告
- 日报/周报自动生成
- 知识库摘要、Stars 更新、GitHub 趋势
- 通过微信推送

### 数据安全
- 密码 SHA-256 哈希存储
- API 速率限制防止滥用
- 文件上传大小限制（200MB）
- 敏感配置自动脱敏

---

## 快速开始

### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/你的用户名/knowhub.git
cd knowhub

# 2. 构建基础镜像（首次需要，包含 AI 模型，约 5 分钟）
docker build -f Dockerfile.base -t knowhub-base .

# 3. 构建应用镜像（很快，几十秒）
docker build -t knowhub .

# 4. 启动容器
docker run -d \
  --name knowhub \
  --restart unless-stopped \
  -p 8765:8765 \
  -v $(pwd)/backend/data:/app/backend/data \
  knowhub

# 5. 打开浏览器访问
open http://localhost:8765
```

> **提示**: 首次启动需要下载 AI 模型，可能需要几分钟。可以在日志中查看进度：`docker logs -f knowhub`

### 方式二：本地开发

```bash
# 1. 克隆项目
git clone https://github.com/你的用户名/knowhub.git
cd knowhub

# 2. 安装后端依赖
pip install -r requirements.txt

# 3. 启动后端
python3 backend/main.py

# 4. 新终端，启动前端开发服务器
cd frontend
npm install
npm run dev

# 5. 访问 http://localhost:8765
```

---

## 配置指南

### 首次配置

1. 打开 `http://localhost:8765`
2. 点击左侧菜单的「设置」
3. 配置以下必填项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| **AI API Key** | AI 模型的 API Key | `sk-xxxxxxxxxx` |
| **AI Base URL** | AI API 地址 | `https://api.deepseek.com` |
| **AI Model** | 模型名称 | `deepseek-chat` |
| **系统密码** | 访问密码（留空=无密码） | `mypassword` |

### 推荐的 AI 模型配置

#### DeepSeek（推荐，性价比高）
```
AI Base URL: https://api.deepseek.com
AI Model: deepseek-chat
AI API Key: sk-xxxxxxxxxx
```

#### OpenAI
```
AI Base URL: https://api.openai.com/v1
AI Model: gpt-4o
AI API Key: sk-xxxxxxxxxx
```

#### 本地 Ollama
```
AI Base URL: http://localhost:11434/v1
AI Model: qwen2.5:7b
AI API Key: ollama
```

#### 其他 OpenAI 兼容 API
KnowHub 支持任何 OpenAI 兼容的 API，包括：
- Claude (via proxy)
- 通义千问
- 智谱 GLM
- Moonshot
- 深度求索

### GitHub 账号配置

在设置页面的「GitHub Stars」区域：

1. 点击「添加账号」
2. 输入 GitHub Personal Access Token
3. 点击保存

> **如何获取 Token**: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → 只需 `repo` 和 `read:user` 权限

支持多账号，每个账号可以独立启用/禁用。

### 微信机器人配置

需要使用 iLink 作为微信机器人框架：

1. 部署 iLink 服务
2. 在设置页面配置 Webhook URL：`http://你的服务器:8765/api/webhook/openilink`
3. （可选）设置 Webhook Token 增加安全性

### Webhook 鉴权

为了安全，建议设置 Webhook Token：

1. 在设置页面配置 `WEBHOOK_TOKEN`
2. iLink 调用时需要带上 Token：
   - 方式一：Header `Authorization: Bearer your_token`
   - 方式二：Query `?token=your_token`

---

## 使用教程

### 知识库管理

#### 添加内容
- **上传文件**: 点击右上角「+」→ 选择文件（支持 PDF、Word、图片、代码等）
- **添加文本**: 点击右上角「+」→ 粘贴文本内容
- **微信投递**: 发消息给 KnowHub 机器人
- **文件夹监控**: 把文件扔进 `Drop_To_Brain/` 文件夹

#### 搜索知识
- **关键词搜索**: 在搜索框输入关键词
- **语义搜索**: 用自然语言描述你想找的内容
- **筛选**: 按类型（文件/文本/图片/代码）和空间筛选

#### 空间管理
- 默认空间：通用知识
- 灵感库：创意灵感，AI 以发散性视角处理
- 工作区：专业内容，AI 保持严谨视角

#### 收藏集
- 创建收藏集，给知识分组
- 支持自定义图标
- AI 可以推荐相关知识到收藏集

### GitHub Stars

#### 同步 Stars
1. 配置 GitHub Token
2. 点击「同步」按钮
3. 首次同步会拉取所有 Stars（可能需要几分钟）
4. 后续同步只检查新增的 Stars

#### AI 分析
- 点击仓库详情 → 「AI 分析」
- 自动生成：摘要、标签、适用平台、推荐分类

#### Release 追踪
1. 在仓库详情页点击「订阅」
2. KnowHub 会自动检查新版本
3. 在「Release」标签页查看所有订阅仓库的更新

#### 趋势发现
- **Trending**: GitHub 官方趋势
- **热门**: 高星仓库
- **按主题搜索**: 输入关键词搜索相关仓库

### AI 对话

- 点击左下角的对话图标
- 可以问任何问题，AI 会基于你的知识库回答
- 支持流式输出，实时显示回答
- 对话历史自动保存

### 知识图谱

- 在「工具箱」→「知识图谱」查看
- 节点代表知识条目，连线代表语义关联
- 可以拖拽、缩放、点击查看详情

### 定时报告

在设置页面配置：
- **知识库日报/周报**: 总结最近添加的内容
- **Stars 日报/周报**: 总结 Stars 更新
- **Trending 日报/周报/月报**: GitHub 趋势分析

报告会自动保存到知识库，并通过微信推送。

### Obsidian 导出

- 点击设置页面的「导出 Obsidian」按钮
- 下载 ZIP 文件
- 解压到你的 Obsidian Vault
- 自动保留：标题、标签、空间、日期等元数据

---

## 技术架构

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   浏览器 UI  │    │  微信 iLink  │    │  DropZone   │
│  React/TS   │    │   Webhook    │    │  文件夹监控   │
└──────┬──────┘    └──────┬───────┘    └──────┬──────┘
       │                  │                    │
       └──────────┬───────┴────────────────────┘
                  │
           ┌──────▼──────┐
           │   FastAPI    │
           │   + Routers  │
           └──────┬──────┘
                  │
    ┌─────────────┼─────────────┐
    │             │             │
┌───▼───┐  ┌─────▼─────┐  ┌───▼───┐
│SQLite │  │ AI Service │  │GitHub │
│  DB   │  │ (LLM/API)  │  │ API   │
└───────┘  └───────────┘  └───────┘
```

### 后端技术栈
- **Python 3.11+**: 主语言
- **FastAPI**: Web 框架
- **SQLite**: 数据库（单文件，零配置）
- **Uvicorn**: ASGI 服务器
- **Sentence Transformers**: 向量嵌入
- **gitmem0**: 记忆系统

### 前端技术栈
- **React 19**: UI 框架
- **TypeScript**: 类型安全
- **Vite**: 构建工具
- **Zustand**: 状态管理
- **DOMPurify**: XSS 防护

---

## API 参考

### 知识库

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 上传文件（最大 200MB） |
| POST | `/api/text` | 添加文本（最大 500K 字符） |
| GET | `/api/items` | 列表/搜索（支持分页、筛选） |
| GET | `/api/items/{id}` | 获取详情 |
| PUT | `/api/items/{id}` | 更新 |
| DELETE | `/api/items/{id}` | 删除 |
| GET | `/api/items/{id}/related` | 相关知识 |
| GET | `/api/items/{id}/crossrefs` | 交叉引用 |
| GET | `/api/download/{id}` | 下载文件 |

### AI

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | RAG 问答（流式） |

### GitHub

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/github/sync` | 同步 Stars |
| GET | `/api/github/stars` | Stars 列表 |
| GET | `/api/github/categories` | 分类列表 |
| GET | `/api/github/releases` | Release 列表 |
| GET | `/api/github/discover/trending` | 趋势仓库 |

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 获取配置 |
| POST | `/api/settings` | 更新配置 |
| POST | `/api/login` | 登录 |
| GET | `/api/stats` | 统计数据 |
| GET | `/api/graph` | 知识图谱 |
| GET | `/api/gallery` | 媒体画廊 |
| GET | `/api/export` | 导出 Obsidian |
| GET | `/api/backup` | 备份数据库 |

---

## 安全特性

- **密码安全**: SHA-256 哈希存储，常量时间比较（防时序攻击）
- **XSS 防护**: 所有 HTML 输出经过 DOMPurify 消毒
- **速率限制**: API 请求频率限制（问答 20次/分，上传 30次/分）
- **文件限制**: 上传文件最大 200MB，文本最大 500K 字符
- **Webhook 鉴权**: 支持 Token 验证
- **配置脱敏**: API 返回配置时自动隐藏敏感信息

---

## 常见问题

### Q: 支持哪些文件格式？
A: 支持常见文档格式（PDF、Word、TXT）、图片（JPG、PNG、GIF）、代码文件、压缩包等。AI 会自动识别并提取内容。

### Q: 数据存储在哪里？
A: 默认存储在 `backend/data/` 目录，包括 SQLite 数据库和上传的文件。Docker 部署时通过 volume 持久化。

### Q: 可以用其他 AI 模型吗？
A: 可以，只要支持 OpenAI 兼容 API 的模型都能用，包括 DeepSeek、Claude、通义千问、本地 Ollama 等。

### Q: 如何备份数据？
A: 在设置页面点击「备份数据库」，会下载一个 SQLite 文件。也可以直接复制 `backend/data/` 目录。

### Q: 微信机器人怎么配置？
A: 需要部署 iLink 服务，然后在 KnowHub 设置页面配置 Webhook URL。详见上面的「微信机器人配置」。

### Q: 如何更新版本？
A: Docker 部署：
```bash
git pull
docker build -t knowhub .
docker stop knowhub && docker rm knowhub
docker run -d --name knowhub --restart unless-stopped \
  -p 8765:8765 \
  -v $(pwd)/backend/data:/app/backend/data \
  knowhub
```

---

## 开发指南

### 项目结构
```
knowhub/
├── backend/
│   ├── main.py           # 应用入口
│   ├── config.py         # 配置管理
│   ├── database.py       # 数据库
│   ├── ai_services.py    # AI 服务
│   ├── file_services.py  # 文件处理
│   ├── github_stars.py   # GitHub 集成
│   ├── wechat_agent.py   # 微信机器人
│   ├── reminder_worker.py # 定时任务
│   └── routers/          # API 路由
├── frontend/
│   ├── src/
│   │   ├── App.tsx       # 主组件
│   │   ├── types.ts      # 类型定义
│   │   └── components/   # 组件
│   └── dist/             # 构建产物
├── requirements.txt      # Python 依赖
├── Dockerfile           # Docker 配置
└── README.md            # 本文件
```

### 运行测试
```bash
pip install pytest httpx
python -m pytest tests/ -v
```

### 代码规范
- Python: 使用 `logging.getLogger("knowhub")` 记录日志
- TypeScript: 使用 `types.ts` 中定义的接口
- 新增 API 放在对应的 router 文件中

---

## 许可证

[MIT License](LICENSE)

---

## 致谢

- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架
- [React](https://react.dev/) - UI 框架
- [sentence-transformers](https://www.sbert.net/) - 向量嵌入
- [DOMPurify](https://github.com/cure53/DOMPurify) - XSS 防护
- [Force Graph](https://github.com/vasturiano/force-graph) - 知识图谱可视化
- [GitHubStarsManage](https://github.com/your-zhao/GitHubStarsManage) - GitHub Stars 管理灵感
- [MarkItDown](https://github.com/microsoft/markitdown) - 文件转 Markdown 提取
