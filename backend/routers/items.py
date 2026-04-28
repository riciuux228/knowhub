import os
import json
import uuid
import re
import hashlib
import logging
import asyncio
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse

from backend.config import load_config, events, get_primary_github_token
from backend.database import get_db
from backend.ai_services import ai_summarize_and_tag, get_embedding, hybrid_search, fetch_url_content, cosine_similarity
from backend.file_services import process_binary_file

logger = logging.getLogger("knowhub")

router = APIRouter()

MAX_UPLOAD_SIZE = 200 * 1024 * 1024  # 200MB
MAX_TEXT_LENGTH = 500_000  # 500K characters


def _clean_item(item: dict) -> dict:
    item.pop("file_path", None)
    item.pop("vector", None)
    if isinstance(item.get("tags"), str):
        try:
            item["tags"] = json.loads(item["tags"])
        except (json.JSONDecodeError, TypeError):
            item["tags"] = []
    return item


async def _suggest_collections(vec: np.ndarray) -> list:
    conn = get_db()
    try:
        collections = conn.execute("SELECT * FROM collections").fetchall()
        if not collections:
            return []
        suggestions = []
        for col in collections:
            member_vecs = conn.execute("""
                SELECT e.vector FROM embeddings e
                JOIN collection_items ci ON ci.item_id = e.item_id
                WHERE ci.collection_id = ?
            """, (col["id"],)).fetchall()
            if not member_vecs:
                continue
            avg = np.mean([np.frombuffer(v[0], dtype=np.float32) for v in member_vecs], axis=0)
            sim = cosine_similarity(vec, avg)
            if sim > 0.45:
                suggestions.append({"id": col["id"], "name": col["name"],
                                   "icon": col["icon"], "similarity": round(sim, 3)})
        suggestions.sort(key=lambda x: x["similarity"], reverse=True)
        return suggestions[:3]
    except Exception as e:
        logger.debug("Collection suggestion failed: %s", e)
        return []
    finally:
        conn.close()


async def _bg_analyze_github(item_id: str, repo_data: dict):
    try:
        from backend.github_stars import AIAnalyzer
        analyzer = AIAnalyzer()
        result = await analyzer.analyze_repo(repo_data, repo_data.get("_readme", ""))
        if result.get("summary"):
            conn = get_db()
            try:
                conn.execute("""
                    UPDATE github_repos SET ai_summary=?, ai_tags=?, ai_platforms=? WHERE item_id=?
                """, (result.get("summary", ""), json.dumps(result.get("tags", [])),
                      json.dumps(result.get("platforms", [])), item_id))
                conn.execute("UPDATE items SET summary=?, tags=? WHERE id=?",
                             (result.get("summary", ""), json.dumps(result.get("tags", [])), item_id))
                conn.commit()
            finally:
                conn.close()
            await events.publish(f"⭐ [AI] 已分析 {repo_data.get('full_name', '')}")
            try:
                from backend.gitmem0_client import remember as gm_remember
                await gm_remember(
                    f"GitHub: {repo_data.get('full_name', '')} - {result['summary']}",
                    type="fact", importance=0.6, source="landrop:github",
                    tags=result.get("tags", [])
                )
            except Exception as e:
                logger.debug("gitmem0 remember failed: %s", e)
    except Exception as e:
        logger.error("GitHub background analysis error: %s", e)


@router.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    title: str = Form(""),
    tags: str = Form("[]"),
    space: str = Form("default")
):
    item_id = uuid.uuid4().hex[:12]
    filename = file.filename or "unnamed"
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"文件过大，最大允许 {MAX_UPLOAD_SIZE // 1024 // 1024}MB")
    result = await process_binary_file(item_id, content, filename, space=space, force_title=title)
    vec = result.pop("_vec", None)
    if vec is not None:
        result["suggested_collections"] = await _suggest_collections(vec)
    return result


