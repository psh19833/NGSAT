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


# ── Advanced Indicators (P-60) ──

def mfi(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[int],
    period: int = 14,
) -> np.ndarray:
    """Money Flow Index — RSI with volume weighting.

    Args:
        high, low, close, volume: OHLCV data.
        period: Lookback period (default 14).

    Returns:
        MFI values array (0~100). First (period*2-1) entries are NaN.
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    n = len(c)
    mfi_vals = np.full(n, np.nan)
    if n < period + 1:
        return mfi_vals
    typical = (h + l + c) / 3.0
    money_flow = typical * v
    for i in range(period, n):
        pos = 0.0
        neg = 0.0
        for j in range(i - period + 1, i + 1):
            if typical[j] > typical[j - 1]:
                pos += money_flow[j]
            else:
                neg += money_flow[j]
        if pos + neg > 0:
            mfi_vals[i] = 100.0 - (100.0 / (1.0 + pos / neg))
    return mfi_vals


def obv(close: Sequence[float], volume: Sequence[int]) -> np.ndarray:
    """On-Balance Volume — cumulative volume from price direction.

    Args:
        close: Closing prices.
        volume: Volume data.

    Returns:
        OBV values array (cumulative).
    """
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    n = len(c)
    obv_vals = np.zeros(n)
    for i in range(1, n):
        if c[i] > c[i - 1]:
            obv_vals[i] = obv_vals[i - 1] + v[i]
        elif c[i] < c[i - 1]:
            obv_vals[i] = obv_vals[i - 1] - v[i]
        else:
            obv_vals[i] = obv_vals[i - 1]
    return obv_vals


def obv_slope(obv_vals: np.ndarray, period: int = 20) -> float:
    """OBV linear regression slope — trend strength.

    Positive = accumulation, Negative = distribution.
    Normalized to prevent extreme values.
    """
    if len(obv_vals) < period:
        return 0.0
    x = np.arange(period)
    y = obv_vals[-period:]
    slope = np.polyfit(x, y, 1)[0]
    max_abs = np.abs(y).max() or 1
    return float(slope / max_abs * 1000)


def adx_with_di(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ADX with DI+ and DI- for trend strength + direction.

    Returns (adx_values, di_plus, di_minus).
    Same logic as adx() but also returns directional indicators.
    adx() remains unchanged for backward compatibility.
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(c)
    adx_vals = np.full(n, np.nan)
    di_plus_vals = np.full(n, np.nan)
    di_minus_vals = np.full(n, np.nan)
    if n < period + 1:
        return adx_vals, di_plus_vals, di_minus_vals
    tr = np.full(n, np.nan)
    for i in range(1, n):
        hl = h[i] - l[i]
        hc = abs(h[i] - c[i - 1])
        lc = abs(l[i] - c[i - 1])
        tr[i] = max(hl, hc, lc)
    plus_dm = np.full(n, np.nan)
    minus_dm = np.full(n, np.nan)
    for i in range(1, n):
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    def wilder(raw, p):
        r = np.full(n, np.nan)
        r[p] = np.nanmean(raw[1 : p + 1])
        for i in range(p + 1, n):
            r[i] = (r[i - 1] * (p - 1) + raw[i]) / p
        return r

    tr_s = wilder(tr, period)
    plus_dm_s = wilder(plus_dm, period)
    minus_dm_s = wilder(minus_dm, period)
    for i in range(period, n):
        if tr_s[i] > 0:
            di_plus_vals[i] = (plus_dm_s[i] / tr_s[i]) * 100.0
            di_minus_vals[i] = (minus_dm_s[i] / tr_s[i]) * 100.0
    dx = np.full(n, np.nan)
    for i in range(period, n):
        s = di_plus_vals[i] + di_minus_vals[i]
        if s > 0:
            dx[i] = abs(di_plus_vals[i] - di_minus_vals[i]) / s * 100.0
    adx_vals[period * 2 - 1] = np.nanmean(dx[period : period * 2])
    for i in range(period * 2, n):
        adx_vals[i] = (adx_vals[i - 1] * (period - 1) + dx[i]) / period
    return adx_vals, di_plus_vals, di_minus_vals


def relative_strength(
    stock_close: Sequence[float],
    index_close: Sequence[float],
    period: int = 20,
) -> float:
    """Relative Strength vs market index.

    RS = stock_return / index_return. >1.0 = outperforming.
    Fallback to 1.0 on invalid data.
    """
    if len(stock_close) < period + 1 or len(index_close) < period + 1:
        return 1.0
    sr = (stock_close[-1] - stock_close[-period]) / (stock_close[-period] or 1)
    ir = (index_close[-1] - index_close[-period]) / (index_close[-period] or 1)
    return sr / ir if ir != 0 else 1.0


def detect_hammer(open_p: float, high: float, low: float, close: float) -> bool:
    """Hammer candlestick — long lower shadow, small body."""
    body = abs(close - open_p)
    if body == 0:
        return False
    lower = min(open_p, close) - low
    upper = high - max(open_p, close)
    return lower >= body * 2.0 and upper <= body * 0.3


def detect_engulfing(
    prev_open: float, prev_close: float, open_p: float, close: float,
) -> bool:
    """Bullish Engulfing — green candle fully engulfs previous red body."""
    return (close > open_p and prev_close < prev_open
            and close > prev_open and open_p < prev_close)


def detect_morning_star(
    o1: float, c1: float, o2: float, c2: float, o3: float, c3: float,
) -> bool:
    """Morning Star — 3-candle bullish reversal."""
    b1 = abs(c1 - o1)
    b2 = abs(c2 - o2)
    b3 = abs(c3 - o3)
    return (c1 < o1 and b3 > 0 and c3 > o3
            and b2 < b1 * 0.3 and b2 < b3 * 0.3
            and c3 > (o1 + c1) / 2)
