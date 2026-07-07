"""NGSAT live trading — shared cycle data models.

Phase 2 refactoring: extracts shared data types from orchestrator.py
so EntryPlanner, ExitManager, and Orchestrator all use the same context.

CycleContext is created once per cycle in orchestrator.run_cycle()
and passed to EntryPlanner / ExitManager methods.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.types import AccountSummary, MarketRegime, Position, PriceData, StrategyMode, now_kst


@dataclass
class CycleContext:
    """Mutable context flowing through one trading cycle.

    Created at the start of run_cycle() and shared between all phases.
    Entry and exit managers read/write this context during execution.
    """
    # ── Immutable inputs (set at cycle start) ──
    cycle_number: int = 0
    timestamp: datetime = field(default_factory=now_kst)
    account: AccountSummary | None = None
    current_positions: list[Position] = field(default_factory=list)
    held_codes: set[str] = field(default_factory=set)
    held_quantities: dict[str, int] = field(default_factory=dict)
    index_prices: list[PriceData] = field(default_factory=list)
    stock_universe: list[tuple[Any, list[PriceData]]] = field(default_factory=list)
    sector_lookup: dict[str, str] = field(default_factory=dict)
    held_sector_counts: dict[str, int] = field(default_factory=dict)

    # ── Mutable state (updated during cycle) ──
    regime_score: float = 50.0
    regime: MarketRegime = MarketRegime.NEUTRAL
    mode: StrategyMode = StrategyMode.SWING
    mode_str: str = "swing"
    is_short_term: bool = False
    market_open: bool = False
    trading_allowed: bool = True
    regime_skipped: bool = False
    atr_vol_pct: float = 0.0
    preset_change: str | None = None

    # ── Daily trade tracking ──
    daily_trade_date: str = ""
    daily_trade_count: int = 0

    # ── Minute data cache (per-cycle) ──
    minute_cache: dict[str, list[PriceData]] = field(default_factory=dict)