@router.post("/api/text")
async def add_text(
    content: str = Form(...),
    title: str = Form(""),
    is_code: bool = Form(False),
    space: str = Form("default")
):
    if len(content) > MAX_TEXT_LENGTH:
        raise HTTPException(413, f"文本过长，最大允许 {MAX_TEXT_LENGTH} 字符")
    item_id = uuid.uuid4().hex[:12]
    await events.publish(f"📝 [系统 IO] 捕获剪切板或直接文本输入传入 ({len(content)} 字符)...")

    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    try:
        conn_check = get_db()
        try:
            existing = conn_check.execute(
                "SELECT h.item_id, i.title FROM content_hashes h JOIN items i ON i.id = h.item_id WHERE h.hash = ?",
                (content_hash,)
            ).fetchone()
        finally:
            conn_check.close()
        if existing:
            return {"ok": True, "id": existing["item_id"], "duplicate": True,
                    "summary": f"与已有记录「{existing['title']}」内容完全相同", "tags": [], "category": ""}
    except Exception as e:
        logger.debug("Duplicate check failed: %s", e)

    stripped = content.strip()
    gh_repo_match = re.match(r'https?://github\.com/([^/]+)/([^/?#\s]+)', stripped)
    if gh_repo_match and '/' not in gh_repo_match.group(2):
        owner, repo = gh_repo_match.group(1), gh_repo_match.group(2)
        full_name = f"{owner}/{repo}"
        try:
            conn_gh = get_db()
            try:
                existing_gh = conn_gh.execute(
                    "SELECT item_id FROM github_repos WHERE full_name=?", (full_name,)
                ).fetchone()
            finally:
                conn_gh.close()
            if existing_gh:
                return {"ok": True, "id": existing_gh["item_id"], "duplicate": True,
                        "summary": f"已存在于 GitHub Stars: {full_name}", "tags": [], "category": ""}
        except Exception as e:
            logger.debug("GitHub duplicate check failed: %s", e)
        try:
            from backend.github_stars import GitHubClient, SyncEngine
            token_gh = get_primary_github_token()
            if token_gh:
                await events.publish(f"⭐ [智能路由] 检测到 GitHub 仓库: {full_name}，接入 Stars 系统...")
                gh = GitHubClient(token_gh)
                repo_data = await gh.get_repo(owner, repo)
                readme = await gh.get_readme(owner, repo)
                repo_data["_readme"] = readme
                engine = SyncEngine(gh)
                item_id_new, is_new = await engine.upsert_repo(repo_data)
                if is_new:
                    asyncio.create_task(_bg_analyze_github(item_id_new, repo_data))
                await gh.close()
                await events.publish(f"⭐ [智能路由] {full_name} 已添加到 GitHub Stars")
                return {"ok": True, "id": item_id_new, "summary": repo_data.get("description", ""),
                        "tags": repo_data.get("topics", []), "category": "",
                        "msg": f"已添加到 GitHub Stars: {full_name}"}
        except Exception as e:
            logger.info("GitHub URL router fallback to normal flow: %s", e)

    url_match = re.search(r'(https?://[^\s]+)', content)
    if url_match:
        url = url_match.group(1)
        scraped = await fetch_url_content(url)
        if scraped:
            content = f"{content}\n\n--- 网页智能抽取内容 ---\n{scraped}"

    info = await ai_summarize_and_tag(title, content, rewrite=True)
    final_content = info.get("formatted_content", content)
    final_title = title if title and title.strip() else info.get("title", "")
    if not final_title or not final_title.strip():
        content_preview = content.strip().replace('\n', ' ')
        final_title = content_preview[:20] + "..." if len(content_preview) > 20 else content_preview
        if not final_title:
            final_title = "无标题"

    item_type = "code" if info.get("is_code", is_code) else "text"
    actual_space = info.get("space", "default") if space == "auto" else space

    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO items (id, type, title, content, tags, summary, space, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, item_type, final_title, final_content,
              json.dumps(info.get("tags", []), ensure_ascii=False),
              info.get("summary", ""), actual_space, now, now))
        conn.commit()

        embed_text = f"{final_title}\n{info.get('summary', '')}\n{content[:2000]}"
        vec = await get_embedding(embed_text)
        conn.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)",
                     (item_id, vec.tobytes()))
        conn.execute("INSERT OR IGNORE INTO content_hashes (hash, item_id, created_at) VALUES (?, ?, ?)",
                     (content_hash, item_id, now))
        conn.commit()
    finally:
        conn.close()

    gm_stored = False
    try:
        from backend.gitmem0_client import remember as gm_remember
        gm_resp = await gm_remember(
            f"{final_title}\n{info.get('summary', '')}",
            type="fact", importance=0.5, source="landrop:text",
            tags=info.get("tags", [])
        )
        gm_stored = gm_resp.get("ok", False)
    except Exception as e:
        logger.debug("gitmem0 remember failed: %s", e)

    cross_refs = []
    try:
        gh_urls = re.findall(r'github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)', content)
        if gh_urls:
            conn_xr = get_db()
            try:
                new_tags = list(info.get("tags", []))
                for fn in set(gh_urls):
                    fn = fn.rstrip('/')
                    row = conn_xr.execute("SELECT item_id FROM github_repos WHERE full_name=?", (fn,)).fetchone()
                    if row:
                        ref_tag = f"🔗{fn}"
                        if ref_tag not in new_tags:
                            new_tags.append(ref_tag)
                        cross_refs.append({"full_name": fn, "item_id": row["item_id"]})
                if cross_refs:
                    conn_xr.execute("UPDATE items SET tags=? WHERE id=?",
                                    (json.dumps(new_tags, ensure_ascii=False), item_id))
                    conn_xr.commit()
            finally:
                conn_xr.close()
    except Exception as e:
        logger.debug("Cross-reference scan failed: %s", e)

    suggestions = await _suggest_collections(vec)

    return {"ok": True, "id": item_id, "summary": info.get("summary", ""),
            "tags": info.get("tags", []), "category": info.get("category", ""),
            "suggested_collections": suggestions, "memory_stored": gm_stored}


