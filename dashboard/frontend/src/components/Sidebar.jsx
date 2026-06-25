import { stateColor, stateLabel } from '../utils.js'

const TABS = [
  { id: 'overview', label: '운영 요약', icon: '◈' },
  { id: 'account', label: '계좌', icon: '₩' },
  { id: 'positions', label: '포지션', icon: '⊞' },
  { id: 'trades', label: '거래 내역', icon: '≡' },
  { id: 'control', label: '운영 제어', icon: '⊙' },
]

export default function Sidebar({ activeTab, onTabChange, status }) {
  const state = status?.state || 'idle'
  const isRunning = status?.is_running || false

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

      {/* Navigation */}
      <nav className="flex-1 py-4">
        {TABS.map(tab => (
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
            <span className="text-base">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
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
