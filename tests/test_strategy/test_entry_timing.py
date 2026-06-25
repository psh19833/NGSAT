"""Tests for entry timing refinement (분봉 진입 정밀화)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.types import PriceData
from strategy.entry_timing import EntryDecision, EntryTiming, refine_entry


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


class TestRefineEntry:
    """refine_entry 단위 테스트."""

    def test_insufficient_data_falls_back_to_market(self):
        """분봉이 부족하면 정밀화 생략하고 시장가 진입으로 폴백."""
        decision = refine_entry(_bars([70000.0] * 5))  # < min_bars(20)
        assert decision.timing == EntryTiming.ENTER_NOW
        assert decision.should_enter is True
        assert decision.limit_price is None
        assert "부족" in decision.reason

    def test_normal_enters_with_limit_price(self):
        """완만한 횡보 → 진입, 현재가 지정가 제안."""
        closes = [70000.0 + (50 if i % 2 else -50) for i in range(25)]
        decision = refine_entry(_bars(closes))
        assert decision.timing == EntryTiming.ENTER_NOW
        assert decision.should_enter is True
        assert decision.limit_price is not None
        assert decision.limit_price == closes[-1]

    def test_surge_defers(self):
        """최근 5분봉 급등 → 추격 보류(WAIT)."""
        closes = [70000.0] * 20 + [70700.0, 71400.0, 72100.0, 72800.0, 73000.0]
        decision = refine_entry(_bars(closes))
        assert decision.timing == EntryTiming.WAIT
        assert decision.should_enter is False
        assert "급등" in decision.reason

    def test_overheated_rsi_defers(self):
        """지속 상승으로 RSI 과열(최근 5분은 급등 임계 미만) → 보류(WAIT)."""
        closes = [70000.0]
        for _ in range(24):
            closes.append(closes[-1] * 1.003)  # 매분 +0.3% 지속 상승
        decision = refine_entry(_bars(closes))
        assert decision.timing == EntryTiming.WAIT
        assert decision.should_enter is False
        assert "과열" in decision.reason

    def test_reason_and_evidence_always_present(self):
        """모든 결정에 근거와 정량 evidence가 존재해야 한다."""
        for closes in ([70000.0] * 5, [70000.0 + (i % 3) for i in range(25)]):
            d = refine_entry(_bars(closes))
            assert isinstance(d, EntryDecision)
            assert d.reason and d.reason.strip()
            assert isinstance(d.evidence, dict)
