"""Unified tool registry for KnowHub AI agent.

All tools defined here. Both WeChat and web /api/ask pull from the same registry.
ReAct loop included for multi-round tool calling.
"""

import json
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable, AsyncGenerator

logger = logging.getLogger("knowhub")


@dataclass
class ToolEntry:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[str]]
    category: str = ""
    channels: list[str] = field(default_factory=lambda: ["all"])


TOOL_REGISTRY: dict[str, ToolEntry] = {}


def tool(name: str, description: str, parameters: dict, category: str = "", channels: list[str] = None):
    """Decorator that registers an async function as an AI-callable tool."""
    def decorator(fn):
        TOOL_REGISTRY[name] = ToolEntry(
            name=name, description=description, parameters=parameters,
            handler=fn, category=category, channels=channels or ["all"],
        )
        return fn
    return decorator


def get_openai_tools(channels: list[str] = None) -> list[dict]:
    """Return OpenAI function-calling format tool list, filtered by channel."""
    result = []
    for entry in TOOL_REGISTRY.values():
        if channels and entry.channels != ["all"]:
            if not any(ch in entry.channels for ch in channels):
                continue
        result.append({
            "type": "function",
            "function": {
                "name": entry.name,
                "description": entry.description,
                "parameters": entry.parameters,
            }
        })
    return result


async def execute_tool(name: str, args: dict, ctx: dict = None) -> str:
    """Execute a tool by name. Wraps errors so agent loop never crashes."""
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return f"Unknown tool: {name}"
    try:
        return await entry.handler(args, ctx or {})
    except Exception as e:
        print(f"[Tool] {name} error: {e}", flush=True)
        return f"Tool [{name}] error: {e}"


# ── ReAct Loop ──────────────────────────────────────────────────────────────

async def react_loop(messages: list, tools: list, ctx: dict, max_rounds: int = 10) -> AsyncGenerator[str, None]:
    """ReAct: Reason -> Act -> Observe. Yields SSE chunks."""
    from backend.ai_services import ai_chat

    for round_i in range(max_rounds):
        response = await ai_chat(messages, stream=True, tools=tools)

        full_content = ""
        tool_calls = []
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # Stream text tokens
            if delta.content:
                full_content += delta.content
                yield f"data: {json.dumps({'content': delta.content}, ensure_ascii=False)}\n\n"
            # Collect tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index or 0
                    while len(tool_calls) <= idx:
                        tool_calls.append({"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls[idx]["arguments"] += tc.function.arguments

        # No tool calls -> final answer
        if not tool_calls or not any(tc["name"] for tc in tool_calls):
            break

        # Build assistant message with tool_calls
        assistant_msg = {"role": "assistant", "content": full_content or None}
        assistant_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in tool_calls if tc["name"]
        ]
        messages.append(assistant_msg)

        # Execute each tool
        for tc in tool_calls:
            if not tc["name"]:
                continue
            try:
                args = json.loads(tc["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            yield f"data: {json.dumps({'tool_call': tc['name'], 'status': 'running'}, ensure_ascii=False)}\n\n"
            result = await execute_tool(tc["name"], args, ctx)
            yield f"data: {json.dumps({'tool_call': tc['name'], 'result': result[:500]}, ensure_ascii=False)}\n\n"
            messages.append({
                "role": "tool", "tool_call_id": tc["id"],
                "name": tc["name"], "content": result
            })

    yield "data: [DONE]\n\n"


# ═══════════════════════════════════════════════════════════════════════════
# Tool Definitions — 16 tools
# ═══════════════════════════════════════════════════════════════════════════

# ── 1. Search ────────────────────────────────────────────────────────────────

@tool(
    name="search_knowledge",
    description="搜索知识库。当用户提问、回忆内容、需要知识总结时调用。返回匹配项的摘要，需由 AI 综合给出答案。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
            "top_k": {"type": "integer", "description": "返回数量，默认5"}
        },
        "required": ["query"]
    },
    category="search",
)
async def _search_knowledge(args, ctx):
    from backend.ai_services import hybrid_search
    results = await hybrid_search(args["query"], top_k=args.get("top_k", 5))
    if not results:
        return "知识库中无匹配结果"
    parts = []
    for i, r in enumerate(results):
        title = r.get("title", "")
        summary = r.get("summary", "")
        tags = r.get("tags", "[]")
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError): tags = []
        tags_str = ", ".join(tags) if isinstance(tags, list) else ""
        item_type = r.get("type", "")
        # GitHub repos: include stars
        if r.get("full_name"):
            stars = r.get("stars", 0) or 0
            parts.append(f"[{i+1}] GitHub ⭐{stars} | {r['full_name']} ({r.get('language','')})\n  {r.get('ai_summary') or summary}")
        else:
            parts.append(f"[{i+1}] {title} ({item_type}) 标签:{tags_str}\n  {summary}")
    return "\n\n".join(parts)


