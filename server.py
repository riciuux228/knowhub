#!/usr/bin/env python3
"""
KnowHub - 智能知识管理平台
启动: python server.py
访问: http://本机IP:8765
"""

import os
import sys
import json
import time
import uuid
import sqlite3
import hashlib
import asyncio
import mimetypes
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "knowhub.db"
CONFIG_PATH = BASE_DIR / "config.json"
HOST = "0.0.0.0"
PORT = 8765

# 加载配置文件
config_data = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config_data = json.load(f)
    except Exception as e:
        print(f"Failed to load config.json: {e}")
else:
    # 第一次运行，生成默认配置
    config_data = {
        "AI_BASE_URL": "https://api.openai.com/v1",
        "AI_API_KEY": "sk-your-key",
        "AI_MODEL": "gpt-4o-mini",
        "AI_EMBED_MODEL": "text-embedding-3-small"
    }
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to create config.json: {e}")

# AI 配置 — 支持任何 OpenAI 兼容接口
# 可以用 OpenAI / DeepSeek / 本地 Ollama / vLLM 等
AI_BASE_URL = os.getenv("AI_BASE_URL", config_data.get("AI_BASE_URL", "https://api.openai.com/v1"))
AI_API_KEY = os.getenv("AI_API_KEY", config_data.get("AI_API_KEY", "sk-your-key"))
AI_MODEL = os.getenv("AI_MODEL", config_data.get("AI_MODEL", "gpt-4o-mini"))
AI_EMBED_MODEL = os.getenv("AI_EMBED_MODEL", config_data.get("AI_EMBED_MODEL", "text-embedding-3-small"))

# 如果用 Ollama 本地:
# "AI_BASE_URL": "http://localhost:11434/v1", "AI_API_KEY": "ollama", "AI_MODEL": "qwen2.5"

DATA_DIR.mkdir(exist_ok=True)

# ============================================================
# 数据库
# ============================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,          -- file / text / code
            title TEXT,
            content TEXT,                -- 文字内容 / 文件名
            file_path TEXT,              -- 文件存储路径
            file_size INTEGER DEFAULT 0,
            mime_type TEXT,
            tags TEXT DEFAULT '[]',
            summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            item_id TEXT PRIMARY KEY,
            vector BLOB NOT NULL,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
    """)
    conn.commit()
    conn.close()

init_db()

# ============================================================
# AI 客户端
# ============================================================
ai_client = AsyncOpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)

EMBED_DIM = 1536  # text-embedding-3-small

async def get_embedding(text: str) -> np.ndarray:
    """获取文本向量"""
    try:
        resp = await ai_client.embeddings.create(
            model=AI_EMBED_MODEL,
            input=text[:8000]
        )
        return np.array(resp.data[0].embedding, dtype=np.float32)
    except Exception as e:
        print(f"Embedding error: {e}")
        return np.zeros(EMBED_DIM, dtype=np.float32)

async def ai_chat(messages: list, stream: bool = False):
    """AI 对话"""
    return await ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=messages,
        stream=stream,
        temperature=0.7,
        max_tokens=4096
    )

async def ai_summarize_and_tag(title: str, content: str, filename: str = "", rewrite: bool = False) -> dict:
    """让 AI 自动整理：生成摘要、标签、标题和判断是否为代码。如果 rewrite=True，还会返回友好的 Markdown 排版重写"""
    if rewrite:
        prompt = f"""请分析并重排/重写以下内容。
原文标题: {title or '(无)'}
原始内容文本: {content[:10000] if content else '(无)'}

要求：
1. 你的任务是理解用户胡乱混合粘贴的复杂文字和代码，整理成非常易于阅读的、对人类友好的 Markdown 知识沉淀文档（自动排版，比如为代码打上语言标签，增加标题层次、修正语病等）。
2. 如果原始内容主要是某段代码，请将整个代码包装好，并补齐一定的原理解释或说明。如果已经是极好的文档，稍微润色格式即可。
3. 务必保留所有有用的原信息，不要随意删减重要逻辑。
4. 返回必须严格包含如下两个标记段落（严禁出现其它废话）：

<metadata>
{{
  "title": "根据内容生成的简明标题，不超过20字",
  "summary": "一句话核心摘要，不要提及截断，概括内容",
  "tags": ["标签1", "标签2", "标签3"],
  "category": "文章/代码/笔记/问答/配置/其他",
  "is_code": true/false
}}
</metadata>

<formatted_content>
这里填入深度重写或排版美化后的 Markdown 正文...
</formatted_content>"""
        try:
            resp = await ai_chat([
                {"role": "system", "content": "你是资深的文档萃取和排版引擎。必须完全按照规定格式（带有 <metadata> 和 <formatted_content> 标签）返回，不能有任何其他寒暄。"},
                {"role": "user", "content": prompt}
            ])
            text = resp.choices[0].message.content.strip()
            
            meta_str = text.split("<metadata>")[1].split("</metadata>")[0].strip()
            content_str = text.split("<formatted_content>")[1].split("</formatted_content>")[0].strip()
            
            if "```" in meta_str:
                meta_str = meta_str.split("```")[1]
                if meta_str.lower().startswith("json"):
                    meta_str = meta_str[4:].strip()
            
            data = json.loads(meta_str)
            data["formatted_content"] = content_str
            # 将 is_code 强制为 false，因为经过了文章式的友好排版重写，它现在是一篇 Markdown 笔记/文章，而不单纯是裸代码，但根据 AI 倾向也可以保留
            # 为了更好的渲染效果，建议作为 text 类型（会过 Markdown 渲染器）。但为了列表角标，仍然允许 AI 决定。
            return data
        except Exception as e:
            print(f"AI rewrite error: {e}")
            # fallback to non-rewrite basic JSON below
    
    prompt = f"""分析以下内容（注意：内容可能因为超出长度被截断，请完全忽略截断、不完整等情况，直接根据已有的部分回答），返回 JSON：
原文标题/文件名: {title or filename or '(无)'}
内容片段: {content[:3000] if content else '(文件内容无法直接预览)'}

