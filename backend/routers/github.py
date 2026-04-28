import json
import logging
import asyncio
from datetime import datetime

import httpx
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import StreamingResponse

from backend.config import (
    load_config, save_config, events,
    get_primary_github_token, get_all_github_tokens,
    get_github_accounts, save_github_accounts,
)
from backend.database import get_db

logger = logging.getLogger("knowhub")

router = APIRouter(prefix="/api/github")


# ── Config ────────────────────────────────────────────────────────────────

@router.get("/config")
async def github_config_get():
    cfg = load_config()
    accounts = get_github_accounts()

    enriched = []
    for acc in accounts:
        token = acc.get("token", "")
        info = {**acc}
        if token and (not acc.get("login") or not acc.get("avatar_url")):
            try:
                from backend.github_stars import GitHubClient
                gh = GitHubClient(token)
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get("https://api.github.com/user", headers=gh.headers)
                    if r.status_code == 200:
                        u = r.json()
                        info["login"] = u.get("login", "")
                        info["avatar_url"] = u.get("avatar_url", "")
                        info["name"] = u.get("name", "")
                await gh.close()
            except Exception as e:
                logger.debug("GitHub user info fetch failed: %s", e)
        info["token_preview"] = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else ""
        enriched.append(info)

    if any(e.get("login") and not acc.get("login") for e, acc in zip(enriched, accounts)):
        save_github_accounts([{k: v for k, v in e.items() if k != "token_preview"} for e in enriched])

    return {
        "has_token": any(a.get("enabled", True) for a in accounts),
        "accounts": [{k: v for k, v in a.items() if k != "token"} for a in enriched],
        "sync_enabled": cfg.get("GITHUB_SYNC_ENABLED", False),
        "auto_analyze": cfg.get("GITHUB_AUTO_ANALYZE", True),
        "asset_platforms": cfg.get("GITHUB_ASSET_PLATFORMS", []),
    }


@router.post("/config")
async def github_config_set(request: Request):
    body = await request.json()
    action = body.get("action", "add")
    cfg = load_config()

    if action == "add":
        token = body.get("token", "").strip()
        if not token:
            return {"ok": False, "msg": "Token 不能为空"}
        user_info = {}
        try:
            from backend.github_stars import GitHubClient
            gh = GitHubClient(token)
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get("https://api.github.com/user", headers=gh.headers)
                if r.status_code == 200:
                    u = r.json()
                    user_info = {"login": u.get("login", ""), "avatar_url": u.get("avatar_url", ""), "name": u.get("name", "")}
                else:
                    return {"ok": False, "msg": f"Token 无效 (HTTP {r.status_code})"}
            await gh.close()
        except Exception as e:
            return {"ok": False, "msg": f"验证失败: {e}"}
        accounts = get_github_accounts()
        if any(a.get("login") == user_info.get("login") for a in accounts if a.get("login")):
            return {"ok": False, "msg": f"账号 {user_info.get('login')} 已存在"}
        accounts.append({
            "token": token,
            "label": body.get("label", user_info.get("login", "")),
            "login": user_info.get("login", ""),
            "avatar_url": user_info.get("avatar_url", ""),
            "name": user_info.get("name", ""),
            "enabled": True,
        })
        save_github_accounts(accounts)

    elif action == "remove":
        login = body.get("login", "")
        idx = body.get("index", -1)
        accounts = get_github_accounts()
        if login:
            accounts = [a for a in accounts if a.get("login") != login]
        elif 0 <= idx < len(accounts):
            accounts.pop(idx)
        save_github_accounts(accounts)

    elif action == "toggle":
        login = body.get("login", "")
        enabled = body.get("enabled", True)
        accounts = get_github_accounts()
        for a in accounts:
            if a.get("login") == login:
                a["enabled"] = enabled
        save_github_accounts(accounts)

    elif action == "update_settings":
        cfg["GITHUB_SYNC_ENABLED"] = body.get("sync_enabled", cfg.get("GITHUB_SYNC_ENABLED", False))
        cfg["GITHUB_AUTO_ANALYZE"] = body.get("auto_analyze", cfg.get("GITHUB_AUTO_ANALYZE", True))
        save_config(cfg)

    if get_all_github_tokens() and cfg.get("GITHUB_SYNC_ENABLED", False):
        try:
            from backend.github_stars import start_github_worker
            asyncio.create_task(start_github_worker())
        except Exception as e:
            logger.warning("Failed to start GitHub worker: %s", e)

    return {"ok": True}


