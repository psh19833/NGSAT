"""NGSAT market regime evaluation.

Evaluates overall market condition (BULL / NEUTRAL / BEAR) using
a weighted scoring system based on index price data.

This is the 1st stage of the NGSAT 3-stage pipeline:
  Regime → Screener → ML

The regime evaluation uses:
- Index moving average alignment (MA5 > MA20 > MA60 → bullish)
- RSI of the index (momentum)
- Price position relative to Bollinger Bands
- Recent change rate (short-term momentum)

Every regime evaluation includes a human-readable reason (Korean)
and quantitative evidence — supporting NGSAT's core principle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from core.logger import logger
from core.types import MarketRegime
from strategy.indicators import sma, rsi, bollinger_bands, adx


@dataclass(frozen=True)
class RegimeResult:
    """Market regime evaluation result.

    Attributes:
        regime: BULL / NEUTRAL / BEAR
        score: Numeric score (0-100, higher = more bullish)
        reason: Human-readable explanation (Korean).
        evidence: Quantitative breakdown of the score.
    """
    regime: MarketRegime
    score: float
    reason: str
    evidence: dict[str, float] = field(default_factory=dict)


# ── Scoring weights ──
# ── Strategy config injection ──
from core.config import StrategyConfig as _StrategyConfig

# ── Scoring weights (configurable via StrategyConfig) ──
_WEIGHT_MA_ALIGNMENT = 30.0    # MA 정렬 (기존 35→30)
_WEIGHT_RSI = 20.0             # RSI 모멘텀
_WEIGHT_BOLLINGER = 20.0       # 볼린저밴드 위치
_WEIGHT_CHANGE_RATE = 15.0     # 단기 등락률
_WEIGHT_VOLUME_TREND = 10.0    # 거래량 추세 (기존 15→10, ADX 5점 확보)
_WEIGHT_ADX = 5.0              # ADX 추세강도 (신규)

# ── Thresholds ──
BULL_THRESHOLD = 65.0          # ≥ 65점 → 강세
BEAR_THRESHOLD = 35.0          # ≤ 35점 → 약세
# Between 35-65 → 중립


def evaluate_regime(
    index_closes: Sequence[float],
    index_volumes: Sequence[int] | None = None,
    index_high: Sequence[float] | None = None,
    index_low: Sequence[float] | None = None,
    config: _StrategyConfig | None = None,
) -> RegimeResult:
    """Evaluate market regime from index price data.

    Uses KOSPI or KOSDAQ index data to determine overall market condition.

    Args:
        index_closes: Index closing prices (at least 60 days recommended).
        index_volumes: Index volumes (optional, for volume trend analysis).

    Returns:
        RegimeResult with regime, score, reason, and evidence.
    """
    c = np.asarray(index_closes, dtype=float)

    if len(c) < 20:
        return RegimeResult(
            regime=MarketRegime.NEUTRAL,
            score=50.0,
            reason=f"데이터 부족으로 중립 판정: {len(c)}개 (권장: 60일 이상)",
            evidence={"data_count": float(len(c))},
        )

    scores: dict[str, float] = {}
    reasons: list[str] = []

    # ── Use dynamic config if injected ──
    cfg = config or _StrategyConfig()
    w_ma = cfg.regime_weight_ma
    w_rsi = cfg.regime_weight_rsi
    w_bb = cfg.regime_weight_bollinger
    w_cr = cfg.regime_weight_change_rate
    w_vol = cfg.regime_weight_volume
    bull_t = cfg.regime_bull_threshold
    bear_t = cfg.regime_bear_threshold

    # ── 1. MA Alignment ──
    ma_score, ma_reason = _score_ma_alignment(c)
    scores["ma_alignment"] = ma_score
    reasons.append(ma_reason)

    # ── 2. RSI (20점) ──
    rsi_score, rsi_reason, rsi_value = _score_rsi(c)
    scores["rsi"] = rsi_score
    reasons.append(rsi_reason)

    # ── 3. Bollinger Band position (20점) ──
    bb_score, bb_reason = _score_bollinger(c)
    scores["bollinger"] = bb_score
    reasons.append(bb_reason)

    # ── 4. Short-term change rate (15점) ──
    cr_score, cr_reason = _score_change_rate(c)
    scores["change_rate"] = cr_score
    reasons.append(cr_reason)

    # ── 5. Volume trend (10점) ──
    vol_score, vol_reason = _score_volume_trend(c, index_volumes)
    scores["volume_trend"] = vol_score
    reasons.append(vol_reason)

    # ── 6. ADX trend strength (5점, 신규) ──
    adx_score, adx_reason, adx_value = _score_adx(c, index_high, index_low)
    scores["adx"] = adx_score
    reasons.append(adx_reason)

    # ── BBW (Bollinger Band Width) — 점수 미포함, 참고용 evidence ──
    bb_upper, bb_middle, bb_lower = bollinger_bands(c, 20, 2.0)
    if not np.isnan(bb_middle[-1]) and bb_middle[-1] > 0:
        bbw = (bb_upper[-1] - bb_lower[-1]) / bb_middle[-1] * 100.0
    else:
        bbw = float("nan")

    # ── Weighted total ──
    total = (
        scores["ma_alignment"] * w_ma / 100
        + scores["rsi"] * w_rsi / 100
        + scores["bollinger"] * w_bb / 100
        + scores["change_rate"] * w_cr / 100
        + scores["volume_trend"] * w_vol / 100
        + scores["adx"] * cfg.regime_weight_adx / 100
    )
    # Validate config weights sum to 100
    w_sum = w_ma + w_rsi + w_bb + w_cr + w_vol + cfg.regime_weight_adx
    if abs(w_sum - 100.0) > 0.01:
        logger.warning(
            f"레짐 가중치 합이 100이 아님: {w_sum:.1f} "
            f"(MA={w_ma} RSI={w_rsi} BB={w_bb} CR={w_cr} VOL={w_vol} ADX={cfg.regime_weight_adx})"
        )

    # Determine regime
    if total >= bull_t:
        regime = MarketRegime.BULL
        regime_kr = "강세장"
    elif total <= bear_t:
        regime = MarketRegime.BEAR
        regime_kr = "약세장"
    else:
        regime = MarketRegime.NEUTRAL
        regime_kr = "중립장"

    reason = f"{regime_kr} (점수: {total:.1f}/100) — " + " | ".join(reasons)

    evidence = {k: v for k, v in scores.items()}
    evidence["total_score"] = total
    evidence["bull_threshold"] = bull_t
    evidence["bear_threshold"] = bear_t
    evidence["adx_value"] = adx_value    # raw ADX (not score)
    if not np.isnan(bbw):
        evidence["bb_width"] = round(bbw, 2)  # 볼린저밴드 폭 %

    return RegimeResult(
        regime=regime,
        score=total,
        reason=reason,
        evidence=evidence,
    )


# ── Scoring sub-functions ──

def _score_ma_alignment(closes: np.ndarray) -> tuple[float, str]:
    """Score MA alignment: MA5 > MA20 > MA60 → bullish."""
    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)

    current_ma5 = float(ma5[-1]) if not np.isnan(ma5[-1]) else 0.0
    current_ma20 = float(ma20[-1]) if not np.isnan(ma20[-1]) else 0.0
    current_price = float(closes[-1])

    score = 50.0  # neutral start
    reasons = []

    if current_price > current_ma5 > 0:
        score += 15
        reasons.append("가격 > MA5")
    else:
        score -= 15
        reasons.append("가격 < MA5")

    if current_ma5 > current_ma20 > 0:
        score += 20
        reasons.append("MA5 > MA20 (단기 상승 추세)")
    else:
        score -= 20
        reasons.append("MA5 < MA20 (단기 하락 추세)")

    if len(closes) >= 60:
        ma60 = sma(closes, 60)
        current_ma60 = float(ma60[-1]) if not np.isnan(ma60[-1]) else 0.0
        if current_ma20 > current_ma60 > 0:
            score += 15
            reasons.append("MA20 > MA60 (중기 상승 추세)")
        else:
            score -= 15
            reasons.append("MA20 < MA60 (중기 하락 추세)")

    score = max(0, min(100, score))
    return score, f"MA정렬 {score:.0f}점 ({', '.join(reasons)})"


def _score_rsi(closes: np.ndarray) -> tuple[float, str, float]:
    """Score RSI: 40-60 neutral, >60 bullish, <40 bearish."""
    rsi_values = rsi(closes, 14)
    current_rsi = float(rsi_values[-1]) if not np.isnan(rsi_values[-1]) else 50.0

    if current_rsi > 60:
        score = 70.0 + min(30, (current_rsi - 60) * 1.5)
        reason = f"RSI {current_rsi:.1f} (강세 모멘텀)"
    elif current_rsi < 40:
        score = 30.0 - min(30, (40 - current_rsi) * 1.5)
        reason = f"RSI {current_rsi:.1f} (약세 모멘텀)"
    else:
        score = 50.0
        reason = f"RSI {current_rsi:.1f} (중립 구간)"

    score = max(0, min(100, score))
    return score, reason, current_rsi


def _score_bollinger(closes: np.ndarray) -> tuple[float, str]:
    """Score based on price position within Bollinger Bands."""
    upper, middle, lower = bollinger_bands(closes, 20, 2.0)

    current_upper = float(upper[-1]) if not np.isnan(upper[-1]) else 0.0
    current_middle = float(middle[-1]) if not np.isnan(middle[-1]) else 0.0
    current_lower = float(lower[-1]) if not np.isnan(lower[-1]) else 0.0
    current_price = float(closes[-1])

    if current_upper == current_lower:
        return 50.0, "밴드 폭 0 (데이터 부족)"

    # Position within bands: 0 = at lower band, 1 = at upper band
    position = (current_price - current_lower) / (current_upper - current_lower)
    position = max(0.0, min(1.0, position))

    # Score: upper band → high score, lower band → low score
    score = position * 100

    if position > 0.8:
        reason = f"밴드 상단 근접 ({position * 100:.0f}% 위치, 강세)"
    elif position < 0.2:
        reason = f"밴드 하단 근접 ({position * 100:.0f}% 위치, 약세)"
    else:
        reason = f"밴드 중간 ({position * 100:.0f}% 위치, 중립)"

    return score, reason


def _score_change_rate(closes: np.ndarray) -> tuple[float, str]:
    """Score short-term price change rate (5-day)."""
    if len(closes) < 6:
        return 50.0, "등락률 데이터 부족"

    recent_5d_change = ((closes[-1] - closes[-6]) / closes[-6]) * 100 if closes[-6] != 0 else 0.0

    # Map change rate to score: +3% → ~85, 0% → 50, -3% → ~15
    score = 50.0 + recent_5d_change * 12.0
    score = max(0, min(100, score))

    if recent_5d_change > 1.0:
        reason = f"5일 등락률 +{recent_5d_change:.1f}% (상승)"
    elif recent_5d_change < -1.0:
        reason = f"5일 등락률 {recent_5d_change:.1f}% (하락)"
    else:
        reason = f"5일 등락률 {recent_5d_change:+.1f}% (횡보)"

    return score, reason


def _score_volume_trend(
    closes: np.ndarray,
    volumes: Sequence[int] | None,
) -> tuple[float, str]:
    """Score volume trend (price up + volume up = bullish confirmation)."""
    if volumes is None or len(volumes) < 20:
        return 50.0, "거래량 데이터 부족 (중립)"

    v = np.asarray(volumes, dtype=float)
    recent_vol_avg = float(np.mean(v[-20:]))
    prev_vol_avg = float(np.mean(v[-40:-20])) if len(v) >= 40 else recent_vol_avg

    price_up = closes[-1] > closes[-5] if len(closes) >= 5 else False
    vol_up = recent_vol_avg > prev_vol_avg if prev_vol_avg > 0 else False

    if price_up and vol_up:
        return 75.0, "가격 상승 + 거래량 증가 (강세 확인)"
    elif not price_up and vol_up:
        return 35.0, "가격 하락 + 거래량 증가 (약세 확인)"
    elif price_up and not vol_up:
        return 55.0, "가격 상승 + 거래량 미증가 (상승 신뢰도 낮음)"
    else:
        return 45.0, "가격 하락 + 거래량 미증가 (중립)"


def _score_adx(
    closes: np.ndarray,
    high: Sequence[float] | None = None,
    low: Sequence[float] | None = None,
) -> tuple[float, str, float]:
    """Score ADX trend strength.

    ADX < 20: no trend → 50점 (중립)
    ADX 20~40: trending → 50~80점 (선형)
    ADX > 40: strong trend → 80~100점

    high/low가 없으면 closes로 근사 (저/고가 = 종가 ±0.5%).
    """
    if len(closes) < 28:  # ADX(14) needs 27+ bars
        return 50.0, "ADX 데이터 부족 (중립)", float("nan")

    if high is not None and low is not None:
        h = np.asarray(high, dtype=float)
        l = np.asarray(low, dtype=float)
    else:
        # 근사: 고가 = 종가 * 1.005, 저가 = 종가 * 0.995
        c = np.asarray(closes, dtype=float)
        h = c * 1.005
        l = c * 0.995

    adx_values = adx(h, l, closes, period=14)
    current_adx = float(adx_values[-1]) if not np.isnan(adx_values[-1]) else 0.0

    if current_adx < 20:
        score = 50.0
        reason = f"ADX {current_adx:.1f} (추세 없음, 중립)"
    elif current_adx < 40:
        # 20→50점, 40→80점 (linear)
        score = 50.0 + (current_adx - 20) * 1.5
        score = min(80, max(50, score))
        reason = f"ADX {current_adx:.1f} (추세 보통)"
    else:
        # 40→80점, 100→100점 (linear)
        score = 80.0 + (min(current_adx, 100) - 40) * 0.333
        score = min(100, score)
        reason = f"ADX {current_adx:.1f} (강한 추세)"

    return score, reason, current_adx
