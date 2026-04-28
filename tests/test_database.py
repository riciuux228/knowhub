"""Tests for backend/database.py"""
import sqlite3
from backend.database import get_db, get_db_ctx, init_db


class TestDatabaseConnection:
    def test_get_db_returns_connection(self, temp_data_dir, monkeypatch):
        monkeypatch.setattr("backend.database.DB_PATH", str(temp_data_dir / "test.db"))
        init_db()
        conn = get_db()
        assert conn is not None
        # Should be able to query
        result = conn.execute("SELECT 1").fetchone()
        assert result[0] == 1
        conn.close()

    def test_get_db_ctx_auto_commits(self, temp_data_dir, monkeypatch):
        monkeypatch.setattr("backend.database.DB_PATH", str(temp_data_dir / "test.db"))
        init_db()
        with get_db_ctx() as conn:
            conn.execute("INSERT INTO spaces (id, name, context_prompt) VALUES (?, ?, ?)",
                         ("test", "Test Space", ""))
        # Verify it was committed
        conn2 = get_db()
        row = conn2.execute("SELECT * FROM spaces WHERE id='test'").fetchone()
        assert row is not None
        conn2.close()

    def test_get_db_ctx_rolls_back_on_error(self, temp_data_dir, monkeypatch):
        monkeypatch.setattr("backend.database.DB_PATH", str(temp_data_dir / "test.db"))
        init_db()
        try:
            with get_db_ctx() as conn:
                conn.execute("INSERT INTO spaces (id, name, context_prompt) VALUES (?, ?, ?)",
                             ("rb_test", "Rollback", ""))
                raise ValueError("force rollback")
        except ValueError:
            pass
        # Should have been rolled back
        conn2 = get_db()
        row = conn2.execute("SELECT * FROM spaces WHERE id='rb_test'").fetchone()
        assert row is None
        conn2.close()

    def test_schema_has_required_tables(self, temp_data_dir, monkeypatch):
        monkeypatch.setattr("backend.database.DB_PATH", str(temp_data_dir / "test.db"))
        init_db()
        conn = get_db()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        required = {"items", "embeddings", "chunks", "content_hashes",
                    "spaces", "collections", "collection_items",
                    "github_repos", "github_categories", "github_releases",
                    "github_subscriptions", "reminders", "digest_config"}
        assert required.issubset(tables), f"Missing tables: {required - tables}"