# ── Sync ──────────────────────────────────────────────────────────────────

@router.post("/sync")
async def github_sync(mode: str = Form("incremental")):
    tokens = get_all_github_tokens()
    if not tokens:
        return {"ok": False, "msg": "未配置 GitHub Token"}
    from backend.github_stars import GitHubClient, SyncEngine
    total_result = {"added": 0, "updated": 0, "unchanged": 0}
    for token in tokens:
        try:
            gh = GitHubClient(token)
            engine = SyncEngine(gh)
            if mode == "full":
                result = await engine.full_sync()
            else:
                result = await engine.incremental_sync()
            await gh.close()
            for k in total_result:
                total_result[k] += result.get(k, 0)
        except Exception as e:
            logger.error("GitHub sync error: %s", e)
    return {"ok": True, **total_result}


# ── Stars CRUD ────────────────────────────────────────────────────────────

@router.get("/stars")
async def github_stars(
    page: int = 1, page_size: int = 30,
    language: str = "", category: str = "",
    search: str = "", sort: str = "stars",
):
    conn = get_db()
    try:
        where_parts = ["1=1"]
        params = []
        if language:
            where_parts.append("gr.language = ?")
            params.append(language)
        if category:
            where_parts.append("gr.category_id = ?")
            params.append(category)
        if search:
            tag_pat = json.dumps(search, ensure_ascii=True).strip('"')
            ids_from_fts = [r[0] for r in conn.execute(
                "SELECT id FROM items_fts WHERE items_fts MATCH ?", (search,)
            ).fetchall()]
            ids_from_tags = [r[0] for r in conn.execute(
                "SELECT item_id FROM github_repos WHERE ai_tags LIKE ?", (f"%{tag_pat}%",)
            ).fetchall()]
            ids_from_desc = [r[0] for r in conn.execute(
                "SELECT i.id FROM items i JOIN github_repos gr ON gr.item_id=i.id WHERE gr.description LIKE ? OR gr.ai_summary LIKE ?",
                (f"%{search}%", f"%{search}%")
            ).fetchall()]
            match_ids = list(set(ids_from_fts) | set(ids_from_tags) | set(ids_from_desc))
            if match_ids:
                placeholders = ",".join(["?"] * len(match_ids))
                where_parts.append(f"i.id IN ({placeholders})")
                params.extend(match_ids)
            else:
                where_parts.append("1=0")

        where = " AND ".join(where_parts)
        order = {"stars": "gr.stars DESC", "recently": "i.created_at DESC", "name": "gr.full_name ASC"}.get(sort, "gr.stars DESC")

        total = conn.execute(f"""
            SELECT COUNT(*) FROM github_repos gr JOIN items i ON i.id = gr.item_id WHERE {where}
        """, params).fetchone()[0]

        rows = conn.execute(f"""
            SELECT gr.*, i.title, i.summary, i.tags, i.created_at, i.content,
                   gc.name as category_name, gc.color as category_color, gc.icon as category_icon
            FROM github_repos gr
            JOIN items i ON i.id = gr.item_id
            LEFT JOIN github_categories gc ON gc.id = gr.category_id
            WHERE {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """, params + [page_size, (page - 1) * page_size]).fetchall()

        languages = conn.execute("""
            SELECT DISTINCT language FROM github_repos WHERE language != '' ORDER BY language
        """).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "languages": [r["language"] for r in languages],
        }
    finally:
        conn.close()


@router.get("/stars/{item_id}")
async def github_star_detail(item_id: str):
    conn = get_db()
    try:
        repo = conn.execute("""
            SELECT gr.*, i.title, i.summary, i.tags, i.content, i.created_at,
                   gc.name as category_name, gc.color as category_color
            FROM github_repos gr
            JOIN items i ON i.id = gr.item_id
            LEFT JOIN github_categories gc ON gc.id = gr.category_id
            WHERE gr.item_id = ?
        """, (item_id,)).fetchone()
        if not repo:
            return {"ok": False, "msg": "仓库不存在"}

        releases = conn.execute("""
            SELECT * FROM github_releases WHERE item_id = ?
            ORDER BY published_at DESC LIMIT 10
        """, (item_id,)).fetchall()

        subscribed = conn.execute("""
            SELECT id FROM github_subscriptions WHERE item_id = ? AND enabled = 1
        """, (item_id,)).fetchone() is not None

        return {
            "ok": True,
            "repo": dict(repo),
            "releases": [dict(r) for r in releases],
            "subscribed": subscribed,
        }
    finally:
        conn.close()


