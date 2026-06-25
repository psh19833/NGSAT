#!/usr/bin/env python3
"""NGSAT 과거 일봉 데이터 백필 스크립트.

KIS API에서 과거 일봉 데이터를 조회해 MarketDataCache 테이블에 저장한다.
ML 모델 학습의 기반 데이터를 확보하기 위한 목적.

사용법:
  python3 scripts/backfill_daily_data.py                    # 기본 30종목
  python3 scripts/backfill_daily_data.py --codes 005930     # 특정 종목
  python3 scripts/backfill_daily_data.py --codes 005930 --days 500  # 500일치
  python3 scripts/backfill_daily_data.py --all              # 등록된 전체 종목
  python3 scripts/backfill_daily_data.py --dry-run          # 조회만 하고 저장 안 함
"""

from __future__ import annotations

import asyncio
import os
import sys
from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config
from core.logger import logger
from core.models import Base
from core.types import PriceData
from data.db import get_engine
from data.repository import MarketDataRepository
from sqlalchemy.orm import Session

KST = timezone(timedelta(hours=9))

# 기본 종목 (KOSPI 30)
DEFAULT_CODES: list[str] = [
    "005930", "000660", "373220", "207940", "005380",
    "000270", "068270", "105560", "055550", "035420",
    "000810", "012330", "006400", "028260", "032830",
    "086790", "003550", "066570", "015760", "017670",
    "329180", "138040", "096770", "018260", "034730",
    "323410", "259960", "352820", "247540", "196170",
]

DAYS_DEFAULT = 250  # 기본 수집 기간 (영업일 기준 약 1년)


def _init_db() -> None:
    """DB 초기화."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("DB 초기화 완료")


def _price_to_dict(p: PriceData, date_str: str) -> dict[str, Any]:
    """PriceData → MarketDataCache dict."""
    change = p.change_pct
    if change == 0.0 and p.close > 0:
        # change_pct가 없으면 이전봉 대비 계산
        pass
    return {
        "code": p.code,
        "date": date_str,
        "open": float(p.open),
        "high": float(p.high),
        "low": float(p.low),
        "close": float(p.close),
        "volume": int(p.volume),
        "change_pct": float(change),
    }


async def run_backfill(codes: list[str], days: int, args: Namespace) -> None:
    """전체 백필 실행."""
    _init_db()

    config = load_config()
    if not config.kis.is_configured:
        logger.error("KIS API 설정 없음 — .env 확인 필요")
        return

    from data.adapters.kis.adapter import KisAdapter

    adapter = KisAdapter.from_env()
    engine = get_engine()

    end = datetime.now(KST)
    start = end - timedelta(days=days)

    logger.info(f"일봉 백필 시작: {len(codes)}개 종목, {days}일간")
    logger.info(f"  기간: {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}")
    logger.info(f"  DB: {engine.url}")
    logger.info(f"  모드: {'DRY-RUN' if args.dry_run else 'LIVE'}")

    total_fetched = 0
    total_saved = 0
    errors = 0

    for i, code in enumerate(codes):
        try:
            # KIS에서 일봉 조회
            prices = await adapter.get_price_history(code, start, end)

            if not prices:
                logger.warning(f"[{code}] 데이터 없음 (0개)")
                errors += 1
                continue

            total_fetched += len(prices)

            # DB 저장
            saved = 0
            if not args.dry_run:
                with Session(engine) as session:
                    repo = MarketDataRepository(session)
                    for p in prices:
                        date_str = p.timestamp.strftime("%Y-%m-%d") if hasattr(p.timestamp, "strftime") else str(p.timestamp)[:10]
                        try:
                            repo.save_price_data(**_price_to_dict(p, date_str))
                            saved += 1
                        except Exception:
                            session.rollback()  # 중복 = 무시
                    if saved > 0:
                        session.commit()
                total_saved += saved
            else:
                saved = len(prices)

            logger.info(f"  [{i+1}/{len(codes)}] {code}: {len(prices)}개 (저장 {saved})")

        except Exception as e:
            logger.warning(f"[{code}] 실패: {type(e).__name__}")
            errors += 1

        # KIS rate limit
        await asyncio.sleep(0.05)

    await adapter.close()

    # 요약
    logger.info(f"\n{'='*50}")
    logger.info(f"일봉 백필 완료")
    logger.info(f"  대상: {len(codes)}종목, {days}일간")
    logger.info(f"  조회: {total_fetched}개")
    logger.info(f"  저장: {total_saved}개")
    logger.info(f"  오류: {errors}건")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="NGSAT 과거 일봉 데이터 백필")
    parser.add_argument("--codes", type=str, default="", help="종목코드 (쉼표 구분)")
    parser.add_argument("--days", type=int, default=DAYS_DEFAULT, help="수집 기간(일)")
    parser.add_argument("--all", action="store_true", help="등록된 전체 종목")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 조회만")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    codes = args.codes.split(",") if args.codes else DEFAULT_CODES
    codes = [c.strip() for c in codes if c.strip()]

    if not codes:
        print("종목코드가 없습니다. --codes 005930,000660")
        sys.exit(1)

    print(f"NGSAT 과거 일봉 백필")
    print(f"  종목: {len(codes)}개")
    print(f"  기간: {args.days}일")
    print(f"  저장: {'안 함 (dry-run)' if args.dry_run else 'DB 저장'}")
    print()

    asyncio.run(run_backfill(codes, args.days, args))


if __name__ == "__main__":
    main()
