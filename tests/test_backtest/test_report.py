"""Tests for NGSAT backtest report."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backtest.engine import BacktestResult, BacktestTrade
from backtest.report import (
    BacktestReport,
    PerformanceMetrics,
    StockPerformance,
    generate_report,
    print_report,
)
from core.types import Market


def _make_mock_result() -> BacktestResult:
    """Create a mock backtest result with realistic data."""
    trades = [
        BacktestTrade(
            code="005930", name="삼성전자", side="buy", quantity=10,
            price=70000, amount=700000, date="2025-02-01",
            action="buy", reason="ML 예측: 매수 (상승 확률 72.0%)",
        ),
        BacktestTrade(
            code="005930", name="삼성전자", side="sell", quantity=10,
            price=72000, amount=720000, date="2025-02-08",
            action="sell", reason="ML 추론(청산): 매도 — 수익 실현",
        ),
        BacktestTrade(
            code="000660", name="SK하이닉스", side="buy", quantity=5,
            price=120000, amount=600000, date="2025-02-15",
            action="buy", reason="ML 예측: 매수 (상승 확률 68.0%)",
        ),
        BacktestTrade(
            code="000660", name="SK하이닉스", side="sell", quantity=5,
            price=115000, amount=575000, date="2025-02-22",
            action="stop_loss", reason="손절: 손실 4.2% >= 손절선 3.0%",
        ),
    ]

    daily_capital = [10_000_000, 10_050_000, 10_100_000, 10_050_000, 9_900_000]

    return BacktestResult(
        start_date="2025-01-01",
        end_date="2025-06-30",
        initial_capital=10_000_000,
        final_capital=10_200_000,
        total_return=2.0,
        total_trades=4,
        buy_count=2,
        sell_count=2,
        winning_trades=1,
        losing_trades=1,
        win_rate=50.0,
        max_drawdown=-2.0,
        trades=trades,
        daily_capital=daily_capital,
        reason="백테스트 완료: 수익률 +2.0%",
    )


class TestGenerateReport:
    """Backtest report generation tests."""

    def test_generate_report_returns_report(self):
        result = _make_mock_result()
        report = generate_report(result)

        assert isinstance(report, BacktestReport)
        assert isinstance(report.metrics, PerformanceMetrics)

    def test_metrics_calculated(self):
        result = _make_mock_result()
        report = generate_report(result)

        m = report.metrics
        assert m.total_return == 2.0
        assert m.total_trades == 4
        assert m.buy_count == 2
        assert m.sell_count == 2
        assert m.win_rate == 50.0

    def test_avg_win_and_loss(self):
        result = _make_mock_result()
        report = generate_report(result)

        # Samsung: +2.86% win, SK Hynix: -4.17% loss
        assert report.metrics.avg_win > 0
        assert report.metrics.avg_loss < 0

    def test_profit_factor(self):
        result = _make_mock_result()
        report = generate_report(result)

        # Gross profit > gross loss → profit factor > 1
        assert report.metrics.profit_factor > 0

    def test_best_and_worst_trade(self):
        result = _make_mock_result()
        report = generate_report(result)

        assert report.metrics.best_trade > 0
        assert report.metrics.worst_trade < 0

    def test_stock_performance(self):
        result = _make_mock_result()
        report = generate_report(result)

        assert len(report.stock_performance) == 2  # Two stocks

        # Samsung should be first (profitable)
        samsung = report.stock_performance[0]
        assert samsung.code == "005930"
        assert samsung.total_pnl > 0

    def test_trade_log_built(self):
        result = _make_mock_result()
        report = generate_report(result)

        assert len(report.trade_log) == 4
        for entry in report.trade_log:
            assert "code" in entry
            assert "reason" in entry
            assert len(entry["reason"]) > 0

    def test_summary_is_korean(self):
        result = _make_mock_result()
        report = generate_report(result)

        assert "백테스트 결과" in report.summary
        assert "수익률" in report.summary
        assert "승률" in report.summary
        assert "Sharpe" in report.summary

    def test_summary_has_all_sections(self):
        result = _make_mock_result()
        report = generate_report(result)

        assert "거래 통계" in report.summary
        assert "리스크 지표" in report.summary

    def test_print_report_does_not_crash(self, capsys):
        """print_report should not crash."""
        result = _make_mock_result()
        report = generate_report(result)
        print_report(report)

        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_empty_trades_report(self):
        """Report with no trades should still work."""
        result = BacktestResult(
            start_date="2025-01-01",
            end_date="2025-06-30",
            initial_capital=10_000_000,
            final_capital=10_000_000,
            total_return=0.0,
            total_trades=0,
            buy_count=0,
            sell_count=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            max_drawdown=0.0,
            trades=[],
            daily_capital=[10_000_000, 10_000_000],
        )

        report = generate_report(result)

        assert report.metrics.total_return == 0.0
        assert report.metrics.total_trades == 0
        assert len(report.stock_performance) == 0
