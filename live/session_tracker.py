"""NGSAT market session tracker — detects market open/close transitions.

Tracks the previous market state (open/closed) across trading cycles
so the main loop can detect state transitions and trigger Telegram
notifications for market open, market close, and daily report.

Usage:
    tracker = MarketSessionTracker()
    while True:
        result = tracker.update(is_market_hours())
        if result["changed"]:
            if result["state"] == "open":
                # send market open notification
            else:
                # send market close + daily report
"""

from __future__ import annotations

from core.logger import logger


class MarketSessionTracker:
    """시장 세션 상태 추적 — 장 시작/종료 감지 및 알림 트리거.

    Attributes:
        _previous_state: 이전 사이클의 시장 상태 (None=최초).
        _daily_report_sent: 오늘 일일 보고서 전송 여부.
    """

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"

    def __init__(self) -> None:
        self._previous_state: str | None = None
        self._daily_report_sent: bool = False

    def update(self, is_open: bool) -> dict:
        """현재 시장 상태를 업데이트하고 상태 변화 정보 반환.

        Args:
            is_open: 현재 시장이 열려있는지 여부.

        Returns:
            변경이 있으면 {"changed": True, "state": "open|closed"}
            변경이 없으면 {"changed": False}
            최초 실행 시에는 상태만 저장하고 changed=False.
        """
        current = self.STATE_OPEN if is_open else self.STATE_CLOSED

        if self._previous_state is None:
            # 최초 실행 — 알림 없이 상태만 저장
            self._previous_state = current
            logger.debug(f"MarketSessionTracker 초기 상태: {current}")
            return {"changed": False}

        if self._previous_state != current:
            self._previous_state = current
            logger.info(f"시장 상태 변경 감지: {current}")
            return {"changed": True, "state": current}

        return {"changed": False}

    @property
    def should_send_daily_report(self) -> bool:
        """장 마감 후 최초 1회만 보고서 전송 필요."""
        return (
            self._previous_state == self.STATE_CLOSED
            and not self._daily_report_sent
        )

    def mark_daily_report_sent(self) -> None:
        """일일 보고서 전송 완료 표시."""
        self._daily_report_sent = True
        logger.info("일일 보고서 전송 완료 — 중복 전송 방지")

    @property
    def state(self) -> str | None:
        """현재 시장 상태."""
        return self._previous_state

    def reset(self) -> None:
        """상태 초기화 (서버 재시작 등)."""
        self._previous_state = None
        self._daily_report_sent = False
        logger.info("MarketSessionTracker 초기화 완료")
