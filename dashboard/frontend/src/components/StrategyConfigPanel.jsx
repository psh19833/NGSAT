import { useState, useEffect } from 'react'
import SkeletonCard from './SkeletonCard.jsx'

// ── Section + field definitions ──
const SECTIONS = [
  {
    id: 'entry',
    title: '① 매매 진입·청산 판단',
    desc: 'AI가 "살까? 팔까?"를 결정하는 기준입니다. 숫자가 높을수록 더 확실할 때만 움직입니다.',
    group: 'trade',
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
    group: 'risk',
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
    group: 'risk',
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
    group: 'risk',
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
    desc: '현재 장세를 "강세·중립·약세"로 구분하는 기준입니다.',
    group: 'market',
    fields: [
      { key: 'regime_bull_threshold', label: '강세장 판정 점수', unit: '점', min: 50, max: 90, step: 1,
        hint: '높을수록(예:75점) 강세장 판정이 깐깐해집니다. 낮추면 더 자주 "강세"로 봅니다.' },
      { key: 'regime_bear_threshold', label: '약세장 판정 점수', unit: '점', min: 10, max: 50, step: 1,
        hint: '낮을수록(예:25점) 약세장 판정이 깐깐해집니다. 올리면 더 자주 "약세"로 봅니다.' },
    ],
  },
  {
    id: 'regime_weights',
    title: '⑤-② 시장 점수 구성 비중',
    desc: '각 요소가 시장 점수(100점 만점)에 얼마나 반영될지 정합니다. 합계가 100이 되어야 합니다.',
    group: 'market',
    compact: true,
    weightSumCheck: true,
    fields: [
      { key: 'regime_weight_ma', label: '이동평균선', unit: '/100', min: 5, max: 60, step: 5,
        hint: '추세 방향(MA 정렬) 중요도.' },
      { key: 'regime_weight_rsi', label: '과열·침체(RSI)', unit: '/100', min: 5, max: 40, step: 5,
        hint: 'RSI 신호 반영 비중.' },
      { key: 'regime_weight_bollinger', label: '밴드 위치', unit: '/100', min: 5, max: 40, step: 5,
        hint: '볼린저밴드 위치 반영 비중.' },
      { key: 'regime_weight_change_rate', label: '단기 등락', unit: '/100', min: 5, max: 30, step: 5,
        hint: '최근 5일 등락 반영 비중.' },
      { key: 'regime_weight_volume', label: '거래량', unit: '/100', min: 5, max: 30, step: 5,
        hint: '거래량 추세 반영 비중.' },
      { key: 'regime_weight_adx', label: '추세 강도(ADX)', unit: '/100', min: 0, max: 20, step: 5,
        hint: '추세 강도 반영 비중. 0=미사용.' },
    ],
  },
  {
    id: 'mode_switch',
    title: '⑥ 스윙 ↔ 단타 자동 전환',
    desc: '시장 변동성(ATR)이 어느 정도일 때 단타로 전환할지 정합니다.',
    group: 'market',
    fields: [
      { key: 'mode_high_volatility_atr_pct', label: '단타 전환 기준', unit: '%', min: 0.5, max: 5, step: 0.5,
        hint: 'ATR이 이 값 이상이면 단타 모드로 전환합니다. 낮추면 더 자주 단타 모드로 바뀝니다.' },
      { key: 'mode_low_volatility_atr_pct', label: '스윙 유지 기준', unit: '%', min: 0.2, max: 2, step: 0.1,
        hint: 'ATR이 이 값 이하면 스윙을 유지합니다. 높이면 단타 범위가 줄어듭니다.' },
    ],
    warning: (cfg) => {
      const high = cfg.mode_high_volatility_atr_pct
      const low = cfg.mode_low_volatility_atr_pct
      if (high <= low) {
        return '⚠️ 단타 전환 기준이 스윙 유지 기준보다 낮거나 같으면 거의 항상 단타 모드로 동작합니다. 단타 전환 > 스윙 유지 가 되도록 설정하세요.'
      }
      return null
    },
  },
  {
    id: 'screener',
    title: '⑦ 종목 선별 기준',
    desc: '매수할 종목을 고를 때 얼마나 엄격하게 볼지 정합니다. 장세별로 다릅니다.',
    group: 'screener',
    fields: [
      { key: 'screener_bull_min_score', label: '강세장 최소 점수', unit: '점', min: 30, max: 90, step: 5,
        hint: '60점이면 60점 이상 종목만 매수 후보.' },
      { key: 'screener_bull_max_candidates', label: '강세장 최대 후보', unit: '개', min: 1, max: 50, step: 1,
        hint: '강세장에서 한 번에 최대 몇 종목까지 살지.' },
      { key: 'screener_neutral_min_score', label: '중립장 최소 점수', unit: '점', min: 30, max: 90, step: 5,
        hint: '중립장은 더 깐깐하게. 70점이면 강세장(60점)보다 엄격합니다.' },
      { key: 'screener_neutral_max_candidates', label: '중립장 최대 후보', unit: '개', min: 1, max: 30, step: 1,
        hint: '중립장은 후보를 더 적게.' },
      { key: 'screener_bear_min_score', label: '약세장 최소 점수', unit: '점', min: 30, max: 95, step: 5,
        hint: '약세장은 매우 깐깐하게. 80점 이상 권장.' },
      { key: 'screener_bear_max_candidates', label: '약세장 최대 후보', unit: '개', min: 1, max: 15, step: 1,
        hint: '약세장에서는 거의 사지 않음. 5개 이하 권장.' },
    ],
  },
  {
    id: 'ml_training',
    title: '⑧ ML 학습 기간',
    desc: 'AI가 학습할 데이터와 모델을 설정합니다.',
    group: 'ml',
    fields: [
      { key: 'ml_model_type', label: 'AI 모델 종류', type: 'select',
        options: [
          { value: 'random_forest', label: 'Random Forest (기본)' },
          { value: 'gradient_boosting', label: 'Gradient Boosting' },
          { value: 'xgboost', label: 'XGBoost (높은 정확도)' },
          { value: 'lightgbm', label: 'LightGBM (빠른 학습)' },
          { value: 'logistic', label: 'Logistic (가벼운 모델)' },
        ],
        hint: 'XGBoost·LightGBM이 일반적으로 더 높은 성능을 냅니다. 변경 후 모델 재학습이 필요합니다.' },
      { key: 'ml_auto_retrain', label: '자동 재학습', type: 'toggle',
        hint: '켜면 매일 장 마감 후 새로운 데이터로 AI가 스스로 재학습합니다.' },
      { key: 'ml_auto_select_model', label: '자동 모델 선택', type: 'toggle',
        hint: '켜면 5개 AI 모델 전부 테스트 후 가장 성능이 좋은 모델로 자동 교체합니다.' },
      { key: 'ml_training_days', label: '최근 N일 학습', unit: '일', min: 30, max: 1000, step: 10,
        hint: '기본 모드. 최근 N일 데이터로 학습합니다.' },
      { key: 'ml_training_start_date', label: '▸ 시작일 (선택)', type: 'date',
        hint: '과거 특정 구간 시작일. 설정 시 위 "최근 N일"보다 우선합니다.' },
      { key: 'ml_training_end_date', label: '▸ 종료일 (선택)', type: 'date',
        hint: '과거 특정 구간 종료일. 비워두면 오늘까지 조회합니다.' },
      { key: 'ml_swing_forward_days', label: '스윙 예측 기간', unit: '일', min: 1, max: 10, step: 1,
        hint: '스윙: N일 뒤 +2% 상승을 예측합니다.' },
      { key: 'ml_short_forward_minutes', label: '단타 예측 기간', unit: '분', min: 10, max: 240, step: 10,
        hint: '단타: N분 뒤 +0.5% 상승을 예측합니다.' },
    ],
  },
  {
    id: 'portfolio',
    title: '⑨ 포트폴리오 제한',
    desc: '한 번에 보유할 종목 수를 제한합니다.',
    group: 'risk',
    fields: [
      { key: 'max_holdings', label: '최대 보유 종목', unit: '개', min: 1, max: 20, step: 1,
        hint: '동시 보유 최대 종목 수. 10이면 최대 10개까지 동시 보유. 0=무제한.' },
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
      mode_high_volatility_atr_pct: 2.0,
      ml_swing_forward_days: 3, ml_short_forward_minutes: 60, mode_low_volatility_atr_pct: 0.7,
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
      mode_high_volatility_atr_pct: 1.5,
      ml_swing_forward_days: 3, ml_short_forward_minutes: 60, mode_low_volatility_atr_pct: 0.5,
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
      mode_high_volatility_atr_pct: 1.0,
      ml_swing_forward_days: 3, ml_short_forward_minutes: 60, mode_low_volatility_atr_pct: 0.3,
    },
  },
}

