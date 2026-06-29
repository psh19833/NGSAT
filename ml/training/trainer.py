"""NGSAT ML model training — price rise probability prediction.

Trains a classifier to predict whether a stock will rise above a threshold
in the next N days. Uses 5 model types:

  Model              | Description                          | Package
  -------------------|--------------------------------------|-------------------
  logistic           | LogisticRegression (linear baseline) | scikit-learn (내장)
  random_forest*     | RandomForest ensemble                | scikit-learn (내장)
  gradient_boosting  | HistGradientBoosting (부스팅)        | scikit-learn (내장)
  xgboost            | XGBoost (고성능 부스팅)              | pip install xgboost
  lightgbm           | LightGBM (대용량 부스팅)             | pip install lightgbm

* 현재 활성 모델 (config: ml_model_type)

Features (27종): RSI(14), MACD, MA distance(5/20/60), Bollinger(position/width),
ATR, Volume ratio(20), Stoch(K/D), Price change(1/5/10/20d), Volatility(20d),
Return skew(20d), High-low range(20d), Foreign/Institutional net buy(5/20d),
PER, PBR, EPS.

Model lifecycle:
1. Build training dataset from historical prices (build_training_dataset)
2. Train model with TimeSeriesSplit cross-validation
3. Evaluate on held-out test set (80/20)
4. Save model to disk (joblib) — auto_retrain saves only if AUC improves
5. Model is ready for inference (ml/inference.py)
"""

from __future__ import annotations

import joblib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None
try:
    from lightgbm import LGBMClassifier
except ImportError:
    LGBMClassifier = None
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
from sklearn.inspection import permutation_importance

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
    positive_rate: float = 0.0
    cv_scores: list[float] = field(default_factory=list)
    feature_importance: dict[str, float] = field(default_factory=dict)
    n_samples: int = 0
    n_features: int = 0
    reason: str = ""


