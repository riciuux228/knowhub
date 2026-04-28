"""GitHub Stars Manager — 同步、分析、分类、Release 追踪、趋势发现

核心模块：将 GitHub star 仓库同步进 KnowHub 知识库。
"""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from base64 import b64decode
from xml.etree import ElementTree

logger = logging.getLogger("knowhub")

import httpx

from backend.database import get_db
from backend.config import load_config, save_config
from backend.config import events


# ── GitHub API 客户端 ─────────────────────────────────────────────────────

class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "KnowHub-GitHubStars/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._sem = asyncio.Semaphore(10)
        self._client = httpx.AsyncClient(timeout=30)

    async def _get(self, url: str, params: dict = None) -> dict | list | None:
        async with self._sem:
            r = await self._client.get(url, headers=self.headers, params=params)
            if r.status_code == 403 and "rate limit" in r.text.lower():
                reset = int(r.headers.get("X-RateLimit-Reset", 0))
                wait = max(reset - int(datetime.now(timezone.utc).timestamp()), 10)
                await asyncio.sleep(min(wait, 120))
                r = await self._client.get(url, headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

    async def get_starred(self, page: int = 1, per_page: int = 100) -> list[dict]:
        return await self._get(
            f"{self.BASE}/user/starred",
            {"page": page, "per_page": per_page, "sort": "created", "direction": "desc"},
        ) or []

    async def get_all_starred(self) -> list[dict]:
        all_repos = []
        page = 1
        while True:
            batch = await self.get_starred(page=page)
            if not batch:
                break
            all_repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            await asyncio.sleep(0.1)
        return all_repos

    async def get_repo(self, owner: str, repo: str) -> dict:
        return await self._get(f"{self.BASE}/repos/{owner}/{repo}") or {}

    async def star_repo(self, owner: str, repo: str) -> bool:
        async with self._sem:
            r = await self._client.put(f"{self.BASE}/user/starred/{owner}/{repo}", headers=self.headers)
            return r.status_code in (204, 200)

    async def unstar_repo(self, owner: str, repo: str) -> bool:
        async with self._sem:
            r = await self._client.delete(f"{self.BASE}/user/starred/{owner}/{repo}", headers=self.headers)
            return r.status_code in (204, 200)

    async def check_starred(self, owner: str, repo: str) -> bool:
        async with self._sem:
            r = await self._client.get(f"{self.BASE}/user/starred/{owner}/{repo}", headers=self.headers)
            return r.status_code == 204

    async def get_readme(self, owner: str, repo: str) -> str:
        try:
            data = await self._get(f"{self.BASE}/repos/{owner}/{repo}/readme")
            if data and data.get("content"):
                return b64decode(data["content"]).decode("utf-8", errors="replace")[:10000]
        except Exception:
            pass
        return ""

    async def get_readme_html(self, owner: str, repo: str) -> str:
        """Fetch README rendered as HTML from GitHub."""
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{self.BASE}/repos/{owner}/{repo}/readme",
                    headers={**self.headers, "Accept": "application/vnd.github.html+json"},
                )
                if r.status_code == 200:
                    return r.text[:20000]
        except Exception:
            pass
        # Fallback: return raw content
        return await self.get_readme(owner, repo)

    async def get_file_html(self, owner: str, repo: str, path: str) -> str:
        """Fetch any file rendered as HTML from GitHub (for .md files)."""
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{self.BASE}/repos/{owner}/{repo}/contents/{path}",
                    headers={**self.headers, "Accept": "application/vnd.github.html+json"},
                )
                if r.status_code == 200:
                    return r.text[:20000]
        except Exception:
            pass
        # Fallback: raw content
        try:
            data = await self._get(f"{self.BASE}/repos/{owner}/{repo}/contents/{path}")
            if data and data.get("content"):
                from base64 import b64decode
                return b64decode(data["content"]).decode("utf-8", errors="replace")[:10000]
        except Exception:
            pass
        return ""

    async def get_releases(self, owner: str, repo: str, page: int = 1, per_page: int = 30) -> list[dict]:
        return await self._get(
            f"{self.BASE}/repos/{owner}/{repo}/releases",
            {"page": page, "per_page": per_page},
        ) or []

    async def search_repos(self, q: str, sort: str = "stars", order: str = "desc", page: int = 1) -> dict:
        return await self._get(
            f"{self.BASE}/search/repositories",
            {"q": q, "sort": sort, "order": order, "page": page, "per_page": 30},
        ) or {}

    async def get_rate_limit(self) -> dict:
        return await self._get(f"{self.BASE}/rate_limit") or {}

    async def close(self):
        await self._client.aclose()