@router.get("/stars/{item_id}/readme")
async def github_star_readme(item_id: str):
    conn = get_db()
    try:
        repo = conn.execute("SELECT full_name FROM github_repos WHERE item_id = ?", (item_id,)).fetchone()
        if not repo:
            return {"ok": False, "msg": "仓库不存在"}
    finally:
        conn.close()
    token = get_primary_github_token()
    if not token:
        return {"ok": False, "readme": "", "msg": "未配置 Token"}
    from backend.github_stars import GitHubClient
    gh = GitHubClient(token)
    try:
        parts = repo["full_name"].split("/")
        readme = await gh.get_readme_html(parts[0], parts[1])
        return {"ok": True, "readme": readme}
    except Exception as e:
        return {"ok": False, "readme": "", "msg": str(e)}
    finally:
        await gh.close()


@router.delete("/stars/{item_id}")
async def github_star_delete(item_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/star")
async def github_star_repo(request: Request):
    body = await request.json()
    full_name = body.get("full_name", "")
    action = body.get("action", "star")
    account_index = body.get("account_index", 0)
    if "/" not in full_name:
        return {"error": "invalid full_name"}, 400
    tokens = get_all_github_tokens()
    if not tokens:
        return {"error": "no token"}, 400
    from backend.github_stars import GitHubClient
    owner, repo = full_name.split("/", 1)
    if action == "unstar":
        for tk in tokens:
            gh = GitHubClient(tk)
            try:
                await gh.unstar_repo(owner, repo)
            except Exception as e:
                logger.debug("Unstar failed for token: %s", e)
            finally:
                await gh.close()
        conn = get_db()
        try:
            row = conn.execute("SELECT item_id FROM github_repos WHERE full_name = ?", (full_name,)).fetchone()
            if row:
                item_id = row["item_id"]
                conn.execute("DELETE FROM github_repos WHERE item_id = ?", (item_id,))
                conn.execute("DELETE FROM embeddings WHERE item_id = ?", (item_id,))
                conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
                conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}
    else:
        idx = min(account_index, len(tokens) - 1)
        gh = GitHubClient(tokens[idx])
        try:
            ok = await gh.star_repo(owner, repo)
            return {"ok": ok}
        finally:
            await gh.close()


@router.post("/stars/{item_id}/reanalyze")
async def github_star_reanalyze(item_id: str):
    conn = get_db()
    try:
        repo = conn.execute("""
            SELECT gr.*, i.content FROM github_repos gr JOIN items i ON i.id = gr.item_id
            WHERE gr.item_id = ?
        """, (item_id,)).fetchone()
    finally:
        conn.close()
    if not repo:
        return {"ok": False, "msg": "仓库不存在"}

    from backend.github_stars import AIAnalyzer
    analyzer = AIAnalyzer()
    result = await analyzer.analyze_repo(dict(repo), repo["content"] or "")

    conn = get_db()
    try:
        conn.execute("UPDATE github_repos SET ai_summary=?, ai_tags=?, ai_platforms=? WHERE item_id=?",
                     (result["summary"], json.dumps(result["tags"]), json.dumps(result["platforms"]), item_id))
        conn.execute("UPDATE items SET summary=?, tags=? WHERE id=?",
                     (result["summary"], json.dumps(result["tags"]), item_id))
        conn.commit()
    finally:
        conn.close()

    if result.get("summary"):
        try:
            from backend.gitmem0_client import remember as gm_remember
            await gm_remember(
                f"GitHub: {repo['full_name']} - {result['summary']}",
                type="fact", importance=0.6, source="knowhub:github",
                tags=result.get("tags", [])
            )
        except Exception as e:
            logger.debug("gitmem0 remember failed: %s", e)
    return {"ok": True, **result}


