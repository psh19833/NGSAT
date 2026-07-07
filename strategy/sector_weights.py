"""NGSAT sector rotation weights — regime-dependent sector bonuses + momentum.

SRP: This module ONLY defines sector/momentum bonus mappings.
Scoring logic stays in scorer.py, screening stays in screener.py.

To add/modify a sector bonus: edit the dict below only.
"""

from __future__ import annotations

from core.types import MarketRegime

# ── Sector bonuses by regime ──
# Bonus points added to screener score for stocks in these sectors.
# Keys = sector names as returned by StockInfo.sector (from KIS).
# Dict values: {sector_name: bonus_points}
SECTOR_REGIME_BONUS: dict[MarketRegime, dict[str, float]] = {
    MarketRegime.BEAR: {
        "헬스케어": 10.0,
        "제약": 10.0,
        "필수소비재": 8.0,
        "음식료": 8.0,
        "유틸리티": 5.0,
        "통신서비스": 3.0,
        "통신장비": 3.0,
        "의료기기": 8.0,
        "생명공학": 7.0,
        "소프트웨어": 3.0,
        "IT서비스": 3.0,
    },
    MarketRegime.NEUTRAL: {},  # No sector preference in neutral
    MarketRegime.BULL: {
        "반도체": 10.0,
        "IT하드웨어": 8.0,
        "자동차": 5.0,
        "자동차부품": 5.0,
        "금융": 3.0,
        "철강": 3.0,
        "조선": 3.0,
        "기계": 3.0,
    },
}

# ── Dual momentum bonus thresholds ──
# Bonus for stocks with strong relative/absolute momentum.
# Values are applied when stock performance exceeds the threshold.
MOMENTUM_BONUS = {
    "12m_return_top_quarter": 8.0,   # Top 25% 12-month return
    "6m_return_top_quarter": 5.0,    # Top 25% 6-month return
    "3m_return_top_quarter": 3.0,    # Top 25% 3-month return (bear)
}


def get_sector_bonus(regime: MarketRegime, sector: str | None) -> float:
    """Get sector bonus for a stock based on its sector and current regime.

    Args:
        regime: Current market regime.
        sector: Stock sector name (from KIS). None if unknown.

    Returns:
        Bonus points (0.0 if no bonus applies).
    """
    if not sector:
        return 0.0
    bonuses = SECTOR_REGIME_BONUS.get(regime, {})
    # Try exact match, then partial match
    if sector in bonuses:
        return bonuses[sector]
    for key, val in bonuses.items():
        if key in sector or sector in key:
            return val
    return 0.0