返回格式必须为无 markdown 块的纯合法 JSON，不要有多余的话：
{{
  "title": "根据已有内容自动生成的一个简明标题，不超过20个字",
  "summary": "一句话摘要，不要提及内容被截断或不完整的事情，直接概括现有的核心内容",
  "tags": ["标签1", "标签2", "标签3"],
  "category": "代码/文档/图片/想法/笔记/配置/其他",
  "is_code": true/false
}}"""
    try:
        resp = await ai_chat([
            {"role": "system", "content": "你是信息整理助手。只返回 JSON，不要多余文字。"},
            {"role": "user", "content": prompt}
        ])
        text = resp.choices[0].message.content.strip()
        # 提取 JSON
        if "```" in text:
            text = text.split("```")[1]
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
    except Exception as e:
        print(f"AI summarize error: {e}")
        return {"title": title or filename, "summary": title or filename, "tags": [], "category": "其他", "is_code": False}

# ============================================================
# RAG 检索
# ============================================================
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

async def search_items(query: str, top_k: int = 10) -> list:
    """RAG 语义搜索"""
    query_vec = await get_embedding(query)
    conn = get_db()

    items = conn.execute("""
        SELECT i.*, e.vector
        FROM items i
        JOIN embeddings e ON i.id = e.item_id
        ORDER BY i.created_at DESC
        LIMIT 200
    """).fetchall()
    conn.close()

    scored = []
    for row in items:
        vec = np.frombuffer(row["vector"], dtype=np.float32)
        sim = cosine_similarity(query_vec, vec)
        scored.append((sim, dict(row)))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]

def keyword_search(query: str, top_k: int = 10) -> list:
    """关键词搜索（兜底）"""
    conn = get_db()
    items = conn.execute("""
        SELECT * FROM items
        WHERE content LIKE ? OR title LIKE ? OR tags LIKE ? OR summary LIKE ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", top_k)).fetchall()
    conn.close()
    return [dict(r) for r in items]

async def hybrid_search(query: str, top_k: int = 8) -> list:
    """混合搜索：语义 + 关键词"""
    semantic = await search_items(query, top_k)
    keyword = keyword_search(query, top_k)

    seen = set()
    merged = []
    for item in semantic + keyword:
        iid = item["id"]
        if iid not in seen:
            seen.add(iid)
            merged.append(item)
    return merged[:top_k]

# ============================================================
# FastAPI 应用
# ============================================================
app = FastAPI(title="KnowHub")

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    title: str = Form(""),
    tags: str = Form("[]")
):
    """上传文件"""
    item_id = uuid.uuid4().hex[:12]
    filename = file.filename or "unnamed"
    ext = Path(filename).suffix
    save_name = f"{item_id}{ext}"
    save_path = DATA_DIR / save_name

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # 尝试读取文本内容用于索引
    text_content = ""
    ext_lower = ext.lower()
    if mime.startswith("text/") or ext_lower in (".py", ".js", ".ts", ".json", ".yaml", ".yml",
                                            ".md", ".txt", ".csv", ".html", ".css",
                                            ".java", ".c", ".cpp", ".h", ".go", ".rs",
                                            ".sh", ".bat", ".toml", ".ini", ".cfg",
                                            ".xml", ".sql", ".r", ".rb", ".php"):
        try:
            text_content = content.decode("utf-8", errors="replace")
        except:
            pass
    elif ext_lower == ".docx":
        try:
            import docx
            import io
            doc = docx.Document(io.BytesIO(content))
            text_content = "\n".join([para.text for para in doc.paragraphs])
        except ImportError:
            print("python-docx 未安装，无法在后端提取 DOCX 文本用于 AI 摘要。请运行 pip install python-docx")
        except Exception as e:
            print(f"Error parsing docx: {e}")
    elif ext_lower == ".pdf":
        try:
            import pypdf
            import io
            reader = pypdf.PdfReader(io.BytesIO(content))
            text_content = "\n".join([page.extract_text() or "" for page in reader.pages][:20]) # 取前20页防止过长
        except ImportError:
            print("pypdf 未安装，无法在后端提取 PDF 文本用于 AI 摘要。请运行 pip install pypdf")
        except Exception as e:
            print(f"Error parsing pdf: {e}")

    # AI 整理
    info = await ai_summarize_and_tag(title, text_content, filename)
    final_title = title if title.strip() else (info.get("title") or filename)

    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO items (id, type, title, content, file_path, file_size, mime_type, tags, summary, created_at, updated_at)
        VALUES (?, 'file', ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, final_title, text_content[:10000], str(save_path),
          len(content), mime, json.dumps(info.get("tags", []), ensure_ascii=False),
          info.get("summary", ""), now, now))
    conn.commit()

    # 生成 embedding
    embed_text = f"{final_title}\n{info.get('summary', '')}\n{text_content[:2000]}"
    vec = await get_embedding(embed_text)
    conn.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)",
                 (item_id, vec.tobytes()))
    conn.commit()
    conn.close()

    return {"ok": True, "id": item_id, "summary": info.get("summary", ""),
            "tags": info.get("tags", []), "category": info.get("category", "")}

@app.post("/api/text")
async def add_text(
    content: str = Form(...),
    title: str = Form(""),
    is_code: bool = Form(False)
):
    """添加文字/代码"""
    item_id = uuid.uuid4().hex[:12]

    info = await ai_summarize_and_tag(title, content, rewrite=True)
    final_content = info.get("formatted_content", content)
    final_title = title if title.strip() else info.get("title", "无标题")
    if not final_title.strip():
        final_title = "无标题"
        
    item_type = "code" if info.get("is_code", is_code) else "text"

    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO items (id, type, title, content, tags, summary, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, item_type, final_title, final_content,
          json.dumps(info.get("tags", []), ensure_ascii=False),
          info.get("summary", ""), now, now))
    conn.commit()

    embed_text = f"{final_title}\n{info.get('summary', '')}\n{content[:2000]}"
    vec = await get_embedding(embed_text)
    conn.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)",
                 (item_id, vec.tobytes()))
    conn.commit()
    conn.close()

    return {"ok": True, "id": item_id, "summary": info.get("summary", ""),
            "tags": info.get("tags", []), "category": info.get("category", "")}

