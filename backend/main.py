#!/usr/bin/env python3
"""
KnowHub - 智能知识管理平台
启动: python backend/main.py
"""

import os
import json
import re
import uuid
import fcntl
import asyncio
import logging
from datetime import datetime
from pathlib import Path

import time
from collections import defaultdict
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import hmac

from backend.config import HOST, PORT, load_config, save_config, events, hash_password
from backend.database import get_db, init_db
from backend.ai_services import ai_chat, ai_summarize_and_tag, get_embedding, hybrid_search, fetch_url_content
from backend.file_services import process_binary_file
from backend.wechat_agent import start_wechat_worker
from backend.dropzone_worker import start_dropzone_worker, stop_dropzone_worker
from backend.reminder_worker import start_reminder_worker, stop_reminder_worker

logger = logging.getLogger("knowhub")


class RateLimiter:
    """Simple in-memory rate limiter per IP."""
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window
        self._hits[key] = [t for t in self._hits[key] if t > window_start]
        if len(self._hits[key]) >= self.max_requests:
            return False
        self._hits[key].append(now)
        return True

ask_limiter = RateLimiter(max_requests=20, window_seconds=60)
upload_limiter = RateLimiter(max_requests=30, window_seconds=60)

app = FastAPI(title="KnowHub")


# ── Middleware ─────────────────────────────────────────────────────────────

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path.startswith("/api/") and request.url.path != "/api/login":
            cfg = load_config()
            pwd = cfg.get("SYSTEM_PASSWORD", "")
            if pwd:
                token = request.cookies.get("knowhub_token")
                if not token:
                    return JSONResponse({"error": "哎呀，还没有登录哦～"}, status_code=401)
                # Cookie stores hash(password). Stored pwd may be hash or legacy plaintext.
                is_hashed = len(pwd) == 64 and all(c in '0123456789abcdef' for c in pwd)
                expected = pwd if is_hashed else hash_password(pwd)
                if not hmac.compare_digest(token, expected):
                    return JSONResponse({"error": "哎呀，还没有登录哦～"}, status_code=401)

            # Rate limiting on expensive endpoints
            client_ip = request.client.host if request.client else "unknown"
            if request.url.path == "/api/ask" and not ask_limiter.is_allowed(client_ip):
                return JSONResponse({"error": "问得太快啦，让我喘口气～"}, status_code=429)
            if request.url.path == "/api/upload" and not upload_limiter.is_allowed(client_ip):
                return JSONResponse({"error": "传得太快啦，稍等一下哦～"}, status_code=429)

        return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1:8999", "http://localhost:8999"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityMiddleware)


# ── Lifecycle ─────────────────────────────────────────────────────────────

wechat_worker_task = None
_worker_lock_fd = None


def _try_acquire_worker_lock():
    global _worker_lock_fd
    lock_path = Path(os.environ.get("LANDROP_LOCK_PATH", "/tmp/knowhub_worker.lock"))
    try:
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        _worker_lock_fd = fd
        return True
    except (IOError, OSError):
        return False


@app.on_event("startup")
async def startup_event():
    global wechat_worker_task
    if _try_acquire_worker_lock():
        print(f"[Worker {os.getpid()}] ✅ Acquired leader lock, starting WeChat & DropZone workers")
        wechat_worker_task = asyncio.create_task(start_wechat_worker())
        await start_dropzone_worker()

        async def _wait_and_start_reminder():
            from backend.wechat_agent import wx_client_ready, wx_client as wxc
            try:
                await asyncio.wait_for(wx_client_ready.wait(), timeout=60)
                await start_reminder_worker(wxc)
            except asyncio.TimeoutError:
                logger.warning("WeChat client not ready in 60s, skipping reminder worker")
            except Exception as e:
                logger.error("Failed to start reminder worker: %s", e)
        asyncio.create_task(_wait_and_start_reminder())

        try:
            from backend.github_stars import start_github_worker
            asyncio.create_task(start_github_worker())
        except Exception as e:
            logger.error("GitHub Stars worker start error: %s", e)
    else:
        print(f"[Worker {os.getpid()}] ℹ️ Another worker holds the lock, HTTP-only mode")

    async def _warmup_gitmem0():
        for attempt in range(6):
            await asyncio.sleep(5)
            try:
                from backend.gitmem0_client import warmup
                ok = await warmup()
                if ok:
                    print(f"[gitmem0] daemon 预热完成 (第{attempt+1}次尝试)", flush=True)
                    return
            except Exception as e:
                logger.debug("gitmem0 warmup attempt %d failed: %s", attempt + 1, e)
        logger.warning("gitmem0 daemon warmup failed after 6 attempts")
    asyncio.create_task(_warmup_gitmem0())

    async def _warmup_qmd():
        import fcntl
        await asyncio.sleep(2)
        def _load_with_lock():
            lock_path = "/tmp/qmd_warmup.lock"
            fd = open(lock_path, 'w')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                from backend.qmd.models import get_model
                get_model()
                print(f"[QMD] Embedding 模型预热完成 (pid={os.getpid()})", flush=True)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
        try:
            await asyncio.to_thread(_load_with_lock)
        except Exception as e:
            logger.error("QMD embedding warmup failed: %s", e)
    asyncio.create_task(_warmup_qmd())


