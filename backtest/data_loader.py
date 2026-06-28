"""NGSAT backtest data loader — loads historical price data for backtesting.

CRITICAL: This module is in the backtest/ package.
It MUST NOT import anything from live/.
It shares only core/, data/, strategy/, ml/ modules.

Data sources:
1. Database (MarketDataCache table) — primary source for cached historical data
2. KIS API (via data/adapters/kis/) — for fetching data not yet cached
3. Synthetic data — for testing the backtest engine itself
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from core.types import Market, PriceData, StockInfo


def load_from_cache(
    code: str,
    start_date: str,
    end_date: str,
) -> list[PriceData]:
    """Load price history from the database cache.

    Args:
        code: 6-digit stock code.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).

    Returns:
        List of PriceData sorted by date ascending.
        Empty list if no data found or DB unavailable.
    """
    try:
        from data.db import db_session
        from data.repository import MarketDataRepository

        with db_session() as session:
            repo = MarketDataRepository(session)
            records = repo.get_price_history(code, start_date, end_date)

            return [
                PriceData(
                    code=r.code,
                    timestamp=datetime.strptime(r.date, "%Y-%m-%d"),
                    open=float(r.open),
                    high=float(r.high),
                    low=float(r.low),
                    close=float(r.close),
                    volume=int(r.volume),
                    change_pct=float(r.change_pct),
                )
                for r in records
            ]
    except Exception:
        return []  # DB not available — return empty


async def load_from_kis(
    code: str,
    start: datetime,
    end: datetime,
    adapter=None,
) -> list[PriceData]:
    """Load price history from KIS API.

    Args:
        code: 6-digit stock code.
        start: Start date.
        end: End date.
        adapter: KisAdapter instance. If None, creates from env.

    Returns:
        List of PriceData sorted by date ascending.
    """
    if adapter is None:
        from data.adapters.kis.adapter import KisAdapter
        adapter = KisAdapter.from_env()

    try:
        history = await adapter.get_price_history(code, start, end)
        return history
    finally:
        if adapter is not None:
            await adapter.close()


def generate_synthetic_data(
    code: str,
    n_days: int = 250,
    start_price: float = 50000,
    trend: float = 50,
    volatility: float = 0.02,
    seed: int = 42,
) -> list[PriceData]:
    """Generate synthetic price data for backtesting tests.

    Creates realistic-looking price data with controllable trend and volatility.
    Used for testing the backtest engine without real market data.

    Args:
        code: Stock code.
        n_days: Number of trading days.
        start_price: Initial price.
        trend: Daily price drift (positive = uptrend).
        volatility: Daily volatility as fraction (0.02 = 2%).
        seed: Random seed for reproducibility.

    Returns:
        List of PriceData.
    """
    rng = np.random.default_rng(seed)

    prices: list[PriceData] = []
    current = start_price
    base_date = datetime(2025, 1, 1)

    for i in range(n_days):
        # Geometric brownian motion
        daily_return = rng.normal(trend / start_price, volatility)
        current = current * (1 + daily_return)

        # Generate OHLC from close
        intraday_vol = current * volatility * 0.5
        open_price = current + rng.normal(0, intraday_vol * 0.3)
        high = max(open_price, current) + abs(rng.normal(0, intraday_vol))
        low = min(open_price, current) - abs(rng.normal(0, intraday_vol))

        volume = int(rng.integers(50000, 200000))

        # Previous close for change_pct
        prev_close = prices[-1].close if prices else open_price
        change_pct = ((current - prev_close) / prev_close * 100
                      ) if prev_close > 0 else 0.0

        prices.append(PriceData(
            code=code,
            timestamp=base_date + timedelta(days=i),
            open=float(open_price),
            high=float(high),
            low=float(low),
            close=float(current),
            volume=volume,
            change_pct=float(change_pct),
        ))

    return prices


def generate_synthetic_index(
    n_days: int = 250,
    start_value: float = 2500,
    trend: float = 2,
    volatility: float = 0.01,
    seed: int = 100,
) -> list[PriceData]:
    """Generate synthetic index data for regime evaluation in backtests.

    Args:
        n_days: Number of trading days.
        start_value: Initial index value.
        trend: Daily drift.
        volatility: Daily volatility.
        seed: Random seed.

    Returns:
        List of PriceData representing index values.
    """
    return generate_synthetic_data(
        code="INDEX",
        n_days=n_days,
        start_price=start_value,
        trend=trend,
        volatility=volatility,
        seed=seed,
    )


def generate_synthetic_universe(
    n_stocks: int = 20,
    n_days: int = 250,
    seed: int = 42,
) -> list[tuple[StockInfo, list[PriceData]]]:
    """Generate a synthetic stock universe for backtesting.

    Creates a mix of uptrending, downtrending, and sideways stocks.

    Args:
        n_stocks: Number of stocks to generate.
        n_days: Days of history per stock.
        seed: Base random seed.

    Returns:
        List of (StockInfo, price history) tuples.
    """
    universe: list[tuple[StockInfo, list[PriceData]]] = []

    for i in range(n_stocks):
        code = f"{i + 1:06d}"
        name = f"synthetic_{i + 1}"

        # Mix of trends: 40% up, 30% sideways, 30% down
        if i < n_stocks * 0.4:
            trend = 80 + i * 5
            market = Market.KOSPI
        elif i < n_stocks * 0.7:
            trend = 0
            market = Market.KOSDAQ
        else:
            trend = -60 - i * 3
            market = Market.KOSPI

        prices = generate_synthetic_data(
            code=code,
            n_days=n_days,
            start_price=30000 + i * 5000,
            trend=trend,
            volatility=0.02 + (i % 3) * 0.005,
            seed=seed + i,
        )

        info = StockInfo(code=code, name=name, market=market)
        universe.append((info, prices))

    return universe


def generate_synthetic_minute_bars(
    day_bar: PriceData,
    n_bars: int = 20,
    intraday_vol: float = 0.004,
    seed: int = 0,
) -> list[PriceData]:
    """일봉 한 개를 그날의 분봉 궤적(n_bars개)으로 분해(합성).

    open에서 시작해 close로 수렴하는 선형 경로에 장중 노이즈를 더하고,
    일봉 high/low 범위 안에 클리핑한다. 실제 과거 분봉이 없는 상황에서
    백테스트의 진입/청산 정밀화(refine_entry/refine_exit)를 검증하기 위한
    합성 데이터다.

    Args:
        day_bar: 분해할 일봉 PriceData.
        n_bars: 생성할 분봉 개수.
        intraday_vol: 장중 변동 크기(일봉 시가 대비 비율).
        seed: 난수 시드.

    Returns:
        n_bars개의 분봉 PriceData (시간 오름차순).
    """
    rng = np.random.default_rng(seed)
    o, h, l = float(day_bar.open), float(day_bar.high), float(day_bar.low)
    c = float(day_bar.close)
    base = max(o, 1.0)
    bars: list[PriceData] = []
    base_ts = day_bar.timestamp

    for i in range(n_bars):
        frac = (i + 1) / n_bars
        mid = o + (c - o) * frac
        price = mid + rng.normal(0, base * intraday_vol)
        price = min(max(price, l), h)  # 일봉 범위 클리핑
        prev = bars[-1].close if bars else o
        hi = min(
            max(price, prev) + abs(rng.normal(0, base * intraday_vol * 0.3)), h)
        lo = max(
            min(price, prev) - abs(rng.normal(0, base * intraday_vol * 0.3)), l)
        bars.append(PriceData(
            code=day_bar.code,
            timestamp=base_ts + timedelta(minutes=i),
            open=float(prev),
            high=float(hi),
            low=float(lo),
            close=float(price),
            volume=max(int(day_bar.volume / n_bars), 1),
            change_pct=0.0,
        ))

    return bars


def synthetic_minute_provider(
    universe: list[tuple[StockInfo, list[PriceData]]],
    n_bars: int = 20,
    intraday_vol: float = 0.004,
):
    """universe 기반 분봉 provider 콜백 생성: (code, day_idx) -> list[PriceData] | None.

    BacktestEngine.run(minute_provider=...) 인자로 전달하면, 백테스트가
    라이브와 동일한 진입/청산 정밀화 경로를 태운다.
    """
    price_map = {info.code: prices for info, prices in universe}

    def provider(code: str, day_idx: int) -> list[PriceData] | None:
        prices = price_map.get(code)
        if not prices or day_idx >= len(prices):
            return None
        return generate_synthetic_minute_bars(
            prices[day_idx], n_bars=n_bars, intraday_vol=intraday_vol, seed=day_idx,
        )

    return provider
