"""NGSAT main entry point — integrates and starts all system components.

Wires together:
  - KIS Broker Adapter (data layer)
  - ML Model (loaded from disk or trained)
  - Trading Orchestrator (live trading cycle)
  - Dashboard API (FastAPI server)
  - Telegram Bot (notifications + remote control)

Usage:
  python main.py                    # Start with default config
  python main.py --train            # Train model before starting
  python main.py --backtest         # Run backtest only
  python main.py --no-dashboard     # Start without dashboard
  python main.py --no-telegram      # Start without telegram bot
"""

from __future__ import annotations

import asyncio
import argparse
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from core.config import load_config
from core.logger import logger, setup_logger
from core.types import now_kst


def parse_args():
    parser = argparse.ArgumentParser(
        description="NGSAT — New Generation Stock Auto Trader",
    )
    parser.add_argument(
        "--train", action="store_true",
        help="ML 모델 학습 후 시작",
    )
    parser.add_argument(
        "--backtest", action="store_true",
        help="백테스트만 실행 (실거래 없음)",
    )
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help="대시보드 API 서버 비활성화",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="텔레그램 봇 비활성화",
    )
    parser.add_argument(
        "--model-path", type=str, default="",
        help="ML 모델 파일 경로 (기본: models/trained/price_rise_model.pkl)",
    )
    parser.add_argument(
        "--tick-interval", type=int, default=10,
        help="매매 사이클 주기 (초, 기본: 10)",
    )
    return parser.parse_args()


async def run_backtest(config):
    """Run a backtest — attempts real KIS data first, falls back to synthetic."""
    from backtest.data_loader import generate_synthetic_index, generate_synthetic_universe
    from backtest.engine import BacktestEngine
    from backtest.report import generate_report, print_report
    from ml.training.trainer import train_from_price_data

    logger.info("=== NGSAT 백테스트 모드 ===")

    # ── Try KIS real data first ──
    universe = []
    index_prices = []
    training_days = config.strategy.ml_training_days
    try:
        from data.real_data_provider import RealDataProvider
        logger.info(f"KIS 실데이터 로드 시도 (기간: {training_days}일)...")
        provider = RealDataProvider(
            training_days=training_days,
            start_date=config.strategy.ml_training_start_date,
            end_date=config.strategy.ml_training_end_date,
        )
        universe, index_prices = await provider.load()
        if universe:
            logger.info(f"KIS 실데이터 로드 성공: {len(universe)}종목, 지수 {len(index_prices)}일")
        else:
            logger.warning("KIS 데이터 없음 — 합성 데이터로 대체")
    except Exception as e:
        logger.warning(f"KIS 데이터 로드 실패: {type(e).__name__}: {e} — 합성 데이터로 대체")

    # ── Fallback to synthetic ──
    if not universe:
        training_days = config.strategy.ml_training_days
        logger.info(f"합성 데이터 생성 중... (기간: {training_days}일)")
        universe = generate_synthetic_universe(n_stocks=20, n_days=training_days, seed=42)
        index_prices = generate_synthetic_index(n_days=training_days, seed=100)

    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]

    # Train model
    logger.info("ML 모델 학습 중...")
    model, train_result = train_from_price_data(
        all_prices, codes,
        model_type=config.strategy.ml_model_type,
        forward_days=config.strategy.ml_swing_forward_days,
        forward_threshold=config.strategy.ml_forward_threshold,
    )
    logger.info(train_result.reason)

    # Run backtest with strategy config
    logger.info("백테스트 실행 중...")
    from backtest.engine import BacktestEngine
    engine = BacktestEngine(
        model,
        initial_capital=10_000_000,
        buy_threshold=config.strategy.buy_threshold,
        sell_threshold=config.strategy.sell_threshold,
        strategy_config=config.strategy,
    )
    # start_day: last ~1 month of the shortest stock's data
    if universe:
        min_stock_days = min(len(prices) for _, prices in universe)
        start_day = max(20, min_stock_days - 22)  # ~1 month
    else:
        start_day = 60
    result = engine.run(universe, index_prices, start_day=start_day)

    # Generate and print report
    report = generate_report(result)
    print()
    print_report(report)

    return result


