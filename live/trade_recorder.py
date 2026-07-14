"""Trade Recorder — centralized trade settlement + DB write.

Phase 2 follow-up (추천작업 #1, #2):
- Wraps Session factory + TradeRepository + PositionRepository
- ExitManager._record_sell → trade_recorder.record_sell()
- Orchestrator._confirm_pending_buys → trade_recorder.confirm_pending_buys()
- EntryPlanner pending buys → trade_recorder.record_pending_buy()

SRP: 모든 거래 기록은 이 클래스를 통해서만 DB에 기록된다.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Coroutine

from core.logger import logger
from core.types import DecisionAction, OrderSide


class TradeRecorder:
    """Centralized trade recording + pending order settlement.

    One instance lives in TradingOrchestrator, injected into EntryPlanner
    and ExitManager so both can record trades without direct DB access.
    """

    def __init__(self, session_factory) -> None:
        self._Session = session_factory
        # Pending buy trades: appended by EntryPlanner, confirmed by orchestrator cycle
        self._pending_buy_trades: list[dict] = []

    # ── Pending buy tracking ──

    @property
    def pending_buy_trades(self) -> list[dict]:
        return self._pending_buy_trades

    def record_pending_buy(self, trade_data: dict) -> None:
        """Register a buy order that hasn't been confirmed filled yet.

        Called by EntryPlanner after execute_buy() returns success.
        """
        self._pending_buy_trades.append(trade_data)

    async def confirm_pending_buys(
        self,
        fetch_positions: Callable[[], Coroutine[Any, Any, list]],
    ) -> None:
        """Check pending buys against broker positions, save confirmed ones to DB.

        TTL: 24시간 경과 미체결 주문 자동 제거.

        Args:
            fetch_positions: Async callable returning list[Position].
        """
        if not self._pending_buy_trades:
            return
        try:
            # TTL filter
            now_ts = time.time()
            self._pending_buy_trades = [
                p for p in self._pending_buy_trades
                if now_ts - p.get("timestamp", 0) < 86400
            ]
            if not self._pending_buy_trades:
                return

            confirmed_positions = await fetch_positions()
            confirmed_codes = {p.code for p in confirmed_positions}

            confirmed = [p for p in self._pending_buy_trades if p["code"] in confirmed_codes]
            for pending in confirmed:
                try:
                    from data.repository import TradeRepository
                    with self._Session() as session:
                        trade_price = pending.get("fill_price", 0) or pending["price"]
                        TradeRepository(session).save_trade(
                            code=pending["code"], name=pending["name"],
                            side=OrderSide.BUY, quantity=pending["quantity"],
                            price=trade_price, amount=pending["amount"],
                            action=pending["action"], reason=pending["reason"],
                        )
                        session.commit()
                    self._pending_buy_trades.remove(pending)
                    logger.info(
                        f"매수 체결 확인: {pending['name']}({pending['code']}) "
                        f"{pending['quantity']}주 — trade 기록 저장"
                    )
                except Exception as e:
                    logger.warning(f"매수 체결 저장 실패: {e}")

            if self._pending_buy_trades:
                pending_codes = [p["code"] for p in self._pending_buy_trades]
                logger.info(f"매수 미체결(다음 사이클 재확인): {pending_codes}")
        except Exception as e:
            logger.warning(f"매수 체결 확인 실패(재확인): {e}")

    # ── Sell trade recording ──

    # ── Win rate stats for Kelly Criterion ──

    def get_kelly_stats(self) -> dict:
        """Calculate win rate and avg win/loss ratio for Kelly Criterion.

        Queries last 100 trades from DB. Returns safe defaults if < 20 trades.
        Win = sell trade with profit (sell_price > avg buy_price).
        Loss = sell trade at loss (stop_loss or price decline).

        Returns:
            dict with: win_rate (0~1), avg_win_pct, avg_loss_pct, trade_count
        """
        try:
            from data.repository import TradeRepository
            from collections import defaultdict

            with self._Session() as session:
                trades = TradeRepository(session).get_recent_trades(limit=100)

            # Group by code → track buy prices for each position
            buys: dict[str, list[dict]] = defaultdict(list)
            wins = 0
            losses = 0
            total_win_pct = 0.0
            total_loss_pct = 0.0

            for t in trades:
                if t.side == "buy":
                    buys[t.code].append({"qty": t.quantity, "price": t.price})
                elif t.side == "sell":
                    inventory = buys.get(t.code, [])
                    if not inventory:
                        continue
                    # FIFO matching
                    sell_qty = t.quantity
                    total_cost = 0.0
                    matched_qty = 0
                    while sell_qty > 0 and inventory:
                        b = inventory[0]
                        use_qty = min(sell_qty, b["qty"])
                        total_cost += use_qty * b["price"]
                        matched_qty += use_qty
                        b["qty"] -= use_qty
                        if b["qty"] <= 0:
                            inventory.pop(0)
                        sell_qty -= use_qty
                    if matched_qty > 0:
                        avg_buy = total_cost / matched_qty
                        profit_pct = (t.price - avg_buy) / avg_buy * 100
                        if profit_pct > 0:
                            wins += 1
                            total_win_pct += profit_pct
                        else:
                            losses += 1
                            total_loss_pct += abs(profit_pct)

            total = wins + losses
            if total < 20:
                return {"win_rate": 0.5, "avg_win_pct": 3.0, "avg_loss_pct": 2.0,
                        "trade_count": total, "use_fallback": True}

            win_rate = wins / total
            avg_win = total_win_pct / max(wins, 1)
            avg_loss = total_loss_pct / max(losses, 1)
            return {"win_rate": win_rate, "avg_win_pct": avg_win,
                    "avg_loss_pct": avg_loss, "trade_count": total, "use_fallback": False}
        except Exception as e:
            logger.warning(f"Kelly 통계 계산 실패(폴백): {e}")
            return {"win_rate": 0.5, "avg_win_pct": 3.0, "avg_loss_pct": 2.0,
                    "trade_count": 0, "use_fallback": True}

    def record_sell(
        self,
        exec_result,
        position,
        sell_price: float | None,
        action: DecisionAction,
        reason: str,
        partial_sold_qty: int | None = None,
    ) -> None:
        """Record a sell trade to DB + update/close position.

        Called by ExitManager after execute_sell().
        Mirrors the original orchestrator._record_sell() logic exactly.
        """
        if not exec_result.success:
            return
        try:
            from data.repository import TradeRepository, PositionRepository

            sold_qty = partial_sold_qty or exec_result.quantity or position.quantity
            is_partial = partial_sold_qty is not None and partial_sold_qty < position.quantity

            with self._Session() as session:
                sell_price_actual = exec_result.fill_price or exec_result.price or sell_price or 0
                TradeRepository(session).save_trade(
                    code=position.code, name=position.name,
                    side=OrderSide.SELL, quantity=sold_qty,
                    price=sell_price_actual,
                    amount=exec_result.amount or (sold_qty * sell_price_actual),
                    action=action, reason=reason,
                )
                if is_partial:
                    PositionRepository(session).update_position_quantity(
                        code=position.code, sold_quantity=sold_qty,
                    )
                else:
                    PositionRepository(session).close_position(
                        code=position.code, final_profit_loss=position.profit_loss_pct
                    )
                session.commit()
        except Exception as e:
            logger.error(f"매도 기록 저장 실패: {e}")
