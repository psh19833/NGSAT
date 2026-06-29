"""NGSAT broker adapter base interface.

All securities broker adapters must implement this interface.
This is the abstraction layer that enables multi-broker support
and future subscription service expansion.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core.types import AccountSummary, OrderSide, OrderStatus, Position, PriceData, StockInfo


class BrokerAdapter(ABC):
    """Abstract interface for securities broker APIs.

    Implementations:
    - data/adapters/kis/adapter.py — KIS (Korea Investment & Securities)
    - Future: Naver, Kiwoom, etc.
    """

    @abstractmethod
    async def get_account_summary(self) -> AccountSummary:
        """Fetch current account balance and position summary.

        Returns:
            AccountSummary with total asset, deposit, P/L, etc.
        """
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Fetch all currently held positions.

        Returns:
            List of Position objects.
        """
        ...

    @abstractmethod
    async def get_price(self, code: str) -> PriceData:
        """Fetch real-time price for a single stock.

        Args:
            code: 6-digit stock code (e.g. "005930")

        Returns:
            PriceData with current OHLCV.
        """
        ...

    @abstractmethod
    async def get_price_history(
        self, code: str, start: datetime, end: datetime
    ) -> list[PriceData]:
        """Fetch historical price data for backtesting.

        Args:
            code: 6-digit stock code
            start: Start date
            end: End date

        Returns:
            List of PriceData sorted by date ascending.
        """
        ...

    @abstractmethod
    async def get_stock_list(self) -> list[StockInfo]:
        """Fetch all tradeable stocks on the market.

        Returns:
            List of StockInfo with code, name, market.
        """
        ...

    @abstractmethod
    async def submit_order(
        self,
        code: str,
        side: OrderSide,
        quantity: int,
        price: Optional[float] = None,
    ) -> str:
        """Submit a buy or sell order.

        Args:
            code: 6-digit stock code
            side: BUY or SELL
            quantity: Number of shares
            price: Limit price (None for market order)

        Returns:
            Order ID from the broker.

        Raises:
            BrokerError: If order submission fails.
        """
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Check the current status of a submitted order.

        Args:
            order_id: Broker-assigned order ID to check.

        Returns:
            OrderStatus enum value (SUBMITTED, FILLED, PARTIALLY_FILLED,
            CANCELLED, or REJECTED).

        Raises:
            BrokerError: If status inquiry fails or order_id is unknown.
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            True if cancellation succeeded.
        """
        ...

    @abstractmethod
    async def is_market_open(self) -> bool:
        """Check if the stock market is currently open.

        Returns:
            True if market is in trading hours.
        """
        ...

    async def get_minute_history(
        self,
        code: str,
        base_time: Optional[datetime] = None,
        include_past: bool = True,
    ) -> list[PriceData]:
        """Fetch intraday minute-bar price data (optional capability).

        Adapters that support intraday data (e.g. KIS) override this method.
        The default implementation raises NotImplementedError so that callers
        can fall back gracefully when an adapter does not support minute bars.

        Args:
            code: 6-digit stock code.
            base_time: Reference time (only HH:MM:SS used); None = now.
            include_past: Whether to include earlier bars of the same day.

        Returns:
            List of PriceData minute bars.
        """
        raise NotImplementedError("이 어댑터는 분봉 조회를 지원하지 않습니다")
