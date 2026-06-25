"""Tests for NGSAT risk management."""

from __future__ import annotations

import pytest

from core.config import RiskConfig
from core.types import AccountSummary, DecisionAction, Position, Market
from live.risk import RiskManager


@pytest.fixture
def risk_config():
    return RiskConfig(
        daily_loss_limit_pct=5.0,
        default_stop_loss_pct=3.0,
        max_stop_loss_pct=5.0,
    )


@pytest.fixture
def risk_manager(risk_config):
    return RiskManager(risk_config)


@pytest.fixture
def safe_account():
    return AccountSummary(
        total_asset=10_000_000,
        deposit=5_000_000,
        total_eval=5_000_000,
        total_profit_loss=0,
        total_profit_loss_pct=0,
        daily_loss=200_000,
        daily_loss_pct=2.0,
        is_trading_halted=False,
    )


@pytest.fixture
def halted_account():
    return AccountSummary(
        total_asset=10_000_000,
        deposit=5_000_000,
        total_eval=5_000_000,
        total_profit_loss=-500_000,
        total_profit_loss_pct=-5.0,
        daily_loss=500_000,
        daily_loss_pct=5.0,
        is_trading_halted=False,
    )


@pytest.fixture
def profitable_position():
    return Position(
        code="005930",
        name="삼성전자",
        quantity=10,
        buy_price=70000,
        current_price=71000,
        market=Market.KOSPI,
        buy_amount=700000,
        eval_amount=710000,
        profit_loss=10000,
        profit_loss_pct=1.43,
        stop_loss_pct=3.0,
    )


@pytest.fixture
def losing_position():
    return Position(
        code="005930",
        name="삼성전자",
        quantity=10,
        buy_price=70000,
        current_price=67500,
        market=Market.KOSPI,
        buy_amount=700000,
        eval_amount=675000,
        profit_loss=-25000,
        profit_loss_pct=-3.57,
        stop_loss_pct=3.0,
    )


class TestDailyLossCheck:
    """Daily loss limit enforcement."""

    def test_safe_daily_loss(self, risk_manager, safe_account):
        """Loss under limit should allow continued trading."""
        result = risk_manager.check_daily_loss(safe_account)
        assert result.is_safe is True
        assert result.halt_trading is False

    def test_daily_loss_limit_reached(self, risk_manager, halted_account):
        """Loss at limit should halt trading."""
        result = risk_manager.check_daily_loss(halted_account)
        assert result.is_safe is False
        assert result.halt_trading is True
        assert risk_manager.is_halted is True
        assert "5.0%" in result.reason

    def test_halt_can_be_reset(self, risk_manager, halted_account):
        """Halt should be resettable for new trading day."""
        risk_manager.check_daily_loss(halted_account)
        assert risk_manager.is_halted is True
        risk_manager.reset_halt()
        assert risk_manager.is_halted is False


class TestStopLoss:
    """Per-position stop loss enforcement."""

    def test_profitable_position_no_stop_loss(self, risk_manager, profitable_position):
        """Profitable position should not trigger stop loss."""
        result = risk_manager.check_stop_loss(profitable_position)
        assert result.is_safe is True
        assert result.action == DecisionAction.NONE

    def test_losing_position_triggers_stop_loss(self, risk_manager, losing_position):
        """Position at loss beyond stop loss should trigger sell."""
        result = risk_manager.check_stop_loss(losing_position)
        assert result.is_safe is False
        assert result.action == DecisionAction.STOP_LOSS
        assert "손절선" in result.reason


class TestStopLossExtension:
    """Dynamic stop loss extension rules."""

    def test_extend_with_reason(self, risk_manager, losing_position):
        """Stop loss can be extended if a reason is provided."""
        can_extend, msg = risk_manager.can_extend_stop_loss(
            position=losing_position,
            new_stop_loss_pct=4.5,
            reason="MA20 지지선 확인, RSI 과매도 진입",
        )
        assert can_extend is True
        assert "승인" in msg

    def test_extend_without_reason_rejected(self, risk_manager, losing_position):
        """Stop loss extension WITHOUT reason must be rejected."""
        can_extend, msg = risk_manager.can_extend_stop_loss(
            position=losing_position,
            new_stop_loss_pct=4.5,
            reason="",
        )
        assert can_extend is False
        assert "사유 없음" in msg

    def test_extend_beyond_max_rejected(self, risk_manager, losing_position):
        """Stop loss beyond max (5%) must be rejected."""
        can_extend, msg = risk_manager.can_extend_stop_loss(
            position=losing_position,
            new_stop_loss_pct=6.0,
            reason="some reason",
        )
        assert can_extend is False
        assert "최대" in msg

    def test_extend_same_value_rejected(self, risk_manager, losing_position):
        """Extending to same value is not an extension."""
        can_extend, msg = risk_manager.can_extend_stop_loss(
            position=losing_position,
            new_stop_loss_pct=3.0,
            reason="some reason",
        )
        assert can_extend is False
        assert "연장 아님" in msg
