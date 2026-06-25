import { stateColor, stateLabel } from '../utils.js'

export default function StatusCard({ status }) {
  if (!status || status.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">시스템 상태</h3>
        <p className="text-ngsat-red">미연결</p>
      </div>
    )
  }

  const state = status.state || 'idle'

  return (
    <div className="ngsat-card p-6">
      <h3 className="text-sm text-ngsat-muted mb-3">시스템 상태</h3>
      <div className="flex items-center gap-3 mb-4">
        <div className={`w-3 h-3 rounded-full ${
          state === 'running' ? 'bg-ngsat-green animate-pulse' :
          state === 'halted' ? 'bg-ngsat-red' :
          state === 'paused' ? 'bg-ngsat-yellow' :
          'bg-ngsat-muted'
        }`} />
        <span className={`text-2xl font-bold ${stateColor(state)}`}>
          {stateLabel(state)}
        </span>
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-ngsat-muted">사이클</span>
          <span className="num text-ngsat-text">#{status.cycle_count || 0}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-ngsat-muted">리스크 중단</span>
          <span className={status.risk_halted ? 'text-ngsat-red' : 'text-ngsat-green'}>
            {status.risk_halted ? '활성' : '정상'}
          </span>
        </div>
        {status.risk_halted && status.risk_reason && (
          <p className="text-xs text-ngsat-red mt-2 leading-relaxed">
            {status.risk_reason}
          </p>
        )}
      </div>
    </div>
  )
}