// ── Group display config ──
const GROUP_ICONS = {
  trade: '📊',
  risk: '🛡️',
  market: '📈',
  screener: '🔍',
  ml: '🤖',
}
const GROUP_LABELS = {
  trade: '매매 판단',
  risk: '리스크 관리',
  market: '시장 분석',
  screener: '종목 선별',
  ml: '학습',
}

// ── Regime weight constraint: sum must always be 100 ──
const REGIME_WEIGHT_KEYS = ['regime_weight_ma','regime_weight_rsi','regime_weight_bollinger','regime_weight_change_rate','regime_weight_volume','regime_weight_adx']
const REGIME_WEIGHT_LIMITS = {
  regime_weight_ma:       { min: 5, max: 60 },
  regime_weight_rsi:      { min: 5, max: 40 },
  regime_weight_bollinger: { min: 5, max: 40 },
  regime_weight_change_rate: { min: 5, max: 30 },
  regime_weight_volume:   { min: 5, max: 30 },
  regime_weight_adx:      { min: 0, max: 20 },
}

function snapStep(v, step=5) {
  return Math.round(v / step) * step
}

function rebalanceWeights(current, changedKey, newVal) {
  // Proportional rebalance: user changed one weight → redistribute remaining budget
  const others = REGIME_WEIGHT_KEYS.filter(k => k !== changedKey)
  const remaining = 100 - newVal
  const totalOthers = others.reduce((s, k) => s + current[k], 0)

  let result = { ...current, [changedKey]: newVal }
  let allocated = 0
  const clamped = new Set()

  // First pass: proportional distribution, clamped
  for (const k of others) {
    if (totalOthers === 0) {
      result[k] = REGIME_WEIGHT_LIMITS[k].min
    } else {
      let v = snapStep((current[k] / totalOthers) * remaining)
      v = Math.max(REGIME_WEIGHT_LIMITS[k].min, Math.min(REGIME_WEIGHT_LIMITS[k].max, v))
      result[k] = v
    }
    allocated += result[k]
    if (result[k] <= current[k] - 5 || result[k] >= current[k] + 5) {
      // mark as changed if moved at least one step
    }
  }

  // Second pass: correct rounding error (±5) on the largest non-clamped weight
  let diff = snapStep(remaining - allocated)
  while (diff !== 0) {
    // Pick the non-clamped weight with most room to adjust
    const candidates = others.filter(k => {
      if (diff > 0) return result[k] < REGIME_WEIGHT_LIMITS[k].max
      return result[k] > REGIME_WEIGHT_LIMITS[k].min
    }).sort((a, b) => diff > 0
      ? (REGIME_WEIGHT_LIMITS[b].max - result[b]) - (REGIME_WEIGHT_LIMITS[a].max - result[a])
      : (result[a] - REGIME_WEIGHT_LIMITS[a].min) - (result[b] - REGIME_WEIGHT_LIMITS[b].min)
    )

    if (candidates.length === 0) break // stuck — shouldn't happen with sane defaults

    const k = candidates[0]
    const adjustStep = Math.sign(diff) * 5
    let newV = snapStep(result[k] + adjustStep)
    newV = Math.max(REGIME_WEIGHT_LIMITS[k].min, Math.min(REGIME_WEIGHT_LIMITS[k].max, newV))
    const actualDelta = newV - result[k]
    result[k] = newV
    diff -= actualDelta
  }

  return result
}

