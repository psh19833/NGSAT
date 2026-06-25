import { useState, useEffect } from 'react'

// ── Field definitions grouped by section ──
const SECTIONS = [
  {
    title: '매매 결정 임계',
    fields: [
      { key: 'buy_threshold', label: '매수 확률', min: 0.3, max: 0.95, step: 0.01, hint: 'ML 예측이 이 값 이상이면 매수' },
      { key: 'sell_threshold', label: '매도 확률', min: 0.05, max: 0.7, step: 0.01, hint: 'ML 예측이 이 값 이하면 매도' },
    ],
  },
  {
    title: '리스크 — 스윙 모드',
    fields: [
      { key: 'mode_swing_stop_loss_pct', label: '종목 손절선', unit: '%', min: 1, max: 10, step: 0.5 },
      { key: 'mode_swing_daily_loss_pct', label: '일일 손실 한도', unit: '%', min: 1, max: 15, step: 0.5 },
      { key: 'mode_swing_position_size', label: '1회 투자 비중', unit: '', min: 0.01, max: 0.5, step: 0.01, fmt: v => (v * 100).toFixed(0) + '%' },
    ],
  },
  {
    title: '리스크 — 단타 모드',
    fields: [
      { key: 'mode_short_stop_loss_pct', label: '종목 손절선', unit: '%', min: 0.5, max: 5, step: 0.5 },
      { key: 'mode_short_daily_loss_pct', label: '일일 손실 한도', unit: '%', min: 0.5, max: 10, step: 0.5 },
      { key: 'mode_short_position_size', label: '1회 투자 비중', unit: '', min: 0.01, max: 0.3, step: 0.01, fmt: v => (v * 100).toFixed(0) + '%' },
    ],
  },
  {
    title: '리스크 — 홀드 모드',
    fields: [
      { key: 'mode_hold_stop_loss_pct', label: '종목 손절선', unit: '%', min: 1, max: 10, step: 0.5 },
      { key: 'mode_hold_daily_loss_pct', label: '일일 손실 한도', unit: '%', min: 1, max: 15, step: 0.5 },
      { key: 'mode_hold_position_size', label: '1회 투자 비중', unit: '', min: 0, max: 0.1, step: 0.01, fmt: v => (v * 100).toFixed(0) + '%' },
    ],
  },
  {
    title: '시장 레짐 판정',
    fields: [
      { key: 'regime_bull_threshold', label: '강세장 기준', unit: '점', min: 50, max: 90, step: 1 },
      { key: 'regime_bear_threshold', label: '약세장 기준', unit: '점', min: 10, max: 50, step: 1 },
      { key: 'regime_weight_ma', label: 'MA 정렬 가중치', unit: '', min: 5, max: 60, step: 5 },
      { key: 'regime_weight_rsi', label: 'RSI 가중치', unit: '', min: 5, max: 40, step: 5 },
      { key: 'regime_weight_bollinger', label: '볼린저 가중치', unit: '', min: 5, max: 40, step: 5 },
      { key: 'regime_weight_change_rate', label: '등락률 가중치', unit: '', min: 5, max: 30, step: 5 },
      { key: 'regime_weight_volume', label: '거래량 가중치', unit: '', min: 5, max: 30, step: 5 },
    ],
  },
  {
    title: '종목 스크리너',
    fields: [
      { key: 'screener_bull_min_score', label: '강세장 최소점수', unit: '점', min: 30, max: 90, step: 5 },
      { key: 'screener_bull_max_candidates', label: '강세장 최대종목', unit: '개', min: 1, max: 50, step: 1 },
      { key: 'screener_neutral_min_score', label: '중립장 최소점수', unit: '점', min: 30, max: 90, step: 5 },
      { key: 'screener_neutral_max_candidates', label: '중립장 최대종목', unit: '개', min: 1, max: 30, step: 1 },
      { key: 'screener_bear_min_score', label: '약세장 최소점수', unit: '점', min: 30, max: 95, step: 5 },
      { key: 'screener_bear_max_candidates', label: '약세장 최대종목', unit: '개', min: 1, max: 15, step: 1 },
    ],
  },
  {
    title: '모드 전환',
    fields: [
      { key: 'mode_high_volatility_atr_pct', label: '고변동성 기준(ATR)', unit: '%', min: 0.5, max: 5, step: 0.5, hint: 'ATR이 이 값 이상이면 단타 모드로' },
      { key: 'mode_low_volatility_atr_pct', label: '저변동성 기준(ATR)', unit: '%', min: 0.2, max: 2, step: 0.1, hint: 'ATR이 이 값 이하면 스윙 유지' },
    ],
  },
]

