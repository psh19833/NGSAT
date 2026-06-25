"""NGSAT ML model training — price rise probability prediction.

Trains a classifier to predict whether a stock will rise above a threshold
in the next N days. Uses scikit-learn (Phase 4 baseline), with
XGBoost/LightGBM upgrade path for Phase 4+.

Model lifecycle:
1. Build training dataset from historical prices
2. Train model with cross-validation
3. Evaluate on held-out test set
4. Save model to disk (joblib)
5. Model is ready for inference (ml/inference.py)
"""

from __future__ import annotations

import joblib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler

from core.logger import logger
from ml.features.builder import FEATURE_NAMES, build_training_dataset

# Project root for model save path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_DIR = _PROJECT_ROOT / "models" / "trained"


@dataclass
class TrainingResult:
    """Result of model training.
    
    Attributes:
        success: Whether training completed successfully.
        model_type: Model name (e.g. "random_forest").
        accuracy: Test set accuracy.
        precision: Test set precision (for "up" class).
        recall: Test set recall.
        f1: Test set F1 score.
        auc: ROC AUC score.
        cv_scores: Cross-validation scores.
        feature_importance: Top feature importance dict.
        n_samples: Number of training samples.
        n_features: Number of features.
        reason: Human-readable summary (Korean).
    """
    success: bool
    model_type: str = ""
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    auc: float = 0.0
    cv_scores: list[float] = field(default_factory=list)
    feature_importance: dict[str, float] = field(default_factory=dict)
    n_samples: int = 0
    n_features: int = 0
    reason: str = ""


