"""Tests for exit timing refinement (분봉 청산 정밀화)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.types import PriceData
from strategy.exit_timing import ExitDecision, ExitUrgency, refine_exit


def _bars(closes: list[float], code: str = "005930") -> list[PriceData]:
    """Build a minute-bar PriceData list from a list of close prices."""
    base = datetime(2026, 6, 25, 9, 0, 0)
    out: list[PriceData] = []
    for i, c in enumerate(closes):
        out.append(PriceData(
            code=code,
            timestamp=base + timedelta(minutes=i),
            open=c, high=c * 1.001, low=c * 0.999, close=c,
            volume=1000,
        ))
    return out


class TestRefineExit:
    """refine_exit 단위 테스트."""

    def test_insufficient_data_skips(self):
        """분봉이 부족하면 청산 정밀화 생략(기존 일봉 로직에 위임)."""
        d = refine_exit(_bars([70000.0] * 5), -1.0)
        assert d.should_exit is False
        assert d.urgency == ExitUrgency.NONE
        assert d.limit_price is None
        assert "생략" in d.reason

    def test_plunge_triggers_immediate(self):
        """최근 분봉 급락 → 즉시 시장가 청산(IMMEDIATE)."""
        closes = [70000.0] * 20 + [69300.0, 68600.0, 67900.0, 67200.0, 67000.0]
        d = refine_exit(_bars(closes), 1.0)
        assert d.should_exit is True
        assert d.urgency == ExitUrgency.IMMEDIATE
        assert d.limit_price is None
        assert "급락" in d.reason

    def test_profit_and_overheat_takes_profit(self):
        """수익 중 + 분봉 RSI 과열 → 익절(지정가)."""
        closes = [70000.0]
        for _ in range(24):
            closes.append(closes[-1] * 1.003)  # 지속 상승 → RSI 과열
        d = refine_exit(_bars(closes), 7.0)  # 수익 +7%
        assert d.should_exit is True
        assert d.urgency == ExitUrgency.NORMAL
        assert d.limit_price == closes[-1]
        assert "익절" in d.reason

    def test_normal_hold_provides_limit_price(self):
        """청산 신호 없음 → 보유 유지, 정상 매도용 현재가 지정가 제공."""
        closes = [70000.0 + (50 if i % 2 else -50) for i in range(25)]
        d = refine_exit(_bars(closes), 1.0)
        assert d.should_exit is False
        assert d.urgency == ExitUrgency.NORMAL
        assert d.limit_price == closes[-1]

    def test_reason_and_evidence_always_present(self):
        """모든 결정에 근거와 정량 evidence가 존재해야 한다."""
        cases = [([70000.0] * 5, -1.0), ([70000.0 + (i % 3) for i in range(25)], 2.0)]
        for closes, profit in cases:
            d = refine_exit(_bars(closes), profit)
            assert isinstance(d, ExitDecision)
            assert d.reason and d.reason.strip()
            assert isinstance(d.evidence, dict)
