"""Tests for NGSAT ML model training."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from core.types import PriceData
from ml.features.builder import build_training_dataset
from ml.training.trainer import PriceRiseModel, TrainingResult, train_from_price_data


def _make_mixed_dataset(n_stocks: int = 5, n_days: int = 120) -> tuple[list, list]:
    """Generate mixed dataset: some uptrending, some downtrending."""
    np.random.seed(42)
    all_prices = []
    codes = []

    for i in range(n_stocks):
        trend = 200 if i % 2 == 0 else -150  # Alternating up/down
        start = 50000 + i * 10000
        prices = []
        for j in range(n_days):
            close = start + j * trend + np.random.randn() * 300
            prices.append(PriceData(
                code=f"{i:06d}",
                timestamp=datetime.now() - timedelta(days=n_days - j),
                open=close - 100,
                high=close + 200,
                low=close - 200,
                close=close,
                volume=100000 + int(np.random.randn() * 30000),
                change_pct=trend / start * 100,
            ))
        all_prices.append(prices)
        codes.append(f"{i:06d}")

    return all_prices, codes


class TestPriceRiseModel:
    """ML model training and prediction tests."""

    def test_train_random_forest(self):
        """Random Forest model should train successfully."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes, forward_days=5, forward_threshold=0.02)

        model = PriceRiseModel("random_forest", forward_days=5, forward_threshold=0.02)
        result = model.train(X, y)

        assert result.success is True
        assert result.model_type == "random_forest"
        assert result.n_samples > 0
        assert result.n_features == 20
        assert len(result.reason) > 0

    def test_train_logistic_regression(self):
        """Logistic Regression model should train successfully."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes, forward_days=5, forward_threshold=0.02)

        model = PriceRiseModel("logistic", forward_days=5, forward_threshold=0.02)
        result = model.train(X, y)

        assert result.success is True
        assert result.model_type == "logistic"

    def test_train_gradient_boosting(self):
        """Gradient Boosting (HistGradientBoosting) model should train successfully."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes, forward_days=5, forward_threshold=0.02)

        model = PriceRiseModel("gradient_boosting", forward_days=5, forward_threshold=0.02)
        result = model.train(X, y)

        assert result.success is True
        assert result.model_type == "gradient_boosting"
        assert result.n_features == 20
        assert 0.0 <= result.positive_rate <= 1.0

    def test_gradient_boosting_predict_proba(self):
        """Gradient Boosting predict_proba should return valid probabilities."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes)

        model = PriceRiseModel("gradient_boosting")
        model.train(X, y)
        proba = model.predict_proba(X[:5])

        assert len(proba) == 5
        assert all(0 <= p <= 1 for p in proba)

    def test_training_result_reports_positive_rate(self):
        """TrainingResult should report the positive (상승) class rate."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes)

        model = PriceRiseModel("random_forest")
        result = model.train(X, y)

        assert 0.0 <= result.positive_rate <= 1.0
        assert "양성비율" in result.reason

    def test_insufficient_data_fails_gracefully(self):
        """Training with < 50 samples should fail gracefully."""
        X = np.random.randn(30, 20)
        y = np.random.randint(0, 2, 30)

        model = PriceRiseModel("random_forest")
        result = model.train(X, y)

        assert result.success is False
        assert "부족" in result.reason

    def test_predict_proba_after_training(self):
        """predict_proba should return probabilities after training."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes)

        model = PriceRiseModel("random_forest")
        model.train(X, y)

        proba = model.predict_proba(X[:5])

        assert len(proba) == 5
        assert all(0 <= p <= 1 for p in proba)

    def test_predict_before_training_raises(self):
        """predict_proba before training should raise RuntimeError."""
        model = PriceRiseModel("random_forest")

        with pytest.raises(RuntimeError, match="학습되지"):
            model.predict_proba(np.array([[1.0] * 20]))

    def test_unsupported_model_type(self):
        """Unsupported model type should fail."""
        X = np.random.randn(100, 20)
        y = np.random.randint(0, 2, 100)

        model = PriceRiseModel("nonexistent")
        result = model.train(X, y)

        assert result.success is False
        assert "지원하지" in result.reason

    def test_save_and_load_model(self, tmp_path):
        """Model should be saveable and loadable."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes)

        model = PriceRiseModel("random_forest")
        model.train(X, y)

        # Save
        save_path = tmp_path / "test_model.pkl"
        model.save(save_path)
        assert save_path.exists()

        # Load
        loaded = PriceRiseModel.load(save_path)
        assert loaded.is_trained is True
        assert loaded.model_type == "random_forest"

        # Predictions should match
        proba_orig = model.predict_proba(X[:3])
        proba_loaded = loaded.predict_proba(X[:3])
        np.testing.assert_array_almost_equal(proba_orig, proba_loaded)

    def test_training_result_has_feature_importance(self):
        """Training result should include feature importance."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes)

        model = PriceRiseModel("random_forest")
        result = model.train(X, y)

        assert len(result.feature_importance) > 0
        # Top feature should be one of the known feature names
        top_feature = list(result.feature_importance.keys())[0]
        assert top_feature in [
            "rsi_14", "macd_histogram", "macd_line", "macd_signal",
            "ma5_distance_pct", "ma20_distance_pct", "ma60_distance_pct",
            "bollinger_position", "bollinger_width", "atr_pct",
            "volume_ratio_20", "stoch_k", "stoch_d",
            "price_change_1d", "price_change_5d", "price_change_10d", "price_change_20d",
            "volatility_20d", "return_skew_20d", "high_low_range_pct",
        ]

    def test_training_result_has_korean_reason(self):
        """Training result should have a Korean reason."""
        all_prices, codes = _make_mixed_dataset(5, 120)
        X, y, _ = build_training_dataset(all_prices, codes)

        model = PriceRiseModel("random_forest")
        result = model.train(X, y)

        assert "학습 완료" in result.reason
        assert "정확도" in result.reason

    def test_train_from_price_data_convenience(self):
        """train_from_price_data should work end-to-end."""
        all_prices, codes = _make_mixed_dataset(5, 120)

        model, result = train_from_price_data(
            all_prices, codes,
            model_type="logistic",
            forward_days=5,
            forward_threshold=0.02,
        )

        assert result.success is True
        assert model.is_trained is True