@tool(
    name="search_memory",
    description="搜索 agent 记忆库。查找用户偏好、历史对话中的事实等长期记忆。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "top": {"type": "integer", "description": "返回数量，默认5"}
        },
        "required": ["query"]
    },
    category="search",
)
async def _search_memory(args, ctx):
    from backend.gitmem0_client import search
    results = await search(args["query"], top=args.get("top", 5))
    if not results:
        return "记忆库中无匹配结果"
    return "\n".join([f"- {r.get('content', '')}" for r in results])


@tool(
    name="search_github_stars",
    description="搜索已收藏的 GitHub 仓库。可按语言、关键词筛选。",
    parameters={
        "type": "object",
        "properties": {
            "search": {"type": "string", "description": "搜索关键词"},
            "language": {"type": "string", "description": "编程语言筛选"},
            "page_size": {"type": "integer", "description": "返回数量，默认10"}
        }
    },
    category="search",
)
async def _search_github_stars(args, ctx):
    from backend.database import get_db
    conn = get_db()
    try:
        where_parts = ["1=1"]
        params = []
        if args.get("language"):
            where_parts.append("gr.language = ?")
            params.append(args["language"])
        if args.get("search"):
            where_parts.append("(i.title LIKE ? OR gr.ai_summary LIKE ?)")
            params.extend([f"%{args['search']}%"] * 2)
        where = " AND ".join(where_parts)
        page_size = args.get("page_size", 10)
        rows = conn.execute(f"""
            SELECT gr.full_name, gr.stars, gr.language, gr.ai_summary, i.title
            FROM github_repos gr JOIN items i ON i.id = gr.item_id
            WHERE {where} ORDER BY gr.stars DESC LIMIT ?
        """, params + [page_size]).fetchall()
        if not rows:
            return "收藏的仓库中无匹配结果"
        return "\n".join([
            f"⭐{r['stars']:,} {r['full_name']} ({r['language'] or 'N/A'})\n  {r['ai_summary'] or r['title'] or ''}"
            for r in rows
        ])
    finally:
        conn.close()


# ── 2. Retrieval ─────────────────────────────────────────────────────────────

@tool(
    name="get_raw_document",
    description="提取原件。当用户要求获取原始文档、图片或文件时调用。微信端会直接发送文件。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词定位原件"}
        },
        "required": ["query"]
    },
    category="retrieval",
)
async def _get_raw_document(args, ctx):
    from backend.ai_services import hybrid_search
    from backend.config import DATA_DIR
    import os

    search_res = await hybrid_search(args["query"], top_k=1)
    if not search_res:
        return "知识库中未找到匹配的原件"

    item = search_res[0]
    item_id = item.get("id", "")

    if item.get("type") in ("file", "image"):
        # Find physical file
        target_path = ""
        for p in DATA_DIR.glob(f"{item_id}*"):
            if p.is_file():
                target_path = str(p)

        if ctx.get("channel") == "wechat" and ctx.get("send_file") and target_path:
            await ctx["send_file"](target_path, item)
            return "文件已发送给用户"
        elif target_path:
            return f"文件: {item.get('title', '')} (ID: {item_id})\n可通过 /api/download/{item_id} 下载"
        else:
            return "服务器中没有该文件的物理备份"
    else:
        text_body = item.get("content", "")
        return f"📄 {item.get('title', '无标题')}\n{'-'*15}\n{text_body[:2000]}"


@tool(
    name="get_item_detail",
    description="获取某条知识项的完整详情。",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "知识项 ID"}
        },
        "required": ["item_id"]
    },
    category="retrieval",
)
async def _get_item_detail(args, ctx):
    from backend.database import get_db
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (args["item_id"],)).fetchone()
        if not row:
            return "未找到该知识项"
        row = dict(row)
        return (f"标题: {row['title']}\n类型: {row['type']}\n标签: {row['tags']}\n"
                f"摘要: {row['summary']}\n\n{row.get('content', '')[:3000]}")
    finally:
        conn.close()


