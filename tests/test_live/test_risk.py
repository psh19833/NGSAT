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


class TestTrailingStop:
    """트레일링 스탑 (P1-1)."""

    @pytest.fixture
    def trailing_risk_manager(self, risk_config):
        from core.config import StrategyConfig
        sc = StrategyConfig()
        sc.trailing_stop_enabled = True
        sc.trailing_stop_atr_multiplier = 2.0
        sc.trailing_stop_activate_pct = 1.0
        return RiskManager(risk_config, strategy_config=sc)

    @pytest.fixture
    def trailing_disabled_manager(self, risk_config):
        from core.config import StrategyConfig
        sc = StrategyConfig()
        sc.trailing_stop_enabled = False
        return RiskManager(risk_config, strategy_config=sc)

    @pytest.fixture
    def profitable_pos(self):
        return Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=71000, market=Market.KOSPI,
            buy_amount=700000, eval_amount=710000,
            profit_loss=10000, profit_loss_pct=1.43,
            stop_loss_pct=3.0,
        )

    def test_disabled_returns_position_unchanged(self, trailing_disabled_manager, profitable_pos):
        """비활성시 position 변경 없음."""
        result = trailing_disabled_manager.update_trailing_stop(profitable_pos, 71000, atr_value=500)
        assert result.trailing_stop_price is None
        assert result.trailing_stop_high_water is None

    def test_activate_below_threshold_no_trail(self, trailing_risk_manager, profitable_pos):
        """수익이 활성화 기준 미만이면 트레일링 스탑 미설정."""
        pos = Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=70050, market=Market.KOSPI,
            buy_amount=700000, eval_amount=700500,
            profit_loss=500, profit_loss_pct=0.07,
            stop_loss_pct=3.0,
        )
        result = trailing_risk_manager.update_trailing_stop(pos, 70050, atr_value=500)
        assert result.trailing_stop_price is None

    def test_high_water_updates_on_price_rise(self, trailing_risk_manager, profitable_pos):
        """가격 상승시 high_water 갱신."""
        # 1차: 71000원
        pos = trailing_risk_manager.update_trailing_stop(profitable_pos, 71000, atr_value=500)
        assert pos.trailing_stop_high_water == 71000
        assert pos.trailing_stop_price == 71000 - (500 * 2.0)

        # 2차: 72000원으로 상승
        from dataclasses import replace as dc_replace
        pos2_input = dc_replace(pos, current_price=72000, profit_loss_pct=2.86)
        pos2 = trailing_risk_manager.update_trailing_stop(pos2_input, 72000, atr_value=500)
        assert pos2.trailing_stop_high_water == 72000
        assert pos2.trailing_stop_price == 72000 - (500 * 2.0)

    def test_ratchet_does_not_lower_stop(self, trailing_risk_manager, profitable_pos):
        """가격 하락시 trailing_stop_price는 내려가지 않음 (ratchet)."""
        # 1차: 72000원에서 트레일링 스탑 설정
        from dataclasses import replace as dc_replace
        pos_high = dc_replace(profitable_pos, current_price=72000, profit_loss_pct=2.86)
        pos = trailing_risk_manager.update_trailing_stop(pos_high, 72000, atr_value=500)
        first_stop = pos.trailing_stop_price
        assert first_stop == 72000 - 1000  # 71000

        # 2차: 71500원으로 하락 — high_water는 72000 유지, stop도 유지
        pos_down = dc_replace(pos, current_price=71500)
        pos2 = trailing_risk_manager.update_trailing_stop(pos_down, 71500, atr_value=500)
        assert pos2.trailing_stop_price == first_stop  # 내려가지 않음
        assert pos2.trailing_stop_high_water == 72000  # 최고가 유지

    def test_trailing_stop_triggers_on_drop(self, trailing_risk_manager, profitable_pos):
        """현재가가 trailing_stop_price 이하면 청산 권고."""
        from dataclasses import replace as dc_replace
        # 트레일링 스탹 설정: 72000 - 1000 = 71000
        pos_high = dc_replace(profitable_pos, current_price=72000, profit_loss_pct=2.86)
        pos = trailing_risk_manager.update_trailing_stop(pos_high, 72000, atr_value=500)

        # 가격이 71000 이하로 하락
        pos_drop = dc_replace(pos, current_price=70900)
        result = trailing_risk_manager.check_trailing_stop(pos_drop)
        assert result.is_safe is False
        assert result.action == DecisionAction.STOP_LOSS
        assert "트레일링 스탑 트리거" in result.reason

    def test_trailing_stop_safe_above_stop(self, trailing_risk_manager, profitable_pos):
        """현재가가 trailing_stop_price 초과면 유지."""
        from dataclasses import replace as dc_replace
        pos = trailing_risk_manager.update_trailing_stop(profitable_pos, 71000, atr_value=500)
        pos_safe = dc_replace(pos, current_price=71500)
        result = trailing_risk_manager.check_trailing_stop(pos_safe)
        assert result.is_safe is True
        assert result.action == DecisionAction.NONE

    def test_no_atr_skips_trailing(self, trailing_risk_manager, profitable_pos):
        """ATR 데이터 없으면 트레일링 스탑 스킵."""
        result = trailing_risk_manager.update_trailing_stop(profitable_pos, 71000, atr_value=None)
        assert result.trailing_stop_price is None

    def test_check_disabled_returns_safe(self, trailing_disabled_manager, profitable_pos):
        """비활성시 check_trailing_stop도 safe 반환."""
        result = trailing_disabled_manager.check_trailing_stop(profitable_pos)
        assert result.is_safe is True


