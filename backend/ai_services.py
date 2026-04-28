import json
import logging
import numpy as np
from openai import AsyncOpenAI
from backend.config import load_config, events
from backend.database import get_db
from backend.qmd.models import embed_text as _st_embed, get_embed_dim

import warnings
warnings.filterwarnings("ignore")

import asyncio

logger = logging.getLogger("knowhub")

def _is_likely_code(content: str) -> bool:
    """启发式判断：内容是否为纯代码而非含代码的文档"""
    if not content:
        return False
    lines = content.strip().split('\n')
    # 有 markdown 标题 → 是文档不是代码
    if any(l.strip().startswith('# ') for l in lines[:30]):
        return False
    # 有连续多行自然语言（>30字的非代码行超过5行）→ 是文档
    prose_count = sum(1 for l in lines if len(l.strip()) > 30 and not l.strip().startswith(('```', 'import ', 'from ', 'def ', 'class ', 'function ', 'const ', 'let ', 'var ', '{', '}', '//', '/*', '*', '#')))
    if prose_count >= 5:
        return False
    return True

async def get_embedding(text: str) -> np.ndarray:
    try:
        embedding = await asyncio.to_thread(_st_embed, text[:8000])
        return embedding
    except Exception as e:
        print(f"Embedding error: {e}")
        return np.zeros(get_embed_dim(), dtype=np.float32)

_ai_client = None
_ai_client_cfg = None

async def ai_chat(messages: list, stream: bool = False, tools: list = None):
    global _ai_client, _ai_client_cfg
    cfg = load_config()
    cfg_key = (cfg.get("AI_BASE_URL"), cfg.get("AI_API_KEY"))
    if _ai_client is None or _ai_client_cfg != cfg_key:
        _ai_client = AsyncOpenAI(base_url=cfg["AI_BASE_URL"], api_key=cfg["AI_API_KEY"])
        _ai_client_cfg = cfg_key
    kwargs = dict(model=cfg["AI_MODEL"], messages=messages, stream=stream, temperature=0.7, max_tokens=4096)
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return await _ai_client.chat.completions.create(**kwargs)

async def ai_summarize_and_tag(title: str, content: str, filename: str = "", rewrite: bool = False) -> dict:
    await events.publish(f"🤖 [AI] 正在调用大模型生成摘要与标签...")
    
    if not content.strip() and (title.lower().endswith(('.jpg', '.png', '.jpeg', '.webp', '.gif', '.bmp')) or 'image' in title.lower() or '图片' in title):
        return {
            "title": title or "未命名图片",
            "summary": "来自设备的图像文件，未提取出任何文字内容",
            "tags": ["图像", "无文本"],
            "space": "default",
            "is_code": False,
            "formatted_content": ""
        }
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
  "space": "必须为以下四个英文之一：work(代码/工作/专业), ideas(灵感/随笔/点子), archive(无用极冷门记录), default(日常杂烩/文章)",
  "is_code": "只有当整篇内容是纯粹的源代码（如 .py/.js/.go 源文件）时才为 true。文章、文档、README、教程等即使包含代码片段也必须为 false"
}}
</metadata>

<formatted_content>
这里填入深度重写或排版美化后的 Markdown 正文...
</formatted_content>"""
        try:
            resp = await ai_chat([
                {"role": "system", "content": "你是资深的文档萃取和排版引擎。必须完全按照规定格式（带有 <metadata> 和 <formatted_content> 标签）返回，不能有任何其他寒暄。排版严格使用 GitHub Flavored Markdown：代码块标注语言（```python），标题用 ## / ###，列表用 - 或 1.，表格用 | 分隔，重点用 **加粗**，数学公式用 $ 和 $$。"},
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
            # 启发式纠正：含标题/段落的文档不应被标记为纯代码
            if data.get("is_code") and not _is_likely_code(content_str):
                data["is_code"] = False
            return data
        except Exception as e:
            print(f"AI rewrite error: {e}")
    
    prompt = f"""分析以下内容（注意：内容可能因为超出长度被截断，请完全忽略截断、不完整等情况，直接根据已有的部分回答），返回 JSON：
原文标题/文件名: {title or filename or '(无)'}
内容片段: {content[:3000] if content else '(文件内容无法直接预览)'}

