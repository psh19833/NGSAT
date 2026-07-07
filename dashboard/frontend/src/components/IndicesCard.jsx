import { formatNumber } from '../utils.js'

export default function IndicesCard({ indices }) {
  if (!indices || Object.keys(indices).length === 0) return null

  const labels = {
    kospi: 'KOSPI', kosdaq: 'KOSDAQ',
    sp500: 'S&P 500', nasdaq: 'NASDAQ', dow: 'DOW',
  }

  return (
    <div className="ngsat-card p-6">
      <h3 className="text-sm text-ngsat-muted mb-4">주요 지수</h3>
      <div className="space-y-2">
        {Object.entries(indices).map(([key, val]) => (
          <div key={key} className="flex items-center justify-between text-xs">
            <span className="text-ngsat-text font-medium">{labels[key] || key}</span>
            <span className={`num font-mono ${val.change_pct >= 0 ? 'text-ngsat-green' : 'text-ngsat-red'}`}>
              {formatNumber(val.price)} 
              <span className="text-[10px]">
                ({val.change_pct >= 0 ? '+' : ''}{val.change_pct?.toFixed(1) || '0.0'}%)
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
