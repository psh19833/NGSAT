"""NGSAT dashboard backend — FastAPI API server.

Provides REST API endpoints for the React frontend dashboard.
Completely new design — not based on SAT3.

Endpoints:
  GET  /api/status           — 시스템 상태
  GET  /api/account           — 계좌 현황
  GET  /api/positions         — 보유 포지션
  GET  /api/trades            — 거래 내역
  GET  /api/regime            — 현재 레짐
  POST /api/control/start     — 매매 시작
  POST /api/control/stop      — 매매 일시정지
  POST /api/control/shutdown  — 시스템 종료
  POST /api/control/forcesell — 강제 매도
  POST /api/control/forcehold — 강제 홀드
  WS   /ws/realtime           — 실시간 업데이트
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.logger import logger
from core.config_service import ConfigService
from core.types import now_kst

# ── ConfigService field map (DB key → StrategyConfig attr) ──
CONFIG_FIELD_MAP: dict[str, str] = {
    "NGSAT_BUY_THRESHOLD": "buy_threshold",
    "NGSAT_SELL_THRESHOLD": "sell_threshold",
    "NGSAT_REGIME_BULL_THRESHOLD": "regime_bull_threshold",
    "NGSAT_REGIME_BEAR_THRESHOLD": "regime_bear_threshold",
    "NGSAT_REGIME_WEIGHT_MA": "regime_weight_ma",
    "NGSAT_REGIME_WEIGHT_RSI": "regime_weight_rsi",
    "NGSAT_REGIME_WEIGHT_BOLLINGER": "regime_weight_bollinger",
    "NGSAT_REGIME_WEIGHT_CHANGE_RATE": "regime_weight_change_rate",
    "NGSAT_REGIME_WEIGHT_VOLUME": "regime_weight_volume",
    "NGSAT_REGIME_WEIGHT_ADX": "regime_weight_adx",
    "NGSAT_SCREENER_BULL_MIN_SCORE": "screener_bull_min_score",
    "NGSAT_SCREENER_BULL_MAX_CANDIDATES": "screener_bull_max_candidates",
    "NGSAT_SCREENER_NEUTRAL_MIN_SCORE": "screener_neutral_min_score",
    "NGSAT_SCREENER_NEUTRAL_MAX_CANDIDATES": "screener_neutral_max_candidates",
    "NGSAT_SCREENER_BEAR_MIN_SCORE": "screener_bear_min_score",
    "NGSAT_SCREENER_BEAR_MAX_CANDIDATES": "screener_bear_max_candidates",
    "NGSAT_MODE_SWING_STOP_LOSS": "mode_swing_stop_loss_pct",
    "NGSAT_MODE_SWING_DAILY_LOSS": "mode_swing_daily_loss_pct",
    "NGSAT_MODE_SWING_POSITION_SIZE": "mode_swing_position_size",
    "NGSAT_MODE_SHORT_STOP_LOSS": "mode_short_stop_loss_pct",
    "NGSAT_MODE_SHORT_DAILY_LOSS": "mode_short_daily_loss_pct",
    "NGSAT_MODE_SHORT_POSITION_SIZE": "mode_short_position_size",
    "NGSAT_MAX_HOLDINGS": "max_holdings",
    "NGSAT_ML_TRAINING_DAYS": "ml_training_days",
    "NGSAT_ML_TRAINING_START_DATE": "ml_training_start_date",
    "NGSAT_ML_TRAINING_END_DATE": "ml_training_end_date",
    "NGSAT_ML_AUTO_SELECT_MODEL": "ml_auto_select_model",
    "NGSAT_MAX_TOTAL_EXPOSURE_PCT": "max_total_exposure_pct",
    "NGSAT_TRAILING_STOP_ENABLED": "trailing_stop_enabled",
    "NGSAT_TRAILING_STOP_ATR_MULTIPLIER": "trailing_stop_atr_multiplier",
    "NGSAT_TRAILING_STOP_ACTIVATE_PCT": "trailing_stop_activate_pct",
    "NGSAT_PARTIAL_TP_ENABLED": "partial_tp_enabled",
    "NGSAT_PARTIAL_TP1_PCT": "partial_tp1_pct",
    "NGSAT_PARTIAL_TP1_RATIO": "partial_tp1_ratio",
    "NGSAT_PARTIAL_TP2_PCT": "partial_tp2_pct",
    "NGSAT_PARTIAL_TP2_RATIO": "partial_tp2_ratio",
}


# ── Request models ──

class ForceSellRequest(BaseModel):
    code: str


class ForceHoldRequest(BaseModel):
    code: str


class StrategyUpdateRequest(BaseModel):
    """전략 설정 업데이트 요청 — 값 범위 검증."""
    buy_threshold: float | None = None
    sell_threshold: float | None = None
    regime_bull_threshold: float | None = Field(None, ge=10, le=100)
    regime_bear_threshold: float | None = Field(None, ge=0, le=90)
    regime_weight_ma: float | None = Field(None, ge=0, le=100)
    regime_weight_rsi: float | None = Field(None, ge=0, le=100)
    regime_weight_bollinger: float | None = Field(None, ge=0, le=100)
    regime_weight_change_rate: float | None = Field(None, ge=0, le=100)
    regime_weight_volume: float | None = Field(None, ge=0, le=100)
    regime_weight_adx: float | None = Field(None, ge=0, le=100)
    screener_bull_min_score: float | None = Field(None, ge=0, le=100)
    screener_bull_max_candidates: int | None = Field(None, ge=1, le=100)
    screener_neutral_min_score: float | None = Field(None, ge=0, le=100)
    screener_neutral_max_candidates: int | None = Field(None, ge=1, le=50)
    screener_bear_min_score: float | None = Field(None, ge=0, le=100)
    screener_bear_max_candidates: int | None = Field(None, ge=1, le=30)
    mode_swing_stop_loss_pct: float | None = Field(None, ge=0.5, le=10)
    mode_swing_daily_loss_pct: float | None = Field(None, ge=0.5, le=20)
    mode_swing_position_size: float | None = Field(None, ge=0.01, le=0.5)
    mode_short_stop_loss_pct: float | None = Field(None, ge=0.3, le=5)
    mode_short_daily_loss_pct: float | None = Field(None, ge=0.3, le=10)
    mode_short_position_size: float | None = Field(None, ge=0.01, le=0.3)
    mode_hold_stop_loss_pct: float | None = Field(None, ge=0.5, le=10)
    mode_hold_daily_loss_pct: float | None = Field(None, ge=0.5, le=20)
    mode_hold_position_size: float | None = Field(None, ge=0.0, le=0.2)
    max_holdings: int | None = Field(None, ge=1, le=100)
    ml_model_type: str | None = None
    ml_auto_retrain: bool | None = None
    ml_training_days: int | None = Field(None, ge=30, le=1000)
    ml_training_start_date: str | None = None
    ml_training_end_date: str | None = None
    ml_auto_select_model: bool | None = None
    mode_high_volatility_atr_pct: float | None = Field(None, ge=0.1, le=10)
    mode_low_volatility_atr_pct: float | None = Field(None, ge=0.1, le=5)
    max_total_exposure_pct: float | None = Field(None, ge=10, le=200)
    trailing_stop_enabled: bool | None = None
    trailing_stop_atr_multiplier: float | None = Field(None, ge=0.5, le=10)
    trailing_stop_activate_pct: float | None = Field(None, ge=0.1, le=20)
    partial_tp_enabled: bool | None = None
    partial_tp1_pct: float | None = Field(None, ge=0.5, le=20)
    partial_tp1_ratio: float | None = Field(None, ge=0.1, le=0.9)
    partial_tp2_pct: float | None = Field(None, ge=1.0, le=50)
    partial_tp2_ratio: float | None = Field(None, ge=0.1, le=0.5)
    reset: bool = False


# ── API DTO models ──

class HealthResponse(BaseModel):
    """/api/health response."""
    status: str = "ok"
    service: str = "NGSAT Dashboard API"
    server_time: str = ""


class StatusResponse(BaseModel):
    """/api/status response."""
    connected: bool
    state: str = "idle"
    is_running: bool = False
    risk_halted: bool = False
    risk_reason: str | None = None
    cycle_count: int = 0
    current_mode: str = "swing"
    mode_stop_loss_pct: float | None = None
    mode_daily_loss_limit: float | None = None
    server_time: str = ""


# ── App factory ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler — startup/shutdown lifecycle."""
    logger.info("NGSAT Dashboard API starting up")
    yield
    # Shutdown: dispose DB engine if we created one
    orch = getattr(app.state, 'orchestrator', None)
    if orch is not None:
        try:
            if hasattr(orch, '_db_engine') and orch._db_engine:
                orch._db_engine.dispose()
        except Exception as e:
            logger.warning(f"DB engine dispose error: {e}")
    logger.info("NGSAT Dashboard API shutdown complete")


