"""NGSAT feature engineering — transforms price data into ML-ready features.

Takes raw PriceData and converts it to a numeric feature vector
suitable for scikit-learn / XGBoost models.

Every feature has a clear name and documented meaning.
Features are normalized/scaled where appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.types import PriceData
from strategy.indicators import (
    atr,
    bollinger_bands,
    macd,
    rsi,
    sma,
    stochastic,
    volume_ratio,
)


# ── Feature names (ordered) ──
FEATURE_NAMES: list[str] = [
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "ma5_distance_pct",
    "ma20_distance_pct",
    "ma60_distance_pct",
    "bollinger_position",
    "bollinger_width",
    "atr_pct",
    "volume_ratio_20",
    "stoch_k",
    "stoch_d",
    "price_change_1d",
    "price_change_5d",
    "price_change_10d",
    "price_change_20d",
    "volatility_20d",
    "return_skew_20d",
    "high_low_range_pct",
    # ── Enhanced features (optional, filled with 0 if unavailable) ──
    "foreign_net_buy_5d",
    "foreign_net_buy_20d",
    "institution_net_buy_5d",
    "institution_net_buy_20d",
    "per",
    "pbr",
    "eps",
]


@dataclass(frozen=True)
class FeatureVector:
    """ML feature vector for a single stock at a single point in time.

    Attributes:
        code: Stock code.
        features: Ordered dict of feature name → value.
        target: Future return (for training, None for inference).
        timestamp: When features were computed.
    """
    code: str
    features: dict[str, float] = field(default_factory=dict)
    target: float | None = None
    timestamp: str = ""


def build_features(
    prices: list[PriceData],
    code: str = "",
    forward_days: int = 5,
    include_target: bool = False,
    external_data: dict[str, Any] | None = None,
) -> FeatureVector | None:
    """Build a feature vector from price history.

    Args:
        prices: Historical price data (at least 60 days recommended).
        code: Stock code.
        forward_days: Number of days ahead for target calculation.
        include_target: Whether to compute the target (future return).
            True for training, False for inference.
        external_data: Optional dict with foreign/institution/financial data.
            Keys: foreign_net_buy_5d, foreign_net_buy_20d,
                  institution_net_buy_5d, institution_net_buy_20d,
                  per, pbr, eps
            If None or missing keys, defaults to 0.0 (backward compatible).

    Returns:
        FeatureVector or None if insufficient data.
    """
    if len(prices) < 60:
        return None

    df = pd.DataFrame([
        {
            "date": p.timestamp,
            "open": p.open,
            "high": p.high,
            "low": p.low,
            "close": p.close,
            "volume": p.volume,
        }
        for p in prices
    ])

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values.astype(float)

    features: dict[str, float] = {}

    # ── RSI ──
    rsi_vals = rsi(closes, 14)
    features["rsi_14"] = _safe_last(rsi_vals, 50.0)

    # ── MACD ──
    macd_line, signal_line, hist = macd(closes)
    features["macd_line"] = _safe_last(macd_line, 0.0)
    features["macd_signal"] = _safe_last(signal_line, 0.0)
    features["macd_histogram"] = _safe_last(hist, 0.0)

    # ── MA distances (% distance from current price to MA) ──
    current = float(closes[-1])

    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60) if len(closes) >= 60 else sma(closes, len(closes))

    ma5_val = _safe_last(ma5, current)
    ma20_val = _safe_last(ma20, current)
    ma60_val = _safe_last(ma60, current)

    features["ma5_distance_pct"] = ((current - ma5_val) / ma5_val * 100) if ma5_val > 0 else 0.0
    features["ma20_distance_pct"] = ((current - ma20_val) / ma20_val * 100) if ma20_val > 0 else 0.0
    features["ma60_distance_pct"] = ((current - ma60_val) / ma60_val * 100) if ma60_val > 0 else 0.0

    # ── Bollinger Bands ──
    upper, middle, lower = bollinger_bands(closes, 20, 2.0)
    bb_upper = _safe_last(upper, current)
    bb_lower = _safe_last(lower, current)
    bb_middle = _safe_last(middle, current)

    bb_width = bb_upper - bb_lower
    features["bollinger_position"] = (
        (current - bb_lower) / bb_width if bb_width > 0 else 0.5
    )
    features["bollinger_width"] = (bb_width / bb_middle * 100) if bb_middle > 0 else 0.0

    # ── ATR (volatility) ──
    atr_vals = atr(highs, lows, closes, 14)
    atr_val = _safe_last(atr_vals, 0.0)
    features["atr_pct"] = (atr_val / current * 100) if current > 0 else 0.0

    # ── Volume ratio ──
    vol_ratios = volume_ratio(volumes, 20)
    features["volume_ratio_20"] = _safe_last(vol_ratios, 1.0)

    # ── Stochastic ──
    k_vals, d_vals = stochastic(highs, lows, closes, 14, 3)
    features["stoch_k"] = _safe_last(k_vals, 50.0)
    features["stoch_d"] = _safe_last(d_vals, 50.0)

    # ── Price changes ──
    features["price_change_1d"] = _pct_change(closes, 1)
    features["price_change_5d"] = _pct_change(closes, 5)
    features["price_change_10d"] = _pct_change(closes, 10)
    features["price_change_20d"] = _pct_change(closes, 20)

    # ── Volatility (20-day std dev of daily returns) ──
    daily_returns = np.diff(closes) / closes[:-1]
    if len(daily_returns) >= 20:
        features["volatility_20d"] = float(np.std(daily_returns[-20:]) * 100)
    else:
        features["volatility_20d"] = 0.0

    # ── Return skewness (20-day) ──
    if len(daily_returns) >= 20:
        recent_returns = daily_returns[-20:]
        mean_r = np.mean(recent_returns)
        std_r = np.std(recent_returns)
        features["return_skew_20d"] = float(
            np.mean(((recent_returns - mean_r) / std_r) ** 3) if std_r > 0 else 0.0
        )
    else:
        features["return_skew_20d"] = 0.0

    # ── High-Low range ──
    recent_high = float(np.max(highs[-20:]))
    recent_low = float(np.min(lows[-20:]))
    features["high_low_range_pct"] = (
        (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0.0
    )

    # ── Enhanced features (외국인/기관/재무 — P1-3 활성화) ──
    # external_data에서 값을 가져오거나, 없으면 0.0 (backward compatible)
    ext = external_data or {}
    features["foreign_net_buy_5d"] = float(ext.get("foreign_net_buy_5d", 0.0))
    features["foreign_net_buy_20d"] = float(ext.get("foreign_net_buy_20d", 0.0))
    features["institution_net_buy_5d"] = float(ext.get("institution_net_buy_5d", 0.0))
    features["institution_net_buy_20d"] = float(ext.get("institution_net_buy_20d", 0.0))
    features["per"] = float(ext.get("per", 0.0))
    features["pbr"] = float(ext.get("pbr", 0.0))
    features["eps"] = float(ext.get("eps", 0.0))

    # ── Target (for training) ──
    target = None
    if include_target and len(prices) > forward_days:
        future_close = float(closes[-1 + forward_days]) if len(closes) > forward_days else None
        # Actually, for training we need to look forward from each point
        # This is handled in build_training_dataset

    timestamp = str(prices[-1].timestamp) if prices else ""

    return FeatureVector(
        code=code,
        features=features,
        target=target,
        timestamp=timestamp,
    )


def build_training_dataset(
    all_prices: list[list[PriceData]],
    codes: list[str],
    forward_days: int = 5,
    forward_threshold: float = 0.02,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build a training dataset from multiple stocks' price history.

    Creates feature vectors at each historical point and labels them
    based on whether the stock rose more than `forward_threshold`
    in the next `forward_days`.

    Args:
        all_prices: List of price histories (one per stock).
        codes: Stock codes corresponding to each price history.
        forward_days: Number of days to look ahead for labeling.
        forward_threshold: Minimum return to label as "up" (default 2%).

    Returns:
        Tuple of (X, y, feature_names) where:
        - X: numpy array of shape (n_samples, n_features)
        - y: numpy array of 0/1 labels (1 = price went up)
        - feature_names: list of feature column names
    """
    X_list: list[list[float]] = []
    y_list: list[int] = []

    for prices, code in zip(all_prices, codes):
        if len(prices) < 60 + forward_days:
            continue

        closes = np.array([p.close for p in prices], dtype=float)
        highs = np.array([p.high for p in prices], dtype=float)
        lows = np.array([p.low for p in prices], dtype=float)
        volumes = np.array([p.volume for p in prices], dtype=float)

        # Build features at each point where we can compute a target
        for i in range(60, len(prices) - forward_days):
            slice_prices = prices[:i + 1]
            fv = build_features(slice_prices, code=code, include_target=False)

            if fv is None or len(fv.features) != len(FEATURE_NAMES):
                continue

            # Target: did price rise more than threshold in next `forward_days`?
            future_return = (closes[i + forward_days] - closes[i]) / closes[i]
            label = 1 if future_return > forward_threshold else 0

            X_list.append([fv.features[name] for name in FEATURE_NAMES])
            y_list.append(label)

    if not X_list:
        return np.array([]).reshape(0, len(FEATURE_NAMES)), np.array([]), FEATURE_NAMES

    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=int)

    # Replace NaN/inf with 0
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    return X, y, FEATURE_NAMES


# ── Helpers ──

def _safe_last(arr: np.ndarray, default: float = 0.0) -> float:
    """Get the last non-NaN value from an array, or default."""
    if len(arr) == 0:
        return default
    val = float(arr[-1])
    return val if not np.isnan(val) else default


def _pct_change(values: np.ndarray, days: int) -> float:
    """Calculate percentage change over N days."""
    if len(values) <= days:
        return 0.0
    past = float(values[-1 - days])
    current = float(values[-1])
    if past == 0:
        return 0.0
    return (current - past) / past * 100
