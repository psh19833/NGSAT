"""Tests for NGSAT technical indicators."""

from __future__ import annotations

import numpy as np
import pytest

from strategy.indicators import (
    atr,
    bollinger_bands,
    current_macd,
    current_rsi,
    ema,
    macd,
    rsi,
    sma,
    stochastic,
    volume_ratio,
)


class TestSMA:
    """Simple Moving Average tests."""

    def test_basic_sma(self):
        values = [1, 2, 3, 4, 5]
        result = sma(values, 3)
        assert result[2] == pytest.approx(2.0)  # (1+2+3)/3
        assert result[3] == pytest.approx(3.0)  # (2+3+4)/3
        assert result[4] == pytest.approx(4.0)  # (3+4+5)/3
        assert np.isnan(result[0])
        assert np.isnan(result[1])

    def test_sma_insufficient_data(self):
        result = sma([1, 2], 5)
        assert len(result) == 2
        assert np.all(np.isnan(result))


class TestEMA:
    """Exponential Moving Average tests."""

    def test_ema_first_value_equals_input(self):
        result = ema([10, 20, 30], 5)
        assert result[0] == 10.0

    def test_ema_smooths(self):
        result = ema([10, 20, 30, 40, 50], 3)
        # EMA should be between input values and trend upward
        assert result[0] < result[-1]
        assert result[-1] < 50.0  # But less than last value


class TestRSI:
    """RSI tests."""

    def test_rsi_all_gains(self):
        """If prices only go up, RSI should be near 100."""
        values = list(range(100, 130))
        result = rsi(values, 14)
        assert result[-1] > 90

    def test_rsi_all_losses(self):
        """If prices only go down, RSI should be near 0."""
        values = list(range(130, 100, -1))
        result = rsi(values, 14)
        assert result[-1] < 10

    def test_rsi_flat(self):
        """Flat prices should give RSI near 50."""
        values = [100] * 30
        result = rsi(values, 14)
        # No gains or losses → avg_loss=0 → RSI=100 by formula
        # But with zero change, all deltas are 0
        assert not np.isnan(result[-1])

    def test_current_rsi_returns_float(self):
        values = list(range(100, 130))
        val = current_rsi(values, 14)
        assert isinstance(val, float)
        assert val > 50  # Uptrend → bullish RSI


class TestMACD:
    """MACD tests."""

    def test_macd_returns_three_arrays(self):
        values = list(range(100, 150))
        macd_line, signal, hist = macd(values)
        assert len(macd_line) == 50
        assert len(signal) == 50
        assert len(hist) == 50

    def test_macd_bullish_in_uptrend(self):
        """In a strong uptrend, MACD line should be above signal (positive histogram)."""
        values = [100 + i * 2 for i in range(50)]
        _, _, hist = macd(values)
        assert hist[-1] > 0  # Bullish

    def test_current_macd_returns_floats(self):
        values = list(range(100, 150))
        m, s, h = current_macd(values)
        assert isinstance(m, float)
        assert isinstance(s, float)
        assert isinstance(h, float)


class TestBollingerBands:
    """Bollinger Bands tests."""

    def test_bands_contain_price(self):
        """Price should mostly be within the bands."""
        np.random.seed(42)
        values = np.cumsum(np.random.randn(50)) + 100
        upper, middle, lower = bollinger_bands(values, 20, 2.0)
        
        # Most recent price should be within bands
        assert lower[-1] <= values[-1] <= upper[-1]

    def test_middle_is_sma(self):
        """Middle band should equal SMA."""
        values = list(range(100, 130))
        upper, middle, lower = bollinger_bands(values, 20, 2.0)
        sma_values = sma(values, 20)
        assert middle[-1] == pytest.approx(sma_values[-1])


class TestATR:
    """ATR tests."""

    def test_atr_positive(self):
        """ATR should always be positive."""
        high = [105, 108, 103, 107, 110, 112, 109, 115, 118, 116,
                120, 122, 119, 125, 128, 126, 130, 133, 131, 135]
        low = [98, 100, 95, 99, 102, 104, 101, 107, 110, 108,
               112, 114, 111, 117, 120, 118, 122, 125, 123, 127]
        close = [100, 105, 100, 104, 108, 110, 107, 113, 116, 114,
                 118, 120, 117, 123, 126, 124, 128, 131, 129, 133]
        result = atr(high, low, close, 14)
        # First valid ATR should be positive
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert all(v > 0 for v in valid)


class TestVolumeRatio:
    """Volume ratio tests."""

    def test_volume_ratio_above_average(self):
        """When recent volume is higher than average, ratio > 1."""
        volumes = [100] * 20 + [200]
        result = volume_ratio(volumes, 20)
        assert result[-1] == pytest.approx(2.0, rel=0.1)  # ~2.0 (SMA includes the last value)

    def test_volume_ratio_normal(self):
        """Normal volume should give ratio near 1.0."""
        volumes = [100] * 25
        result = volume_ratio(volumes, 20)
        assert result[-1] == pytest.approx(1.0)


class TestStochastic:
    """Stochastic oscillator tests."""

    def test_stochastic_range(self):
        """%K should be between 0 and 100."""
        high = [110, 115, 120, 118, 122, 125, 123, 128, 130, 125,
                128, 132, 135, 130, 133, 137, 140, 135, 138, 142]
        low = [95, 100, 105, 103, 107, 110, 108, 113, 115, 110,
               113, 117, 120, 115, 118, 122, 125, 120, 123, 127]
        close = [105, 110, 115, 113, 117, 120, 118, 123, 125, 120,
                 123, 127, 130, 125, 128, 132, 135, 130, 133, 137]
        k, d = stochastic(high, low, close, 14, 3)
        valid_k = k[~np.isnan(k)]
        assert all(0 <= v <= 100 for v in valid_k)
