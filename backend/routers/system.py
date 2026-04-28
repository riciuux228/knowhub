import os
import json
import time
import logging
import asyncio
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse

from backend.config import events
from backend.database import DB_PATH, get_db
from backend.ai_services import get_embedding, cosine_similarity

logger = logging.getLogger("knowhub")

router = APIRouter()


# ── Reminders ─────────────────────────────────────────────────────────────

@router.get("/api/reminders")
async def list_reminders(status: str = "all"):
    conn = get_db()
    try:
        if status == "all":
            rows = conn.execute("SELECT * FROM reminders ORDER BY remind_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM reminders WHERE status=? ORDER BY remind_at DESC", (status,)).fetchall()
        return {"reminders": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.delete("/api/reminders/{reminder_id}")
async def cancel_reminder(reminder_id: str):
    conn = get_db()
    try:
        conn.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (reminder_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── Digest / Reports ──────────────────────────────────────────────────────

@router.get("/api/digest/config")
async def get_digest_config():
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM digest_config WHERE id=1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


@router.post("/api/digest/config")
async def update_digest_config(request: Request):
    data = await request.json()
    conn = get_db()
    try:
        fields = [
            "daily_enabled", "daily_hour",
            "weekly_enabled", "weekly_hour", "weekly_day",
            "gh_stars_daily_enabled", "gh_stars_daily_hour",
            "gh_stars_weekly_enabled", "gh_stars_weekly_hour", "gh_stars_weekly_day",
            "gh_trending_daily_enabled", "gh_trending_daily_hour",
            "gh_trending_weekly_enabled", "gh_trending_weekly_hour", "gh_trending_weekly_day",
            "gh_trending_monthly_enabled", "gh_trending_monthly_hour", "gh_trending_monthly_day",
        ]
        sets = ", ".join([f"{f}=?" for f in fields])
        vals = [int(data.get(f, 0)) for f in fields]
        conn.execute(f"UPDATE digest_config SET {sets} WHERE id=1", vals)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


async def trigger_digest_type(report_type: str):
    from backend.reminder_worker import enqueue_report
    valid_types = {"kb_daily", "kb_weekly", "gh_stars_daily", "gh_stars_weekly",
                   "gh_trending_daily", "gh_trending_weekly", "gh_trending_monthly"}
    if report_type not in valid_types:
        return {"ok": False, "msg": f"未知报告类型: {report_type}"}
    try:
        await enqueue_report(report_type)
        return {"ok": True, "type": report_type}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@router.post("/api/digest/trigger")
async def trigger_digest(request: Request):
    body = await request.json()
    report_type = body.get("type", "")
    if not report_type:
        period = body.get("period", "daily")
        report_type = f"kb_{period}"
    return await trigger_digest_type(report_type)


# ── Backup & Export ───────────────────────────────────────────────────────

@router.get("/api/backup")
async def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(DB_PATH, filename=f"landrop_backup_{timestamp}.db", media_type="application/x-sqlite3")


@router.get("/api/export")
async def export_markdown():
    conn = get_db()
    try:
        items = conn.execute("SELECT id, type, title, content, tags, summary, space, updated_at FROM items").fetchall()
    finally:
        conn.close()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for it in items:
                space_str = it['space'] or 'default'
                title = it['title'] or '无标题'
                safe_title = "".join([c if c.isalnum() else "_" for c in title]).strip("_")[:50]
                if not safe_title:
                    safe_title = "unnamed"

                tags_str = it['tags'] or "[]"
                file_name = f"LANDrop_Brain/{space_str}/{safe_title}_{it['id'][:6]}.md"

                md_content = f"---\nSpace: {space_str}\nTags: {tags_str}\nID: {it['id']}\nDate: {it['updated_at']}\n---\n\n# {title}\n\n> **AI Summary**: {it.get('summary', '')}\n\n---\n\n{it.get('content', '')}"
                zf.writestr(file_name, md_content.encode('utf-8'))
    except Exception:
        os.unlink(tmp.name)
        raise

    from starlette.background import BackgroundTask
    return FileResponse(
        tmp.name,
        filename=f"landrop_obsidian_{int(time.time())}.zip",
        media_type="application/zip",
        background=BackgroundTask(os.unlink, tmp.name)
    )


# ── Gallery ───────────────────────────────────────────────────────────────

@router.get("/api/gallery")
async def get_gallery(
    type: str = "image",
    space: str = "all",
    page: int = 1,
    page_size: int = 50
):
    conn = get_db()
    try:
        offset = (page - 1) * page_size
        space_cond = "1=1" if space == "all" else "space=?"
        space_params = () if space == "all" else (space,)

        if type == "image":
            items = conn.execute(
                f"SELECT id, type, title, file_path, file_size, mime_type, created_at, space FROM items WHERE type='image' AND {space_cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                space_params + (page_size, offset)
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM items WHERE type='image' AND {space_cond}", space_params
            ).fetchone()[0]
        else:
            items = conn.execute(
                f"SELECT id, type, title, file_path, file_size, mime_type, created_at, space FROM items WHERE type='file' AND {space_cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                space_params + (page_size, offset)
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM items WHERE type='file' AND {space_cond}", space_params
            ).fetchone()[0]

        result = []
        for it in items:
            d = dict(it)
            d["has_file"] = bool(d.get("file_path") and os.path.exists(d["file_path"]))
            result.append(d)
        return {"items": result, "total": total}
    finally:
        conn.close()


# ── Knowledge Graph ───────────────────────────────────────────────────────

@router.get("/api/graph")
async def get_graph_data():
    from backend.qmd.models import get_embed_dim

    def _compute_graph():
        conn = get_db()
        try:
            items = conn.execute("SELECT i.id, i.title, i.space, i.type, e.vector FROM items i JOIN embeddings e ON i.id = e.item_id").fetchall()
        finally:
            conn.close()

        if not items:
            return {"nodes": [], "links": []}

        valid_items = []
        valid_vectors = []
        dim = get_embed_dim()

        nodes = []
        for row in items:
            vec = np.frombuffer(row["vector"], dtype=np.float32)
            if vec.shape[0] == dim:
                valid_items.append(row)
                valid_vectors.append(vec)
                nodes.append({
                    "id": row["id"],
                    "name": row["title"] or "未命名节点",
                    "group": row["space"] or "default",
                    "val": 1 if row["type"] != 'file' else 2
                })

        if not valid_vectors:
            return {"nodes": [], "links": []}

        matrix = np.array(valid_vectors)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        normalized_matrix = matrix / norms

        sim_matrix = np.dot(normalized_matrix, normalized_matrix.T)

        links = []
        THRESHOLD = 0.58

        num_items = len(valid_items)
        for i in range(num_items):
            for j in range(i + 1, num_items):
                score = sim_matrix[i, j]
                if score > THRESHOLD:
                    links.append({
                        "source": valid_items[i]["id"],
                        "target": valid_items[j]["id"],
                        "value": float(score)
                    })

        return {"nodes": nodes, "links": links}

    return await asyncio.to_thread(_compute_graph)
