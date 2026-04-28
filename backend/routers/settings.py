import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.config import load_config, save_config, get_safe_config, verify_password, hash_password
from backend.database import get_db

logger = logging.getLogger("knowhub")

router = APIRouter()


@router.get("/api/settings")
async def get_settings():
    return get_safe_config()


@router.post("/api/settings")
async def update_settings(request: Request):
    data = await request.json()
    data.pop("GITHUB_TOKEN", None)
    save_config(data)
    return {"ok": True}


@router.post("/api/login")
async def login(request: Request):
    body = await request.json()
    password = body.get("password", "")
    cfg = load_config()
    stored = cfg.get("SYSTEM_PASSWORD", "")
    if verify_password(password, stored):
        cookie_val = hash_password(password)
        response = JSONResponse({"ok": True})
        response.set_cookie(key="knowhub_token", value=cookie_val, httponly=True, samesite="strict", max_age=86400 * 30)
        return response
    return JSONResponse({"error": "Invalid Password"}, status_code=401)


@router.get("/api/stats")
async def stats():
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM items WHERE type='file'").fetchone()[0]
        texts = conn.execute("SELECT COUNT(*) FROM items WHERE type='text'").fetchone()[0]
        codes = conn.execute("SELECT COUNT(*) FROM items WHERE type='code'").fetchone()[0]
        total_size = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM items").fetchone()[0]
    finally:
        conn.close()
    return {"total": total, "files": files, "texts": texts, "codes": codes, "total_size": total_size}


@router.get("/api/gitmem0/stats")
async def gitmem0_stats():
    from backend.gitmem0_client import stats as gm_stats
    return await gm_stats()


@router.get("/api/gitmem0/metrics")
async def gitmem0_metrics():
    from backend.gitmem0_client import metrics as gm_metrics
    return await gm_metrics()