async def run_live(config, args):
    """Start live trading system with all components."""
    from data.adapters.kis.adapter import KisAdapter
    from dashboard.backend.api import create_app
    from live.orchestrator import TradingOrchestrator
    from ml.training.trainer import PriceRiseModel
    from messaging.bot import TelegramBot

    logger.info("=== NGSAT 실거래 모드 시작 ===")

    # ── 1. Load ML models ──
    model_path = args.model_path or str(
        Path(__file__).resolve().parent / "models" / "trained" / "price_rise_model.pkl"
    )

    try:
        model = PriceRiseModel.load(model_path)
        model.auto_select_model = config.strategy.ml_auto_select_model
        if model.data_source == "synthetic":
            logger.warning(
                "⚠️ 로드된 모델의 데이터 출처가 'synthetic'입니다. "
                "KIS 실데이터로 재학습을 권장합니다."
            )
        logger.info(f"ML 모델 로드: {model_path}")
    except Exception:
        logger.warning(f"저장된 모델 없음 ({model_path}). 모델 학습이 필요합니다.")
        logger.warning("python main.py --train 으로 모델을 먼저 학습하세요.")
        return

    # ── 1B. Load minute ML model (optional, for short_term mode) ──
    minute_model_path = str(
        Path(__file__).resolve().parent / "models" / "trained" / "minute_model.pkl"
    )
    minute_model = None
    try:
        minute_model = PriceRiseModel.load(minute_model_path)
        logger.info(f"분봉 ML 모델 로드: {minute_model_path}")
    except Exception:
        logger.info(
            f"분봉 ML 모델 없음 ({minute_model_path}) — "
            "단타 모드 시 일봉 ML로 폴백"
        )

    # ── 2. Create KIS adapter ──
    if not config.kis.is_configured:
        logger.error("KIS API 설정이 없습니다. .env 파일을 확인하세요.")
        return

    broker = KisAdapter(
        app_key=config.kis.app_key,
        app_secret=config.kis.app_secret,
        base_url=config.kis.base_url,
        account_no=config.kis.account_no,
        account_product_code=config.kis.account_product_code,
    )
    logger.info("KIS 어댑터 연결 완료")

    # ── 3. Create orchestrator ──
    orchestrator = TradingOrchestrator(
        broker=broker,
        model=model,
        minute_model=minute_model,
        risk_config=config.risk,
        strategy_config=config.strategy,
        buy_threshold=config.strategy.buy_threshold,
        sell_threshold=config.strategy.sell_threshold,
        position_budget_pct=config.strategy.mode_swing_position_size,
        db_url=config.database.url,
        db_pool_size=config.database.pool_size,
        db_max_overflow=config.database.max_overflow,
    )
    logger.info("오케스트레이터 초기화 완료")

    # ── 3A. Restart recovery: sync positions from broker ──
    try:
        positions = await orchestrator._fetch_positions()
        if positions:
            logger.info(
                f"재시작 복구: KIS 계좌에서 {len(positions)}개 포지션 감지됨 — "
                + ", ".join(f"{p.name}({p.code}) {p.quantity}주" for p in positions[:5])
            )
        else:
            logger.info("재시작 복구: 보유 포지션 없음 — 신규 시작")
    except Exception as e:
        logger.warning(f"재시작 복구: 포지션 조회 실패 — {type(e).__name__}: {e}")

    # ── 4. Telegram bot (optional) ──
    telegram_bot = None
    if not args.no_telegram and config.telegram.is_configured:
        telegram_bot = TelegramBot(
            bot_token=config.telegram.bot_token,
            chat_id=config.telegram.chat_id,
        )
        telegram_bot.set_orchestrator(orchestrator)
        await telegram_bot.send_system_event("start", "NGSAT 시스템 기동")
        logger.info("텔레그램 봇 연결 완료")
    elif not args.no_telegram:
        logger.info("텔레그램 미설정 — 봇 비활성화")

    # ── 5. Dashboard API (optional) ──
    import uvicorn  # imported here for scope; no_dashboard skips config below
    dashboard_app = None
    api_server = None
    if not args.no_dashboard:
        dashboard_app = create_app(orchestrator, config)
        logger.info("대시보드 API 준비 완료 (포트 8000)")

    # ── 6. Start trading loop ──
    tick_interval = args.tick_interval
    logger.info(f"매매 사이클 주기: {tick_interval}초")

    data_provider = None  # P-54: finally 블록 NameError 방지

    async def trading_loop():
        """Main trading loop — runs orchestrator cycle with real KIS data."""
        nonlocal data_provider
        from data.real_data_provider import RealDataProvider
        from live.session_tracker import MarketSessionTracker

        data_provider = RealDataProvider(
            training_days=config.strategy.ml_training_days,
            start_date=config.strategy.ml_training_start_date,
            end_date=config.strategy.ml_training_end_date,
        )
        universe, index_prices = await data_provider.load()

        # Connect minute bar builder from live data provider to orchestrator
        orchestrator._minute_builder = data_provider._minute_builder

        if not universe:
            logger.error("실데이터 로드 실패 — 시스템 중단")
            return

        logger.info(f"KIS 실데이터 연결 완료: {len(universe)}종목, 지수 {len(index_prices)}일")

        # Store references for manual retrain API
        if dashboard_app:
            dashboard_app.state.data_provider = data_provider
            dashboard_app.state.model = model
            dashboard_app.state.latest_universe = universe
            dashboard_app.state.latest_index_prices = index_prices

        _refresh_counter = 1
        _consecutive_errors = 0
        _last_retrain_date = ""
        _last_status_time = 0.0  # 마지막 정기 Status 발송 시간 (timestamp)
        session_tracker = MarketSessionTracker()
        # C-2: refresh 주기 상수 (tick_interval=10s 기준)
        REFRESH_FIRST_CYCLE = 2       # 2번째 사이클에서 최초 1회 refresh
        REFRESH_AFTER_RETRAIN = 35    # 재학습 후 refresh (약 5분 50초)
        REFRESH_INTERVAL = 60         # 60사이클(10분)마다 refresh (P-51: 300→60 단축)

        # ── Helper: KST 장 마감 여부 ──
        def _is_after_market_close_kst() -> bool:
            kst_now = now_kst()
            return kst_now.hour > 15 or (kst_now.hour == 15 and kst_now.minute >= 30)

        # ── Helper: 시장 세션 변경 감지 및 알림 ──
        async def _handle_market_session() -> None:
            from core.types import is_market_hours
            session_result = session_tracker.update(is_market_hours())
            if not session_result.get("changed") or not telegram_bot:
                return
            state = session_result["state"]
            if state == "open":
                try:
                    account = await orchestrator._broker.get_account_summary()
                    regime_info = f"레짐: {orchestrator._last_regime.regime.value}({orchestrator._last_regime.score:.0f}점)" if orchestrator._last_regime else "레짐: 미평가"
                    msg = (
                        f"🌅 장 시작 (09:00)\n"
                        f"──────────\n"
                        f"{regime_info}\n"
                        f"모드: {orchestrator._current_mode}\n"
                        f"예수금: {account.deposit:,.0f}원\n"
                        f"보유: {len(orchestrator._last_positions) if hasattr(orchestrator, '_last_positions') else '?'}개 종목"
                    )
                except Exception:
                    msg = "🌅 장 시작 (09:00)"
                await telegram_bot.send_system_event("market_open", msg)
            else:
                await telegram_bot.send_system_event("market_close", "🌇 장 종료 (15:30)")

        # ── Helper: 일일 보고서 전송 ──
        async def _send_daily_report() -> None:
            if not telegram_bot or not session_tracker.should_send_daily_report:
                return
            try:
                from data.repository import TradeRepository
                from core.models import DailyReport
                db_session = orchestrator._Session()
                try:
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                    # P-54: DB에 오늘자 보고서가 이미 있으면 중복 발송 방지
                    existing = db_session.query(DailyReport).filter(DailyReport.date == today).first()
                    if existing is not None:
                        logger.info(f"오늘({today}) 일일 보고서 이미 전송됨 — 중복 방지")
                        session_tracker.mark_daily_report_sent()
                        return

                    trade_repo = TradeRepository(db_session)
                    trades = trade_repo.get_trades_by_date(today)
                    buy_count = sum(1 for t in trades if t.side == "buy")
                    sell_count = sum(1 for t in trades if t.side == "sell")
                    total_trades = len(trades)
                    # 오늘 체결 기반 손익 계산 (계좌 평가손익 대신)
                    from collections import defaultdict
                    buy_by_code: dict[str, list] = defaultdict(list)
                    sell_by_code: dict[str, list] = defaultdict(list)
                    for t in trades:
                        if t.side == "buy":
                            buy_by_code[t.code].append(t)
                        else:
                            sell_by_code[t.code].append(t)
                    realized_pnl = 0.0
                    all_codes = set(buy_by_code.keys()) | set(sell_by_code.keys())
                    for code in all_codes:
                        buys = buy_by_code.get(code, [])
                        sells = sell_by_code.get(code, [])
                        total_buy_qty = sum(b.quantity for b in buys)
                        total_buy_amt = sum(b.amount for b in buys)
                        total_sell_qty = sum(s.quantity for s in sells)
                        total_sell_amt = sum(s.amount for s in sells)
                        matched_qty = min(total_buy_qty, total_sell_qty)
                        if matched_qty > 0 and total_buy_qty > 0:
                            avg_buy = total_buy_amt / total_buy_qty
                            avg_sell = total_sell_amt / total_sell_qty
                            realized_pnl += (avg_sell - avg_buy) * matched_qty
                    account = await orchestrator._broker.get_account_summary()
                    positions = await orchestrator._broker.get_positions()
                    pos_summary = "\n".join(
                        f"{p.name} {p.quantity}주 ({p.profit_loss_pct:+.1f}%)"
                        for p in positions[:10]
                    ) if positions else "없음"
                    win_rate = trade_repo.get_win_rate(today)
                    await telegram_bot.send_daily_report(
                        date=today, total_trades=total_trades,
                        buy_count=buy_count, sell_count=sell_count,
                        total_pnl=realized_pnl,
                        win_rate=win_rate, current_capital=account.total_asset,
                        positions_summary=pos_summary,
                    )
                    # P-54: DailyReport DB 저장 (재시작 중복 발송 방지)
                    db_session.add(DailyReport(
                        date=today,
                        total_asset=account.total_asset,
                        daily_loss=abs(min(realized_pnl, 0)),
                        trade_count=total_trades,
                        buy_count=buy_count, sell_count=sell_count,
                        summary={
                            "pnl": realized_pnl,
                            "win_rate": win_rate,
                            "positions": pos_summary,
                        },
                    ))
                    db_session.commit()
                finally:
                    db_session.close()
            except Exception as e:
                logger.error(f"일일 보고서 전송 실패: {e}")
            session_tracker.mark_daily_report_sent()

        # ── Helper: 데이터 갱신 + 자동 재학습 ──
        async def _refresh_data_and_retrain() -> None:
            nonlocal _refresh_counter, _last_retrain_date, universe, index_prices
            _refresh_counter += 1
            if _refresh_counter not in (REFRESH_FIRST_CYCLE, REFRESH_AFTER_RETRAIN) and _refresh_counter % REFRESH_INTERVAL != 0:
                return
            universe, index_prices = await data_provider.refresh_prices()
            if dashboard_app:
                dashboard_app.state.latest_universe = universe
                dashboard_app.state.latest_index_prices = index_prices
            # Auto retrain
            if config.strategy.ml_auto_retrain and model:
                today_kst = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if _last_retrain_date != today_kst and _is_after_market_close_kst():
                    _last_retrain_date = today_kst
                    codes = [info.code for info, _ in universe]
                    prices_list = [prices for _, prices in universe]
                    logger.info(f"자동 재학습 시작: {len(codes)}개 종목")
                    try:
                        changed, result = model.auto_retrain(prices_list, codes)
                        if changed:
                            model.save()
                            logger.info(f"자동 재학습 완료: AUC={result.auc:.3f}")
                            if dashboard_app:
                                dashboard_app.state.model = model
                    except Exception as e:
                        logger.exception(f"자동 재학습 실패: {e}")

                    # 분봉ML 자동 재학습 (in-process, PC-3)
                    if config.strategy.ml_minute_auto_retrain:
                        logger.info("분봉ML 자동 재학습 시작")
                        try:
                            from ml.training.trainer import train_from_minute_data
                            mb = getattr(orchestrator, '_minute_builder', None)
                            minute_prices_list = []
                            minute_codes: list[str] = []
                            for code in codes:
                                if mb is not None:
                                    bars = mb.get_bars(code, 60)
                                else:
                                    bars = None
                                if not bars or len(bars) < 60:
                                    continue
                                minute_prices_list.append(bars)
                                minute_codes.append(code)
                            if len(minute_codes) >= 10:
                                minute_model, minute_result = train_from_minute_data(
                                    minute_prices_list, minute_codes,
                                    model_type=config.strategy.ml_model_type,
                                    forward_minutes=config.strategy.ml_short_forward_minutes,
                                    forward_threshold=config.strategy.ml_minute_forward_threshold,
                                )
                                if minute_model and minute_model.is_trained:
                                    model.update_minute_model(minute_model)
                                    from ml.training.persistence import save_model
                                    save_model(minute_model, "models/trained/minute_model.pkl")
                                    logger.info(f"분봉ML 재학습 완료: AUC={minute_result.auc:.3f}")
                            else:
                                logger.warning(f"분봉ML 재학습 데이터 부족: {len(minute_codes)}개 종목")
                        except Exception as e:
                            logger.exception(f"분봉ML 재학습 실패: {e}")

        # ── Helper: 동적 유니버스 관리 ──
        async def _manage_universe() -> list:
            from data.universe_manager import UniverseManager
            if not hasattr(orchestrator, '_universe_manager') or orchestrator._universe_manager is None:
                orchestrator._universe_manager = UniverseManager()
            kst = now_kst()
            um = orchestrator._universe_manager
            # 09:00~09:10: 초기화 + 거래 차단
            if kst.hour == 9 and 0 <= kst.minute < 10:
                if not um.initialized and kst.minute >= 0:
                    await um.initialize(broker, data_provider)
                orchestrator._trading_allowed = False
            else:
                if not um.initialized:
                    await um.initialize(broker, data_provider)
                # 09:10 이후 _trading_allowed 초기화 (09:00~09:10에 False 설정된 것 복원)
                if orchestrator._trading_allowed is False or orchestrator._trading_allowed is None:
                    orchestrator._trading_allowed = True
            # 보유 포지션 업데이트
            try:
                positions = await broker.get_positions()
                um.held_codes = {p.code for p in positions}
            except Exception:
                pass
            # 5분 교체
            if um.initialized and um.should_swap(kst):
                await um.swap(broker, data_provider)
                um.last_swap = kst
            # active + reserve + 보유포지션 전체 스크리닝
            # reserve도 검토 대상에 포함해 더 많은 종목에서 후보 발견
            pool_codes = set(um.active.keys()) | set(um.reserve.keys()) if um.initialized else None
            if pool_codes:
                include_codes = pool_codes | orchestrator._last_held_codes
                # Step 1: _universe_cache 우선 (C안 refresh로 최신 데이터)
                cache_by_code: dict[str, tuple] = {}
                if data_provider._universe_cache:
                    for info, prices in data_provider._universe_cache:
                        cache_by_code[info.code] = (info, prices)
                result = []
                found = set()
                for code in include_codes:
                    if code in cache_by_code:
                        result.append(cache_by_code[code])
                        found.add(code)
                # Step 2: reserve 중 _universe_cache에 없는 종목 → um._reserve_prices에서 보충
                missing = include_codes - found
                if missing and hasattr(um, '_reserve_prices'):
                    reserve_prices = um._reserve_prices
                    for code in missing:
                        entry = reserve_prices.get(code)
                        if entry is None:
                            continue
                        info, prices = entry
                        # info.name이 빈값이면 ScoredStock name으로 fallback
                        if not info.name and code in um.reserve:
                            from dataclasses import replace
                            info = replace(info, name=um.reserve[code].name)
                        result.append((info, prices))
                return result
            return universe

        # ── Helper: 정기 Status 메시지 ──
        async def _send_status_if_due(result) -> None:
            nonlocal _last_status_time
            if not telegram_bot:
                return
            from core.types import is_market_hours
            if not is_market_hours():
                return
            now_ts = time.time()
            if now_ts - _last_status_time < 600:
                return
            _last_status_time = now_ts
            try:
                tokens = []
                tokens.append(f"🔄 사이클 #{result.timestamp.strftime('%H:%M')}")
                preset_name = getattr(orchestrator, '_preset_router', None)
                preset_name = getattr(preset_name, 'current_preset', None) if preset_name else None
                mode_label = {"swing": "스윙", "short_term": "단타", "hold": "홀드"}.get(result.mode, result.mode)
                tokens.append(f"🏷️ {mode_label}" + (f" · {preset_name}" if preset_name else ""))
                regime_kr = {"bull": "강세장", "neutral": "중립장", "bear": "약세장"}.get(result.regime.value if result.regime else "", "?")
                rs = getattr(orchestrator, '_last_regime', None)
                tokens.append(f"📈 {regime_kr} ({rs.score:.0f}점)" if rs else "📈 ?")
                tokens.append(f"🎯 후보 {result.candidates_found}개")
                buy_preds = sum(1 for p in result.predictions if p.get('action') == 'buy')
                if buy_preds > 0:
                    tokens.append(f"🤖 ML BUY {buy_preds}건")
                if result.buys_executed > 0 or result.sells_executed > 0:
                    tokens.append(f"🟢 매수 {result.buys_executed}건")
                    tokens.append(f"🔴 매도 {result.sells_executed}건")
                acc = await orchestrator._broker.get_account_summary()
                if acc:
                    tokens.append(f"💰 {acc.deposit:,.0f}원 / {acc.total_asset:,.0f}원")
                positions = await orchestrator._fetch_positions()
                if positions:
                    pos_str = " · ".join(f"{p.name} {p.quantity}주 ({p.profit_loss_pct:+.1f}%)" for p in positions[:3])
                    tokens.append(f"📊 {pos_str}" + (f" 외 {len(positions)-3}종목" if len(positions) > 3 else ""))
                await telegram_bot.send_system_event("info", "\n".join(tokens))
                logger.info("정기 Status 메시지 발송 완료")
            except Exception as e:
                logger.warning(f"정기 Status 발송 실패: {e}")

        # ── Main loop ──
        while True:
            try:
                await _handle_market_session()
                await _send_daily_report()
                await _refresh_data_and_retrain()

                if orchestrator.controller.is_running:
                    stock_universe = await _manage_universe()
                    result = await orchestrator.run_cycle(index_prices, stock_universe)

                    # 자동 프리셋 변경 시 텔레그램 알림
                    if telegram_bot and result.preset_change:
                        await telegram_bot.send_system_event(
                            "info", f"🔄 프리셋 자동 전환: {result.preset_change}",
                        )

                    # 미체결 주문 취소 (지정가 30초 경과 시)
                    if orchestrator.controller.is_running:
                        cancelled = await orchestrator.cancel_unfilled_orders(max_age_seconds=30)
                        if cancelled > 0 and telegram_bot:
                            await telegram_bot.send_system_event(
                                "info", f"미체결 주문 {cancelled}건 자동 취소",
                            )

                    # 거래 알림
                    if telegram_bot and (result.buys_executed > 0 or result.sells_executed > 0):
                        await telegram_bot.send_system_event("info", result.reason)

                    # WebSocket 브로드캐스트
                    if dashboard_app and hasattr(dashboard_app.state, 'broadcast'):
                        await dashboard_app.state.broadcast({
                            "type": "cycle",
                            "buys": result.buys_executed,
                            "sells": result.sells_executed,
                            "candidates": result.candidates_found,
                            "regime": result.regime.value if result.regime else "unknown",
                            "mode": result.mode,
                        })

                # Reset consecutive error counter on successful cycle
                _consecutive_errors = 0

                # 정기 Status 메시지 (10분마다)
                if orchestrator.controller.is_running:
                    await _send_status_if_due(result)

                await asyncio.sleep(tick_interval)

            except asyncio.CancelledError:
                logger.info("매매 루프 종료")
                break
            except Exception as e:
                _consecutive_errors += 1
                logger.error(
                    f"매매 루프 오류 ({_consecutive_errors}/5): "
                    f"{type(e).__name__}: {e}"
                )
                if telegram_bot:
                    await telegram_bot.send_system_event(
                        "error", f"매매 루프 오류 ({_consecutive_errors}회): {e}"
                    )
                if _consecutive_errors >= 5:
                    logger.critical("연속 에러 5회 — 매매 루프 중단")
                    if telegram_bot:
                        await telegram_bot.send_system_event(
                            "critical", "연속 오류 5회로 시스템 중단"
                        )
                    break
                backoff = min(tick_interval * (2 ** _consecutive_errors), 300)
                await asyncio.sleep(backoff)

    # ── 7. Start everything ──
    tasks: list[asyncio.Task] = []

    # Trading loop
    tasks.append(asyncio.create_task(trading_loop()))

    # Dashboard API server
    # Telegram command polling
    if telegram_bot:
        tasks.append(asyncio.create_task(telegram_bot.start_polling()))

    if dashboard_app:
        config_uvicorn = uvicorn.Config(
            dashboard_app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )
        api_server = uvicorn.Server(config_uvicorn)
        tasks.append(asyncio.create_task(api_server.serve()))

    logger.info("NGSAT 시스템 기동 완료")
    logger.info("  - 매매 사이클: {}초 주기".format(tick_interval))
    if dashboard_app:
        logger.info("  - 대시보드: http://localhost:8000")
    if telegram_bot:
        logger.info("  - 텔레그램: 활성화")
    logger.info("  - 제어: 대시보드 또는 텔레그램 /start 로 매매 시작")

    # ── 8. Wait for shutdown signal ──
    stop_event = asyncio.Event()

    def signal_handler(sig, frame=None):
        logger.info(f"종료 신호 수신: {sig}")
        stop_event.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        await stop_event.wait()
    finally:
        # Cleanup
        logger.info("NGSAT 종료 중...")

        # Send Telegram notification before shutdown
        if telegram_bot:
            try:
                await telegram_bot.send_system_event("stop", "NGSAT 시스템 종료")
            except Exception:
                pass

        # Cancel all running tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Cleanup resources
        if data_provider is not None:
            await data_provider.close()
        await broker.close()
        if orchestrator:
            await orchestrator.close()
        logger.info("NGSAT 종료 완료")