@tool(
    name="get_related_items",
    description="获取与某条知识项语义相关的其他项目。",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "知识项 ID"}
        },
        "required": ["item_id"]
    },
    category="retrieval",
)
async def _get_related_items(args, ctx):
    from backend.database import get_db
    from backend.qmd.models import get_embed_dim
    from backend.ai_services import cosine_similarity
    import numpy as np

    item_id = args["item_id"]
    conn = get_db()
    try:
        embed_row = conn.execute("SELECT vector FROM embeddings WHERE item_id=?", (item_id,)).fetchone()
        if not embed_row:
            return "未找到该项目的向量数据"
        query_vec = np.frombuffer(embed_row[0], dtype=np.float32)
        items = conn.execute("""
            SELECT i.*, e.vector FROM items i JOIN embeddings e ON i.id = e.item_id
            WHERE i.id != ? ORDER BY i.created_at DESC
        """, (item_id,)).fetchall()
    finally:
        conn.close()

    if not items:
        return "无关联项"

    dim = get_embed_dim()
    scored = []
    for row in items:
        vec = np.frombuffer(row["vector"], dtype=np.float32)
        if vec.shape[0] != dim:
            continue
        sim = cosine_similarity(query_vec, vec)
        if sim > 0.4:
            scored.append((sim, dict(row)))

    if not scored:
        return "未找到语义相关的项目"

    scored.sort(key=lambda x: x[0], reverse=True)
    return "\n".join([
        f"[相关度:{sim:.2f}] {r['title']}: {r.get('summary', '')[:100]}"
        for sim, r in scored[:5]
    ])


# ── 3. Content ───────────────────────────────────────────────────────────────

@tool(
    name="save_to_brain",
    description="核心工具：将文本、代码、链接保存到知识库。用户说「保存」「存下来」或分享长文/链接时必须调用。",
    parameters={
        "type": "object",
        "properties": {
            "content_to_save": {"type": "string", "description": "要保存的完整内容或URL"},
            "title": {"type": "string", "description": "可选标题"}
        },
        "required": ["content_to_save"]
    },
    category="content",
)
async def _save_to_brain(args, ctx):
    from backend.ai_services import ai_summarize_and_tag, fetch_url_content, get_embedding
    from backend.database import get_db
    from backend.gitmem0_client import remember as gm_remember
    import uuid, re
    from datetime import datetime

    to_save = args["content_to_save"]
    item_id = uuid.uuid4().hex[:12]

    url_match = re.search(r'(https?://[^\s]+)', to_save)
    scraped = ""
    if url_match:
        scraped = await fetch_url_content(url_match.group(1))
        content_full = f"{to_save}\n\n[智能抽取网页]\n{scraped}" if scraped else to_save
    else:
        content_full = to_save

    should_rewrite = bool(scraped) or len(content_full) > 50
    info = await ai_summarize_and_tag(args.get("title", ""), content_full, rewrite=should_rewrite)

    final_content = info.get("formatted_content", content_full) if should_rewrite else content_full
    final_title = args.get("title") or info.get("title", to_save[:15] + "...")
    item_type = "code" if info.get("is_code", False) else "text"

    now = datetime.now().isoformat()
    tags = json.dumps(["自动捕获"] + info.get("tags", []), ensure_ascii=False)
    summary = info.get("summary", to_save[:50])
    ai_space = info.get("space", "default")
    if ai_space not in ["default", "work", "ideas", "archive"]:
        ai_space = "default"

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO items (id, type, title, content, tags, summary, space, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, item_type, final_title, final_content, tags, summary, ai_space, now, now))
        conn.commit()
        vec = await get_embedding(f"{final_title}\n{summary}\n{final_content[:2000]}")
        conn.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)", (item_id, vec.tobytes()))
        conn.commit()
    finally:
        conn.close()

    try:
        source = f"{ctx.get('channel', 'api')}:{ctx.get('sender', 'user')}"
        await gm_remember(f"{final_title}\n{summary}", type="fact", importance=0.5,
                          source=source, tags=info.get("tags", []))
    except Exception as e:
        logger.debug("gitmem0 remember failed: %s", e)

    space_map = {"default": "📦 默认区", "work": "💼 工作区", "ideas": "💡 灵感区", "archive": "🧊 冷藏库"}
    return f"✅ 已保存\n📂 {space_map.get(ai_space, '📦 默认区')}\n🔖 {final_title}\n🏷️ {', '.join(info.get('tags', []))}"


@tool(
    name="fetch_url",
    description="抓取网页内容。支持普通网页和 GitHub 仓库 README。返回 Markdown。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的 URL"}
        },
        "required": ["url"]
    },
    category="content",
)
async def _fetch_url(args, ctx):
    from backend.ai_services import fetch_url_content
    content = await fetch_url_content(args["url"])
    if not content:
        return "网页抓取失败或内容为空"
    return content[:5000]


# ── 4. GitHub ────────────────────────────────────────────────────────────────

