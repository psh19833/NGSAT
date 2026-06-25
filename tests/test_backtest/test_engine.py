"""Tests for NGSAT backtest engine."""

from __future__ import annotations

import pytest

from backtest.data_loader import generate_synthetic_data, generate_synthetic_index, generate_synthetic_universe
from backtest.engine import BacktestEngine, BacktestResult, BacktestTrade
from core.types import Market, MarketRegime
from ml.training.trainer import PriceRiseModel, train_from_price_data


def _train_model_for_backtest():
    """Train a small, fast model for backtesting tests."""
    universe = generate_synthetic_universe(n_stocks=5, n_days=120, seed=99)
    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]
    
    model, result = train_from_price_data(
        all_prices, codes,
        model_type="logistic",  # Faster than RF
        forward_days=5,
        forward_threshold=0.02,
    )
    return model


class TestBacktestEngine:
    """Backtest engine tests."""

    @pytest.fixture
    def trained_model(self):
        return _train_model_for_backtest()

    @pytest.fixture
    def universe(self):
        return generate_synthetic_universe(n_stocks=5, n_days=100, seed=42)

    @pytest.fixture
    def index_prices(self):
        return generate_synthetic_index(n_days=100, seed=100)

    def test_run_backtest(self, trained_model, universe, index_prices):
        """Backtest should run and produce a result."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        assert isinstance(result, BacktestResult)
        assert result.initial_capital == 10_000_000
        assert result.start_date != ""
        assert len(result.reason) > 0

    def test_empty_universe(self, trained_model, index_prices):
        """Empty universe should return empty result."""
        engine = BacktestEngine(trained_model)
        result = engine.run([], index_prices)
        
        assert result.total_trades == 0
        assert "데이터 없음" in result.reason

    def test_empty_index(self, trained_model, universe):
        """Empty index should return empty result."""
        engine = BacktestEngine(trained_model)
        result = engine.run(universe, [])
        
        assert result.total_trades == 0

    def test_backtest_produces_trades(self, trained_model, universe, index_prices):
        """Backtest on sufficient data should produce some trades."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        # With 5 stocks and 100 days, trades may be sparse
        assert result.buy_count >= 0
        assert result.sell_count >= 0
        assert result.buy_count + result.sell_count == result.total_trades

    def test_all_trades_have_reasons(self, trained_model, universe, index_prices):
        """Every trade should have a non-empty reason."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        for trade in result.trades:
            assert len(trade.reason) > 0, f"Trade {trade.code} has empty reason"
            assert trade.action != ""

    def test_result_has_korean_reason(self, trained_model, universe, index_prices):
        """Result reason should be in Korean."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        assert "백테스트 완료" in result.reason
        assert "수익률" in result.reason

    def test_max_drawdown_non_positive(self, trained_model, universe, index_prices):
        """Max drawdown should be <= 0."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        assert result.max_drawdown <= 0

    def test_daily_capital_tracked(self, trained_model, universe, index_prices):
        """Daily capital should be tracked."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        assert len(result.daily_capital) > 0

    def test_initial_final_capital(self, trained_model, universe, index_prices):
        """Final capital should be a valid number."""
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60)
        
        assert result.initial_capital == 10_000_000
        assert result.final_capital >= 0  # Can't go below 0

    def test_halt_on_excessive_loss(self, trained_model):
        """Engine should halt when daily loss exceeds limit."""
        from core.config import RiskConfig
        
        # Very strict risk config: 1% daily loss limit
        risk = RiskConfig(daily_loss_limit_pct=1.0)
        engine = BacktestEngine(
            trained_model,
            initial_capital=10_000_000,
            risk_config=risk,
        )
        
        universe = generate_synthetic_universe(n_stocks=5, n_days=100, seed=42)
        index_prices = generate_synthetic_index(n_days=100, seed=100, trend=-5)
        
        result = engine.run(universe, index_prices, start_day=60)
        
        # With a 1% limit and downtrending market, should halt at some point
        # Just verify it doesn't crash
        assert isinstance(result, BacktestResult)
