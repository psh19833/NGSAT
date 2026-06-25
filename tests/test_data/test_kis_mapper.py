"""Tests for KIS response mapper."""

from __future__ import annotations

import pytest

from core.types import Market, OrderSide
from data.adapters.kis.mapper import (
    build_order_payload,
    parse_account_summary,
    parse_positions,
    parse_price,
    parse_price_history,
    parse_stock_info,
)


class TestParseAccountSummary:
    """Account summary parsing from KIS balance response."""

    def test_parse_valid_summary(self):
        raw = {
            "output2": [{
                "tot_evlu_amt": "10000000",
                "prvs_rcdl_excc_amt": "5000000",
                "evlu_tot_amt": "5000000",
                "evlu_tot_pl": "100000",
                "evlu_tot_pl_pct": "1.0",
            }],
            "output": [],
        }
        summary = parse_account_summary(raw)
        assert summary.total_asset == 10_000_000
        assert summary.deposit == 5_000_000
        assert summary.total_eval == 5_000_000
        assert summary.total_profit_loss == 100_000

    def test_parse_empty_response(self):
        summary = parse_account_summary({})
        assert summary.total_asset == 0.0
        assert summary.deposit == 0.0


class TestParsePositions:
    """Position parsing from KIS balance response."""

    def test_parse_multiple_positions(self):
        raw = {
            "output": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "10",
                    "pchs_avg_pric": "70000",
                    "prpr": "71000",
                    "evlu_amt": "710000",
                    "evlu_pl": "10000",
                    "evlu_pl_pct": "1.43",
                },
                {
                    "pdno": "000660",
                    "prdt_name": "SK하이닉스",
                    "hldg_qty": "5",
                    "pchs_avg_pric": "120000",
                    "prpr": "125000",
                    "evlu_amt": "625000",
                    "evlu_pl": "25000",
                    "evlu_pl_pct": "4.17",
                },
            ]
        }
        positions = parse_positions(raw)
        assert len(positions) == 2
        assert positions[0].code == "005930"
        assert positions[0].name == "삼성전자"
        assert positions[0].quantity == 10
        assert positions[1].code == "000660"

    def test_skip_zero_quantity(self):
        raw = {
            "output": [
                {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "0", "pchs_avg_pric": "70000", "prpr": "71000"},
            ]
        }
        positions = parse_positions(raw)
        assert len(positions) == 0

    def test_empty_output(self):
        positions = parse_positions({})
        assert len(positions) == 0


class TestParsePrice:
    """Price parsing from KIS inquire-price response."""

    def test_parse_valid_price(self):
        raw = {
            "stck_prpr": "71000",
            "stck_oprc": "70000",
            "stck_hgpr": "72000",
            "stck_lwpr": "69000",
            "acml_vol": "1000000",
            "prdy_ctrt": "1.43",
        }
        price = parse_price(raw, code="005930")
        assert price.code == "005930"
        assert price.close == 71000
        assert price.open == 70000
        assert price.high == 72000
        assert price.low == 69000
        assert price.volume == 1_000_000
        assert price.change_pct == 1.43

    def test_parse_empty_price(self):
        price = parse_price({}, code="005930")
        assert price.code == "005930"
        assert price.close == 0.0


class TestParsePriceHistory:
    """Price history parsing from KIS daily-chart response."""

    def test_parse_multiple_days(self):
        raw = {
            "output2": [
                {"stck_bsop_date": "20260620", "stck_oprc": "70000", "stck_hgpr": "72000", "stck_lwpr": "69000", "stck_clpr": "71000", "acml_vol": "1000000"},
                {"stck_bsop_date": "20260623", "stck_oprc": "71000", "stck_hgpr": "73000", "stck_lwpr": "70000", "stck_clpr": "72000", "acml_vol": "1200000"},
            ]
        }
        history = parse_price_history(raw, code="005930")
        assert len(history) == 2
        assert history[0].close == 71000
        assert history[1].close == 72000

    def test_empty_history(self):
        history = parse_price_history({}, code="005930")
        assert len(history) == 0


class TestParseStockInfo:
    """Stock info parsing."""

    def test_parse_kospi_stock(self):
        raw = {
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "mrkt_cls_nm": "KOSPI",
        }
        info = parse_stock_info(raw)
        assert info.code == "005930"
        assert info.name == "삼성전자"
        assert info.market == Market.KOSPI

    def test_parse_kosdaq_stock(self):
        raw = {
            "pdno": "207760",
            "prdt_name": "크래프톤",
            "mrkt_cls_nm": "KOSDAQ",
        }
        info = parse_stock_info(raw)
        assert info.code == "207760"
        assert info.market == Market.KOSDAQ


class TestBuildOrderPayload:
    """Order payload construction."""

    def test_market_order_buy(self):
        payload = build_order_payload(
            code="005930",
            side=OrderSide.BUY,
            quantity=10,
            account_no="12345678",
            account_product_code="01",
            price=None,  # market order
        )
        assert payload["PDNO"] == "005930"
        assert payload["ORD_DVSN"] == "01"  # market
        assert payload["ORD_QTY"] == "10"
        assert "ORD_UNPR" not in payload

    def test_limit_order_sell(self):
        payload = build_order_payload(
            code="005930",
            side=OrderSide.SELL,
            quantity=5,
            account_no="12345678",
            account_product_code="01",
            price=72000,
        )
        assert payload["ORD_DVSN"] == "00"  # limit
        assert payload["ORD_UNPR"] == "72000"