@router.get("/api/spaces")
async def get_spaces():
    conn = get_db()
    try:
        spaces = conn.execute("SELECT * FROM spaces ORDER BY id").fetchall()
        return {"spaces": [dict(s) for s in spaces]}
    finally:
        conn.close()


@router.get("/api/upload")
async def dummy_upload_get():
    return {"msg": "Use POST to upload"}


@router.get("/api/items")
async def list_items(
    page: int = 1,
    page_size: int = 30,
    type_filter: str = "",
    search: str = "",
    space: str = "default",
    collection: str = "",
    rerank: bool = False
):
    conn = get_db()
    try:
        if collection:
            offset = (page - 1) * page_size
            join = "JOIN collection_items ci ON ci.item_id = i.id WHERE ci.collection_id=?"
            params = (collection,)

            if type_filter:
                items = conn.execute(
                    f"SELECT i.* FROM items i {join} AND i.type=? ORDER BY i.created_at DESC LIMIT ? OFFSET ?",
                    params + (type_filter, page_size, offset)
                ).fetchall()
                total = conn.execute(f"SELECT COUNT(*) FROM items i {join} AND i.type=?", params + (type_filter,)).fetchone()[0]
            else:
                items = conn.execute(
                    f"SELECT i.* FROM items i {join} ORDER BY i.created_at DESC LIMIT ? OFFSET ?",
                    params + (page_size, offset)
                ).fetchall()
                total = conn.execute(f"SELECT COUNT(*) FROM items i {join}", params).fetchone()[0]

            return {"items": [_clean_item(dict(i)) for i in items], "total": total}

        if search:
            context_prompt = ""
            if space != "all":
                space_row = conn.execute("SELECT context_prompt FROM spaces WHERE id=?", (space,)).fetchone()
                if space_row:
                    context_prompt = space_row["context_prompt"]

            results = await hybrid_search(search, page_size, rerank=rerank, space_context_prompt=context_prompt)
            results = [r for r in results if r.get("space", "default") == space or space == "all"]

            try:
                gh_token = get_primary_github_token()
                if gh_token and len(search) >= 2:
                    from backend.github_stars import GitHubClient
                    gh_s = GitHubClient(gh_token)
                    gh_data = await gh_s.search_repos(search, sort="stars", page=1)
                    await gh_s.close()
                    existing_fns = {r.get("full_name", "") for r in results if r.get("type") == "github_star"}
                    for r in gh_data.get("items", [])[:5]:
                        if r["full_name"] not in existing_fns:
                            results.append({
                                "id": f"gh_ext_{r['full_name']}",
                                "type": "github_external",
                                "title": r["full_name"],
                                "content": r.get("description", "") or "",
                                "summary": r.get("description", "") or "",
                                "tags": r.get("topics", []),
                                "space": "default",
                                "created_at": r.get("created_at", ""),
                                "updated_at": r.get("updated_at", ""),
                                "_gh_external": {
                                    "full_name": r["full_name"],
                                    "html_url": r.get("html_url", ""),
                                    "stars": r.get("stargazers_count", 0),
                                    "forks": r.get("forks_count", 0),
                                    "language": r.get("language", "") or "",
                                    "topics": r.get("topics", []),
                                }
                            })
            except Exception as e:
                logger.warning("GitHub API supplement error: %s", e)

            return {"items": [_clean_item(i) for i in results], "total": len(results)}

        offset = (page - 1) * page_size
        query_space = "1=1" if space == "all" else "space=?"
        params = () if space == "all" else (space,)

        if type_filter:
            items = conn.execute(
                f"SELECT * FROM items WHERE type=? AND {query_space} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (type_filter,) + params + (page_size, offset)
            ).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM items WHERE type=? AND {query_space}", (type_filter,) + params).fetchone()[0]
        else:
            items = conn.execute(
                f"SELECT * FROM items WHERE {query_space} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + (page_size, offset)
            ).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM items WHERE {query_space}", params).fetchone()[0]

        return {"items": [_clean_item(dict(i)) for i in items], "total": total}
    finally:
        conn.close()


