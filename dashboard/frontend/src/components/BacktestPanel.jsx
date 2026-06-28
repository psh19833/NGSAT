import { useState, useRef, useEffect } from 'react'
import SkeletonCard from './SkeletonCard.jsx'

export default function BacktestPanel({ api }) {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(null)
  const pollRef = useRef(null)

  const runBacktest = async () => {
    setLoading(true)
    setResult(null)
    setProgress({ pct: 0, label: '데이터 로드 중...' })

    try {
      const resp = await api.runBacktest()
      if (resp?.status === 'completed' && resp?.result) {
        setResult(resp.result)
        setProgress(null)
      } else {
        setProgress({ pct: 0, label: '실패: ' + (resp?.message || '알 수 없는 오류') })
      }
    } catch (e) {
      setProgress({ pct: 0, label: '오류: ' + e.message })
    } finally {
      setLoading(false)
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  return (
    <div className="max-w-4xl space-y-6">
      {/* Title */}
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-4">백테스트 실행</h3>
        <div className="flex items-center gap-4">
          <button
            onClick={runBacktest}
            disabled={loading}
            className="px-6 py-2.5 rounded-lg text-sm font-medium transition-all
              bg-ngsat-accent text-white hover:opacity-90
              disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {loading ? '실행 중...' : '🚀 백테스트 실행'}
          </button>
          <span className="text-xs text-ngsat-muted">
            현재 전략설정 값을 기준으로 실행됩니다
          </span>
        </div>
        {progress && (
          <div className="mt-4">
            <div className="w-full bg-ngsat-border rounded h-2">
              <div className="bg-ngsat-accent h-2 rounded transition-all" style={{ width: `${progress.pct}%` }} />
            </div>
            <p className="text-xs text-ngsat-muted mt-2">{progress.label}</p>
          </div>
        )}
      </div>

      {/* Result */}
      {loading && !result && (
        <SkeletonCard lines={6} />
      )}

      {result && (
        <>
          {/* Summary */}
          <div className="ngsat-card p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm text-ngsat-muted">📊 백테스트 결과</h3>
              <span className={`text-xs px-2 py-1 rounded ${
                result.data_source === 'real' ? 'bg-ngsat-green/10 text-ngsat-green' : 'bg-ngsat-yellow/10 text-ngsat-yellow'
              }`}>
                {result.data_source_label}
              </span>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
              <div>
                <div className="text-xs text-ngsat-muted mb-1">수익률</div>
                <div className={`text-lg font-semibold ${result.total_return_pct >= 0 ? 'text-ngsat-green' : 'text-ngsat-red'}`}>
                  {result.total_return_pct >= 0 ? '+' : ''}{result.total_return_pct}%
                </div>
              </div>
              <div>
                <div className="text-xs text-ngsat-muted mb-1">승률</div>
                <div className="text-lg font-semibold text-ngsat-text">{result.win_rate}%</div>
              </div>
              <div>
                <div className="text-xs text-ngsat-muted mb-1">MDD</div>
                <div className="text-lg font-semibold text-ngsat-red">{result.max_drawdown_pct}%</div>
              </div>
              <div>
                <div className="text-xs text-ngsat-muted mb-1">거래 횟수</div>
                <div className="text-lg font-semibold text-ngsat-text">{result.total_trades}회</div>
              </div>
            </div>

            <div className="text-xs text-ngsat-muted space-y-1">
              <p>기간: {result.start_date} ~ {result.end_date}</p>
              <p>초기: {result.initial_capital?.toLocaleString()}원 → 최종: {result.final_capital?.toLocaleString()}원</p>
              <p>모드: 스윙 {result.swing_days}일 / 단타 {result.short_term_days}일 / 관망 {result.hold_days}일</p>
            </div>
          </div>

          {/* Trades */}
          {result.trades && result.trades.length > 0 && (
            <div className="ngsat-card p-6">
              <h3 className="text-sm text-ngsat-muted mb-4">거래 내역 (총 {result.trades.length}건)</h3>
              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-ngsat-muted text-xs border-b border-ngsat-border sticky top-0 bg-ngsat-card">
                      <th className="text-left py-2 px-2 font-medium">일시</th>
                      <th className="text-left py-2 px-2 font-medium">종목</th>
                      <th className="text-center py-2 px-2 font-medium">구분</th>
                      <th className="text-right py-2 px-2 font-medium">수량</th>
                      <th className="text-right py-2 px-2 font-medium">가격</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.slice(0, 50).map((t, i) => (
                      <tr key={i} className="border-b border-ngsat-border/30 hover:bg-ngsat-border/10">
                        <td className="py-2 px-2 text-xs text-ngsat-muted">{t.date}</td>
                        <td className="py-2 px-2 text-ngsat-text">{t.code}</td>
                        <td className="py-2 px-2 text-center">
                          <span className={`px-2 py-0.5 text-xs rounded ${
                            t.side === 'buy' ? 'bg-ngsat-green/10 text-ngsat-green' : 'bg-ngsat-red/10 text-ngsat-red'
                          }`}>{t.side === 'buy' ? '매수' : '매도'}</span>
                        </td>
                        <td className="text-right py-2 px-2 text-ngsat-text">{t.quantity}</td>
                        <td className="text-right py-2 px-2 text-ngsat-muted">{t.price?.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {result.trades.length > 50 && (
                  <p className="text-xs text-ngsat-muted text-center mt-2">최근 50건만 표시</p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