# ── AI 分析器 ─────────────────────────────────────────────────────────────

class AIAnalyzer:
    async def analyze_repo(self, repo_data: dict, readme: str = "") -> dict:
        from backend.ai_services import ai_chat
        info = (
            f"Name: {repo_data.get('full_name', '')}\n"
            f"Description: {repo_data.get('description', '')}\n"
            f"Language: {repo_data.get('language', '')}\n"
            f"Stars: {repo_data.get('stargazers_count', 0)}\n"
            f"Topics: {', '.join(repo_data.get('topics', []))}\n"
            f"README (excerpt):\n{(readme or '')[:3000]}"
        )
        messages = [
            {"role": "system", "content": (
                "你是一个技术仓库分析师。请用中文分析这个 GitHub 仓库并返回 JSON。\n"
                "要求：\n"
                "1. summary: 用中文写一段 80-150 字的摘要，说明项目是什么、解决什么问题、有什么特点\n"
                "2. tags: 3-5 个技术标签（中文），如：前端框架、CLI工具、机器学习、数据库、移动端、桌面应用、API、安全工具、游戏引擎、设计工具、效率工具、教育学习、数据分析\n"
                "3. platforms: 检测平台，从以下选择：web, cli, mobile, desktop, library, api, other\n"
                "4. category: 建议分类，从以下选择：web应用, 移动应用, 桌面应用, 数据库, AI/机器学习, 开发工具, 安全工具, 游戏, 设计工具, 效率工具, 教育学习, 社交网络, 数据分析\n\n"
                '返回格式：{"summary":"...","tags":["..."],"platforms":["..."],"category":"..."}\n'
                "只返回 JSON，不要 markdown 代码块。"
            )},
            {"role": "user", "content": info},
        ]
        try:
            resp = await ai_chat(messages)
            # Handle both string and ChatCompletion object
            if hasattr(resp, 'choices'):
                text = resp.choices[0].message.content.strip()
            else:
                text = str(resp).strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception as e:
            print(f"[AI] Analysis error: {e}")
            return {"summary": repo_data.get("description", "")[:100], "tags": [], "platforms": [], "category": ""}

    async def batch_analyze(self, repos: list[dict], concurrency: int = 5, progress_cb=None):
        sem = asyncio.Semaphore(concurrency)
        results = {}
        total = len(repos)
        done = [0]

        async def _analyze(repo):
            async with sem:
                readme = ""
                try:
                    gh = None  # readme already fetched in upsert
                    result = await self.analyze_repo(repo, repo.get("_readme", ""))
                except Exception:
                    result = {"summary": "", "tags": [], "platforms": []}
                done[0] += 1
                if progress_cb:
                    await progress_cb(done[0], total, repo.get("full_name", ""))
                return repo.get("full_name", ""), result

        tasks = [_analyze(r) for r in repos]
        for coro in asyncio.as_completed(tasks):
            key, result = await coro
            results[key] = result
        return results


# ── 同步引擎 ─────────────────────────────────────────────────────────────

