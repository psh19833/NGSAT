import { useState, useEffect, useRef } from 'react'
import EvidenceBox from './EvidenceBox.jsx'
import Pagination from './Pagination.jsx'

const PAGE_SIZE = 20

export default function TradesTable({ trades: propTrades, api }) {
  const [page, setPage] = useState(1)
  const [localData, setLocalData] = useState(null)
  const [localTotal, setLocalTotal] = useState(0)
  const prevApiRef = useRef(null)

  // Use prop trades when available, otherwise fetch with pagination
  const hasApi = !!api
  const apiChanged = prevApiRef.current !== api
  prevApiRef.current = api

  useEffect(() => {
    if (!hasApi) return
    let mounted = true

    const fetch = async () => {
      const resp = await api.getTrades(PAGE_SIZE, (page - 1) * PAGE_SIZE)
      if (mounted && resp?.connected) {
        setLocalData(resp)
        setLocalTotal(resp.total || 0)
      }
    }
    fetch()
    return () => { mounted = false }
  }, [hasApi, page, api, apiChanged])

  // Use prop data if provided (from App.jsx refreshAll), otherwise use local paginated data
  const data = propTrades || localData
  const total = propTrades ? (propTrades.total || 0) : localTotal

  if (!data || data.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">거래 내역</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  const tradeList = data.trades || []

  if (tradeList.length === 0) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">거래 내역</h3>
        <div className="text-center py-8">
          <p className="text-ngsat-muted text-sm">
            {data.message || '거래 내역이 없습니다'}
          </p>
        </div>
      </div>
    )
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="ngsat-card p-6">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm text-ngsat-muted">거래 내역</h3>
        <span className="text-xs text-ngsat-muted">총 {total}건</span>
      </div>
      <p className="text-xs text-ngsat-muted mb-4">행을 클릭하면 상세 근거를 볼 수 있습니다</p>
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
              <TradesRow key={t.id || i} trade={t} index={i} />
            ))}
          </tbody>
        </table>
      </div>
      {hasApi && (
        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      )}
    </div>
  )
}

function TradesRow({ trade: t, index: i }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <tr
        onClick={() => setExpanded(!expanded)}
        className={`border-b border-ngsat-border/50 cursor-pointer transition-colors
          ${expanded
            ? 'bg-ngsat-accent/10 hover:bg-ngsat-accent/15'
            : 'hover:bg-ngsat-border/20'
          }`}
      >
        <td className="py-3 px-3 num text-ngsat-muted text-xs">{t.date}</td>
        <td className="py-3 px-3 text-ngsat-text font-medium">{t.name}({t.code})</td>
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
        <td className="py-3 px-3 text-xs text-ngsat-muted max-w-[200px] truncate" title={t.reason}>
          {t.reason}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-ngsat-border/50">
          <td colSpan={7} className="p-0">
            <div className="bg-ngsat-bg px-6 py-4 space-y-3 border-l-2 border-ngsat-accent">
              <div>
                <div className="text-xs text-ngsat-muted mb-1">매매 근거</div>
                <div className="text-sm text-ngsat-text leading-relaxed whitespace-pre-wrap">
                  {t.reason || '—'}
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <div>
                  <div className="text-xs text-ngsat-muted">구분</div>
                  <div className={`font-medium ${
                    t.side === 'buy' ? 'text-ngsat-green' : 'text-ngsat-red'
                  }`}>
                    {t.action || t.side}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-ngsat-muted">총 거래금액</div>
                  <div className="num text-ngsat-text font-medium">
                    {t.amount?.toLocaleString()}원
                  </div>
                </div>
                <div>
                  <div className="text-xs text-ngsat-muted">체결가</div>
                  <div className="num text-ngsat-text">{t.price?.toLocaleString()}원</div>
                </div>
                <div>
                  <div className="text-xs text-ngsat-muted">수량</div>
                  <div className="num text-ngsat-text">{t.quantity}주</div>
                </div>
                {t.mode && (
                  <div>
                    <div className="text-xs text-ngsat-muted">모드</div>
                    <div className="text-ngsat-text">{t.mode}</div>
                  </div>
                )}
              </div>
              {t.evidence && Object.keys(t.evidence).length > 0 && (
                <EvidenceBox evidence={t.evidence} />
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
