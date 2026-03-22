"""Tests for secret key validation and DB file permissions."""

import os
import tempfile

import pytest


class TestSecretKeyValidation:
    """Settings must refuse to start without an explicit CLEANER_SECRET_KEY."""

    def test_missing_secret_key_raises(self, monkeypatch):
        monkeypatch.delenv("CLEANER_SECRET_KEY", raising=False)
        # Point env_file at a nonexistent file so pydantic won't read .env
        monkeypatch.setenv("CLEANER_ENV_FILE", "/dev/null")

        from pydantic import ValidationError

        # Re-import to get fresh Settings class (not cached singleton)
        import importlib
        import backend.config as config_mod

        # Patch env_file to /dev/null so the real .env doesn't supply the key
        original_config = config_mod.Settings.model_config
        monkeypatch.setattr(
            config_mod.Settings,
            "model_config",
            {**original_config, "env_file": "/tmp/nonexistent.env"},
        )

        with pytest.raises(ValidationError, match="secret_key"):
            config_mod.Settings()

    def test_explicit_secret_key_accepted(self, monkeypatch):
        monkeypatch.setenv("CLEANER_SECRET_KEY", "test-key-abc123")

        import importlib
        import backend.config as config_mod

        s = config_mod.Settings()
        assert s.secret_key == "test-key-abc123"


class TestDatabasePermissions:
    """init_db must set the DB file to owner-only (600)."""

    @pytest.mark.asyncio
    async def test_db_file_permissions_are_600(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test_cleaner.db")
        monkeypatch.setattr("backend.database.DB_PATH", db_path)

        from backend.database import init_db

        await init_db()

        mode = oct(os.stat(db_path).st_mode & 0o777)
        assert mode == "0o600", f"Expected 0o600, got {mode}"
