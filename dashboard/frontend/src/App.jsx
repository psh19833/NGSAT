import { useState, useEffect, useCallback } from 'react'
import { Menu } from 'lucide-react'
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
import IndicesCard from './components/IndicesCard.jsx'
import PositionsTable from './components/PositionsTable.jsx'
import ControlPanel from './components/ControlPanel.jsx'
import TradesTable from './components/TradesTable.jsx'
import DiagnosisPanel from './components/DiagnosisPanel.jsx'
import StrategyConfigPanel from './components/StrategyConfigPanel.jsx'
import Toast from './components/Toast.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import ConfirmModal from './components/ConfirmModal.jsx'
import BacktestPanel from './components/BacktestPanel.jsx'
import StrategySummaryCard from './components/StrategySummaryCard.jsx'

export default function App() {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [positions, setPositions] = useState(null)
  const [regime, setRegime] = useState(null)
  const [trades, setTrades] = useState(null)
  const [strategyConfig, setStrategyConfig] = useState(null)
  const [indices, setIndices] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [activeTab, setActiveTab] = useState('overview')
  const [toast, setToast] = useState(null)
  const [confirmAction, setConfirmAction] = useState(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [strategyDirty, setStrategyDirty] = useState(false)

  const showToast = (message, type = 'info') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3000)
  }

  const refreshForTab = useCallback(async (tab) => {
    const tabApiMap = {
      overview: [api.getStatus, api.getAccount, api.getPositions, api.getRegime, api.getTrades, api.getStrategyConfig, api.getIndices],
      account: [api.getStatus, api.getAccount],
      positions: [api.getStatus, api.getPositions],
      trades: [api.getStatus, api.getTrades],
      control: [api.getStatus],
      diagnosis: [api.getStatus],
      strategy: [api.getStatus, api.getStrategyConfig],
      backtest: [api.getStatus],
    }
    const apis = tabApiMap[tab] || tabApiMap.overview
    setRefreshing(true)
    const results = await Promise.allSettled(apis.map(fn => fn()))
    let idx = 0
    if (apis.includes(api.getStatus) && results[idx]?.status === 'fulfilled') setStatus(results[idx++].value); else if (apis.includes(api.getStatus)) idx++
    if (apis.includes(api.getAccount) && results[idx]?.status === 'fulfilled') setAccount(results[idx++].value); else if (apis.includes(api.getAccount)) idx++
    if (apis.includes(api.getPositions) && results[idx]?.status === 'fulfilled') setPositions(results[idx++].value); else if (apis.includes(api.getPositions)) idx++
    if (apis.includes(api.getRegime) && results[idx]?.status === 'fulfilled') setRegime(results[idx++].value); else if (apis.includes(api.getRegime)) idx++
    if (apis.includes(api.getTrades) && results[idx]?.status === 'fulfilled') setTrades(results[idx++].value); else if (apis.includes(api.getTrades)) idx++
    if (apis.includes(api.getStrategyConfig) && results[idx]?.status === 'fulfilled') {
      const d = results[idx++].value
      setStrategyConfig(d?.config ? { ...d.config, active_preset: d.active_preset } : null)
    } else if (apis.includes(api.getStrategyConfig)) idx++
    if (apis.includes(api.getIndices) && results[idx]?.status === 'fulfilled') {
      const d = results[idx++].value
      if (d?.indices) setIndices(d.indices)
    }
    setRefreshing(false)
  }, [])

  const refreshAll = useCallback(async () => {
    return refreshForTab('overview')
  }, [refreshForTab])

  useEffect(() => {
    refreshAll()
    const interval = setInterval(() => refreshForTab(activeTab), 5000) // 탭별 필요한 API만 5초마다 갱신
    return () => clearInterval(interval)
  }, [refreshAll, refreshForTab, activeTab])

  const handleControl = async (action, code) => {
    // Confirm for dangerous actions
    if (action === 'shutdown' || action === 'forcesell') {
      setConfirmAction({ action, code })
      return
    }
    try {
      if (action === 'start') await api.start()
      else if (action === 'stop') await api.stop()
      else if (action === 'forcehold') await api.forceHold(code)
      await refreshAll()
    } catch (err) {
      const actionLabels = { start: '매매 시작', stop: '매매 중단', forcehold: '강제 홀드' }
      showToast(`${actionLabels[action] || action} 실패: ${err.message}`, 'error')
    }
  }

  const handleTabChange = (tabId) => {
    if (activeTab === 'strategy' && strategyDirty && tabId !== 'strategy') {
      if (!window.confirm('저장하지 않은 변경사항이 있습니다. 이동하시겠습니까?')) {
        return
      }
      setStrategyDirty(false)
    }
    setActiveTab(tabId)
    setMobileSidebarOpen(false)
    // 탭 전환 시 해당 탭 데이터만 갱신 (전체 7개 API 대신 선택적 호출)
    refreshForTab(tabId)
  }

  const handleRestart = async () => {
    showToast('서버 재시작 중...', 'info')
    await api.restart()
    // Poll until server is back (timeout 30s)
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 1000))
      try {
        const s = await api.getStatus()
        if (s?.state) {
          showToast('서버 재시작 완료', 'success')
          return refreshAll()
        }
      } catch {}
    }
    showToast('서버 재시작 응답 없음', 'error')
  }

  const handleConfirm = async () => {
    if (!confirmAction) return
    const { action, code } = confirmAction
    setConfirmAction(null)
    if (action === 'shutdown') await api.shutdown()
    else if (action === 'forcesell') await api.forceSell(code)
    await refreshAll()
  }

  const connected = status?.connected !== false

  return (
    <ErrorBoundary>
    <div className="flex h-screen bg-ngsat-bg">
      {/* Sidebar */}
      <Sidebar
        onTabChange={handleTabChange}
        onRestart={handleRestart}
        status={status}
        mobileOpen={mobileSidebarOpen}
        onToggleMobile={() => setMobileSidebarOpen(v => !v)}
      />

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        {/* Header */}
        <header className="flex items-center justify-between px-4 md:px-8 py-5 border-b border-ngsat-border">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileSidebarOpen(v => !v)}
              className="md:hidden text-ngsat-muted hover:text-ngsat-text"
              aria-label="메뉴 열기"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-xl font-semibold text-ngsat-text">
              {activeTab === 'overview' && '운영 요약'}
              {activeTab === 'backtest' && '백테스트'}
              {activeTab === 'account' && '계좌 현황'}
              {activeTab === 'positions' && '보유 포지션'}
              {activeTab === 'trades' && '거래 내역'}
              {activeTab === 'control' && '운영 제어'}
              {activeTab === 'diagnosis' && '진단 현황'}
              {activeTab === 'strategy' && '전략 설정'}
            </h1>
            <p className="text-sm text-ngsat-muted mt-0.5">NGSAT Dashboard</p>
            </div>
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
            {/* Indices mini strip - always visible */}
            {indices && Object.keys(indices).length > 0 && (
              <div className="flex items-center gap-2 text-[11px] font-mono">
                {indices.kospi && (
                  <span className={indices.kospi.change_pct >= 0 ? 'text-ngsat-green' : 'text-ngsat-red'}>
                    KOSPI {indices.kospi.change_pct >= 0 ? '+' : ''}{indices.kospi.change_pct?.toFixed(1)}%
                  </span>
                )}
                {indices.sp500 && (
                  <span className={indices.sp500.change_pct >= 0 ? 'text-ngsat-green' : 'text-ngsat-red'}>
                    S&P {indices.sp500.change_pct >= 0 ? '+' : ''}{indices.sp500.change_pct?.toFixed(1)}%
                  </span>
                )}
                {indices.nasdaq && (
                  <span className={indices.nasdaq.change_pct >= 0 ? 'text-ngsat-green' : 'text-ngsat-red'}>
                    NASDAQ {indices.nasdaq.change_pct >= 0 ? '+' : ''}{indices.nasdaq.change_pct?.toFixed(1)}%
                  </span>
                )}
              </div>
            )}
            <button
              onClick={refreshAll}
              disabled={refreshing}
              className={`px-3 py-1.5 text-sm transition-all rounded-lg border ${
                refreshing
                  ? 'text-ngsat-muted/50 border-ngsat-border/50 cursor-wait'
                  : 'text-ngsat-muted hover:text-ngsat-text border-ngsat-border hover:border-ngsat-accent/30'
              }`}
            >
              {refreshing ? '⟳' : '↻'} 새로고침
            </button>
          </div>
        </header>

        {!connected && (
          <div className="sticky top-0 z-40 px-4 md:px-8 py-2 bg-red-700/90 border-b border-red-500/30 backdrop-blur-sm">
            <p className="text-xs text-white text-center font-medium">⚠ 연결 끊김 — 백엔드 서버가 응답하지 않습니다</p>
          </div>
        )}

        {strategyDirty && activeTab === 'strategy' && (
          <div className="px-4 md:px-8 py-2 bg-yellow-900/30 border-b border-yellow-700/30">
            <p className="text-xs text-yellow-400">● 변경사항이 저장되지 않았습니다</p>
          </div>
        )}

        {/* Content */}
        <main className="p-4 md:p-8">
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

              {/* Indices */}
              {indices && <IndicesCard indices={indices} />}

              {/* Strategy Summary */}
              <StrategySummaryCard config={strategyConfig} regime={regime} />

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
            <StrategyConfigPanel api={api} onDirtyChange={setStrategyDirty} />
          )}

          {connected && activeTab === 'backtest' && (
            <BacktestPanel api={api} />
          )}

          {connected && activeTab === 'control' && (
            <div className="max-w-2xl space-y-6">
              <div className="ngsat-card p-6">
                <h3 className="text-sm text-ngsat-muted mb-4">운영 제어</h3>
                <ControlPanel status={status} onAction={handleControl} onRestart={handleRestart} />
              </div>
            </div>
          )}
        </main>
        <Toast toast={toast} onClose={() => setToast(null)} />
    <ConfirmModal
      open={confirmAction !== null}
      title={confirmAction?.action === 'shutdown' ? '시스템 종료' : '강제 매도'}
      message={confirmAction?.action === 'shutdown'
        ? '진행 중인 매매가 모두 중단됩니다. 포지션이 정리되지 않은 상태로 종료됩니다.'
        : '해당 종목을 시장가로 즉시 매도합니다.'}
      confirmLabel={confirmAction?.action === 'shutdown' ? '종료' : '매도'}
      onConfirm={handleConfirm}
      onCancel={() => setConfirmAction(null)}
    />
      </div>
    </div>
    </ErrorBoundary>
)
}
