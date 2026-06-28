const COLOR_MAP = {
  'ngsat-green': 'bg-ngsat-green/10 text-ngsat-green hover:bg-ngsat-green/20 border border-ngsat-green/20',
  'ngsat-yellow': 'bg-ngsat-yellow/10 text-ngsat-yellow hover:bg-ngsat-yellow/20 border border-ngsat-yellow/20',
  'ngsat-red': 'bg-ngsat-red/10 text-ngsat-red hover:bg-ngsat-red/20 border border-ngsat-red/20',
}

export default function ControlPanel({ status, onAction, compact = false }) {
  const state = status?.state || 'idle'
  const isRunning = state === 'running'
  const isShutdown = state === 'shutdown'

  const buttons = [
    {
      action: 'start',
      label: '매매 시작',
      color: 'ngsat-green',
      disabled: isRunning || isShutdown,
    },
    {
      action: 'stop',
      label: '일시정지',
      color: 'ngsat-yellow',
      disabled: !isRunning,
    },
    {
      action: 'shutdown',
      label: '종료',
      color: 'ngsat-red',
      disabled: isShutdown,
    },
  ]

  return (
    <div className={compact ? 'space-y-2' : 'space-y-3'}>
      {buttons.map(btn => (
        <button
          key={btn.action}
          onClick={() => onAction(btn.action)}
          disabled={btn.disabled}
          className={
            `w-full py-2.5 rounded-lg text-sm font-medium transition-all
            ${btn.disabled
              ? 'bg-ngsat-border text-ngsat-muted cursor-not-allowed'
              : COLOR_MAP[btn.color] || ''
            }`
          }
        >
          {btn.label}
        </button>
      ))}
      {!compact && (
        <div className="pt-4 border-t border-ngsat-border">
          <p className="text-xs text-ngsat-muted mb-2">강제 제어</p>
          <p className="text-xs text-ngsat-muted">포지션 탭에서 종목별 강제매도/홀드 가능</p>
        </div>
      )}
    </div>
  )
}
