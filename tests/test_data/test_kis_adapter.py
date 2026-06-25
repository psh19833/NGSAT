"""Tests for KIS adapter account normalization and config validation."""

from __future__ import annotations

import pytest

from core.exceptions import ConfigError
from data.adapters.kis.adapter import KisAdapter


class TestKisAdapterConfig:
    """Adapter initialization and configuration validation."""

    def test_missing_app_key_raises(self):
        with pytest.raises(ConfigError, match="app_key"):
            KisAdapter(
                app_key="",
                app_secret="secret",
                base_url="https://example.com",
                account_no="12345678",
                account_product_code="01",
            )

    def test_missing_app_secret_raises(self):
        with pytest.raises(ConfigError, match="app_key"):
            KisAdapter(
                app_key="key",
                app_secret="",
                base_url="https://example.com",
                account_no="12345678",
                account_product_code="01",
            )

    def test_account_no_with_hyphen_normalized(self):
        """Account number '12345678-01' should extract '12345678'."""
        adapter = KisAdapter(
            app_key="key",
            app_secret="secret",
            base_url="https://example.com",
            account_no="12345678-01",
            account_product_code="01",
        )
        assert adapter._account_no == "12345678"

    def test_account_no_digits_only(self):
        adapter = KisAdapter(
            app_key="key",
            app_secret="secret",
            base_url="https://example.com",
            account_no="12345678",
            account_product_code="01",
        )
        assert adapter._account_no == "12345678"

    def test_invalid_account_no_length_raises(self):
        with pytest.raises(ConfigError, match="8 digits"):
            KisAdapter(
                app_key="key",
                app_secret="secret",
                base_url="https://example.com",
                account_no="123",
                account_product_code="01",
            )

    def test_default_product_code(self):
        adapter = KisAdapter(
            app_key="key",
            app_secret="secret",
            base_url="https://example.com",
            account_no="12345678",
            account_product_code="",  # empty → default "01"
        )
        assert adapter._account_product_code == "01"