// ── Components ──
function FieldRow({ field, value, onChange }) {
  const displayValue = field.fmt ? field.fmt(value) : `${value}${field.unit || ''}`

  // Select dropdown
  if (field.type === 'select') {
    return (
      <div className="mb-3">
        <label className="text-sm font-medium text-ngsat-text block mb-1">{field.label}</label>
        <select
          value={value || field.options?.[0]?.value}
          onChange={e => onChange(field.key, e.target.value)}
          className="w-full px-3 py-2 text-sm bg-ngsat-bg border border-ngsat-border rounded
            text-ngsat-text focus:outline-none focus:border-ngsat-accent/50"
        >
          {field.options?.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        {field.hint && <p className="text-xs text-ngsat-muted mt-1">{field.hint}</p>}
      </div>
    )
  }

  // Toggle switch
  if (field.type === 'toggle') {
    return (
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium text-ngsat-text">{field.label}</label>
          <button
            onClick={() => onChange(field.key, !value)}
            role="switch"
            aria-checked={value}
            className={`relative w-10 h-5 rounded-full transition-colors ${
              value ? 'bg-ngsat-green' : 'bg-ngsat-border'
            }`}
          >
            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              value ? 'translate-x-5' : 'translate-x-0.5'
            }`} />
          </button>
        </div>
        {field.hint && <p className="text-xs text-ngsat-muted mt-1">{field.hint}</p>}
      </div>
    )
  }

  // Date input
  if (field.type === 'date') {
    return (
      <div className="mb-3">
        <label className="text-sm font-medium text-ngsat-text block mb-1">{field.label}</label>
        <input
          type="date"
          value={value || ''}
          onChange={e => onChange(field.key, e.target.value || null)}
          className="w-full px-3 py-2 text-sm bg-ngsat-bg border border-ngsat-border rounded
            text-ngsat-text focus:outline-none focus:border-ngsat-accent/50"
        />
        {field.hint && <p className="text-xs text-ngsat-muted mt-1">{field.hint}</p>}
      </div>
    )
  }

  // Default: range slider + number input
  return (
    <div className="mb-3">
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
          style={{ '--fill-pct': (((value ?? field.min) - field.min) / (field.max - field.min) * 100) + '%' }}
          className="flex-1 h-1.5 accent-ngsat-accent"
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value ?? field.min}
          onChange={e => onChange(field.key, parseFloat(e.target.value) || field.min)}
          className="w-14 px-2 py-1 text-xs text-right font-mono bg-ngsat-bg border border-ngsat-border rounded
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
  const [presets, setPresets] = React.useState(null);
  const [applying, setApplying] = React.useState(null);

  React.useEffect(() => {
    fetch('/api/strategy/presets')
      .then(r => r.json())
      .then(d => { if (d.connected) setPresets(d.presets); })
      .catch(() => {});
  }, []);

  if (!presets) return null;

  const handleApply = async (name) => {
    if (!confirm(`"${name}" 스타일로 모든 값을 변경하시겠습니까?\n현재 설정은 사라집니다.`)) return;
    setApplying(name);
    try {
      const resp = await fetch('/api/strategy/apply-preset', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, retrain: true}),
      });
      const data = await resp.json();
      if (data.connected) {
        // Apply values to local form, trigger parent refresh
        const p = presets[name];
        if (p) onSelect(p.values);
        alert(`✅ "${name}" 적용 완료\n변경: ${data.applied}개 항목\n${data.retrain?.auc ? `AUC: ${(data.retrain.auc*100).toFixed(1)}%${data.retrain.changed ? ' ✅ 향상' : ' ➖ 유지'}` : ''}`);
      } else {
        alert(`❌ 적용 실패: ${data.message}`);
      }
    } catch (e) {
      alert(`❌ 적용 오류: ${e.message}`);
    } finally {
      setApplying(null);
    }
  };

  const entries = Object.entries(presets);

  return (
    <div className="ngsat-card p-4">
      <div className="text-xs text-ngsat-muted mb-3">
        💡 처음이시면 아래 스타일 중 하나를 골라보세요. 선택하면 모든 값이 자동으로 조정됩니다.
      </div>
      <div className="flex flex-wrap gap-2">
        {entries.map(([name, preset]) => (
          <button
            key={name}
            onClick={() => handleApply(name)}
            disabled={applying !== null}
            className={`flex-1 min-w-[120px] p-3 rounded-lg border text-sm transition-all
              ${current === name
                ? 'border-ngsat-accent bg-ngsat-accent/10 text-ngsat-text'
                : 'border-ngsat-border bg-ngsat-card hover:border-ngsat-accent/30 text-ngsat-muted hover:text-ngsat-text'
              }
              ${applying === name ? 'opacity-50 cursor-wait' : ''}
            `}
          >
            <div className="font-bold">{preset.label}</div>
            <div className="text-[10px] mt-1 opacity-70">{preset.desc}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function CollapsibleSection({ section, config, onChange, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen)

  // Compute summary: count how many fields have been changed from defaults
  const changedCount = section.fields.filter(f => {
    if (f.type === 'select' || f.type === 'toggle' || f.type === 'date') return false
    return config[f.key] !== undefined && config[f.key] !== f.min
  }).length

  // Weight sum check for regime_weights section — show always
  const weightSumStatus = section.weightSumCheck && (() => {
    const keys = REGIME_WEIGHT_KEYS
    const sum = keys.reduce((s, k) => s + (config[k] || 0), 0)
    return { sum, ok: sum === 100 }
  })()

  return (
    <div className={`ngsat-card transition-all duration-200 ${open ? 'shadow-md' : ''}`}>
      {/* Header - clickable */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-start gap-3 p-4 text-left"
      >
        {/* Expand indicator */}
        <div className={`mt-0.5 w-5 h-5 flex-shrink-0 rounded flex items-center justify-center transition-all
          ${open ? 'bg-ngsat-accent/20 text-ngsat-accent' : 'bg-ngsat-border/30 text-ngsat-muted'}`}
        >
          <svg className={`w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>

        {/* Title + desc */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-sm font-semibold text-ngsat-text">{section.title}</h4>
            {/* Badges */}
            {!open && changedCount > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-ngsat-accent/15 text-ngsat-accent font-medium">
                {changedCount}개 변경
              </span>
            )}
            {!open && weightSumStatus && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                weightSumStatus.ok
                  ? 'bg-ngsat-green/15 text-ngsat-green'
                  : 'bg-ngsat-red/15 text-ngsat-red'
              }`}>
                {weightSumStatus.ok ? '합계 100 ✅' : `⚠️ 합계 ${weightSumStatus.sum}점`}
              </span>
            )}
          </div>
          {!open && (
            <p className="text-xs text-ngsat-muted mt-0.5 line-clamp-1">{section.desc}</p>
          )}
        </div>
      </button>

      {/* Body - collapsible */}
      <div className={`overflow-hidden transition-all duration-300 ease-in-out ${
        open ? 'max-h-[2000px] opacity-100' : 'max-h-0 opacity-0'
      }`}>
        {open && (
          <div className="px-4 pb-4">
            <p className="text-xs text-ngsat-muted mb-3 leading-relaxed">{section.desc}</p>

            {/* Model info banner */}
            {section.id === 'ml_training' && config._modelInfo && (
              <div className="flex items-center gap-3 px-3 py-2 mb-3 bg-ngsat-accent/5 border border-ngsat-accent/20 rounded text-xs">
                <span className="text-ngsat-text font-medium">현재 모델:</span>
                <span className="text-ngsat-accent font-semibold">{config._modelInfo.type}</span>
                {config._modelInfo.auc != null && (
                  <>
                    <span className="text-ngsat-muted">|</span>
                    <span className="text-ngsat-text">AUC:</span>
                    <span className="text-ngsat-green font-semibold">{Number(config._modelInfo.auc).toFixed(3)}</span>
                  </>
                )}
              </div>
            )}

            {/* Warning */}
            {section.warning && section.warning(config) && (
              <div className="bg-ngsat-red/10 border border-ngsat-red/20 rounded-lg p-3 mb-3 text-xs text-ngsat-red leading-relaxed">
                {section.warning(config)}
              </div>
            )}

            {/* Weight sum status (regime_weights section) */}
            {section.weightSumCheck && (
              <div className={`flex items-center gap-2 px-3 py-2 mb-3 rounded-lg text-xs ${
                weightSumStatus.ok
                  ? 'bg-ngsat-green/10 text-ngsat-green border border-ngsat-green/20'
                  : 'bg-ngsat-yellow/10 text-ngsat-yellow border border-ngsat-yellow/20'
              }`}>
                <span>{weightSumStatus.ok ? '✅' : '⚠️'}</span>
                <span>가중치 합계 <strong>{weightSumStatus.sum}점</strong> / 100점
                  {weightSumStatus.ok ? ' — 정상' : ' — 합계를 확인하세요'}</span>
                {/* Auto-adjustment toast */}
                {config._adjustMsg && (
                  <span className="ml-auto text-ngsat-accent font-medium animate-pulse">
                    ⇢ {config._adjustMsg}
                  </span>
                )}
              </div>
            )}

            {/* Fields */}
            <div className="pt-2 border-t border-ngsat-border/50">
              {section.fields.map(field => (
                <FieldRow
                  key={field.key}
                  field={field}
                  value={config[field.key]}
                  onChange={onChange}
                />
              ))}
            </div>

            {/* Retrain button */}
            {section.id === 'ml_training' && (
              <div className="mt-3 pt-3 border-t border-ngsat-border/50">
                <button
                  onClick={config._onRetrain}
                  disabled={config._retraining}
                  className="w-full px-4 py-2 text-sm font-medium text-white bg-ngsat-accent rounded-lg
                    hover:bg-ngsat-accent/80 transition-all disabled:opacity-50 disabled:cursor-wait"
                >
                  {config._retraining ? '재학습 중... (1~2분)' : '⚡ 지금 재학습 실행'}
                </button>
                {config._retrainMsg && (
                  <div className={`mt-2 px-3 py-2 rounded text-xs ${
                    config._retrainMsg.ok
                      ? 'bg-ngsat-green/10 text-ngsat-green border border-ngsat-green/20'
                      : 'bg-ngsat-red/10 text-ngsat-red border border-ngsat-red/20'
                  }`}>
                    {config._retrainMsg.text}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Panel ──
export default function StrategyConfigPanel({ api, onDirtyChange }) {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [activePreset, setActivePreset] = useState('균형형')
  const [currentModelType, setCurrentModelType] = useState(null)
  const [currentAuc, setCurrentAuc] = useState(null)
  const [retraining, setRetraining] = useState(false)
  const [retrainMsg, setRetrainMsg] = useState(null)
  const [allOpen, setAllOpen] = useState(false)
  const [adjustMsg, setAdjustMsg] = useState(null) // regime weight auto-adjustment toast

  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    setLoading(true)
    const resp = await api.getStrategyConfig()
    if (resp?.config) {
      setConfig(resp.config)
      if (resp.current_model_type) {
        setCurrentModelType(resp.current_model_type)
        setCurrentAuc(resp.current_auc ?? null)
      }
      for (const [name, p] of Object.entries(PRESETS)) {
        if (Object.entries(p.values).every(([k, v]) => Math.abs(v - (resp.config[k] ?? 0)) < 0.001)) {
          setActivePreset(name)
          break
        }
      }
    }
    setLoading(false)
  }

  const handleChange = (key, value) => {
    // Regime weights: rebalance others proportionally to keep sum=100
    if (REGIME_WEIGHT_KEYS.includes(key)) {
      setConfig(prev => {
        if (!prev) return prev
        const rebalanced = rebalanceWeights(prev, key, value)
        // Build adjustment summary
        const changes = REGIME_WEIGHT_KEYS
          .filter(k => rebalanced[k] !== prev[k])
          .map(k => {
            const labels = { regime_weight_ma: 'MA', regime_weight_rsi: 'RSI', regime_weight_bollinger: 'BB',
              regime_weight_change_rate: 'CR', regime_weight_volume: 'VOL', regime_weight_adx: 'ADX' }
            const delta = rebalanced[k] - prev[k]
            return `${labels[k]} ${delta > 0 ? '+' : ''}${delta}`
          })
        if (changes.length > 0) {
          setAdjustMsg(changes.join(', '))
          setTimeout(() => setAdjustMsg(null), 3000)
        }
        return rebalanced
      })
    } else {
      setConfig(prev => prev ? { ...prev, [key]: value } : prev)
    }
    setActivePreset('')
    onDirtyChange?.(true)
  }

  const handleRetrain = async () => {
    setRetraining(true)
    setRetrainMsg(null)
    const resp = await api.retrain()
    if (resp?.connected) {
      setRetrainMsg({ ok: true, text: resp.message || '재학습 완료' })
      if (resp.model_type) setCurrentModelType(resp.model_type)
      if (resp.auc != null) setCurrentAuc(resp.auc)
    } else {
      setRetrainMsg({ ok: false, text: resp?.message || '재학습 실패' })
    }
    setRetraining(false)
  }

  const handlePreset = (values) => {
    setConfig(prev => prev ? { ...prev, ...values } : prev)
    onDirtyChange?.(true)
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
      onDirtyChange?.(false)
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
      onDirtyChange?.(false)
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
    return <SkeletonCard lines={6} />
  }

  if (!config) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-4">전략 설정</h3>
        <p className="text-ngsat-red text-sm">설정을 불러올 수 없습니다</p>
      </div>
    )
  }

  // Augment config with meta props for child components
  const configWithMeta = {
    ...config,
    _modelInfo: currentModelType ? { type: currentModelType, auc: currentAuc } : null,
    _retraining: retraining,
    _retrainMsg: retrainMsg,
    _onRetrain: handleRetrain,
    _adjustMsg: adjustMsg,
  }

  // Group sections
  const groupedSections = {}
  SECTIONS.forEach(s => {
    if (!groupedSections[s.group]) groupedSections[s.group] = []
    groupedSections[s.group].push(s)
  })

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-ngsat-text">전략 설정</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAllOpen(!allOpen)}
            className="px-3 py-1.5 text-xs text-ngsat-muted border border-ngsat-border rounded-lg
              hover:text-ngsat-accent hover:border-ngsat-accent/30 transition-all"
          >
            {allOpen ? '전체 접기' : '전체 펼치기'}
          </button>
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

      {/* Message */}
      {message && (
        <div className={`text-sm px-4 py-2 rounded-lg ${
          message.includes('실패') ? 'bg-ngsat-red/10 text-ngsat-red' : 'bg-ngsat-green/10 text-ngsat-green'
        }`}>
          {message}
        </div>
      )}

      {/* Presets */}
      <PresetButtons onSelect={handlePreset} current={activePreset} />

      {/* Sections by group */}
      <div className="space-y-6">
        {Object.entries(groupedSections).map(([group, sections]) => (
          <div key={group}>
            {/* Group header */}
            <div className="flex items-center gap-2 mb-3">
              <span className="text-base">{GROUP_ICONS[group]}</span>
              <h4 className="text-sm font-semibold text-ngsat-text/80 uppercase tracking-wider">{GROUP_LABELS[group]}</h4>
              <div className="flex-1 h-px bg-ngsat-border/30" />
              <span className="text-[10px] text-ngsat-muted">{sections.length}개 항목</span>
            </div>
            {/* Section cards */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
              {sections.map((section, idx) => {
                // Determine default state: open first section in first group, or if allOpen
                const isFirst = idx === 0 && group === Object.keys(groupedSections)[0]
                const defaultOpen = allOpen ? true : (isFirst && !allOpen)
                return (
                  <CollapsibleSection
                    key={section.id}
                    section={section}
                    config={configWithMeta}
                    onChange={handleChange}
                    defaultOpen={defaultOpen}
                  />
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
