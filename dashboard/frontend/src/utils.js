// Utility functions for formatting

export function formatNumber(n) {
  if (n == null || isNaN(n)) return '—'
  return new Intl.NumberFormat('ko-KR').format(Math.round(n))
}

export function formatPercent(n, sign = true) {
  if (n == null || isNaN(n)) return '—'
  const prefix = sign && n > 0 ? '+' : ''
  return `${prefix}${n.toFixed(1)}%`
}

export function formatWon(n) {
  if (n == null || isNaN(n)) return '—'
  return `${formatNumber(n)}원`
}

export function pnlColor(n) {
  if (n > 0) return 'text-ngsat-green'
  if (n < 0) return 'text-ngsat-red'
  return 'text-ngsat-muted'
}

export function regimeColor(regime) {
  if (regime === 'bull') return 'text-ngsat-green'
  if (regime === 'bear') return 'text-ngsat-red'
  return 'text-ngsat-yellow'
}

export function regimeLabel(regime) {
  if (regime === 'bull') return '강세장'
  if (regime === 'bear') return '약세장'
  if (regime === 'neutral') return '중립장'
  return '알 수 없음'
}

export function stateColor(state) {
  if (state === 'running') return 'text-ngsat-green'
  if (state === 'paused') return 'text-ngsat-yellow'
  if (state === 'halted') return 'text-ngsat-red'
  if (state === 'shutdown') return 'text-ngsat-muted'
  return 'text-ngsat-muted'
}

export function stateLabel(state) {
  const labels = {
    idle: '대기',
    running: '운영 중',
    paused: '일시정지',
    halted: '자동중단',
    shutdown: '종료',
  }
  return labels[state] || state
}
