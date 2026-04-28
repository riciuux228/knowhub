"""Pytest configuration and fixtures for LANDrop tests."""
import os
import sys
import json
import tempfile
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temp directory for each test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Patch config to use temp directory
    monkeypatch.setattr("backend.config.DATA_DIR", data_dir)
    monkeypatch.setattr("backend.config.CONFIG_FILE", data_dir / "config.json")
    return data_dir


@pytest.fixture
def sample_config():
    """Return a sample configuration dict."""
    return {
        "AI_API_KEY": "sk-test123",
        "AI_BASE_URL": "https://api.example.com",
        "AI_MODEL": "test-model",
        "SYSTEM_PASSWORD": "",
        "VISION_API_KEY": "",
        "VISION_BASE_URL": "",
        "VISION_MODEL": "",
    }


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)
