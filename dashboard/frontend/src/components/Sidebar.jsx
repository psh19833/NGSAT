import { stateColor, stateLabel } from '../utils.js'
import {
  LayoutDashboard,
  Wallet,
  BarChart3,
  ListOrdered,
  Radio,
  Search,
  SlidersHorizontal,
  RotateCcw,
} from 'lucide-react'

const TABS = [
  { id: 'overview', label: '운영 요약', icon: LayoutDashboard },
  { id: 'account', label: '계좌', icon: Wallet },
  { id: 'positions', label: '포지션', icon: BarChart3 },
  { id: 'trades', label: '거래 내역', icon: ListOrdered },
  { id: 'control', label: '운영 제어', icon: Radio },
  { id: 'diagnosis', label: '진단 현황', icon: Search },
  { id: 'strategy', label: '전략 설정', icon: SlidersHorizontal },
]

export default function Sidebar({ activeTab, onTabChange, onRestart, status }) {
  const state = status?.state || 'idle'
  const isRunning = status?.is_running || false
  const serverConnected = status?.connected !== false

  return (
    <aside className="w-60 bg-ngsat-card border-r border-ngsat-border flex flex-col">
      {/* Logo */}
      <div className="px-6 py-5 border-b border-ngsat-border">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-ngsat-accent to-ngsat-purple flex items-center justify-center">
            <span className="text-white font-bold text-sm">N</span>
          </div>
          <div>
            <h2 className="text-sm font-semibold text-ngsat-text">NGSAT</h2>
            <p className="text-xs text-ngsat-muted">Stock Auto Trader</p>
          </div>
        </div>
      </div>

      {/* Server Control */}
      <div className="px-5 py-3 border-b border-ngsat-border">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${serverConnected ? 'bg-ngsat-green' : 'bg-ngsat-red'}`} />
            <span className="text-xs text-ngsat-muted">서버</span>
          </div>
          <span className={`text-xs font-medium ${serverConnected ? 'text-ngsat-green' : 'text-ngsat-red'}`}>
            {serverConnected ? '연결됨' : '미연결'}
          </span>
        </div>
        <button
          onClick={onRestart}
          disabled={!serverConnected}
          className="w-full py-1.5 rounded text-xs font-medium transition-all
            bg-ngsat-border/50 text-ngsat-muted hover:bg-ngsat-border hover:text-ngsat-text
            disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <RotateCcw className="inline-block w-3 h-3 mr-1.5 -mt-0.5" />
          서버 재시작
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4">
        {TABS.map(tab => {
          const Icon = tab.icon
          return (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`
                w-full flex items-center gap-3 px-6 py-3 text-sm transition-all
                ${activeTab === tab.id
                  ? 'text-ngsat-text bg-ngsat-accent/10 border-r-2 border-ngsat-accent'
                  : 'text-ngsat-muted hover:text-ngsat-text hover:bg-ngsat-border/30'
                }
              `}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          )
        })}
      </nav>

      {/* Status Indicator */}
      <div className="px-6 py-4 border-t border-ngsat-border">
        <div className="flex items-center gap-2 mb-1">
          <div className={`w-2 h-2 rounded-full ${isRunning ? 'bg-ngsat-green animate-pulse' : 'bg-ngsat-muted'}`} />
          <span className={`text-sm font-medium ${stateColor(state)}`}>
            {stateLabel(state)}
          </span>
        </div>
        {status?.risk_halted && (
          <p className="text-xs text-ngsat-red mt-1">⚠ 리스크 자동중단</p>
        )}
        <p className="text-xs text-ngsat-muted mt-1">
          사이클 #{status?.cycle_count || 0}
        </p>
      </div>
    </aside>
  )
}
