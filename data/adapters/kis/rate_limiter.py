"""
KIS API 중앙 Rate Limiter (Token Bucket + Semaphore).

KIS API 제한: 초당 ~20회 (50ms 간격), 분당 ~1,200회.
WebSocket 41종목 제한과 달리 REST API는 명시적 초당 호출 제한이 있음.

사용법:
    limiter = KisRateLimiter(rate_per_sec=20, burst=30, max_concurrent=15)
    async with limiter:
        await client.get(...)

모든 KIS REST API 호출은 KisHttpClient를 통과하므로,
client.py의 get()/post()에서 이 limiter를 사용하면 전체 API 호출이 중앙 관리됨.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional


class KisRateLimitError(Exception):
    """Rate limit exceeded."""
    pass


class KisRateLimiter:
    """Token Bucket + Semaphore 기반 KIS API Rate Limiter.

    Features:
    - Token Bucket: 초당 rate_per_sec개의 토큰, 최대 burst개의 버스트
    - Semaphore: 최대 max_concurrent개의 동시 호출
    - Wait queue: 토큰이 없으면 대기 (즉시 실패하지 않음)
    - Stats: 호출 수, 대기 시간 추적

    Args:
        rate_per_sec: 초당 허용 호출 수 (KIS 기본 ~20)
        burst: 버스트 허용량 (순간적으로 허용할 최대 토큰)
        max_concurrent: 최대 동시 호출 수 (Semaphore)
    """

    def __init__(
        self,
        rate_per_sec: int = 20,
        burst: int = 30,
        max_concurrent: int = 15,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if burst < rate_per_sec:
            burst = rate_per_sec  # burst는 최소 rate_per_sec 이상

        self._rate_per_sec = rate_per_sec
        self._burst = burst
        self._interval = 1.0 / rate_per_sec  # 호출 간 최소 간격

        # Token Bucket state
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

        # Semaphore for concurrency control
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Stats
        self._total_calls: int = 0
        self._total_waited: float = 0.0

    async def acquire(self) -> None:
        """토큰을 획득할 때까지 대기 (무한 대기).

        모든 KIS API 호출 전에 이 메서드를 호출하여 Rate Limit을 준수한다.
        """
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._total_calls += 1
                return

        # 토큰 부족 → 다음 토큰까지 대기
        wait_time = self._interval
        await asyncio.sleep(wait_time)
        self._total_waited += wait_time

        # 재시도 (재귀)
        await self.acquire()

    async def __aenter__(self) -> None:
        """async with limiter: — Semaphore + Token Bucket 동시 적용."""
        await self._semaphore.acquire()
        await self.acquire()

    async def __aexit__(self, *args) -> None:
        self._semaphore.release()

    def _refill(self) -> None:
        """Token Bucket refill: 경과 시간에 비례하여 토큰 충전."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate_per_sec)

    @property
    def stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "total_waited_sec": round(self._total_waited, 3),
            "rate_per_sec": self._rate_per_sec,
            "burst": self._burst,
            "tokens_available": round(self._tokens, 2),
        }

    def reset_stats(self) -> None:
        self._total_calls = 0
        self._total_waited = 0.0
