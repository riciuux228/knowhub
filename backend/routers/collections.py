import json
import uuid
import logging
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Request, HTTPException

from backend.database import get_db
from backend.ai_services import get_embedding, cosine_similarity

logger = logging.getLogger("knowhub")

router = APIRouter()


@router.get("/api/collections")
async def list_collections():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT c.*, COUNT(ci.item_id) as item_count
            FROM collections c LEFT JOIN collection_items ci ON ci.collection_id = c.id
            GROUP BY c.id ORDER BY c.updated_at DESC
        """).fetchall()
        return {"collections": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post("/api/collections")
async def create_collection(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    cid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    icon = data.get("icon", "📁")
    desc = data.get("description", "")
    conn = get_db()
    try:
        conn.execute("INSERT INTO collections (id, name, description, icon, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (cid, name, desc, icon, now, now))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": cid}


@router.put("/api/collections/{collection_id}")
async def update_collection(collection_id: str, request: Request):
    data = await request.json()
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute("UPDATE collections SET name=?, description=?, icon=?, updated_at=? WHERE id=?",
                     (data.get("name", ""), data.get("description", ""), data.get("icon", "📁"), now, collection_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/collections/{collection_id}/items")
async def add_to_collection(collection_id: str, request: Request):
    data = await request.json()
    item_ids = data.get("item_ids", [])
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        for iid in item_ids:
            conn.execute("INSERT OR IGNORE INTO collection_items (collection_id, item_id, added_at) VALUES (?,?,?)",
                        (collection_id, iid, now))
        conn.execute("UPDATE collections SET updated_at=? WHERE id=?", (now, collection_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "added": len(item_ids)}


@router.delete("/api/collections/{collection_id}/items/{item_id}")
async def remove_from_collection(collection_id: str, item_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM collection_items WHERE collection_id=? AND item_id=?", (collection_id, item_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/collections/suggest")
async def suggest_collections(request: Request):
    data = await request.json()
    title = data.get("title", "")
    content = data.get("content", "")
    text = f"{title}\n{content[:2000]}"

    conn = get_db()
    try:
        collections = conn.execute("SELECT * FROM collections").fetchall()
        if not collections:
            return {"suggestions": []}

        text_vec = await get_embedding(text)
        suggestions = []

        for col in collections:
            member_vecs = conn.execute("""
                SELECT e.vector FROM embeddings e
                JOIN collection_items ci ON ci.item_id = e.item_id
                WHERE ci.collection_id = ?
            """, (col["id"],)).fetchall()

            if not member_vecs:
                continue

            vecs = [np.frombuffer(v[0], dtype=np.float32) for v in member_vecs]
            avg_vec = np.mean(vecs, axis=0)
            sim = cosine_similarity(text_vec, avg_vec)

            if sim > 0.45:
                suggestions.append({"id": col["id"], "name": col["name"],
                                   "icon": col["icon"], "similarity": round(sim, 3)})

        suggestions.sort(key=lambda x: x["similarity"], reverse=True)
        return {"suggestions": suggestions[:3]}
    finally:
        conn.close()
