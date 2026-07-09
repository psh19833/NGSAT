#!/usr/bin/env python3
"""분봉ML 모델 1회성 재학습 — DB 저장된 전체 분봉 데이터 사용."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.db import get_engine
from data.repository import MinuteDataRepository
from data.minute_bar_builder import MinuteBarBuilder
from ml.training.trainer import train_from_minute_data
from ml.training.persistence import save_model, load_model
from core.logger import logger
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone, timedelta
import numpy as np

KST = timezone(timedelta(hours=9))
DEFAULT_CODES = [
    "005930","000660","373220","207940","005380","000270","068270",
    "105560","055550","035420","000810","012330","006400","028260",
    "032830","086790","003550","066570","015760","017670","329180",
    "138040","096770","018260","034730","323410","259960","352820",
    "247540","196170",
]

def main():
    engine = get_engine()
    codes = DEFAULT_CODES

    # DB에서 전체 분봉 데이터 로드
    minute_prices_list = []
    minute_codes = []
    with Session(engine) as session:
        repo = MinuteDataRepository(session)
        for code in codes:
            # SQL 직접: date='2026-07-08' or '2026-07-09', time 정렬
            rows = session.execute(
                text("SELECT date, time, open, high, low, close, volume "
                     "FROM minute_data_cache WHERE code = :code "
                     "AND date IN ('2026-07-08','2026-07-09') "
                     "ORDER BY date, time"),
                {"code": code}
            ).fetchall()
            
            if len(rows) < 100:
                logger.info(f"  {code}: {len(rows)}개 (부족, skip)")
                continue
            
            from core.types import PriceData
            prices = []
            for r in rows:
                dt_str = f"{r[0]} {r[1]}"
                try:
                    ts = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except:
                    ts = datetime.now()
                prices.append(PriceData(
                    code=code, timestamp=ts,
                    open=float(r[2]), high=float(r[3]),
                    low=float(r[4]), close=float(r[5]),
                    volume=int(r[6]),
                ))
            minute_prices_list.append(prices)
            minute_codes.append(code)
            logger.info(f"  {code}: {len(prices)}개 로드")

    logger.info(f"\n=== 분봉ML 재학습 시작 ===")
    logger.info(f"종목: {len(minute_codes)}개")
    logger.info(f"전체 샘플 수: {sum(len(p) for p in minute_prices_list)}개 분봉")

    if len(minute_codes) < 10:
        logger.error("데이터 부족으로 재학습 불가")
        return

    model, result = train_from_minute_data(
        minute_prices_list, minute_codes,
        model_type="xgboost",
        forward_minutes=10,
        forward_threshold=0.01,
    )

    logger.info(f"\n=== 재학습 결과 ===")
    logger.info(f"성공: {result.success}")
    logger.info(f"AUC: {result.auc:.4f}")
    logger.info(f"정확도: {result.accuracy:.1%}")
    logger.info(f"F1: {result.f1:.1%}")
    logger.info(f"견본: {result.n_samples}")
    logger.info(f"특징: {result.n_features}")

    if result.success and result.auc > 0.5:
        model.save("models/trained/minute_model.pkl")
        logger.info(f"✅ 모델 저장 완료 (AUC={result.auc:.3f})")
        
        # 현재 실행 중인 모델 hot-reload
        try:
            from ml.inference import MLInference
            from ml.training.persistence import load_model
            new_model_data = load_model("models/trained/minute_model.pkl")
            # 메모리 상의 inference engine 찾기
            logger.info("시스템 재시작 시 새 모델이 자동 로드됩니다")
        except Exception as e:
            logger.warning(f"Hot-reload 실패: {e}")
    else:
        logger.warning("AUC太低, 모델 저장 생략")

if __name__ == "__main__":
    main()
