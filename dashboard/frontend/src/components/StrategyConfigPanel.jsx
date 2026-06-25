import { useState, useEffect } from 'react'

// ── Section + field definitions ──
const SECTIONS = [
  {
    id: 'entry',
    title: '① 매매 진입·청산 판단',
    desc: 'AI가 "살까? 팔까?"를 결정하는 기준입니다. 숫자가 높을수록 더 확실할 때만 움직입니다.',
    fields: [
      { key: 'buy_threshold', label: '매수 기준', min: 0.3, max: 0.95, step: 0.01,
        hint: '높을수록(예:0.8) 신중하게 삽니다. 매수 횟수는 줄지만 성공률이 올라갑니다.' },
      { key: 'sell_threshold', label: '매도 기준', min: 0.05, max: 0.7, step: 0.01,
        hint: '낮을수록(예:0.3) 손실을 빨리 잘라냅니다. 높으면 더 들고 버팁니다.' },
    ],
  },
  {
    id: 'risk_swing',
    title: '② 스윙 모드 — 며칠 보유하는 매매',
    desc: '강세장에서 추세를 따라 며칠~몇 주 보유할 때 적용됩니다.',
    fields: [
      { key: 'mode_swing_stop_loss_pct', label: '종목별 손절선', unit: '%', min: 1, max: 10, step: 0.5,
        hint: '한 종목이 이만큼 손실 나면 바로 팝니다. 5%로 올리면 손실을 더 감수합니다.' },
      { key: 'mode_swing_daily_loss_pct', label: '하루 손실 한도', unit: '%', min: 1, max: 15, step: 0.5,
        hint: '하루 전체 손실이 이 수치를 넘으면 모든 매매를 멈춥니다. 낮을수록 안전합니다.' },
      { key: 'mode_swing_position_size', label: '한 번에 투자하는 금액', unit: '', min: 0.01, max: 0.5, step: 0.01, fmt: v => '전체 자산의 ' + (v * 100).toFixed(0) + '%',
        hint: '전체 보유 현금 중 한 종목에 넣는 비율입니다. 10%면 10종목까지 분산 가능합니다.' },
    ],
  },
  {
    id: 'risk_short',
    title: '③ 단타 모드 — 당일치기 매매',
    desc: '중립장에서 짧게 먹고 빠질 때 적용됩니다. 스윙보다 기준이 타이트합니다.',
    fields: [
      { key: 'mode_short_stop_loss_pct', label: '종목별 손절선', unit: '%', min: 0.5, max: 5, step: 0.5,
        hint: '단타는 손절을 더 빠르게. 1.5%면 스윙(3%)보다 두 배 빨리 손절합니다.' },
      { key: 'mode_short_daily_loss_pct', label: '하루 손실 한도', unit: '%', min: 0.5, max: 10, step: 0.5,
        hint: '단타 날에는 손실 한도를 더 타이트하게. 3%면 스윙(5%)보다 엄격합니다.' },
      { key: 'mode_short_position_size', label: '한 번에 투자하는 금액', unit: '', min: 0.01, max: 0.3, step: 0.01, fmt: v => '전체 자산의 ' + (v * 100).toFixed(0) + '%',
        hint: '단타는 한 번에 적게 베팅합니다. 5%면 스윙(10%)의 절반입니다.' },
    ],
  },
  {
    id: 'risk_hold',
    title: '④ 홀드 모드 — 신규매수 금지',
    desc: '약세장에서 적용. 새로 사지 않고 기존 보유 종목만 관리합니다.',
    fields: [
      { key: 'mode_hold_stop_loss_pct', label: '종목별 손절선', unit: '%', min: 1, max: 10, step: 0.5,
        hint: '약세장에서 기존 종목 손절 기준. 빠르게 털어낼지 버틸지 결정합니다.' },
      { key: 'mode_hold_daily_loss_pct', label: '하루 손실 한도', unit: '%', min: 1, max: 15, step: 0.5,
        hint: '약세장 하루 손실 한도.' },
      { key: 'mode_hold_position_size', label: '신규 투자 금액', unit: '', min: 0, max: 0.1, step: 0.01, fmt: v => (v * 100).toFixed(0) + '%',
        hint: '0%로 두면 약세장에서 아예 새로 사지 않습니다. (권장)' },
    ],
  },
  {
    id: 'regime',
    title: '⑤ 시장 분위기 판단',
    desc: '현재 장세를 "강세·중립·약세"로 구분하는 기준입니다. 점수 100점 만점에 몇 점 이상이면 강세로 볼지 정합니다.',
    fields: [
      { key: 'regime_bull_threshold', label: '강세장 판정 점수', unit: '점', min: 50, max: 90, step: 1,
        hint: '높을수록(예:75점) 강세장 판정이 깐깐해집니다. 낮추면 더 자주 "강세"로 봅니다.' },
      { key: 'regime_bear_threshold', label: '약세장 판정 점수', unit: '점', min: 10, max: 50, step: 1,
        hint: '낮을수록(예:25점) 약세장 판정이 깐깐해집니다. 올리면 더 자주 "약세"로 봅니다.' },
      { key: 'regime_weight_ma', label: '이동평균선 중요도', unit: '/100', min: 5, max: 60, step: 5,
        hint: '추세 방향(MA 정렬)을 얼마나 중요하게 볼지. 높을수록 추세 추종 전략에 가까워집니다.' },
      { key: 'regime_weight_rsi', label: '과열·침체 중요도', unit: '/100', min: 5, max: 40, step: 5,
        hint: 'RSI(과매수·과매도) 신호를 얼마나 반영할지.' },
      { key: 'regime_weight_bollinger', label: '밴드 위치 중요도', unit: '/100', min: 5, max: 40, step: 5,
        hint: '가격이 볼린저밴드 어디에 있는지 반영 비중.' },
      { key: 'regime_weight_change_rate', label: '단기 등락 중요도', unit: '/100', min: 5, max: 30, step: 5,
        hint: '최근 5일 등락을 얼마나 반영할지.' },
      { key: 'regime_weight_volume', label: '거래량 중요도', unit: '/100', min: 5, max: 30, step: 5,
        hint: '거래량 추세를 얼마나 반영할지.' },
    ],
  },
  {
    id: 'screener',
    title: '⑥ 종목 선별 기준',
    desc: '매수할 종목을 고를 때 얼마나 엄격하게 볼지, 몇 개까지 고를지 정합니다. 장세별로 다른 기준을 적용합니다.',
    fields: [
      { key: 'screener_bull_min_score', label: '강세장 최소 점수', unit: '점', min: 30, max: 90, step: 5,
        hint: '60점이면 "100점 만점에 60점 이상" 종목만 매수 후보. 낮추면 더 많은 종목이 후보에 오릅니다.' },
      { key: 'screener_bull_max_candidates', label: '강세장 최대 후보', unit: '개', min: 1, max: 50, step: 1,
        hint: '강세장에서 한 번에 최대 몇 종목까지 살지. 많을수록 분산되지만 관리 부담이 늡니다.' },
      { key: 'screener_neutral_min_score', label: '중립장 최소 점수', unit: '점', min: 30, max: 90, step: 5,
        hint: '중립장은 더 깐깐하게. 70점이면 강세장(60점)보다 엄격합니다.' },
      { key: 'screener_neutral_max_candidates', label: '중립장 최대 후보', unit: '개', min: 1, max: 30, step: 1,
        hint: '중립장은 후보를 더 적게.' },
      { key: 'screener_bear_min_score', label: '약세장 최소 점수', unit: '점', min: 30, max: 95, step: 5,
        hint: '약세장은 매우 깐깐하게. 80점이면 거의 완벽한 종목만 후보로.' },
      { key: 'screener_bear_max_candidates', label: '약세장 최대 후보', unit: '개', min: 1, max: 15, step: 1,
        hint: '약세장에서는 거의 사지 않음. 5개 이하 권장.' },
    ],
  },
  {
    id: 'mode_switch',
    title: '⑦ 스윙 ↔ 단타 자동 전환',
    desc: '시장 변동성(ATR)이 어느 정도일 때 단타로 전환할지 정합니다.',
    fields: [
      { key: 'mode_high_volatility_atr_pct', label: '단타 전환 기준', unit: '%', min: 0.5, max: 5, step: 0.5,
        hint: 'ATR이 이 값 이상이면 단타 모드로 전환합니다. 낮추면 더 자주 단타 모드로 바뀝니다.' },
      { key: 'mode_low_volatility_atr_pct', label: '스윙 유지 기준', unit: '%', min: 0.2, max: 2, step: 0.1,
        hint: 'ATR이 이 값 이하면 스윙을 유지합니다. 높이면 단타 범위가 줄어듭니다.' },
    ],
  },
]

