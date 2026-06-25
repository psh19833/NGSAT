"""Tests for NGSAT feature engineering."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from core.types import PriceData
from ml.features.builder import FEATURE_NAMES, FeatureVector, build_features, build_training_dataset


def _make_price_data(n: int, start: float = 50000, trend: float = 100) -> list[PriceData]:
    """Generate n days of realistic price data."""
    np.random.seed(42)
    prices = []
    for i in range(n):
        close = start + i * trend + np.random.randn() * 200
        prices.append(PriceData(
            code="005930",
            timestamp=datetime.now() - timedelta(days=n - i),
            open=close - 100,
            high=close + 150,
            low=close - 150,
            close=close,
            volume=100000 + int(np.random.randn() * 20000),
            change_pct=trend / start * 100,
        ))
    return prices


class TestBuildFeatures:
    """Feature vector building tests."""

    def test_build_features_success(self):
        """Should build 20 features from 60+ days of data."""
        prices = _make_price_data(80)
        fv = build_features(prices, code="005930")
        
        assert fv is not None
        assert fv.code == "005930"
        assert len(fv.features) == len(FEATURE_NAMES)
        
        # Check all expected features present
        for name in FEATURE_NAMES:
            assert name in fv.features, f"Missing feature: {name}"

    def test_insufficient_data_returns_none(self):
        """Less than 60 days should return None."""
        prices = _make_price_data(30)
        fv = build_features(prices, code="005930")
        assert fv is None

    def test_features_are_numeric(self):
        """All feature values should be numeric (float)."""
        prices = _make_price_data(80)
        fv = build_features(prices, code="005930")
        
        for name, value in fv.features.items():
            assert isinstance(value, (int, float)), f"{name} is not numeric: {type(value)}"
            assert not np.isnan(value), f"{name} is NaN"

    def test_feature_names_count(self):
        """Should have exactly 20 features."""
        assert len(FEATURE_NAMES) == 20

    def test_rsi_in_valid_range(self):
        """RSI should be between 0 and 100."""
        prices = _make_price_data(80)
        fv = build_features(prices, code="005930")
        assert 0 <= fv.features["rsi_14"] <= 100

    def test_bollinger_position_in_valid_range(self):
        """Bollinger position should be between 0 and 1 (roughly)."""
        prices = _make_price_data(80)
        fv = build_features(prices, code="005930")
        # Can slightly exceed [0,1] due to price moving outside bands
        assert -0.5 <= fv.features["bollinger_position"] <= 1.5


class TestBuildTrainingDataset:
    """Training dataset building tests."""

    def test_dataset_shape(self):
        """Dataset should have correct shape."""
        stocks = [_make_price_data(100, trend=200), _make_price_data(100, trend=-100)]
        codes = ["005930", "000660"]
        
        X, y, names = build_training_dataset(stocks, codes, forward_days=5, forward_threshold=0.02)
        
        assert X.shape[1] == len(FEATURE_NAMES)
        assert len(y) == X.shape[0]
        assert names == FEATURE_NAMES
        assert X.shape[0] > 0  # Should have some samples

    def test_labels_are_binary(self):
        """Labels should be 0 or 1."""
        stocks = [_make_price_data(100, trend=200), _make_price_data(100, trend=-100)]
        codes = ["005930", "000660"]
        
        X, y, names = build_training_dataset(stocks, codes)
        
        assert set(np.unique(y)).issubset({0, 1})

    def test_insufficient_data_returns_empty(self):
        """Stocks with < 60 + forward_days should produce no samples."""
        stocks = [_make_price_data(50)]
        codes = ["005930"]
        
        X, y, names = build_training_dataset(stocks, codes, forward_days=5)
        
        assert X.shape[0] == 0

    def test_no_nan_in_features(self):
        """Feature matrix should not contain NaN (replaced with 0)."""
        stocks = [_make_price_data(100, trend=200)]
        codes = ["005930"]
        
        X, y, names = build_training_dataset(stocks, codes)
        
        assert not np.any(np.isnan(X))

    def test_uptrend_stock_has_more_up_labels(self):
        """A strongly uptrending stock should have more 'up' labels."""
        stocks = [_make_price_data(100, trend=500)]  # Strong uptrend
        codes = ["005930"]
        
        X, y, names = build_training_dataset(stocks, codes, forward_threshold=0.01)
        
        if len(y) > 0:
            up_ratio = np.mean(y)
            # In a strong uptrend, most labels should be 1
            assert up_ratio > 0.3  # At least 30% should be "up"
