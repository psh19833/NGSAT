"""NGSAT backtest engine — simulates the full trading pipeline on historical data.

CRITICAL: This module is in the backtest/ package.
It MUST NOT import anything from live/.
It uses only core/, data/, strategy/, ml/ shared modules.

Runs the complete NGSAT 3-stage pipeline on historical data:
  1. Regime evaluation (strategy/regime.py)
  2. Stock screening (strategy/screener.py)
  3. ML prediction (ml/inference.py)
  4. Simulated order execution
  5. Portfolio tracking

Simulates trading day-by-day with:
- Starting capital
- Buy/sell execution at closing price
- Position tracking with P/L
- Risk management (stop loss, daily loss limit)
- Full trade log with reasons

Every simulated trade includes a decision reason — same principle as live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from core.config import RiskConfig, StrategyConfig
from core.logger import logger
from core.types import (
    DecisionAction,
    Market,
    PriceData,
    StockInfo,
)
from ml.inference import MLInference, MLPrediction
from ml.training.trainer import PriceRiseModel
from strategy.regime import evaluate_regime
from strategy.screener import screen_stocks
from strategy.entry_timing import refine_entry
from strategy.exit_timing import refine_exit, ExitUrgency
from strategy.mode_selector import select_mode, estimate_volatility_from_prices


@dataclass
class BacktestPosition:
    """Simulated position in backtest."""
    code: str
    name: str
    market: Market
    quantity: int
    buy_price: float
    buy_date: str
    stop_loss_pct: float = 3.0
    stop_loss_reason: str = "기본 손절선 -3%"
    is_force_hold: bool = False


@dataclass
class BacktestTrade:
    """Simulated trade record."""
    code: str
    name: str
    side: str               # buy / sell
    quantity: int
    price: float
    amount: float
    date: str
    action: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Complete backtest result.

    Attributes:
        start_date: Backtest start date.
        end_date: Backtest end date.
        initial_capital: Starting capital.
        final_capital: Ending capital.
        total_return: Total return percentage.
        total_trades: Number of trades executed.
        buy_count: Number of buy trades.
        sell_count: Number of sell trades.
        winning_trades: Number of profitable sells.
        losing_trades: Number of losing sells.
        win_rate: Win rate percentage.
        max_drawdown: Maximum drawdown percentage.
        trades: List of all trade records.
        daily_capital: Daily capital tracking.
        reason: Human-readable summary (Korean).
    """
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    total_trades: int
    buy_count: int
    sell_count: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    max_drawdown: float
    entries_deferred: int = 0
    mode_swing_days: int = 0
    mode_short_term_days: int = 0
    mode_hold_days: int = 0
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_capital: list[float] = field(default_factory=list)
    reason: str = ""


