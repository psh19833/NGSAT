"""Tests for NGSAT live orchestrator."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from backtest.data_loader import generate_synthetic_data, generate_synthetic_index, generate_synthetic_universe
from core.config import RiskConfig
from core.types import AccountSummary, Market, Position, PriceData, StockInfo
from data.adapters.base import BrokerAdapter
from live.orchestrator import CycleResult, TradingOrchestrator
from ml.training.trainer import PriceRiseModel, train_from_price_data


class MockBroker(BrokerAdapter):
    """Mock broker for orchestrator testing."""

    def __init__(self, account=None, positions=None):
        self._account = account or AccountSummary(
            total_asset=10_000_000, deposit=10_000_000,
            total_eval=0, total_profit_loss=0, total_profit_loss_pct=0,
        )
        self._positions = positions or []
        self._order_counter = 0

    async def get_account_summary(self):
        return self._account

    async def get_positions(self):
        return self._positions

    async def get_price(self, code):
        return PriceData(code=code, timestamp=datetime.now(), open=70000, high=71000, low=69000, close=70500, volume=100000)

    async def get_price_history(self, code, start, end):
        return []

    async def get_stock_list(self):
        return []

    async def submit_order(self, code, side, quantity, price=None):
        self._order_counter += 1
        return f"MOCK_ORDER_{self._order_counter}"

    async def cancel_order(self, order_id):
        return True

    async def get_order_status(self, order_id):
        from core.types import OrderStatus
        return OrderStatus.FILLED

    async def is_market_open(self):
        return True

    async def close(self):
        pass


class OverheatedMinuteBroker(MockBroker):
    """분봉 과열 데이터를 반환하는 MockBroker — 진입 정밀화 보류 검증용."""

    async def get_minute_history(self, code, base_time=None, include_past=True):
        base = datetime(2026, 6, 25, 9, 0, 0)
        closes = [70000.0]
        for _ in range(24):
            closes.append(closes[-1] * 1.003)  # 지속 상승 → RSI 과열
        return [
            PriceData(code=code, timestamp=base + timedelta(minutes=i),
                      open=c, high=c * 1.001, low=c * 0.999, close=c, volume=1000)
            for i, c in enumerate(closes)
        ]


def _train_quick_model():
    """Train a small model for testing."""
    universe = generate_synthetic_universe(n_stocks=5, n_days=120, seed=77)
    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]
    model, _ = train_from_price_data(all_prices, codes, model_type="logistic")
    return model


class TestTradingOrchestrator:
    """Orchestrator tests."""

    @pytest.fixture
    def model(self):
        return _train_quick_model()

    @pytest.fixture
    def broker(self):
        return MockBroker()

    @pytest.fixture
    def orchestrator(self, model, broker):
        return TradingOrchestrator(broker, model)

    @pytest.fixture
    def index_prices(self):
        return generate_synthetic_index(n_days=100, seed=100)

    @pytest.fixture
    def universe(self):
        return generate_synthetic_universe(n_stocks=5, n_days=100, seed=42)

    @pytest.mark.asyncio
    async def test_cycle_when_not_running(self, orchestrator, index_prices, universe):
        """Cycle should skip when controller is not running."""
        result = await orchestrator.run_cycle(index_prices, universe)

        assert result.buys_executed == 0
        assert result.sells_executed == 0
        assert "대기" in result.reason

    @pytest.mark.asyncio
    async def test_cycle_when_running(self, orchestrator, index_prices, universe):
        """Cycle should run when controller is started."""
        orchestrator.controller.start()

        result = await orchestrator.run_cycle(index_prices, universe)

        assert isinstance(result, CycleResult)
        assert len(result.reason) > 0
        # May or may not execute trades depending on ML predictions
        assert result.buys_executed >= 0

    @pytest.mark.asyncio
    async def test_cycle_with_empty_data(self, orchestrator):
        """Cycle with empty data should handle gracefully."""
        orchestrator.controller.start()

        result = await orchestrator.run_cycle([], [])

        assert result.candidates_found == 0

    @pytest.mark.asyncio
    async def test_cycle_records_regime(self, orchestrator, index_prices, universe):
        """Cycle should evaluate and record regime."""
        orchestrator.controller.start()

        result = await orchestrator.run_cycle(index_prices, universe)

        # Regime should be one of the valid values
        assert result.regime in ["bull", "neutral", "bear"]

    @pytest.mark.asyncio
    async def test_cycle_reason_is_korean(self, orchestrator, index_prices, universe):
        """Cycle reason should be in Korean."""
        orchestrator.controller.start()

        result = await orchestrator.run_cycle(index_prices, universe)

        assert "사이클" in result.reason or "대기" in result.reason or "리스크" in result.reason

    @pytest.mark.asyncio
    async def test_controller_accessible(self, orchestrator):
        """Controller should be accessible for start/stop."""
        assert orchestrator.controller is not None
        assert orchestrator.controller.state.value == "idle"

    @pytest.mark.asyncio
    async def test_risk_manager_accessible(self, orchestrator):
        """Risk manager should be accessible."""
        assert orchestrator.risk_manager is not None
        assert orchestrator.risk_manager.is_halted is False

    @pytest.mark.asyncio
    async def test_force_sell_no_position(self, orchestrator):
        """Force sell on non-held stock should fail gracefully."""
        orchestrator.controller.start()

        result = await orchestrator.force_sell("005930", "삼성전자")

        assert result.success is False
        assert "보유하지" in result.error

    @pytest.mark.asyncio
    async def test_force_sell_with_position(self, model):
        """Force sell on held stock should execute."""
        position = Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=71000,
            market=Market.KOSPI, buy_amount=700000, eval_amount=710000,
            profit_loss=10000, profit_loss_pct=1.43, stop_loss_pct=3.0,
        )
        broker = MockBroker(positions=[position])
        orch = TradingOrchestrator(broker, model)
        orch.controller.start()

        result = await orch.force_sell("005930", "삼성전자")

        assert result.success is True
        assert result.action == "force_sell"

    @pytest.mark.asyncio
    async def test_risk_halt_stops_cycle(self, orchestrator, index_prices, universe):
        """Cycle should skip when risk is halted."""
        orchestrator.controller.start()
        orchestrator.risk_manager._halted = True
        orchestrator.risk_manager._halt_reason = "일일 손실 한도 -5%"

        result = await orchestrator.run_cycle(index_prices, universe)

        assert result.buys_executed == 0
        assert "리스크" in result.reason

    @pytest.mark.asyncio
    async def test_refine_entry_fallback_when_unsupported(self, orchestrator):
        """분봉 미지원 broker → 진입 정밀화는 시장가 진입으로 폴백."""
        decision = await orchestrator._refine_entry("005930")
        assert decision.should_enter is True
        assert decision.limit_price is None
        assert "정밀화 생략" in decision.reason

    @pytest.mark.asyncio
    async def test_refine_entry_defers_on_overheated_minutes(self, model):
        """분봉 과열 시 진입 보류(WAIT)."""
        orch = TradingOrchestrator(OverheatedMinuteBroker(), model)
        decision = await orch._refine_entry("005930")
        assert decision.should_enter is False

    @pytest.mark.asyncio
    async def test_refine_exit_fallback_when_unsupported(self, orchestrator):
        """분봉 미지원 broker → 청산 정밀화 생략(시장가 폴백)."""
        decision = await orchestrator._refine_exit("005930", -2.0)
        assert decision.should_exit is False
        assert decision.limit_price is None
        assert "정밀화 생략" in decision.reason
