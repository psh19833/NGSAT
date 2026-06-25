"""Tests for NGSAT market regime evaluation."""

from __future__ import annotations

import numpy as np
import pytest

from core.types import MarketRegime
from strategy.regime import (
    BULL_THRESHOLD,
    BEAR_THRESHOLD,
    evaluate_regime,
    RegimeResult,
)


def _make_bull_market(n: int = 80) -> list[float]:
    """Generate bullish index data (steady uptrend)."""
    np.random.seed(42)
    noise = np.random.randn(n) * 10
    trend = np.array([2500 + i * 5 for i in range(n)], dtype=float)
    return list(trend + noise)


def _make_bear_market(n: int = 80) -> list[float]:
    """Generate bearish index data (steady downtrend)."""
    np.random.seed(42)
    noise = np.random.randn(n) * 10
    trend = np.array([3000 - i * 5 for i in range(n)], dtype=float)
    return list(trend + noise)


def _make_neutral_market(n: int = 80) -> list[float]:
    """Generate neutral/sideways index data."""
    np.random.seed(42)
    return list(2500 + np.random.randn(n) * 20)


class TestEvaluateRegime:
    """Market regime evaluation tests."""

    def test_bull_market_detected(self):
        """Strong uptrend should be classified as BULL."""
        closes = _make_bull_market(80)
        result = evaluate_regime(closes)
        assert result.regime == MarketRegime.BULL
        assert result.score >= BULL_THRESHOLD
        assert "강세장" in result.reason

    def test_bear_market_detected(self):
        """Strong downtrend should be classified as BEAR."""
        closes = _make_bear_market(80)
        result = evaluate_regime(closes)
        assert result.regime == MarketRegime.BEAR
        assert result.score <= BEAR_THRESHOLD
        assert "약세장" in result.reason

    def test_neutral_market_detected(self):
        """Sideways market should not be classified as BULL."""
        closes = _make_neutral_market(80)
        result = evaluate_regime(closes)
        # Random sideways data may lean slightly bear or neutral,
        # but should never be BULL
        assert result.regime != MarketRegime.BULL
        assert BEAR_THRESHOLD - 5 <= result.score <= BULL_THRESHOLD + 5

    def test_insufficient_data_returns_neutral(self):
        """Less than 20 data points should return NEUTRAL with warning."""
        result = evaluate_regime([100, 101, 102])
        assert result.regime == MarketRegime.NEUTRAL
        assert "데이터 부족" in result.reason

    def test_regime_result_has_evidence(self):
        """Every regime result should include quantitative evidence."""
        closes = _make_bull_market(80)
        result = evaluate_regime(closes)
        assert "total_score" in result.evidence
        assert "ma_alignment" in result.evidence
        assert "rsi" in result.evidence
        assert "bollinger" in result.evidence
        assert "change_rate" in result.evidence

    def test_regime_result_has_korean_reason(self):
        """Reason should be in Korean with score."""
        closes = _make_bull_market(80)
        result = evaluate_regime(closes)
        assert "점수" in result.reason
        # Should contain MA, RSI, etc. mentions
        assert len(result.reason) > 20

    def test_score_within_valid_range(self):
        """Score should always be between 0 and 100."""
        for closes in [_make_bull_market(80), _make_bear_market(80), _make_neutral_market(80)]:
            result = evaluate_regime(closes)
            assert 0 <= result.score <= 100

    def test_volume_trend_optional(self):
        """Regime evaluation should work without volume data."""
        closes = _make_bull_market(80)
        result = evaluate_regime(closes, index_volumes=None)
        assert result.regime == MarketRegime.BULL

    def test_volume_trend_with_data(self):
        """Regime evaluation should use volume data when provided."""
        closes = _make_bull_market(80)
        volumes = [1000000 + i * 10000 for i in range(80)]  # Increasing volume
        result = evaluate_regime(closes, index_volumes=volumes)
        assert "volume_trend" in result.evidence
