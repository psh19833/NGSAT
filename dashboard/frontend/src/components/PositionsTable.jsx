import { formatNumber, formatPercent, formatWon, pnlColor } from '../utils.js'

export default function PositionsTable({ positions, onAction, detailed = false }) {
  if (!positions || positions.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">보유 포지션</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  const pos = positions.positions || []

  if (pos.length === 0) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">보유 포지션</h3>
        <div className="text-center py-8">
          <p className="text-ngsat-muted text-sm">보유 중인 포지션이 없습니다</p>
        </div>
      </div>
    )
  }

  return (
    <div className="ngsat-card p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm text-ngsat-muted">보유 포지션 ({pos.length}개)</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-ngsat-muted text-xs border-b border-ngsat-border">
              <th className="text-left py-2 px-3 font-medium">종목</th>
              <th className="text-right py-2 px-3 font-medium">수량</th>
              <th className="text-right py-2 px-3 font-medium">매수가</th>
              <th className="text-right py-2 px-3 font-medium">현재가</th>
              <th className="text-right py-2 px-3 font-medium">평가손익</th>
              <th className="text-right py-2 px-3 font-medium">수익률</th>
              <th className="text-center py-2 px-3 font-medium">손절선</th>
              {detailed && <th className="text-center py-2 px-3 font-medium">관리</th>}
            </tr>
          </thead>
          <tbody>
            {pos.map(p => (
              <tr key={p.code} className="border-b border-ngsat-border/50 hover:bg-ngsat-border/20 transition-colors">
                <td className="py-3 px-3">
                  <div className="flex items-center gap-2">
                    <div>
                      <p className="text-ngsat-text font-medium">{p.name}</p>
                      <p className="text-xs text-ngsat-muted">{p.code} · {p.market}</p>
                    </div>
                    {p.is_force_hold && (
                      <span className="px-1.5 py-0.5 text-xs bg-ngsat-yellow/10 text-ngsat-yellow rounded">홀드</span>
                    )}
                  </div>
                </td>
                <td className="text-right py-3 px-3 num text-ngsat-text">{p.quantity}</td>
                <td className="text-right py-3 px-3 num text-ngsat-muted">{formatNumber(p.buy_price)}</td>
                <td className="text-right py-3 px-3 num text-ngsat-text">{formatNumber(p.current_price)}</td>
                <td className={`text-right py-3 px-3 num ${pnlColor(p.profit_loss)}`}>
                  {formatNumber(p.profit_loss)}원
                </td>
                <td className={`text-right py-3 px-3 num font-semibold ${pnlColor(p.profit_loss_pct)}`}>
                  {formatPercent(p.profit_loss_pct)}
                </td>
                <td className="text-center py-3 px-3 num text-ngsat-muted">
                  -{p.stop_loss_pct.toFixed(1)}%
                </td>
                {detailed && (
                  <td className="text-center py-3 px-3">
                    <div className="flex justify-center gap-1.5">
                      <button
                        onClick={() => onAction('forcesell', p.code)}
                        className="px-2 py-1 text-xs text-ngsat-red hover:bg-ngsat-red/10 rounded transition-colors"
                      >
                        강제매도
                      </button>
                      {!p.is_force_hold && (
                        <button
                          onClick={() => onAction('forcehold', p.code)}
                          className="px-2 py-1 text-xs text-ngsat-yellow hover:bg-ngsat-yellow/10 rounded transition-colors"
                        >
                          홀드
                        </button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