@router.post("/api/generate_report")
async def generate_report(request: Request):
    body = await request.json()
    period = body.get("period", "daily")
    type_map = {"daily": "kb_daily", "weekly": "kb_weekly"}
    report_type = type_map.get(period, "kb_daily")
    from backend.routers.system import trigger_digest_type
    return await trigger_digest_type(report_type)


@router.get("/api/items/{item_id}/related")
async def get_related_items(item_id: str):
    from backend.qmd.models import get_embed_dim
    conn = get_db()
    try:
        embed_row = conn.execute("SELECT vector FROM embeddings WHERE item_id=?", (item_id,)).fetchone()
        if not embed_row:
            return {"items": []}

        query_vec = np.frombuffer(embed_row[0], dtype=np.float32)
        if query_vec.ndim > 1:
            query_vec = query_vec.flatten()

        items = conn.execute("""
            SELECT i.*, e.vector
            FROM items i
            JOIN embeddings e ON i.id = e.item_id
            WHERE i.id != ?
            ORDER BY i.created_at DESC
        """, (item_id,)).fetchall()
    finally:
        conn.close()

    if not items:
        return {"items": []}

    valid_items = []
    valid_vectors = []
    dim = get_embed_dim()

    for row in items:
        vec = np.frombuffer(row["vector"], dtype=np.float32)
        if vec.shape[0] == dim:
            valid_items.append(row)
            valid_vectors.append(vec)

    if not valid_vectors:
        return {"items": []}

    items = valid_items
    matrix = np.array(valid_vectors)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return {"items": []}

    matrix_norms = np.linalg.norm(matrix, axis=1)
    matrix_norms[matrix_norms == 0] = 1e-10

    sims = np.dot(matrix, query_vec) / (query_norm * matrix_norms)

    valid_indices = np.where(sims > 0.4)[0]
    sorted_valid_indices = valid_indices[np.argsort(sims[valid_indices])[::-1]][:5]

    related = [_clean_item(dict(items[i])) for i in sorted_valid_indices]
    return {"items": related}