// ── Presets ──
const PRESETS = {
  안정형: {
    label: '🛡️ 안정형',
    desc: '손실을 최소화. 매매 횟수는 적지만 큰 손실은 거의 없습니다.',
    values: {
      buy_threshold: 0.75, sell_threshold: 0.30,
      mode_swing_stop_loss_pct: 2.0, mode_swing_daily_loss_pct: 3.0, mode_swing_position_size: 0.05,
      mode_short_stop_loss_pct: 1.0, mode_short_daily_loss_pct: 2.0, mode_short_position_size: 0.03,
      mode_hold_stop_loss_pct: 2.0, mode_hold_daily_loss_pct: 3.0, mode_hold_position_size: 0.0,
      regime_bull_threshold: 70, regime_bear_threshold: 30,
      screener_bull_min_score: 70, screener_neutral_min_score: 80, screener_bear_min_score: 90,
      screener_bull_max_candidates: 8, screener_neutral_max_candidates: 5, screener_bear_max_candidates: 2,
      mode_high_volatility_atr_pct: 2.0, mode_low_volatility_atr_pct: 0.7,
    },
  },
  균형형: {
    label: '⚖️ 균형형',
    desc: '기본 설정. 적당한 위험과 수익을 추구합니다. (권장)',
    values: {
      buy_threshold: 0.65, sell_threshold: 0.35,
      mode_swing_stop_loss_pct: 3.0, mode_swing_daily_loss_pct: 5.0, mode_swing_position_size: 0.10,
      mode_short_stop_loss_pct: 1.5, mode_short_daily_loss_pct: 3.0, mode_short_position_size: 0.05,
      mode_hold_stop_loss_pct: 3.0, mode_hold_daily_loss_pct: 5.0, mode_hold_position_size: 0.0,
      regime_bull_threshold: 65, regime_bear_threshold: 35,
      screener_bull_min_score: 60, screener_neutral_min_score: 70, screener_bear_min_score: 80,
      screener_bull_max_candidates: 15, screener_neutral_max_candidates: 10, screener_bear_max_candidates: 5,
      mode_high_volatility_atr_pct: 1.5, mode_low_volatility_atr_pct: 0.5,
    },
  },
  공격형: {
    label: '🚀 공격형',
    desc: '기회를 많이 잡습니다. 수익이 클 수 있지만 손실도 커질 수 있습니다.',
    values: {
      buy_threshold: 0.55, sell_threshold: 0.40,
      mode_swing_stop_loss_pct: 5.0, mode_swing_daily_loss_pct: 8.0, mode_swing_position_size: 0.20,
      mode_short_stop_loss_pct: 2.5, mode_short_daily_loss_pct: 5.0, mode_short_position_size: 0.10,
      mode_hold_stop_loss_pct: 5.0, mode_hold_daily_loss_pct: 8.0, mode_hold_position_size: 0.0,
      regime_bull_threshold: 55, regime_bear_threshold: 40,
      screener_bull_min_score: 50, screener_neutral_min_score: 60, screener_bear_min_score: 70,
      screener_bull_max_candidates: 25, screener_neutral_max_candidates: 15, screener_bear_max_candidates: 8,
      mode_high_volatility_atr_pct: 1.0, mode_low_volatility_atr_pct: 0.3,
    },
  },
}

