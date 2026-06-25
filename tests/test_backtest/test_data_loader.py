"""Tests for NGSAT backtest data loader."""

from __future__ import annotations

import numpy as np
import pytest

from backtest.data_loader import (
    generate_synthetic_data,
    generate_synthetic_index,
    generate_synthetic_universe,
    load_from_cache,
)
from core.types import Market, PriceData


class TestSyntheticData:
    """Synthetic data generation tests."""

    def test_generate_synthetic_data_length(self):
        prices = generate_synthetic_data("005930", n_days=100)
        assert len(prices) == 100
        assert all(isinstance(p, PriceData) for p in prices)

    def test_synthetic_data_has_valid_ohlc(self):
        prices = generate_synthetic_data("005930", n_days=50)
        for p in prices:
            assert p.high >= p.low
            assert p.close > 0
            assert p.volume > 0
            assert p.code == "005930"

    def test_synthetic_data_reproducible(self):
        """Same seed should produce same data."""
        p1 = generate_synthetic_data("005930", n_days=30, seed=42)
        p2 = generate_synthetic_data("005930", n_days=30, seed=42)
        assert [p.close for p in p1] == [p.close for p in p2]

    def test_synthetic_uptrend(self):
        """Uptrending data should end higher than start."""
        prices = generate_synthetic_data("005930", n_days=100, start_price=50000, trend=200, seed=42)
        assert prices[-1].close > prices[0].close

    def test_synthetic_downtrend(self):
        """Downtrending data should end lower than start."""
        prices = generate_synthetic_data("005930", n_days=100, start_price=50000, trend=-200, seed=42)
        assert prices[-1].close < prices[0].close

    def test_synthetic_index(self):
        idx = generate_synthetic_index(n_days=80)
        assert len(idx) == 80
        assert all(p.code == "INDEX" for p in idx)

    def test_synthetic_universe(self):
        universe = generate_synthetic_universe(n_stocks=10, n_days=100)
        assert len(universe) == 10
        for info, prices in universe:
            assert len(prices) == 100
            assert len(info.code) == 6
            assert info.market in [Market.KOSPI, Market.KOSDAQ]

    def test_synthetic_universe_has_mix(self):
        """Universe should have both KOSPI and KOSDAQ stocks."""
        universe = generate_synthetic_universe(n_stocks=20, n_days=100)
        markets = {info.market for info, _ in universe}
        assert Market.KOSPI in markets
        assert Market.KOSDAQ in markets


class TestLoadFromCache:
    """Database cache loading tests."""

    def test_load_from_cache_no_db(self):
        """Should return empty list when DB is not available."""
        result = load_from_cache("005930", "2025-01-01", "2025-06-01")
        assert isinstance(result, list)
        # Will be empty since no DB is configured
        assert len(result) == 0
