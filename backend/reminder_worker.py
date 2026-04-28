import asyncio
import uuid
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("knowhub")

from backend.database import get_db
from backend.config import events

_client = None
_task = None
_last_digest_check_minute = -1
_report_queue = None  # Sequential queue for report generation


async def start_reminder_worker(client):
    global _client, _task, _report_queue
    _client = client
    _report_queue = asyncio.Queue()
    _task = asyncio.create_task(_poll_loop())
    asyncio.create_task(_report_queue_worker())
    print("[Reminder] 提醒调度器已启动 (每30秒检查一次到期任务)", flush=True)

async def stop_reminder_worker():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    print("[Reminder] 提醒调度器已停止")


async def _report_queue_worker():
    """Process report generation jobs sequentially."""
    while True:
        try:
            fn = await _report_queue.get()
            await fn()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[ReportQueue] Job failed: {e}", flush=True)


async def _get_wx_client():
    """Get WeChat client — try worker's client first, then wechat_agent's."""
    if _client:
        return _client
    try:
        from backend.wechat_agent import wx_client_ready, wx_client
        if wx_client_ready.is_set() and wx_client:
            return wx_client
    except Exception as e:
        logger.debug("wechat_agent import failed: %s", e)
    return None


async def _poll_loop():
    global _last_digest_check_minute
    while True:
        try:
            await _check_and_send()
            now = datetime.now()
            current_minute = now.hour * 60 + now.minute
            if current_minute != _last_digest_check_minute:
                _last_digest_check_minute = current_minute
                await _check_digest(now)
        except Exception as e:
            print(f"[Reminder] 检查异常: {e}", flush=True)
        await asyncio.sleep(30)

async def _check_and_send():
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status='pending' AND remind_at <= ?", (now,)
        ).fetchall()
        if not rows:
            return

        for row in rows:
            rid = row["id"]
            to_user_id = row["to_user_id"]
            ctx_token = row["context_token"]
            content = row["content"]

            try:
                from backend.weclaw_bot import build_text_message
                c_id = uuid.uuid4().hex
                msg = build_text_message(to_user_id, f"⏰ 定时提醒\n\n{content}", ctx_token, c_id)
                await _client.send_message(msg)
                conn.execute("UPDATE reminders SET status='sent' WHERE id=?", (rid,))
                conn.commit()
                await events.publish(f"⏰ [Reminder] 已发送提醒: {content[:30]}")
                print(f"[Reminder] 已发送提醒 [{rid}]: {content[:50]}", flush=True)
            except Exception as e:
                print(f"[Reminder] 发送失败 [{rid}]: {e}", flush=True)
    finally:
        conn.close()


# ── Schedule checker ────────────────────────────────────────────────────────

def _should_fire(enabled, hour, last_val, now, check_fn):
    if not enabled:
        return False
    if now.hour != hour:
        return False
    return not check_fn(last_val or "", now)

async def _check_digest(now: datetime):
    """Check if any scheduled digest should fire."""
    conn = get_db()
    try:
        cfg = conn.execute("SELECT * FROM digest_config WHERE id=1").fetchone()
        if not cfg:
            return

        now_iso = now.isoformat()
        to_fire = []

        # ── KB digest ──
        if _should_fire(cfg["daily_enabled"], cfg["daily_hour"], cfg["last_daily"], now,
                         lambda last, n: last.startswith(n.strftime("%Y-%m-%d"))):
            to_fire.append(("kb_daily", "last_daily"))

        if cfg["weekly_enabled"] and now.weekday() == cfg["weekly_day"] and now.hour == cfg["weekly_hour"]:
            last_weekly = cfg["last_weekly"] or ""
            week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            if not last_weekly.startswith(week_start):
                to_fire.append(("kb_weekly", "last_weekly"))

        # ── GitHub Stars ──
        if _should_fire(cfg["gh_stars_daily_enabled"], cfg["gh_stars_daily_hour"], cfg["last_gh_stars_daily"], now,
                         lambda last, n: last.startswith(n.strftime("%Y-%m-%d"))):
            to_fire.append(("gh_stars_daily", "last_gh_stars_daily"))

        if cfg["gh_stars_weekly_enabled"] and now.weekday() == cfg["gh_stars_weekly_day"] and now.hour == cfg["gh_stars_weekly_hour"]:
            last = cfg["last_gh_stars_weekly"] or ""
            week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            if not last.startswith(week_start):
                to_fire.append(("gh_stars_weekly", "last_gh_stars_weekly"))

        # ── GitHub Trending ──
        if _should_fire(cfg["gh_trending_daily_enabled"], cfg["gh_trending_daily_hour"], cfg["last_gh_trending_daily"], now,
                         lambda last, n: last.startswith(n.strftime("%Y-%m-%d"))):
            to_fire.append(("gh_trending_daily", "last_gh_trending_daily"))

        if cfg["gh_trending_weekly_enabled"] and now.weekday() == cfg["gh_trending_weekly_day"] and now.hour == cfg["gh_trending_weekly_hour"]:
            last = cfg["last_gh_trending_weekly"] or ""
            week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            if not last.startswith(week_start):
                to_fire.append(("gh_trending_weekly", "last_gh_trending_weekly"))

        if cfg["gh_trending_monthly_enabled"] and now.day == cfg["gh_trending_monthly_day"] and now.hour == cfg["gh_trending_monthly_hour"]:
            last = cfg["last_gh_trending_monthly"] or ""
            month_start = now.strftime("%Y-%m-")
            if not last.startswith(month_start):
                to_fire.append(("gh_trending_monthly", "last_gh_trending_monthly"))

        # Update timestamps immediately to prevent re-enqueue on next check
        for _, col in to_fire:
            conn.execute(f"UPDATE digest_config SET {col}=? WHERE id=1", (now_iso,))
        if to_fire:
            conn.commit()

        for report_type, _ in to_fire:
            await enqueue_report(report_type)
            print(f"[Digest] 入队: {report_type}", flush=True)

    except Exception as e:
        print(f"[Digest] 检查异常: {e}", flush=True)
    finally:
        conn.close()


