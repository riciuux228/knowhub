import json
import logging
import asyncio
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from backend.config import load_config, get_primary_github_token
from backend.database import get_db
from backend.ai_services import hybrid_search

logger = logging.getLogger("knowhub")

router = APIRouter()


@router.post("/api/ask")
async def ask_question(request: Request):
    body = await request.json()
    question = body.get("question", "")
    history = body.get("history", [])
    space = body.get("space", "all")

    if not question:
        raise HTTPException(400, "Question required")

    conn = get_db()
    try:
        context_prompt = ""
        if space != "all":
            space_row = conn.execute("SELECT context_prompt FROM spaces WHERE id=?", (space,)).fetchone()
            if space_row:
                context_prompt = space_row["context_prompt"]
    finally:
        conn.close()

    async def _search():
        return await hybrid_search(question, 8, rerank=False, space_context_prompt=context_prompt)

    async def _gm_context():
        try:
            from backend.gitmem0_client import query_context as gm_query
            gm_ctx = await gm_query(question, budget=600)
            if gm_ctx.get("has_memories"):
                return f"\n\n[用户历史记忆]\n{gm_ctx['context']}"
        except Exception as e:
            logger.debug("gitmem0 query failed: %s", e)
        return ""

    async def _gh_stars_boost():
        q_lower = question.lower()
        gh_keywords = ["github", "星标", "star", "仓库", "repo", "标星"]
        if not any(kw in q_lower for kw in gh_keywords):
            return []
        try:
            conn_gh = get_db()
            try:
                rows = conn_gh.execute("""
                    SELECT gr.item_id, gr.full_name, gr.stars, gr.forks, gr.language,
                           gr.topics, gr.ai_summary, gr.ai_platforms,
                           i.title, i.content, i.summary, i.tags, i.type, i.created_at, i.space
                    FROM github_repos gr
                    JOIN items i ON i.id = gr.item_id
                    ORDER BY gr.stars DESC
                """).fetchall()
            finally:
                conn_gh.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug("GitHub Stars boost failed: %s", e)
            return []

    async def _gh_trending():
        q_lower = question.lower()
        trend_keywords = ["趋势", "trending", "热门", "流行", "hot", "today", "今日", "本周"]
        if not any(kw in q_lower for kw in trend_keywords):
            return []
        try:
            from backend.github_stars import fetch_github_trending
            lang = ""
            for l in ["python", "javascript", "typescript", "rust", "go", "java", "c++", "swift"]:
                if l in q_lower:
                    lang = l
                    break
            since = "daily"
            if any(kw in q_lower for kw in ["周", "week"]):
                since = "weekly"
            elif any(kw in q_lower for kw in ["月", "month"]):
                since = "monthly"
            return await fetch_github_trending(language=lang, since=since)
        except Exception as e:
            logger.debug("GitHub trending fetch failed: %s", e)
            return []

    search_task = asyncio.create_task(_search())
    gm_task = asyncio.create_task(_gm_context())
    gh_task = asyncio.create_task(_gh_stars_boost())
    trend_task = asyncio.create_task(_gh_trending())
    results = await search_task
    gm_context = await gm_task
    gh_extra = await gh_task
    gh_trending = await trend_task

    if gh_extra:
        existing_ids = {r.get("id") or r.get("item_id") for r in results}
        for gh_item in gh_extra:
            iid = gh_item.get("item_id") or gh_item.get("id")
            if iid and iid not in existing_ids:
                gh_item["type"] = "github_star"
                results.append(gh_item)
                existing_ids.add(iid)

    if space != "all":
        results = [r for r in results if r.get("space", "default") == space]

    context_parts = []
    gh_ids = [item.get("id") or item.get("item_id") for item in results if item.get("type") == "github_star"]
    gh_meta = {}
    if gh_ids:
        try:
            conn_gh = get_db()
            try:
                placeholders = ",".join("?" * len(gh_ids))
                gh_rows = conn_gh.execute(f"""
                    SELECT gr.item_id, gr.full_name, gr.stars, gr.forks, gr.language,
                           gr.topics, gr.ai_summary, gr.ai_platforms
                    FROM github_repos gr WHERE gr.item_id IN ({placeholders})
                """, gh_ids).fetchall()
            finally:
                conn_gh.close()
            gh_meta = {r["item_id"]: dict(r) for r in gh_rows}
        except Exception as e:
            logger.debug("GitHub metadata batch query failed: %s", e)

    for i, item in enumerate(results):
        tags = item.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        time_str = item.get("created_at", "")[:19].replace("T", " ")
        item_id = item.get("id") or item.get("item_id", "")

        if item_id in gh_meta:
            gm = gh_meta[item_id]
            stars_str = f"{gm['stars']:,}" if gm.get("stars") else "0"
            topics = json.loads(gm.get("topics") or "[]")
            platforms = json.loads(gm.get("ai_platforms") or "[]")
            gh_line = f"⭐{stars_str} | 🍴{gm.get('forks',0)} | 📝{gm.get('language','')}"
            if topics:
                gh_line += f" | 🏷 {','.join(topics[:5])}"
            if platforms:
                gh_line += f" | 🖥 {','.join(platforms)}"
            ctx = f"[{i+1}] 类型:GitHub仓库 | {gm['full_name']} | {gh_line}"
            if gm.get("ai_summary"):
                ctx += f"\nAI摘要: {gm['ai_summary']}"
            if item.get("content"):
                ctx += f"\nREADME: {item['content'][:2000]}"
        else:
            ctx = f"[{i+1}] 时间:{time_str} | 类型:{item['type']} | 标题:{item.get('title','')} | 标签:{','.join(tags)} | 摘要:{item.get('summary','')}"
            if item.get("content") and item["type"] != "file":
                ctx += f"\n详细内容: {item['content'][:3000]}"
        context_parts.append(ctx)

    context = "\n\n".join(context_parts) if context_parts else "（检索库中没有找到强相关内容）"

    if gh_trending:
        trend_lines = []
        for i, r in enumerate(gh_trending[:15], 1):
            desc = (r.get("description") or "")[:80]
            topics = ", ".join(r.get("topics", [])[:3])
            trend_lines.append(f"  {i}. ⭐{r['stars']:,} | {r['full_name']} ({r['language'] or 'N/A'}){f' [{topics}]' if topics else ''}\n     {desc}")
        context += f"\n\n======== [GitHub 实时趋势] ========\n" + "\n".join(trend_lines) + "\n================================="

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    system_prompt = f"""你是一个运行在用户私有局域网深处的【智能知识助理引擎】。当前时间是 {current_time}。
系统已在底层跑通了双路混合检索（语义+全文 RRF重排），为你提取出最精准的前后文背景。
知识库中包含 GitHub 星标仓库（标记为 GitHub仓库 类型），带有 ⭐ stars、🍴 forks、📝 language、🏷 topics 等结构化数据和 AI 摘要。

======== [系统检索命中的高价值关联卡片] ========
{context}
=================================================
{gm_context}

你拥有以下工具可以调用。当需要更多信息时，主动调用工具获取：
1. search_knowledge — 搜索知识库
2. search_memory — 搜索记忆库
3. search_github_stars — 搜索 GitHub 仓库
4. github_trending — 查 GitHub 趋势
5. github_star_detail — 查仓库详情
6. github_releases — 查 Release 列表
7. fetch_url — 抓取网页内容

回答守则：
1. 深入分析卡片中的 [详细内容] 提炼最佳答案，严禁只看标题。
2. 必须且只能基于检索到的事实进行归纳推理。如果卡片中的信息不够回答，调用工具补充查询。
3. 标注来源序号（如 [1] 或 [2]），增加可信度。
4. 如果所有工具都查不到，诚实告知"知识库缺少这部分数据"。

排版规范（严格遵守 GitHub Flavored Markdown）：
- 用 # / ## / ### 做标题层次，标题前后留空行
- 代码块用三个反引号包裹并标注语言（如 ```python）
- 行内代码用单反引号（如 `variable`）
- 列表用 - 或 1. 2. 3.，每项之间空一行
- 引用块用 > 开头
- 表格用 | 分隔，表头与分隔行必须有 |---|
- 重点内容用 **加粗**，链接用 [文字](url)
- 数学公式用 $行内$ 或 $$块级$$（LaTeX语法）
- 回答较长时分段，每段用 ## 小标题引导"""

    from backend.tools import get_openai_tools, react_loop

    tools = get_openai_tools(channels=["web"])
    tool_ctx = {"channel": "web"}

    async def generate():
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            if msg.get('role') in ['user', 'assistant'] and msg.get('content'):
                messages.append({"role": msg['role'], "content": msg['content']})
        messages.append({"role": "user", "content": question})

        try:
            async for sse_chunk in react_loop(messages, tools, tool_ctx, max_rounds=10):
                yield sse_chunk
            sources = [{"title": r.get("title", ""), "type": r["type"],
                         "tags": json.loads(r.get("tags", "[]")) if isinstance(r.get("tags"), str) else r.get("tags", []),
                         "summary": r.get("summary", ""), "id": r["id"]}
                        for r in results[:5]]
            yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        except Exception as e:
            err_msg = f"\\n\\n❌ **大模型连接失败**: `{str(e)}`\\n\\n请点击侧边栏【系统设置】检查配置。"
            yield f"data: {json.dumps({'content': err_msg}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
