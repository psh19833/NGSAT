"""Tests for NGSAT core types and config."""

from __future__ import annotations

import pytest

from core.types import (
    DecisionAction,
    DecisionReason,
    Market,
    MarketRegime,
    OrderSide,
)


class TestDecisionReason:
    """DecisionReason is the cornerstone of NGSAT — no decision without a reason."""

    def test_valid_decision_reason(self):
        """A decision with a reason is valid."""
        reason = DecisionReason(
            action=DecisionAction.BUY,
            reason="레짐 강세 + RSI 과매도 + ML 상승확률 78%",
            evidence={"regime": "bull", "rsi": 28.5, "ml_prob": 0.78},
        )
        assert reason.action == DecisionAction.BUY
        assert "레짐" in reason.reason
        assert reason.evidence["ml_prob"] == 0.78

    def test_empty_reason_raises_error(self):
        """An empty reason must raise ValueError — no exceptions."""
        with pytest.raises(ValueError, match="cannot be empty"):
            DecisionReason(
                action=DecisionAction.BUY,
                reason="",
            )

    def test_whitespace_only_reason_raises_error(self):
        """Whitespace-only reason is also invalid."""
        with pytest.raises(ValueError, match="cannot be empty"):
            DecisionReason(
                action=DecisionAction.SELL,
                reason="   ",
            )


class TestEnums:
    """Verify enum values are correct."""

    def test_market_regime_values(self):
        assert MarketRegime.BULL.value == "bull"
        assert MarketRegime.NEUTRAL.value == "neutral"
        assert MarketRegime.BEAR.value == "bear"

    def test_order_side_values(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_market_values(self):
        assert Market.KOSPI.value == "kospi"
        assert Market.KOSDAQ.value == "kosdaq"

    def test_decision_action_values(self):
        assert DecisionAction.BUY.value == "buy"
        assert DecisionAction.FORCE_SELL.value == "force_sell"
        assert DecisionAction.FORCE_HOLD.value == "force_hold"
        assert DecisionAction.STOP_LOSS.value == "stop_loss"
