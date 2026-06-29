"""NGSAT ML model comparison — uses RealDataProvider to load real prices."""
import sys, os, asyncio
sys.path.insert(0, '/home/psh19/NGSAT')
os.chdir('/home/psh19/NGSAT')

os.environ['NGSAT_CONFIG'] = '/home/psh19/NGSAT/.env'
from data.real_data_provider import RealDataProvider

async def main():
    print("🏗  RealDataProvider 생성 중...")
    provider = RealDataProvider()

    print("📡 KIS API에서 데이터 로딩 중...")
    universe, index_prices = await provider.load()

    print(f"✅ 로드 완료: {len(universe)} 종목, 지수 {len(index_prices)}일")

    # Extract prices and codes
    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]

    total_points = sum(len(p) for p in all_prices)
    print(f"   전체 가격 포인트: {total_points}")

    # ── Build dataset ──
    from ml.features.builder import build_training_dataset

    X, y, feature_names = build_training_dataset(
        all_prices, codes, forward_days=3, forward_threshold=0.02
    )
    print(f"\n📊 데이터셋: {X.shape[0]} 샘플, {X.shape[1]} 피처, 양성={y.mean():.1%}")

    # ── Compare 5 models ──
    from ml.training.trainer import PriceRiseModel

    results = []
    for mt in ['logistic', 'random_forest', 'gradient_boosting', 'xgboost', 'lightgbm']:
        model = PriceRiseModel(model_type=mt, forward_days=3, forward_threshold=0.02)
        result = model.train(X, y)
        results.append((mt, result))

    results.sort(key=lambda r: r[1].auc, reverse=True)

    print(f"\n{'='*65}")
    print(f"{'RANK':>4} {'AUC':>6} {'정밀도':>8} {'재현율':>7} {'F1':>7} {'정확도':>7}  모델")
    print(f"{'='*65}")
    for rank, (mt, r) in enumerate(results, 1):
        print(f"{rank:>4} {r.auc:>6.3f} {r.precision:>7.1%} {r.recall:>7.1%} {r.f1:>7.1%} {r.accuracy:>7.1%}  {mt}")

    best_mt, best_r = results[0]
    print(f"\n★ 최고: {best_mt} (AUC={best_r.auc:.3f})")

    if best_r.feature_importance:
        top5 = sorted(best_r.feature_importance.items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"  Top 5 피처: {', '.join(f'{n}={v:.3f}' for n, v in top5)}")

    # ── Target tuning ──
    print(f"\n{'='*65}")
    print("타겟 튜닝 (forward_days × threshold)")
    print(f"{'='*65}")

    param_grid = [
        (3, 0.02, f"현재설정"),
        (5, 0.02, f""),
        (7, 0.02, f""),
        (3, 0.015, f""),
        (3, 0.03, f""),
        (5, 0.03, f""),
    ]

    for fwd, thr, label in param_grid:
        Xg, yg, _ = build_training_dataset(all_prices, codes, forward_days=fwd, forward_threshold=thr)
        if len(Xg) < 30:
            print(f"  [----] forward={fwd}d, thr={thr:.1%}: 데이터 부족 ({len(Xg)}샘플)")
            continue
        model = PriceRiseModel(model_type=best_mt, forward_days=fwd, forward_threshold=thr)
        result = model.train(Xg, yg)
        flag = "  ← 현재" if label else ""
        print(f"  [{result.auc:.3f}] forward={fwd}d, thr={thr:.1%}  AUC={result.auc:.3f} 양성={yg.mean():.1%} 샘플={len(Xg)}{flag}")

    print(f"\n{'='*65}")
    print("✅ 비교 완료")

asyncio.run(main())