async def train_model(config):
    """Train ML model using real KIS data and save to disk."""
    from data.real_data_provider import RealDataProvider
    from ml.training.trainer import train_from_price_data

    logger.info("=== NGSAT ML 모델 학습 (KIS 실데이터) ===")

    data_provider = RealDataProvider(
        training_days=config.strategy.ml_training_days,
        start_date=config.strategy.ml_training_start_date,
        end_date=config.strategy.ml_training_end_date,
    )
    universe, index_prices = await data_provider.load()

    if not universe:
        logger.error("KIS 실데이터 로드 실패 — 학습 중단")
        return

    logger.info(f"KIS 실데이터 로드 완료: {len(universe)}종목")
    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]

    model, result = train_from_price_data(
        all_prices, codes,
        model_type=config.strategy.ml_model_type,
        forward_days=config.strategy.ml_swing_forward_days,
        forward_threshold=config.strategy.ml_forward_threshold,
    )

    if result.success:
        save_path = model.save()
        logger.info(f"모델 저장 완료: {save_path}")
        logger.info(result.reason)
    else:
        logger.error(f"학습 실패: {result.reason}")


def main():
    args = parse_args()
    config = load_config()

    setup_logger("ngsat", level=20, log_file="logs/ngsat.log")  # INFO + file

    logger.info("NGSAT — New Generation Stock Auto Trader")
    logger.info(f"환경: {config.env.value}")

    if args.backtest:
        asyncio.run(run_backtest(config))
    elif args.train:
        asyncio.run(train_model(config))
    else:
        asyncio.run(run_live(config, args))


if __name__ == "__main__":
    main()
