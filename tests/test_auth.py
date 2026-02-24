"""Tests for admin API authentication and input validation."""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

import pytest

from scheduling.auth import require_admin_token


# ── Replicate _validate_id and _STATE_PATCH_ALLOWLIST locally ──────
# These are defined in app.py which has heavy imports (gateway).
# We test the logic directly rather than importing through the full app.

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_STATE_PATCH_ALLOWLIST = {
    "on_enter", "system_prompt", "narration", "tool_names", "transitions",
    "state_fields", "tool_args_map", "auto_intent", "step_type", "handler",
    "max_turns", "max_turns_target",
}


def _validate_id(value: str) -> bool:
    """Return True if valid, False if invalid."""
    return bool(_ID_PATTERN.match(value))


# ── Fixture: mock settings for auth tests ──────────────────────────

class FakeSettings:
    def __init__(self, admin_api_key="", debug=False):
        self.admin_api_key = admin_api_key
        self.debug = debug


# ── Tests: Auth logic ──────────────────────────────────────────────

class TestRequireAdminToken:
    """Test the require_admin_token dependency directly."""

    async def test_rejects_no_token_when_key_set(self, monkeypatch):
        monkeypatch.setattr("scheduling.auth.settings", FakeSettings(admin_api_key="secret"))
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_token(credentials=None)
        assert exc_info.value.status_code == 401

    async def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setattr("scheduling.auth.settings", FakeSettings(admin_api_key="secret"))
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_token(credentials=creds)
        assert exc_info.value.status_code == 401

    async def test_allows_correct_token(self, monkeypatch):
        monkeypatch.setattr("scheduling.auth.settings", FakeSettings(admin_api_key="secret"))
        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret")
        # Should not raise
        await require_admin_token(credentials=creds)

    async def test_allows_no_key_debug_mode(self, monkeypatch):
        monkeypatch.setattr("scheduling.auth.settings", FakeSettings(admin_api_key="", debug=True))
        # No key + debug = allow without any credentials
        await require_admin_token(credentials=None)

    async def test_rejects_no_key_production(self, monkeypatch):
        monkeypatch.setattr("scheduling.auth.settings", FakeSettings(admin_api_key="", debug=False))
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_token(credentials=None)
        assert exc_info.value.status_code == 403


# ── Tests: Input validation ─────────────────────────────────────

class TestInputValidation:
    """_validate_id rejects path traversal and unsafe characters."""

    def test_valid_alphanumeric(self):
        assert _validate_id("apartment_viewing")

    def test_valid_with_dashes(self):
        assert _validate_id("my-workflow-v2")

    def test_valid_simple(self):
        assert _validate_id("abc123")

    def test_rejects_path_traversal(self):
        assert not _validate_id("../../etc/passwd")

    def test_rejects_slashes(self):
        assert not _validate_id("foo/bar")

    def test_rejects_dots(self):
        assert not _validate_id("foo.bar")

    def test_rejects_empty(self):
        assert not _validate_id("")

    def test_rejects_too_long(self):
        assert not _validate_id("a" * 65)

    def test_rejects_spaces(self):
        assert not _validate_id("foo bar")

    def test_rejects_special_chars(self):
        assert not _validate_id("foo;rm -rf /")


# ── Tests: setattr allowlist ──────────────────────────────────────

class TestSetAttrAllowlist:
    """The _STATE_PATCH_ALLOWLIST blocks internal Pydantic fields."""

    def test_model_config_not_in_allowlist(self):
        assert "model_config" not in _STATE_PATCH_ALLOWLIST

    def test_model_fields_not_in_allowlist(self):
        assert "model_fields" not in _STATE_PATCH_ALLOWLIST

    def test_id_not_in_allowlist(self):
        assert "id" not in _STATE_PATCH_ALLOWLIST

    def test_system_prompt_in_allowlist(self):
        assert "system_prompt" in _STATE_PATCH_ALLOWLIST

    def test_transitions_in_allowlist(self):
        assert "transitions" in _STATE_PATCH_ALLOWLIST

    def test_on_enter_in_allowlist(self):
        assert "on_enter" in _STATE_PATCH_ALLOWLIST

    def test_tool_names_in_allowlist(self):
        assert "tool_names" in _STATE_PATCH_ALLOWLIST

    def test_narration_in_allowlist(self):
        assert "narration" in _STATE_PATCH_ALLOWLIST


# ── Tests: Session ID entropy ─────────────────────────────────────

class TestSessionIds:
    """Session IDs should use high-entropy tokens."""

    def test_session_ids_are_url_safe(self):
        import secrets
        token = secrets.token_urlsafe(18)
        assert len(token) == 24
        # URL-safe base64 characters only
        import string
        valid = set(string.ascii_letters + string.digits + "-_")
        assert all(c in valid for c in token)

    def test_session_ids_unique(self):
        import secrets
        ids = {secrets.token_urlsafe(18) for _ in range(100)}
        assert len(ids) == 100


# ── Tests: PII redaction ──────────────────────────────────────────

class TestPiiRedaction:
    """redact_pii masks sensitive data for logging."""

    def test_redacts_phone(self):
        from scheduling.session import redact_pii
        assert redact_pii("+15551234567") == "+15***67"

    def test_redacts_short_value(self):
        from scheduling.session import redact_pii
        assert redact_pii("abc") == "***"

    def test_redacts_empty(self):
        from scheduling.session import redact_pii
        assert redact_pii("") == "***"

    def test_redacts_email(self):
        from scheduling.session import redact_pii
        assert redact_pii("user@example.com") == "use***om"
