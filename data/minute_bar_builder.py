"""NGSAT WebSocket tick → 1-minute OHLCV bar builder.

Receives real-time trade ticks from KIS WebSocket (H0UCNT0),
accumulates them into 1-minute candlesticks in memory.
No REST API calls, no database I/O — pure memory operation.

Usage:
    builder = MinuteBarBuilder()
    builder.feed("005930", 75000, 75100, 74900, 74800, 12345, "142512")
    bars = builder.get_bars("005930", 60)  # latest 60 bars
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from core.types import PriceData


class MinuteBarBuilder:
    """Accumulate WebSocket trade ticks into 1-minute OHLCV bars.

    Thread-safe if used from a single asyncio task (KIS WebSocket
    listener). Each call to feed() processes one tick; when the
    minute boundary crosses, the previous minute's bar is finalized.

    Attributes:
        max_bars: Maximum number of minute bars to keep per code.
    """

    def __init__(self, max_bars: int = 120):
        self._max_bars = max_bars

        # code → list of ticks in the current (incomplete) minute
        self._buffers: dict[str, list[dict]] = defaultdict(list)

        # code → list of completed PriceData bars (ascending, oldest first)
        self._bars: dict[str, list[PriceData]] = defaultdict(list)

        # code → current minute label "HHMM"
        self._current_minute: dict[str, str] = {}

    # ── Public API ──

    def feed(
        self,
        code: str,
        price: float,
        high: float,
        low: float,
        open_price: float,
        volume: int,
        timestamp: str,
    ) -> None:
        """Process one trade tick from WebSocket.

        Args:
            code: 6-digit stock code.
            price: Current trade price.
            high: Today's high price so far.
            low: Today's low price so far.
            open_price: Today's open price.
            volume: Accumulated volume for today.
            timestamp: HHMMSS or HHMM format string.
        """
        minute_key = timestamp[:4]  # "HHMM"
        prev_minute = self._current_minute.get(code)

        if prev_minute is not None and minute_key != prev_minute:
            self._flush(code, prev_minute)

        self._current_minute[code] = minute_key
        self._buffers[code].append({
            "price": price,
            "high": high,
            "low": low,
            "open": open_price,
            "volume": volume,
            "timestamp": timestamp,
        })

    def get_bars(self, code: str, n: int = 60) -> list[PriceData]:
        """Get the latest N completed minute bars for a stock code.

        Returns:
            List of PriceData in ascending time order.
            May be shorter than n if insufficient data collected.
        """
        bars = self._bars.get(code, [])
        return bars[-n:] if len(bars) > n else bars

    def has_enough(self, code: str, min_bars: int = 60) -> bool:
        """Check whether enough minute bars have been accumulated.

        Used to decide whether 분봉 ML can be activated.
        """
        return len(self._bars.get(code, [])) >= min_bars

    def all_codes(self) -> list[str]:
        """Return all stock codes that have at least one bar."""
        return list(self._bars.keys())

    def clear(self, code: Optional[str] = None) -> None:
        """Clear accumulated data for a code (or all codes if None)."""
        if code:
            self._bars.pop(code, None)
            self._buffers.pop(code, None)
            self._current_minute.pop(code, None)
        else:
            self._bars.clear()
            self._buffers.clear()
            self._current_minute.clear()

    # ── Internal ──

    def _flush(self, code: str, minute: str) -> None:
        """Finalize the current minute's bar and append to _bars.

        Called automatically when a new minute's first tick arrives.
        If no ticks were received for this minute (gap), the previous
        bar's close is carried forward.
        """
        ticks = self._buffers.get(code, [])
        if not ticks:
            return

        # Build the completed OHLCV bar
        opens = [t["open"] for t in ticks if t["open"] > 0]
        closes = [t["price"] for t in ticks]
        highs = [t["high"] for t in ticks]
        lows = [t["low"] for t in ticks]
        volumes = [t["volume"] for t in ticks]

        # Use last tick's timestamp instead of datetime.now()
        last_ts = ticks[-1].get("timestamp", "") if ticks else ""
        if len(last_ts) >= 4:
            hour = int(minute[:2])
            min_min = int(minute[2:4])
            bar_ts = datetime.now().replace(hour=hour, minute=min_min, second=0, microsecond=0)
        else:
            bar_ts = datetime.now()

        bar = PriceData(
            code=code,
            timestamp=bar_ts,
            open=opens[0] if opens else closes[0],
            high=max(highs) if highs else closes[-1],
            low=min(lows) if lows else closes[-1],
            close=closes[-1],
            volume=volumes[-1] if volumes else 0,
        )

        self._bars[code].append(bar)
        self._buffers[code] = []

        # Trim to max_bars
        if len(self._bars[code]) > self._max_bars:
            self._bars[code] = self._bars[code][-self._max_bars:]
