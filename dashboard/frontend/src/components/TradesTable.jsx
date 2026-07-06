import { useState, useEffect, useMemo } from 'react'
import EvidenceBox from './EvidenceBox.jsx'
import Pagination from './Pagination.jsx'

const PAGE_SIZE = 20
const DAY_NAMES = ['일', '월', '화', '수', '목', '금', '토']

// ── Date grouping logic ──
function groupTradesByDate(trades) {
  const map = {}
  for (const t of trades) {
    // Extract date part ("2026-05-04" from "2026-05-04 09:30")
    const dateKey = t.date ? t.date.substring(0, 10) : '날짜 없음'
    if (!map[dateKey]) {
      map[dateKey] = { date: dateKey, trades: [], buyCount: 0, sellCount: 0, buyAmount: 0, sellAmount: 0 }
    }
    const group = map[dateKey]
    group.trades.push(t)
    if (t.side === 'buy') {
      group.buyCount++
      group.buyAmount += (t.amount || 0)
    } else {
      group.sellCount++
      group.sellAmount += (t.amount || 0)
    }
  }

  // Sort groups reverse chronological; sort trades within each group by time
  return Object.values(map)
    .sort((a, b) => b.date.localeCompare(a.date))
    .map(g => {
      const dt = new Date(g.date + 'T00:00:00')
      const dayOfWeek = DAY_NAMES[dt.getDay()]
      const dow = dt.getDay()
      // Weekend coloring hint
      return {
        ...g,
        dayOfWeek,
        isWeekend: dow === 0 || dow === 6,
        trades: g.trades.sort((a, b) => (a.date || '').localeCompare(b.date || '')),
      }
    })
}

// ── Components ──
function DateGroupHeader({ group, isOpen, onToggle }) {
  const isWeekend = group.isWeekend
  return (
    <button
      onClick={onToggle}
      className={`w-full flex items-center gap-3 px-3 py-2 text-left transition-colors
        ${isWeekend
          ? 'bg-ngsat-border/15 text-ngsat-muted'
          : 'bg-ngsat-bg/80 text-ngsat-text'
        } hover:bg-ngsat-accent/5 border-b border-ngsat-border`}
    >
      {/* Expand indicator */}
      <svg className={`w-3 h-3 transition-transform flex-shrink-0 ${isOpen ? 'rotate-90' : ''}`}
        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
      </svg>

      {/* Date + day */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-semibold tabular-nums">{group.date}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
          isWeekend ? 'bg-ngsat-border/20' : 'bg-ngsat-accent/10 text-ngsat-accent'
        }`}>
          {group.dayOfWeek}요일
        </span>
      </div>

      <div className="flex-1" />

      {/* Summary stats */}
      <div className="flex items-center gap-3 text-xs tabular-nums">
        {group.buyCount > 0 && (
          <span className="text-ngsat-green/80">
            매수 {group.buyCount}건
          </span>
        )}
        {group.sellCount > 0 && (
          <span className="text-ngsat-red/80">
            매도 {group.sellCount}건
          </span>
        )}
        <span className="text-ngsat-muted">
          {(group.buyAmount + group.sellAmount).toLocaleString()}원
        </span>
      </div>
    </button>
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
        <td className="py-2.5 px-3 num text-ngsat-muted text-xs whitespace-nowrap">
          {t.date ? t.date.substring(11, 16) : '--:--'}
        </td>
        <td className="py-2.5 px-3 text-ngsat-text font-medium whitespace-nowrap">
          {t.name}({t.code})
        </td>
        <td className="py-2.5 px-3 text-center">
          <span className={`px-2 py-0.5 text-xs rounded ${
            t.side === 'buy' ? 'bg-ngsat-green/10 text-ngsat-green' : 'bg-ngsat-red/10 text-ngsat-red'
          }`}>
            {t.side === 'buy' ? '매수' : '매도'}
          </span>
        </td>
        <td className="text-right py-2.5 px-3 num text-ngsat-text">{t.quantity}</td>
        <td className="text-right py-2.5 px-3 num text-ngsat-text">{t.price?.toLocaleString()}</td>
        <td className="text-right py-2.5 px-3 num text-ngsat-muted">{t.amount?.toLocaleString()}</td>
        <td className="py-2.5 px-3 text-xs text-ngsat-muted max-w-[180px] truncate hidden md:table-cell" title={t.reason}>
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

function GroupContent({ group }) {
  const [open, setOpen] = useState(true) // default: expanded

  return (
    <div className="border-b border-ngsat-border/40">
      <DateGroupHeader group={group} isOpen={open} onToggle={() => setOpen(!open)} />
      {open && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-ngsat-muted text-xs border-b border-ngsat-border/30">
              <th className="text-left py-1.5 px-3 font-medium w-[60px]">시간</th>
              <th className="text-left py-1.5 px-3 font-medium">종목</th>
              <th className="text-center py-1.5 px-3 font-medium w-[52px]">구분</th>
              <th className="text-right py-1.5 px-3 font-medium w-[48px]">수량</th>
              <th className="text-right py-1.5 px-3 font-medium w-[90px]">가격</th>
              <th className="text-right py-1.5 px-3 font-medium w-[100px]">금액</th>
              <th className="text-left py-1.5 px-3 font-medium max-w-[180px] hidden md:table-cell">근거</th>
            </tr>
          </thead>
          <tbody>
            {group.trades.map((t, i) => (
              <TradesRow key={t.id || `${t.date}-${i}`} trade={t} index={i} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Main ──
export default function TradesTable({ trades: propTrades, api }) {
  const [page, setPage] = useState(1)
  const [localData, setLocalData] = useState(null)
  const [localTotal, setLocalTotal] = useState(0)

  const hasApi = !!api

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
  }, [hasApi, page, api])

  const data = propTrades || localData
  const total = propTrades ? (propTrades.total || 0) : localTotal
  const tradeList = (data && data.connected !== false) ? (data.trades || []) : []

  // useMemo must be before any early return (React Hooks rule)
  const PAGE_SIZE = 20
  const groups = useMemo(() => groupTradesByDate(tradeList), [tradeList])
  const totalPages = Math.ceil(total / PAGE_SIZE)

  if (!data || data.connected === false) {
    return (
      <div className="ngsat-card p-6">
        <h3 className="text-sm text-ngsat-muted mb-3">거래 내역</h3>
        <p className="text-ngsat-muted">—</p>
      </div>
    )
  }

  return (
    <div className="ngsat-card p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm text-ngsat-muted">거래 내역</h3>
        <span className="text-xs text-ngsat-muted">
          {groups.length}일 / 총 {total}건
        </span>
      </div>
      <div className="-mx-6">
        {groups.map(group => (
          <GroupContent key={group.date} group={group} />
        ))}
      </div>
      {hasApi && (
        <div className="mt-4">
          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </div>
      )}
    </div>
  )
}