@tool(
    name="github_trending",
    description="查询 GitHub 实时热门项目趋势。用户问「GitHub趋势」「热门项目」「最近流行什么」时调用。",
    parameters={
        "type": "object",
        "properties": {
            "language": {"type": "string", "description": "编程语言筛选，如 python/rust/go"},
            "since": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "时间范围，默认 daily"}
        }
    },
    category="github",
)
async def _github_trending(args, ctx):
    from backend.github_stars import fetch_github_trending
    repos = await fetch_github_trending(language=args.get("language", ""), since=args.get("since", "daily"))
    if not repos:
        return "GitHub 趋势查询失败，可能是 API 限流"
    lines = []
    for i, r in enumerate(repos[:15], 1):
        desc = (r.get("description") or "")[:60]
        lines.append(f"{i}. ⭐{r['stars']:,} {r['full_name']} ({r['language'] or 'N/A'})\n   {desc}")
    return "\n".join(lines)


@tool(
    name="github_star_detail",
    description="获取已收藏 GitHub 仓库的详细信息：AI 摘要、stars、语言、README 等。",
    parameters={
        "type": "object",
        "properties": {
            "full_name": {"type": "string", "description": "仓库全名，如 facebook/react"}
        },
        "required": ["full_name"]
    },
    category="github",
)
async def _github_star_detail(args, ctx):
    from backend.database import get_db
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT gr.*, i.title, i.summary, i.tags, i.content
            FROM github_repos gr JOIN items i ON i.id = gr.item_id
            WHERE gr.full_name = ?
        """, (args["full_name"],)).fetchone()
        if not row:
            return f"未收藏该仓库: {args['full_name']}"
        row = dict(row)
        return (f"⭐ {row['full_name']}\n"
                f"Stars: {row.get('stars', 0):,} | Forks: {row.get('forks', 0)} | Language: {row.get('language', '')}\n"
                f"AI摘要: {row.get('ai_summary', '') or row.get('summary', '')}\n"
                f"Topics: {row.get('topics', '[]')}\n"
                f"README: {(row.get('content', '') or '')[:2000]}")
    finally:
        conn.close()


@tool(
    name="github_releases",
    description="获取 GitHub 仓库的 Release 列表。",
    parameters={
        "type": "object",
        "properties": {
            "full_name": {"type": "string", "description": "仓库全名，如 facebook/react"},
            "per_page": {"type": "integer", "description": "返回数量，默认5"}
        },
        "required": ["full_name"]
    },
    category="github",
)
async def _github_releases(args, ctx):
    from backend.config import load_config
    from backend.github_stars import GitHubClient
    cfg = load_config()
    token = cfg.get("GITHUB_TOKEN", "")
    if not token:
        return "未配置 GitHub Token"
    parts = args["full_name"].split("/")
    if len(parts) != 2:
        return "仓库名格式错误，应为 owner/repo"
    gh = GitHubClient(token)
    try:
        releases = await gh.get_releases(parts[0], parts[1], per_page=args.get("per_page", 5))
        if not releases:
            return "无 Release 记录"
        lines = []
        for r in releases:
            body = (r.get("body") or "")[:200]
            lines.append(f"📦 {r.get('tag_name', '')} ({r.get('published_at', '')[:10]})\n"
                         f"   {r.get('name', '')}\n   {body}")
        return "\n\n".join(lines)
    finally:
        await gh.close()


@tool(
    name="github_search_repos",
    description="在 GitHub 上搜索公开仓库。发现新项目时使用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "language": {"type": "string", "description": "编程语言筛选"},
            "sort": {"type": "string", "enum": ["stars", "forks", "updated"], "description": "排序，默认 stars"}
        },
        "required": ["query"]
    },
    category="github",
)
async def _github_search_repos(args, ctx):
    from backend.config import load_config
    from backend.github_stars import GitHubClient
    cfg = load_config()
    token = cfg.get("GITHUB_TOKEN", "")
    if not token:
        return "未配置 GitHub Token"
    gh = GitHubClient(token)
    try:
        q = args["query"]
        if args.get("language"):
            q += f" language:{args['language']}"
        data = await gh.search_repos(q, sort=args.get("sort", "stars"), page=1)
        items = data.get("items", [])[:10]
        if not items:
            return "GitHub 搜索无结果"
        lines = []
        for r in items:
            lines.append(f"⭐{r.get('stargazers_count', 0):,} {r['full_name']} ({r.get('language', 'N/A')})\n"
                         f"   {r.get('description', '') or ''}")
        return "\n".join(lines)
    finally:
        await gh.close()


# ── 5. Memory ────────────────────────────────────────────────────────────────

@tool(
    name="remember",
    description="主动将信息存入 agent 记忆库。记住用户偏好、重要事实等长期信息。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容"},
            "importance": {"type": "number", "description": "重要性 0-1，默认 0.5"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"}
        },
        "required": ["content"]
    },
    category="memory",
)
async def _remember(args, ctx):
    from backend.gitmem0_client import remember
    resp = await remember(
        args["content"], type="fact",
        importance=args.get("importance", 0.5),
        source=f"{ctx.get('channel', 'api')}:{ctx.get('sender', 'user')}",
        tags=args.get("tags", [])
    )
    return "🧠 已记住" if resp.get("ok") else "记忆存储失败"


@tool(
    name="forget",
    description="从 agent 记忆库中删除记忆。用户说「忘掉 XXX」「删除记忆」时调用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要遗忘的内容关键词"}
        },
        "required": ["query"]
    },
    category="memory",
)
async def _forget(args, ctx):
    from backend.gitmem0_client import _send
    resp = await _send("forget", query=args["query"])
    return "已遗忘" if resp.get("ok") else "未找到匹配的记忆"


@tool(
    name="query_memory",
    description="查询并构建记忆上下文。获取用户历史记忆的压缩摘要，适合注入对话。",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "查询消息"},
            "budget": {"type": "integer", "description": "上下文预算字符数，默认600"}
        },
        "required": ["message"]
    },
    category="memory",
)
async def _query_memory(args, ctx):
    from backend.gitmem0_client import query_context
    result = await query_context(args["message"], budget=args.get("budget", 600))
    if result.get("has_memories"):
        return result["context"]
    return "记忆库中无相关记忆"


# ── 6. System ────────────────────────────────────────────────────────────────

@tool(
    name="set_reminder",
    description="设置定时提醒。用户要求日程、闹钟时调用。时间用 ISO 8601 格式。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "提醒内容"},
            "remind_at": {"type": "string", "description": "提醒时间，如 2026-04-26T09:00:00"}
        },
        "required": ["content", "remind_at"]
    },
    category="system",
)
async def _set_reminder(args, ctx):
    from backend.database import get_db
    from datetime import datetime
    import uuid

    r_content = args["content"].strip()
    r_remind_at = args["remind_at"].strip()
    if not r_content or not r_remind_at:
        return "参数不完整"

    try:
        parsed_time = datetime.fromisoformat(r_remind_at)
        if parsed_time < datetime.now():
            return "提醒时间已过期，请指定未来时间"
    except ValueError:
        return f"时间格式无效：'{r_remind_at}'，请使用 ISO 8601 格式"

    reminder_id = uuid.uuid4().hex[:12]
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO reminders (id, to_user_id, context_token, content, remind_at, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (reminder_id, ctx.get("sender", "web"), ctx.get("ctx_token", ""),
              r_content, r_remind_at, datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()

    display_time = parsed_time.strftime("%m月%d日 %H:%M")
    return f"⏰ 提醒已设置！\n📝 {r_content}\n🕐 {display_time}"


@tool(
    name="get_stats",
    description="获取知识库统计：总条目数、文件数、GitHub 仓库数等。",
    parameters={"type": "object", "properties": {}},
    category="system",
)
async def _get_stats(args, ctx):
    from backend.database import get_db
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM items WHERE type='file'").fetchone()[0]
        texts = conn.execute("SELECT COUNT(*) FROM items WHERE type='text'").fetchone()[0]
        codes = conn.execute("SELECT COUNT(*) FROM items WHERE type='code'").fetchone()[0]
        github = conn.execute("SELECT COUNT(*) FROM items WHERE type='github_star'").fetchone()[0]
        return f"📊 知识库统计\n总条目: {total}\n文件: {files} | 文本: {texts} | 代码: {codes} | GitHub: {github}"
    finally:
        conn.close()


# ── 7. AI ────────────────────────────────────────────────────────────────────

@tool(
    name="summarize_content",
    description="对长文本进行 AI 摘要与标签提取。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要摘要的文本"},
            "title": {"type": "string", "description": "可选标题"}
        },
        "required": ["content"]
    },
    category="ai",
)
async def _summarize_content(args, ctx):
    from backend.ai_services import ai_summarize_and_tag
    result = await ai_summarize_and_tag(args.get("title", ""), args["content"], rewrite=False)
    return (f"标题: {result.get('title', '')}\n"
            f"摘要: {result.get('summary', '')}\n"
            f"标签: {', '.join(result.get('tags', []))}\n"
            f"分类: {result.get('space', 'default')}")