返回格式必须为无 markdown 块的纯合法 JSON，不要有多余的话：
{{
  "title": "根据已有内容自动生成的一个简明标题，不超过20个字",
  "summary": "一句话摘要，概括现有的核心内容",
  "tags": ["标签1", "标签2", "标签3"],
  "space": "必须为以下四个英文之一：work(代码/工作/专业), ideas(灵感/随笔/点子), archive(待删除/无干货的杂音), default(日常文章/杂项)",
  "is_code": "只有当整篇内容是纯粹的源代码（如 .py/.js/.go 源文件）时才为 true。文章、文档、README、教程等即使包含代码片段也必须为 false"
}}"""
    try:
        resp = await ai_chat([
            {"role": "system", "content": "你是信息整理助手。只返回 JSON，不要多余文字。"},
            {"role": "user", "content": prompt}
        ])
        text = resp.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.lower().startswith("json"):
                text = text[4:].strip()
        data = json.loads(text)
        # 启发式纠正：含标题/段落的文档不应被标记为纯代码
        if data.get("is_code") and not _is_likely_code(content):
            data["is_code"] = False
        return data
    except Exception as e:
        print(f"AI summarize error: {e}")
        return {"title": title or filename, "summary": title or filename, "tags": [], "space": "default", "is_code": False}

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape: return 0.0
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0: return 0.0
    return float(np.dot(a, b) / denom)


async def hybrid_search(query: str, top_k: int = 8, rerank: bool = False, space_context_prompt: str = "") -> list:
    """混合检索：BM25 + 向量 + 分块 + RRF 融合 + 可选 LLM 重排

    Delegates core search to QMDStore (GGUF embeddings + smart chunking).
    Keeps LLM-based query expansion and reranking via DeepSeek API.
    """
    from backend.qmd.store import get_store
    store = get_store()

    # Phase 1: 查询扩展（仅重排模式下启用）
    search_query = query
    if rerank:
        try:
            expanded = await expand_query(query, space_context_prompt)
            kw_list = expanded.get("keywords", [])
            sem = expanded.get("semantic", "").strip()
            if sem:
                search_query = sem
            elif kw_list:
                search_query = " ".join(kw_list)
        except Exception as e:
            print(f"Query expansion failed, using original: {e}")

    # Phase 2: QMDStore 混合检索 (BM25 + 向量 + 分块, RRF fusion)
    candidates = await asyncio.to_thread(store.search, search_query, top_k * 2 if rerank else top_k)

    # Phase 2.5: gitmem0 补充检索（agent 记忆作为额外信号源）
    try:
        from backend.gitmem0_client import search as gm_search, is_available as gm_available
        if await gm_available():
            gm_results = await gm_search(search_query, top=top_k)
            if gm_results:
                seen_contents = {r.get("summary", "")[:100] for r in candidates}
                for gm_r in gm_results:
                    gm_content = gm_r.get("content", "")
                    if gm_content[:100] not in seen_contents:
                        candidates.append({
                            "id": f"gm_{gm_r.get('id', '')}",
                            "title": gm_content[:50],
                            "content": gm_content,
                            "summary": gm_content[:200],
                            "tags": "[]",
                            "type": "memory",
                            "source": "gitmem0",
                            "_gm_score": gm_r.get("conf", 0.5)
                        })
                        seen_contents.add(gm_content[:100])
    except Exception as e:
        logger.debug("gitmem0 search failed, silently degrading: %s", e)

    # Phase 3: LLM 重排（可选）
    if rerank and len(candidates) > 1:
        try:
            candidates = await rerank_results(query, candidates, top_k)
        except Exception as e:
            print(f"LLM rerank failed, using RRF order: {e}")
            candidates = candidates[:top_k]

    return candidates[:top_k]

async def fetch_url_content(url: str) -> str:
    """三级降级网页抓取：GitHub API → Jina → crawl4ai"""
    import re as _re
    import httpx

    await events.publish(f"🌐 [Net] 尝试抓取网页内容: {url}")

    # ── Step 1: GitHub 仓库特判（直接调 API，无需爬虫）──
    gh_match = _re.match(r'https?://github\.com/([^/]+)/([^/?#]+)', url)
    if gh_match:
        owner, repo = gh_match.group(1), gh_match.group(2)
        if repo.endswith('.git'): repo = repo[:-4]
        try:
            await events.publish(f"🐙 [GitHub] 检测到仓库链接，通过 API 获取 README...")
            async with httpx.AsyncClient() as hc:
                # Try main branch first, then master
                for branch in ("main", "master"):
                    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
                    res = await hc.get(api_url, headers={
                        "Accept": "application/vnd.github.raw+json",
                        "User-Agent": "KnowHub/2.0"
                    }, timeout=10.0)
                    if res.status_code == 200 and len(res.text) > 50:
                        readme = res.text
                        # Also fetch repo description
                        repo_res = await hc.get(f"https://api.github.com/repos/{owner}/{repo}", headers={"User-Agent": "KnowHub/2.0"}, timeout=10.0)
                        desc = ""
                        if repo_res.status_code == 200:
                            rd = repo_res.json()
                            desc = f"# {rd.get('full_name', owner+'/'+repo)}\n\n> {rd.get('description', '')}\n\nStars: {rd.get('stargazers_count', 0)} | Language: {rd.get('language', 'N/A')}\n\n---\n\n"
                        await events.publish(f"✅ [GitHub] README 获取成功 ({len(readme)} 字符)")
                        return desc + readme
        except Exception as e:
            print(f"GitHub API error: {e}")
            await events.publish(f"⚠️ [GitHub] API 获取失败，降级到通用抓取: {e}")

    # ── Step 2: Jina Reader 快速通道 ──
    try:
        jina_url = f"https://r.jina.ai/{url}"
        async with httpx.AsyncClient(follow_redirects=True) as hc:
            res = await hc.get(jina_url, timeout=15.0, headers={"User-Agent": "KnowHub/2.0"})
            if res.status_code == 200 and len(res.text.strip()) > 100:
                # Jina returns "[404 Not Found]" or empty for failed fetches
                if "[404" not in res.text[:50] and "Not Found" not in res.text[:50]:
                    await events.publish(f"✅ [Jina] 网页抓取成功 ({len(res.text)} 字符)")
                    return res.text
        await events.publish(f"⚠️ [Jina] 内容过短或无效，降级到 crawl4ai...")
    except Exception as e:
        await events.publish(f"⚠️ [Jina] 抓取失败: {e}，降级到 crawl4ai...")

    # ── Step 3: crawl4ai 真浏览器渲染 ──
    try:
        await events.publish(f"🤖 [crawl4ai] 启动真实浏览器渲染: {url}")
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig(
            wait_until="domcontentloaded",
            page_timeout=30000,
            word_count_threshold=10,
        )

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
            # success flag is unreliable — check markdown length instead
            if result.markdown and len(result.markdown.strip()) > 200:
                md = result.markdown
                await events.publish(f"✅ [crawl4ai] 浏览器渲染成功 ({len(md)} 字符)")
                return md
            else:
                await events.publish(f"⚠️ [crawl4ai] 渲染完成但内容为空")
    except Exception as e:
        await events.publish(f"❌ [crawl4ai] 浏览器渲染失败: {e}")
        print(f"crawl4ai error: {e}")

    return ""


async def expand_query(query: str, space_context_prompt: str = "") -> dict:
    """LLM 查询扩展：将用户自然语言转为结构化多路检索意图
    
    借鉴 QMD 的查询扩展策略，将模糊的用户查询拆解为：
    - keywords: 用于 BM25 全文检索的关键词列表
    - semantic: 用于向量语义检索的重述
    - intent: 用户检索意图的一句话描述
    """
    sys_prompt = "你是搜索查询优化引擎。分析用户搜索意图，返回纯 JSON（不要 markdown 代码块）"
    if space_context_prompt:
        sys_prompt += f"。请特别注意当前所处空间的系统设定，以更精准地重组查询。\n[空间设定]：{space_context_prompt}"
    
    prompt = f"""返回格式：
{{
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "semantic": "用自然语言重新描述用户想要搜索的内容，便于向量语义匹配",
  "intent": "一句话概括检索意图"
}}

