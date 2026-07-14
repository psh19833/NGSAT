"""NGSAT position sizing — Kelly Criterion + regime-based dynamic allocation.

SRP: This module ONLY calculates position size based on win rate + regime.
Orchestrator/risk.py calls calc_position_size() — no side effects, pure function.

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
    kelly_stats: dict | None = None,
) -> float:
    """Calculate position size as fraction of total capital using Half-Kelly.

    Kelly Criterion: f* = (p * b - (1-p)) / b
      where p = win_rate, b = avg_win_pct / avg_loss_pct

    Half-Kelly applied for safety. Falls back to regime-based static sizing
    when kelly_stats has use_fallback=True (too few trades) or invalid data.

    Args:
        regime: Current market regime enum or string value.
        regime_score: Regime score (0~100).
        atr_pct: Current ATR percentage (volatility).
        config: StrategyConfig (unused currently, for future tuning).
        max_position_pct: Maximum position size (default 15%).
        kelly_stats: Dict from TradeRecorder.get_kelly_stats() with keys:
            win_rate, avg_win_pct, avg_loss_pct, use_fallback.

    Returns:
        Position size as fraction (0.0 ~ max_position_pct).
    """
    # Normalize regime to MarketRegime
    if isinstance(regime, str):
        try:
            regime = MarketRegime(regime)
        except ValueError:
            regime = MarketRegime.NEUTRAL

    # ── Kelly Criterion ──
    kelly_pct = None
    if kelly_stats and not kelly_stats.get("use_fallback", True):
        p = kelly_stats["win_rate"]
        avg_win = kelly_stats["avg_win_pct"]
        avg_loss = kelly_stats["avg_loss_pct"]
        if avg_loss > 0 and 0 < p < 1:
            b = avg_win / avg_loss  # win/loss ratio
            f_star = (p * b - (1 - p)) / b  # full Kelly
            kelly_pct = max(0.0, f_star * 0.5)  # Half-Kelly for safety

    # ── Regime-based sizing (base) ──
    if regime == MarketRegime.BEAR:
        if regime_score <= 15:
            base = 0.02
        elif regime_score <= 25:
            base = 0.05
        elif regime_score <= 35:
            base = 0.08
        else:
            base = 0.08

    elif regime == MarketRegime.NEUTRAL:
        if atr_pct > 5.0:
            base = 0.10
        elif atr_pct > 3.0:
            base = 0.12
        else:
            base = 0.15

    elif regime == MarketRegime.BULL:
        base = max_position_pct

    else:
        base = max_position_pct * 0.5

    # ── Blend Kelly with regime base ──
    if kelly_pct is not None:
        # Blend: 70% Kelly + 30% regime base (Kelly dominates but regime anchors)
        blended = kelly_pct * 0.7 + base * 0.3
    else:
        blended = base

    return min(blended, max_position_pct)
