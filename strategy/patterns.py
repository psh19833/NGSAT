"""NGSAT chart pattern detection.

Detects chart patterns from price data. Each pattern returns a
PatternResult with a clear boolean signal and human-readable reason
(in Korean) — supporting NGSAT's core principle: every decision has a reason.

Pattern detection is pure calculation — no KIS API calls, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from strategy.indicators import sma, ema, rsi, bollinger_bands, volume_ratio


@dataclass(frozen=True)
class PatternResult:
    """Result of a pattern detection.

    Attributes:
        detected: Whether the pattern was found.
        pattern_name: Pattern name (English identifier).
        pattern_name_kr: Pattern name in Korean (for notifications).
        reason: Human-readable explanation of why the pattern was/wasn't detected.
        evidence: Quantitative evidence (values that triggered the pattern).
    """
    detected: bool
    pattern_name: str
    pattern_name_kr: str
    reason: str
    evidence: dict[str, float] = field(default_factory=dict)


# ── Breakout (돌파) ──

def detect_breakout(
    closes: Sequence[float],
    highs: Sequence[float],
    volumes: Sequence[int],
    lookback: int = 20,
    volume_threshold: float = 1.5,
) -> PatternResult:
    """Detect a price breakout above recent high.

    Conditions:
    - Current close > highest high in last `lookback` periods
    - Volume ratio > volume_threshold (above-average volume confirms breakout)

    Args:
        closes: Closing prices.
        highs: High prices.
        volumes: Volume series.
        lookback: Period for recent high (default 20).
        volume_threshold: Minimum volume ratio to confirm (default 1.5).

    Returns:
        PatternResult with breakout detection.
    """
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float)
    v = np.asarray(volumes, dtype=float)

    if len(c) < lookback + 1:
        return PatternResult(
            detected=False,
            pattern_name="breakout",
            pattern_name_kr="돌파",
            reason=f"데이터 부족: {len(c)}개 (필요: {lookback + 1}개)",
        )

    recent_high = float(np.max(h[-lookback - 1:-1]))
    current_close = float(c[-1])

    vol_ratios = volume_ratio(v, lookback)
    current_vol_ratio = float(vol_ratios[-1]) if not np.isnan(vol_ratios[-1]) else 0.0

    evidence = {
        "current_close": current_close,
        "recent_high": recent_high,
        "volume_ratio": current_vol_ratio,
        "volume_threshold": volume_threshold,
    }

    if current_close > recent_high and current_vol_ratio >= volume_threshold:
        return PatternResult(
            detected=True,
            pattern_name="breakout",
            pattern_name_kr="돌파",
            reason=(
                f"돌파 감지: 현재가 {current_close:,.0f} > 최근{lookback}일 고점 {recent_high:,.0f}, "
                f"거래량 비율 {current_vol_ratio:.1f}배 (기준 {volume_threshold}배)"
            ),
            evidence=evidence,
        )

    return PatternResult(
        detected=False,
        pattern_name="breakout",
        pattern_name_kr="돌파",
        reason=(
            f"돌파 미감지: 현재가 {current_close:,.0f} <= 고점 {recent_high:,.0f} "
            f"또는 거래량 {current_vol_ratio:.1f}배 < 기준 {volume_threshold}배"
        ),
        evidence=evidence,
    )


# ── Pullback (눌림) ──

def detect_pullback(
    closes: Sequence[float],
    highs: Sequence[float],
    ma_period: int = 20,
    pullback_pct: float = 0.05,
) -> PatternResult:
    """Detect a pullback to moving average support.

    Conditions:
    - Price was above MA, then pulled back
    - Current price is near the MA (within pullback_pct)
    - Price is still above the MA (support holding)

    Args:
        closes: Closing prices.
        highs: High prices.
        ma_period: Moving average period (default 20).
        pullback_pct: Maximum distance from MA as fraction (default 5%).

    Returns:
        PatternResult with pullback detection.
    """
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float)

    if len(c) < ma_period + 5:
        return PatternResult(
            detected=False,
            pattern_name="pullback",
            pattern_name_kr="눌림",
            reason=f"데이터 부족: {len(c)}개 (필요: {ma_period + 5}개)",
        )

    ma = sma(c, ma_period)
    current_close = float(c[-1])
    current_ma = float(ma[-1]) if not np.isnan(ma[-1]) else 0.0

    recent_high = float(np.max(h[-ma_period:]))
    pullback_from_high = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0

    # Distance from MA as percentage
    ma_distance_pct = abs(current_close - current_ma) / current_ma if current_ma > 0 else 1.0

    evidence = {
        "current_close": current_close,
        "ma_value": current_ma,
        "ma_distance_pct": ma_distance_pct * 100,
        "pullback_from_high_pct": pullback_from_high * 100,
    }

    # Pullback conditions: price above MA, close to MA, pulled back from recent high
    if (
        current_close > current_ma
        and ma_distance_pct <= pullback_pct
        and pullback_from_high > 0.02
    ):
        return PatternResult(
            detected=True,
            pattern_name="pullback",
            pattern_name_kr="눌림",
            reason=(
                f"눌림 감지: 현재가 {current_close:,.0f}가 {ma_period}일선 {current_ma:,.0f} 근처, "
                f"MA 이격도 {ma_distance_pct * 100:.1f}%, 고점대비 눌림 {pullback_from_high * 100:.1f}%"
            ),
            evidence=evidence,
        )

    return PatternResult(
        detected=False,
        pattern_name="pullback",
        pattern_name_kr="눌림",
        reason=(
            f"눌림 미감지: MA 이격도 {ma_distance_pct * 100:.1f}% (기준 {pullback_pct * 100:.0f}%) "
            f"또는 눌림 폭 {pullback_from_high * 100:.1f}% 부족"
        ),
        evidence=evidence,
    )


# ── Rebound (반등) ──

def detect_rebound(
    closes: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[int],
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    rebound_bars: int = 3,
) -> PatternResult:
    """Detect a rebound from oversold conditions.

    Conditions:
    - RSI was below oversold threshold recently
    - Price has been rising for the last `rebound_bars` periods
    - Volume is increasing (confirmation)

    Args:
        closes: Closing prices.
        lows: Low prices.
        volumes: Volume series.
        rsi_period: RSI calculation period (default 14).
        rsi_oversold: RSI oversold threshold (default 30).
        rebound_bars: Number of rising bars to confirm rebound (default 3).

    Returns:
        PatternResult with rebound detection.
    """
    c = np.asarray(closes, dtype=float)
    l = np.asarray(lows, dtype=float)
    v = np.asarray(volumes, dtype=float)

    min_data = rsi_period + rebound_bars + 2
    if len(c) < min_data:
        return PatternResult(
            detected=False,
            pattern_name="rebound",
            pattern_name_kr="반등",
            reason=f"데이터 부족: {len(c)}개 (필요: {min_data}개)",
        )

    rsi_values = rsi(c, rsi_period)
    current_rsi = float(rsi_values[-1]) if not np.isnan(rsi_values[-1]) else 50.0

    # Check if RSI was oversold in the recent past
    recent_rsi = rsi_values[-(rebound_bars + 5):-rebound_bars]
    was_oversold = bool(np.any(recent_rsi < rsi_oversold)) if not np.all(np.isnan(recent_rsi)) else False

    # Check rising bars
    recent_closes = c[-rebound_bars - 1:]
    rising = bool(all(recent_closes[i] > recent_closes[i - 1] for i in range(1, len(recent_closes))))

    # Volume increasing
    recent_volumes = v[-rebound_bars:]
    vol_increasing = bool(all(recent_volumes[i] > recent_volumes[i - 1] for i in range(1, len(recent_volumes)))) if len(recent_volumes) > 1 else False

    evidence = {
        "current_rsi": current_rsi,
        "was_oversold": float(was_oversold),
        "rising_bars": float(rising),
        "volume_increasing": float(vol_increasing),
        "rsi_threshold": rsi_oversold,
    }

    if was_oversold and rising:
        vol_text = "거래량 증가 확인" if vol_increasing else "거래량 확인 미흡"
        return PatternResult(
            detected=True,
            pattern_name="rebound",
            pattern_name_kr="반등",
            reason=(
                f"반등 감지: RSI 과매도 구간 반등, 현재 RSI {current_rsi:.1f}, "
                f"연속 상승 {rebound_bars}봉, {vol_text}"
            ),
            evidence=evidence,
        )

    reasons = []
    if not was_oversold:
        reasons.append(f"RSI 과매도 이력 없음 (현재 {current_rsi:.1f})")
    if not rising:
        reasons.append(f"연속 상승 아님")

    return PatternResult(
        detected=False,
        pattern_name="rebound",
        pattern_name_kr="반등",
        reason=f"반등 미감지: {', '.join(reasons)}",
        evidence=evidence,
    )


# ── Bollinger Squeeze (볼린저 밴드 수축) ──

def detect_bollinger_squeeze(
    closes: Sequence[float],
    period: int = 20,
    std_dev: float = 2.0,
    squeeze_threshold: float = 0.05,
) -> PatternResult:
    """Detect Bollinger Band squeeze (low volatility → potential breakout).

    Conditions:
    - Band width (upper - lower) / middle is below squeeze_threshold
    - Indicates low volatility, potential breakout incoming

    Args:
        closes: Closing prices.
        period: Bollinger period (default 20).
        std_dev: Standard deviation multiplier (default 2.0).
        squeeze_threshold: Maximum band width ratio for squeeze (default 5%).

    Returns:
        PatternResult with squeeze detection.
    """
    c = np.asarray(closes, dtype=float)

    if len(c) < period:
        return PatternResult(
            detected=False,
            pattern_name="bollinger_squeeze",
            pattern_name_kr="밴드수축",
            reason=f"데이터 부족: {len(c)}개 (필요: {period}개)",
        )

    upper, middle, lower = bollinger_bands(c, period, std_dev)

    current_upper = float(upper[-1]) if not np.isnan(upper[-1]) else 0.0
    current_middle = float(middle[-1]) if not np.isnan(middle[-1]) else 0.0
    current_lower = float(lower[-1]) if not np.isnan(lower[-1]) else 0.0

    band_width = current_upper - current_lower
    band_width_ratio = band_width / current_middle if current_middle > 0 else 1.0

    evidence = {
        "upper_band": current_upper,
        "middle_band": current_middle,
        "lower_band": current_lower,
        "band_width": band_width,
        "band_width_ratio": band_width_ratio * 100,
        "squeeze_threshold": squeeze_threshold * 100,
    }

    if band_width_ratio <= squeeze_threshold:
        return PatternResult(
            detected=True,
            pattern_name="bollinger_squeeze",
            pattern_name_kr="밴드수축",
            reason=(
                f"밴드수축 감지: 밴드 폭 비율 {band_width_ratio * 100:.1f}% <= 기준 {squeeze_threshold * 100:.0f}%, "
                f"변동성 축소 → 돌파 임박 가능성"
            ),
            evidence=evidence,
        )

    return PatternResult(
        detected=False,
        pattern_name="bollinger_squeeze",
        pattern_name_kr="밴드수축",
        reason=f"밴드수축 미감지: 밴드 폭 비율 {band_width_ratio * 100:.1f}% > 기준 {squeeze_threshold * 100:.0f}%",
        evidence=evidence,
    )


# ── Golden Cross / Death Cross ──

def detect_ma_cross(
    closes: Sequence[float],
    fast_period: int = 5,
    slow_period: int = 20,
) -> PatternResult:
    """Detect moving average crossover (golden cross / death cross).

    Args:
        closes: Closing prices.
        fast_period: Fast MA period (default 5).
        slow_period: Slow MA period (default 20).

    Returns:
        PatternResult with cross detection.
    """
    c = np.asarray(closes, dtype=float)

    if len(c) < slow_period + 2:
        return PatternResult(
            detected=False,
            pattern_name="ma_cross",
            pattern_name_kr="MA교차",
            reason=f"데이터 부족: {len(c)}개 (필요: {slow_period + 2}개)",
        )

    fast_ma = sma(c, fast_period)
    slow_ma = sma(c, slow_period)

    current_fast = float(fast_ma[-1]) if not np.isnan(fast_ma[-1]) else 0.0
    current_slow = float(slow_ma[-1]) if not np.isnan(slow_ma[-1]) else 0.0
    prev_fast = float(fast_ma[-2]) if not np.isnan(fast_ma[-2]) else 0.0
    prev_slow = float(slow_ma[-2]) if not np.isnan(slow_ma[-2]) else 0.0

    # Golden cross: fast crosses above slow
    # Death cross: fast crosses below slow
    golden_cross = prev_fast <= prev_slow and current_fast > current_slow
    death_cross = prev_fast >= prev_slow and current_fast < current_slow

    evidence = {
        "fast_ma": current_fast,
        "slow_ma": current_slow,
        "prev_fast_ma": prev_fast,
        "prev_slow_ma": prev_slow,
    }

    if golden_cross:
        return PatternResult(
            detected=True,
            pattern_name="golden_cross",
            pattern_name_kr="골든크로스",
            reason=(
                f"골든크로스 감지: {fast_period}일선 {current_fast:,.0f}가 "
                f"{slow_period}일선 {current_slow:,.0f}를 상향 돌파"
            ),
            evidence=evidence,
        )

    if death_cross:
        return PatternResult(
            detected=True,
            pattern_name="death_cross",
            pattern_name_kr="데드크로스",
            reason=(
                f"데드크로스 감지: {fast_period}일선 {current_fast:,.0f}가 "
                f"{slow_period}일선 {current_slow:,.0f}를 하향 돌파"
            ),
            evidence=evidence,
        )

    return PatternResult(
        detected=False,
        pattern_name="ma_cross",
        pattern_name_kr="MA교차",
        reason=(
            f"MA교차 미감지: {fast_period}일선 {current_fast:,.0f}, "
            f"{slow_period}일선 {current_slow:,.0f} (교차 없음)"
        ),
        evidence=evidence,
    )
