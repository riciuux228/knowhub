"""Integration tests for API endpoints."""
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(temp_data_dir, monkeypatch):
    """Create a test client with no auth required."""
    monkeypatch.setattr("backend.config.DATA_DIR", temp_data_dir)
    monkeypatch.setattr("backend.config.CONFIG_FILE", temp_data_dir / "config.json")
    # Save empty config (no password = no auth)
    from backend.config import save_config
    save_config({
        "AI_API_KEY": "sk-test",
        "AI_BASE_URL": "https://api.example.com",
        "AI_MODEL": "test-model",
        "SYSTEM_PASSWORD": "",
    })
    monkeypatch.setattr("backend.database.DB_PATH", str(temp_data_dir / "test.db"))
    from backend.database import init_db
    init_db()

    from backend.main import app
    return TestClient(app)


class TestHealthEndpoints:
    def test_stats(self, app_client):
        r = app_client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert "total" in data
        assert "files" in data
        assert "texts" in data

    def test_spaces(self, app_client):
        r = app_client.get("/api/spaces")
        assert r.status_code == 200
        assert "spaces" in r.json()

    def test_items_empty(self, app_client):
        r = app_client.get("/api/items")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestSettingsEndpoints:
    def test_get_settings_masks_secrets(self, app_client):
        r = app_client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert data["AI_API_KEY"] == "***"

    def test_login_wrong_password(self, app_client):
        from backend.config import save_config, hash_password
        save_config({
            "SYSTEM_PASSWORD": hash_password("correct"),
        })
        r = app_client.post("/api/login", json={"password": "wrong"})
        assert r.status_code == 401

    def test_login_correct_password(self, app_client):
        from backend.config import load_config, save_config, hash_password, verify_password
        cfg = load_config()
        pwd = "correct"
        cfg["SYSTEM_PASSWORD"] = hash_password(pwd)
        save_config(cfg)
        # Verify the config was saved correctly
        loaded = load_config()
        assert verify_password(pwd, loaded["SYSTEM_PASSWORD"]), "Password verification should succeed"
        r = app_client.post("/api/login", json={"password": pwd})
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestCollectionEndpoints:
    def test_crud_collection(self, app_client):
        # Create
        r = app_client.post("/api/collections", json={"name": "Test Col", "icon": "📁"})
        assert r.status_code == 200
        cid = r.json()["id"]

        # List
        r = app_client.get("/api/collections")
        assert r.status_code == 200
        assert len(r.json()["collections"]) == 1
        assert r.json()["collections"][0]["name"] == "Test Col"

        # Update
        r = app_client.put(f"/api/collections/{cid}", json={"name": "Updated", "icon": "⭐"})
        assert r.status_code == 200

        # Delete
        r = app_client.delete(f"/api/collections/{cid}")
        assert r.status_code == 200

        # Verify deleted
        r = app_client.get("/api/collections")
        assert len(r.json()["collections"]) == 0


class TestReminderEndpoints:
    def test_list_reminders_empty(self, app_client):
        r = app_client.get("/api/reminders")
        assert r.status_code == 200
        assert r.json()["reminders"] == []


class TestDigestEndpoints:
    def test_get_digest_config(self, app_client):
        r = app_client.get("/api/digest/config")
        assert r.status_code == 200
        data = r.json()
        assert "daily_enabled" in data

    def test_update_digest_config(self, app_client):
        r = app_client.post("/api/digest/config", json={
            "daily_enabled": 1,
            "daily_hour": 9,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestWebhookAuth:
    def test_webhook_rejected_without_token(self, app_client, temp_data_dir):
        from backend.config import save_config
        cfg = {"WEBHOOK_TOKEN": "secret123", "SYSTEM_PASSWORD": ""}
        save_config(cfg)
        r = app_client.post("/api/webhook/openilink",
                           json={"content": "test"})
        assert r.status_code == 401

    def test_webhook_accepted_with_token(self, app_client, temp_data_dir):
        from backend.config import save_config
        cfg = {"WEBHOOK_TOKEN": "secret123", "SYSTEM_PASSWORD": ""}
        save_config(cfg)
        r = app_client.post("/api/webhook/openilink?token=secret123",
                           json={"content": "hello world"})
        assert r.status_code == 200


class TestRateLimit:
    def test_ask_rate_limit(self, app_client, temp_data_dir):
        """Rate limiter should block after 20 requests in 60s."""
        from backend.config import save_config
        save_config({"SYSTEM_PASSWORD": ""})
        # Make 20 requests (the limit)
        for _ in range(20):
            app_client.post("/api/ask", json={"question": "test"})
        # 21st should be rate limited
        r = app_client.post("/api/ask", json={"question": "test"})
        assert r.status_code == 429


class TestGraphEndpoint:
    def test_graph_empty(self, app_client):
        r = app_client.get("/api/graph")
        assert r.status_code == 200
        data = r.json()
        assert data["nodes"] == []
        assert data["links"] == []


class TestGalleryEndpoint:
    def test_gallery_empty(self, app_client):
        r = app_client.get("/api/gallery")
        assert r.status_code == 200
        data = r.json()
        assert data["items"] == []
        assert data["total"] == 0
