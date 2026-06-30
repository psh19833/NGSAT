"""NGSAT ML model selection — auto-tuning and model comparison.

Extracted from trainer.py in BE-15 refactoring.
Handles Optuna hyperparameter tuning and multi-model selection.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

from core.logger import logger

# Optional XGBoost / LightGBM
try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None

try:
    from lightgbm import LGBMClassifier
except ImportError:
    LGBMClassifier = None

from ml.training.trainer import PriceRiseModel, TrainingResult, FEATURE_NAMES


def auto_tune(model: PriceRiseModel, X: np.ndarray, y: np.ndarray, n_trials: int = 50, timeout: int = 300) -> dict:
    """Optuna 하이퍼파라미터 자동 튜닝.

    Args:
        model: PriceRiseModel instance (for model_type reference).
        X: Feature matrix.
        y: Labels.
        n_trials: Number of Optuna trials.
        timeout: Max tuning time in seconds.

    Returns:
        dict with best_params and best_score.
    """
    import optuna

    def objective(trial):
        if model.model_type == "random_forest":
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=50),
                "max_depth": trial.suggest_int("max_depth", 3, 15),
                "class_weight": "balanced",
                "random_state": 42,
                "n_jobs": -1,
            }
            m = RandomForestClassifier(**params)
        elif model.model_type == "xgboost" and XGBClassifier is not None:
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=50),
                "max_depth": trial.suggest_int("max_depth", 3, 12),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "random_state": 42,
                "verbosity": 0,
                "n_jobs": -1,
            }
            m = XGBClassifier(**params)
        elif model.model_type == "lightgbm" and LGBMClassifier is not None:
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 50, 300, step=50),
                "max_depth": trial.suggest_int("max_depth", 3, 12),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "random_state": 42,
                "verbose": -1,
                "n_jobs": -1,
                "class_weight": "balanced",
            }
            m = LGBMClassifier(**params)
        elif model.model_type == "gradient_boosting":
            params = {
                "max_iter": trial.suggest_int("max_iter", 100, 500, step=50),
                "max_depth": trial.suggest_int("max_depth", 3, 12),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "random_state": 42,
                "class_weight": "balanced",
            }
            m = HistGradientBoostingClassifier(**params)
        else:
            return 0.0

        cv = TimeSeriesSplit(n_splits=3)
        scores = cross_val_score(m, X, y, cv=cv, scoring="roc_auc", n_jobs=-1)
        return float(scores.mean())

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, timeout=timeout)

    best_params = study.best_params
    if model.model_type == "random_forest":
        best_model = RandomForestClassifier(**best_params)
    elif model.model_type == "xgboost" and XGBClassifier is not None:
        best_model = XGBClassifier(**best_params)
    elif model.model_type == "lightgbm" and LGBMClassifier is not None:
        best_model = LGBMClassifier(**best_params)
    elif model.model_type == "gradient_boosting":
        best_model = HistGradientBoostingClassifier(**best_params)
    else:
        logger.error(f"auto_tune: 알 수 없는 모델 타입 {model.model_type}")
        return {"best_params": best_params, "best_auc": study.best_value}

    best_model.fit(X, y)
    model._model = best_model
    scaler = StandardScaler()
    scaler.fit(X)
    model._scaler = scaler
    model._is_trained = True

    logger.info(
        f"Optuna 튜닝 완료: {model.model_type}, "
        f"best AUC={study.best_value:.4f}, "
        f"trials={len(study.trials)}, "
        f"params={best_params}"
    )
    return {"best_params": best_params, "best_auc": study.best_value}


def single_model_retrain(model: PriceRiseModel, X: np.ndarray, y: np.ndarray):
    """단일 모델 재학습 후 AUC 비교. 기존보다 좋으면 교체.

    Args:
        model: Current PriceRiseModel instance.
        X: Feature matrix.
        y: Labels.

    Returns:
        (was_replaced: bool, new_result: TrainingResult)
    """
    new_model = PriceRiseModel(model.model_type, model.forward_days, model.forward_threshold)
    new_result = new_model.train(X, y)

    if not new_result.success:
        return False, TrainingResult(
            success=False,
            reason=f"재학습 실패: {new_result.reason}",
        )

    # Compare AUC — replace only if better
    if new_result.auc > model._last_auc:
        model._model = new_model._model
        model._scaler = new_model._scaler
        model._is_trained = True
        model._last_auc = new_result.auc
        logger.info(
            f"재학습 교체: {model.model_type}, "
            f"AUC {model._last_auc:.3f} → {new_result.auc:.3f}"
        )
        return True, new_result
    else:
        logger.info(
            f"재학습 유지: 새 AUC {new_result.auc:.3f} ≤ 기존 {model._last_auc:.3f}"
        )
        return False, TrainingResult(
            success=True,
            model_type=model.model_type,
            auc=model._last_auc,
            reason=f"기존 모델 유지 (AUC {model._last_auc:.3f} ≥ {new_result.auc:.3f})",
        )


def multi_model_retrain(model: PriceRiseModel, X: np.ndarray, y: np.ndarray):
    """5개 모델 전부 학습 후 최고 AUC 모델 선택.

    Args:
        model: Current PriceRiseModel instance.
        X: Feature matrix.
        y: Labels.

    Returns:
        (was_replaced: bool, new_result: TrainingResult)
    """
    model_types = ["logistic", "random_forest", "gradient_boosting", "xgboost", "lightgbm"]
    results: list[tuple[str, TrainingResult]] = []
    errors: list[str] = []

    for mt in model_types:
        if mt in ("xgboost", "lightgbm"):
            pkg = XGBClassifier if mt == "xgboost" else LGBMClassifier
            if pkg is None:
                errors.append(f"{mt} not installed — skip")
                continue
        try:
            new_m = PriceRiseModel(mt, model.forward_days, model.forward_threshold)
            r = new_m.train(X, y)
            if r.success:
                results.append((mt, r))
            else:
                errors.append(f"{mt} failed: {r.reason}")
        except Exception as e:
            errors.append(f"{mt} error: {e}")

    if not results:
        return False, TrainingResult(
            success=False,
            reason=f"모든 모델 학습 실패: {'; '.join(errors)}",
        )

    # Pick best by AUC
    best_mt, best_result = max(results, key=lambda x: x[1].auc)

    logger.info(
        f"멀티모델 선택: {best_mt} (AUC={best_result.auc:.3f}), "
        f"후보={[f'{mt}({r.auc:.3f})' for mt, r in results]}"
    )

    if best_result.auc > model._last_auc or model.model_type != best_mt:
        # Train best model type from scratch with full data
        final = PriceRiseModel(best_mt, model.forward_days, model.forward_threshold)
        final.train(X, y)
        model._model = final._model
        model._scaler = final._scaler
        model._is_trained = True
        model._last_auc = best_result.auc
        model.model_type = best_mt
        model.auto_select_model = True
        logger.info(f"모델 교체: {best_mt} (AUC={best_result.auc:.3f})")
        return True, TrainingResult(
            success=True,
            model_type=best_mt,
            auc=best_result.auc,
            reason=f"멀티모델 선택: {best_mt} AUC={best_result.auc:.3f}",
        )

    return False, TrainingResult(
        success=True,
        model_type=best_mt,
        auc=best_result.auc,
        reason=f"기존 모델 유지 (현재 {model._last_auc:.3f}, 최고 {best_result.auc:.3f}, {best_mt})",
    )
