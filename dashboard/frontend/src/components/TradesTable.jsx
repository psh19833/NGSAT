export default function TradesTable({ trades }) {
  if (!trades || trades.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">거래 내역</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  const tradeList = trades.trades || []

  if (tradeList.length === 0) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">거래 내역</h3>
        <div className="text-center py-8">
          <p className="text-ngsat-muted text-sm">
            {trades.message || '거래 내역이 없습니다'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="ngsat-card p-6">
      <h3 className="text-sm text-ngsat-muted mb-4">거래 내역</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-ngsat-muted text-xs border-b border-ngsat-border">
              <th className="text-left py-2 px-3 font-medium">일시</th>
              <th className="text-left py-2 px-3 font-medium">종목</th>
              <th className="text-center py-2 px-3 font-medium">구분</th>
              <th className="text-right py-2 px-3 font-medium">수량</th>
              <th className="text-right py-2 px-3 font-medium">가격</th>
              <th className="text-right py-2 px-3 font-medium">금액</th>
              <th className="text-left py-2 px-3 font-medium">근거</th>
            </tr>
          </thead>
          <tbody>
            {tradeList.map((t, i) => (
              <tr key={i} className="border-b border-ngsat-border/50 hover:bg-ngsat-border/20">
                <td className="py-3 px-3 num text-ngsat-muted text-xs">{t.date}</td>
                <td className="py-3 px-3 text-ngsat-text">{t.name}({t.code})</td>
                <td className="py-3 px-3 text-center">
                  <span className={`px-2 py-0.5 text-xs rounded ${
                    t.side === 'buy' ? 'bg-ngsat-green/10 text-ngsat-green' : 'bg-ngsat-red/10 text-ngsat-red'
                  }`}>
                    {t.side === 'buy' ? '매수' : '매도'}
                  </span>
                </td>
                <td className="text-right py-3 px-3 num text-ngsat-text">{t.quantity}</td>
                <td className="text-right py-3 px-3 num text-ngsat-text">{t.price?.toLocaleString()}</td>
                <td className="text-right py-3 px-3 num text-ngsat-muted">{t.amount?.toLocaleString()}</td>
                <td className="py-3 px-3 text-xs text-ngsat-muted max-w-xs truncate" title={t.reason}>
                  {t.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
