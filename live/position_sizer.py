"""NGSAT position sizing — Kelly-approximate dynamic position allocation.

SRP: This module ONLY calculates position size based on regime condition.
Orchestrator.py calls calc_position_size() — no side effects, pure function.

Output is a fraction of total capital (0.0 ~ max_position_pct).
"""

from __future__ import annotations

from core.types import MarketRegime


def calc_position_size(
    regime: MarketRegime | str,
    regime_score: float,
    atr_pct: float = 0.0,
    config=None,
    max_position_pct: float = 0.15,
) -> float:
    """Calculate position size as fraction of total capital.

    Uses regime + score to determine Kelly-approximate sizing.
    Falls back to current mode-based sizing when regime is not BEAR.

    Args:
        regime: Current market regime enum or string value.
        regime_score: Regime score (0~100).
        atr_pct: Current ATR percentage (volatility).
        config: StrategyConfig (unused currently, for future tuning).
        max_position_pct: Maximum position size (default 15%).

    Returns:
        Position size as fraction (0.0 ~ max_position_pct).
    """
    # Normalize regime to MarketRegime
    if isinstance(regime, str):
        try:
            regime = MarketRegime(regime)
        except ValueError:
            regime = MarketRegime.NEUTRAL

    if regime == MarketRegime.BEAR:
        # Kelly-approximate: lower score = smaller position
        if regime_score <= 15:
            return 0.02  # 2% — extreme bear, minimal exposure
        elif regime_score <= 25:
            return 0.05  # 5% — deep bear
        elif regime_score <= 35:
            return 0.08  # 8% — moderate bear
        else:
            # Score > 35 but still BEAR (hysteresis band)
            return 0.08

    elif regime == MarketRegime.NEUTRAL:
        # Neutral: scale with ATR (higher vol = smaller position)
        if atr_pct > 5.0:
            return 0.10  # 10% — high vol
        elif atr_pct > 3.0:
            return 0.12  # 12%
        else:
            return 0.15  # 15% — max

    elif regime == MarketRegime.BULL:
        return max_position_pct  # 15%

    return max_position_pct * 0.5  # Fallback: 7.5%