class TestPartialTakeProfit:
    """부분 청산 (P1-2)."""

    @pytest.fixture
    def partial_risk_manager(self, risk_config):
        from core.config import StrategyConfig
        sc = StrategyConfig()
        sc.partial_tp_enabled = True
        sc.partial_tp1_pct = 3.0
        sc.partial_tp1_ratio = 0.5
        sc.partial_tp2_pct = 6.0
        sc.partial_tp2_ratio = 0.3
        return RiskManager(risk_config, strategy_config=sc)

    @pytest.fixture
    def partial_disabled_manager(self, risk_config):
        from core.config import StrategyConfig
        sc = StrategyConfig()
        sc.partial_tp_enabled = False
        return RiskManager(risk_config, strategy_config=sc)

    @pytest.fixture
    def tp1_eligible_pos(self):
        """수익 +3.5% → 1차 익절 대상."""
        return Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=72450, market=Market.KOSPI,
            buy_amount=700000, eval_amount=724500,
            profit_loss=24500, profit_loss_pct=3.5,
            stop_loss_pct=3.0,
            original_quantity=10,
        )

    @pytest.fixture
    def tp2_eligible_pos(self):
        """수익 +6.5% → 2차 익절 대상 (1차 이미 실행)."""
        return Position(
            code="005930", name="삼성전자", quantity=5,
            buy_price=70000, current_price=74550, market=Market.KOSPI,
            buy_amount=700000, eval_amount=372750,
            profit_loss=22750, profit_loss_pct=6.5,
            stop_loss_pct=3.0,
            partial_tp1_executed=True,
            original_quantity=10,
        )

    def test_disabled_returns_no_sell(self, partial_disabled_manager, tp1_eligible_pos):
        """비활성시 매도 없음."""
        result = partial_disabled_manager.check_partial_take_profit(tp1_eligible_pos)
        assert result["should_sell"] is False
        assert result["sell_quantity"] == 0

    def test_tp1_triggers_at_threshold(self, partial_risk_manager, tp1_eligible_pos):
        """수익 +3.5% → 1차 익절 (5주 매도)."""
        result = partial_risk_manager.check_partial_take_profit(tp1_eligible_pos)
        assert result["should_sell"] is True
        assert result["tp_stage"] == 1
        assert result["sell_quantity"] == 5  # 10 × 0.5

    def test_tp2_triggers_at_threshold(self, partial_risk_manager, tp2_eligible_pos):
        """수익 +6.5% + 1차 실행 → 2차 익절 (3주 매도, 원래 10의 30%)."""
        result = partial_risk_manager.check_partial_take_profit(tp2_eligible_pos)
        assert result["should_sell"] is True
        assert result["tp_stage"] == 2
        assert result["sell_quantity"] == 3  # 10 × 0.3

    def test_below_threshold_no_sell(self, partial_risk_manager):
        """수익 +1.5% → 익절 대기."""
        pos = Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=71050, market=Market.KOSPI,
            buy_amount=700000, eval_amount=710500,
            profit_loss=10500, profit_loss_pct=1.5,
            stop_loss_pct=3.0, original_quantity=10,
        )
        result = partial_risk_manager.check_partial_take_profit(pos)
        assert result["should_sell"] is False
        assert "대기" in result["reason"]

    def test_already_executed_no_repeat(self, partial_risk_manager, tp1_eligible_pos):
        """1차 이미 실행 + 수익 +3.5% → 1차 재실행 안 됨."""
        from dataclasses import replace as dc_replace
        pos = dc_replace(tp1_eligible_pos, partial_tp1_executed=True)
        result = partial_risk_manager.check_partial_take_profit(pos)
        # 수익 3.5%는 2차 기준(6%) 미만이므로 should_sell=False
        assert result["should_sell"] is False

    def test_min_1_share_guaranteed(self, partial_risk_manager):
        """1주 × 50% = 0.5 → 최소 1주 보장."""
        pos = Position(
            code="005930", name="삼성전자", quantity=1,
            buy_price=70000, current_price=72500, market=Market.KOSPI,
            buy_amount=70000, eval_amount=72500,
            profit_loss=2500, profit_loss_pct=3.57,
            stop_loss_pct=3.0, original_quantity=1,
        )
        result = partial_risk_manager.check_partial_take_profit(pos)
        assert result["should_sell"] is True
        assert result["sell_quantity"] >= 1  # 최소 1주
