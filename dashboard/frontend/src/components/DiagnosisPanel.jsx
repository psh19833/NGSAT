import { useState, useEffect, useRef } from 'react'
import SkeletonCard from './SkeletonCard.jsx'

export default function DiagnosisPanel({ api }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [expandedStock, setExpandedStock] = useState(null)  // P-60: 상세 점수 접기
  const prevCycle = useRef(0)

  useEffect(() => {
    let mounted = true
    const fetch = async () => {
      const resp = await api.getDiagnosis()
      if (mounted && resp?.connected) {
        setData(resp)
        setLoading(false)
      }
    }
    fetch()
    const interval = setInterval(fetch, 5000)
    return () => { mounted = false; clearInterval(interval) }
  }, [api])

  if (loading || !data) {
    return <SkeletonCard lines={4} className="mt-4" />
  }

  if (data.message) {
    return (
      <div className="ngsat-card p-4 mt-4">
        <h3 className="text-sm font-semibold text-ngsat-text mb-2">🔍 진단 현황</h3>
        <p className="text-xs text-ngsat-muted">{data.message}</p>
      </div>
    )
  }

  const screened = data.screened || []
  const predictions = data.predictions || []
  const deferred = data.deferred_entries || []
  const buys = predictions.filter(p => p.action === 'buy')
  const holds = predictions.filter(p => p.action === 'hold')
  const watches = predictions.filter(p => p.action === 'none')

  // Flash effect on new cycle
  const isNew = data.cycle !== prevCycle.current
  if (isNew) prevCycle.current = data.cycle

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className={`ngsat-card p-4 transition-colors ${isNew ? 'ring-1 ring-ngsat-accent/30' : ''}`}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-sm font-semibold text-ngsat-text">🔍 진단 현황</h3>
          <div className="flex items-center gap-3 text-xs text-ngsat-muted">
            <span>사이클 <span className="num text-ngsat-text font-medium">#{data.cycle || 0}</span></span>
            <span>시장 <span className={`font-medium ${
              data.regime === 'bull' ? 'text-ngsat-green' : data.regime === 'bear' ? 'text-ngsat-red' : 'text-ngsat-yellow'
            }`}>{data.regime === 'bull' ? '강세장' : data.regime === 'bear' ? '약세장' : '중립장'}</span></span>
            <span>모드 <span className="text-ngsat-accent font-medium">{data.mode === 'short_term' ? '단타' : data.mode === 'hold' ? '홀드' : '스윙'}</span></span>
            {data.minute_ml_status && data.mode === 'short_term' && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                data.minute_ml_status === '정상(분봉ML)' ? 'bg-ngsat-green/10 text-ngsat-green' : 'bg-ngsat-yellow/10 text-ngsat-yellow'
              }`}>{data.minute_ml_status}</span>
            )}
            {data.buys > 0 && <span className="text-ngsat-green font-medium">🟢 매수 {data.buys}건</span>}
            {data.sells > 0 && <span className="text-ngsat-red font-medium">🔴 매도 {data.sells}건</span>}
          </div>
        </div>
        {data.summary && <p className="text-xs text-ngsat-muted mt-2">{data.summary}</p>}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Screener Results */}
        <div className="ngsat-card p-4">
          <h4 className="text-sm font-medium text-ngsat-text mb-3">
            📊 스크리너 ({screened.length}종목 통과)
          </h4>
          {screened.length === 0 ? (
            <p className="text-xs text-ngsat-muted">통과 종목 없음</p>
          ) : (
            <div className="space-y-1 max-h-80 overflow-y-auto">
              {screened.map((s, i) => {
                const isExpanded = expandedStock === i
                const ind = s.indicators || {}
                return (
                  <div key={i}>
                    <div className={'flex items-center justify-between text-xs py-1.5 px-2 rounded cursor-pointer transition-colors ' + (
                      isExpanded ? 'bg-ngsat-accent/10 ring-1 ring-ngsat-accent/30' :
                      s.score >= 70 ? 'bg-ngsat-green/5' : s.score >= 60 ? 'bg-ngsat-yellow/5' : ''
                    )} onClick={() => setExpandedStock(isExpanded ? null : i)}>
                      <div className="flex items-center gap-2 min-w-0">
                        <span className={'transition-transform ' + (isExpanded ? 'rotate-90' : '')}>{'>'}</span>
                        <span className="text-ngsat-text font-medium truncate">{s.name}({s.code})</span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className={'num font-mono ' + (
                          s.score >= 70 ? 'text-ngsat-green' : s.score >= 60 ? 'text-ngsat-yellow' : 'text-ngsat-muted'
                        )}>{s.score}</span>
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="ml-5 pl-3 border-l-2 border-ngsat-border py-2 space-y-1.5 text-xs">
                        <ScoreBar label="RSI" raw={ind.rsi} />
                        <ScoreBar label="MFI" raw={ind.mfi} />
                        <ScoreBar label="ADX" raw={ind.adx} extra={'DI+' + (ind.di_plus?.toFixed(0)||'?') + '/' + (ind.di_minus?.toFixed(0)||'?')} />
                        <ScoreBar label="OBV" raw={ind.obv_slope} />
                        <ScoreBar label="MA" raw={ind.ma5} extra={'MA5 ' + (ind.ma5?.toFixed(0)||'?') + ' MA20 ' + (ind.ma20?.toFixed(0)||'?')} />
                        <ScoreBar label="거래량" raw={ind.volume_ratio} />
                        <ScoreBar label="스토캐스틱" raw={ind.stochastic_k} />
                        <ScoreBar label="ATR" raw={ind.atr_pct} suffix="%" />
                        {ind.rs != null && <ScoreBar label="RS" raw={ind.rs} />}
                        <div className="pt-1 text-[10px] text-ngsat-muted border-t border-ngsat-border/50 mt-1">{s.reason}</div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* ML Predictions */}
        <div className="ngsat-card p-4">
          <h4 className="text-sm font-medium text-ngsat-text mb-3">
            🤖 ML 예측 ({predictions.length}건)
          </h4>
          <div className="text-[10px] text-ngsat-muted mb-2 flex items-center gap-3">
            <span>🟢 매수</span><span>🟡 홀드</span><span>🔴 관망</span>
            <span className="ml-auto">% = 상승 확률</span>
          </div>
          {predictions.length === 0 ? (
            <p className="text-xs text-ngsat-muted">예측 결과 없음</p>
          ) : (
            <div className="space-y-1.5 max-h-80 overflow-y-auto">
              {predictions.map((p, i) => (
                <div key={i} className={`flex items-center justify-between text-xs py-1.5 px-2 rounded ${
                  p.action === 'buy' ? 'bg-ngsat-green/10' : p.action === 'hold' ? '' : 'bg-ngsat-red/5'
                }`}>
                  <div className="flex items-center gap-2 min-w-0">
                    <span>{p.action === 'buy' ? '🟢' : p.action === 'hold' ? '🟡' : '🔴'}</span>
                    <span className="text-ngsat-text font-medium truncate">{p.name}({p.code})</span>
                  </div>
                  <span className={`shrink-0 num font-mono ${
                    p.action === 'buy' ? 'text-ngsat-green' : 'text-ngsat-muted'
                  }`}>
                    {(p.probability * 100).toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Deferred Entries */}
      {deferred.length > 0 && (
        <div className="ngsat-card p-4">
          <h4 className="text-sm font-medium text-ngsat-text mb-2">⏱️ 진입 보류 ({deferred.length}건)</h4>
          <div className="space-y-1">
            {deferred.map((d, i) => (
              <div key={i} className="flex items-center justify-between text-xs py-1 px-2 bg-ngsat-yellow/5 rounded">
                <span className="text-ngsat-text">{d.name}({d.code})</span>
                <span className="text-ngsat-muted">{d.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Mode Decision */}
      {data.mode_decision && (
        <div className="ngsat-card p-3 text-xs text-ngsat-muted">
          <span className="text-ngsat-text font-medium">모드 선택 근거:</span> {data.mode_decision.reason}
          <span className="ml-2">(신뢰도 {(data.mode_decision.confidence * 100).toFixed(0)}%)</span>
        </div>
      )}
    </div>
  )
}

/** P-60: 지표별 점수 바 — raw 값을 0~100% 막대로 표시 */
function ScoreBar({ label, raw, extra, suffix }) {
  const val = (raw != null && !isNaN(raw)) ? raw : null
  // Normalize to 0~100 for bar width
  let pct = 50
  if (val != null) {
    if (label === 'RSI' || label === 'MFI' || label === '스토캐스틱') {
      pct = Math.min(100, Math.max(0, val))
    } else if (label === 'ADX') {
      pct = Math.min(100, Math.max(0, val * 2))
    } else if (label === '거래량') {
      pct = Math.min(100, Math.max(0, (val - 0.5) * 100))
    } else if (label === 'OBV') {
      pct = 50 + Math.min(50, Math.max(-50, val * 5))
    } else if (label === 'ATR') {
      pct = Math.min(100, Math.max(0, val * 30))
    } else if (label === 'MA') {
      pct = 50 + Math.min(50, Math.max(-50, (val - 48000) / 200))
    } else {
      pct = Math.min(100, Math.max(0, (val + 1) * 50))
    }
  }
  const color = pct >= 70 ? 'bg-ngsat-green' : pct >= 40 ? 'bg-ngsat-yellow' : 'bg-ngsat-red'
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 text-ngsat-muted shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-ngsat-border/30 rounded-full overflow-hidden">
        <div className={'h-full rounded-full transition-all ' + color} style={{width: pct + '%'}} />
      </div>
      <span className="w-12 text-right num text-ngsat-text font-mono">{val != null ? val.toFixed(1) + (suffix || '') : '?'}</span>
      {extra && <span className="text-[9px] text-ngsat-muted hidden lg:block">{extra}</span>}
    </div>
  )
}