class SyncEngine:
    def __init__(self, gh: GitHubClient):
        self.gh = gh
        self.analyzer = AIAnalyzer()

    async def full_sync(self, progress_cb=None) -> dict:
        await events.publish("⭐ [GitHub] 开始同步星标仓库...")
        all_repos = await self.gh.get_all_starred()
        total = len(all_repos)
        await events.publish(f"⭐ [GitHub] 共发现 {total} 个星标仓库")

        new_count, update_count = 0, 0
        for i, repo in enumerate(all_repos):
            try:
                readme = await self.gh.get_readme(repo["owner"]["login"], repo["name"])
                repo["_readme"] = readme
                item_id, is_new = await self.upsert_repo(repo)
                if is_new:
                    new_count += 1
                else:
                    update_count += 1
                if progress_cb:
                    await progress_cb(i + 1, total, repo["full_name"])
            except Exception as e:
                print(f"[GitHub] Sync error for {repo.get('full_name', '?')}: {e}")

        # AI analyze new repos
        cfg = load_config()
        if cfg.get("GITHUB_AUTO_ANALYZE", True):
            await events.publish("⭐ [GitHub] 开始 AI 分析新仓库...")
            await self._analyze_unanalyzed()

        await events.publish(f"⭐ [GitHub] 同步完成! 新增 {new_count}, 更新 {update_count}")
        return {"total": total, "new": new_count, "updated": update_count}

    async def incremental_sync(self) -> dict:
        batch = await self.gh.get_starred(page=1, per_page=100)
        conn = get_db()
        existing = {r["full_name"] for r in conn.execute("SELECT full_name FROM github_repos").fetchall()}
        conn.close()

        new_repos = [r for r in batch if r["full_name"] not in existing]
        if not new_repos:
            return {"new": 0}

        new_count = 0
        for repo in new_repos:
            try:
                readme = await self.gh.get_readme(repo["owner"]["login"], repo["name"])
                repo["_readme"] = readme
                _, is_new = await self.upsert_repo(repo)
                if is_new:
                    new_count += 1
            except Exception as e:
                print(f"[GitHub] Incremental sync error: {e}")

        if new_count > 0:
            await events.publish(f"⭐ [GitHub] 增量同步: 新增 {new_count} 个仓库")
            cfg = load_config()
            if cfg.get("GITHUB_AUTO_ANALYZE", True):
                await self._analyze_unanalyzed()

        return {"new": new_count}

    async def upsert_repo(self, repo_data: dict) -> tuple[str, bool]:
        full_name = repo_data["full_name"]
        owner = repo_data["owner"]["login"]
        repo_name = repo_data["name"]
        now = datetime.now(timezone.utc).isoformat()

        conn = get_db()
        existing = conn.execute(
            "SELECT item_id FROM github_repos WHERE full_name = ?", (full_name,)
        ).fetchone()

        if existing:
            item_id = existing["item_id"]
            conn.execute("""
                UPDATE github_repos SET description=?, language=?, topics=?, license=?,
                stars=?, forks=?, watchers=?, open_issues=?, pushed_at=?, last_synced=?
                WHERE item_id=?
            """, (
                repo_data.get("description", ""),
                repo_data.get("language", ""),
                json.dumps(repo_data.get("topics", [])),
                (repo_data.get("license") or {}).get("spdx_id", ""),
                repo_data.get("stargazers_count", 0),
                repo_data.get("forks_count", 0),
                repo_data.get("watchers_count", 0),
                repo_data.get("open_issues_count", 0),
                repo_data.get("pushed_at", ""),
                now, item_id,
            ))
            conn.execute("UPDATE items SET updated_at=? WHERE id=?", (now, item_id))
            conn.commit()
            conn.close()
            return item_id, False

        # New repo
        item_id = uuid.uuid4().hex[:12]
        readme = repo_data.get("_readme", "")
        content = f"{full_name}\n{repo_data.get('description', '')}\n{readme[:5000]}"

        conn.execute("""
            INSERT INTO items (id, type, title, content, tags, summary, created_at, updated_at)
            VALUES (?, 'github_star', ?, ?, '[]', '', ?, ?)
        """, (item_id, full_name, content, now, now))

        conn.execute("""
            INSERT INTO github_repos (item_id, full_name, owner, repo_name, html_url,
                description, homepage, language, topics, license, stars, forks, watchers,
                open_issues, default_branch, pushed_at, created_at_gh, last_synced)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            item_id, full_name, owner, repo_name,
            repo_data.get("html_url", ""),
            repo_data.get("description", ""),
            repo_data.get("homepage", ""),
            repo_data.get("language", ""),
            json.dumps(repo_data.get("topics", [])),
            (repo_data.get("license") or {}).get("spdx_id", ""),
            repo_data.get("stargazers_count", 0),
            repo_data.get("forks_count", 0),
            repo_data.get("watchers_count", 0),
            repo_data.get("open_issues_count", 0),
            repo_data.get("default_branch", "main"),
            repo_data.get("pushed_at", ""),
            repo_data.get("created_at", ""),
            now,
        ))

        # Generate embedding
        try:
            from backend.ai_services import get_embedding
            embed_text = f"{full_name} {repo_data.get('description', '')} {' '.join(repo_data.get('topics', []))}"
            vec = await get_embedding(embed_text)
            conn.execute("INSERT OR REPLACE INTO embeddings (item_id, vector) VALUES (?, ?)",
                         (item_id, vec.tobytes()))
        except Exception as e:
            print(f"[GitHub] Embedding error for {full_name}: {e}")

        conn.commit()
        conn.close()

        # gitmem0 sync
        try:
            from backend.gitmem0_client import remember as gm_remember
            asyncio.create_task(gm_remember(
                f"GitHub star: {full_name} - {repo_data.get('description', '')}",
                type="fact", importance=0.5, source="knowhub:github",
                tags=repo_data.get("topics", [])
            ))
        except Exception as e:
            logger.debug("gitmem0 remember failed for %s: %s", full_name, e)

        return item_id, True

    async def _analyze_unanalyzed(self):
        conn = get_db()
        rows = conn.execute("""
            SELECT gr.item_id, gr.full_name, gr.description, gr.language, gr.topics,
                   i.content as readme
            FROM github_repos gr
            JOIN items i ON i.id = gr.item_id
            WHERE gr.ai_summary = '' OR gr.ai_summary IS NULL
            ORDER BY gr.stars DESC LIMIT 50
        """).fetchall()
        conn.close()

        for row in rows:
            try:
                repo_data = {
                    "full_name": row["full_name"],
                    "description": row["description"],
                    "language": row["language"],
                    "topics": json.loads(row["topics"] or "[]"),
                    "_readme": row["readme"] or "",
                }
                result = await self.analyzer.analyze_repo(repo_data, row["readme"] or "")
                conn = get_db()
                # Try to assign category from AI result
                cat_id = None
                ai_cat = result.get("category", "")
                if ai_cat:
                    cat_row = conn.execute("SELECT id FROM github_categories WHERE name=?", (ai_cat,)).fetchone()
                    if cat_row:
                        cat_id = cat_row["id"]
                conn.execute("""
                    UPDATE github_repos SET ai_summary=?, ai_tags=?, ai_platforms=?, category_id=COALESCE(?, category_id)
                    WHERE item_id=?
                """, (
                    result.get("summary", ""),
                    json.dumps(result.get("tags", [])),
                    json.dumps(result.get("platforms", [])),
                    cat_id,
                    row["item_id"],
                ))
                conn.execute("UPDATE items SET summary=?, tags=? WHERE id=?",
                             (result.get("summary", ""), json.dumps(result.get("tags", [])), row["item_id"]))
                conn.commit()
                conn.close()
                await events.publish(f"⭐ [AI] 已分析 {row['full_name']}")
                # 写入 gitmem0 记忆
                if result.get("summary"):
                    try:
                        from backend.gitmem0_client import remember as gm_remember
                        await gm_remember(
                            f"GitHub: {row['full_name']} - {result['summary']}",
                            type="fact", importance=0.6, source="knowhub:github",
                            tags=result.get("tags", [])
                        )
                    except Exception as e:
                        logger.debug("gitmem0 remember failed for %s: %s", row['full_name'], e)
            except Exception as e:
                print(f"[GitHub] AI analysis error for {row['full_name']}: {e}")


# ── 分类管理 ─────────────────────────────────────────────────────────────

class CategoryManager:
    def list_all(self) -> list[dict]:
        conn = get_db()
        rows = conn.execute("""
            SELECT c.*, COUNT(gr.item_id) as repo_count
            FROM github_categories c
            LEFT JOIN github_repos gr ON gr.category_id = c.id
            GROUP BY c.id ORDER BY c.name
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create(self, name: str, keywords: list = None, color: str = "#8b5cf6", icon: str = "📁") -> str:
        cat_id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        conn.execute("""
            INSERT INTO github_categories (id, name, keywords, color, icon, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (cat_id, name, json.dumps(keywords or []), color, icon, now, now))
        conn.commit()
        conn.close()
        return cat_id

    def update(self, cat_id: str, **kwargs):
        conn = get_db()
        for key in ("name", "keywords", "color", "icon"):
            if key in kwargs:
                val = json.dumps(kwargs[key]) if key == "keywords" else kwargs[key]
                conn.execute(f"UPDATE github_categories SET {key}=?, updated_at=? WHERE id=?",
                             (val, datetime.now(timezone.utc).isoformat(), cat_id))
        conn.commit()
        conn.close()

    def delete(self, cat_id: str):
        conn = get_db()
        conn.execute("UPDATE github_repos SET category_id=NULL WHERE category_id=?", (cat_id,))
        conn.execute("DELETE FROM github_categories WHERE id=?", (cat_id,))
        conn.commit()
        conn.close()

    async def auto_assign(self) -> int:
        conn = get_db()
        categories = conn.execute("SELECT * FROM github_categories").fetchall()
        if not categories:
            conn.close()
            return 0

        uncategorized = conn.execute("""
            SELECT gr.item_id, gr.full_name, gr.description, gr.language, gr.topics
            FROM github_repos gr WHERE gr.category_id IS NULL
        """).fetchall()

        assigned = 0
        for repo in uncategorized:
            best_cat = None
            best_score = 0
            desc = (repo["description"] or "").lower()
            topics = json.loads(repo["topics"] or "[]")
            lang = (repo["language"] or "").lower()

            for cat in categories:
                keywords = json.loads(cat["keywords"] or "[]")
                score = 0
                for kw in keywords:
                    kw_lower = kw.lower()
                    if kw_lower in desc:
                        score += 2
                    if kw_lower in [t.lower() for t in topics]:
                        score += 3
                    if kw_lower == lang:
                        score += 1
                if score > best_score:
                    best_score = score
                    best_cat = cat["id"]

            if best_cat and best_score >= 2:
                conn.execute("UPDATE github_repos SET category_id=? WHERE item_id=?",
                             (best_cat, repo["item_id"]))
                assigned += 1

        conn.commit()
        conn.close()
        if assigned > 0:
            await events.publish(f"⭐ [分类] AI 自动分类完成，分配了 {assigned} 个仓库")
        return assigned


# ── Release 追踪 ─────────────────────────────────────────────────────────

class ReleaseTracker:
    def __init__(self, gh: GitHubClient):
        self.gh = gh

    def subscribe(self, item_id: str, full_name: str) -> str:
        sub_id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        conn.execute("""
            INSERT OR IGNORE INTO github_subscriptions (id, item_id, full_name, enabled, created_at)
            VALUES (?, ?, ?, 1, ?)
        """, (sub_id, item_id, full_name, now))
        conn.commit()
        conn.close()
        return sub_id

    def unsubscribe(self, item_id: str):
        conn = get_db()
        conn.execute("DELETE FROM github_subscriptions WHERE item_id=?", (item_id,))
        conn.commit()
        conn.close()

    def list_subscriptions(self) -> list[dict]:
        conn = get_db()
        rows = conn.execute("""
            SELECT s.*, gr.stars, gr.language, gr.ai_summary,
                   i.title as repo_title
            FROM github_subscriptions s
            JOIN github_repos gr ON gr.item_id = s.item_id
            JOIN items i ON i.id = s.item_id
            WHERE s.enabled = 1 ORDER BY s.created_at DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def fetch_all_subscriptions(self) -> int:
        conn = get_db()
        subs = conn.execute("SELECT * FROM github_subscriptions WHERE enabled=1").fetchall()
        conn.close()

        total_new = 0
        for sub in subs:
            try:
                parts = sub["full_name"].split("/")
                if len(parts) != 2:
                    continue
                new_count = await self.fetch_releases(sub["item_id"], parts[0], parts[1])
                total_new += new_count
            except Exception as e:
                print(f"[GitHub] Release fetch error for {sub['full_name']}: {e}")

        if total_new > 0:
            await events.publish(f"🆕 [GitHub] 发现 {total_new} 个新版本!")
        return total_new

    async def fetch_releases(self, item_id: str, owner: str, repo: str) -> int:
        releases = await self.gh.get_releases(owner, repo, per_page=10)
        conn = get_db()
        existing = {r["tag_name"] for r in conn.execute(
            "SELECT tag_name FROM github_releases WHERE item_id=?", (item_id,)
        ).fetchall()}

        new_count = 0
        for rel in releases:
            if rel["tag_name"] in existing:
                continue
            rel_id = uuid.uuid4().hex[:8]
            assets = [
                {"name": a.get("name", ""), "size": a.get("size", 0),
                 "url": a.get("browser_download_url", ""), "content_type": a.get("content_type", "")}
                for a in rel.get("assets", [])
            ]
            conn.execute("""
                INSERT INTO github_releases (id, item_id, full_name, tag_name, name, body,
                    html_url, published_at, is_prerelease, assets, is_read, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
            """, (
                rel_id, item_id, f"{owner}/{repo}",
                rel["tag_name"], rel.get("name", ""), rel.get("body", ""),
                rel.get("html_url", ""), rel.get("published_at", ""),
                1 if rel.get("prerelease") else 0,
                json.dumps(assets), datetime.now(timezone.utc).isoformat(),
            ))
            new_count += 1

        conn.commit()
        conn.close()
        return new_count

    def mark_read(self, release_id: str):
        conn = get_db()
        conn.execute("UPDATE github_releases SET is_read=1 WHERE id=?", (release_id,))
        conn.commit()
        conn.close()

    def get_timeline(self, unread_only: bool = False, limit: int = 50) -> list[dict]:
        conn = get_db()
        where = "WHERE r.is_read = 0" if unread_only else ""
        rows = conn.execute(f"""
            SELECT r.*, gr.language, gr.ai_summary
            FROM github_releases r
            JOIN github_repos gr ON gr.item_id = r.item_id
            {where}
            ORDER BY r.published_at DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def unread_count(self) -> int:
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM github_releases WHERE is_read=0").fetchone()[0]
        conn.close()
        return count

    def filter_assets(self, assets: list[dict], platforms: list[str] = None) -> list[dict]:
        if not platforms:
            cfg = load_config()
            platforms = cfg.get("GITHUB_ASSET_PLATFORMS", [])
        if not platforms:
            return assets
        keywords = [p.lower() for p in platforms]
        return [a for a in assets if any(kw in a.get("name", "").lower() for kw in keywords)]


# ── 趋势发现 ─────────────────────────────────────────────────────────────

# Language colors for rendering
LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "Java": "#b07219", "Go": "#00ADD8", "Rust": "#dea584", "C": "#555555",
    "C++": "#f34b7d", "C#": "#178600", "Ruby": "#701516", "PHP": "#4F5D95",
    "Swift": "#F05138", "Kotlin": "#A97BFF", "Dart": "#00B4AB", "Shell": "#89e051",
    "Vue": "#41b883", "Svelte": "#ff3e00", "HTML": "#e34c26", "CSS": "#563d7c",
}

class _SimpleCache:
    """In-memory cache with TTL for discovery results."""
    def __init__(self):
        self._store: dict[str, tuple[float, any]] = {}

    def get(self, key: str, ttl_seconds: int = 300):
        if key in self._store:
            ts, val = self._store[key]
            if (datetime.now(timezone.utc).timestamp() - ts) < ttl_seconds:
                return val
            del self._store[key]
        return None

    def set(self, key: str, val: any):
        self._store[key] = (datetime.now(timezone.utc).timestamp(), val)

_discovery_cache = _SimpleCache()


class DiscoveryEngine:
    """Trending / Hot Releases / Most Popular — 100% 对标 GithubStarsManager 实现."""

    RSS_BASE = "https://mshibanami.github.io/GitHubTrendingRSS"

    def __init__(self, gh: GitHubClient):
        self.gh = gh

    # ── 1. Trending: RSS Feed (与原项目完全一致) ──────────────────────────

    async def trending(self, since: str = "daily", language: str = "") -> list[dict]:
        """Fetch trending repos via RSS feed, supplement ALL repos with GitHub API. Fallback to Search API."""
        cache_key = f"trending:{since}:{language}"
        cached = _discovery_cache.get(cache_key, ttl_seconds=600)  # 10 min cache
        if cached is not None:
            return cached

        rss_url = f"{self.RSS_BASE}/{since}/all.xml"
        repos = []

        # Try RSS feed first
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(rss_url)
                r.raise_for_status()
                xml_text = r.text

            root = ElementTree.fromstring(xml_text)
            for item in root.findall(".//item")[:25]:
                link = item.findtext("link", "").strip()
                desc = item.findtext("description", "").strip()
                m = re.search(r"github\.com/([^/]+/[^/?#]+)", link)
                if not m:
                    continue
                full_name = m.group(1)
                clean_desc = re.sub(r'<[^>]+>', ' ', desc)
                clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()[:200]
                repos.append({
                    "full_name": full_name,
                    "description": clean_desc,
                    "language": "", "stargazers_count": 0, "forks_count": 0,
                    "html_url": f"https://github.com/{full_name}",
                })
        except Exception as e:
            print(f"[GitHub] Trending RSS error, falling back to Search API: {e}")

        # Fallback: GitHub Search API if RSS failed or returned nothing
        if not repos and self.gh:
            try:
                if since == "daily":
                    dt = datetime.now(timezone.utc) - timedelta(days=1)
                elif since == "weekly":
                    dt = datetime.now(timezone.utc) - timedelta(weeks=1)
                else:
                    dt = datetime.now(timezone.utc) - timedelta(days=30)
                date_str = dt.strftime("%Y-%m-%d")
                q = f"pushed:>{date_str} stars:>50"
                if language:
                    q += f" language:{language}"
                data = await self.gh.search_repos(q, sort="stars", page=1)
                for r in data.get("items", [])[:25]:
                    repos.append({
                        "full_name": r["full_name"],
                        "description": r.get("description", "") or "",
                        "language": r.get("language", "") or "",
                        "stargazers_count": r.get("stargazers_count", 0),
                        "forks_count": r.get("forks_count", 0),
                        "html_url": r.get("html_url", ""),
                    })
                _discovery_cache.set(cache_key, repos)
                return repos  # Search API already has all data, no need to enrich
            except Exception as e:
                print(f"[GitHub] Trending Search fallback error: {e}")

        # Supplement RSS repos with GitHub API (RSS doesn't include stars/language)
        if repos and self.gh:
            sem = asyncio.Semaphore(5)

            async def enrich(repo):
                async with sem:
                    try:
                        owner, name = repo["full_name"].split("/")
                        data = await self.gh.get_repo(owner, name)
                        if data:
                            repo["stargazers_count"] = data.get("stargazers_count", 0)
                            repo["forks_count"] = data.get("forks_count", 0)
                            repo["language"] = data.get("language", "") or ""
                            repo["description"] = data.get("description", "") or repo["description"]
                    except Exception:
                        pass

            await asyncio.gather(*[enrich(r) for r in repos])

        # Filter by language if specified (after enrichment)
        if language:
            repos = [r for r in repos if r.get("language", "").lower() == language.lower()]

        _discovery_cache.set(cache_key, repos)
        return repos

    # ── 2. Hot Releases: GitHub Search API (与原项目一致) ─────────────────

    async def hot_releases(self, language: str = "", platform: str = "", page: int = 1) -> list[dict]:
        """Find repos with recent updates via GitHub Search API."""
        if not self.gh:
            return []
        cache_key = f"hot:{language}:{platform}:{page}"
        cached = _discovery_cache.get(cache_key, ttl_seconds=600)
        if cached is not None:
            return cached

        fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
        q = f"stars:>50 archived:false pushed:>={fourteen_days_ago}"
        if platform and platform.lower() != "all":
            q += f" {platform}"
        if language:
            q += f" language:{language}"
        try:
            data = await self.gh.search_repos(q, sort="updated", order="desc", page=page)
            items = data.get("items", [])
            result = [{
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "language": r.get("language", ""),
                "stargazers_count": r.get("stargazers_count", 0),
                "forks_count": r.get("forks_count", 0),
                "html_url": r.get("html_url", ""),
                "pushed_at": r.get("pushed_at", ""),
                "updated_at": r.get("updated_at", ""),
            } for r in items[:20]]
            _discovery_cache.set(cache_key, result)
            return result
        except Exception as e:
            print(f"[GitHub] Hot releases search error: {e}")
            return []

    # ── 3. Most Popular: GitHub Search API (与原项目一致) ─────────────────

    async def most_popular(self, language: str = "", platform: str = "", page: int = 1) -> list[dict]:
        """Find globally popular mature repos via GitHub Search API."""
        if not self.gh:
            return []
        cache_key = f"popular:{language}:{platform}:{page}"
        cached = _discovery_cache.get(cache_key, ttl_seconds=600)
        if cached is not None:
            return cached

        six_months_ago = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
        one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
        q = f"stars:>1000 archived:false created:<{six_months_ago} pushed:>={one_year_ago}"
        if platform and platform.lower() != "all":
            q += f" {platform}"
        if language:
            q += f" language:{language}"
        try:
            data = await self.gh.search_repos(q, sort="stars", order="desc", page=page)
            items = data.get("items", [])
            result = [{
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "language": r.get("language", ""),
                "stargazers_count": r.get("stargazers_count", 0),
                "forks_count": r.get("forks_count", 0),
                "html_url": r.get("html_url", ""),
                "topics": r.get("topics", []),
                "created_at": r.get("created_at", ""),
            } for r in items[:20]]
            _discovery_cache.set(cache_key, result)
            return result
        except Exception as e:
            print(f"[GitHub] Most popular search error: {e}")
            return []

    # ── 4. By Topic: GitHub Search API ────────────────────────────────────

    async def by_topic(self, topic: str, page: int = 1) -> list[dict]:
        if not self.gh:
            return []
        cache_key = f"topic:{topic}:{page}"
        cached = _discovery_cache.get(cache_key, ttl_seconds=600)
        if cached is not None:
            return cached
        try:
            data = await self.gh.search_repos(f"topic:{topic} stars:>10", sort="stars", page=page)
            result = [{
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "language": r.get("language", ""),
                "stargazers_count": r.get("stargazers_count", 0),
                "forks_count": r.get("forks_count", 0),
                "html_url": r.get("html_url", ""),
            } for r in data.get("items", [])[:20]]
            _discovery_cache.set(cache_key, result)
            return result
        except Exception:
            return []

    # ── 5. Search: Local + GitHub API ─────────────────────────────────────

    async def search(self, query: str, page: int = 1) -> list[dict]:
        conn = get_db()
        local = conn.execute("""
            SELECT gr.*, i.title, i.summary, i.tags
            FROM github_repos gr JOIN items i ON i.id = gr.item_id
            WHERE i.title LIKE ? OR gr.description LIKE ? OR gr.ai_summary LIKE ?
            ORDER BY gr.stars DESC LIMIT 20
        """, (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
        conn.close()

        local_results = [dict(r) for r in local]
        if len(local_results) >= 5:
            return local_results

        if not self.gh:
            return local_results

        try:
            data = await self.gh.search_repos(query, page=page)
            gh_results = [{
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "language": r.get("language", ""),
                "stargazers_count": r.get("stargazers_count", 0),
                "html_url": r.get("html_url", ""),
            } for r in data.get("items", [])]
            return local_results + gh_results
        except Exception:
            return local_results


# ── 后台 Worker ──────────────────────────────────────────────────────────

_worker_task = None


async def start_github_worker():
    global _worker_task
    from backend.config import get_all_github_tokens
    if not get_all_github_tokens():
        return
    cfg = load_config()
    if not cfg.get("GITHUB_SYNC_ENABLED", False):
        return
    _worker_task = asyncio.create_task(_sync_loop())
    print("[GitHub Stars] Background worker started")


async def stop_github_worker():
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


async def _sync_loop():
    from backend.main import _try_acquire_worker_lock
    if not _try_acquire_worker_lock():
        print("[GitHub Stars] Another worker holds the lock, skipping")
        return

    while True:
        try:
            from backend.config import get_github_accounts
            accounts = [a for a in get_github_accounts() if a.get("enabled", True) and a.get("token")]
            if not accounts:
                await asyncio.sleep(300)
                continue

            for acc in accounts:
                token = acc["token"]
                login = acc.get("login", "unknown")
                try:
                    gh = GitHubClient(token)
                    engine = SyncEngine(gh)
                    tracker = ReleaseTracker(gh)
                    print(f"[GitHub Stars] Syncing account: {login}")
                    await engine.incremental_sync()
                    await tracker.fetch_all_subscriptions()
                    await gh.close()
                except Exception as e:
                    print(f"[GitHub Stars] Sync error for {login}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[GitHub Stars] Sync loop error: {e}")

        await asyncio.sleep(3600)


# ── GitHub Trending ─────────────────────────────────────────────────────────

async def fetch_github_trending(language: str = "", since: str = "daily") -> list[dict]:
    """Fetch trending repos via GitHub Search API.

    since: daily / weekly / monthly
    """
    from datetime import timedelta
    now = datetime.now()
    delta = {"daily": 1, "weekly": 7, "monthly": 30}.get(since, 1)
    date_str = (now - timedelta(days=delta)).strftime("%Y-%m-%d")

    q = f"created:>{date_str}"
    if language:
        q += f" language:{language}"

    token = _get_token()
    if not token:
        return []

    gh = GitHubClient(token)
    try:
        data = await gh.search_repos(q, sort="stars", order="desc", page=1)
        items = data.get("items", [])
        results = []
        for repo in items[:25]:
            results.append({
                "full_name": repo.get("full_name", ""),
                "description": repo.get("description", "") or "",
                "language": repo.get("language", "") or "",
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "html_url": repo.get("html_url", ""),
                "topics": repo.get("topics", []),
                "created_at": repo.get("created_at", ""),
            })
        return results
    except Exception as e:
        print(f"[GitHub] Trending fetch error: {e}")
        return []
    finally:
        await gh.close()


def _get_token() -> str:
    try:
        from backend.config import get_primary_github_token
        return get_primary_github_token()
    except Exception as e:
        logger.debug("get_primary_github_token failed: %s", e)
        return ""
