import { formatNumber, formatWon, pnlColor } from '../utils.js'

function TradeRow({ trade }) {
  const isBuy = trade.side === 'buy'
  return (
    <div className="flex items-center justify-between text-xs py-1.5 border-b border-ngsat-border/30 last:border-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isBuy ? 'bg-blue-400' : 'bg-red-400'}`} />
        <span className="text-ngsat-text truncate">{trade.name}({trade.code})</span>
        <span className={`${isBuy ? 'text-blue-400' : 'text-red-400'} font-medium`}>
          {isBuy ? '매수' : '매도'}
        </span>
      </div>
      <div className="flex items-center gap-3 flex-shrink-0 text-ngsat-muted">
        <span>{trade.qty}주</span>
        <span className="font-mono">{formatWon(trade.price)}</span>
      </div>
    </div>
  )
}

export default function PnLCard({ data }) {
  if (!data || data.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">일일 손익</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  const summary = data.summary || {}
  const daily = data.daily || []

  return (
    <div className="space-y-6">
      {/* Summary */}
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-4">전체 손익 요약</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-ngsat-muted mb-1">순손익</p>
            <p className={`num text-lg font-semibold ${pnlColor(summary.total_pnl)}`}>
              {formatWon(summary.total_pnl)}
            </p>
          </div>
          <div>
            <p className="text-xs text-ngsat-muted mb-1">실현손익</p>
            <p className={`num text-lg font-semibold ${pnlColor(summary.total_realized)}`}>
              {formatWon(summary.total_realized)}
            </p>
          </div>
          <div>
            <p className="text-xs text-ngsat-muted mb-1">추정 제세금</p>
            <p className="num text-lg font-semibold text-ngsat-red">
              {formatWon(summary.total_fees)}
            </p>
          </div>
          <div>
            <p className="text-xs text-ngsat-muted mb-1">거래일 / 총거래</p>
            <p className="num text-lg font-semibold text-ngsat-text">
              {summary.trade_days}일 / {summary.total_trades}건
            </p>
          </div>
          <div>
            <p className="text-xs text-ngsat-muted mb-1">일평균 손익</p>
            <p className={`num text-lg font-semibold ${pnlColor(summary.avg_daily_pnl)}`}>
              {formatWon(summary.avg_daily_pnl)}
            </p>
          </div>
          <div>
            <p className="text-xs text-ngsat-muted mb-1">수익일</p>
            <p className="num text-lg font-semibold text-ngsat-green">{summary.win_days}일</p>
          </div>
          <div>
            <p className="text-xs text-ngsat-muted mb-1">손실일</p>
            <p className="num text-lg font-semibold text-ngsat-red">{summary.lose_days}일</p>
          </div>
        </div>
      </div>

      {/* Daily Detail */}
      <div className="space-y-4">
        {daily.length === 0 && (
          <div className="ngsat-card p-6 text-center">
            <p className="text-ngsat-muted text-sm">거래 내역이 없습니다</p>
          </div>
        )}
        {daily.map(day => (
          <div key={day.date} className="ngsat-card p-5">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-sm font-medium text-ngsat-text">{day.date}</h4>
              <div className="flex items-center gap-4 text-xs">
                <span className="text-ngsat-muted">{day.trade_count}건</span>
                <span className="text-ngsat-muted">승률 {day.win_rate}%</span>
                <span className={`font-semibold font-mono ${pnlColor(day.net_pnl)}`}>
                  {formatWon(day.net_pnl)}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-4 text-xs text-ngsat-muted mb-2 px-1">
              <span className="text-ngsat-green">실현 {formatWon(day.realized_pnl)}</span>
              <span className="text-ngsat-red">세금 {formatWon(day.fee_estimate)}</span>
            </div>
            <div className="border border-ngsat-border rounded-lg px-3 py-1 divide-y divide-ngsat-border/30">
              {day.trades.map((t, i) => (
                <TradeRow key={i} trade={t} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
