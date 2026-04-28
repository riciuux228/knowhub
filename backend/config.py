import os
import json
import hmac
import hashlib
import asyncio
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"

HOST = "0.0.0.0"
PORT = 8765

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, TypeError): pass
    return {
        "AI_API_KEY": os.getenv("AI_API_KEY", "sk-xxx"),
        "AI_BASE_URL": os.getenv("AI_BASE_URL", "https://api.deepseek.com"),
        "AI_MODEL": os.getenv("AI_MODEL", "deepseek-chat"),
        "SYSTEM_PASSWORD": "",
        "VISION_API_KEY": "",
        "VISION_BASE_URL": "",
        "VISION_MODEL": ""
    }

def save_config(data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def hash_password(password: str) -> str:
    """Hash password with SHA-256 for storage."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, stored: str) -> bool:
    """Verify password against stored value. Supports both plaintext and hashed."""
    if not stored:
        return False
    # If stored value looks like a SHA-256 hash (64 hex chars), compare hash
    if len(stored) == 64 and all(c in '0123456789abcdef' for c in stored):
        return hmac.compare_digest(hash_password(password), stored)
    # Legacy plaintext — compare constant-time, then auto-upgrade to hash
    if hmac.compare_digest(password, stored):
        cfg = load_config()
        cfg["SYSTEM_PASSWORD"] = hash_password(stored)
        save_config(cfg)
        return True
    return False

_SENSITIVE_KEYS = {"AI_API_KEY", "VISION_API_KEY", "SYSTEM_PASSWORD", "GITHUB_TOKEN"}

def get_safe_config() -> dict:
    """Return config with sensitive values masked."""
    cfg = load_config()
    safe = {}
    for k, v in cfg.items():
        if k == "GITHUB_ACCOUNTS" and isinstance(v, list):
            safe[k] = [
                {**a, "token": a.get("token", "")[:8] + "***" if a.get("token") else ""}
                for a in v
            ]
        elif k in _SENSITIVE_KEYS and v:
            safe[k] = "***"
        else:
            safe[k] = v
    return safe


def get_github_accounts() -> list[dict]:
    """Get all GitHub accounts. Auto-migrates legacy GITHUB_TOKEN."""
    cfg = load_config()
    accounts = cfg.get("GITHUB_ACCOUNTS", [])
    # Migrate legacy single token
    if not accounts and cfg.get("GITHUB_TOKEN"):
        accounts = [{
            "token": cfg["GITHUB_TOKEN"],
            "label": "主号",
            "login": "",
            "avatar_url": "",
            "enabled": True,
        }]
        cfg["GITHUB_ACCOUNTS"] = accounts
        cfg.pop("GITHUB_TOKEN", None)
        save_config(cfg)
    return accounts

def save_github_accounts(accounts: list[dict]):
    cfg = load_config()
    cfg["GITHUB_ACCOUNTS"] = accounts
    cfg.pop("GITHUB_TOKEN", None)
    save_config(cfg)

def get_primary_github_token() -> str:
    """Return the first enabled GitHub token."""
    for acc in get_github_accounts():
        if acc.get("enabled", True) and acc.get("token"):
            return acc["token"]
    return ""

def get_all_github_tokens() -> list[str]:
    """Return all enabled GitHub tokens."""
    return [acc["token"] for acc in get_github_accounts() if acc.get("enabled", True) and acc.get("token")]

class EventStream:
    def __init__(self):
        self.subscribers = set()
        self.running = True
        
    async def publish(self, message: str):
        if not self.running: return
        for q in list(self.subscribers):
            try:
                await q.put(message)
            except asyncio.QueueFull:
                pass
                
    async def shutdown(self):
        self.running = False
        for q in list(self.subscribers):
            try:
                await q.put(None)
            except asyncio.QueueFull:
                pass
                
    async def subscribe(self):
        q = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        try:
            while self.running:
                msg = await q.get()
                if msg is None:
                    break
                yield f"data: {msg}\n\n"
        finally:
            self.subscribers.discard(q)

events = EventStream()
