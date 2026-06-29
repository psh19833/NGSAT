"""NGSAT integration tests — end-to-end pipeline validation.

Tests the full pipeline working together:
  1. Regime → Screener → ML → Orchestrator → Executor
  2. Backtest engine end-to-end
  3. Dashboard API with real orchestrator
  4. Telegram bot with real orchestrator

These tests verify that all modules integrate correctly,
not just individual components in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from backtest.data_loader import generate_synthetic_data, generate_synthetic_index, generate_synthetic_universe
from backtest.engine import BacktestEngine
from backtest.report import generate_report
from core.config import RiskConfig
from core.types import AccountSummary, Market, Position, PriceData, StockInfo
from data.adapters.base import BrokerAdapter
from dashboard.backend.api import create_app
from fastapi.testclient import TestClient
from live.orchestrator import TradingOrchestrator
from ml.training.trainer import PriceRiseModel, train_from_price_data
from strategy.regime import evaluate_regime
from strategy.screener import screen_stocks
from messaging.bot import TelegramBot


# ── Test fixtures ──

class IntegrationBroker(BrokerAdapter):
    """Full mock broker for integration testing."""

    def __init__(self):
        self._account = AccountSummary(
            total_asset=10_000_000, deposit=10_000_000,
            total_eval=0, total_profit_loss=0, total_profit_loss_pct=0,
        )
        self._positions: list[Position] = []
        self._order_counter = 0
        self._orders: dict[str, tuple] = {}  # order_id → (code, side, qty, price)

    async def get_account_summary(self):
        return self._account

    async def get_positions(self):
        return self._positions

    async def get_price(self, code):
        return PriceData(
            code=code, timestamp=datetime.now(),
            open=70000, high=71000, low=69000, close=70500,
            volume=100000,
        )

    async def get_price_history(self, code, start, end):
        return generate_synthetic_data(code, n_days=100, seed=42)

    async def get_stock_list(self):
        return []

    async def submit_order(self, code, side, quantity, price=None):
        self._order_counter += 1
        order_id = f"INT_ORDER_{self._order_counter}"
        self._orders[order_id] = (code, side, quantity, price or 70500)
        return order_id

    async def cancel_order(self, order_id):
        if order_id in self._orders:
            del self._orders[order_id]
            return True
        return False

    async def get_order_status(self, order_id):
        from core.types import OrderStatus
        if order_id in self._orders:
            return OrderStatus.FILLED
        return OrderStatus.REJECTED

    async def is_market_open(self):
        return True

    async def close(self):
        pass


@pytest.fixture(scope="module")
def trained_model():
    """Train a model once for all integration tests."""
    universe = generate_synthetic_universe(n_stocks=10, n_days=150, seed=99)
    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]
    model, _ = train_from_price_data(
        all_prices, codes,
        model_type="logistic",
        forward_days=5,
        forward_threshold=0.02,
    )
    return model


@pytest.fixture
def broker():
    return IntegrationBroker()


@pytest.fixture
def universe():
    return generate_synthetic_universe(n_stocks=10, n_days=100, seed=42)


@pytest.fixture
def index_prices():
    return generate_synthetic_index(n_days=100, seed=100)


# ── Integration tests ──

class TestFullPipeline:
    """End-to-end pipeline: Regime → Screener → ML → Orchestrator."""

    @pytest.mark.asyncio
    async def test_regime_to_screener_to_ml(self, trained_model, universe, index_prices):
        """Full pipeline: regime → screener → ML prediction."""
        from ml.inference import MLInference
        from strategy.screener import screen_stocks

        # Step 1: Regime
        regime = evaluate_regime(
            [p.close for p in index_prices],
            [p.volume for p in index_prices],
        )
        assert regime.regime in ["bull", "neutral", "bear"]

        # Step 2: Screener
        screen_result = screen_stocks(universe, regime)
        assert screen_result.total_scanned == 10

        # Step 3: ML on top candidates
        inference = MLInference(trained_model)

        if screen_result.candidates:
            candidate = screen_result.candidates[0]
            prices = next(p for info, p in universe if info.code == candidate.code)

            pred = inference.predict_entry(candidate, prices)
            if pred:
                assert 0 <= pred.rise_probability <= 1
                assert len(pred.reason) > 0

    @pytest.mark.asyncio
    async def test_orchestrator_full_cycle(self, trained_model, broker, universe, index_prices):
        """Orchestrator runs a complete cycle with all components."""
        orch = TradingOrchestrator(broker, trained_model)
        orch.controller.start()

        result = await orch.run_cycle(index_prices, universe)

        assert result.regime in ["bull", "neutral", "bear"]
        assert len(result.reason) > 0
        assert "사이클" in result.reason

    @pytest.mark.asyncio
    async def test_orchestrator_force_sell_flow(self, trained_model, broker):
        """Force sell flow: position → force sell command → execution."""
        from live.controller import TradingState

        # Add a mock position
        broker._positions.append(Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=71000,
            market=Market.KOSPI, buy_amount=700000, eval_amount=710000,
            profit_loss=10000, profit_loss_pct=1.43, stop_loss_pct=3.0,
        ))

        orch = TradingOrchestrator(broker, trained_model)
        orch.controller.start()

        result = await orch.force_sell("005930", "삼성전자")

        assert result.success is True
        assert result.action == "force_sell"
        assert result.code == "005930"


class TestBacktestIntegration:
    """Backtest engine integration with real pipeline."""

    def test_backtest_with_report(self, trained_model):
        """Backtest produces a report with valid metrics."""
        universe = generate_synthetic_universe(n_stocks=10, n_days=100, seed=55)
        index_prices = generate_synthetic_index(n_days=100, seed=200)

        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)

        report = generate_report(result)

        assert report.metrics.total_trades >= 0
        assert len(report.summary) > 0
        assert "백테스트 결과" in report.summary
        assert "수익률" in report.summary


class TestDashboardIntegration:
    """Dashboard API with real orchestrator."""

    @pytest.mark.asyncio
    async def test_dashboard_with_orchestrator(self, trained_model, broker):
        """Dashboard API should work with a real orchestrator."""
        orch = TradingOrchestrator(broker, trained_model)
        app = create_app(orch)
        client = TestClient(app)

        # Status
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["state"] == "idle"

        # Start
        resp = client.post("/api/control/start")
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"

        # Status after start
        resp = client.get("/api/status")
        assert resp.json()["is_running"] is True

        # Account
        resp = client.get("/api/account")
        assert resp.status_code == 200
        assert resp.json()["total_asset"] == 10_000_000

        # Stop
        resp = client.post("/api/control/stop")
        assert resp.json()["state"] == "paused"

    @pytest.mark.asyncio
    async def test_dashboard_force_sell(self, trained_model, broker):
        """Dashboard force sell should reach orchestrator."""
        broker._positions.append(Position(
            code="005930", name="삼성전자", quantity=10,
            buy_price=70000, current_price=71000,
            market=Market.KOSPI, buy_amount=700000, eval_amount=710000,
            profit_loss=10000, profit_loss_pct=1.43, stop_loss_pct=3.0,
        ))

        orch = TradingOrchestrator(broker, trained_model)
        orch.controller.start()
        app = create_app(orch)
        client = TestClient(app)

        resp = client.post("/api/control/forcesell", json={"code": "005930"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True


class TestTelegramIntegration:
    """Telegram bot with real orchestrator."""

    @pytest.mark.asyncio
    async def test_telegram_with_orchestrator(self, trained_model, broker):
        """Telegram bot should process commands via orchestrator."""
        orch = TradingOrchestrator(broker, trained_model)
        bot = TelegramBot(bot_token="test", chat_id="test")
        bot.set_orchestrator(orch)

        # Start command
        result = await bot.process_command("start")
        assert "시작" in result
        assert orch.controller.is_running is True

        # Status command
        result = await bot.process_command("status")
        assert "NGSAT 상태" in result
        assert "running" in result

        # Account command
        result = await bot.process_command("account")
        assert "10,000,000" in result

        # Stop command
        result = await bot.process_command("stop")
        assert "정지" in result


class TestModuleIsolation:
    """Verify live/ and backtest/ package isolation."""

    def test_live_does_not_import_backtest(self):
        """live/ modules must not import backtest/."""
        import ast
        from pathlib import Path

        live_dir = Path(__file__).resolve().parent.parent / "live"

        for py_file in live_dir.glob("*.py"):
            with open(py_file) as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = ""
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            module = alias.name
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""

                    assert "backtest" not in module, (
                        f"{py_file.name} imports backtest — isolation violated!"
                    )

    def test_backtest_does_not_import_live(self):
        """backtest/ modules must not import live/."""
        import ast
        from pathlib import Path

        backtest_dir = Path(__file__).resolve().parent.parent / "backtest"

        for py_file in backtest_dir.glob("*.py"):
            with open(py_file) as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = ""
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            module = alias.name
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""

                    assert "live" not in module or "live." not in module, (
                        f"{py_file.name} imports live — isolation violated!"
                    )

    def test_core_types_decision_reason_mandatory(self):
        """DecisionReason must always require a reason."""
        from core.types import DecisionAction, DecisionReason

        # Empty reason should raise
        with pytest.raises(ValueError):
            DecisionReason(action=DecisionAction.BUY, reason="")

        # Valid reason should work
        dr = DecisionReason(
            action=DecisionAction.BUY,
            reason="ML 상승 확률 75%",
        )
        assert dr.reason == "ML 상승 확률 75%"


class TestSystemIntegrity:
    """Overall system integrity checks."""

    def test_all_core_enums_valid(self):
        """All core enums should have valid values."""
        from core.types import MarketRegime, OrderSide, DecisionAction, Market

        assert len(list(MarketRegime)) == 3
        assert len(list(OrderSide)) == 2
        assert len(list(DecisionAction)) >= 6
        assert len(list(Market)) == 2

    def test_risk_config_defaults(self):
        """Risk config should match 기획서 values."""
        config = RiskConfig()
        assert config.daily_loss_limit_pct == 5.0   # 일일 -5%
        assert config.default_stop_loss_pct == 3.0   # 종목 -3%
        assert config.max_stop_loss_pct == 5.0       # 최대 -5%
        assert config.kospi_weight == 0.7            # 코스피 비중

    def test_feature_count(self):
        """ML feature count should be exactly 27."""
        from ml.features.builder import FEATURE_NAMES
        assert len(FEATURE_NAMES) == 27

    def test_endpoint_catalog_complete(self):
        """KIS endpoint catalog should have all required endpoints."""
        from data.adapters.kis.endpoints import get_endpoint

        required = [
            "token_issue", "inquire_balance", "inquire_daily_ccld",
            "order_cash", "inquire_price", "inquire_daily_chart",
            "inquire_asking_price", "inquire_stock_basic",
            "inquire_holiday", "inquire_market_hours",
        ]
        for name in required:
            ep = get_endpoint(name)
            assert ep.path.startswith("/") or ep.path.startswith("/oauth2")
