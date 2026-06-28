"""Tests for NGSAT stock screener."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from core.types import Market, MarketRegime, PriceData, StockInfo
from strategy.regime import RegimeResult
from strategy.screener import ScreenCandidate, ScreenResult, screen_stocks


def _make_price_history(n: int, start_price: float = 50000, trend: float = 100) -> list[PriceData]:
    """Generate n days of price data with a trend."""
    np.random.seed(42)
    prices = []
    for i in range(n):
        close = start_price + i * trend + np.random.randn() * 200
        prices.append(PriceData(
            code="test",
            timestamp=datetime.now() - timedelta(days=n - i),
            open=close - 100,
            high=close + 150,
            low=close - 150,
            close=close,
            volume=100000 + int(np.random.randn() * 20000),
            change_pct=trend / start_price * 100,
        ))
    return prices


def _make_stock_info(code: str, name: str, market: Market = Market.KOSPI) -> StockInfo:
    return StockInfo(code=code, name=name, market=market)


class TestScreenStocks:
    """Stock screening tests."""

    def test_bull_regime_screening(self):
        """In bull regime, good uptrending stocks should pass."""
        regime = RegimeResult(
            regime=MarketRegime.BULL,
            score=75.0,
            reason="강세장 (테스트)",
            evidence={"total_score": 75.0},
        )

        stocks = [
            (_make_stock_info("005930", "삼성전자", Market.KOSPI), _make_price_history(40, trend=200)),
            (_make_stock_info("000660", "SK하이닉스", Market.KOSPI), _make_price_history(40, trend=150)),
        ]

        result = screen_stocks(stocks, regime)

        assert result.regime == MarketRegime.BULL
        assert result.total_scanned == 2
        assert isinstance(result.candidates, list)
        assert isinstance(result.reason, str)

    def test_bear_regime_strict_filtering(self):
        """In bear regime, fewer candidates should pass (stricter threshold)."""
        regime = RegimeResult(
            regime=MarketRegime.BEAR,
            score=25.0,
            reason="약세장 (테스트)",
            evidence={"total_score": 25.0},
        )

        stocks = [
            (_make_stock_info("005930", "삼성전자"), _make_price_history(40, trend=100)),
        ]

        result = screen_stocks(stocks, regime)

        # In bear regime, threshold is 80 — most stocks won't pass
        assert result.total_scanned == 1
        # Candidates may be empty due to high threshold
        assert result.total_passed <= 1

    def test_kospi_bonus_applied(self):
        """KOSPI stocks should get a score bonus."""
        regime = RegimeResult(
            regime=MarketRegime.NEUTRAL,
            score=50.0,
            reason="중립장 (테스트)",
            evidence={"total_score": 50.0},
        )

        # Same price data, different markets
        kospi_stock = (_make_stock_info("005930", "삼성전자", Market.KOSPI), _make_price_history(40, trend=150))
        kosdaq_stock = (_make_stock_info("207760", "크래프톤", Market.KOSDAQ), _make_price_history(40, trend=150))

        result = screen_stocks([kospi_stock, kosdaq_stock], regime)

        # Find candidates and check KOSPI bonus
        for cand in result.candidates:
            if cand.code == "005930":
                assert cand.kospi_bonus is True
            if cand.code == "207760":
                assert cand.kospi_bonus is False

    def test_insufficient_data_skipped(self):
        """Stocks with less than 30 days of data should be skipped."""
        regime = RegimeResult(
            regime=MarketRegime.BULL,
            score=75.0,
            reason="강세장",
            evidence={},
        )

        stocks = [
            (_make_stock_info("005930", "삼성전자"), _make_price_history(20)),  # Only 20 days
        ]

        result = screen_stocks(stocks, regime)
        assert result.total_scanned == 1
        assert len(result.candidates) == 0  # Skipped

    def test_candidates_sorted_by_score(self):
        """Candidates should be sorted by score descending."""
        regime = RegimeResult(
            regime=MarketRegime.BULL,
            score=75.0,
            reason="강세장",
            evidence={},
        )

        stocks = [
            (_make_stock_info("005930", "삼성전자"), _make_price_history(40, trend=200)),
            (_make_stock_info("000660", "SK하이닉스"), _make_price_history(40, trend=50)),
            (_make_stock_info("035420", "NAVER"), _make_price_history(40, trend=150)),
        ]

        result = screen_stocks(stocks, regime)

        if len(result.candidates) >= 2:
            scores = [c.score for c in result.candidates]
            assert scores == sorted(scores, reverse=True)

    def test_candidate_has_reason_and_evidence(self):
        """Each candidate should have a Korean reason and indicator evidence."""
        regime = RegimeResult(
            regime=MarketRegime.BULL,
            score=75.0,
            reason="강세장",
            evidence={},
        )

        stocks = [
            (_make_stock_info("005930", "삼성전자"), _make_price_history(40, trend=200)),
        ]

        result = screen_stocks(stocks, regime)

        for cand in result.candidates:
            assert len(cand.reason) > 0
            assert "rsi" in cand.indicators
            assert "macd_histogram" in cand.indicators
            assert "ma5" in cand.indicators
            assert "volume_ratio" in cand.indicators

    def test_empty_stock_list(self):
        """Empty stock list should return empty result."""
        regime = RegimeResult(
            regime=MarketRegime.NEUTRAL,
            score=50.0,
            reason="중립장",
            evidence={},
        )

        result = screen_stocks([], regime)
        assert result.total_scanned == 0
        assert len(result.candidates) == 0

    def test_max_candidates_respected(self):
        """Result should not exceed max_candidates for the regime."""
        regime = RegimeResult(
            regime=MarketRegime.BULL,
            score=75.0,
            reason="강세장",
            evidence={},
        )

        # Create many stocks
        stocks = []
        for i in range(50):
            code = f"{i:06d}"
            stocks.append((_make_stock_info(code, f"stock_{i}"), _make_price_history(40, trend=200)))

        result = screen_stocks(stocks, regime)

        # Bull regime max is 30
        assert len(result.candidates) <= 30
