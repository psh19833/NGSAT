import { formatNumber, formatPercent, formatWon, pnlColor } from '../utils.js'
import EquityChart from './EquityChart.jsx'

export default function AccountCard({ account, detailed = false }) {
  if (!account || account.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">계좌 현황</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  if (account.error) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">계좌 현황</h3>
        <p className="text-ngsat-red text-sm">{account.error}</p>
      </div>
    )
  }

  const stats = [
    { label: '총 자산', value: formatWon(account.total_asset), color: 'text-ngsat-text' },
    { label: '예수금', value: formatWon(account.deposit), color: 'text-ngsat-text' },
    { label: '평가 금액', value: formatWon(account.total_eval), color: 'text-ngsat-text' },
    {
      label: '평가 손익',
      value: `${formatNumber(account.total_profit_loss)}원 (${formatPercent(account.total_profit_loss_pct)})`,
      color: pnlColor(account.total_profit_loss),
    },
  ]

  if (detailed) {
    stats.push(
      { label: '당일 손실', value: formatWon(Math.abs(account.daily_loss)), color: pnlColor(-account.daily_loss) },
      { label: '당일 손실률', value: formatPercent(Math.abs(account.daily_loss_pct)), color: pnlColor(-account.daily_loss_pct) },
    )
  }

  return (
    <div className="ngsat-card p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm text-ngsat-muted">계좌 현황</h3>
        {account.equity_history && <EquityChart data={account.equity_history} height={60} />}
      </div>
      <div className={`grid gap-4 ${detailed ? 'grid-cols-2 md:grid-cols-3' : 'grid-cols-2 md:grid-cols-4'}`}>
        {stats.map(stat => (
          <div key={stat.label}>
            <p className="text-xs text-ngsat-muted mb-1">{stat.label}</p>
            <p className={`num text-lg font-semibold ${stat.color}`}>
              {stat.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}