def create_app(orchestrator=None, config=None) -> FastAPI:
    """Create the FastAPI dashboard app.

    Args:
        orchestrator: TradingOrchestrator instance. If None,
                      endpoints return "not connected" responses.

    Returns:
        FastAPI application.
    """
    app = FastAPI(
        title="NGSAT Dashboard API",
        description="New Generation Stock Auto Trader — Dashboard",
        version="0.1.1",
        lifespan=lifespan,
    )

    # CORS for frontend — production: set NGSAT_CORS_ORIGINS env (comma-separated)
    origins_env = os.getenv("NGSAT_CORS_ORIGINS", "http://localhost:5173,http://localhost:8000,http://127.0.0.1:8000")
    allow_origins = [o.strip() for o in origins_env.split(",") if o.strip()] if origins_env != "*" else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve React frontend static files (built by npm run build)
    # Catch-all: non-API paths serve index.html (SPA support)
    from pathlib import Path
    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        import mimetypes
        mimetypes.init()

        @app.exception_handler(404)
        async def serve_frontend(request, exc):
            # API routes return 404 as usual — only catch non-/api paths
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)

            file_path = frontend_dist / request.url.path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))

            # SPA: all other paths serve index.html
            index = frontend_dist / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        logger.info(f"대시보드 프론트엔드 마운트: {frontend_dist}")
    else:
        logger.warning(f"프론트엔드 빌드 파일 없음: {frontend_dist} — npm run build 필요")

    # Store orchestrator reference
    app.state.orchestrator = orchestrator
    app.state.config = config

    # ConfigService: DB-backed runtime config persistence
    if config is not None:
        from data.db import get_engine
        from sqlalchemy.orm import sessionmaker
        from core.models import Base

        engine = get_engine(config.database)
        Base.metadata.create_all(engine)
        sess = sessionmaker(bind=engine)()
        config_service = ConfigService(sess)
        app.state.config_service = config_service

        applied = config_service.apply_to(config.strategy, CONFIG_FIELD_MAP)
        if applied > 0:
            logger.info(f"ConfigService: {applied}개 DB 설정 적용됨")

    def _get_orchestrator():
        return app.state.orchestrator

    def _not_connected():
        return {"error": "거래 시스템이 연결되지 않았습니다", "connected": False}

    def _get_app_config():
        """Get StrategyConfig from app state."""
        if app.state.config is None:
            return None
        return app.state.config.strategy

    # ── Auto-preset toggle ──
    @app.post("/api/strategy/auto-preset")
    async def set_auto_preset(data: dict):
        enabled = data.get("enabled", True)
        orch = _get_orchestrator()
        if orch:
            router = getattr(orch, '_preset_router', None)
            if router:
                router.set_auto_enabled(enabled)
        return {"connected": True, "enabled": enabled}

    @app.get("/api/strategy/auto-preset")
    async def get_auto_preset():
        orch = _get_orchestrator()
        enabled = True
        if orch:
            router = getattr(orch, '_preset_router', None)
            if router:
                enabled = router.auto_enabled
        return {"connected": True, "enabled": enabled}

    # ── Status ──
    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        orch = _get_orchestrator()
        if orch is None:
            return StatusResponse(connected=False)

        controller = orch.controller
        risk = orch.risk_manager

        return StatusResponse(
            connected=True,
            state=controller.state.value,
            is_running=controller.is_running,
            risk_halted=risk.is_halted,
            risk_reason=risk.halt_reason,
            cycle_count=orch._cycle_count,
            current_mode=orch._current_mode,
            mode_stop_loss_pct=risk.effective_stop_loss_pct,
            mode_daily_loss_limit=risk.effective_daily_loss_limit,
            server_time=now_kst().isoformat(),
        )

    # ── Account ──
    @app.get("/api/account")
    async def get_account():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        try:
            account = await orch._broker.get_account_summary()
            return {
                "connected": True,
                "total_asset": account.total_asset,
                "deposit": account.deposit,
                "total_eval": account.total_eval,
                "total_profit_loss": account.total_profit_loss,
                "total_profit_loss_pct": account.total_profit_loss_pct,
                "daily_loss": account.daily_loss,
                "daily_loss_pct": account.daily_loss_pct,
            }
        except Exception as e:
            return {"error": str(e), "connected": True}

    # ── Positions ──
    @app.get("/api/positions")
    async def get_positions():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        try:
            positions = await orch._broker.get_positions()
            return {
                "connected": True,
                "positions": [
                    {
                        "code": p.code,
                        "name": p.name,
                        "market": p.market.value,
                        "quantity": p.quantity,
                        "buy_price": p.buy_price,
                        "current_price": p.current_price,
                        "buy_amount": p.buy_amount,
                        "eval_amount": p.eval_amount,
                        "profit_loss": p.profit_loss,
                        "profit_loss_pct": p.profit_loss_pct,
                        "stop_loss_pct": p.stop_loss_pct,
                        "stop_loss_reason": p.stop_loss_reason,
                        "is_force_hold": orch.controller.is_force_hold(p.code),
                    }
                    for p in positions
                ],
            }
        except Exception as e:
            return {"error": str(e), "connected": True}

    # ── Trades (from database) ──
    @app.get("/api/trades")
    async def get_trades(limit: int = 50, offset: int = 0):
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        try:
            records = orch._trade_repo.get_recent_trades(limit, offset)
            total = orch._trade_repo.count_trades()
            trades = [
                {
                    "date": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                    "code": r.code, "name": r.name,
                    "side": r.side, "quantity": r.quantity,
                    "price": r.price, "amount": r.amount,
                    "action": r.action, "reason": r.reason,
                    "mode": r.mode,
                }
                for r in records
            ]
            return {"connected": True, "trades": trades, "total": total}
        except Exception as e:
            return {"connected": True, "trades": [], "message": f"거래 내역 조회 오류: {e}"}

    # ── Regime ──
    @app.get("/api/regime")
    async def get_regime():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        if orch._last_regime is None:
            return {
                "connected": True,
                "regime": "unknown",
                "mode": orch._current_mode,
                "score": 0,
                "reason": "아직 레짐 평가가 실행되지 않았습니다",
                "regime_skipped": orch._regime_skipped,
            }

        regime = orch._last_regime
        regime_kr = {
            "bull": "강세장",
            "neutral": "중립장",
            "bear": "약세장",
        }.get(regime.regime.value, regime.regime.value)

        return {
            "connected": True,
            "regime": regime.regime.value,
            "mode": orch._current_mode,
            "regime_kr": regime_kr,
            "score": regime.score,
            "reason": regime.reason,
            "evidence": {
                k: (None if (isinstance(v, float) and (v != v)) else v)
                for k, v in regime.evidence.items()
            },
            "regime_skipped": orch._regime_skipped,
        }

    # ── Indices: KOSPI/KOSDAQ + US indices ──
    @app.get("/api/indices")
    async def get_indices():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        result = {}
        broker = getattr(orch, '_broker', None)

        # KOSPI (raw KIS response 사용, parse_price 우회)
        try:
            if broker and hasattr(broker, '_http'):
                resp = await broker._http.get("inquire_index_price", params={
                    "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001",
                })
                if resp.success:
                    raw = resp.raw.get("output", {}) if isinstance(resp.raw, dict) else resp.data
                    price = float(raw.get("bstp_nmix_prpr", 0) or 0)
                    chg = raw.get("bstp_nmix_prdy_ctrt") or raw.get("prdy_ctrt") or "0"
                    result["kospi"] = {
                        "price": round(price, 2) if price else 0,
                        "change_pct": round(float(chg), 2) if chg else 0.0,
                    }
        except Exception:
            pass

        # KOSDAQ
        try:
            if broker and hasattr(broker, '_http'):
                resp = await broker._http.get("inquire_index_price", params={
                    "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "1001",
                })
                if resp.success:
                    raw = resp.raw.get("output", {}) if isinstance(resp.raw, dict) else resp.data
                    price = float(raw.get("bstp_nmix_prpr", 0) or 0)
                    chg = raw.get("bstp_nmix_prdy_ctrt") or raw.get("prdy_ctrt") or "0"
                    result["kosdaq"] = {
                        "price": round(price, 2) if price else 0,
                        "change_pct": round(float(chg), 2) if chg else 0.0,
                    }
        except Exception:
            pass

        # US indices via Yahoo Finance
        us_symbols = {
            "sp500": "^GSPC", "nasdaq": "^IXIC", "dow": "^DJI",
        }
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            for key, symbol in us_symbols.items():
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        data = resp.json()
                        r0 = data.get("chart", {}).get("result", [{}])[0]
                        # Get closes from quote data (most reliable)
                        quotes = r0.get("indicators", {}).get("quote", [{}])[0]
                        closes = [c for c in quotes.get("close", []) if c is not None]
                        if len(closes) >= 2:
                            prev_close = closes[-2]
                            current = closes[-1]
                        else:
                            meta = r0.get("meta", {})
                            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
                            current = meta.get("regularMarketPrice") or meta.get("currentPrice") or 0
                        if current and prev_close and prev_close > 0:
                            change_pct = (current - prev_close) / prev_close * 100
                            result[key] = {
                                "price": round(current, 2),
                                "change_pct": round(change_pct, 2),
                            }
                except Exception:
                    pass

        return {"connected": True, "indices": result}

    # ── Control: Start ──
    @app.post("/api/control/start")
    async def control_start():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        msg = orch.controller.start()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}

    # ── Control: Stop ──
    @app.post("/api/control/stop")
    async def control_stop():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        msg = orch.controller.stop()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}

    # ── Control: Shutdown ──
    @app.post("/api/control/shutdown")
    async def control_shutdown():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        msg = orch.controller.shutdown()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}

    # ── Control: Restart ──
    @app.post("/api/control/restart")
    async def control_restart():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        msg = orch.controller.restart()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}

    # ── Control: Force Sell ──
    @app.post("/api/control/forcesell")
    async def control_force_sell(req: ForceSellRequest):
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        result = await orch.force_sell(req.code)
        return {
            "connected": True,
            "success": result.success,
            "message": result.reason if result.success else result.error,
            "order_id": result.order_id,
        }

    # ── Control: Force Hold ──
    @app.post("/api/control/forcehold")
    async def control_force_hold(req: ForceHoldRequest):
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()

        orch.controller.force_hold(req.code)
        return {"connected": True, "message": f"강제 홀드 설정: {req.code}"}

    # ── Control: Manual Retrain ──
    @app.post("/api/control/retrain")
    async def control_retrain():
        """수동 ML 모델 재학습 트리거.

        app.state에 저장된 data_provider + model + universe 정보를 사용.
        """
        model = getattr(app.state, 'model', None)
        data_provider = getattr(app.state, 'data_provider', None)
        universe = getattr(app.state, 'latest_universe', None)

        if not model:
            return {"connected": False, "message": "ML 모델이 로드되지 않았습니다"}
        if not data_provider:
            return {"connected": False, "message": "데이터 프로바이더가 없습니다"}
        if not universe:
            return {"connected": False, "message": "시세 데이터가 없습니다 — 매매 사이클 시작 후 시도하세요"}

        try:
            # 현재 전략 설정에 맞춰 데이터 프로바이더 날짜 갱신 + 캐시 무효화
            cfg = _get_app_config()
            if cfg:
                data_provider.update_date_range(
                    start_date=cfg.ml_training_start_date,
                    end_date=cfg.ml_training_end_date,
                    training_days=cfg.ml_training_days,
                )
            # Fresh data load with updated date range
            new_universe, _ = await data_provider.load()
            if not new_universe:
                return {"connected": False, "message": "시세 데이터 로드 실패"}

            codes = [info.code for info, _ in new_universe]
            prices_list = [prices for _, prices in new_universe]

            logger.info(f"수동 재학습 시작: {len(codes)}개 종목, 모델={model.model_type}")

            # Config의 auto_select_model을 모델에 반영
            cfg = _get_app_config()
            if cfg and hasattr(cfg, 'ml_auto_select_model'):
                model.auto_select_model = cfg.ml_auto_select_model

            changed, result = model.auto_retrain(prices_list, codes)

            if changed:
                # Save new model
                model.save()
                # Update orchestrator's inference model
                orch = _get_orchestrator()
                if orch and hasattr(orch, '_inference') and orch._inference is not None:
                    orch._inference._model = model
                logger.info(f"수동 재학습 완료: AUC={result.auc:.3f} (model_type={result.model_type})")
                return {
                    "connected": True,
                    "message": f"재학습 완료 — {result.model_type}, AUC={result.auc:.3f} (향상)",
                    "model_type": result.model_type,
                    "auc": result.auc,
                    "replaced": True,
                }
            elif result.success:
                # Model trained but not better than current best —
                # notify user of the comparison, do NOT replace the model
                current_auc = getattr(model, '_last_auc', 0.0)
                logger.info(
                    f"수동 재학습: 새 모델 AUC={result.auc:.3f} ≤ 기존 "
                    f"AUC={current_auc:.3f} — 기존 모델 유지"
                )
                return {
                    "connected": True,
                    "message": (
                        f"새 모델 AUC={result.auc:.3f} (기존 {current_auc:.3f} 유지"
                        f" — {result.model_type}, 성능 낮음)"
                    ),
                    "model_type": model.model_type,       # 기존 모델 유지
                    "auc": current_auc,                    # 기존 AUC 유지
                    "new_auc": result.auc,                 # 새 모델의 AUC (참고용)
                    "replaced": False,
                }
            else:
                logger.info(f"수동 재학습 실패 — {result.reason}")
                return {
                    "connected": False,
                    "model_type": model.model_type,
                    "auc": getattr(model, '_last_auc', 0.0),
                    "message": result.reason,
                }
        except Exception as e:
            logger.exception("수동 재학습 실패")
            return {"connected": False, "message": f"재학습 실패: {e}"}

    # ── Strategy Config ──
    @app.get("/api/strategy/config")
    async def get_strategy_config():
        """현재 전략·정책 설정값 반환 + 현재 ML 모델 정보."""
        from dataclasses import asdict
        cfg = _get_app_config()
        if cfg is None:
            return {"connected": False}
        result = {"connected": True, "config": asdict(cfg)}
        # Detect active preset by comparing config values
        try:
            import json
            from pathlib import Path
            presets_path = Path(__file__).resolve().parent.parent.parent / "config" / "presets.json"
            if presets_path.exists():
                with open(presets_path, encoding="utf-8") as f:
                    presets = json.load(f)
                cfg_dict = asdict(cfg)
                for name, p in presets.items():
                    if all(
                        abs(cfg_dict.get(k, 0) - v) < 0.001
                        for k, v in p.get("values", {}).items()
                    ):
                        result["active_preset"] = name
                        break
        except Exception:
            pass
        orch = _get_orchestrator()
        if orch and hasattr(orch, '_inference') and orch._inference is not None:
            m = getattr(orch._inference, '_model', None)
            if m is not None:
                result['current_model_type'] = m.model_type
                result['current_auc'] = getattr(m, '_last_auc', None)
        return result

    @app.put("/api/strategy/config")
    async def update_strategy_config(data: StrategyUpdateRequest):
        """전략·정책 설정값 업데이트 → DB (ConfigService) 반영.
        Pydantic으로 값 범위 검증됨 (buy_threshold=0.0~1.0 등).
        """
        cfg = _get_app_config()
        if cfg is None:
            return {"connected": False}

        cs: ConfigService | None = getattr(app.state, 'config_service', None)

        if data.reset:
            from core.config import StrategyConfig
            restored = StrategyConfig()
            if cs:
                for key in list(CONFIG_FIELD_MAP.keys()):
                    cs.delete(key)
            return {"connected": True, "config": asdict(restored), "restart_required": True}

        # 부분 업데이트 (Pydantic 모델의 None이 아닌 필드만 적용)
        updated = 0
        for key, value in data.model_dump(exclude_none=True).items():
            if key == "reset":
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)
                if cs:
                    db_key = next((k for k, v in CONFIG_FIELD_MAP.items() if v == key), None)
                    if db_key:
                        cs.set(db_key, value)
                updated += 1

        if updated > 0:
            logger.info(f"전략 설정 {updated}개 변경 — DB 저장 (재시작 후 유지)")

        return {"connected": True, "message": f"{updated}개 저장 완료", "config": asdict(cfg), "restart_required": True}

    # ── Presets ──
    @app.get("/api/strategy/presets")
    async def get_presets():
        """설정 프리셋 목록 반환 (config/presets.json)."""
        import json as _json
        from pathlib import Path
        presets_path = Path(__file__).resolve().parent.parent.parent / "config" / "presets.json"
        if presets_path.exists():
            try:
                data = _json.loads(presets_path.read_text(encoding="utf-8"))
                return {"connected": True, "presets": data}
            except Exception as e:
                return {"connected": False, "message": f"프리셋 파일 읽기 실패: {e}"}
        return {"connected": False, "message": "프리셋 파일이 없습니다"}

    @app.post("/api/strategy/apply-preset")
    async def apply_preset(data: dict):
        """프리셋 적용: DB 저장 + 재학습 + 텔레그램 알림."""
        preset_name = data.get("name", "")
        retrain = data.get("retrain", True)
        if not preset_name:
            return {"connected": False, "message": "프리셋 이름이 필요합니다"}

        # 1) Load presets file
        import json as _json
        from pathlib import Path
        presets_path = Path(__file__).resolve().parent.parent.parent / "config" / "presets.json"
        if not presets_path.exists():
            return {"connected": False, "message": "프리셋 파일이 없습니다"}
        all_presets = _json.loads(presets_path.read_text(encoding="utf-8"))
        preset = all_presets.get(preset_name)
        if not preset:
            return {"connected": False, "message": f"프리셋 '{preset_name}'을 찾을 수 없습니다"}

        vals = preset["values"]
        cfg = _get_app_config()
        cs: ConfigService | None = getattr(app.state, 'config_service', None)
        if cfg is None:
            return {"connected": False, "message": "설정이 로드되지 않았습니다"}

        # 2) Apply values to in-memory config + DB
        applied = 0
        for key, value in vals.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
                if cs:
                    db_key = next((k for k, v in CONFIG_FIELD_MAP.items() if v == key), None)
                    if db_key:
                        cs.set(db_key, value)
                applied += 1

        logger.info(f"프리셋 '{preset_name}' 적용: {applied}개 값 변경")

        # 3) Trigger retrain if requested
        retrain_result = None
        if retrain:
            try:
                model = getattr(app.state, 'model', None)
                data_provider = getattr(app.state, 'data_provider', None)
                universe = getattr(app.state, 'latest_universe', None)
                if model and data_provider and universe:
                    if hasattr(cfg, 'ml_auto_select_model'):
                        model.auto_select_model = cfg.ml_auto_select_model
                    data_provider.update_date_range(
                        start_date=getattr(cfg, 'ml_training_start_date', None),
                        end_date=getattr(cfg, 'ml_training_end_date', None),
                        training_days=getattr(cfg, 'ml_training_days', None),
                    )
                    new_universe, _ = await data_provider.load()
                    if new_universe:
                        codes = [info.code for info, _ in new_universe]
                        prices_list = [prices for _, prices in new_universe]
                        changed, result = model.auto_retrain(prices_list, codes)
                        retrain_result = {
                            "changed": changed,
                            "auc": result.auc if result.success else None,
                            "model_type": result.model_type if result.success else None,
                        }
                        if changed:
                            model.save()
                            orch = _get_orchestrator()
                            if orch and hasattr(orch, '_inference') and orch._inference is not None:
                                orch._inference._model = model
                        logger.info(f"프리셋 재학습: {result.reason}")
            except Exception as e:
                logger.warning(f"프리셋 재학습 실패 (skip): {e}")
                retrain_result = {"error": str(e)}

        # 4) Send Telegram notification
        telegram_bot = getattr(app.state, 'telegram_bot', None)
        if telegram_bot:
            try:
                msg = (
                    f"🔄 프리셋 변경: {preset['label']}\n"
                    f"──────────\n"
                    f"{preset['desc']}\n"
                    f"변경 항목: {applied}개\n"
                )
                if retrain_result and retrain_result.get("auc"):
                    direction = "✅ 향상" if retrain_result.get("changed") else "➖ 유지"
                    msg += (
                        f"재학습 완료: {direction}\n"
                        f"AUC: {retrain_result['auc']:.3f}"
                    )
                await telegram_bot.send_system_event("info", msg)
            except Exception as e:
                logger.warning(f"프리셋 텔레그램 전송 실패: {e}")

        return {
            "connected": True,
            "message": f"프리셋 '{preset['label']}' 적용 완료 ({applied}개)",
            "applied": applied,
            "retrain": retrain_result,
        }

    # ── Diagnosis ──
    @app.get("/api/diagnosis")
    async def get_diagnosis():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        diag = orch._last_diagnosis
        if diag is None:
            return {"connected": True, "message": "아직 진단 데이터가 없습니다"}
        return {"connected": True, **diag}

    # ── Backtest ──
    @app.post("/api/backtest/run")
    async def backtest_run():
        """백테스트 실행 (별도 스레드, 진행률 폴링 가능)."""
        from core.backtest_runner import run_backtest_async, get_backtest_state
        import asyncio

        state = get_backtest_state()
        if state["status"] == "running":
            return {"connected": True, "status": "error", "message": "이미 백테스트가 실행 중입니다"}

        cfg = app.state.config
        if cfg is None:
            return {"connected": False}

        # Run in executor to avoid blocking the dashboard
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: asyncio.run(run_backtest_async(cfg))
            )
            return {"connected": True, "status": "completed", "result": result}
        except Exception as e:
            logger.exception(f"백테스트 실행 실패: {e}")
            return {"connected": True, "status": "error", "message": f"백테스트 실패: {e}"}

    @app.get("/api/backtest/state")
    async def backtest_state():
        """백테스트 진행률 조회."""
        from core.backtest_runner import get_backtest_state
        state = get_backtest_state()
        return {"connected": True, **state}

    @app.get("/api/backtest/results")
    async def backtest_results():
        """완료된 백테스트 결과 조회."""
        from core.backtest_runner import get_backtest_state
        state = get_backtest_state()
        if state["status"] == "completed" and state["result"]:
            return {"connected": True, "result": state["result"]}
        return {"connected": True, "result": None, "message": "완료된 백테스트 결과가 없습니다"}

    # ── Health ──
    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            service="NGSAT Dashboard API",
            server_time=now_kst().isoformat(),
        )

    # ── WebSocket: Realtime ──
    connected_ws: set[WebSocket] = set()

    async def broadcast(event: dict):
        """Broadcast event to all connected WebSocket clients."""
        nonlocal connected_ws
        dead: set[WebSocket] = set()
        for ws in connected_ws:
            try:
                await ws.send_json(event)
            except Exception:
                dead.add(ws)
        connected_ws -= dead
    # Expose broadcast so main.py can push trade events
    app.state.broadcast = broadcast

    @app.websocket("/ws/realtime")
    async def websocket_realtime(ws: WebSocket):
        await ws.accept()
        connected_ws.add(ws)
        try:
            # Send initial status
            orch = _get_orchestrator()
            if orch:
                await ws.send_json({
                    "type": "status",
                    "state": orch.controller.state.value,
                    "is_running": orch.controller.is_running,
                })

            # Listen for messages (ping/pong keepalive)
            while True:
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_text("pong")

        except WebSocketDisconnect:
            logger.info("대시보드 WebSocket 연결 종료")
        except Exception as e:
            logger.error(f"WebSocket 오류: {e}")
        finally:
            connected_ws.discard(ws)

    return app
