"""Tests for backend/config.py"""
import json
from backend.config import (
    load_config, save_config, get_safe_config,
    hash_password, verify_password,
    get_github_accounts, save_github_accounts,
    get_primary_github_token, get_all_github_tokens,
)


class TestLoadSaveConfig:
    def test_load_default_when_no_file(self, temp_data_dir):
        cfg = load_config()
        assert "AI_API_KEY" in cfg
        assert cfg["AI_API_KEY"] == "sk-xxx"  # default

    def test_save_and_load(self, temp_data_dir):
        cfg = {"AI_API_KEY": "sk-test", "AI_MODEL": "gpt-4"}
        save_config(cfg)
        loaded = load_config()
        assert loaded["AI_API_KEY"] == "sk-test"
        assert loaded["AI_MODEL"] == "gpt-4"

    def test_load_corrupt_json(self, temp_data_dir):
        config_file = temp_data_dir / "config.json"
        config_file.write_text("not valid json{{{")
        cfg = load_config()
        # Should return defaults on corrupt file
        assert "AI_API_KEY" in cfg


class TestPasswordSecurity:
    def test_hash_password_deterministic(self):
        h1 = hash_password("mypassword")
        h2 = hash_password("mypassword")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_password_different_inputs(self):
        h1 = hash_password("pass1")
        h2 = hash_password("pass2")
        assert h1 != h2

    def test_verify_password_with_hash(self, temp_data_dir):
        pwd_hash = hash_password("secret123")
        save_config({"SYSTEM_PASSWORD": pwd_hash})
        assert verify_password("secret123", pwd_hash) is True
        assert verify_password("wrong", pwd_hash) is False

    def test_verify_password_legacy_plaintext(self, temp_data_dir):
        save_config({"SYSTEM_PASSWORD": "plainpass"})
        assert verify_password("plainpass", "plainpass") is True
        assert verify_password("wrong", "plainpass") is False

    def test_verify_password_empty_stored(self):
        assert verify_password("anything", "") is False

    def test_auto_upgrade_plaintext(self, temp_data_dir):
        save_config({"SYSTEM_PASSWORD": "oldplain"})
        verify_password("oldplain", "oldplain")
        cfg = load_config()
        # Should have been upgraded to hash
        assert len(cfg["SYSTEM_PASSWORD"]) == 64


class TestSafeConfig:
    def test_masks_sensitive_keys(self, temp_data_dir):
        save_config({
            "AI_API_KEY": "sk-secret123",
            "SYSTEM_PASSWORD": "mypassword",
            "VISION_API_KEY": "vk-secret",
            "AI_MODEL": "gpt-4",
        })
        safe = get_safe_config()
        assert safe["AI_API_KEY"] == "***"
        assert safe["SYSTEM_PASSWORD"] == "***"
        assert safe["VISION_API_KEY"] == "***"
        assert safe["AI_MODEL"] == "gpt-4"  # not sensitive

    def test_masks_github_tokens(self, temp_data_dir):
        save_config({
            "GITHUB_ACCOUNTS": [
                {"token": "ghp_abcdefghij", "login": "user1", "enabled": True}
            ]
        })
        safe = get_safe_config()
        assert "***" in safe["GITHUB_ACCOUNTS"][0]["token"]
        assert safe["GITHUB_ACCOUNTS"][0]["login"] == "user1"


class TestGitHubAccounts:
    def test_empty_by_default(self, temp_data_dir):
        save_config({})
        accounts = get_github_accounts()
        assert accounts == []

    def test_migrate_legacy_token(self, temp_data_dir):
        save_config({"GITHUB_TOKEN": "ghp_old_token"})
        accounts = get_github_accounts()
        assert len(accounts) == 1
        assert accounts[0]["token"] == "ghp_old_token"
        assert accounts[0]["label"] == "主号"
        # Legacy key should be removed
        cfg = load_config()
        assert "GITHUB_TOKEN" not in cfg

    def test_save_and_get_accounts(self, temp_data_dir):
        save_config({})
        accounts = [
            {"token": "ghp_aaa", "login": "user1", "enabled": True},
            {"token": "ghp_bbb", "login": "user2", "enabled": False},
        ]
        save_github_accounts(accounts)
        result = get_github_accounts()
        assert len(result) == 2
        assert result[0]["login"] == "user1"

    def test_get_primary_token(self, temp_data_dir):
        save_config({"GITHUB_ACCOUNTS": [
            {"token": "ghp_first", "enabled": True},
            {"token": "ghp_second", "enabled": True},
        ]})
        assert get_primary_github_token() == "ghp_first"

    def test_get_primary_token_skips_disabled(self, temp_data_dir):
        save_config({"GITHUB_ACCOUNTS": [
            {"token": "ghp_first", "enabled": False},
            {"token": "ghp_second", "enabled": True},
        ]})
        assert get_primary_github_token() == "ghp_second"

    def test_get_all_enabled_tokens(self, temp_data_dir):
        save_config({"GITHUB_ACCOUNTS": [
            {"token": "ghp_a", "enabled": True},
            {"token": "ghp_b", "enabled": False},
            {"token": "ghp_c", "enabled": True},
        ]})
        tokens = get_all_github_tokens()
        assert tokens == ["ghp_a", "ghp_c"]