# ── Report queue ────────────────────────────────────────────────────────────

async def enqueue_report(report_type: str):
    """Enqueue a report for sequential generation. Returns immediately."""
    if _report_queue is None:
        # Fallback: run directly if worker not started (e.g. called from web endpoint)
        await _run_report(report_type)
        return
    await _report_queue.put(lambda: _run_report(report_type))


async def _run_report(report_type: str):
    """Execute a single report by type."""
    conn = get_db()
    now = datetime.now()
    try:
        dispatch = {
            "kb_daily": lambda: _generate_kb_digest("daily", conn, now),
            "kb_weekly": lambda: _generate_kb_digest("weekly", conn, now),
            "gh_stars_daily": lambda: _generate_gh_stars_report("daily", conn, now),
            "gh_stars_weekly": lambda: _generate_gh_stars_report("weekly", conn, now),
            "gh_trending_daily": lambda: _generate_gh_trending_report("daily", conn, now),
            "gh_trending_weekly": lambda: _generate_gh_trending_report("weekly", conn, now),
            "gh_trending_monthly": lambda: _generate_gh_trending_report("monthly", conn, now),
        }
        fn = dispatch.get(report_type)
        if fn:
            await fn()
    finally:
        conn.close()


# ── Shared helpers ──────────────────────────────────────────────────────────