function FieldRow({ field, value, onChange }) {
  const displayValue = field.fmt ? field.fmt(value) : `${value}${field.unit || ''}`

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-1">
        <label className="text-xs text-ngsat-muted">{field.label}</label>
        <span className="text-xs font-mono text-ngsat-text tabular-nums">{displayValue}</span>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value ?? field.min}
          onChange={e => onChange(field.key, parseFloat(e.target.value))}
          className="flex-1 h-1.5 accent-ngsat-accent"
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value ?? field.min}
          onChange={e => onChange(field.key, parseFloat(e.target.value) || field.min)}
          className="w-16 px-2 py-1 text-xs text-right font-mono bg-ngsat-bg border border-ngsat-border rounded 
            text-ngsat-text focus:outline-none focus:border-ngsat-accent/50 tabular-nums"
        />
      </div>
      {field.hint && <p className="text-xs text-ngsat-muted/60 mt-0.5">{field.hint}</p>}
    </div>
  )
}

export default function StrategyConfigPanel({ api }) {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    setLoading(true)
    const resp = await api.getStrategyConfig()
    if (resp?.config) {
      setConfig(resp.config)
    }
    setLoading(false)
  }

  const handleChange = (key, value) => {
    setConfig(prev => prev ? { ...prev, [key]: value } : prev)
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage('')
    const resp = await api.updateStrategyConfig(config)
    if (resp?.connected) {
      setMessage(resp.message || '저장 완료')
      if (resp.restart_required) {
        setMessage(m => m + ' — 서버 재시작 중...')
        setTimeout(async () => {
          await api.restart()
          setMessage(m => m + ' 완료')
        }, 500)
      }
    } else {
      setMessage('저장 실패 — 서버 연결 확인')
    }
    setSaving(false)
  }

  const handleReset = async () => {
    if (!confirm('모든 전략 설정을 기본값으로 복원하시겠습니까?')) return
    setSaving(true)
    setMessage('')
    const resp = await api.updateStrategyConfig({ reset: true })
    if (resp?.config) {
      setConfig(resp.config)
      setMessage(resp.message || '기본값 복원 완료')
      if (resp.restart_required) {
        setTimeout(async () => {
          await api.restart()
          setMessage(m => m + ' — 서버 재시작 완료')
        }, 500)
      }
    }
    setSaving(false)
  }

  if (loading) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-4">전략 설정</h3>
        <p className="text-ngsat-muted text-sm">불러오는 중...</p>
      </div>
    )
  }

  if (!config) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-4">전략 설정</h3>
        <p className="text-ngsat-red text-sm">설정을 불러올 수 없습니다</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-ngsat-text">전략 설정</h3>
        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            disabled={saving}
            className="px-3 py-1.5 text-xs text-ngsat-muted border border-ngsat-border rounded-lg 
              hover:text-ngsat-red hover:border-ngsat-red/30 transition-all disabled:opacity-50"
          >
            기본값 복원
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 text-sm text-white bg-ngsat-accent rounded-lg 
              hover:bg-ngsat-accent/80 transition-all disabled:opacity-50"
          >
            {saving ? '저장 중...' : '설정 저장'}
          </button>
        </div>
      </div>

      {message && (
        <div className={`text-sm px-4 py-2 rounded-lg ${
          message.includes('실패') ? 'bg-ngsat-red/10 text-ngsat-red' : 'bg-ngsat-green/10 text-ngsat-green'
        }`}>
          {message}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {SECTIONS.map(section => (
          <div key={section.title} className="ngsat-card p-5">
            <h4 className="text-sm font-medium text-ngsat-text mb-4 pb-2 border-b border-ngsat-border">
              {section.title}
            </h4>
            {section.fields.map(field => (
              <FieldRow
                key={field.key}
                field={field}
                value={config[field.key]}
                onChange={handleChange}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
