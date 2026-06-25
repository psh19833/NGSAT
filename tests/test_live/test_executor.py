"""Tests for NGSAT live order executor."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import RiskConfig
from core.types import AccountSummary, DecisionAction, Market, OrderSide, Position
from data.adapters.base import BrokerAdapter
from live.controller import TradingController
from live.executor import ExecutionResult, OrderExecutor
from live.risk import RiskManager


class MockBroker(BrokerAdapter):
    """Mock broker for testing — no real API calls."""
    
    def __init__(self):
        self._submit_order = AsyncMock(return_value="ORDER_12345")
        self._account = AccountSummary(
            total_asset=10_000_000, deposit=5_000_000,
            total_eval=5_000_000, total_profit_loss=0,
            total_profit_loss_pct=0,
        )
        self._positions: list[Position] = []
    
    async def get_account_summary(self): return self._account
    async def get_positions(self): return self._positions
    async def get_price(self, code): ...
    async def get_price_history(self, code, start, end): ...
    async def get_stock_list(self): return []
    async def submit_order(self, code, side, quantity, price=None):
        return await self._submit_order(code, side, quantity, price)
    async def cancel_order(self, order_id): return True
    async def is_market_open(self): return True
    async def close(self): pass


@pytest.fixture
def broker():
    return MockBroker()


@pytest.fixture
def risk_manager():
    return RiskManager(RiskConfig())


@pytest.fixture
def controller():
    return TradingController()


@pytest.fixture
def executor(broker, risk_manager, controller):
    return OrderExecutor(broker, risk_manager, controller)


class TestExecuteBuy:
    """Buy order execution tests."""

    @pytest.mark.asyncio
    async def test_successful_buy(self, executor, controller):
        """Buy with valid reason and running state should succeed."""
        controller.start()
        
        result = await executor.execute_buy(
            code="005930", name="삼성전자",
            quantity=10, price=70000,
            action=DecisionAction.BUY,
            reason="ML 예측: 매수 (상승 확률 72%)",
        )
        
        assert result.success is True
        assert result.order_id == "ORDER_12345"
        assert result.code == "005930"
        assert result.side == "buy"
        assert result.quantity == 10

    @pytest.mark.asyncio
    async def test_buy_without_reason_rejected(self, executor, controller):
        """Buy without reason should be rejected."""
        controller.start()
        
        result = await executor.execute_buy(
            code="005930", name="삼성전자",
            quantity=10, price=70000,
            action=DecisionAction.BUY,
            reason="",
        )
        
        assert result.success is False
        assert "사유 없음" in result.error

    @pytest.mark.asyncio
    async def test_buy_when_not_running(self, executor):
        """Buy when controller is not running should be rejected."""
        result = await executor.execute_buy(
            code="005930", name="삼성전자",
            quantity=10, price=70000,
            action=DecisionAction.BUY,
            reason="valid reason",
        )
        
        assert result.success is False
        assert "진행 중 아님" in result.error

    @pytest.mark.asyncio
    async def test_buy_when_risk_halted(self, executor, controller, risk_manager):
        """Buy when risk is halted should be rejected."""
        controller.start()
        risk_manager._halted = True
        risk_manager._halt_reason = "일일 손실 한도"
        
        result = await executor.execute_buy(
            code="005930", name="삼성전자",
            quantity=10, price=70000,
            action=DecisionAction.BUY,
            reason="valid reason",
        )
        
        assert result.success is False
        assert "리스크" in result.error


class TestExecuteSell:
    """Sell order execution tests."""

    @pytest.mark.asyncio
    async def test_successful_sell(self, executor, controller):
        """Sell with valid reason should succeed."""
        controller.start()
        
        result = await executor.execute_sell(
            code="005930", name="삼성전자",
            quantity=10, price=72000,
            action=DecisionAction.SELL,
            reason="ML 추론(청산): 매도 — 상승 확률 저하 25%",
        )
        
        assert result.success is True
        assert result.side == "sell"

    @pytest.mark.asyncio
    async def test_sell_without_reason_rejected(self, executor, controller):
        controller.start()
        
        result = await executor.execute_sell(
            code="005930", name="삼성전자",
            quantity=10, price=72000,
            action=DecisionAction.SELL,
            reason="",
        )
        
        assert result.success is False
        assert "사유 없음" in result.error

    @pytest.mark.asyncio
    async def test_sell_force_hold_rejected(self, executor, controller):
        """Sell on force-held position should be rejected."""
        controller.start()
        controller.force_hold("005930", "삼성전자")
        
        result = await executor.execute_sell(
            code="005930", name="삼성전자",
            quantity=10, price=72000,
            action=DecisionAction.SELL,
            reason="ML sell signal",
        )
        
        assert result.success is False
        assert "강제 홀드" in result.error

    @pytest.mark.asyncio
    async def test_force_sell_bypasses_hold(self, executor, controller):
        """Force sell should bypass force hold."""
        controller.start()
        controller.force_hold("005930", "삼성전자")
        
        result = await executor.execute_force_sell(
            code="005930", name="삼성전자",
            quantity=10,
        )
        
        assert result.success is True
        assert result.action == "force_sell"

    @pytest.mark.asyncio
    async def test_force_sell_when_shutdown(self, executor, controller):
        """Force sell during shutdown should be rejected."""
        controller.start()
        controller.shutdown()
        
        result = await executor.execute_force_sell(
            code="005930", name="삼성전자",
            quantity=10,
        )
        
        assert result.success is False
        assert "종료" in result.error


class TestExecutionResultFormat:
    """Verify execution result format."""

    @pytest.mark.asyncio
    async def test_result_has_reason(self, executor, controller):
        """Successful execution should carry the reason."""
        controller.start()
        
        result = await executor.execute_buy(
            code="005930", name="삼성전자",
            quantity=5, price=70000,
            action=DecisionAction.BUY,
            reason="ML 예측: 상승 확률 75%",
        )
        
        assert "ML" in result.reason
        assert "75%" in result.reason
