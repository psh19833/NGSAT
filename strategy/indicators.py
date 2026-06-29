"""NGSAT technical indicators — pure calculation functions.

All functions take numpy arrays/lists and return numeric results.
No external dependencies beyond numpy/pandas.
No KIS API calls — pure math.

Every indicator is a single, testable function with clear inputs/outputs.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


# ── Moving Averages ──

def sma(values: Sequence[float], period: int) -> np.ndarray:
    """Simple Moving Average.

    Args:
        values: Price series (e.g. closing prices).
        period: Number of periods to average.

    Returns:
        numpy array of same length; first (period-1) entries are NaN.
    """
    arr = np.asarray(values, dtype=float)
    if len(arr) < period:
        return np.full(len(arr), np.nan)

    result = np.convolve(arr, np.ones(period) / period, mode="valid")
    padded = np.full(len(arr), np.nan)
    padded[period - 1:] = result
    return padded


def ema(values: Sequence[float], period: int) -> np.ndarray:
    """Exponential Moving Average.

    Args:
        values: Price series.
        period: EMA period.

    Returns:
        numpy array of same length.
    """
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return arr

    alpha = 2.0 / (period + 1)
    result = np.empty_like(arr)
    result[0] = arr[0]

    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]

    return result


# ── RSI (Relative Strength Index) ──

def rsi(values: Sequence[float], period: int = 14) -> np.ndarray:
    """Relative Strength Index.

    Args:
        values: Price series (typically closing prices).
        period: RSI period (default 14).

    Returns:
        numpy array of RSI values (0-100); first `period` entries may be NaN.
    """
    arr = np.asarray(values, dtype=float)
    if len(arr) <= period:
        return np.full(len(arr), np.nan)

    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder's smoothing
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    result = np.full(len(arr), np.nan)

    for i in range(period, len(arr)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period

        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))

    return result


# ── MACD (Moving Average Convergence Divergence) ──

def macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD indicator.

    Args:
        values: Price series.
        fast: Fast EMA period (default 12).
        slow: Slow EMA period (default 26).
        signal: Signal line EMA period (default 9).

    Returns:
        Tuple of (macd_line, signal_line, histogram).
    """
    arr = np.asarray(values, dtype=float)

    ema_fast = ema(arr, fast)
    ema_slow = ema(arr, slow)

    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


# ── Bollinger Bands ──

