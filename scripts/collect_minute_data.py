#!/usr/bin/env python3
"""NGSAT 과거분봉 수집기.

KIS API에서 당일 분봉 데이터를 수집해 SQLite/DB에 저장한다.
장중 5분~10분 주기로 실행하면 매일 분봉 데이터를 누적할 수 있다.

사용법:
  python3 scripts/collect_minute_data.py                        # 기본 종목 목록 수집
  python3 scripts/collect_minute_data.py --codes 005930,000660  # 특정 종목만 수집
  python3 scripts/collect_minute_data.py --all-today            # KIS volume-top 종목 수집

설정 (선택):
  NGSAT_MINUTE_CODES  쉼표로 구분된 종목코드 (기본: KOSPI 주요종목 30개)
  NGSAT_COLLECT_INTERVAL  수집 간격 (초, 기본 600 = 10분)
"""

from __future__ import annotations

import asyncio
import os
import sys
from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone, timedelta
from typing import Any

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import logger
from core.models import Base
from data.adapters.kis.adapter import KisAdapter
from data.db import get_engine
from data.repository import MinuteDataRepository
from sqlalchemy.orm import Session

KST = timezone(timedelta(hours=9))

# ── 기본 수집 대상 종목 (KOSPI 주요 30종목) ──
DEFAULT_CODES: list[str] = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "373220",  # LG에너지솔루션
    "207940",  # 삼성바이오로직스
    "005380",  # 현대차
    "000270",  # 기아
    "068270",  # 셀트리온
    "105560",  # KB금융
    "055550",  # 신한지주
    "035420",  # NAVER
    "000810",  # 삼성화재
    "012330",  # 현대모비스
    "006400",  # 삼성SDI
    "028260",  # 삼성물산
    "032830",  # 삼성생명
    "086790",  # 하나금융지주
    "003550",  # LG
    "066570",  # LG전자
    "015760",  # 한국전력
    "017670",  # SK텔레콤
    "329180",  # HD현대중공업
    "138040",  # 메리츠금융지주
    "096770",  # SK이노베이션
    "018260",  # 삼성에스디에스
    "034730",  # SK
    "323410",  # 카카오뱅크
    "259960",  # 크래프톤
    "352820",  # 하이브
    "247540",  # 에코프로비엠
    "196170",  # 알테오젠
]


def _get_codes(args: Namespace) -> list[str]:
    """수집할 종목코드 목록 반환.

    우선순위: args.codes > env NGSAT_MINUTE_CODES > DEFAULT_CODES
    """
    if args.codes:
        return [c.strip() for c in args.codes.split(",") if c.strip()]
    env_codes = os.getenv("NGSAT_MINUTE_CODES", "")
    if env_codes:
        return [c.strip() for c in env_codes.split(",") if c.strip()]
    return DEFAULT_CODES


def _init_db() -> None:
    """DB 초기화 (테이블 생성)."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("DB 초기화 완료 — 모든 테이블 생성됨")


def _bar_to_dict(bar: Any, code: str) -> dict[str, Any]:
    """PriceData → 저장용 dict 변환.

    PriceData.timestamp는 datetime 객체로,
    KIS minute_chart는 timestamp에 YYYY-MM-DD HH:MM:SS 정보를 포함한다.
    """
    ts = bar.timestamp
    date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts.date())
    time_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else "00:00:00"
    return {
        "code": code or bar.code,
        "date": date_str,
        "time": time_str,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
    }


async def run_collection(codes: list[str], args: Namespace) -> None:
    """전체 수집 실행."""
    _init_db()

    codes = _get_codes(args)
    if not codes:
        logger.error("수집할 종목코드가 없습니다. --codes 또는 NGSAT_MINUTE_CODES를 설정하세요.")
        return

    adapter = KisAdapter.from_env()
    engine = get_engine()

    logger.info(f"분봉 수집 시작: {len(codes)}개 종목")
    logger.info(f"  DB: {engine.url}")
    logger.info(f"  모드: {'DRY-RUN (저장 안 함)' if args.dry_run else 'LIVE (DB 저장)'}")

    all_results: list[dict[str, Any]] = []
    total_fetched = 0
    total_saved = 0
    errors = 0

    for i, code in enumerate(codes):
        try:
            now_kst = datetime.now(KST)
            today_str = now_kst.strftime("%Y-%m-%d")

            bars = await adapter.get_minute_history(code, include_past=True)
            fetched = len(bars)
            total_fetched += fetched

            saved = 0
            if bars and not args.dry_run:
                dicts = [_bar_to_dict(b, code) for b in bars]
                with Session(engine) as session:
                    local_repo = MinuteDataRepository(session)
                    saved = local_repo.save_minute_bars(dicts)
                total_saved += saved
            elif bars:
                saved = fetched  # dry-run: 모두 '저장됨' 가상

            all_results.append({
                "code": code, "fetched": fetched, "saved": saved,
                "date": today_str, "error": None,
            })
        except Exception as e:
            err_msg = str(e)[:200]
            logger.warning(f"[{code}] 수집 실패: {err_msg}")
            all_results.append({
                "code": code, "fetched": 0, "saved": 0,
                "date": "", "error": err_msg,
            })
            errors += 1

        # 진행률 표시
        if (i + 1) % 10 == 0 or i == len(codes) - 1:
            logger.info(f"  진행: {i + 1}/{len(codes)} 조회 완료 (총 {total_fetched}개 분봉)")

        # KIS rate limit: 초당 20건. 50ms 간격으로 충분.
        await asyncio.sleep(0.05)

    await adapter.close()

    # 요약
    now_kst = datetime.now(KST)
    logger.info(f"\n{'='*50}")
    logger.info(f"분봉 수집 완료: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    logger.info(f"  대상 종목: {len(codes)}개")
    logger.info(f"  조회된 분봉: {total_fetched}개")
    logger.info(f"  신규 저장: {total_saved}개")
    logger.info(f"  오류: {errors}건")

    # 오류 요약
    failed = [r for r in all_results if r.get("error")]
    if failed:
        logger.warning(f"  오류 상위 5건:")
        for r in failed[:5]:
            logger.warning(f"    - {r['code']}: {r['error']}")

    # 통계
    saved_codes = [r for r in all_results if r["saved"] > 0]
    logger.info(f"  저장된 종목: {len(saved_codes)}개")
    if saved_codes:
        total_bars = sum(r["saved"] for r in saved_codes)
        logger.info(f"  총 분봉 수: {total_bars}개")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="NGSAT 과거분봉 수집기")
    parser.add_argument(
        "--codes",
        type=str,
        default="",
        help="수집할 종목코드 (쉼표 구분, 예: 005930,000660)",
    )
    parser.add_argument(
        "--all-today",
        action="store_true",
        help="KIS volume-top 종목 (향후 구현 — 현재는 기본 종목 목록 사용)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB 저장 없이 조회만 수행",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    codes = _get_codes(args)

    if not codes:
        print("수집할 종목코드가 없습니다.")
        print("  --codes 005930,000660  또는  export NGSAT_MINUTE_CODES=005930,000660")
        sys.exit(1)

    print(f"NGSAT 과거분봉 수집기")
    print(f"  종목: {len(codes)}개")
    print(f"  DB 저장: {'안 함 (dry-run)' if args.dry_run else '함'}")
    print()

    asyncio.run(run_collection(codes, args))


if __name__ == "__main__":
    main()
