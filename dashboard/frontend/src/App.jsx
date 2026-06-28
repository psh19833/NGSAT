import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from './api.js'
import {
  formatNumber, formatPercent, formatWon,
  pnlColor, regimeColor, regimeLabel,
  stateColor, stateLabel, formatDateTime,
} from './utils.js'
import Sidebar from './components/Sidebar.jsx'
import StatusCard from './components/StatusCard.jsx'
import AccountCard from './components/AccountCard.jsx'
import RegimeCard from './components/RegimeCard.jsx'
import PositionsTable from './components/PositionsTable.jsx'
import ControlPanel from './components/ControlPanel.jsx'
import TradesTable from './components/TradesTable.jsx'
import DiagnosisPanel from './components/DiagnosisPanel.jsx'
import StrategyConfigPanel from './components/StrategyConfigPanel.jsx'
import Toast from './components/Toast.jsx'

export default function App() {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [positions, setPositions] = useState(null)
  const [regime, setRegime] = useState(null)
  const [trades, setTrades] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')
  const [toast, setToast] = useState(null)

  const showToast = (message, type = 'info') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3000)
  }

  const refreshAll = useCallback(async () => {
    // Use allSettled so one failure doesn't block all; keep stale data on error
    const results = await Promise.allSettled([
      api.getStatus(), api.getAccount(), api.getPositions(),
      api.getRegime(), api.getTrades(),
    ])
    // Only update state on success — stale data persists on failure
    if (results[0].status === 'fulfilled') setStatus(results[0].value)
    if (results[1].status === 'fulfilled') setAccount(results[1].value)
    if (results[2].status === 'fulfilled') setPositions(results[2].value)
    if (results[3].status === 'fulfilled') setRegime(results[3].value)
    if (results[4].status === 'fulfilled') setTrades(results[4].value)
  }, [])

  useEffect(() => {
    refreshAll()
    const interval = setInterval(refreshAll, 5000) // 5초마다 갱신
    return () => clearInterval(interval)
  }, [refreshAll])

  // WebSocket real-time updates
  const wsRef = useRef(null)
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/realtime`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      console.debug('WebSocket 연결됨')
    }
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'cycle' || data.type === 'status') {
          refreshAll()
        }
      } catch {
        // ignore non-JSON messages (e.g. "pong")
      }
    }
    ws.onclose = () => {
      console.debug('WebSocket 연결 종료')
      wsRef.current = null
    }

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [refreshAll])

  const handleControl = async (action, code) => {
    if (action === 'start') await api.start()
    else if (action === 'stop') await api.stop()
    else if (action === 'shutdown') await api.shutdown()
    else if (action === 'forcesell') await api.forceSell(code)
    else if (action === 'forcehold') await api.forceHold(code)
    refreshAll()
  }

  const handleRestart = async () => {
    showToast('서버 재시작 중...', 'info')
    await api.restart()
    refreshAll()
    showToast('서버 재시작 완료', 'success')
  }

  const connected = status?.connected !== false

  return (
    <div className="flex h-screen bg-ngsat-bg">
      {/* Sidebar */}
      <Sidebar
        onTabChange={setActiveTab}
        onRestart={handleRestart}
        status={status}
      />

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        {/* Header */}
        <header className="flex items-center justify-between px-8 py-5 border-b border-ngsat-border">
          <div>
            <h1 className="text-xl font-semibold text-ngsat-text">
              {activeTab === 'overview' && '운영 요약'}
              {activeTab === 'account' && '계좌 현황'}
              {activeTab === 'positions' && '보유 포지션'}
              {activeTab === 'trades' && '거래 내역'}
              {activeTab === 'control' && '운영 제어'}
              {activeTab === 'diagnosis' && '진단 현황'}
              {activeTab === 'strategy' && '전략 설정'}
            </h1>
            <p className="text-sm text-ngsat-muted mt-0.5">NGSAT Dashboard</p>
          </div>
          <div className="flex items-center gap-3">
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-ngsat-green' : 'bg-ngsat-red'} animate-pulse`} />
            <span className="text-sm text-ngsat-muted">
              {connected ? '연결됨' : '미연결'}
            </span>
            {status?.server_time && (
              <span className="text-sm text-ngsat-muted font-mono tabular-nums">
                {formatDateTime(status.server_time)}
              </span>
            )}
            <button
              onClick={refreshAll}
              className="px-3 py-1.5 text-sm text-ngsat-muted hover:text-ngsat-text border border-ngsat-border rounded-lg hover:border-ngsat-accent/30 transition-all"
            >
              ↻ 새로고침
            </button>
          </div>
        </header>

        {/* Content */}
        <main className="p-8">
          {!connected && (
            <div className="ngsat-card p-8 text-center">
              <p className="text-ngsat-red text-lg font-medium">거래 시스템에 연결되지 않았습니다</p>
              <p className="text-ngsat-muted text-sm mt-2">백엔드 서버가 실행 중인지 확인해 주세요</p>
            </div>
          )}

          {connected && activeTab === 'overview' && (
            <div className="space-y-6">
              {/* Top Row: Status + Regime */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <StatusCard status={status} />
                <RegimeCard regime={regime} />
                <div className="ngsat-card p-6">
                  <h3 className="text-sm text-ngsat-muted mb-4">운영 제어</h3>
                  <ControlPanel
                    status={status}
                    onAction={handleControl}
                    compact
                  />
                </div>
              </div>

              {/* Middle Row: Account */}
              <AccountCard account={account} />

              {/* Bottom: Positions */}
              <PositionsTable positions={positions} onAction={handleControl} />
            </div>
          )}

          {connected && activeTab === 'account' && (
            <AccountCard account={account} detailed />
          )}

          {connected && activeTab === 'positions' && (
            <PositionsTable positions={positions} onAction={handleControl} detailed />
          )}

          {connected && activeTab === 'trades' && (
            <TradesTable trades={trades} />
          )}

          {connected && activeTab === 'diagnosis' && (
            <DiagnosisPanel api={api} />
          )}

          {connected && activeTab === 'strategy' && (
            <StrategyConfigPanel api={api} />
          )}

          {connected && activeTab === 'control' && (
            <div className="max-w-2xl space-y-6">
              <div className="ngsat-card p-6">
                <h3 className="text-sm text-ngsat-muted mb-4">운영 제어</h3>
                <ControlPanel status={status} onAction={handleControl} />
              </div>
            </div>
          )}
        </main>
        <Toast toast={toast} onClose={() => setToast(null)} />
      </div>
    </div>
  </div>
)