def bollinger_bands(
    values: Sequence[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands.

    Args:
        values: Price series.
        period: SMA period (default 20).
        std_dev: Standard deviation multiplier (default 2.0).

    Returns:
        Tuple of (upper_band, middle_band, lower_band).
    """
    arr = np.asarray(values, dtype=float)

    middle = sma(arr, period)

    # Rolling standard deviation
    rolling_std = np.full(len(arr), np.nan)
    for i in range(period - 1, len(arr)):
        rolling_std[i] = np.std(arr[i - period + 1 : i + 1], ddof=0)

    upper = middle + std_dev * rolling_std
    lower = middle - std_dev * rolling_std

    return upper, middle, lower


# ── ATR (Average True Range) ──

def atr(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> np.ndarray:
    """Average True Range — volatility measure.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: ATR period (default 14).

    Returns:
        numpy array of ATR values.
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(c)

    if n < 2:
        return np.full(n, np.nan)

    # True Range: max of (high-low, |high-prev_close|, |low-prev_close|)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]

    for i in range(1, n):
        tr1 = h[i] - l[i]
        tr2 = abs(h[i] - c[i - 1])
        tr3 = abs(l[i] - c[i - 1])
        tr[i] = max(tr1, tr2, tr3)

    # Wilder's smoothing for ATR
    result = np.full(n, np.nan)
    if n > period:
        result[period] = np.mean(tr[1 : period + 1])
        for i in range(period + 1, n):
            result[i] = (result[i - 1] * (period - 1) + tr[i]) / period

    return result


# ── Volume indicators ──

def volume_ratio(volumes: Sequence[int], period: int = 20) -> np.ndarray:
    """Volume ratio vs recent average.

    Args:
        volumes: Volume series.
        period: Lookback period for average (default 20).

    Returns:
        Ratio of current volume to moving average volume.
        Values > 1.0 mean above-average volume.
    """
    arr = np.asarray(volumes, dtype=float)
    if len(arr) < period:
        return np.full(len(arr), np.nan)

    avg_vol = sma(arr, period)
    return arr / avg_vol


# ── Stochastic Oscillator ──

def stochastic(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """Stochastic Oscillator (%K, %D).

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        k_period: %K period (default 14).
        d_period: %D smoothing period (default 3).

    Returns:
        Tuple of (%K, %D).
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(c)

    k = np.full(n, np.nan)

    for i in range(k_period - 1, n):
        period_high = max(h[i - k_period + 1 : i + 1])
        period_low = min(l[i - k_period + 1 : i + 1])

        if period_high == period_low:
            k[i] = 50.0
        else:
            k[i] = ((c[i] - period_low) / (period_high - period_low)) * 100.0

    d = sma(k, d_period)
    return k, d


# ── Helpers ──

def current_rsi(values: Sequence[float], period: int = 14) -> float:
    """Get the latest RSI value (or NaN if insufficient data)."""
    result = rsi(values, period)
    val = result[-1]
    return float(val) if not np.isnan(val) else float("nan")


def current_macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float]:
    """Get the latest MACD values (macd_line, signal_line, histogram)."""
    macd_line, signal_line, hist = macd(values, fast, slow, signal)
    return float(macd_line[-1]), float(signal_line[-1]), float(hist[-1])


def adx(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> np.ndarray:
    """Average Directional Index — trend strength (0~100).

    ADX < 20: trendless (weak)
    ADX 20~40: trending
    ADX > 40: strong trend

    Uses Wilder's smoothing (modified EMA with alpha=1/period).
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(c)
    if n < period + 1:
        return np.full(n, np.nan)

    # True Range
    tr = np.full(n, np.nan)
    for i in range(1, n):
        hl = h[i] - l[i]
        hc = abs(h[i] - c[i - 1])
        lc = abs(l[i] - c[i - 1])
        tr[i] = max(hl, hc, lc)

    # Directional Movement
    plus_dm = np.full(n, np.nan)
    minus_dm = np.full(n, np.nan)
    for i in range(1, n):
        up_move = h[i] - h[i - 1]
        down_move = l[i - 1] - l[i]
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        else:
            plus_dm[i] = 0.0
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move
        else:
            minus_dm[i] = 0.0

    # Wilder's smoothing (first value = SMA, then EMA-like)
    def wilder_smooth(raw: np.ndarray, p: int) -> np.ndarray:
        result = np.full(n, np.nan)
        result[p] = np.nanmean(raw[1 : p + 1])  # first SMA
        for i in range(p + 1, n):
            result[i] = (result[i - 1] * (p - 1) + raw[i]) / p
        return result

    tr_s = wilder_smooth(tr, period)
    plus_dm_s = wilder_smooth(plus_dm, period)
    minus_dm_s = wilder_smooth(minus_dm, period)

    # Directional Indicators
    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    for i in range(period, n):
        if tr_s[i] > 0:
            plus_di[i] = (plus_dm_s[i] / tr_s[i]) * 100.0
            minus_di[i] = (minus_dm_s[i] / tr_s[i]) * 100.0

    # DX and ADX
    dx = np.full(n, np.nan)
    for i in range(period, n):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = abs(plus_di[i] - minus_di[i]) / di_sum * 100.0

    adx_values = np.full(n, np.nan)
    adx_values[period * 2 - 1] = np.nanmean(dx[period : period * 2])  # first SMA
    for i in range(period * 2, n):
        adx_values[i] = (adx_values[i - 1] * (period - 1) + dx[i]) / period

    return adx_values
