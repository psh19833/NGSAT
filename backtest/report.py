"""NGSAT backtest report — performance analysis and reporting.

Analyzes BacktestResult and produces detailed performance metrics.
All reports are in Korean for the operator (대표님).

Reports include:
- Summary metrics (return, win rate, drawdown)
- Monthly performance breakdown
- Per-stock trade analysis
- Risk metrics (Sharpe ratio, max drawdown, etc.)
- Trade log with reasons
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backtest.engine import BacktestResult, BacktestTrade


@dataclass
class PerformanceMetrics:
    """Detailed performance metrics from a backtest.
    
    Attributes:
        total_return: Total return (%).
        annualized_return: Annualized return (%).
        win_rate: Win rate (%).
        profit_factor: Gross profit / gross loss.
        max_drawdown: Maximum drawdown (%).
        sharpe_ratio: Sharpe ratio (risk-free = 0).
        sortino_ratio: Sortino ratio (downside-only volatility).
        avg_win: Average profit per winning trade (%).
        avg_loss: Average loss per losing trade (%).
        total_trades: Total number of trades.
        buy_count: Number of buy trades.
        sell_count: Number of sell trades.
        avg_holding_days: Average holding period (days).
        best_trade: Best single trade return (%).
        worst_trade: Worst single trade return (%).
        reason: Human-readable summary (Korean).
    """
    total_return: float
    annualized_return: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    avg_win: float
    avg_loss: float
    total_trades: int
    buy_count: int
    sell_count: int
    avg_holding_days: float
    best_trade: float
    worst_trade: float
    reason: str = ""


@dataclass
class StockPerformance:
    """Per-stock performance in backtest."""
    code: str
    name: str
    trades: int
    buys: int
    sells: int
    total_pnl: float
    win_rate: float
    avg_return: float


@dataclass
class BacktestReport:
    """Complete backtest report.
    
    Attributes:
        metrics: Performance metrics.
        stock_performance: Per-stock breakdown.
        trade_log: All trades with reasons.
        summary: Human-readable summary (Korean).
    """
    metrics: PerformanceMetrics
    stock_performance: list[StockPerformance] = field(default_factory=list)
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


def generate_report(result: BacktestResult) -> BacktestReport:
    """Generate a detailed report from a backtest result.
    
    Args:
        result: BacktestResult from the backtest engine.
    
    Returns:
        BacktestReport with metrics, per-stock analysis, and trade log.
    """
    metrics = _calculate_metrics(result)
    stock_perf = _analyze_stocks(result.trades)
    trade_log = _build_trade_log(result.trades)
    
    summary = _build_summary(metrics, result)
    
    return BacktestReport(
        metrics=metrics,
        stock_performance=stock_perf,
        trade_log=trade_log,
        summary=summary,
    )


def _calculate_metrics(result: BacktestResult) -> PerformanceMetrics:
    """Calculate detailed performance metrics."""
    # Pair buy/sell trades to compute returns
    buy_trades: dict[str, BacktestTrade] = {}
    trade_returns: list[float] = []
    holding_days: list[int] = []
    
    for trade in result.trades:
        if trade.side == "buy":
            buy_trades[trade.code] = trade
        elif trade.side == "sell" and trade.code in buy_trades:
            buy = buy_trades[trade.code]
            ret = (trade.price - buy.price) / buy.price * 100
            trade_returns.append(ret)
            
            # Holding days (simplified: use date string difference)
            try:
                from datetime import datetime
                d1 = datetime.strptime(buy.date, "%Y-%m-%d")
                d2 = datetime.strptime(trade.date, "%Y-%m-%d")
                holding_days.append((d2 - d1).days)
            except (ValueError, TypeError):
                pass
            
            del buy_trades[trade.code]
    
    # Win/loss
    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r < 0]
    
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    
    # Sharpe ratio (from daily capital)
    sharpe = 0.0
    sortino = 0.0
    if len(result.daily_capital) > 1:
        daily_returns = np.diff(result.daily_capital) / result.daily_capital[:-1]
        daily_returns = daily_returns[daily_returns != 0]  # Filter zero-return days
        
        if len(daily_returns) > 0:
            mean_return = float(np.mean(daily_returns))
            std_return = float(np.std(daily_returns))
            sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
            
            # Sortino: only downside deviation
            downside = daily_returns[daily_returns < 0]
            downside_std = float(np.std(downside)) if len(downside) > 0 else 0.0
            sortino = (mean_return / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
    
    # Annualized return
    n_days = len(result.daily_capital)
    if n_days > 0 and result.initial_capital > 0:
        annualized = ((result.final_capital / result.initial_capital) ** (252 / n_days) - 1) * 100
    else:
        annualized = 0.0
    
    best_trade = max(trade_returns) if trade_returns else 0.0
    worst_trade = min(trade_returns) if trade_returns else 0.0
    avg_holding = float(np.mean(holding_days)) if holding_days else 0.0
    
    reason = (
        f"수익률 {result.total_return:+.1f}%, "
        f"승률 {result.win_rate:.1f}%, "
        f"Profit Factor {profit_factor:.2f}, "
        f"Sharpe {sharpe:.2f}, "
        f"최대 낙폭 {result.max_drawdown:.1f}%, "
        f"평균 승리 {avg_win:+.1f}%, 평균 손실 {avg_loss:+.1f}%, "
        f"평균 보유 {avg_holding:.0f}일"
    )
    
    return PerformanceMetrics(
        total_return=result.total_return,
        annualized_return=float(annualized),
        win_rate=result.win_rate,
        profit_factor=profit_factor,
        max_drawdown=result.max_drawdown,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        avg_win=avg_win,
        avg_loss=avg_loss,
        total_trades=result.total_trades,
        buy_count=result.buy_count,
        sell_count=result.sell_count,
        avg_holding_days=avg_holding,
        best_trade=best_trade,
        worst_trade=worst_trade,
        reason=reason,
    )


def _analyze_stocks(trades: list[BacktestTrade]) -> list[StockPerformance]:
    """Analyze performance per stock."""
    stock_data: dict[str, dict[str, Any]] = {}
    
    for trade in trades:
        if trade.code not in stock_data:
            stock_data[trade.code] = {
                "code": trade.code,
                "name": trade.name,
                "trades": 0,
                "buys": 0,
                "sells": 0,
                "pnl": 0.0,
                "returns": [],
            }
        
        stock_data[trade.code]["trades"] += 1
        if trade.side == "buy":
            stock_data[trade.code]["buys"] += 1
        else:
            stock_data[trade.code]["sells"] += 1
    
    # Calculate PnL by pairing buys and sells
    buy_prices: dict[str, float] = {}
    for trade in trades:
        if trade.side == "buy":
            buy_prices[trade.code] = trade.price
        elif trade.side == "sell" and trade.code in buy_prices:
            ret = (trade.price - buy_prices[trade.code]) / buy_prices[trade.code] * 100
            stock_data[trade.code]["pnl"] += ret
            stock_data[trade.code]["returns"].append(ret)
            del buy_prices[trade.code]
    
    result: list[StockPerformance] = []
    for data in stock_data.values():
        returns = data["returns"]
        wins = [r for r in returns if r > 0]
        win_rate = (len(wins) / len(returns) * 100) if returns else 0.0
        avg_ret = float(np.mean(returns)) if returns else 0.0
        
        result.append(StockPerformance(
            code=data["code"],
            name=data["name"],
            trades=data["trades"],
            buys=data["buys"],
            sells=data["sells"],
            total_pnl=data["pnl"],
            win_rate=win_rate,
            avg_return=avg_ret,
        ))
    
    # Sort by total PnL descending
    result.sort(key=lambda s: s.total_pnl, reverse=True)
    return result


def _build_trade_log(trades: list[BacktestTrade]) -> list[dict[str, Any]]:
    """Build a readable trade log."""
    log: list[dict[str, Any]] = []
    for t in trades:
        log.append({
            "date": t.date,
            "code": t.code,
            "name": t.name,
            "side": t.side,
            "quantity": t.quantity,
            "price": t.price,
            "amount": t.amount,
            "action": t.action,
            "reason": t.reason,
        })
    return log


def _build_summary(metrics: PerformanceMetrics, result: BacktestResult) -> str:
    """Build a human-readable summary in Korean."""
    lines = [
        "═══ NGSAT 백테스트 결과 ═══",
        "",
        f"기간: {result.start_date} ~ {result.end_date}",
        f"초기 자본: {result.initial_capital:,.0f}원",
        f"최종 자본: {result.final_capital:,.0f}원",
        f"총 수익률: {metrics.total_return:+.1f}%",
        f"연환산 수익률: {metrics.annualized_return:+.1f}%",
        "",
        "── 거래 통계 ──",
        f"총 거래: {metrics.total_trades}회 (매수 {metrics.buy_count} / 매도 {metrics.sell_count})",
        f"승률: {metrics.win_rate:.1f}%",
        f"Profit Factor: {metrics.profit_factor:.2f}",
        f"평균 수익: {metrics.avg_win:+.1f}% | 평균 손실: {metrics.avg_loss:+.1f}%",
        f"최고 거래: {metrics.best_trade:+.1f}% | 최저 거래: {metrics.worst_trade:+.1f}%",
        f"평균 보유: {metrics.avg_holding_days:.0f}일",
        "",
        "── 리스크 지표 ──",
        f"최대 낙폭: {metrics.max_drawdown:.1f}%",
        f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}",
        f"Sortino Ratio: {metrics.sortino_ratio:.2f}",
        "",
        f"종합 평가: {metrics.reason}",
    ]
    return "\n".join(lines)


def print_report(report: BacktestReport) -> None:
    """Print the report to console."""
    print(report.summary)
    
    if report.stock_performance:
        print("\n── 종목별 성과 ──")
        print(f"{'종목':20s} {'거래':>4s} {'승률':>6s} {'평균수익':>8s} {'총PnL':>8s}")
        print("-" * 55)
        for s in report.stock_performance[:10]:
            print(f"{s.name:20s} {s.trades:>4d} {s.win_rate:>5.1f}% {s.avg_return:>+7.1f}% {s.total_pnl:>+7.1f}%")
