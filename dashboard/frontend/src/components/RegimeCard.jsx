import { regimeColor, regimeLabel } from '../utils.js'

export default function RegimeCard({ regime }) {
  if (!regime || regime.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">시장 레짐</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  const r = regime.regime || 'unknown'
  const score = regime.score || 0
  const kr = regimeLabel(r)

  // Score bar: 0-100
  const barWidth = Math.min(100, Math.max(0, score))
  const isSkipped = regime.regime_skipped

  return (
    <div className="ngsat-card p-6">
      <h3 className="text-sm text-ngsat-muted mb-3">시장 레짐</h3>
      {isSkipped ? (
        <div className="text-center py-4">
          <span className="text-lg font-semibold text-ngsat-yellow">장 종료</span>
          <p className="text-xs text-ngsat-muted mt-2">정규장 시간에만 레짐이 평가됩니다</p>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between mb-4">
            <span className={`text-2xl font-bold ${regimeColor(r)}`}>
              {kr}
            </span>
            <span className="num text-lg text-ngsat-text">
              {score.toFixed(0)}<span className="text-sm text-ngsat-muted">/100</span>
            </span>
          </div>
          {/* Score bar */}
          <div className="h-2 bg-ngsat-border rounded-full overflow-hidden mb-3">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                r === 'bull' ? 'bg-ngsat-green' :
                r === 'bear' ? 'bg-ngsat-red' :
                'bg-ngsat-yellow'
              }`}
              style={{ width: `${barWidth}%` }}
            />
          </div>
          {regime.reason && (
            <p className="text-xs text-ngsat-muted leading-relaxed line-clamp-2">
              {regime.reason}
            </p>
          )}
        </>
      )}
    </div>
  )
}
