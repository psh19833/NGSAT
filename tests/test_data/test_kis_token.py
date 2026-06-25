"""Tests for KIS token manager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from data.adapters.kis.token_manager import KisToken, KisTokenManager


class TestKisToken:
    """KisToken data model tests."""

    def test_token_creation(self):
        token = KisToken(
            access_token="test_token_12345",
            token_type="Bearer",
            expires_in=86400,
            issued_at=datetime.now(timezone.utc),
        )
        assert token.token_type == "Bearer"
        assert token.expires_in == 86400

    def test_token_not_expired(self):
        token = KisToken(
            access_token="test",
            token_type="Bearer",
            expires_in=86400,
            issued_at=datetime.now(timezone.utc),
        )
        assert token.is_expired is False

    def test_token_expired(self):
        token = KisToken(
            access_token="test",
            token_type="Bearer",
            expires_in=60,
            issued_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        assert token.is_expired is True

    def test_token_repr_does_not_expose_plaintext(self):
        """Token plaintext must never appear in repr."""
        token = KisToken(
            access_token="SECRET_TOKEN_12345",
            token_type="Bearer",
            expires_in=86400,
            issued_at=datetime.now(timezone.utc),
        )
        repr_str = repr(token)
        assert "SECRET_TOKEN_12345" not in repr_str

    def test_authorization_header(self):
        token = KisToken(
            access_token="abc123",
            token_type="Bearer",
            expires_in=86400,
            issued_at=datetime.now(timezone.utc),
        )
        assert token.authorization_header == "Bearer abc123"


class TestKisTokenManager:
    """Token manager lifecycle tests."""

    def test_is_configured_with_credentials(self):
        mgr = KisTokenManager(
            app_key="key123",
            app_secret="secret456",
            base_url="https://openapi.koreainvestment.com:9443",
        )
        assert mgr.is_configured is True

    def test_not_configured_without_credentials(self):
        mgr = KisTokenManager(
            app_key="",
            app_secret="",
            base_url="https://openapi.koreainvestment.com:9443",
        )
        assert mgr.is_configured is False

    def test_clear_cache(self):
        mgr = KisTokenManager("key", "secret", "https://example.com")
        mgr.clear_cache()  # Should not raise
