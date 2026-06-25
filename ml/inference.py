"""NGSAT ML inference — real-time price rise probability prediction.

3rd stage of the NGSAT pipeline:
  Regime → Screener → ML (this module)

Takes screened candidates (from stage 2), builds feature vectors,
runs the trained ML model, and produces buy/sell/hold decisions
with probability scores and mandatory reasons.

Every decision includes:
- Price rise probability (0-1)
- Action: BUY / SELL / HOLD / NONE
- Human-readable reason (Korean)
- Quantitative evidence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.logger import logger
from core.types import DecisionAction, DecisionReason, MarketRegime, PriceData
from ml.features.builder import FEATURE_NAMES, FeatureVector, build_features
from ml.training.trainer import PriceRiseModel
from strategy.screener import ScreenCandidate


@dataclass(frozen=True)
class MLPrediction:
    """ML prediction for a single stock.
    
    Attributes:
        code: Stock code.
        name: Stock name.
        rise_probability: Probability of price rising (0-1).
        action: Recommended action (BUY/HOLD/NONE).
        reason: Human-readable reason (Korean).
        evidence: Quantitative evidence dict.
        feature_vector: The feature values used for prediction.
    """
    code: str
    name: str
    rise_probability: float
    action: DecisionAction
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    feature_vector: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitPrediction:
    """ML prediction for exit timing (sell/hold existing position).
    
    Attributes:
        code: Stock code.
        name: Stock name.
        rise_probability: Probability of continued rise.
        action: SELL / HOLD
        reason: Human-readable reason (Korean).
        evidence: Quantitative evidence.
    """
    code: str
    name: str
    rise_probability: float
    action: DecisionAction
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ── Thresholds ──
BUY_PROBABILITY_THRESHOLD = 0.65    # ≥ 65% → BUY
SELL_PROBABILITY_THRESHOLD = 0.35   # ≤ 35% → SELL (price likely to fall)
# Between 35-65% → HOLD


class MLInference:
    """ML inference engine for entry and exit decisions.
    
    Entry: Takes ScreenCandidate + price data → MLPrediction (buy/hold)
    Exit: Takes Position + price data → ExitPrediction (sell/hold)
    
    Every prediction includes a reason with probability and evidence.
    """
    
    def __init__(
        self,
        model: PriceRiseModel,
        buy_threshold: float = BUY_PROBABILITY_THRESHOLD,
        sell_threshold: float = SELL_PROBABILITY_THRESHOLD,
    ):
        self._model = model
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
    
    def predict_entry(
        self,
        candidate: ScreenCandidate,
        prices: list[PriceData],
    ) -> MLPrediction | None:
        """Predict whether to buy a screened candidate.
        
        Args:
            candidate: Screened stock from the screener (stage 2).
            prices: Price history for this stock.
        
        Returns:
            MLPrediction with buy/hold recommendation, or None if insufficient data.
        """
        if not self._model.is_trained:
            raise RuntimeError("ML 모델이 학습되지 않았습니다")
        
        # Build feature vector
        fv = build_features(prices, code=candidate.code)
        if fv is None or len(fv.features) != len(FEATURE_NAMES):
            return None
        
        # Convert to matrix and predict
        X = np.array([[fv.features[name] for name in FEATURE_NAMES]])
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        proba = float(self._model.predict_proba(X)[0])
        
        # Determine action
        if proba >= self._buy_threshold:
            action = DecisionAction.BUY
            action_kr = "매수"
        elif proba <= self._sell_threshold:
            action = DecisionAction.NONE
            action_kr = "관망"
        else:
            action = DecisionAction.HOLD
            action_kr = "홀드"
        
        # Build reason with screening score and ML probability
        pattern_names = [p.pattern_name_kr for p in candidate.patterns]
        reason = (
            f"ML 예측: {action_kr} (상승 확률 {proba:.1%}), "
            f"스크리닝 점수 {candidate.score:.1f}점, "
            f"감지 패턴: {', '.join(pattern_names) if pattern_names else '없음'}, "
            f"RSI {fv.features.get('rsi_14', 0):.1f}"
        )
        
        evidence = {
            "rise_probability": proba,
            "screening_score": candidate.score,
            "buy_threshold": self._buy_threshold,
            "sell_threshold": self._sell_threshold,
            "patterns_detected": len(candidate.patterns),
            "rsi": fv.features.get("rsi_14", 0),
            "macd_histogram": fv.features.get("macd_histogram", 0),
            "bollinger_position": fv.features.get("bollinger_position", 0),
        }
        
        logger.info(f"ML 추론(진입): {candidate.code} 확률={proba:.1%} → {action_kr}")
        
        return MLPrediction(
            code=candidate.code,
            name=candidate.name,
            rise_probability=proba,
            action=action,
            reason=reason,
            evidence=evidence,
            feature_vector=fv.features,
        )
    
    def predict_exit(
        self,
        code: str,
        name: str,
        prices: list[PriceData],
        current_profit_pct: float,
    ) -> ExitPrediction | None:
        """Predict whether to sell or hold an existing position.
        
        Args:
            code: Stock code.
            name: Stock name.
            prices: Price history for this stock.
            current_profit_pct: Current profit/loss percentage.
        
        Returns:
            ExitPrediction with sell/hold recommendation, or None if insufficient data.
        """
        if not self._model.is_trained:
            raise RuntimeError("ML 모델이 학습되지 않았습니다")
        
        fv = build_features(prices, code=code)
        if fv is None or len(fv.features) != len(FEATURE_NAMES):
            return None
        
        X = np.array([[fv.features[name] for name in FEATURE_NAMES]])
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        proba = float(self._model.predict_proba(X)[0])
        
        # Exit logic: if rise probability is low, sell
        # Also consider current profit — if profitable and prob dropping, take profit
        if proba <= self._sell_threshold:
            action = DecisionAction.SELL
            action_kr = "매도"
            reason_detail = f"상승 확률 저하 ({proba:.1%} <= {self._sell_threshold:.0%})"
        elif proba < 0.50 and current_profit_pct > 5.0:
            action = DecisionAction.SELL
            action_kr = "매도"
            reason_detail = f"수익 실현 (수익률 {current_profit_pct:.1f}%, 상승 확률 {proba:.1%})"
        else:
            action = DecisionAction.HOLD
            action_kr = "홀드"
            reason_detail = f"상승 확률 유지 ({proba:.1%})"
        
        reason = (
            f"ML 추론(청산): {action_kr} — {reason_detail}, "
            f"현재 수익률 {current_profit_pct:+.1f}%, "
            f"RSI {fv.features.get('rsi_14', 0):.1f}"
        )
        
        evidence = {
            "rise_probability": proba,
            "current_profit_pct": current_profit_pct,
            "sell_threshold": self._sell_threshold,
            "rsi": fv.features.get("rsi_14", 0),
            "macd_histogram": fv.features.get("macd_histogram", 0),
        }
        
        logger.info(f"ML 추론(청산): {code} 확률={proba:.1%} 수익률={current_profit_pct:+.1f}% → {action_kr}")
        
        return ExitPrediction(
            code=code,
            name=name,
            rise_probability=proba,
            action=action,
            reason=reason,
            evidence=evidence,
        )
    
    def batch_predict_entry(
        self,
        candidates: list[tuple[ScreenCandidate, list[PriceData]]],
    ) -> list[MLPrediction]:
        """Run entry predictions for multiple candidates.
        
        Args:
            candidates: List of (ScreenCandidate, price history) tuples.
        
        Returns:
            List of MLPredictions, sorted by probability (descending).
        """
        predictions: list[MLPrediction] = []
        
        for candidate, prices in candidates:
            pred = self.predict_entry(candidate, prices)
            if pred is not None:
                predictions.append(pred)
        
        # Sort by probability descending
        predictions.sort(key=lambda p: p.rise_probability, reverse=True)
        
        return predictions
