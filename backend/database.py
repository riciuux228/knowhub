import sqlite3
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "knowhub.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT,
            content TEXT,
            file_path TEXT,
            file_size INTEGER DEFAULT 0,
            mime_type TEXT,
            tags TEXT DEFAULT '[]',
            summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            item_id TEXT PRIMARY KEY,
            vector BLOB NOT NULL,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            chunk_pos INTEGER DEFAULT 0,
            vector BLOB,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS spaces (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            context_prompt TEXT
        );
        INSERT OR IGNORE INTO spaces (id, name, context_prompt) VALUES ('default', '默认空间', '无特定预设');
        INSERT OR IGNORE INTO spaces (id, name, context_prompt) VALUES ('ideas', '灵感库', '请以发散性、创意性的视角处理');
        INSERT OR IGNORE INTO spaces (id, name, context_prompt) VALUES ('work', '工作区', '请保持专业、严谨的商业工程视角');

        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            to_user_id TEXT NOT NULL,
            context_token TEXT NOT NULL,
            content TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_status_at ON reminders(status, remind_at);

        CREATE TABLE IF NOT EXISTS digest_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            daily_enabled INTEGER DEFAULT 1,
            daily_hour INTEGER DEFAULT 9,
            weekly_enabled INTEGER DEFAULT 1,
            weekly_hour INTEGER DEFAULT 9,
            weekly_day INTEGER DEFAULT 1,
            last_daily TEXT,
            last_weekly TEXT
        );
        INSERT OR IGNORE INTO digest_config (id) VALUES (1);

        CREATE TABLE IF NOT EXISTS content_hashes (
            hash TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📁',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collection_items (
            collection_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (collection_id, item_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_ci_collection ON collection_items(collection_id);
        CREATE INDEX IF NOT EXISTS idx_ci_item ON collection_items(item_id);

        -- GitHub Stars: 分类
        CREATE TABLE IF NOT EXISTS github_categories (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            keywords TEXT DEFAULT '[]',
            color TEXT DEFAULT '#8b5cf6',
            icon TEXT DEFAULT '📁',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_web', 'Web应用', '["react","vue","angular","next","nuxt","svelte","web","frontend","backend","fullstack","html","css","javascript","typescript","node","deno","bun","express","fastapi","django","flask","spring"]', '#3b82f6', '🌐', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_mobile', '移动应用', '["android","ios","react-native","flutter","swift","kotlin","mobile","app","expo","capacitor","ionic"]', '#10b981', '📱', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_desktop', '桌面应用', '["electron","tauri","desktop","windows","macos","linux","gui","qt","gtk","wpf","cocoa"]', '#8b5cf6', '💻', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_db', '数据库', '["database","sql","nosql","redis","mongodb","postgres","mysql","sqlite","elasticsearch","vector","chroma","pinecone"]', '#f59e0b', '🗄️', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_ai', 'AI/机器学习', '["ai","ml","machine-learning","deep-learning","llm","gpt","transformer","neural","model","tensorflow","pytorch","openai","langchain","rag","embedding"]', '#ef4444', '🤖', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_devtools', '开发工具', '["devtool","ide","editor","vim","neovim","vscode","debugger","linter","formatter","git","ci","cd","docker","kubernetes","devops","terminal","shell"]', '#6366f1', '🔧', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_security', '安全工具', '["security","cybersecurity","vulnerability","pentest","ctf","encryption","firewall","auth","authentication","oauth","jwt"]', '#dc2626', '🛡️', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_game', '游戏', '["game","gaming","unity","unreal","godot","pygame","engine","2d","3d","opengl","vulkan"]', '#ec4899', '🎮', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_design', '设计工具', '["design","figma","sketch","ui","ux","css","tailwind","animation","3d","blender","canvas","svg"]', '#f472b6', '🎨', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_productivity', '效率工具', '["productivity","todo","note","calendar","task","automation","workflow","obsidian","notion","markdown","writing"]', '#22c55e', '⚡', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_education', '教育学习', '["education","learning","tutorial","course","algorithm","math","science","programming","teaching","study"]', '#0ea5e9', '📚', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_social', '社交网络', '["social","chat","messaging","forum","community","twitter","mastodon","discord","telegram","weibo"]', '#a855f7', '👥', datetime('now'), datetime('now'));
        INSERT OR IGNORE INTO github_categories (id, name, keywords, color, icon, created_at, updated_at) VALUES ('cat_data', '数据分析', '["data","analytics","visualization","chart","dashboard","pandas","numpy","jupyter","etl","pipeline","scraping","crawler"]', '#14b8a6', '📊', datetime('now'), datetime('now'));

        -- GitHub Stars: 仓库元数据（1:1 关联 items）
        CREATE TABLE IF NOT EXISTS github_repos (
            item_id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL UNIQUE,
            owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            html_url TEXT NOT NULL,
            description TEXT DEFAULT '',
            homepage TEXT DEFAULT '',
            language TEXT DEFAULT '',
            topics TEXT DEFAULT '[]',
            license TEXT DEFAULT '',
            stars INTEGER DEFAULT 0,
            forks INTEGER DEFAULT 0,
            watchers INTEGER DEFAULT 0,
            open_issues INTEGER DEFAULT 0,
            default_branch TEXT DEFAULT 'main',
            pushed_at TEXT,
            created_at_gh TEXT,
            ai_summary TEXT DEFAULT '',
            ai_tags TEXT DEFAULT '[]',
            ai_platforms TEXT DEFAULT '[]',
            category_id TEXT,
            last_synced TEXT,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES github_categories(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ghr_full_name ON github_repos(full_name);
        CREATE INDEX IF NOT EXISTS idx_ghr_language ON github_repos(language);
        CREATE INDEX IF NOT EXISTS idx_ghr_stars ON github_repos(stars DESC);
        CREATE INDEX IF NOT EXISTS idx_ghr_category ON github_repos(category_id);

        -- GitHub Stars: Release 订阅
        CREATE TABLE IF NOT EXISTS github_subscriptions (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            full_name TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gsub_item ON github_subscriptions(item_id);

        -- GitHub Stars: Release 记录
        CREATE TABLE IF NOT EXISTS github_releases (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            full_name TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            name TEXT DEFAULT '',
            body TEXT DEFAULT '',
            html_url TEXT DEFAULT '',
            published_at TEXT,
            is_prerelease INTEGER DEFAULT 0,
            assets TEXT DEFAULT '[]',
            is_read INTEGER DEFAULT 0,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_gr_item ON github_releases(item_id);
        CREATE INDEX IF NOT EXISTS idx_gr_published ON github_releases(published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_gr_unread ON github_releases(is_read, published_at DESC);

        CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
        CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks(item_id);
        
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            id UNINDEXED, title, summary, content, tags,
            tokenize='unicode61 remove_diacritics 1'
        );
        
        DROP TRIGGER IF EXISTS items_ai;
        CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
            INSERT INTO items_fts(rowid, id, title, summary, content, tags) 
            VALUES (new.rowid, new.id, new.title, new.summary, new.content, new.tags);
        END;
        DROP TRIGGER IF EXISTS items_ad;
        CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
            DELETE FROM items_fts WHERE rowid = old.rowid;
        END;
        DROP TRIGGER IF EXISTS items_au;
        CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
            UPDATE items_fts SET id=new.id, title=new.title, summary=new.summary, content=new.content, tags=new.tags WHERE rowid=old.rowid;
        END;
    """)
    try:
        conn.execute("ALTER TABLE items ADD COLUMN space TEXT DEFAULT 'default'")
    except:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_space ON items(space)")
    except:
        pass
    try:
        conn.execute("ALTER TABLE chunks ADD COLUMN chunk_pos INTEGER DEFAULT 0")
    except:
        pass
    try:
        conn.execute("ALTER TABLE github_repos ADD COLUMN synced_by TEXT DEFAULT ''")
    except:
        pass

    # Digest config: GitHub Stars & Trending fields
    _digest_cols = [
        ("gh_stars_daily_enabled", "INTEGER DEFAULT 0"),
        ("gh_stars_daily_hour", "INTEGER DEFAULT 8"),
        ("gh_stars_weekly_enabled", "INTEGER DEFAULT 0"),
        ("gh_stars_weekly_hour", "INTEGER DEFAULT 9"),
        ("gh_stars_weekly_day", "INTEGER DEFAULT 1"),
        ("gh_trending_daily_enabled", "INTEGER DEFAULT 0"),
        ("gh_trending_daily_hour", "INTEGER DEFAULT 8"),
        ("gh_trending_weekly_enabled", "INTEGER DEFAULT 0"),
        ("gh_trending_weekly_hour", "INTEGER DEFAULT 9"),
        ("gh_trending_weekly_day", "INTEGER DEFAULT 1"),
        ("gh_trending_monthly_enabled", "INTEGER DEFAULT 0"),
        ("gh_trending_monthly_hour", "INTEGER DEFAULT 9"),
        ("gh_trending_monthly_day", "INTEGER DEFAULT 1"),
        ("last_gh_stars_daily", "TEXT"),
        ("last_gh_stars_weekly", "TEXT"),
        ("last_gh_trending_daily", "TEXT"),
        ("last_gh_trending_weekly", "TEXT"),
        ("last_gh_trending_monthly", "TEXT"),
    ]
    existing = {row[1] for row in conn.execute("PRAGMA table_info(digest_config)").fetchall()}
    for col, typedef in _digest_cols:
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE digest_config ADD COLUMN {col} {typedef}")
            except:
                pass
        
    fts_count = conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
    items_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if fts_count != items_count or fts_count == 0:
        conn.execute("DELETE FROM items_fts")
        conn.execute('''
            INSERT INTO items_fts(rowid, id, title, summary, content, tags)
            SELECT rowid, id, title, summary, content, tags FROM items
        ''')
        
    conn.commit()
    conn.close()

@contextmanager
def get_db_ctx():
    """Context manager that auto-commits on success, rolls back on error."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

init_db()