class BacktestEngine:
    """Backtest execution engine.

    Runs the NGSAT pipeline on historical data, simulating trades
    day by day. Does NOT use live/ modules — complete isolation.

    Usage:
        engine = BacktestEngine(model, initial_capital=10_000_000)
        result = engine.run(universe, index_prices)
    """

    def __init__(
        self,
        model: PriceRiseModel,
        initial_capital: float = 10_000_000,
        risk_config: RiskConfig | None = None,
        strategy_config: StrategyConfig | None = None,
        buy_threshold: float = 0.65,
        sell_threshold: float = 0.35,
        minute_model: PriceRiseModel | None = None,
    ):
        self._model = model
        self._minute_model = minute_model
        self._inference = MLInference(model, buy_threshold, sell_threshold, minute_model=minute_model)
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._risk = risk_config or RiskConfig()
        self._strategy = strategy_config or StrategyConfig()
        self._positions: dict[str, BacktestPosition] = {}
        self._trades: list[BacktestTrade] = []
        self._daily_capital: list[float] = []
        self._daily_loss: float = 0.0
        self._is_halted: bool = False
        self._peak_capital: float = initial_capital
        # FIFO queue of buy trades per stock code (for correct win/loss matching)
        self._buy_queue: dict[str, list[BacktestTrade]] = {}
        self._entries_deferred: int = 0
        self._current_mode: str = "swing"
        self._swing_days: int = 0
        self._short_term_days: int = 0
        self._hold_days: int = 0
        # Slippage: seed from initial capital for deterministic backtests

    # ── Slippage model ──
    def _slippage(self, price: float, urgent: bool = False) -> float:
        """Apply slippage to execution price.

        Normal: ±0.1% random (default)
        Urgent (stop loss / force sell): ±0.3% random

        Uses deterministic seed based on trade count for reproducibility.
        """
        import hashlib
        seed = int(hashlib.md5(f"{self._initial_capital}_{len(self._trades)}".encode()).hexdigest()[:8], 16)
        rng_seed = (seed % 10000) / 10000
        slip_pct = 0.003 if urgent else 0.001
        slippage = price * slip_pct * (rng_seed * 2 - 1)
        return price + slippage

    # ── Fee constants ──
    BUY_FEE_RATE = 0.00015    # 매수 수수료 0.015%
    SELL_FEE_RATE = 0.00015   # 매도 수수료 0.015%
    SELL_TAX_RATE = 0.0023    # 농특세 0.20% + 거래소/청산소 0.03%

    def run(
        self,
        universe: list[tuple[StockInfo, list[PriceData]]],
        index_prices: list[PriceData],
        start_day: int = 60,
        minute_provider=None,
    ) -> BacktestResult:
        """Run backtest on historical data.

        Args:
            universe: List of (StockInfo, price history) tuples.
            index_prices: Index price history for regime evaluation.
            start_day: First day to start trading (need history for indicators).

        Returns:
            BacktestResult with full performance metrics.
        """
        if not universe or not index_prices:
            return self._empty_result()

        # Determine the number of trading days
        n_days = min(len(index_prices), max(len(p) for _, p in universe if p))

        logger.info(f"백테스트 시작: {n_days}일, 종목 {len(universe)}개, 자본 {self._initial_capital:,.0f}")

        for day_idx in range(start_day, n_days):
            date_str = str(index_prices[day_idx].timestamp.date()) if day_idx < len(index_prices) else f"day_{day_idx}"

            # Check if halted
            if self._is_halted:
                self._daily_capital.append(self._total_capital(universe, day_idx))
                continue

            # 1. Regime evaluation
            regime_index = index_prices[:day_idx + 1]
            regime_result = evaluate_regime(
                [p.close for p in regime_index],
                [p.volume for p in regime_index],
                config=self._strategy,
            )

            # 1b. Mode selection (하이브리드 2단계)
            vol = estimate_volatility_from_prices(
                [p.close for p in regime_index],
                [p.high for p in regime_index],
                [p.low for p in regime_index],
            )
            mode_decision = select_mode(regime_result, atr_pct=vol, config=self._strategy)
            self._current_mode = mode_decision.mode.value
            is_short_term = self._current_mode == "short_term"

            # 모드별 통계
            if self._current_mode == "swing":
                self._swing_days += 1
            elif self._current_mode == "short_term":
                self._short_term_days += 1
            else:
                self._hold_days += 1

            # 2. Screen stocks
            stocks_for_screening: list[tuple[StockInfo, list[PriceData]]] = []
            for info, prices in universe:
                if len(prices) > day_idx:
                    stocks_for_screening.append((info, prices[:day_idx + 1]))

            screen_result = screen_stocks(stocks_for_screening, regime_result, config=self._strategy)

            # 3. ML predictions for top candidates (모드별 라우팅)
            for candidate in screen_result.candidates:
                if candidate.code in self._positions:
                    continue  # Already holding

                # 포지션 리스크: 최대 보유 종목 수 체크 (break = 루프 종료)
                if self._strategy.max_holdings > 0 and len(self._positions) >= self._strategy.max_holdings:
                    logger.info(
                        f"백테스트 최대 보유({self._strategy.max_holdings}개) 도달 — 신규 진입 생략"
                    )
                    break

                # HOLD 모드: 신규 진입 금지
                if self._current_mode == "hold":
                    continue

                # Find price data for this candidate
                prices = None
                for info, p in universe:
                    if info.code == candidate.code and len(p) > day_idx:
                        prices = p[:day_idx + 1]
                        break

                if prices is None or len(prices) < 60:
                    continue

                # 모드별 ML 진입 예측
                if is_short_term and self._minute_model is not None:
                    # 단타 모드: 분봉 ML로 진입 예측
                    minute_bars = minute_provider(candidate.code, day_idx) if minute_provider else None
                    if minute_bars and len(minute_bars) >= 60:
                        pred = self._inference.predict_minute_entry(candidate, minute_bars)
                    else:
                        pred = self._inference.predict_entry(candidate, prices)
                else:
                    # 스윙 모드: 일봉 ML로 진입 예측 (기존)
                    pred = self._inference.predict_entry(candidate, prices)

                if pred and pred.action == DecisionAction.BUY:
                    entry_price = prices[-1].close
                    if minute_provider is not None:
                        minute_bars = minute_provider(candidate.code, day_idx)
                        if minute_bars:
                            entry = refine_entry(minute_bars)
                            if not entry.should_enter:
                                self._entries_deferred += 1
                                continue
                            if entry.limit_price:
                                entry_price = entry.limit_price
                    self._execute_buy(pred, entry_price, date_str, is_short_term)

            # 4. Check exits for existing positions (모드별 청산)
            positions_to_check = list(self._positions.items())
            for code, pos in positions_to_check:
                if pos.is_force_hold:
                    continue

                prices = None
                for info, p in universe:
                    if info.code == code and len(p) > day_idx:
                        prices = p[:day_idx + 1]
                        break

                if prices is None:
                    continue

                current_price = prices[-1].close
                profit_pct = (current_price - pos.buy_price) / pos.buy_price * 100

                # 청산 정밀화: 분봉 제공 시 매도 긴급도/가격 반영 (하이브리드)
                sell_price = current_price
                exit_ref = None
                if minute_provider is not None:
                    minute_bars = minute_provider(code, day_idx)
                    if minute_bars:
                        exit_ref = refine_exit(minute_bars, profit_pct)
                        if exit_ref.urgency != ExitUrgency.IMMEDIATE and exit_ref.limit_price:
                            sell_price = exit_ref.limit_price

                # Stop loss check (모드별 손절선 반영)
                mode_stop_loss = {
                    "swing": self._strategy.mode_swing_stop_loss_pct,
                    "short_term": self._strategy.mode_short_stop_loss_pct,
                    "hold": self._strategy.mode_hold_stop_loss_pct,
                }
                mode_stop = mode_stop_loss.get(self._current_mode, pos.stop_loss_pct)
                loss_pct = abs(min(profit_pct, 0))
                if loss_pct >= mode_stop:
                    self._execute_sell(
                        pos, sell_price, date_str,
                        DecisionAction.STOP_LOSS,
                        f"손절: {pos.name}({pos.code}) 손실 {loss_pct:.1f}% >= 손절선 {mode_stop:.1f}%",
                    )
                    continue

                # 분봉 선제 청산 (급락/과열익절)
                if exit_ref is not None and exit_ref.should_exit:
                    self._execute_sell(pos, sell_price, date_str, DecisionAction.SELL, f"분봉 청산: {exit_ref.reason}")
                    continue

                # ML 청산 예측 (모드별 라우팅)
                if is_short_term and self._minute_model is not None:
                    minute_bars = minute_provider(code, day_idx) if minute_provider else None
                    if minute_bars and len(minute_bars) >= 60:
                        exit_pred = self._inference.predict_minute_exit(code, pos.name, minute_bars, profit_pct)
                    else:
                        exit_pred = self._inference.predict_exit(code, pos.name, prices, profit_pct)
                else:
                    exit_pred = self._inference.predict_exit(code, pos.name, prices, profit_pct)

                if exit_pred and exit_pred.action == DecisionAction.SELL:
                    self._execute_sell(pos, sell_price, date_str, DecisionAction.SELL, exit_pred.reason)

            # 5. Daily loss check (day-over-day, not max drawdown)
            current_capital = self._total_capital(universe, day_idx)
            self._daily_capital.append(current_capital)

            if current_capital > self._peak_capital:
                self._peak_capital = current_capital

            daily_loss_pct = 0.0
            if len(self._daily_capital) > 1:
                prev = self._daily_capital[-2]
                daily_loss_pct = ((current_capital - prev) / prev * 100) if prev > 0 else 0

            if abs(daily_loss_pct) >= self._risk.daily_loss_limit_pct:
                self._is_halted = True
                logger.warning(f"백테스트 일일 손실 한도 도달: {daily_loss_pct:.1f}% → 매매 중단")

        # Close all remaining positions at last price
        for code, pos in list(self._positions.items()):
            last_price = 0.0
            for info, prices in universe:
                if info.code == code and prices:
                    last_price = prices[-1].close
                    break

            if last_price > 0:
                self._execute_sell(pos, last_price, str(n_days), DecisionAction.SELL, "백테스트 종료 - 전량 매도")

        return self._build_result(start_day, n_days, index_prices)

    def _execute_buy(
        self,
        pred: MLPrediction,
        price: float,
        date_str: str,
        is_short_term: bool = False,
    ) -> None:
        """Execute a simulated buy with mode-aware position sizing and slippage."""
        # 모드별 포지션 크기: 스윙=10%, 단타=5%
        size_pct = 0.05 if is_short_term else 0.10
        budget = self._cash * size_pct
        # Apply slippage: buy pays slightly more (adverse)
        exec_price = self._slippage(price, urgent=False)
        quantity = int(budget / exec_price)

        if quantity <= 0:
            return

        amount = exec_price * quantity
        fee = amount * self.BUY_FEE_RATE
        total_cost = amount + fee

        if total_cost > self._cash:
            return

        self._cash -= total_cost

        self._positions[pred.code] = BacktestPosition(
            code=pred.code,
            name=pred.name,
            market=Market.KOSPI,
            quantity=quantity,
            buy_price=exec_price,
            buy_date=date_str,
            stop_loss_pct=self._risk.default_stop_loss_pct,
        )

        trade = BacktestTrade(
            code=pred.code,
            name=pred.name,
            side="buy",
            quantity=quantity,
            price=exec_price,
            amount=amount,
            date=date_str,
            action=pred.action.value,
            reason=pred.reason,
            evidence=pred.evidence,
        )
        self._trades.append(trade)

        # Track buy in FIFO queue for correct win/loss matching
        self._buy_queue.setdefault(pred.code, []).append(trade)

        logger.debug(f"백테스트 매수: {pred.name}({pred.code}) {quantity}주 @ {exec_price:,.0f} (슬리피지 {exec_price - price:+.1f})")

    def _execute_sell(
        self,
        pos: BacktestPosition,
        price: float,
        date_str: str,
        action: DecisionAction,
        reason: str,
    ) -> None:
        """Execute a simulated sell with slippage (urgent=stop loss/force sell: 0.3%, normal: 0.1%)."""
        urgent = action in (DecisionAction.STOP_LOSS, DecisionAction.FORCE_SELL)
        exec_price = self._slippage(price, urgent=urgent)
        amount = exec_price * pos.quantity
        fee = amount * self.SELL_FEE_RATE
        tax = amount * self.SELL_TAX_RATE
        net_amount = amount - fee - tax
        self._cash += net_amount

        self._trades.append(BacktestTrade(
            code=pos.code,
            name=pos.name,
            side="sell",
            quantity=pos.quantity,
            price=exec_price,
            amount=amount,
            date=date_str,
            action=action.value,
            reason=reason,
        ))

        del self._positions[pos.code]

        logger.debug(f"백테스트 매도: {pos.name}({pos.code}) {pos.quantity}주 @ {exec_price:,.0f} (슬리피지 {exec_price - price:+.1f})")

    def _total_capital(self, universe: list[tuple[StockInfo, list[PriceData]]], day_idx: int) -> float:
        """Calculate total capital (cash + position values)."""
        total = self._cash

        price_map: dict[str, float] = {}
        for info, prices in universe:
            if len(prices) > day_idx:
                price_map[info.code] = prices[day_idx].close

        for code, pos in self._positions.items():
            if code in price_map:
                total += price_map[code] * pos.quantity

        return total

    def _build_result(self, start_day: int, n_days: int, index_prices: list[PriceData]) -> BacktestResult:
        """Build the final backtest result."""
        final_capital = self._cash

        buy_count = sum(1 for t in self._trades if t.side == "buy")
        sell_count = sum(1 for t in self._trades if t.side == "sell")

        # Calculate win/loss from sell trades
        winning = 0
        losing = 0
        for t in self._trades:
            if t.side == "sell":
                # Find corresponding buy (FIFO queue)
                buy_queue = self._buy_queue.get(t.code, [])
                buy_trade = buy_queue.pop(0) if buy_queue else None
                if buy_trade and t.price > buy_trade.price:
                    winning += 1
                elif buy_trade:
                    losing += 1

        win_rate = (winning / (winning + losing) * 100) if (winning + losing) > 0 else 0.0

        total_return = ((final_capital - self._initial_capital) / self._initial_capital * 100) if self._initial_capital > 0 else 0.0

        # Max drawdown
        max_dd = 0.0
        if self._daily_capital:
            peak = self._daily_capital[0]
            for cap in self._daily_capital:
                if cap > peak:
                    peak = cap
                dd = (cap - peak) / peak * 100 if peak > 0 else 0
                if dd < max_dd:
                    max_dd = dd

        start_date = str(index_prices[start_day].timestamp.date()) if start_day < len(index_prices) else ""
        end_date = str(index_prices[-1].timestamp.date()) if index_prices else ""

        reason = (
            f"백테스트 완료: {start_date} ~ {end_date}, "
            f"초기 자본 {self._initial_capital:,.0f} → 최종 {final_capital:,.0f}, "
            f"수익률 {total_return:+.1f}%, "
            f"거래 {len(self._trades)}회 (매수 {buy_count}/매도 {sell_count}), "
            f"승률 {win_rate:.1f}%, 최대 낙폭 {max_dd:.1f}%, "
            f"진입보류 {self._entries_deferred}건, "
            f"모드: 스윙{self._swing_days}일/단타{self._short_term_days}일/관망{self._hold_days}일"
        )

        logger.info(reason)

        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=self._initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            total_trades=len(self._trades),
            buy_count=buy_count,
            sell_count=sell_count,
            winning_trades=winning,
            losing_trades=losing,
            win_rate=win_rate,
            max_drawdown=max_dd,
            entries_deferred=self._entries_deferred,
            trades=self._trades,
            daily_capital=self._daily_capital,
            reason=reason,
        )

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            start_date="",
            end_date="",
            initial_capital=self._initial_capital,
            final_capital=self._initial_capital,
            total_return=0.0,
            total_trades=0,
            buy_count=0,
            sell_count=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            max_drawdown=0.0,
            reason="백테스트 데이터 없음",
        )