async def _save_report(title, content, tags, summary, conn, now):
    """Save a report to items + embedding, return report_id."""
    from backend.ai_services import get_embedding
    report_id = uuid.uuid4().hex[:12]
    now_iso = now.isoformat()

    conn.execute('''INSERT INTO items (id, type, title, content, tags, summary, space, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (report_id, 'text', title, content, json.dumps(tags, ensure_ascii=False),
         summary, 'ideas', now_iso, now_iso))

    embed_text = f"{title}\n{content[:2000]}"
    vec = await get_embedding(embed_text)
    conn.execute("INSERT INTO embeddings (item_id, vector) VALUES (?, ?)", (report_id, vec.tobytes()))
    conn.commit()
    return report_id

async def _push_wechat(content_preview, conn):
    """Push a notification via WeChat, splitting long messages if needed."""
    wx = await _get_wx_client()
    if not wx:
        return
    try:
        user_row = conn.execute("SELECT to_user_id, context_token FROM reminders LIMIT 1").fetchone()
        if not user_row:
            return
        to_id = user_row["to_user_id"]
        ctx_token = user_row["context_token"]
        MAX_LEN = 800

        if len(content_preview) <= MAX_LEN:
            from backend.weclaw_bot import build_text_message
            c_id = uuid.uuid4().hex
            msg = build_text_message(to_id, content_preview, ctx_token, c_id)
            await wx.send_message(msg)
            return

        # Split by paragraphs
        parts, current = [], ""
        for para in content_preview.split("\n\n"):
            if len(current) + len(para) + 2 > MAX_LEN and current:
                parts.append(current.rstrip())
                current = para
            else:
                current = current + "\n\n" + para if current else para
        if current:
            parts.append(current.rstrip())

        # Hard-split oversized paragraphs by lines
        final_parts = []
        for p in parts:
            if len(p) <= MAX_LEN:
                final_parts.append(p)
            else:
                chunk = ""
                for line in p.split("\n"):
                    if len(chunk) + len(line) + 1 > MAX_LEN and chunk:
                        final_parts.append(chunk.rstrip())
                        chunk = line
                    else:
                        chunk = chunk + "\n" + line if chunk else line
                if chunk:
                    final_parts.append(chunk.rstrip())

        # Hard-split by chars if still too long
        really_final = []
        for p in final_parts:
            if len(p) <= MAX_LEN:
                really_final.append(p)
            else:
                for i in range(0, len(p), MAX_LEN):
                    really_final.append(p[i:i+MAX_LEN])

        total = len(really_final)
        from backend.weclaw_bot import build_text_message
        for i, part in enumerate(really_final):
            prefix = f"[{i + 1}/{total}]\n" if total > 1 else ""
            c_id = uuid.uuid4().hex
            msg = build_text_message(to_id, prefix + part, ctx_token, c_id)
            await wx.send_message(msg)
            if i < total - 1:
                await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[Digest] WeChat push failed: {e}", flush=True)


# ── KB Digest ───────────────────────────────────────────────────────────────

async def _generate_kb_digest(period: str, conn, now: datetime):
    days = 1 if period == "daily" else 7
    time_threshold = now - timedelta(days=days)
    time_str = time_threshold.isoformat()

    rows = conn.execute(
        "SELECT id, title, summary, type, created_at FROM items WHERE created_at >= ? AND title NOT LIKE '%AI 节点战报%' AND title NOT LIKE '%GitHub%' AND title NOT LIKE '%Trending%'",
        (time_str,)
    ).fetchall()

    if not rows:
        print(f"[Digest] {'日报' if period == 'daily' else '周报'} 无新内容，跳过", flush=True)
        return

    snippets = [f"- [{r['created_at'][:16]}] {r['title']} ({r['type']}): {r['summary']}" for r in rows]
    snippets_str = "\n".join(snippets)
    period_name = "日报" if period == "daily" else "周报"

    today = now.strftime("%Y-%m-%d")
    prompt = f"今天是 {today}。以下是过去 {'24小时' if period == 'daily' else '7天'} 内存入的知识碎片：\n\n{snippets_str}\n\n请撰写一份《{period_name}》，找出碎片间的隐藏关联，用精美的 GitHub Flavored Markdown 排版。报告开头注明日期。"

    try:
        from backend.ai_services import ai_chat
        resp = await ai_chat([
            {"role": "system", "content": "你是 KnowHub 的知识助手小可，负责撰写知识简报。语气亲切温暖，像朋友聊天一样自然。使用 GitHub Flavored Markdown 格式。"},
            {"role": "user", "content": prompt}
        ])
        content = resp.choices[0].message.content.strip()

        report_title = f"AI 节点战报：{now.strftime('%Y-%m-%d')} ({'日结' if period == 'daily' else '周报'})"
        await _save_report(report_title, content, ["AI衍生", "知识简报"], f"由 {len(rows)} 块碎片自主归纳。", conn, now)

        conn.commit()

        await _push_wechat(f"📰 {period_name}已生成\n\n{content[:800]}...", conn)
        await events.publish(f"📰 [Digest] {period_name}已自动生成并推送")
        print(f"[Digest] {period_name} 已发送 ({len(rows)} 条记录)", flush=True)
    except Exception as e:
        print(f"[Digest] {period_name} 生成失败: {e}", flush=True)


# ── GitHub Stars Report ─────────────────────────────────────────────────────

async def _generate_gh_stars_report(period: str, conn, now: datetime):
    days = 1 if period == "daily" else 7
    time_threshold = now - timedelta(days=days)
    time_str = time_threshold.isoformat()

    rows = conn.execute("""
        SELECT r.full_name, r.description, r.language, r.stars, r.ai_summary, r.ai_tags, i.created_at
        FROM github_repos r JOIN items i ON r.item_id = i.id
        WHERE i.created_at >= ?
        ORDER BY r.stars DESC
    """, (time_str,)).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM github_repos").fetchone()[0]
    languages = conn.execute("SELECT language, COUNT(*) as cnt FROM github_repos WHERE language != '' GROUP BY language ORDER BY cnt DESC LIMIT 10").fetchall()

    period_name = "日报" if period == "daily" else "周报"

    today = now.strftime("%Y-%m-%d")
    if not rows:
        lang_stats = ", ".join([f"{r['language']}({r['cnt']})" for r in languages[:5]])
        prompt = f"今天是 {today}。当前 GitHub Stars 数据库共有 {total} 个仓库。\n语言分布：{lang_stats or '暂无数据'}\n\n请撰写一份简短的《GitHub Stars {period_name}》状态概览，用 GitHub Flavored Markdown 排版。"
    else:
        snippets = [f"- **{r['full_name']}** ⭐{r['stars']} | {r['language'] or 'N/A'} | {r['description'][:80]}" for r in rows]
        snippets_str = "\n".join(snippets)
        lang_stats = ", ".join([f"{r['language']}({r['cnt']})" for r in languages[:5]])
        prompt = f"今天是 {today}。过去 {'24小时' if period == 'daily' else '7天'} 新增了 {len(rows)} 个 Star 仓库：\n\n{snippets_str}\n\n数据库总计 {total} 个仓库。语言分布：{lang_stats}\n\n请撰写一份《GitHub Stars {period_name}》，包含新增亮点、趋势分析，用 GitHub Flavored Markdown 排版。"

    try:
        from backend.ai_services import ai_chat
        resp = await ai_chat([
            {"role": "system", "content": "你是 KnowHub 的 GitHub 分析助手。用亲切自然的语气写报告，像朋友推荐好东西一样。使用 GitHub Flavored Markdown 格式。"},
            {"role": "user", "content": prompt}
        ])
        content = resp.choices[0].message.content.strip()

        report_title = f"GitHub Stars {'日结' if period == 'daily' else '周报'}：{now.strftime('%Y-%m-%d')}"
        await _save_report(report_title, content, ["AI衍生", "GitHub", "Stars"], f"共 {total} 个仓库，本期新增 {len(rows)} 个。", conn, now)

        conn.commit()

        await _push_wechat(f"⭐ GitHub Stars {period_name}已生成\n\n{content[:800]}...", conn)
        await events.publish(f"⭐ [GH Stars] {period_name}已自动生成并推送")
        print(f"[GH Stars] {period_name} 已发送 ({len(rows)} 新增)", flush=True)
    except Exception as e:
        print(f"[GH Stars] {period_name} 生成失败: {e}", flush=True)


# ── GitHub Trending Report ──────────────────────────────────────────────────

async def _generate_gh_trending_report(period: str, conn, now: datetime):
    since_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    since = since_map[period]
    period_name = {"daily": "日报", "weekly": "周报", "monthly": "月报"}[period]

    try:
        from backend.github_stars import fetch_github_trending
        trending = await fetch_github_trending(language="", since=since)

        if not trending:
            print(f"[GH Trending] {period_name} 无趋势数据", flush=True)
            return

        snippets = [f"- **{r.get('full_name', '?')}** ⭐{r.get('stars', 0)} | {r.get('language', 'N/A')} | {r.get('description', '')[:80]}" for r in trending[:20]]
        snippets_str = "\n".join(snippets)

        today = now.strftime("%Y-%m-%d")
        prompt = f"今天是 {today}。以下是 GitHub {period_name}趋势项目 ({since})：\n\n{snippets_str}\n\n共 {len(trending)} 个趋势项目。请撰写一份《GitHub Trending {period_name}》，分析热门技术方向和值得关注的项目，用 GitHub Flavored Markdown 排版。报告开头注明日期。"

        from backend.ai_services import ai_chat
        resp = await ai_chat([
            {"role": "system", "content": "你是 KnowHub 的趋势发现助手。用轻松有趣的语气分析 GitHub 趋势，像分享有趣发现一样。使用 GitHub Flavored Markdown 格式。"},
            {"role": "user", "content": prompt}
        ])
        content = resp.choices[0].message.content.strip()

        report_title = f"GitHub Trending {period_name}：{now.strftime('%Y-%m-%d')}"
        await _save_report(report_title, content, ["AI衍生", "GitHub", "Trending"], f"本期 {len(trending)} 个趋势项目。", conn, now)

        conn.commit()

        await _push_wechat(f"🔥 GitHub Trending {period_name}已生成\n\n{content[:800]}...", conn)
        await events.publish(f"🔥 [GH Trending] {period_name}已自动生成并推送")
        print(f"[GH Trending] {period_name} 已发送 ({len(trending)} 项目)", flush=True)
    except Exception as e:
        print(f"[GH Trending] {period_name} 生成失败: {e}", flush=True)
