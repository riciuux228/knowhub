KnowHub — 智能知识管理平台

这是一个完整的单文件解决方案：Python FastAPI 后端 + 自适应 Web UI，支持文件/文字/代码分享、AI 自动整理、RAG 检索和自然语言提问。


项目结构

knowhub/
├── server.py          # 主服务（下面的完整代码）
├── data/              # 自动创建，存储上传文件
├── knowhub.db         # SQLite 自动创建
└── requirements.txt   # 依赖

requirements.txt


fastapi
uvicorn[standard]
python-multipart
openai
numpy
aiofiles

server.py — 完整代码


启动方式

bash
bash
# 1. 安装依赖
pip install fastapi uvicorn openai numpy aiofiles python-multipart

# 2. 配置 AI（选一种）

# 方案 A: OpenAI
export AI_BASE_URL="https://api.openai.com/v1"
export AI_API_KEY="sk-your-key"
export AI_MODEL="gpt-4o-mini"

# 方案 B: DeepSeek（便宜好用）
export AI_BASE_URL="https://api.deepseek.com/v1"
export AI_API_KEY="sk-f9d7ea29590a4ed488b5967a11c93688"
export AI_MODEL="deepseek-chat"


# 方案 C: 本地 Ollama（完全免费离线）
# 先安装 ollama，然后 ollama pull qwen2.5
export AI_BASE_URL="http://localhost:11434/v1"
export AI_API_KEY="ollama"
export AI_MODEL="qwen2.5"

# 3. 启动
python server.py

启动后，手机和电脑连同一 WiFi，浏览器打开 http://电脑IP:8765 即可。


核心功能一览

功能	说明
文件上传	拖拽/点击上传，支持所有格式，代码文件自动提取内容建索引
文字/代码	快捷粘贴想法和代码片段，区分类型管理
AI 自动整理	每次保存自动调用 AI 生成摘要 + 标签，无需手动分类
RAG 语义搜索	向量嵌入 + 关键词混合检索，搜"数据库相关笔记"能找到相关文件
自然语言提问	切到 AI 问答标签，直接问"上周存了哪些 Python 代码"
自适应 UI	PC 端侧边栏 + 网格布局，手机端汉堡菜单 + 单列布局
流式回答	AI 回答逐字输出，不卡顿

设计思路
用户丢入任何内容
       │
       ▼
  ┌─────────┐     ┌──────────────┐
  │ 存储层   │────▶│ AI 整理层     │  自动生成摘要+标签
  │ SQLite   │     │ (每次保存触发) │
  │ + 文件系统│     └──────┬───────┘
  └─────────┘            │
                          ▼
                   ┌──────────────┐
                   │ 向量索引层    │  text-embedding 向量化
                   │ (embeddings) │
                   └──────┬───────┘
                          │
        用户提问 ─────────┤
                          ▼
                   ┌──────────────┐
                   │ RAG 检索层    │  语义搜索 + 关键词混合
                   │ hybrid_search│
                   └──────┬───────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ LLM 回答层    │  带上下文的自然语言回答
                   │ (stream)     │
                   └──────────────┘

整个项目只有一个 Python 文件，零配置启动，数据全部存在本地 SQLite，适合个人或小团队在局域网内使用。