@router.post("/stars/reanalyze-all")
async def github_star_reanalyze_all():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT gr.item_id, gr.full_name, gr.description, gr.language, gr.topics,
                   i.content as readme
            FROM github_repos gr JOIN items i ON i.id = gr.item_id
            ORDER BY gr.stars DESC LIMIT 30
        """).fetchall()
    finally:
        conn.close()

    from backend.github_stars import AIAnalyzer
    analyzer = AIAnalyzer()
    done = 0
    for row in rows:
        try:
            repo_data = {
                "full_name": row["full_name"],
                "description": row["description"],
                "language": row["language"],
                "topics": json.loads(row["topics"] or "[]"),
            }
            result = await analyzer.analyze_repo(repo_data, row["readme"] or "")
            if result.get("summary"):
                conn = get_db()
                try:
                    cat_id = None
                    ai_cat = result.get("category", "")
                    if ai_cat:
                        cat_row = conn.execute("SELECT id FROM github_categories WHERE name=?", (ai_cat,)).fetchone()
                        if cat_row:
                            cat_id = cat_row["id"]
                    conn.execute("UPDATE github_repos SET ai_summary=?, ai_tags=?, ai_platforms=?, category_id=COALESCE(?, category_id) WHERE item_id=?",
                                 (result["summary"], json.dumps(result["tags"]), json.dumps(result["platforms"]), cat_id, row["item_id"]))
                    conn.execute("UPDATE items SET summary=?, tags=? WHERE id=?",
                                 (result["summary"], json.dumps(result["tags"]), row["item_id"]))
                    conn.commit()
                finally:
                    conn.close()
                done += 1
        except Exception as e:
            logger.error("Re-analyze error for %s: %s", row['full_name'], e)
    return {"ok": True, "analyzed": done, "total": len(rows)}


@router.post("/memory-backfill")
async def github_memory_backfill():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT gr.full_name, gr.ai_summary, gr.ai_tags
            FROM github_repos gr
            WHERE gr.ai_summary IS NOT NULL AND gr.ai_summary != ''
        """).fetchall()
    finally:
        conn.close()

    from backend.gitmem0_client import remember as gm_remember
    stored = 0
    for row in rows:
        try:
            tags = json.loads(row["ai_tags"] or "[]")
            resp = await gm_remember(
                f"GitHub: {row['full_name']} - {row['ai_summary']}",
                type="fact", importance=0.6, source="knowhub:github",
                tags=tags
            )
            if resp.get("ok"):
                stored += 1
        except Exception as e:
            logger.debug("Memory backfill failed for %s: %s", row['full_name'], e)
    return {"ok": True, "total": len(rows), "stored": stored}


# ── Categories ────────────────────────────────────────────────────────────

@router.get("/categories")
async def github_categories():
    from backend.github_stars import CategoryManager
    mgr = CategoryManager()
    return {"items": mgr.list_all()}


@router.post("/categories")
async def github_categories_create(
    name: str = Form(...), keywords: str = Form("[]"),
    color: str = Form("#8b5cf6"), icon: str = Form("📁"),
):
    from backend.github_stars import CategoryManager
    mgr = CategoryManager()
    try:
        kws = json.loads(keywords)
    except (json.JSONDecodeError, TypeError):
        kws = []
    cat_id = mgr.create(name, kws, color, icon)
    return {"ok": True, "id": cat_id}


@router.put("/categories/{cat_id}")
async def github_categories_update(cat_id: str, name: str = Form(""), keywords: str = Form(""), color: str = Form(""), icon: str = Form("")):
    from backend.github_stars import CategoryManager
    mgr = CategoryManager()
    kwargs = {}
    if name:
        kwargs["name"] = name
    if keywords:
        try:
            kwargs["keywords"] = json.loads(keywords)
        except (json.JSONDecodeError, TypeError):
            pass
    if color:
        kwargs["color"] = color
    if icon:
        kwargs["icon"] = icon
    mgr.update(cat_id, **kwargs)
    return {"ok": True}


@router.delete("/categories/{cat_id}")
async def github_categories_delete(cat_id: str):
    from backend.github_stars import CategoryManager
    mgr = CategoryManager()
    mgr.delete(cat_id)
    return {"ok": True}


