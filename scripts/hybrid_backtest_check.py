"""하이브리드 백테스트 검증 스크립트."""
import logging
logging.disable(logging.CRITICAL)

import sys
sys.path.insert(0, '.')

from backtest.data_loader import generate_synthetic_data, generate_synthetic_index, generate_synthetic_universe, synthetic_minute_provider
from backtest.engine import BacktestEngine
from ml.training.trainer import train_from_price_data

universe = generate_synthetic_universe(n_stocks=5, n_days=250)
index_prices = generate_synthetic_index(n_days=250)

all_prices = [prices for _, prices in universe]
codes = [info.code for info, _ in universe]
model, _ = train_from_price_data(all_prices, codes, model_type='random_forest')

minute_prov = synthetic_minute_provider(universe)
engine = BacktestEngine(model, initial_capital=10_000_000)
bt = engine.run(universe, index_prices, start_day=60, minute_provider=minute_prov)

print("=== 백테스트 결과 (하이브리드 모드) ===")
print(f"기간: {bt.start_date} ~ {bt.end_date}")
print(f"수익률: {bt.total_return:+.1f}%")
print(f"거래: {bt.total_trades}회 (매수 {bt.buy_count}/매도 {bt.sell_count})")
print(f"승률: {bt.win_rate:.1f}%")
print(f"최대낙폭: {bt.max_drawdown:.1f}%")
print(f"진입보류: {bt.entries_deferred}건")
print(f"모드: 스윙{bt.mode_swing_days}일 / 단타{bt.mode_short_term_days}일 / 관망{bt.mode_hold_days}일")
print(f"요약: {bt.reason}")
