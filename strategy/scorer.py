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
# Can be overridden via env NGSAT_SCORER_WEIGHTS (JSON string)
import json
import os

_SCORER_WEIGHTS_ENV = os.getenv("NGSAT_SCORER_WEIGHTS", "")
if _SCORER_WEIGHTS_ENV:
    try:
        _REGIME_WEIGHTS = json.loads(_SCORER_WEIGHTS_ENV)
        logger.info(f"스코어 가중치: env override 적용 ({len(_REGIME_WEIGHTS)}개 레짐)")
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"NGSAT_SCORER_WEIGHTS env 파싱 실패, 기본값 사용: {e}")
        _REGIME_WEIGHTS = {}
_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {
        "rsi": 10, "mfi": 10, "adx_di": 20, "obv": 15,
        "ma": 15, "volume": 10, "pattern": 10, "candle": 5,
        "rs": 5, "investor": 5,
    },
    "neutral": {
        "rsi": 15, "mfi": 10, "adx_di": 10, "obv": 10,
        "ma": 10, "volume": 10, "pattern": 15, "candle": 5,
        "stochastic_k": 10, "rs": 5, "investor": 8,
    },
    "bear": {
        "rsi": 15, "mfi": 10, "adx_di": 10, "obv": 5,
        "ma": 5, "volume": 10, "pattern": 10, "candle": 5,
        "rs": 20, "investor": 10,
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


def score_volume(
    vol_ma5: float, vol_ma20: float, vol_ratio: float,
    vol_ma3: float | None = None,
    price_direction: float | None = None,
) -> float:
    """Volume trend scoring with multi-period analysis.

    Args:
        vol_ma5: 5-day avg volume.
        vol_ma20: 20-day avg volume.
        vol_ratio: current volume / avg volume.
        vol_ma3: 3-day avg volume (ultra-short term spike).
        price_direction: +1=up, -1=down, 0=flat (price confirmation).

    Returns:
        0~100 score.
    """
    score = 50.0  # neutral baseline

    # Long term trend (MA5 vs MA20)
    if vol_ma5 > vol_ma20 * 1.3:
        score += 15  # strong uptrend
    elif vol_ma5 > vol_ma20:
        score += 8   # mild uptrend
    elif vol_ma5 < vol_ma20 * 0.7:
        score -= 10  # strong downtrend

    # Short term spike (vol_ma3 vs vol_ma5)
    if vol_ma3 is not None and vol_ma3 > vol_ma5 * 1.5:
        score += 12  # ultra-short spike
    elif vol_ma3 is not None and vol_ma3 > vol_ma5:
        score += 5

    # Volume ratio confirmation
    if vol_ratio > 2.0:
        score += 10  # 2x+ = strong interest
    elif vol_ratio > 1.5:
        score += 7
    elif vol_ratio > 1.2:
        score += 4

    # Price-volume confirmation
    if price_direction is not None:
        if price_direction > 0 and vol_ratio > 1.2:
            score += 8  # volume supporting uptrend
        elif price_direction < 0 and vol_ratio > 1.2:
            score -= 8  # volume supporting downtrend (distribution)

    return max(0.0, min(100.0, score))


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


def score_investor_flow(investor_data: dict | None) -> float:
    """외인/기관 순매수 데이터 기반 점수 (0~100).

    Args:
        investor_data: get_investor_data() 결과 dict.
            foreign_net_buy_amt, institution_net_buy_amt

    Returns:
        0~100 점수. 데이터 없으면 50.0 (중립).
    """
    if not investor_data:
        return 50.0
    foreign_amt = investor_data.get("foreign_net_buy_amt", 0.0)
    inst_amt = investor_data.get("institution_net_buy_amt", 0.0)
    total = float(foreign_amt) + float(inst_amt)
    # +1억 = 100점, 0 = 50점, -1억 = 0점
    score = 50.0 + (total / 100_000_000) * 50.0
    return max(0.0, min(100.0, score))


# Pattern scoring weights (P-66 강화: 패턴별 + 레짐별 차등)
_PATTERN_TYPE_WEIGHTS: dict[str, float] = {
    "breakout": 1.5,
    "bollinger_squeeze": 1.3,
    "ma_cross": 1.2,
    "pullback": 0.9,
    "rebound": 0.8,
}
_PATTERN_REGIME_MOD: dict[str, float] = {
    "bull": 1.2, "neutral": 1.0, "bear": 0.7,
}


def score_patterns(
    patterns: list,
    regime: str = "neutral",
) -> float:
    """Pattern score with regime modulation and diminishing returns.

    Args:
        patterns: List of pattern objects with .pattern_name and .detected.
        regime: Current market regime.

    Returns:
        0~100 score.
    """
    if not patterns:
        return 0.0

    regime_mod = _PATTERN_REGIME_MOD.get(regime, 1.0)
    total = 0.0
    count = 0

    for p in patterns:
        if not hasattr(p, 'detected') or not p.detected:
            continue
        base_weight = _PATTERN_TYPE_WEIGHTS.get(
            getattr(p, 'pattern_name', ''), 1.0
        )
        # Base score per pattern (diminishing returns for multiple patterns)
        count += 1
        addition = 20 * base_weight * regime_mod
        if count > 1:
            addition *= 0.6  # 2nd+ patterns get only 60%
        total += addition

    return min(100.0, total)


# ── Composite calculation ──

def compute_total_score(
    indicator_scores: dict[str, float],
    regime: str,
    sector_bonus: float = 0.0,
    momentum_bonus: float = 0.0,
) -> float:
    """Weighted total score from individual indicator scores.

    Each indicator_scores value should be 0~100.
    Weights are regime-dependent.
    Result is clamped to 0~100.

    Args:
        indicator_scores: {indicator_name: score_0_100}
        regime: "bull" / "neutral" / "bear"
        sector_bonus: Sector rotation bonus (P-66).
        momentum_bonus: Dual momentum bonus (P-66).

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

    normalized = weighted_sum / total_weight
    normalized += sector_bonus
    normalized += momentum_bonus
    return max(0.0, min(100.0, normalized))
