"""Tests for KIS endpoint catalog."""

from __future__ import annotations

import pytest

from data.adapters.kis.endpoints import (
    BUY_TR_ID,
    SELL_TR_ID,
    get_endpoint,
    is_order_endpoint,
    KisCategory,
)


class TestEndpointCatalog:
    """Verify endpoint registry is correct."""

    def test_get_token_endpoint(self):
        ep = get_endpoint("token_issue")
        assert ep.category == KisCategory.OAUTH
        assert ep.method == "POST"
        assert ep.path == "/oauth2/tokenP"

    def test_get_balance_endpoint(self):
        ep = get_endpoint("inquire_balance")
        assert ep.category == KisCategory.TRADING
        assert ep.method == "GET"
        assert ep.tr_id == "TTTC8434R"

    def test_get_price_endpoint(self):
        ep = get_endpoint("inquire_price")
        assert ep.category == KisCategory.QUOTATION
        assert ep.tr_id == "FHKST01010100"

    def test_get_chart_endpoint(self):
        ep = get_endpoint("inquire_daily_chart")
        assert ep.category == KisCategory.QUOTATION
        assert ep.tr_id == "FHKST03010100"

    def test_get_minute_chart_endpoint(self):
        ep = get_endpoint("inquire_time_chart")
        assert ep.category == KisCategory.QUOTATION
        assert ep.tr_id == "FHKST03010200"
        assert ep.method == "GET"
        assert "inquire-time-itemchartprice" in ep.path
        assert ep.is_order is False

    def test_order_endpoint_flagged(self):
        ep = get_endpoint("order_cash")
        assert ep.is_order is True

    def test_non_order_endpoints_not_flagged(self):
        ep = get_endpoint("inquire_balance")
        assert ep.is_order is False

    def test_is_order_endpoint_check(self):
        assert is_order_endpoint("order_cash") is True
        assert is_order_endpoint("inquire_balance") is False

    def test_unknown_endpoint_raises(self):
        with pytest.raises(KeyError, match="not found"):
            get_endpoint("nonexistent_endpoint")

    def test_buy_sell_tr_ids(self):
        assert BUY_TR_ID == "TTTC0802U"
        assert SELL_TR_ID == "TTTC0801U"
        assert BUY_TR_ID != SELL_TR_ID

    # ── P1-3: 외국인/기관/재무 엔드포인트 ──

    def test_investor_endpoint(self):
        """투자자 매매동향 엔드포인트 등록 확인."""
        ep = get_endpoint("inquire_investor")
        assert ep.tr_id == "FHKST01010900"
        assert ep.category == KisCategory.QUOTATION
        assert "inquire-investor" in ep.path

    def test_financial_ratio_endpoint(self):
        """재무비율 엔드포인트 등록 확인."""
        ep = get_endpoint("inquire_financial_ratio")
        assert ep.tr_id == "FHKST66430300"
        assert ep.category == KisCategory.STOCK_INFO
        assert "financial-ratio" in ep.path