class PriceRiseModel:
    """ML model for predicting stock price rise probability.
    
    Supports two model types:
    - "logistic": Logistic Regression (fast baseline)
    - "random_forest": Random Forest (better accuracy)
    
    Future: "xgboost", "lightgbm" when packages installed.
    """
    
    def __init__(
        self,
        model_type: str = "random_forest",
        forward_days: int = 5,
        forward_threshold: float = 0.02,
    ):
        self.model_type = model_type
        self.forward_days = forward_days
        self.forward_threshold = forward_threshold
        self._model: Any = None
        self._scaler: StandardScaler | None = None
        self._is_trained = False
    
    @property
    def is_trained(self) -> bool:
        return self._is_trained
    
    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> TrainingResult:
        """Train the model on a feature matrix and labels.
        
        Args:
            X: Feature matrix (n_samples, n_features).
            y: Labels (0 = down, 1 = up).
        
        Returns:
            TrainingResult with metrics.
        """
        if len(X) < 50:
            return TrainingResult(
                success=False,
                reason=f"학습 데이터 부족: {len(X)}개 (최소 50개 필요)",
            )
        
        # Time-series split: use last 20% as test set
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        
        # Scale features
        self._scaler = StandardScaler()
        X_train_scaled = self._scaler.fit_transform(X_train)
        X_test_scaled = self._scaler.transform(X_test)
        
        # Create model
        if self.model_type == "logistic":
            self._model = LogisticRegression(
                max_iter=1000,
                random_state=42,
                class_weight="balanced",
            )
        elif self.model_type == "random_forest":
            self._model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                random_state=42,
                class_weight="balanced",
                n_jobs=-1,
            )
        else:
            return TrainingResult(
                success=False,
                reason=f"지원하지 않는 모델 타입: {self.model_type}",
            )
        
        # Train
        self._model.fit(X_train_scaled, y_train)
        self._is_trained = True
        
        # Evaluate
        y_pred = self._model.predict(X_test_scaled)
        y_proba = self._model.predict_proba(X_test_scaled)[:, 1]
        
        accuracy = float(accuracy_score(y_test, y_pred))
        precision = float(precision_score(y_test, y_pred, zero_division=0))
        recall = float(recall_score(y_test, y_pred, zero_division=0))
        f1 = float(f1_score(y_test, y_pred, zero_division=0))
        
        try:
            auc = float(roc_auc_score(y_test, y_proba))
        except ValueError:
            auc = 0.0  # Only one class in test set
        
        # Cross-validation (time-series aware)
        cv = TimeSeriesSplit(n_splits=3)
        cv_scores = []
        try:
            cv_result = cross_val_score(
                self._model, X_train_scaled, y_train,
                cv=cv, scoring="f1", n_jobs=-1,
            )
            cv_scores = [float(s) for s in cv_result]
        except Exception:
            pass  # CV may fail with small datasets
        
        # Feature importance
        feature_importance = {}
        if hasattr(self._model, "feature_importances_"):
            importances = self._model.feature_importances_
            for name, imp in sorted(
                zip(FEATURE_NAMES, importances),
                key=lambda x: x[1],
                reverse=True,
            ):
                feature_importance[name] = float(imp)
        elif hasattr(self._model, "coef_"):
            coefs = np.abs(self._model.coef_[0])
            for name, coef in sorted(
                zip(FEATURE_NAMES, coefs),
                key=lambda x: x[1],
                reverse=True,
            ):
                feature_importance[name] = float(coef)
        
        reason = (
            f"학습 완료: {self.model_type}, "
            f"정확도 {accuracy:.1%}, 정밀도 {precision:.1%}, "
            f"F1 {f1:.1%}, AUC {auc:.1%}, "
            f"샘플 {len(X)}개, 피처 {X.shape[1]}개"
        )
        
        logger.info(f"ML 모델 학습: {reason}")
        
        return TrainingResult(
            success=True,
            model_type=self.model_type,
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1=f1,
            auc=auc,
            cv_scores=cv_scores,
            feature_importance=feature_importance,
            n_samples=len(X),
            n_features=X.shape[1],
            reason=reason,
        )
    
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Predict probability of price rise.
        
        Args:
            X: Feature matrix (n_samples, n_features).
        
        Returns:
            Array of probabilities (0-1) for the "up" class.
        
        Raises:
            RuntimeError: If model is not trained.
        """
        if not self._is_trained or self._model is None:
            raise RuntimeError("모델이 학습되지 않았습니다")
        
        if self._scaler is not None:
            X = self._scaler.transform(X)
        
        return self._model.predict_proba(X)[:, 1]
    
    def save(self, path: str | Path | None = None) -> Path:
        """Save model to disk.
        
        Args:
            path: File path. Defaults to models/trained/price_rise_model.pkl.
        
        Returns:
            Path where model was saved.
        """
        if not self._is_trained:
            raise RuntimeError("학습되지 않은 모델은 저장할 수 없습니다")
        
        save_path = Path(path) if path else _MODEL_DIR / "price_rise_model.pkl"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        joblib.dump(
            {
                "model": self._model,
                "scaler": self._scaler,
                "model_type": self.model_type,
                "forward_days": self.forward_days,
                "forward_threshold": self.forward_threshold,
                "feature_names": FEATURE_NAMES,
            },
            save_path,
        )
        
        logger.info(f"ML 모델 저장: {save_path}")
        return save_path
    
    @classmethod
    def load(cls, path: str | Path | None = None) -> "PriceRiseModel":
        """Load a trained model from disk.
        
        Args:
            path: File path. Defaults to models/trained/price_rise_model.pkl.
        
        Returns:
            Loaded PriceRiseModel instance.
        """
        load_path = Path(path) if path else _MODEL_DIR / "price_rise_model.pkl"
        
        data = joblib.load(load_path)
        
        instance = cls(
            model_type=data["model_type"],
            forward_days=data["forward_days"],
            forward_threshold=data["forward_threshold"],
        )
        instance._model = data["model"]
        instance._scaler = data["scaler"]
        instance._is_trained = True
        
        logger.info(f"ML 모델 로드: {load_path}")
        return instance


def train_from_price_data(
    all_prices: list[list],
    codes: list[str],
    model_type: str = "random_forest",
    forward_days: int = 5,
    forward_threshold: float = 0.02,
) -> tuple[PriceRiseModel, TrainingResult]:
    """Convenience function: build dataset and train model in one call.
    
    Args:
        all_prices: List of price histories.
        codes: Stock codes.
        model_type: "logistic" or "random_forest".
        forward_days: Days ahead for target.
        forward_threshold: Min return to label as "up".
    
    Returns:
        Tuple of (trained model, training result).
    """
    X, y, feature_names = build_training_dataset(
        all_prices, codes, forward_days, forward_threshold
    )
    
    logger.info(f"학습 데이터셋 구축: {X.shape[0]} 샘플, {X.shape[1]} 피처")
    
    model = PriceRiseModel(model_type, forward_days, forward_threshold)
    result = model.train(X, y)
    
    return model, result