// ── Components ──
function FieldRow({ field, value, onChange }) {
  const displayValue = field.fmt ? field.fmt(value) : `${value}${field.unit || ''}`

  return (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-1">
        <label className="text-sm font-medium text-ngsat-text">{field.label}</label>
        <span className="text-sm font-mono text-ngsat-accent tabular-nums">{displayValue}</span>
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
      {field.hint && (
        <p className="text-xs text-ngsat-muted mt-1 leading-relaxed">{field.hint}</p>
      )}
    </div>
  )
}

function PresetButtons({ onSelect, current }) {
  return (
    <div className="ngsat-card p-4 mb-0">
      <div className="text-xs text-ngsat-muted mb-3">
        💡 처음이시면 아래 세 가지 스타일 중 하나를 골라보세요. 선택하면 모든 값이 자동으로 조정됩니다.
      </div>
      <div className="flex gap-2">
        {Object.entries(PRESETS).map(([name, preset]) => (
          <button
            key={name}
            onClick={() => {
              if (confirm(`"${name}" 스타일로 모든 값을 변경하시겠습니까?\n현재 설정은 사라집니다.`)) {
                onSelect(preset.values)
              }
            }}
            className={`flex-1 p-3 rounded-lg border text-sm transition-all
              ${current === name
                ? 'border-ngsat-accent bg-ngsat-accent/10 text-ngsat-text'
                : 'border-ngsat-border bg-ngsat-card hover:border-ngsat-accent/30 text-ngsat-muted hover:text-ngsat-text'
              }`}
          >
            <div className="text-base mb-1">{preset.label}</div>
            <div className="text-xs leading-relaxed">{preset.desc}</div>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Main Panel ──
export default function StrategyConfigPanel({ api }) {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [activePreset, setActivePreset] = useState('균형형')

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
    setActivePreset('') // custom changed
  }

  const handlePreset = (values) => {
    setConfig(prev => prev ? { ...prev, ...values } : prev)
    // find matching preset name
    for (const [name, p] of Object.entries(PRESETS)) {
      if (Object.entries(p.values).every(([k, v]) => Math.abs(v - values[k]) < 0.001)) {
        setActivePreset(name)
        return
      }
    }
    setActivePreset('')
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
      setActivePreset('균형형')
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
      {/* Header */}
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

      {/* Presets */}
      <PresetButtons onSelect={handlePreset} current={activePreset} />

      {/* Message */}
      {message && (
        <div className={`text-sm px-4 py-2 rounded-lg ${
          message.includes('실패') ? 'bg-ngsat-red/10 text-ngsat-red' : 'bg-ngsat-green/10 text-ngsat-green'
        }`}>
          {message}
        </div>
      )}

      {/* Sections */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {SECTIONS.map(section => (
          <div key={section.id} className="ngsat-card p-5">
            <h4 className="text-sm font-semibold text-ngsat-text mb-1">{section.title}</h4>
            <p className="text-xs text-ngsat-muted mb-4 leading-relaxed">{section.desc}</p>
            <div className="pt-2 border-t border-ngsat-border/50">
              {section.fields.map(field => (
                <FieldRow
                  key={field.key}
                  field={field}
                  value={config[field.key]}
                  onChange={handleChange}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
