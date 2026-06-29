const LABEL_MAP = {
  regime_score: '레짐 점수',
  atr_pct: 'ATR(%)',
  ma_alignment: 'MA 정렬',
  rsi: 'RSI',
  bollinger: '볼린저',
  change_rate: '등락률',
  volume_trend: '거래량',
  adx: 'ADX',
  bb_width: '밴드폭',
  total_score: '총점',
  bull_threshold: '강세 기준',
  bear_threshold: '약세 기준',
  high_volatility: '고변동',
  low_volatility: '저변동',
  strong_trend: '강한 추세',
}

export default function EvidenceBox({ evidence }) {
  if (!evidence || typeof evidence !== 'object' || Object.keys(evidence).length === 0) {
    return null
  }

  const entries = Object.entries(evidence).slice(0, 10)

  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs mt-3 pt-3 border-t border-ngsat-border/50">
      {entries.map(([key, value]) => (
        <div key={key} className="flex justify-between">
          <span className="text-ngsat-muted">{LABEL_MAP[key] || key.replace(/_/g, ' ')}</span>
          <span className="text-ngsat-text font-mono tabular-nums">
            {typeof value === 'number' ? value.toFixed(2) : String(value)}
          </span>
        </div>
      ))}
      {Object.keys(evidence).length > 10 && (
        <div className="col-span-2 text-ngsat-muted text-[10px] text-center mt-1">
          외 {Object.keys(evidence).length - 10}개 항목
        </div>
      )}
    </div>
  )
}
