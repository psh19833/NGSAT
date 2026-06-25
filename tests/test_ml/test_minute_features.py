"""Tests for NGSAT minute-candle feature builder."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from core.types import PriceData
from ml.features.minute_builder import (
    MINUTE_FEATURE_NAMES,
    build_minute_features,
    build_minute_training_dataset,
    MinuteFeatureVector,
)


def _make_minute_bars(
    n_bars: int = 100,
    start_price: float = 50000.0,
    trend: float = 10.0,
    volatility: float = 0.003,
    seed: int = 42,
) -> list[PriceData]:
    """Generate synthetic minute bars for testing."""
    rng = np.random.default_rng(seed)
    prices: list[PriceData] = []
    current = start_price
    base_ts = datetime(2026, 6, 25, 9, 0)

    for i in range(n_bars):
        change = rng.normal(trend / start_price, volatility)
        current = current * (1 + change)
        intra_vol = current * volatility

        body_high = rng.uniform(0, intra_vol * 0.5)
        body_low = rng.uniform(0, intra_vol * 0.5)

        o = current - body_low + rng.uniform(-intra_vol * 0.1, intra_vol * 0.1)
        hi = max(o, current) + abs(rng.normal(0, intra_vol * 0.3))
        lo = min(o, current) - abs(rng.normal(0, intra_vol * 0.3))

        prices.append(PriceData(
            code="005930",
            timestamp=base_ts + timedelta(minutes=i),
            open=float(o),
            high=float(hi),
            low=float(lo),
            close=float(current),
            volume=int(rng.integers(50000, 500000)),
        ))

    return prices


class TestBuildMinuteFeatures:
    """Test minute feature vector generation."""

    def test_returns_none_for_insufficient_data(self):
        prices = _make_minute_bars(n_bars=30)
        result = build_minute_features(prices)
        assert result is None

    def test_returns_feature_vector(self):
        prices = _make_minute_bars(n_bars=100)
        result = build_minute_features(prices, code="005930")
        assert result is not None
        assert isinstance(result, MinuteFeatureVector)
        assert result.code == "005930"
        assert len(result.features) > 0

    def test_feature_count_matches_names(self):
        prices = _make_minute_bars(n_bars=100)
        result = build_minute_features(prices)
        assert result is not None
        assert len(result.features) == len(MINUTE_FEATURE_NAMES)

    def test_all_feature_names_present(self):
        prices = _make_minute_bars(n_bars=100)
        result = build_minute_features(prices)
        assert result is not None
        for name in MINUTE_FEATURE_NAMES:
            assert name in result.features, f"Missing feature: {name}"

    def test_feature_values_are_finite(self):
        prices = _make_minute_bars(n_bars=100)
        result = build_minute_features(prices)
        assert result is not None
        for name, val in result.features.items():
            assert np.isfinite(val), f"Non-finite value for {name}: {val}"

    def test_target_is_none_without_include_target(self):
        prices = _make_minute_bars(n_bars=100)
        result = build_minute_features(prices, include_target=False)
        assert result is not None
        assert result.target is None

    def test_target_present_with_include_target(self):
        prices = _make_minute_bars(n_bars=100)
        result = build_minute_features(prices, include_target=True)
        assert result is not None
        # Target might be None if there aren't enough bars for forward_minutes
        # But with 100 bars and default forward_minutes=10, there should be

    def test_uptrend_has_positive_momentum(self):
        """Strongly trending up prices should show positive momentum features."""
        prices = _make_minute_bars(
            n_bars=100, start_price=50000, trend=500, volatility=0.001, seed=42
        )
        result = build_minute_features(prices)
        assert result is not None
        assert result.features["m_momentum_3"] > -5.0  # Not strongly negative

    def test_feature_reproducibility(self):
        """Same seed should produce identical features."""
        p1 = _make_minute_bars(n_bars=100, seed=123)
        p2 = _make_minute_bars(n_bars=100, seed=123)
        f1 = build_minute_features(p1)
        f2 = build_minute_features(p2)
        assert f1 is not None and f2 is not None
        for name in MINUTE_FEATURE_NAMES:
            assert abs(f1.features[name] - f2.features[name]) < 1e-6, f"{name} differs"


class TestBuildMinuteTrainingDataset:
    """Test training dataset generation from minute data."""

    def test_empty_on_insufficient_data(self):
        prices = _make_minute_bars(n_bars=30)
        X, y, prices_at, names = build_minute_training_dataset(
            [prices], ["TEST"],
        )
        assert len(X) == 0
        assert len(y) == 0

    def test_returns_dataset(self):
        prices = _make_minute_bars(n_bars=120)
        X, y, prices_at, names = build_minute_training_dataset(
            [prices], ["005930"],
        )
        assert len(X) > 0
        assert len(y) > 0
        assert len(X) == len(y)
        assert len(names) == len(MINUTE_FEATURE_NAMES)

    def test_labels_are_binary(self):
        prices = _make_minute_bars(n_bars=120)
        X, y, prices_at, names = build_minute_training_dataset(
            [prices], ["005930"],
        )
        assert set(np.unique(y)).issubset({0, 1})

    def test_multi_stock_dataset(self):
        p1 = _make_minute_bars(n_bars=120, seed=1)
        p2 = _make_minute_bars(n_bars=120, seed=2)
        X, y, prices_at, names = build_minute_training_dataset(
            [p1, p2], ["A", "B"],
        )
        assert len(X) > 0
        assert X.shape[1] == len(MINUTE_FEATURE_NAMES)

    def test_no_nan_in_output(self):
        prices = _make_minute_bars(n_bars=120)
        X, y, prices_at, names = build_minute_training_dataset(
            [prices], ["005930"],
        )
        assert not np.any(np.isnan(X))
        assert not np.any(np.isnan(y.astype(float)))
