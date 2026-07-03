#!/usr/bin/env python3
"""Evaluate screener scores for all universe stocks."""
import asyncio
import sys
sys.path.insert(0, '/home/psh19/NGSAT')

async def main():
    from core.config import load_config
    from data.real_data_provider import RealDataProvider

    cfg = load_config()
    provider = RealDataProvider()
    adapter = await provider._get_adapter()

    # Load data
    universe, index_prices = await provider.load()
    print(f'Universe: {len(universe)} stocks')

    from strategy.screener import _evaluate_single_stock, _build_regime_thresholds
    from core.types import MarketRegime

    thresholds = _build_regime_thresholds(cfg.strategy).get(MarketRegime.NEUTRAL)
    if thresholds is None:
        thresholds = {'min_score': 70.0, 'max_candidates': 15, 'pattern_weight': 1.0}

    results = []
    for info, prices in universe:
        if len(prices) < 60:
            results.append((info.code, info.name, 0, '데이터부족'))
            continue
        try:
            cand = _evaluate_single_stock(info, prices, thresholds)
            if cand:
                status = '통과' if cand.score >= thresholds['min_score'] else '미달'
                results.append((info.code, info.name, cand.score, status))
            else:
                results.append((info.code, info.name, 0, '평가실패'))
        except Exception as e:
            results.append((info.code, info.name, 0, f'오류:{type(e).__name__}'))

    results.sort(key=lambda x: x[2], reverse=True)
    passed = [r for r in results if r[3] == '통과']
    print(f'\n=== 점수 기준: {thresholds["min_score"]}점, 통과 {len(passed)}개 ===')
    print(f'{"종목명":<20} {"코드":<8} {"점수":>6} {"결과":<8}')
    print('-' * 45)
    for code, name, score, status in results:
        if score > 0:
            icon = '✅' if status == '통과' else '❌'
            print(f'{name:<20} {code:<8} {score:>5.1f}  {icon} {status}')
        else:
            print(f'{name:<20} {code:<8} {"-":>6}  ⏭ {status}')

    await provider.close()

asyncio.run(main())