@app.get("/api/items")
async def list_items(
    page: int = 1,
    page_size: int = 30,
    type_filter: str = "",
    search: str = ""
):
    """列表查询"""
    conn = get_db()

    if search:
        results = await hybrid_search(search, page_size)
        return {"items": [_clean_item(i) for i in results], "total": len(results)}

    offset = (page - 1) * page_size
    if type_filter:
        items = conn.execute(
            "SELECT * FROM items WHERE type=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (type_filter, page_size, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM items WHERE type=?", (type_filter,)).fetchone()[0]
    else:
        items = conn.execute(
            "SELECT * FROM items ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (page_size, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    conn.close()

    return {"items": [_clean_item(dict(i)) for i in items], "total": total}

def _clean_item(item: dict) -> dict:
    """清理返回数据"""
    item.pop("file_path", None)
    if isinstance(item.get("tags"), str):
        try:
            item["tags"] = json.loads(item["tags"])
        except:
            item["tags"] = []
    return item

@app.get("/api/download/{item_id}")
async def download(item_id: str):
    """下载文件"""
    conn = get_db()
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Not found")
    row = dict(row)
    if row["type"] != "file" or not row.get("file_path"):
        raise HTTPException(400, "Not a file")
    return FileResponse(row["file_path"], filename=row["content"].split("\n")[0]
                        if row["content"] else Path(row["file_path"]).name)

@app.delete("/api/items/{item_id}")
async def delete_item(item_id: str):
    """删除"""
    conn = get_db()
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if row:
        row = dict(row)
        if row.get("file_path") and os.path.exists(row["file_path"]):
            os.remove(row["file_path"])
        conn.execute("DELETE FROM embeddings WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()
    conn.close()
    return {"ok": True}

@app.put("/api/items/{item_id}")
async def update_item(
    item_id: str,
    content: str = Form(...),
    title: str = Form(""),
):
    """编辑更新文本/代码"""
    conn = get_db()
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Not found")
    row = dict(row)
    if row["type"] == "file":
        conn.close()
        raise HTTPException(400, "Cannot edit file directly")

    # 重新AI分析以生成合适的title和tags，如果你不想覆盖原有tag，可以选择保留
    info = await ai_summarize_and_tag(title or row["title"], content)
    final_title = title if title.strip() else info.get("title", row["title"])
    item_type = "code" if info.get("is_code", row["type"] == "code") else "text"
    
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE items 
        SET title=?, content=?, summary=?, tags=?, type=?, updated_at=?
        WHERE id=?
    """, (final_title, content, info.get("summary", row["summary"]),
          json.dumps(info.get("tags", []), ensure_ascii=False),
          item_type, now, item_id))
    
    embed_text = f"{final_title}\n{info.get('summary', '')}\n{content[:2000]}"
    vec = await get_embedding(embed_text)
    conn.execute("UPDATE embeddings SET vector=? WHERE item_id=?", (vec.tobytes(), item_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/ask")
async def ask_question(request: Request):
    """自然语言提问 — RAG + AI 回答（流式）"""
    body = await request.json()
    question = body.get("question", "")
    if not question:
        raise HTTPException(400, "Question required")

    # RAG 检索相关条目
    results = await hybrid_search(question, 8)

    # 构建上下文
    context_parts = []
    for i, item in enumerate(results):
        tags = item.get("tags", "[]")
        if isinstance(tags, str):
            tags = json.loads(tags)
        ctx = f"[{i+1}] 类型:{item['type']} | 标题:{item.get('title','')} | 标签:{','.join(tags)} | 摘要:{item.get('summary','')}"
        if item.get("content") and item["type"] != "file":
            ctx += f"\n内容预览: {item['content'][:500]}"
        context_parts.append(ctx)

    context = "\n\n".join(context_parts) if context_parts else "（没有找到相关内容）"

    system_prompt = f"""你是一个智能知识助手。用户在局域网中收集了各种文件、代码片段和文字想法。
请根据以下检索到的内容回答用户问题。如果相关内容不足，请诚实说明。

检索到的内容：
{context}

回答要求：
- 用中文回答
- 引用具体内容时标注来源序号 [1][2] 等
- 如果涉及代码，给出完整可运行的代码
- 保持简洁但有深度"""

    async def generate():
        stream = await ai_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ], stream=True)
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield f"data: {json.dumps({'content': delta}, ensure_ascii=False)}\n\n"
        # 追加来源信息
        sources = [{"title": r.get("title", ""), "type": r["type"],
                     "tags": json.loads(r.get("tags", "[]")) if isinstance(r.get("tags"), str) else r.get("tags", []),
                     "summary": r.get("summary", ""), "id": r["id"]}
                    for r in results[:5]]
        yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.get("/api/stats")
async def stats():
    """统计"""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    files = conn.execute("SELECT COUNT(*) FROM items WHERE type='file'").fetchone()[0]
    texts = conn.execute("SELECT COUNT(*) FROM items WHERE type='text'").fetchone()[0]
    codes = conn.execute("SELECT COUNT(*) FROM items WHERE type='code'").fetchone()[0]
    total_size = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM items").fetchone()[0]
    conn.close()
    return {"total": total, "files": files, "texts": texts, "codes": codes,
            "total_size": total_size}

# ============================================================
# 前端 HTML（单文件，自适应 PC + 手机）
# ============================================================
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>KnowHub</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+SC:wght@300;400;500;700&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
/* ====== Markdown Body ====== */
.markdown-body { line-height: 1.6; }
.markdown-body p { margin-bottom: 1em; }
.markdown-body pre { background: rgba(0,0,0,0.3); padding: 12px; border-radius: var(--radius-sm); border: 1px solid var(--border); overflow-x: auto; margin-bottom: 1em; }
.markdown-body code { font-family: 'JetBrains Mono', monospace; font-size: 0.9em; }
.markdown-body :not(pre) > code { background: rgba(255,255,255,0.1); padding: 2px 4px; border-radius: 4px; color: var(--accent2); }
.markdown-body img { max-width: 100%; border-radius: var(--radius-sm); }

:root {
  --bg: #09090b;
  --surface: rgba(24, 24, 27, 0.65);
  --surface2: rgba(39, 39, 42, 0.65);
  --surface3: rgba(63, 63, 70, 0.65);
  --border: rgba(255, 255, 255, 0.1);
  --border-light: rgba(255, 255, 255, 0.15);
  --text: #fafafa;
  --text-dim: #a1a1aa;
  --text-muted: #71717a;
  --accent: #8b5cf6;
  --accent2: #a78bfa;
  --accent-glow: rgba(139, 92, 246, 0.2);
  --green: #10b981;
  --orange: #f59e0b;
  --red: #ef4444;
  --cyan: #06b6d4;
  --radius: 12px;
  --radius-sm: 8px;
  --radius-xs: 6px;
  --shadow: 0 4px 24px rgba(0,0,0,0.4);
  --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html { font-size: 15px; }
body {
  font-family: 'Noto Sans SC', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}
/* ====== Background ====== */
body::before {
  content: '';
  position: fixed; inset: 0;
  background:
    radial-gradient(ellipse 900px 700px at 10% 20%, rgba(139, 92, 246, 0.15), transparent),
    radial-gradient(ellipse 900px 500px at 90% 80%, rgba(6, 182, 212, 0.12), transparent),
    radial-gradient(ellipse 600px 400px at 50% 50%, rgba(16, 185, 129, 0.05), transparent);
  pointer-events: none; z-index: 0;
}
.sidebar, .header, .item-card, .quick-input-zone, .modal, .chat-input-wrap, .detail-content, .toast, .search-box input {
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}
/* ====== Layout ====== */
.app { display: flex; height: 100vh; position: relative; z-index: 1; }
.sidebar {
  width: 260px; min-width: 260px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  transition: transform var(--transition);
  z-index: 100;
}
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
/* ====== Sidebar ====== */
.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid var(--border);
}
.logo {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.4rem; font-weight: 700;
  background: linear-gradient(135deg, var(--accent), var(--cyan));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  letter-spacing: -0.5px;
}
.logo-sub { font-size: 0.7rem; color: var(--text-muted); margin-top: 2px; letter-spacing: 1px; }
.sidebar-nav { flex: 1; padding: 12px; overflow-y: auto; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; border-radius: var(--radius-sm);
  cursor: pointer; transition: all var(--transition);
  color: var(--text-dim); font-size: 0.9rem;
  margin-bottom: 2px;
}
.nav-item:hover { background: var(--surface2); color: var(--text); }
.nav-item.active { background: var(--accent-glow); color: var(--accent2); border: 1px solid rgba(108,92,231,0.2); }
.nav-item svg { width: 18px; height: 18px; flex-shrink: 0; }
.nav-badge {
  margin-left: auto; font-size: 0.7rem;
  background: var(--surface3); padding: 2px 8px; border-radius: 10px;
  font-family: 'JetBrains Mono', monospace;
}
.sidebar-stats {
  padding: 16px 20px;
  border-top: 1px solid var(--border);
  font-size: 0.75rem; color: var(--text-muted);
}
.stat-row { display: flex; justify-content: space-between; margin-bottom: 4px; }
/* ====== Header ====== */
.header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 16px;
  background: var(--surface);
}
.menu-btn {
  display: none; background: none; border: none; color: var(--text);
  cursor: pointer; padding: 4px;
}
.search-box {
  flex: 1; max-width: 480px;
  position: relative;
}
.search-box input {
  width: 100%; padding: 10px 14px 10px 38px;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--text);
  font-size: 0.9rem; outline: none;
  transition: border-color var(--transition);
  font-family: inherit;
}
.search-box input:focus { border-color: var(--accent); }
.search-box svg {
  position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  width: 16px; height: 16px; color: var(--text-muted);
}
/* ====== Content Area ====== */
.content { flex: 1; overflow-y: auto; padding: 24px; }
/* ====== Items Grid ====== */
.items-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.item-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  cursor: pointer;
  transition: all var(--transition);
  position: relative;
  overflow: hidden;
}
.item-card:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
  box-shadow: var(--shadow), 0 0 20px var(--accent-glow);
}
.item-card::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, var(--accent), var(--cyan));
  opacity: 0; transition: opacity var(--transition);
}
.item-card:hover::before { opacity: 1; }
.item-type-badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 0.7rem; padding: 3px 8px;
  border-radius: 4px; font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.5px;
  font-family: 'JetBrains Mono', monospace;
}
.badge-file { background: rgba(108,92,231,0.15); color: var(--accent2); }
.badge-text { background: rgba(0,184,148,0.15); color: var(--green); }
.badge-code { background: rgba(253,203,110,0.15); color: var(--orange); }
.item-title {
  font-size: 1rem; font-weight: 500;
  margin: 10px 0 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.item-summary {
  font-size: 0.8rem; color: var(--text-dim);
  line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.item-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.tag {
  font-size: 0.7rem; padding: 2px 8px;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 4px; color: var(--text-dim); transition: all var(--transition);
}
.tag:hover { background: var(--accent); color: white; border-color: var(--accent); }
.item-meta {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 12px; font-size: 0.72rem; color: var(--text-muted);
}
.item-actions {
  display: flex; gap: 6px; opacity: 0;
  transition: opacity var(--transition);
}
.item-card:hover .item-actions { opacity: 1; }
.icon-btn {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius-xs); padding: 6px;
  cursor: pointer; color: var(--text-dim);
  transition: all var(--transition);
  display: flex; align-items: center; justify-content: center;
}
.icon-btn:hover { background: var(--accent); color: white; border-color: var(--accent); }
.icon-btn svg { width: 14px; height: 14px; }
/* ====== AI Chat Panel ====== */
.chat-panel {
  display: none; flex-direction: column; height: 100%;
}
.chat-panel.active { display: flex; }
.chat-messages {
  flex: 1; overflow-y: auto; padding: 20px 0;
}
.chat-msg {
  max-width: 85%; margin-bottom: 16px;
  animation: fadeInUp 0.3s ease;
}
.chat-msg.user { margin-left: auto; }
.chat-bubble {
  padding: 12px 16px; border-radius: var(--radius);
  font-size: 0.9rem; line-height: 1.7;
}
.chat-msg.user .chat-bubble {
  background: var(--accent); color: white;
  border-bottom-right-radius: 4px;
}
.chat-msg.ai .chat-bubble {
  background: var(--surface2); border: 1px solid var(--border);
  border-bottom-left-radius: 4px;
}
.chat-bubble pre {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius-xs); padding: 12px;
  overflow-x: auto; margin: 8px 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.82rem;
}
.chat-bubble code {
  font-family: 'JetBrains Mono', monospace;
  background: var(--surface3); padding: 1px 5px;
  border-radius: 3px; font-size: 0.85em;
}
.chat-sources {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid var(--border);
  font-size: 0.75rem; color: var(--text-muted);
}
.chat-sources a {
  color: var(--accent2); text-decoration: none;
}
.chat-input-area {
  padding: 16px 0;
  border-top: 1px solid var(--border);
}
.chat-input-wrap {
  display: flex; gap: 10px;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 8px;
  transition: border-color var(--transition);
}
.chat-input-wrap:focus-within { border-color: var(--accent); }
.chat-input {
  flex: 1; background: none; border: none;
  color: var(--text); font-size: 0.9rem;
  outline: none; resize: none;
  font-family: inherit; min-height: 24px; max-height: 120px;
}
.chat-send {
  background: var(--accent); border: none;
  color: white; border-radius: var(--radius-sm);
  padding: 8px 16px; cursor: pointer;
  font-weight: 500; font-size: 0.85rem;
  transition: all var(--transition);
  font-family: inherit;
}
.chat-send:hover { background: var(--accent2); }
.chat-send:disabled { opacity: 0.5; cursor: not-allowed; }
/* ====== Quick Input Zone ====== */
.quick-input-zone {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px; margin-bottom: 20px;
  transition: all var(--transition); position: relative;
}
.quick-input-zone:focus-within { border-color: var(--accent); }
.quick-input-zone.dragover { border-color: var(--accent); background: var(--accent-glow); border-style: dashed; }
.quick-input-textarea {
  width: 100%; min-height: 80px; background: transparent; border: none;
  color: var(--text); font-family: inherit; font-size: 0.95rem;
  resize: vertical; outline: none; margin-bottom: 12px;
}
.quick-input-actions {
  display: flex; justify-content: space-between; align-items: center;
  border-top: 1px solid var(--border); padding-top: 12px;
}
.quick-input-actions .left-actions { display: flex; gap: 10px; align-items: center; }
.quick-input-actions .right-actions { display: flex; gap: 10px; }
.upload-btn {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--text-dim); cursor: pointer; font-size: 0.85rem; padding: 6px 12px;
  border-radius: var(--radius-sm); border: 1px dashed var(--border);
  transition: all var(--transition);
}
.upload-btn:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-glow); }
.upload-btn svg { width: 16px; height: 16px; }
/* ====== Modal ====== */
.modal-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
  z-index: 200; align-items: center; justify-content: center;
  padding: 20px;
}
.modal-overlay.active { display: flex; }
.modal {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 24px;
  max-width: 600px; width: 100%; max-height: 80vh;
  overflow-y: auto; animation: fadeInUp 0.3s ease;
}
.modal h3 { margin-bottom: 16px; font-size: 1.1rem; }
.modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
.btn {
  padding: 8px 20px; border-radius: var(--radius-sm);
  border: 1px solid var(--border); background: var(--surface2);
  color: var(--text); cursor: pointer; font-size: 0.85rem;
  transition: all var(--transition); font-family: inherit;
}
.btn:hover { background: var(--surface3); }
.btn-primary { background: var(--accent); border-color: var(--accent); color: white; }
.btn-primary:hover { background: var(--accent2); }
.btn-danger { border-color: var(--red); color: var(--red); }
.btn-danger:hover { background: var(--red); color: white; }
/* ====== Text Input ====== */
.text-input-area textarea {
  width: 100%; min-height: 200px; padding: 14px;
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem; resize: vertical; outline: none;
  transition: border-color var(--transition);
}
.text-input-area textarea:focus { border-color: var(--accent); }
.text-input-area .title-input {
  width: 100%; padding: 10px 14px; margin-bottom: 10px;
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--text);
  font-size: 0.9rem; outline: none; font-family: inherit;
}
.text-input-area .title-input:focus { border-color: var(--accent); }
.toggle-row {
  display: flex; align-items: center; gap: 10px; margin: 10px 0;
  font-size: 0.85rem; color: var(--text-dim);
}
.toggle {
  width: 36px; height: 20px; background: var(--surface3);
  border-radius: 10px; position: relative; cursor: pointer;
  transition: background var(--transition); border: 1px solid var(--border);
}
.toggle.on { background: var(--accent); border-color: var(--accent); }
.toggle::after {
  content: ''; position: absolute;
  width: 16px; height: 16px; border-radius: 50%;
  background: white; top: 1px; left: 1px;
  transition: transform var(--transition);
}
.toggle.on::after { transform: translateX(16px); }
/* ====== Empty State ====== */
.empty-state {
  text-align: center; padding: 60px 20px;
  color: var(--text-muted);
}
.empty-state svg { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.3; }
.empty-state h3 { font-size: 1rem; margin-bottom: 8px; color: var(--text-dim); }
/* ====== Loading ====== */
.loading-dots::after {
  content: ''; animation: dots 1.5s steps(4, end) infinite;
}
@keyframes dots {
  0% { content: ''; } 25% { content: '.'; }
  50% { content: '..'; } 75% { content: '...'; }
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
/* ====== Toast ====== */
.toast {
  position: fixed; bottom: 24px; right: 24px;
  background: var(--surface2); border: 1px solid var(--border);
  padding: 12px 20px; border-radius: var(--radius-sm);
  font-size: 0.85rem; z-index: 300;
  animation: fadeInUp 0.3s ease;
  box-shadow: var(--shadow);
}
.toast.success { border-color: var(--green); }
.toast.error { border-color: var(--red); }
/* ====== Responsive ====== */
@media (max-width: 768px) {
  .sidebar {
    position: fixed; left: 0; top: 0; bottom: 0;
    transform: translateX(-100%);
  }
  .sidebar.open { transform: translateX(0); }
  .menu-btn { display: block; }
  .items-grid { grid-template-columns: 1fr; }
  .content { padding: 16px; }
  .header { padding: 12px 16px; }
}
/* ====== Scrollbar ====== */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--surface3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-light); }
/* ====== Detail View ====== */
.detail-view { display: none; }
.detail-view.active { display: block; }
.detail-back {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--text-dim); cursor: pointer; font-size: 0.85rem;
  margin-bottom: 16px; transition: color var(--transition);
}
.detail-back:hover { color: var(--text); }
.detail-content {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 20px;
  white-space: pre-wrap; font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem; line-height: 1.8;
  max-height: 60vh; overflow-y: auto;
}
</style>
</head>
<body>
<div class="app">
  <!-- Sidebar -->
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <div class="logo">KnowHub</div>
      <div class="logo-sub">LOCAL AI HUB</div>
    </div>
    <nav class="sidebar-nav">
      <div class="nav-item active" data-view="all" onclick="switchView('all')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        全部内容
        <span class="nav-badge" id="badge-all">0</span>
      </div>
      <div class="nav-item" data-view="file" onclick="switchView('file')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/></svg>
        文件
        <span class="nav-badge" id="badge-file">0</span>
      </div>
      <div class="nav-item" data-view="text" onclick="switchView('text')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 10H3M21 6H3M21 14H3M17 18H3"/></svg>
        文字想法
        <span class="nav-badge" id="badge-text">0</span>
      </div>
      <div class="nav-item" data-view="code" onclick="switchView('code')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16,18 22,12 16,6"/><polyline points="8,6 2,12 8,18"/></svg>
        代码片段
        <span class="nav-badge" id="badge-code">0</span>
      </div>
      <div class="nav-item" data-view="ai" onclick="switchView('ai')" style="margin-top: 12px; border-top: 1px solid var(--border); padding-top: 14px;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
        AI 问答
      </div>
    </nav>
    <div class="sidebar-stats" id="sidebar-stats">
      <div class="stat-row"><span>总计</span><span id="stat-total">0 项</span></div>
      <div class="stat-row"><span>存储</span><span id="stat-size">0 B</span></div>
    </div>
  </aside>

  <!-- Main -->
  <div class="main">
    <div class="header">
      <button class="menu-btn" onclick="toggleSidebar()">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
      <div class="search-box">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" id="searchInput" placeholder="搜索内容、代码、想法..." oninput="debounceSearch()" onkeydown="if(event.key==='Enter' && this.value.trim()) { switchView('ai'); document.getElementById('chatInput').value = this.value; sendChat(); this.value=''; }">
      </div>
      <button class="btn btn-primary" onclick="showAddModal()">+ 添加</button>
    </div>

    <div class="content">
      <!-- Items View -->
      <div id="items-view">
        <div class="quick-input-zone" id="dropZone">
          <textarea class="quick-input-textarea" id="quickAddContent" placeholder="在此直接粘贴文字、网页链接、代码，写下想法；或拖入文件上传（支持 PDF/Word/PPT/Excel/HTML/CSV/ZIP/图片/代码等）...、代码，写下想法；或直接拖入文件以上传..." onkeydown="if(event.ctrlKey && event.key==='Enter') submitQuickAdd()"></textarea>
          <div class="quick-input-actions">
            <div class="left-actions">
              <label class="upload-btn" onclick="document.getElementById('fileInput').click()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17,8 12,3 7,8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                浏览文件
              </label>
              <input type="file" id="fileInput" multiple style="display:none" onchange="handleFiles(this.files)">
            </div>
            <div class="right-actions">
              <button class="btn btn-primary" onclick="submitQuickAdd()">一键保存 (Ctrl+Enter)</button>
            </div>
          </div>
        </div>
        <div class="items-grid" id="itemsGrid"></div>
        <div class="empty-state" id="emptyState" style="display:none">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
          <h3>还没有内容</h3>
          <p>上传文件或添加文字开始使用</p>
        </div>
      </div>

      <!-- Detail View -->
      <div class="detail-view" id="detailView">
        <div class="detail-back" style="display:flex; justify-content:space-between">
          <div onclick="closeDetail()" style="display:inline-flex; align-items:center; gap:6px; cursor:pointer;" class="back-link">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15,18 9,12 15,6"/></svg>
            返回列表
          </div>
          <div id="detailEditBtnContainer"></div>
        </div>
        <div id="detailContent"></div>
      </div>

      <!-- AI Chat View -->
      <div class="chat-panel" id="chatPanel">
        <div class="chat-messages" id="chatMessages">
          <div class="chat-msg ai">
            <div class="chat-bubble">
              你好！我可以帮你搜索和分析已保存的所有内容。试试问我：
              <br><br>
              "上周保存了哪些 Python 代码？"<br>
              "关于数据库设计的笔记有哪些？"<br>
              "帮我总结一下所有关于项目的文件"
            </div>
          </div>
        </div>
        <div class="chat-input-area">
          <div class="chat-input-wrap">
            <textarea class="chat-input" id="chatInput" placeholder="用自然语言提问..." rows="1"
              onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}"></textarea>
            <button class="chat-send" id="chatSend" onclick="sendChat()">发送</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Add Modal -->
<div class="modal-overlay" id="addModal">
  <div class="modal">
    <h3>添加内容</h3>
    <div class="text-input-area">
      <input class="title-input" id="addTitle" placeholder="标题（可选）">
      <textarea id="addContent" placeholder="粘贴文字、代码或任何想法..."></textarea>
      <div class="toggle-row">
        <div class="toggle" id="codeToggle" onclick="this.classList.toggle('on')"></div>
        <span>标记为代码片段</span>
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeAddModal()">取消</button>
      <button class="btn btn-primary" onclick="submitText()">保存</button>
    </div>
  </div>
</div>

<script>
// State
let currentView = 'all';
let allItems = [];
let searchTimer = null;

// Init
document.addEventListener('DOMContentLoaded', () => {
  loadItems();
  loadStats();
  initDragDrop();
});

// Drag & Drop
function initDragDrop() {
  const zone = document.getElementById('dropZone');
  ['dragenter', 'dragover'].forEach(e => {
    zone.addEventListener(e, ev => { ev.preventDefault(); zone.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(e => {
    zone.addEventListener(e, ev => { ev.preventDefault(); zone.classList.remove('dragover'); });
  });
  zone.addEventListener('drop', ev => handleFiles(ev.dataTransfer.files));
  // Global drop
  document.body.addEventListener('dragover', ev => ev.preventDefault());
  document.body.addEventListener('drop', ev => { ev.preventDefault(); handleFiles(ev.dataTransfer.files); });
}

// File Upload
async function handleFiles(files) {
  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    toast(`上传中: ${file.name}...`);
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.ok) {
        toast(`已保存: ${file.name}`, 'success');
      }
    } catch (e) {
      toast(`上传失败: ${file.name}`, 'error');
    }
  }
  loadItems();
  loadStats();
}

// Load Items
async function loadItems(search = '') {
  const params = new URLSearchParams();
  if (currentView !== 'all' && currentView !== 'ai') params.set('type_filter', currentView);
  if (search) params.set('search', search);

  try {
    const res = await fetch(`/api/items?${params}`);
    const data = await res.json();
    allItems = data.items;
    renderItems();
  } catch (e) {
    console.error(e);
  }
}

function renderItems() {
  const grid = document.getElementById('itemsGrid');
  const empty = document.getElementById('emptyState');

  if (allItems.length === 0) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  grid.innerHTML = allItems.map(item => {
    const tags = Array.isArray(item.tags) ? item.tags : [];
    const time = new Date(item.created_at).toLocaleString('zh-CN', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
    const size = item.file_size ? formatSize(item.file_size) : '';

    return `
      <div class="item-card" onclick="showDetail('${item.id}')">
        <span class="item-type-badge badge-${item.type}">
          ${item.type === 'file' ? '文件' : item.type === 'code' ? '代码' : '文字'}
        </span>
        <div class="item-title">${esc(item.title || '无标题')}</div>
        <div class="item-summary">${esc(item.summary || '')}</div>
        <div class="item-tags">${tags.slice(0, 4).map(t => `<span class="tag" onclick="event.stopPropagation(); filterByTag('${esc(t)}')">${esc(t)}</span>`).join('')}</div>
        <div class="item-meta">
          <span>${time} ${size ? '· ' + size : ''}</span>
          <div class="item-actions">
            ${item.type === 'file' ? `<button class="icon-btn" onclick="event.stopPropagation();downloadItem('${item.id}')" title="下载">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            </button>` : ''}
            <button class="icon-btn" onclick="event.stopPropagation();deleteItem('${item.id}')" title="删除">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// Detail
function showDetail(id) {
  const item = allItems.find(i => i.id === id);
  if (!item) return;

  document.getElementById('items-view').style.display = 'none';
  document.getElementById('chatPanel').classList.remove('active');
  const dv = document.getElementById('detailView');
  dv.classList.add('active');

  const tags = Array.isArray(item.tags) ? item.tags : [];
  let html = `
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px">
      <span class="item-type-badge badge-${item.type}">
        ${item.type === 'file' ? '文件' : item.type === 'code' ? '代码' : '文字'}
      </span>
      <h2 style="font-size:1.2rem">${esc(item.title || '无标题')}</h2>
    </div>
    <p style="color:var(--text-dim);margin-bottom:16px;font-size:0.85rem">${esc(item.summary || '')}</p>
    <div class="item-tags" style="margin-bottom:16px">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
  `;

  if (item.type === 'file') {
    html += `<p style="margin-bottom:12px"><a href="/api/download/${item.id}" class="btn btn-primary" style="display:inline-flex;text-decoration:none">下载文件</a></p>`;
    document.getElementById('detailEditBtnContainer').innerHTML = '';
  } else {
    document.getElementById('detailEditBtnContainer').innerHTML = `<button class="btn" style="padding:4px 10px; font-size:0.8rem" onclick="editItem('${item.id}')">编辑内容</button>`;
  }

  if (item.content && item.type !== 'file') {
    const renderedContent = item.type === 'code' ? `<pre><code>${esc(item.content)}</code></pre>` : marked.parse(item.content);
    html += `<div style="position:relative; margin-top:20px;">
               <button class="icon-btn" style="position:absolute; top:12px; right:12px; z-index:10; background:var(--surface3)" onclick="copyToClipboard('${item.id}', this)" title="复制内容">
                 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
               </button>
               <div class="detail-content markdown-body" style="padding-top:40px">${renderedContent}</div>
             </div>`;
  } else if (item.type === 'file' && item.content) {
    html += `<p style="color:var(--text-muted);font-size:0.8rem;margin-bottom:8px">文件内容预览：</p>`;
    html += `<div class="detail-content">${esc(item.content.slice(0, 5000))}</div>`;
  }

  document.getElementById('detailContent').innerHTML = html;
  
  if (item.type !== 'file') {
    document.querySelectorAll('#detailContent pre code').forEach((el) => {
      hljs.highlightElement(el);
    });
  }
}

function editItem(id) {
  const item = allItems.find(i => i.id === id);
  if (!item) return;

  const html = `
      <div class="text-input-area" style="margin-top: 10px">
        <input class="title-input" id="editTitle" value="${esc(item.title || '')}" placeholder="标题（可选）">
        <textarea id="editContent" style="min-height:300px; width:100%; padding:14px; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:var(--radius-sm); font-family:'JetBrains Mono', monospace; outline: none; margin-top:10px">${esc(item.content || '')}</textarea>
      </div>
      <div style="margin-top:16px; text-align:right;">
        <button class="btn" onclick="showDetail('${id}')">取消</button>
        <button class="btn btn-primary" onclick="saveEdit('${id}')">保存修改</button>
      </div>
  `;
  document.getElementById('detailContent').innerHTML = html;
  document.getElementById('detailEditBtnContainer').innerHTML = '';
}

async function saveEdit(id) {
  const title = document.getElementById('editTitle').value;
  const content = document.getElementById('editContent').value;
  if (!content.trim()) { toast('内容不能为空', 'error'); return; }

  const fd = new FormData();
  fd.append('content', content);
  fd.append('title', title);

  try {
    toast('保存中...');
    const res = await fetch(`/api/items/${id}`, { method: 'PUT', body: fd });
    const data = await res.json();
    if (data.ok) {
      toast('修改已保存', 'success');
      await loadItems();
      showDetail(id);
    }
  } catch(e) {
    toast('保存失败', 'error');
  }
}

function copyToClipboard(id, btn) {
  const item = allItems.find(i => i.id === id);
  const content = item ? item.content : '';
  
  const success = () => {
    const oldHtml = btn.innerHTML;
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    toast('已复制到剪贴板', 'success');
    setTimeout(() => btn.innerHTML = oldHtml, 2000);
  };

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(content).then(success).catch(() => toast('复制失败', 'error'));
  } else {
    // 局域网 HTTP 访问的兼容降级
    const ta = document.createElement('textarea');
    ta.value = content;
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      success();
    } catch(e) {
      toast('浏览器拦截了复制请求', 'error');
    }
    document.body.removeChild(ta);
  }
}

function filterByTag(tag) {
  document.getElementById('searchInput').value = tag;
  loadItems(tag);
}

function closeDetail() {
  document.getElementById('detailView').classList.remove('active');
  document.getElementById('items-view').style.display = 'block';
}

// Delete
async function deleteItem(id) {
  if (!confirm('确定删除？')) return;
  await fetch(`/api/items/${id}`, { method: 'DELETE' });
  closeDetail();
  loadItems();
  loadStats();
  toast('已删除', 'success');
}

// Download
function downloadItem(id) {
  window.open(`/api/download/${id}`);
}

// View Switch
function switchView(view) {
  currentView = view;
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.querySelector(`[data-view="${view}"]`)?.classList.add('active');

  if (view === 'ai') {
    document.getElementById('items-view').style.display = 'none';
    document.getElementById('detailView').classList.remove('active');
    document.getElementById('chatPanel').classList.add('active');
  } else {
    document.getElementById('chatPanel').classList.remove('active');
    document.getElementById('detailView').classList.remove('active');
    document.getElementById('items-view').style.display = 'block';
    loadItems(document.getElementById('searchInput').value);
  }

  // Close sidebar on mobile
  document.getElementById('sidebar').classList.remove('open');
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

// Search
function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    loadItems(document.getElementById('searchInput').value);
  }, 300);
}

// Add Text Modal
function showAddModal() { document.getElementById('addModal').classList.add('active'); }
function closeAddModal() {
  document.getElementById('addModal').classList.remove('active');
  document.getElementById('addTitle').value = '';
  document.getElementById('addContent').value = '';
  document.getElementById('codeToggle').classList.remove('on');
}

async function submitText() {
  const title = document.getElementById('addTitle').value;
  const content = document.getElementById('addContent').value;
  const isCode = document.getElementById('codeToggle').classList.contains('on');

  if (!content.trim()) { toast('请输入内容', 'error'); return; }

  const fd = new FormData();
  fd.append('content', content);
  fd.append('title', title);
  fd.append('is_code', isCode);

  try {
    const res = await fetch('/api/text', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.ok) {
      toast('已保存', 'success');
      closeAddModal();
      loadItems();
      loadStats();
    }
  } catch (e) {
    toast('保存失败', 'error');
  }
}

// Quick Add
async function submitQuickAdd() {
  const content = document.getElementById('quickAddContent').value;
  if (!content.trim()) { toast('请输入内容', 'error'); return; }

  const fd = new FormData();
  fd.append('content', content);
  fd.append('title', ''); // 空标题，由后端自动生成或截取
  // is_code 参数省略，由后端AI自动识别

  try {
    const res = await fetch('/api/text', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.ok) {
      toast('已保存', 'success');
      document.getElementById('quickAddContent').value = '';
      loadItems();
      loadStats();
    }
  } catch (e) {
    toast('保存失败', 'error');
  }
}

// AI Chat
async function sendChat() {
  const input = document.getElementById('chatInput');
  const question = input.value.trim();
  if (!question) return;

  input.value = '';
  input.style.height = 'auto';

  const msgs = document.getElementById('chatMessages');
  msgs.innerHTML += `<div class="chat-msg user"><div class="chat-bubble">${esc(question)}</div></div>`;

  const aiMsg = document.createElement('div');
  aiMsg.className = 'chat-msg ai';
  aiMsg.innerHTML = `<div class="chat-bubble"><span class="loading-dots">思考中</span></div>`;
  msgs.appendChild(aiMsg);
  msgs.scrollTop = msgs.scrollHeight;

  document.getElementById('chatSend').disabled = true;

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let answer = '';
    let sources = [];

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value, { stream: true });
      const lines = text.split('\n');

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') {
          aiMsg.querySelectorAll('.markdown-body pre code').forEach(el => hljs.highlightElement(el));
          continue;
        }

        try {
          const parsed = JSON.parse(data);
          if (parsed.content) {
            answer += parsed.content;
            aiMsg.querySelector('.chat-bubble').innerHTML = `<div class="markdown-body">${marked.parse(answer)}</div>`;
          }
          if (parsed.sources) sources = parsed.sources;
        } catch {}
      }
    }

    if (sources.length) {
      aiMsg.querySelector('.chat-bubble').innerHTML += `
        <div class="chat-sources">
          相关来源: ${sources.map((s, i) =>
            `<a href="#" onclick="event.preventDefault();showDetail('${s.id}');switchView('all')">[${i+1}] ${esc(s.title || s.type)}</a>`
          ).join(' ')}
        </div>
      `;
    }

    msgs.scrollTop = msgs.scrollHeight;
  } catch (e) {
    aiMsg.querySelector('.chat-bubble').textContent = 'AI 服务连接失败，请检查配置';
  }

  document.getElementById('chatSend').disabled = false;
}

// Stats
async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    const s = await res.json();
    document.getElementById('badge-all').textContent = s.total;
    document.getElementById('badge-file').textContent = s.files;
    document.getElementById('badge-text').textContent = s.texts;
    document.getElementById('badge-code').textContent = s.codes;
    document.getElementById('stat-total').textContent = `${s.total} 项`;
    document.getElementById('stat-size').textContent = formatSize(s.total_size);
  } catch {}
}

// Helpers
function esc(str) {
  const d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(1) + ' GB';
}

function renderMarkdown(text) {
  try {
    return marked.parse(text);
  } catch(e) {
    return text;
  }
}

function toast(msg, type = '') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

async function handleFiles(files) {
  if (!files.length) return;
  toast(`正在上传并分析 ${files.length} 个文件，请稍候...`);
  
  for (let file of files) {
      const fd = new FormData();
      fd.append('file', file);
      try {
          const res = await fetch('/api/upload', { method: 'POST', body: fd });
          if (!res.ok) throw new Error();
      } catch(e) {
          toast(`上传 ${file.name} 失败`, 'error');
      }
  }
  toast('全部文件已保存并提取完毕', 'success');
  loadItems();
  loadStats();
}

// Auto-resize chat input
document.getElementById('chatInput').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

// Drag & drop logic
const dropZone = document.getElementById('dropZone');
if (dropZone) {
  dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', () => {
      dropZone.classList.remove('dragover');
  });
  dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      if (e.dataTransfer.files.length) {
          handleFiles(e.dataTransfer.files);
      }
  });
}
</script>
</body>
</html>"""

# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print(f"""
╔══════════════════════════════════════════════╗
║  KnowHub — 智能知识管理平台              ║
╠══════════════════════════════════════════════╣
║  本机访问: http://localhost:{PORT}             ║
║  局域网:   http://<本机IP>:{PORT}              ║
╠══════════════════════════════════════════════╣
║  AI 接口: {AI_BASE_URL:<31}║
║  模型:    {AI_MODEL:<31}║
╚══════════════════════════════════════════════╝
    """)
    uvicorn.run(app, host=HOST, port=PORT)
