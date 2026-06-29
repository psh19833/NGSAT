"""NGSAT backtest runner — shared between CLI and dashboard.

Extracts the backtest execution logic from main.py so it can
be called both from the command line and the dashboard API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.config import Config as NGSATConfig
from core.logger import logger

# ── Global state for dashboard progress tracking ──
_backtest_state: dict[str, Any] = {
    "status": "idle",      # idle / running / completed / error
    "progress_pct": 0,
    "completed_stocks": 0,
    "total_stocks": 0,
    "elapsed_sec": 0,
    "result": None,
    "error": None,
    "started_at": None,
}


def get_backtest_state() -> dict:
    """Get current backtest state (for dashboard polling)."""
    return dict(_backtest_state)


def reset_backtest_state() -> None:
    """Reset backtest state to idle."""
    _backtest_state["status"] = "idle"
    _backtest_state["progress_pct"] = 0
    _backtest_state["completed_stocks"] = 0
    _backtest_state["total_stocks"] = 0
    _backtest_state["elapsed_sec"] = 0
    _backtest_state["result"] = None
    _backtest_state["error"] = None
    _backtest_state["started_at"] = None


async def run_backtest_async(
    config: NGSATConfig,
    start_date: str | None = None,
    end_date: str | None = None,
    initial_capital: float = 10_000_000,
    use_ml: bool = True,
) -> dict:
    """Run backtest asynchronously, updating progress state.

    This function is designed to be called from an executor thread
    so the dashboard API remains responsive during backtest execution.

    Args:
        config: Application config.
        start_date: Optional start date (YYYY-MM-DD).
        end_date: Optional end date (YYYY-MM-DD).
        initial_capital: Initial capital for backtest.
        use_ml: Whether to use ML model for predictions.

    Returns:
        Backtest result dict.
    """
    # ── 0. 상태 락 — 중복 실행 방지 (BE-12)
    if _backtest_state["status"] == "running":
        raise RuntimeError("백테스트가 이미 실행 중입니다")
    reset_backtest_state()
    _backtest_state["status"] = "running"
    _backtest_state["started_at"] = datetime.now().isoformat()

    try:
        # ── 1. Load market data ──
        from backtest.data_loader import generate_synthetic_index, generate_synthetic_universe

        universe: list = []
        index_prices: list = []
        data_source = "synthetic"

        try:
            from data.real_data_provider import RealDataProvider
            provider = RealDataProvider()
            universe, index_prices = await provider.load()
            if universe:
                data_source = "real"
                logger.info(f"KIS 실데이터 로드 성공: {len(universe)}종목")
            else:
                logger.warning("KIS 데이터 없음 — 합성 데이터로 대체")
        except Exception as e:
            logger.warning(f"KIS 데이터 실패: {e} — 합성 데이터로 대체")

        if not universe:
            universe = generate_synthetic_universe(n_stocks=20, n_days=250, seed=42)
            index_prices = generate_synthetic_index(n_days=250, seed=100)

        _backtest_state["total_stocks"] = len(universe)

        all_prices = [prices for _, prices in universe]
        codes = [info.code for info, _ in universe]

        # ── 2. Train model (optional) ──
        model = None
        if use_ml:
            from ml.training.trainer import train_from_price_data
            model, train_result = train_from_price_data(
                all_prices, codes,
                model_type=config.strategy.ml_model_type,
                forward_days=config.strategy.ml_swing_forward_days,
                forward_threshold=0.02,
            )
            logger.info(f"ML 모델 학습 완료: {train_result.reason}")

        # ── 3. Run backtest engine ──
        from backtest.engine import BacktestEngine

        engine = BacktestEngine(
            model=model,
            initial_capital=initial_capital,
            risk_config=config.risk,
            strategy_config=config.strategy,
            buy_threshold=config.strategy.buy_threshold,
            sell_threshold=config.strategy.sell_threshold,
        )

        result = engine.run(universe, index_prices, start_day=60)

        # ── 4. Build response ──
        bt_result = {
            "status": "completed",
            "data_source": data_source,
            "data_source_label": "KIS 실데이터" if data_source == "real" else "합성 데이터",
            "stocks_count": len(universe),
            "start_date": result.start_date,
            "end_date": result.end_date,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return_pct": round(result.total_return, 2),
            "win_rate": round(result.win_rate, 1),
            "max_drawdown_pct": round(result.max_drawdown, 2),
            "total_trades": result.total_trades,
            "buy_count": result.buy_count,
            "sell_count": result.sell_count,
            "swing_days": getattr(engine, '_swing_days', 0),
            "short_term_days": getattr(engine, '_short_term_days', 0),
            "hold_days": getattr(engine, '_hold_days', 0),
            "trades": [
                {
                    "date": t.date,
                    "code": t.code,
                    "name": t.name,
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "amount": t.amount,
                    "action": t.action,
                    "reason": t.reason,
                }
                for t in getattr(engine, '_trades', [])
            ],
            "daily_capital": getattr(engine, '_daily_capital', []),
            "created_at": datetime.now().isoformat(),
        }

        _backtest_state["status"] = "completed"
        _backtest_state["result"] = bt_result
        _backtest_state["progress_pct"] = 100

        logger.info(f"백테스트 완료: 수익률 {result.total_return:+.1f}%, 승률 {result.win_rate:.1f}%")
        return bt_result

    except Exception as e:
        _backtest_state["status"] = "error"
        _backtest_state["error"] = str(e)
        logger.error(f"백테스트 실패: {e}")
        raise
