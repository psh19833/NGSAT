import { useState, useEffect, useRef } from 'react'
import SkeletonCard from './SkeletonCard.jsx'

export default function DiagnosisPanel({ api }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
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
  }, [])

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
            <div className="space-y-1.5 max-h-80 overflow-y-auto">
              {screened.map((s, i) => (
                <div key={i} className={`flex items-center justify-between text-xs py-1.5 px-2 rounded ${
                  s.score >= 70 ? 'bg-ngsat-green/5' : s.score >= 60 ? 'bg-ngsat-yellow/5' : ''
                }`}>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-ngsat-text font-medium truncate">{s.name}</span>
                    <span className="text-ngsat-muted">{s.code}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className={`num font-mono ${
                      s.score >= 70 ? 'text-ngsat-green' : s.score >= 60 ? 'text-ngsat-yellow' : 'text-ngsat-muted'
                    }`}>{s.score}점</span>
                  </div>
                </div>
              ))}
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
                    <span className="text-ngsat-text font-medium truncate">{p.name}</span>
                    <span className="text-ngsat-muted">{p.code}</span>
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
