// NGSAT Dashboard API client

const BASE = '/api'

async function fetchJSON(endpoint, options = {}) {
  try {
    const resp = await fetch(`${BASE}${endpoint}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
    const data = await resp.json()
    return data
  } catch (e) {
    return { error: e.message, connected: false }
  }
}

export const api = {
  getStatus: () => fetchJSON('/status'),
  getAccount: () => fetchJSON('/account'),
  getPositions: () => fetchJSON('/positions'),
  getTrades: () => fetchJSON('/trades'),
  getRegime: () => fetchJSON('/regime'),

  start: () => fetchJSON('/control/start', { method: 'POST' }),
  stop: () => fetchJSON('/control/stop', { method: 'POST' }),
  shutdown: () => fetchJSON('/control/shutdown', { method: 'POST' }),
  restart: () => fetchJSON('/control/restart', { method: 'POST' }),
  forceSell: (code) => fetchJSON('/control/forcesell', {
    method: 'POST',
    body: JSON.stringify({ code }),
  }),
  forceHold: (code) => fetchJSON('/control/forcehold', {
    method: 'POST',
    body: JSON.stringify({ code }),
  }),

  getDiagnosis: () => fetchJSON('/diagnosis'),
  getStrategyConfig: () => fetchJSON('/strategy/config'),
  updateStrategyConfig: (data) => fetchJSON('/strategy/config', {
    method: 'PUT',
    body: JSON.stringify(data),
  }),

  runBacktest: () => fetchJSON('/backtest/run', { method: 'POST' }),
  getBacktestState: () => fetchJSON('/backtest/state'),
  getBacktestResults: () => fetchJSON('/backtest/results'),
}
