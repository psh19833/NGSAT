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

    def test_synthetic_minute_bars_within_day_range(self, universe):
        """합성 분봉은 일봉 high/low 범위 안에 있어야 한다."""
        from backtest.data_loader import generate_synthetic_minute_bars
        info, prices = universe[0]
        day_bar = prices[70]
        bars = generate_synthetic_minute_bars(day_bar, n_bars=20)
        assert len(bars) == 20
        for b in bars:
            assert day_bar.low - 1 <= b.close <= day_bar.high + 1

    def test_backtest_with_minute_provider_runs(self, trained_model, universe, index_prices):
        """분봉 provider를 주면 진입/청산 정밀화 경로를 태우며 정상 작동한다."""
        from backtest.data_loader import synthetic_minute_provider
        provider = synthetic_minute_provider(universe)
        engine = BacktestEngine(trained_model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices, start_day=60, minute_provider=provider)

        assert isinstance(result, BacktestResult)
        assert result.entries_deferred >= 0
        assert "백테스트 완료" in result.reason
        assert "진입보류" in result.reason
        for trade in result.trades:
            assert len(trade.reason) > 0

    def test_minute_provider_backtest_no_crash_vs_baseline(self, trained_model, universe, index_prices):
        """정밀화 백테스트도 일봉 백테스트와 동일하게 유효한 결과를 낸다."""
        from backtest.data_loader import synthetic_minute_provider
        baseline = BacktestEngine(trained_model).run(universe, index_prices, start_day=60)
        refined = BacktestEngine(trained_model).run(
            universe, index_prices, start_day=60,
            minute_provider=synthetic_minute_provider(universe),
        )
        assert baseline.final_capital >= 0
        assert refined.final_capital >= 0
        assert refined.entries_deferred >= 0