@router.get("/tags")
async def github_tags(limit: int = 50):
    conn = get_db()
    try:
        rows = conn.execute("SELECT ai_tags FROM github_repos WHERE ai_tags IS NOT NULL AND ai_tags != '' AND ai_tags != '[]'").fetchall()
    finally:
        conn.close()
    tag_count: dict[str, int] = {}
    for row in rows:
        try:
            tags = json.loads(row["ai_tags"])
            if isinstance(tags, list):
                for t in tags:
                    tag_count[t] = tag_count.get(t, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    items = sorted(tag_count.items(), key=lambda x: -x[1])[:limit]
    return {"items": [{"name": t, "count": c} for t, c in items]}


@router.post("/categories/auto-assign")
async def github_categories_auto():
    from backend.github_stars import CategoryManager
    mgr = CategoryManager()
    count = await mgr.auto_assign()
    return {"ok": True, "assigned": count}


# ── Subscriptions & Releases ──────────────────────────────────────────────

@router.get("/subscriptions")
async def github_subscriptions():
    from backend.github_stars import ReleaseTracker
    tracker = ReleaseTracker(None)
    return {"items": tracker.list_subscriptions()}


@router.post("/subscriptions")
async def github_subscriptions_add(item_id: str = Form(...), full_name: str = Form(...)):
    token = get_primary_github_token()
    from backend.github_stars import GitHubClient, ReleaseTracker
    gh = GitHubClient(token) if token else None
    tracker = ReleaseTracker(gh)
    sub_id = tracker.subscribe(item_id, full_name)
    if gh:
        try:
            parts = full_name.split("/")
            if len(parts) == 2:
                await tracker.fetch_releases(item_id, parts[0], parts[1])
        except Exception as e:
            logger.error("Release fetch on subscribe error: %s", e)
        await gh.close()
    return {"ok": True, "id": sub_id}


@router.delete("/subscriptions/{item_id}")
async def github_subscriptions_delete(item_id: str):
    from backend.github_stars import ReleaseTracker
    tracker = ReleaseTracker(None)
    tracker.unsubscribe(item_id)
    return {"ok": True}


@router.post("/subscriptions/fetch")
async def github_subscriptions_fetch():
    token = get_primary_github_token()
    if not token:
        return {"ok": False, "msg": "未配置 Token"}
    from backend.github_stars import GitHubClient, ReleaseTracker
    gh = GitHubClient(token)
    tracker = ReleaseTracker(gh)
    count = await tracker.fetch_all_subscriptions()
    await gh.close()
    return {"ok": True, "new_releases": count}


@router.get("/releases/fetch")
async def github_releases_fetch(full_name: str = "", per_page: int = 10):
    if not full_name or "/" not in full_name:
        return {"items": []}
    token = get_primary_github_token()
    if not token:
        return {"items": []}
    from backend.github_stars import GitHubClient
    gh = GitHubClient(token)
    try:
        parts = full_name.split("/")
        releases = await gh.get_releases(parts[0], parts[1], per_page=per_page)
        items = [{
            "id": r.get("id", ""),
            "tag_name": r.get("tag_name", ""),
            "name": r.get("name", ""),
            "body": r.get("body", ""),
            "html_url": r.get("html_url", ""),
            "published_at": r.get("published_at", ""),
            "is_prerelease": r.get("prerelease", False),
            "assets": [
                {"name": a.get("name", ""), "size": a.get("size", 0),
                 "url": a.get("browser_download_url", ""), "content_type": a.get("content_type", "")}
                for a in r.get("assets", [])
            ],
        } for r in releases]
        return {"items": items}
    except Exception as e:
        logger.error("Release fetch error for %s: %s", full_name, e)
        return {"items": []}
    finally:
        await gh.close()


@router.get("/releases")
async def github_releases(unread_only: bool = False, limit: int = 50):
    from backend.github_stars import ReleaseTracker
    tracker = ReleaseTracker(None)
    return {"items": tracker.get_timeline(unread_only, limit)}


@router.post("/releases/{release_id}/read")
async def github_release_read(release_id: str):
    from backend.github_stars import ReleaseTracker
    tracker = ReleaseTracker(None)
    tracker.mark_read(release_id)
    return {"ok": True}


@router.get("/releases/unread-count")
async def github_release_unread():
    from backend.github_stars import ReleaseTracker
    tracker = ReleaseTracker(None)
    return {"count": tracker.unread_count()}


# ── Discover ──────────────────────────────────────────────────────────────

@router.get("/discover/trending")
async def github_discover_trending(since: str = "daily", language: str = ""):
    token = get_primary_github_token()
    from backend.github_stars import GitHubClient, DiscoveryEngine
    gh = GitHubClient(token) if token else None
    engine = DiscoveryEngine(gh)
    items = await engine.trending(since, language)
    if gh:
        await gh.close()
    return {"items": items}


@router.get("/discover/hot")
async def github_discover_hot(language: str = "", platform: str = "", page: int = 1):
    token = get_primary_github_token()
    if not token:
        return {"items": []}
    from backend.github_stars import GitHubClient, DiscoveryEngine
    gh = GitHubClient(token)
    engine = DiscoveryEngine(gh)
    items = await engine.hot_releases(language, platform, page)
    await gh.close()
    return {"items": items}


@router.get("/discover/popular")
async def github_discover_popular(language: str = "", platform: str = "", page: int = 1):
    token = get_primary_github_token()
    if not token:
        return {"items": []}
    from backend.github_stars import GitHubClient, DiscoveryEngine
    gh = GitHubClient(token)
    engine = DiscoveryEngine(gh)
    items = await engine.most_popular(language, platform, page)
    await gh.close()
    return {"items": items}


@router.get("/discover/topic/{topic}")
async def github_discover_topic(topic: str, page: int = 1):
    token = get_primary_github_token()
    from backend.github_stars import GitHubClient, DiscoveryEngine
    gh = GitHubClient(token) if token else None
    engine = DiscoveryEngine(gh)
    items = await engine.by_topic(topic, page)
    if gh:
        await gh.close()
    return {"items": items}


@router.get("/discover/readme")
async def github_discover_readme(full_name: str = "", path: str = ""):
    if not full_name:
        return {"ok": False, "readme": ""}
    token = get_primary_github_token()
    if not token:
        return {"ok": False, "readme": ""}
    from backend.github_stars import GitHubClient
    gh = GitHubClient(token)
    try:
        parts = full_name.split("/")
        if len(parts) != 2:
            return {"ok": False, "readme": ""}
        if path:
            readme = await gh.get_file_html(parts[0], parts[1], path)
        else:
            readme = await gh.get_readme_html(parts[0], parts[1])
        return {"ok": True, "readme": readme}
    except Exception:
        return {"ok": False, "readme": ""}
    finally:
        await gh.close()


@router.get("/discover/search")
async def github_discover_search(q: str = "", page: int = 1):
    token = get_primary_github_token()
    from backend.github_stars import GitHubClient, DiscoveryEngine
    gh = GitHubClient(token) if token else None
    engine = DiscoveryEngine(gh)
    items = await engine.search(q, page)
    if gh:
        await gh.close()
    return {"items": items}


@router.post("/discover/ask")
async def github_discover_ask(request: Request):
    from backend.ai_services import ai_chat

    body = await request.json()
    question = body.get("question", "")
    context = body.get("context", "")
    mode = body.get("mode", "repo")

    if not question:
        raise HTTPException(400, "Question required")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if mode == "repo":
        system = f"""你是一位资深的 GitHub 技术顾问。当前时间 {current_time}。
用户正在浏览一个 GitHub 仓库，以下是该仓库的信息：

{context}

请用中文回答用户关于这个仓库的问题。回答要专业、深入、有洞察力。
如果用户问"这个项目怎么样"，请从技术架构、社区活跃度、适用场景等维度分析。
如果问"怎么用"，请结合 README 给出入门指引。"""
    elif mode == "release":
        system = f"""你是一位资深的版本发布分析师。当前时间 {current_time}。
以下是某个 GitHub 仓库的 Release 信息：

{context}

请用中文分析这些 Release。回答要有深度：
- 总结每个版本的核心变更和新功能
- 指出 Breaking Changes 和升级注意事项
- 评估版本迭代节奏和项目活跃度
- 如果用户有具体问题，请针对性回答。"""
    else:
        system = f"""你是一位资深的 GitHub 技术趋势分析师。当前时间 {current_time}。
以下是当前 GitHub 上的热门项目/趋势数据：

{context}

请用中文分析这些趋势。回答要有深度和洞察力：
- 指出当前技术热点方向
- 分析为什么这些项目受欢迎
- 推荐值得关注的项目及理由
- 如果用户有具体问题，请针对性回答。"""

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]

    async def generate():
        try:
            resp = await ai_chat(messages, stream=True)
            async for chunk in resp:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield f"data: {json.dumps({'content': delta.content})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'content': f'AI 响应错误: {str(e)}'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