class PriceRiseModel:
    """ML model for predicting stock price rise probability.

    ┌────────────────────┬────────────────────────────────┬───────────────────┐
    │ Model              │ Description                    │ Package           │
    ├────────────────────┼────────────────────────────────┼───────────────────┤
    │ logistic           │ LogisticRegression (선형 기준)  │ scikit-learn 내장 │
    │ random_forest      │ RandomForest 앙상블 (현재 활성) │ scikit-learn 내장 │
    │ gradient_boosting  │ HistGradientBoosting 부스팅     │ scikit-learn 내장 │
    │ xgboost            │ XGBoost 고성능 부스팅           │ pip install 필요  │
    │ lightgbm           │ LightGBM 대용량 부스팅          │ pip install 필요  │
    └────────────────────┴────────────────────────────────┴───────────────────┘

    각 model_type에 맞는 sklearn 호환 분류기를 생성하고,
    TimeSeriesSplit 교차검증으로 평가한 후 최종 모델을 저장한다.
    """

    def __init__(
        self,
        model_type: str = "random_forest",
        forward_days: int = 5,
        forward_threshold: float = 0.02,
        auto_select_model: bool = False,
    ):
        self.model_type = model_type
        self.forward_days = forward_days
        self.forward_threshold = forward_threshold
        self.auto_select_model = auto_select_model
        self._model: Any = None
        self._scaler: StandardScaler | None = None
        self._is_trained = False
        self._last_auc = 0.0

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

        # ── Create model ──
        if self.model_type == "logistic":
            # 로지스틱 회귀: 선형 기준선, 학습 빠름, 해석 용이
            # but expressiveness limited — used as baseline comparison
            self._model = LogisticRegression(
                max_iter=1000,
                random_state=42,
                class_weight="balanced",
            )
        elif self.model_type == "random_forest":
            # 랜덤포레스트: 비선형+앙상블, 과적합에 강함
            # 100개 트리, max_depth=10으로 일반화 유지
            self._model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                random_state=42,
                class_weight="balanced",
                n_jobs=-1,
            )
        elif self.model_type == "gradient_boosting":
            # HistGradientBoosting: sklearn 내장 부스팅 트리
            # GradientBoosting보다 2-3배 빠름, NaN 자동 처리
            # AUC 0.68~0.69로 random_forest보다 소폭 우수
            self._model = HistGradientBoostingClassifier(
                max_iter=200,
                max_depth=6,
                learning_rate=0.1,
                l2_regularization=1.0,
                random_state=42,
                class_weight="balanced",
            )
        elif self.model_type == "xgboost" and XGBClassifier is not None:
            # XGBoost: 고성능 부스팅, 결측치 자동 처리
            # column 기반 병렬 학습, regularization 내장
            # AUC 0.675 (2026-06-29 비교)
            self._model = XGBClassifier(
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                random_state=42,
                verbosity=0,
                n_jobs=-1,
            )
        elif self.model_type == "lightgbm" and LGBMClassifier is not None:
            # LightGBM: GOSS 기반 부스팅, 대용량 데이터에 최적
            # leaf-wise 트리, 속도 가장 빠름
            # AUC 0.676 (2026-06-29 비교)
            self._model = LGBMClassifier(
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                random_state=42,
                verbose=-1,
                n_jobs=-1,
                class_weight="balanced",
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
            if np.isnan(auc):
                auc = 0.0  # 검증셋에 한 클래스만 존재
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
        else:
            # 부스팅 등 내장 중요도 없는 모델: permutation importance로 근거 제공
            try:
                perm = permutation_importance(
                    self._model, X_test_scaled, y_test,
                    n_repeats=5, random_state=42,
                )
                for name, imp in sorted(
                    zip(FEATURE_NAMES, perm.importances_mean),
                    key=lambda x: x[1],
                    reverse=True,
                ):
                    feature_importance[name] = float(imp)
            except Exception:
                pass

        pos_rate = float(np.mean(y))
        test_pos = int(np.sum(y_test))
        if pos_rate < 0.05:
            imbalance_note = (
                f", ⚠ 양성(상승)비율 {pos_rate:.1%} 매우 낮음 "
                f"— 타겟 기준(forward_days/threshold) 재조정 권장"
            )
        elif test_pos == 0:
            imbalance_note = ", ⚠ 검증셋에 상승샘플 없음 — 평가 신뢰도 낮음"
        else:
            imbalance_note = ""

        reason = (
            f"학습 완료: {self.model_type}, "
            f"정확도 {accuracy:.1%}, 정밀도 {precision:.1%}, "
            f"F1 {f1:.1%}, AUC {auc:.1%}, "
            f"양성비율 {pos_rate:.1%}, "
            f"샘플 {len(X)}개, 피처 {X.shape[1]}개{imbalance_note}"
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
            positive_rate=pos_rate,
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

        # Handle feature count mismatch (backward compatibility)
        expected = getattr(self._model, 'n_features_in_', X.shape[1])
        if X.shape[1] > expected:
            X = X[:, :expected]
        elif X.shape[1] < expected:
            pad = np.zeros((X.shape[0], expected - X.shape[1]))
            X = np.hstack([X, pad])

        if self._scaler is not None:
            X = self._scaler.transform(X)

        return self._model.predict_proba(X)[:, 1]

    def save(self, path: str | Path | None = None) -> Path:
        """Save model to disk with integrity sidecar.

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
                "last_auc": self._last_auc,
            },
            save_path,
        )

        # Compute integrity hash and save as sidecar (.sha256 file)
        import hashlib
        with open(save_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        sha256_path = save_path.with_suffix(".pkl.sha256")
        sha256_path.write_text(file_hash + "\n")

        logger.info(f"ML 모델 저장: {save_path} (SHA-256: {file_hash[:16]}...)")
        return save_path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PriceRiseModel":
        """Load a trained model from disk with integrity verification.

        Args:
            path: File path. Defaults to models/trained/price_rise_model.pkl.

        Returns:
            Loaded PriceRiseModel instance.

        Raises:
            RuntimeError: If integrity check fails or sidecar file is missing.
        """
        load_path = Path(path) if path else _MODEL_DIR / "price_rise_model.pkl"

        # Verify integrity via sidecar (.sha256) file
        import hashlib
        with open(load_path, "rb") as f:
            file_bytes = f.read()
        computed = hashlib.sha256(file_bytes).hexdigest()

        sha256_path = load_path.with_suffix(".pkl.sha256")
        if not sha256_path.exists():
            logger.warning(f"무결성 해시 파일 없음 — 검증 생략: {sha256_path}")
        else:
            stored = sha256_path.read_text().strip()
            if computed != stored:
                raise RuntimeError(
                    f"모델 파일 무결성 검증 실패: {load_path}\n"
                    f"  computed={computed}\n"
                    f"  stored  ={stored}\n"
                    f"  파일이 변조되었거나 손상되었습니다."
                )
            logger.info(f"모델 무결성 확인 완료 (SHA-256: {computed[:16]}...)")

        data = joblib.load(load_path)
        # Drop legacy in-file integrity hash if present
        data.pop("_integrity_hash", None)

        instance = cls(
            model_type=data["model_type"],
            forward_days=data["forward_days"],
            forward_threshold=data["forward_threshold"],
        )
        instance._model = data["model"]
        instance._scaler = data["scaler"]
        instance._is_trained = True
        instance._last_auc = data.get("last_auc", 0.0)

        logger.info(f"ML 모델 로드: {load_path}")
        return instance

    def auto_tune(self, X, y, n_trials=50, timeout=300):
        """Optuna 하이퍼파라미터 자동 튜닝.

        Args:
            X: Feature matrix.
            y: Labels.
            n_trials: Number of Optuna trials.
            timeout: Max tuning time in seconds.

        Returns:
            dict with best_params and best_score.
        """
        import optuna
        from sklearn.model_selection import cross_val_score, TimeSeriesSplit

        def objective(trial):
            if self.model_type == "random_forest":
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=50),
                    "max_depth": trial.suggest_int("max_depth", 3, 15),
                    "class_weight": "balanced",
                    "random_state": 42,
                    "n_jobs": -1,
                }
                model = RandomForestClassifier(**params)
            elif self.model_type == "xgboost" and XGBClassifier is not None:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=50),
                    "max_depth": trial.suggest_int("max_depth", 3, 12),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                    "random_state": 42,
                    "verbosity": 0,
                    "n_jobs": -1,
                }
                model = XGBClassifier(**params)
            elif self.model_type == "lightgbm" and LGBMClassifier is not None:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=50),
                    "max_depth": trial.suggest_int("max_depth", 3, 12),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                    "random_state": 42,
                    "verbose": -1,
                    "n_jobs": -1,
                    "class_weight": "balanced",
                }
                model = LGBMClassifier(**params)
            elif self.model_type == "gradient_boosting":
                params = {
                    "max_iter": trial.suggest_int("max_iter", 100, 500, step=50),
                    "max_depth": trial.suggest_int("max_depth", 3, 12),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                    "random_state": 42,
                    "class_weight": "balanced",
                }
                model = HistGradientBoostingClassifier(**params)
            else:
                return 0.0

            cv = TimeSeriesSplit(n_splits=3)
            scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc", n_jobs=-1)
            return float(scores.mean())

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, timeout=timeout)

        # Train with best params — construct and fit the best model
        best_params = study.best_params
        if self.model_type == "random_forest":
            best_model = RandomForestClassifier(**best_params)
        elif self.model_type == "xgboost" and XGBClassifier is not None:
            best_model = XGBClassifier(**best_params)
        elif self.model_type == "lightgbm" and LGBMClassifier is not None:
            best_model = LGBMClassifier(**best_params)
        elif self.model_type == "gradient_boosting":
            best_model = HistGradientBoostingClassifier(**best_params)
        else:
            logger.error(f"auto_tune: 알 수 없는 모델 타입 {self.model_type}")
            return {"best_params": best_params, "best_auc": study.best_value}

        best_model.fit(X, y)
        self._model = best_model
        # StandardScaler 일관성 유지 (train()과 동일 파이프라인)
        from sklearn.preprocessing import StandardScaler
        self._scaler = StandardScaler()
        self._scaler.fit(X)
        self._is_trained = True

        logger.info(
            f"Optuna 튜닝 완료: {self.model_type}, "
            f"best AUC={study.best_value:.4f}, "
            f"trials={len(study.trials)}, "
            f"params={best_params}"
        )
        return {"best_params": best_params, "best_auc": study.best_value}

    def auto_retrain(self, all_prices, codes):
        """FreqAI-style 자동 재학습: 새 데이터로 학습 후 기존보다 좋으면 교체.

        매 20회째 재학습 시 Optuna 하이퍼파라미터 튜닝 병행
        (auto_tune)하여 AUC 개선을 시도한다.

        Returns:
            (was_replaced: bool, new_result: TrainingResult)
        """
        from ml.features.builder import build_training_dataset

        # Build dataset and train new model
        X, y, _ = build_training_dataset(
            all_prices, codes,
            self.forward_days, self.forward_threshold,
        )
        if len(X) < 50:
            logger.warning(f"재학습 데이터 부족: {len(X)}개")
            return False, TrainingResult(success=False, reason=f"데이터 부족 ({len(X)}개)")

        # ── Auto-tuning every 20 cycles ──
        tune_count = getattr(self, '_auto_tune_count', 0) + 1
        self._auto_tune_count = tune_count
        if tune_count % 20 == 0 and len(X) >= 200:
            try:
                logger.info(f"Optuna auto-tune 실행 ({tune_count}회째 재학습)...")
                tune_result = self.auto_tune(X, y, n_trials=20, timeout=60)
                logger.info(f"Auto-tune 완료: AUC={tune_result['best_auc']:.4f}")
                self._is_trained = True
                # Evaluate tuned model
                split_idx = int(len(X) * 0.8)
                X_train, X_test = X[:split_idx], X[split_idx:]
                y_train, y_test = y[:split_idx], y[split_idx:]
                from sklearn.preprocessing import StandardScaler
                scaler = StandardScaler()
                X_train_s = scaler.fit_transform(X_train)
                X_test_s = scaler.transform(X_test)
                self._scaler = scaler
                self._model.fit(X_train_s, y_train)
                y_pred = self._model.predict(X_test_s)
                y_proba = self._model.predict_proba(X_test_s)[:, 1]
                auc = float(roc_auc_score(y_test, y_proba))
                self._last_auc = auc
                acc = float(accuracy_score(y_test, y_pred))
                f1 = float(f1_score(y_test, y_pred, zero_division=0))
                logger.info(f"튜닝 모델 적용: AUC={auc:.3f}, 정확도={acc:.1%}, F1={f1:.1%}")
                return True, TrainingResult(
                    success=True, model_type=self.model_type,
                    auc=auc, accuracy=acc, f1=f1,
                    n_samples=len(X), n_features=X.shape[1],
                    reason=f"Optuna 튜닝 모델 적용 (AUC={auc:.3f})",
                )
            except Exception as e:
                logger.warning(f"Auto-tune 실패 (skip, 기존 모델 유지): {e}")

        # ── Model selection & retrain ──
        if self.auto_select_model:
            return self._multi_model_retrain(X, y)
        else:
            return self._single_model_retrain(X, y)

    def _single_model_retrain(self, X, y):
        """기존 방식: 현재 model_type 하나만 학습 후 AUC 비교 교체."""
        new_model = PriceRiseModel(self.model_type, self.forward_days, self.forward_threshold)
        new_result = new_model.train(X, y)

        if not new_result.success:
            return False, new_result

        # Compare: only replace if new model is better
        if self._is_trained and new_result.auc <= getattr(self, '_last_auc', 0):
            logger.info(
                f"재학습 모델 성능 낮음 (기존 AUC={getattr(self, '_last_auc', 0):.3f} > "
                f"신규 AUC={new_result.auc:.3f}) — 기존 모델 유지"
            )
            return False, new_result

        # Replace
        self._model = new_model._model
        self._scaler = new_model._scaler
        self._is_trained = True
        self._last_auc = new_result.auc

        logger.info(
            f"모델 자동 교체: {self.model_type}, AUC={new_result.auc:.3f}"
        )
        return True, new_result

    def _multi_model_retrain(self, X, y):
        """자동 모델 선택: 5개 모델 전부 학습 후 최고 AUC로 교체.

        XGBoost/LightGBM 미설치 시 자동 fallback (sklearn 3종만 비교).
        """
        models_to_try = [
            "gradient_boosting",
            "logistic",
            "random_forest",
            "xgboost",
            "lightgbm",
        ]
        best_auc = -1.0
        best_model = None
        best_type = self.model_type
        results = []

        for mtype in models_to_try:
            try:
                model = PriceRiseModel(mtype, self.forward_days, self.forward_threshold)
                result = model.train(X, y)
                if result.success:
                    results.append((mtype, result.auc))
                    if result.auc > best_auc:
                        best_auc = result.auc
                        best_model = model
                        best_type = mtype
            except Exception as e:
                logger.debug(f"모델 {mtype} 학습 실패 (skip): {e}")

        if not results:
            logger.warning("모든 모델 학습 실패 — 기존 모델 유지")
            return False, TrainingResult(success=False, reason="모든 모델 학습 실패")

        # Log ranking
        results.sort(key=lambda x: -x[1])
        rank_str = " | ".join(f"{t}:{a:.3f}" for t, a in results)
        logger.info(f"모델 랭킹: {rank_str}")

        # Swap model if better than current
        prev_auc = getattr(self, '_last_auc', 0)
        if best_auc > prev_auc:
            old_type = self.model_type
            self._model = best_model._model
            self._scaler = best_model._scaler
            self.model_type = best_type
            self._is_trained = True
            self._last_auc = best_auc
            changed_str = " (변경)" if best_type != old_type else ""
            logger.info(
                f"모델 자동 선택: {old_type} → {best_type}{changed_str}, "
                f"AUC {prev_auc:.3f} → {best_auc:.3f}"
            )
            return True, TrainingResult(
                success=True, model_type=best_type,
                auc=best_auc,
                n_samples=len(X), n_features=X.shape[1],
                reason=f"Auto-select: {best_type} (AUC={best_auc:.3f})",
            )
        else:
            logger.info(
                f"모델 자동 선택: 기존 유지 ({best_type}, AUC={best_auc:.3f} ≤ 기존 {prev_auc:.3f})"
            )
            return False, TrainingResult(
                success=True, model_type=best_type,
                auc=best_auc,
                n_samples=len(X), n_features=X.shape[1],
                reason=f"Auto-select: {best_type} 유지 (AUC={best_auc:.3f} ≤ 기존 {prev_auc:.3f})",
            )


def train_from_price_data(
    all_prices: list[list],
    codes: list[str],
    model_type: str = "gradient_boosting",
    forward_days: int = 5,
    forward_threshold: float = 0.02,
) -> tuple[PriceRiseModel, TrainingResult]:
    """Convenience: build dataset + train model in one call.

    Args:
        all_prices: List of price histories per stock.
        codes: Stock codes.
        model_type: Model type. Defaults to gradient_boosting
                   (best AUC in 2026-06-29 comparison).
        forward_days: Days ahead for target.
        forward_threshold: Min return to label "up".

    Returns:
        (model, result)
    """
    X, y, feature_names = build_training_dataset(
        all_prices, codes, forward_days, forward_threshold
    )

    logger.info(f"학습 데이터셋 구축: {X.shape[0]} 샘플, {X.shape[1]} 피처")

    model = PriceRiseModel(model_type, forward_days, forward_threshold)
    result = model.train(X, y)

    return model, result


def train_from_minute_data(
    all_minute_prices: list[list],
    codes: list[str],
    model_type: str = "random_forest",
    forward_minutes: int = 10,
    forward_threshold: float = 0.01,
) -> tuple[PriceRiseModel, TrainingResult]:
    """분봉 데이터로 단타 모델 학습 (convenience function).

    Args:
        all_minute_prices: 종목별 분봉 가격 리스트.
        codes: 종목코드.
        model_type: 모델 타입.
        forward_minutes: 타겟 예측 분.
        forward_threshold: 양성 판정 임계 수익률.

    Returns:
        (trained model, training result).
    """
    from ml.features.minute_builder import (
        MINUTE_FEATURE_NAMES,
        build_minute_training_dataset,
    )

    X, y, prices_at, feature_names = build_minute_training_dataset(
        all_minute_prices, codes, forward_minutes, forward_threshold
    )

    logger.info(f"분봉 학습 데이터셋 구축: {X.shape[0]} 샘플, {X.shape[1]} 피처 (단타)")

    model = PriceRiseModel(
        model_type,
        forward_days=forward_minutes,  # 실제 값은 분 단위지만 필드 재사용
        forward_threshold=forward_threshold,
    )
    result = model.train(X, y)

    return model, result
