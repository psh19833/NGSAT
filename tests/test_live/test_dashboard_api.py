"""Tests for NGSAT dashboard API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.api import create_app


class MockOrchestrator:
    """Mock orchestrator for dashboard API tests."""
    
    def __init__(self):
        from core.types import AccountSummary
        from live.controller import TradingController
        from live.risk import RiskManager
        from core.config import RiskConfig
        
        self.controller = TradingController()
        self.risk_manager = RiskManager(RiskConfig())
        self._cycle_count = 5
        self._current_mode = "swing"
        self._last_regime = None
        
        from unittest.mock import AsyncMock
        self._broker = AsyncMock()
        self._broker.get_account_summary = AsyncMock(return_value=AccountSummary(
            total_asset=10_000_000, deposit=5_000_000,
            total_eval=5_000_000, total_profit_loss=100000,
            total_profit_loss_pct=1.0,
        ))
        self._broker.get_positions = AsyncMock(return_value=[])
    
    async def force_sell(self, code):
        from live.executor import ExecutionResult
        return ExecutionResult(
            success=True, code=code, name="삼성전자",
            quantity=10, price=70000, amount=700000,
            action="force_sell", reason="test",
        )


@pytest.fixture
def app():
    return create_app(MockOrchestrator())


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def empty_app():
    return create_app(None)


@pytest.fixture
def empty_client(empty_app):
    return TestClient(empty_app)


class TestDashboardAPI:
    """Dashboard API endpoint tests."""

    def test_health_check(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_status(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert "state" in data
        assert "is_running" in data

    def test_account(self, client):
        resp = client.get("/api/account")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["total_asset"] == 10_000_000

    def test_positions(self, client):
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert "positions" in data

    def test_regime_no_data(self, client):
        resp = client.get("/api/regime")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["regime"] == "unknown"

    def test_control_start(self, client):
        resp = client.post("/api/control/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert "시작" in data["message"]

    def test_control_stop(self, client):
        # Start first, then stop
        client.post("/api/control/start")
        resp = client.post("/api/control/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert "정지" in data["message"]

    def test_control_shutdown(self, client):
        resp = client.post("/api/control/shutdown")
        assert resp.status_code == 200
        data = resp.json()
        assert "종료" in data["message"]

    def test_control_force_sell(self, client):
        resp = client.post("/api/control/forcesell", json={"code": "005930"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True

    def test_control_force_hold(self, client):
        resp = client.post("/api/control/forcehold", json={"code": "005930"})
        assert resp.status_code == 200
        data = resp.json()
        assert "홀드" in data["message"]

    def test_not_connected_status(self, empty_client):
        resp = empty_client.get("/api/status")
        data = resp.json()
        assert data["connected"] is False

    def test_not_connected_account(self, empty_client):
        resp = empty_client.get("/api/account")
        data = resp.json()
        assert data["connected"] is False

    def test_trades_endpoint(self, client):
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
