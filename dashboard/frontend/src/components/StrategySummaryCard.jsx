import React from 'react'

const PRESET_ICONS = {
  안정형: '🛡️',
  균형형: '⚖️',
  공격형: '🚀',
  단타형: '📈',
  스윙형: '🐢',
  'AI 집중형': '🔬',
  분산형: '📊',
  수익형: '💰',
  '사용자 정의': '✏️',
}

function detectPreset(config) {
  if (!config) return { name: '균형형', icon: '⚖️' }
  // Try to match from the config presets (loaded via PresetButtons)
  // Fall back to '사용자 정의' if no match
  return { name: config.active_preset || '사용자 정의', icon: '✏️' }
}

export default function StrategySummaryCard({ config, regime }) {
  if (!config) return null

  const preset = detectPreset(config)
  const mode = regime?.mode || '—'
  const modeLabel = { swing: '스윙', short_term: '단타', hold: '홀드' }[mode] || mode

  return (
    <div className="ngsat-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm text-ngsat-muted">📋 전략 설정 요약</h3>
        <span className="text-xs px-2 py-0.5 rounded bg-ngsat-accent/10 text-ngsat-accent font-medium">
          {preset.icon} {preset.name}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Left: Trading */}
        <div>
          <p className="text-xs text-ngsat-muted mb-2 font-medium">매매 판단</p>
          <div className="space-y-1.5">
            <Row label="모드" value={modeLabel} />
            <Row label="매수문턱" value={`${(config.buy_threshold * 100).toFixed(0)}%`} />
            <Row label="매도문턱" value={`${(config.sell_threshold * 100).toFixed(0)}%`} />
          </div>
        </div>

        {/* Right: Risk */}
        <div>
          <p className="text-xs text-ngsat-muted mb-2 font-medium">리스크 관리</p>
          <div className="space-y-1.5">
            <Row label="손절선" value={`${config.mode_short_stop_loss_pct || config.mode_swing_stop_loss_pct}%`} />
            <Row label="일일한도" value={`${config.mode_short_daily_loss_pct || config.mode_swing_daily_loss_pct}%`} />
            <Row label="포지션크기" value={`${((config.mode_short_position_size || config.mode_swing_position_size) * 100).toFixed(0)}%`} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-ngsat-border">
        {/* Left: Market */}
        <div>
          <p className="text-xs text-ngsat-muted mb-2 font-medium">시장 분석</p>
          <div className="space-y-1.5">
            <Row label="강세 기준" value={`${config.regime_bull_threshold}점`} />
            <Row label="약세 기준" value={`${config.regime_bear_threshold}점`} />
            <Row label="스크리너" value={`${config.screener_neutral_min_score}점 / ${config.screener_neutral_max_candidates}개`} />
          </div>
        </div>

        {/* Right: Portfolio */}
        <div>
          <p className="text-xs text-ngsat-muted mb-2 font-medium">포트폴리오</p>
          <div className="space-y-1.5">
            <Row label="최대 보유" value={`${config.max_holdings}개`} />
            <Row label="최대 노출" value={`${config.max_total_exposure_pct}%`} />
          </div>
        </div>
      </div>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-ngsat-muted">{label}</span>
      <span className="text-ngsat-text font-medium tabular-nums">{value}</span>
    </div>
  )
}