@router.get("/api/items/{item_id}/crossrefs")
async def get_cross_references(item_id: str):
    conn = get_db()
    try:
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return {"github_repos": [], "related_items": []}

        tags = json.loads(item["tags"] or "[]")
        linked_repos = []
        for tag in tags:
            if tag.startswith("🔗"):
                fn = tag[1:]
                repo_row = conn.execute("""
                    SELECT gr.*, i.id as item_id, i.summary, i.tags as item_tags
                    FROM github_repos gr JOIN items i ON i.id = gr.item_id
                    WHERE gr.full_name=?
                """, (fn,)).fetchone()
                if repo_row:
                    linked_repos.append(dict(repo_row))

        title = item["title"] or ""
        reverse_refs = []
        if title:
            rows = conn.execute("""
                SELECT i.id, i.title, i.summary, i.tags, i.type, i.created_at
                FROM items i WHERE i.id != ? AND i.tags LIKE ?
                ORDER BY i.created_at DESC LIMIT 10
            """, (item_id, f"%🔗%{title}%")).fetchall()
            reverse_refs = [dict(r) for r in rows]

        if item["type"] == "github_star":
            repo_row = conn.execute("SELECT full_name FROM github_repos WHERE item_id=?", (item_id,)).fetchone()
            if repo_row:
                fn = repo_row["full_name"]
                rows = conn.execute("""
                    SELECT i.id, i.title, i.summary, i.tags, i.type, i.created_at
                    FROM items i WHERE i.id != ? AND i.tags LIKE ?
                    ORDER BY i.created_at DESC LIMIT 10
                """, (item_id, f"%🔗{fn}%")).fetchall()
                for r in rows:
                    r_dict = dict(r)
                    if r_dict not in reverse_refs:
                        reverse_refs.append(r_dict)

    finally:
        conn.close()

    return {"github_repos": linked_repos, "related_items": reverse_refs}


@router.get("/api/download/{item_id}")
async def download(item_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Not found")
    row = dict(row)
    if row["type"] not in ("file", "image") or not row.get("file_path"):
        raise HTTPException(400, "Not a file or image")
    ext = Path(row["file_path"]).suffix
    base_name = row.get("title") or Path(row["file_path"]).stem
    download_name = base_name if base_name.lower().endswith(ext.lower()) else f"{base_name}{ext}"

    if not os.path.exists(row["file_path"]):
        raise HTTPException(404, "此文件的物理实体已不在硬盘中，但记录残留，建议直接在前端删除此废弃卡片。")

    return FileResponse(row["file_path"], filename=download_name)


@router.delete("/api/items/{item_id}")
async def delete_item(item_id: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row:
            row = dict(row)
            if row.get("file_path") and os.path.exists(row["file_path"]):
                os.remove(row["file_path"])
            conn.execute("DELETE FROM chunks WHERE item_id=?", (item_id,))
            conn.execute("DELETE FROM embeddings WHERE item_id=?", (item_id,))
            conn.execute("DELETE FROM items WHERE id=?", (item_id,))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.put("/api/items/{item_id}")
async def update_item(
    item_id: str,
    content: str = Form(...),
    title: str = Form(""),
):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        row = dict(row)
        if row["type"] == "file":
            raise HTTPException(400, "Cannot edit file directly")

        info = await ai_summarize_and_tag(title or row["title"], content)
        final_title = title if title and title.strip() else info.get("title", row["title"])
        if not final_title or not final_title.strip():
            content_preview = content.strip().replace('\n', ' ')
            final_title = content_preview[:20] + "..." if len(content_preview) > 20 else content_preview
            if not final_title:
                final_title = "无标题"

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
    finally:
        conn.close()
    return {"ok": True}