用户查询: {query}"""
    try:
        resp = await ai_chat([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ])
        text = resp.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
    except Exception as e:
        print(f"Query expansion error: {e}")
        return {"keywords": query.split(), "semantic": query, "intent": query}


async def rerank_results(query: str, candidates: list, top_k: int = 8) -> list:
    """LLM 重排：对融合后的候选结果用大模型精排
    
    借鉴 QMD 的 Reranking 机制，让 LLM 对 RRF 融合后的候选结果
    按与用户查询的相关性进行精确打分和重排序。
    """
    # 构造候选摘要列表
    candidate_summaries = []
    for i, item in enumerate(candidates):
        title = item.get("title", "无标题")
        summary = item.get("summary", "")
        tags = item.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
        candidate_summaries.append(f"[{i}] 标题: {title} | 标签: {tags_str} | 摘要: {summary}")
    
    candidates_text = "\n".join(candidate_summaries)
    
    prompt = f"""你是搜索结果重排引擎。根据用户查询，对以下候选结果按相关性从高到低排序。

用户查询: {query}

候选结果:
{candidates_text}

请返回纯 JSON 数组，包含按相关性排序的序号（最相关的在前）。只返回序号数组，例如: [3, 0, 5, 1]
注意：只包含与查询确实相关的结果，不相关的不要包含。"""
    
    try:
        resp = await ai_chat([
            {"role": "system", "content": "你是搜索结果重排引擎。只返回纯 JSON 数组，无多余文字。"},
            {"role": "user", "content": prompt}
        ])
        text = resp.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.lower().startswith("json"):
                text = text[4:].strip()
        ranked_indices = json.loads(text)
        
        # 按 LLM 排序组装结果
        reranked = []
        seen = set()
        for idx in ranked_indices:
            if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen:
                reranked.append(candidates[idx])
                seen.add(idx)
        
        # 补充 LLM 遗漏的候选（保底）
        if len(reranked) < top_k:
            for i, c in enumerate(candidates):
                if i not in seen:
                    reranked.append(c)
                    if len(reranked) >= top_k:
                        break
        
        return reranked[:top_k]
    except Exception as e:
        print(f"LLM rerank error: {e}")
        return candidates[:top_k]
