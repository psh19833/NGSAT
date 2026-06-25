"""Tests for NGSAT ML inference."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from core.types import DecisionAction, MarketRegime, PriceData, StockInfo
from ml.features.builder import build_training_dataset
from ml.inference import (
    BUY_PROBABILITY_THRESHOLD,
    SELL_PROBABILITY_THRESHOLD,
    ExitPrediction,
    MLInference,
    MLPrediction,
)
from ml.training.trainer import PriceRiseModel, train_from_price_data
from strategy.regime import RegimeResult
from strategy.screener import ScreenCandidate


def _make_price_data(n: int, start: float = 50000, trend: float = 200) -> list[PriceData]:
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


def _train_test_model() -> PriceRiseModel:
    """Train a small model for testing."""
    np.random.seed(42)
    all_prices = []
    codes = []
    for i in range(5):
        trend = 250 if i % 2 == 0 else -150
        all_prices.append(_make_price_data(120, start=50000 + i * 10000, trend=trend))
        codes.append(f"{i:06d}")
    
    model, _ = train_from_price_data(all_prices, codes, model_type="random_forest")
    return model


class TestMLInference:
    """ML inference engine tests."""

    @pytest.fixture
    def trained_model(self):
        return _train_test_model()

    @pytest.fixture
    def inference(self, trained_model):
        return MLInference(trained_model)

    @pytest.fixture
    def candidate(self):
        return ScreenCandidate(
            code="005930",
            name="삼성전자",
            market="kospi",
            score=75.0,
            patterns=[],
            indicators={},
            reason="테스트 후보",
        )

    def test_predict_entry_returns_prediction(self, inference, candidate):
        """Entry prediction should return an MLPrediction."""
        prices = _make_price_data(80)
        pred = inference.predict_entry(candidate, prices)
        
        assert pred is not None
        assert pred.code == "005930"
        assert pred.name == "삼성전자"
        assert 0 <= pred.rise_probability <= 1
        assert pred.action in [DecisionAction.BUY, DecisionAction.HOLD, DecisionAction.NONE]
        assert len(pred.reason) > 0
        assert "ML 예측" in pred.reason

    def test_predict_entry_insufficient_data(self, inference, candidate):
        """Entry prediction with < 60 days should return None."""
        prices = _make_price_data(30)
        pred = inference.predict_entry(candidate, prices)
        assert pred is None

    def test_predict_entry_has_evidence(self, inference, candidate):
        """Entry prediction should include quantitative evidence."""
        prices = _make_price_data(80)
        pred = inference.predict_entry(candidate, prices)
        
        assert pred is not None
        assert "rise_probability" in pred.evidence
        assert "screening_score" in pred.evidence
        assert "buy_threshold" in pred.evidence

    def test_predict_entry_has_feature_vector(self, inference, candidate):
        """Entry prediction should include the feature vector."""
        prices = _make_price_data(80)
        pred = inference.predict_entry(candidate, prices)
        
        assert pred is not None
        assert len(pred.feature_vector) == 20  # 20 features

    def test_predict_exit_returns_prediction(self, inference):
        """Exit prediction should return an ExitPrediction."""
        prices = _make_price_data(80)
        pred = inference.predict_exit("005930", "삼성전자", prices, current_profit_pct=3.5)
        
        assert pred is not None
        assert pred.code == "005930"
        assert 0 <= pred.rise_probability <= 1
        assert pred.action in [DecisionAction.SELL, DecisionAction.HOLD]
        assert "ML 추론(청산)" in pred.reason

    def test_predict_exit_insufficient_data(self, inference):
        """Exit prediction with < 60 days should return None."""
        prices = _make_price_data(30)
        pred = inference.predict_exit("005930", "삼성전자", prices, current_profit_pct=0)
        assert pred is None

    def test_predict_exit_high_profit_low_prob_sells(self, inference):
        """High profit + low rise probability should trigger SELL."""
        prices = _make_price_data(80, trend=-100)  # Downtrending
        pred = inference.predict_exit("005930", "삼성전자", prices, current_profit_pct=8.0)
        
        if pred is not None:
            # With downtrending data, probability should be low
            if pred.rise_probability < 0.50:
                assert pred.action == DecisionAction.SELL

    def test_predict_before_training_raises(self):
        """Inference with untrained model should raise RuntimeError."""
        model = PriceRiseModel("random_forest")  # Not trained
        
        with pytest.raises(RuntimeError, match="학습되지"):
            inference = MLInference(model)
            candidate = ScreenCandidate(
                code="005930", name="test", market="kospi", score=70, reason="test"
            )
            inference.predict_entry(candidate, _make_price_data(80))

    def test_batch_predict_entry_sorted(self, inference):
        """Batch predictions should be sorted by probability descending."""
        candidates = []
        for i in range(5):
            cand = ScreenCandidate(
                code=f"{i:06d}", name=f"stock_{i}", market="kospi",
                score=70 + i, reason="test",
            )
            prices = _make_price_data(80, start=50000 + i * 10000, trend=200 if i % 2 == 0 else 50)
            candidates.append((cand, prices))
        
        predictions = inference.batch_predict_entry(candidates)
        
        if len(predictions) >= 2:
            probs = [p.rise_probability for p in predictions]
            assert probs == sorted(probs, reverse=True)

    def test_prediction_reason_is_korean(self, inference, candidate):
        """Prediction reason should contain Korean text."""
        prices = _make_price_data(80)
        pred = inference.predict_entry(candidate, prices)
        
        assert pred is not None
        # Should contain Korean action words
        korean_words = ["매수", "홀드", "관망", "상승", "확률"]
        assert any(w in pred.reason for w in korean_words)

    def test_buy_threshold_configurable(self, trained_model):
        """Buy/sell thresholds should be configurable."""
        inference = MLInference(
            trained_model,
            buy_threshold=0.80,
            sell_threshold=0.20,
        )
        
        assert inference._buy_threshold == 0.80
        assert inference._sell_threshold == 0.20