@app.on_event("shutdown")
async def shutdown_event_handler():
    await stop_reminder_worker()
    try:
        from backend.github_stars import stop_github_worker
        await stop_github_worker()
    except Exception as e:
        logger.debug("GitHub worker stop error: %s", e)
    if wechat_worker_task:
        wechat_worker_task.cancel()
        try:
            await wechat_worker_task
        except asyncio.CancelledError:
            pass
    await stop_dropzone_worker()
    await events.shutdown()
    if _worker_lock_fd:
        fcntl.flock(_worker_lock_fd, fcntl.LOCK_UN)
        _worker_lock_fd.close()


# ── Webhook ───────────────────────────────────────────────────────────────

@app.post("/api/webhook/openilink")
async def openilink_webhook(request: Request):
    try:
        # Validate webhook auth token if configured
        cfg_wh = load_config()
        expected_token = cfg_wh.get("WEBHOOK_TOKEN", "")
        if expected_token:
            auth_header = request.headers.get("Authorization", "")
            query_token = request.query_params.get("token", "")
            if not (hmac.compare_digest(auth_header.replace("Bearer ", ""), expected_token) or
                    hmac.compare_digest(query_token, expected_token)):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)

        data = await request.json()
        content = data.get("content", "").strip()
        content = re.sub(r'^@\S+\s+', '', content).strip()

        if not content:
            return {"type": "text", "content": "收到空消息。"}

        await events.publish(f"💬 [微信通道] 收到 ClawBot 微信消息：{content[:20]}...")

        if content.startswith("/ask ") or content.startswith("问 "):
            question = content[5:] if content.startswith("/ask ") else content[2:]
            search_res = await hybrid_search(question, top_k=5)
            ctx = "\n".join([f"[{r['title']}] {r['summary']}" for r in search_res])
            prompt = f"上下文资料：\n{ctx}\n\n微信用户提问：{question}\n限制条件：回复必须极其精简，适合在微信手机端阅读，不要废话。"

            ans_stream = await ai_chat([{"role": "system", "content": prompt}])
            ans = ans_stream.choices[0].message.content
            return {"type": "text", "content": f"💡 {ans}"}

        else:
            item_id = uuid.uuid4().hex[:12]
            url_match = re.search(r'(https?://[^\s]+)', content)
            scraped = ""
            if url_match:
                scraped = await fetch_url_content(url_match.group(1))
                content_full = f"{content}\n\n[微信端通过智能抽取的网页]\n{scraped}" if scraped else content
            else:
                content_full = content

            info = await ai_summarize_and_tag("微信捕获", content_full, rewrite=True)
            final_content = info.get("formatted_content", content_full)
            final_title = info.get("title", content[:15] + "...")
            item_type = "code" if info.get("is_code", False) else "text"

            now = datetime.now().isoformat()
            tags = json.dumps(["微信投递"] + info.get("tags", []), ensure_ascii=False)
            summary = info.get("summary", content[:50])

            conn = get_db()
            try:
                conn.execute("""
                    INSERT INTO items (id, type, title, content, tags, summary, space, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (item_id, item_type, final_title, final_content, tags, summary, "default", now, now))

                embed_text = f"{final_title}\n{summary}\n{final_content[:2000]}"
                vec = await get_embedding(embed_text)
                conn.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)", (item_id, vec.tobytes()))
                conn.commit()
            finally:
                conn.close()

            return {
                "type": "text",
                "content": f"收到啦！已经帮你整理好了～\n\n📌 {final_title}\n🏷️ {', '.join(info.get('tags', []))}"
            }

    except Exception as e:
        logger.error("Webhook processing error: %s", e)
        return {"type": "text", "content": f"哎呀，出了点小状况：{str(e)}"}


# ── Register Routers ──────────────────────────────────────────────────────

from backend.routers import events as events_router
from backend.routers import items, ask, settings, github, collections, system

app.include_router(events_router.router)
app.include_router(items.router)
app.include_router(ask.router)
app.include_router(settings.router)
app.include_router(github.router)
app.include_router(collections.router)
app.include_router(system.router)


# ── Static Files ──────────────────────────────────────────────────────────

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
else:
    @app.get("/")
    async def index():
        return {"message": "Frontend not built yet. Please run 'npm run build' in the frontend directory."}


# ── Entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    print(f"""
  ✨ KnowHub 已启动～
  本机访问: http://localhost:{PORT}
  AI 模型:  {cfg.get('AI_MODEL')}
  去浏览器打开看看吧！
    """)
    uvicorn.run(app, host=HOST, port=PORT)
