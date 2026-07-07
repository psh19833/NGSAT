"""NGSAT stock screener scoring — indicator values → composite score.

Separated from screener.py for SRP compliance.
Each score function takes an indicator value + regime, returns contribution (0~100).
New indicators: add a function here + implement in indicators.py.

Regime weights are loaded from StrategyConfig (core/config.py).
"""

from __future__ import annotations

from core.logger import logger

# ── Regime weight profiles ──
# Weights are defined in StrategyConfig. These are the default fallbacks.
# Format: {regime: {indicator_name: weight_percent}}
_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {
        "rsi": 10, "mfi": 10, "adx_di": 20, "obv": 15,
        "ma": 20, "volume": 10, "pattern": 10, "candle": 5,
    },
    "neutral": {
        "rsi": 20, "mfi": 15, "adx_di": 10, "obv": 10,
        "ma": 10, "volume": 10, "pattern": 15, "candle": 10,
    },
    "bear": {
        "rsi": 15, "mfi": 20, "adx_di": 10, "obv": 10,
        "ma": 5, "volume": 10, "pattern": 10, "candle": 15,
    },
}


def get_regime_weights(regime: str) -> dict[str, float]:
    """Get score weights for a given regime. Falls back to neutral."""
    return _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS["neutral"])


def score_rsi(rsi_val: float) -> float:
    """RSI scoring: 0~100. Oversold recovery/bullish zone = high."""
    if rsi_val < 30:
        return 70.0  # Oversold — potential rebound
    elif rsi_val < 50:
        return 80.0  # Oversold recovery zone
    elif rsi_val < 70:
        return 90.0  # Healthy bullish zone
    else:
        return 30.0  # Overbought — risky


def score_mfi(mfi_val: float) -> float:
    """MFI scoring: 0~100. Money flow with volume confirmation."""
    if mfi_val < 20:
        return 80.0  # Oversold + volume confirmation
    elif mfi_val < 50:
        return 60.0  # Neutral with accumulation potential
    elif mfi_val < 80:
        return 40.0  # Distribution zone
    else:
        return 20.0  # Overbought + volume divergence risk


def score_obv_slope(slope: float) -> float:
    """OBV trend scoring: accumulation/distribution detection."""
    if slope > 0:
        return min(80.0, 50.0 + slope * 10)
    else:
        return max(20.0, 50.0 + slope * 10)


def score_ma_alignment(close: float, ma5: float, ma20: float) -> float:
    """Moving average alignment scoring."""
    if close > ma5 > ma20:
        return 90.0  # Perfect bullish alignment
    elif close > ma5:
        return 60.0  # Above short-term MA
    elif close < ma5 < ma20:
        return 20.0  # Bearish alignment
    else:
        return 40.0  # Mixed


def score_adx_di(adx_val: float, di_plus: float, di_minus: float) -> float:
    """ADX + Directional Indicator scoring: trend strength + direction."""
    if adx_val > 25 and di_plus > di_minus:
        return 85.0  # Strong uptrend
    elif adx_val > 25 and di_minus > di_plus:
        return 25.0  # Strong downtrend
    elif adx_val > 20:
        return 60.0  # Mild trend
    else:
        return 50.0  # Trendless


def score_volume(vol_ma5: float, vol_ma20: float, vol_ratio: float) -> float:
    """Volume trend scoring."""
    if vol_ma5 > vol_ma20 and vol_ratio > 1.2:
        return 85.0  # Volume increasing + above average
    elif vol_ma5 > vol_ma20:
        return 65.0  # Volume trend improving
    elif vol_ratio > 1.2:
        return 70.0  # Above-average volume
    else:
        return 35.0  # Volume declining


def score_stochastic(k_val: float) -> float:
    """Stochastic scoring."""
    if k_val < 20:
        return 80.0  # Oversold — rebound expected
    elif k_val > 80:
        return 25.0  # Overbought — chase risk
    else:
        return 55.0  # Neutral


def score_macd(hist: float) -> float:
    """MACD histogram scoring."""
    if hist > 0:
        return 65.0  # Bullish MACD
    else:
        return 35.0  # Bearish MACD


def score_relative_strength(rs: float) -> float:
    """Relative Strength vs market index scoring."""
    if rs > 1.3:
        return 85.0  # Strong outperformance
    elif rs > 1.1:
        return 70.0  # Moderate outperformance
    elif rs > 0.9:
        return 50.0  # In line with market
    elif rs > 0.7:
        return 30.0  # Underperforming
    else:
        return 15.0  # Significant underperformance


def score_candlestick(bullish: bool, bearish: bool) -> float:
    """Candlestick pattern scoring."""
    if bullish:
        return 75.0
    elif bearish:
        return 30.0
    return 50.0


# ── Composite calculation ──

def compute_total_score(
    indicator_scores: dict[str, float],
    regime: str,
) -> float:
    """Weighted total score from individual indicator scores.

    Each indicator_scores value should be 0~100.
    Weights are regime-dependent.
    Result is clamped to 0~100.

    Args:
        indicator_scores: {indicator_name: score_0_100}
        regime: "bull" / "neutral" / "bear"

    Returns:
        Composite score 0~100.
    """
    weights = get_regime_weights(regime)
    total_weight = 0.0
    weighted_sum = 0.0

    for name, score in indicator_scores.items():
        w = weights.get(name, 1.0)
        weighted_sum += score * w
        total_weight += w

    if total_weight <= 0:
        return 50.0

    normalized = weighted_sum / total_weight * 100
    return max(0.0, min(100.0, normalized))
