"""NGSAT 분봉 피처 엔지니어링 — 단타(Short-term) 모드용.

일봉 피처(builder.py)와 동일한 원칙을 따르되, 분봉 데이터에 적합한
피처와 타겟을 정의한다.

타겟: N분 뒤 가격이 X% 이상 상승하는가 (이진 분류)
- 기본: 10분 뒤 1% 이상 상승 → 양성
- 단타 모드는 빠른 진입/청산이 목적이므로 타겟 기간이 짧다

피처 (20종):
- RSI (5/14/30분)
- MACD (12/26/9)
- 볼린저밴드 위치
- ATR (%)
- 거래량 급등 (최근 N분 대비)
- 가격 가속도 (ROC)
- 단기 모멘텀 (1/3/5/10분)
- 변동성 (최근 N분 표준편차)
- candlestick 패턴 (몸통/그림자 비율)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from core.types import PriceData
from strategy.indicators import (
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
    volume_ratio,
)

# ── Feature names (ordered) ──
MINUTE_FEATURE_NAMES: list[str] = [
    "m_rsi_5",
    "m_rsi_14",
    "m_rsi_30",
    "m_macd_line",
    "m_macd_signal",
    "m_macd_histogram",
    "m_bollinger_position",
    "m_bollinger_width",
    "m_atr_pct",
    "m_volume_spike_5",
    "m_volume_spike_20",
    "m_roc_1",
    "m_roc_3",
    "m_roc_5",
    "m_roc_10",
    "m_momentum_3",
    "m_momentum_5",
    "m_volatility_10",
    "m_volatility_20",
    "m_candle_body_pct",
    "m_high_low_range_pct",
    "m_price_acceleration",
    "m_consecutive_up",
    "m_consecutive_down",
    "m_vwap_distance_pct",
]


@dataclass(frozen=True)
class MinuteFeatureVector:
    """분봉 ML 피처 벡터.

    Attributes:
        code: 종목코드.
        features: 피처명→값 dict.
        target: N분 뒤 수익률 (학습 시, 추론 시 None).
        timestamp: 피처 기준 시각.
    """
    code: str
    features: dict[str, float] = field(default_factory=dict)
    target: float | None = None
    timestamp: str = ""


def build_minute_features(
    prices: list[PriceData],
    code: str = "",
    forward_minutes: int = 10,
    forward_threshold: float = 0.01,
    include_target: bool = False,
) -> MinuteFeatureVector | None:
    """분봉 데이터로 피처 벡터 생성.

    Args:
        prices: 분봉 PriceData 리스트 (최소 60개 권장).
        code: 종목코드.
        forward_minutes: 타겟 예측 분 (기본 10분).
        forward_threshold: 양성 판정 임계 수익률 (기본 1%).
        include_target: 타겟 포함 여부.

    Returns:
        MinuteFeatureVector 또는 None (데이터 부족 시).
    """
    if len(prices) < 30:
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

    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    opens = df["open"].values.astype(float)
    volumes = df["volume"].values.astype(float)

    current = float(closes[-1])
    features: dict[str, float] = {}

    # ── RSI (5/14/30) ──
    for period, name in [(5, "m_rsi_5"), (14, "m_rsi_14"), (30, "m_rsi_30")]:
        vals = rsi(closes, period)
        features[name] = _safe_last(vals, 50.0)

    # ── MACD ──
    macd_line, signal_line, hist = macd(closes, 12, 26, 9)
    features["m_macd_line"] = _safe_last(macd_line, 0.0)
    features["m_macd_signal"] = _safe_last(signal_line, 0.0)
    features["m_macd_histogram"] = _safe_last(hist, 0.0)

    # ── Bollinger Bands ──
    upper, middle, lower = bollinger_bands(closes, 20, 2.0)
    bb_upper = _safe_last(upper, current)
    bb_lower = _safe_last(lower, current)
    bb_middle = _safe_last(middle, current)
    bb_width = bb_upper - bb_lower
    features["m_bollinger_position"] = (
        (current - bb_lower) / bb_width if bb_width > 0 else 0.5
    )
    features["m_bollinger_width"] = (bb_width / bb_middle * 100) if bb_middle > 0 else 0.0

    # ── ATR ──
    atr_vals = atr(highs, lows, closes, 14)
    atr_val = _safe_last(atr_vals, 0.0)
    features["m_atr_pct"] = (atr_val / current * 100) if current > 0 else 0.0

    # ── Volume spike (5/20분 대비) ──
    vol_5 = volume_ratio(volumes, 5)
    vol_20 = volume_ratio(volumes, 20)
    features["m_volume_spike_5"] = _safe_last(vol_5, 1.0)
    features["m_volume_spike_20"] = _safe_last(vol_20, 1.0)

    # ── ROC (Rate of Change: 1/3/5/10분) ──
    features["m_roc_1"] = _pct_change(closes, 1)
    features["m_roc_3"] = _pct_change(closes, 3)
    features["m_roc_5"] = _pct_change(closes, 5)
    features["m_roc_10"] = _pct_change(closes, 10)

    # ── 단기 모멘텀 (EMA crossover 근사) ──
    ema3 = ema(closes, 3)
    ema5 = ema(closes, 5)
    ema10 = ema(closes, 10)
    ema3_val = _safe_last(ema3, current)
    ema5_val = _safe_last(ema5, current)
    ema10_val = _safe_last(ema10, current)
    features["m_momentum_3"] = ((ema3_val - ema10_val) / ema10_val * 100) if ema10_val > 0 else 0.0
    features["m_momentum_5"] = ((ema5_val - ema10_val) / ema10_val * 100) if ema10_val > 0 else 0.0

    # ── 변동성 (최근 10/20분 표준편차) ──
    if len(closes) >= 10:
        features["m_volatility_10"] = float(np.std(closes[-10:]) / np.mean(closes[-10:]) * 100) if np.mean(closes[-10:]) > 0 else 0.0
    else:
        features["m_volatility_10"] = 0.0
    if len(closes) >= 20:
        features["m_volatility_20"] = float(np.std(closes[-20:]) / np.mean(closes[-20:]) * 100) if np.mean(closes[-20:]) > 0 else 0.0
    else:
        features["m_volatility_20"] = 0.0

    # ── 캔들스틱 특징 ──
    if len(closes) >= 1:
        body = abs(closes[-1] - opens[-1])
        candle_range = highs[-1] - lows[-1]
        features["m_candle_body_pct"] = (body / candle_range * 100) if candle_range > 0 else 50.0
    else:
        features["m_candle_body_pct"] = 50.0

    # 최근 20분 고가-저가 범위
    if len(closes) >= 20:
        recent_high = float(np.max(highs[-20:]))
        recent_low = float(np.min(lows[-20:]))
        features["m_high_low_range_pct"] = (
            (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0.0
        )
    else:
        features["m_high_low_range_pct"] = 0.0

    # ── 가격 가속도 (ROC 변화율) ──
    roc3 = _pct_change(closes, 3)
    roc5 = _pct_change(closes, 5)
    features["m_price_acceleration"] = roc3 - roc5

    # ── 연속 상승/하락 분봉 개수 ──
    up_count = 0
    down_count = 0
    for i in range(min(10, len(closes) - 1), 0, -1):
        if closes[len(closes) - i] > closes[len(closes) - i - 1]:
            up_count += 1
            down_count = 0
        elif closes[len(closes) - i] < closes[len(closes) - i - 1]:
            down_count += 1
            up_count = 0
        else:
            break
    features["m_consecutive_up"] = float(up_count)
    features["m_consecutive_down"] = float(down_count)

    # ── VWAP 거리 (단순화: 종가 단순평균 대비) ──
    if len(closes) >= 20:
        vwap_approx = float(np.mean(closes[-20:]))
        features["m_vwap_distance_pct"] = ((current - vwap_approx) / vwap_approx * 100) if vwap_approx > 0 else 0.0
    else:
        features["m_vwap_distance_pct"] = 0.0

    # ── Target ──
    target = None
    time_order = prices[-1].timestamp < prices[0].timestamp
    if include_target and len(prices) > forward_minutes:
        if not time_order:
            future_idx = len(prices) + forward_minutes
        else:
            future_idx = len(prices) - forward_minutes
        if 0 <= future_idx < len(closes):
            future_return = (closes[future_idx] - current) / current if current > 0 else 0.0
            target = float(future_return)

    # ── NaN/Inf 정리 ──
    for k, v in features.items():
        if not np.isfinite(v):
            features[k] = 0.0

    timestamp = str(prices[-1].timestamp) if prices else ""

    return MinuteFeatureVector(
        code=code,
        features=features,
        target=target,
        timestamp=timestamp,
    )


def build_minute_training_dataset(
    all_minute_prices: list[list[PriceData]],
    codes: list[str],
    forward_minutes: int = 10,
    forward_threshold: float = 0.01,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, list[str]]:
    """분봉 학습 데이터셋 생성.

    각 과거 시점에서 피처를 뽑고, forward_minutes 뒤 수익률이
    forward_threshold 이상이면 양성(1)으로 레이블링한다.

    Args:
        all_minute_prices: 종목별 분봉 가격 리스트.
        codes: 종목코드 리스트.
        forward_minutes: 타겟 예측 분.
        forward_threshold: 양성 판정 임계 수익률.

    Returns:
        (X, y, prices_at_point, feature_names)
        prices_at_point: 각 샘플 시점의 가격 (회귀/분석용, None 가능).
    """
    n_features = len(MINUTE_FEATURE_NAMES)
    X_list: list[list[float]] = []
    y_list: list[int] = []
    prices_list: list[float] = []

    for prices, code in zip(all_minute_prices, codes):
        n = len(prices)
        if n < 60 + forward_minutes:
            continue

        closes = np.array([p.close for p in prices], dtype=float)

        for i in range(60, n - forward_minutes):
            slice_prices = prices[:i + 1]
            fv = build_minute_features(slice_prices, code=code, include_target=False)

            if fv is None or len(fv.features) != n_features:
                continue

            # 레이블: forward_minutes 뒤 수익률이 threshold 이상?
            future_return = (closes[i + forward_minutes] - closes[i]) / closes[i]
            label = 1 if future_return > forward_threshold else 0

            vec = [fv.features[name] for name in MINUTE_FEATURE_NAMES]
            X_list.append(vec)
            y_list.append(label)
            prices_list.append(float(closes[i]))

    if not X_list:
        return np.array([]).reshape(0, n_features), np.array([]), None, MINUTE_FEATURE_NAMES

    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=int)
    prices_at = np.array(prices_list, dtype=float) if prices_list else None

    # NaN/Inf → 0
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    return X, y, prices_at, MINUTE_FEATURE_NAMES


# ── Helpers ──

def _safe_last(arr: np.ndarray, default: float = 0.0) -> float:
    if len(arr) == 0:
        return default
    val = float(arr[-1])
    return val if not np.isnan(val) else default


def _pct_change(values: np.ndarray, lookback: int) -> float:
    if len(values) <= lookback:
        return 0.0
    past = float(values[-1 - lookback])
    current = float(values[-1])
    if past == 0:
        return 0.0
    return (current - past) / past * 100
