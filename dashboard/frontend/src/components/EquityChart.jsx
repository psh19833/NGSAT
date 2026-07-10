import { LineChart, Line, Area, BarChart, Bar, ResponsiveContainer, YAxis, XAxis, Tooltip, CartesianGrid } from 'recharts'

export default function EquityChart({ data, height = 80, type = 'line' }) {
  if (!data || data.length < 2) return null

  if (type === 'bar') {
    // Daily P&L bar chart
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
          <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#8b8e98' }} hide />
          <YAxis domain={['dataMin', 'dataMax']} hide />
          <Tooltip
            contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a', borderRadius: '8px', fontSize: '12px' }}
            labelStyle={{ color: '#8b8e98' }}
            formatter={(val) => [`${val >= 0 ? '+' : ''}${val.toLocaleString()}원`, '일일 P&L']}
            labelFormatter={(label) => label}
          />
          <Bar dataKey="daily_pnl" fill="#6366f1" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // Cumulative P&L line chart
  const isPositive = data[data.length - 1]?.cumulative_pnl >= 0

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <YAxis domain={['dataMin', 'dataMax']} hide />
        <Tooltip
          contentStyle={{
            background: '#1a1d27',
            border: '1px solid #2a2d3a',
            borderRadius: '8px',
            fontSize: '12px',
          }}
          labelStyle={{ color: '#8b8e98' }}
          formatter={(val) => [`${val >= 0 ? '+' : ''}${val.toLocaleString()}원`, '누적 P&L']}
          labelFormatter={(label) => label}
        />
        <defs>
          <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0.3} />
            <stop offset="95%" stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone" dataKey="cumulative_pnl"
          fill="url(#pnlGradient)" stroke="none"
        />
        <Line
          type="monotone" dataKey="cumulative_pnl"
          stroke={isPositive ? '#22c55e' : '#ef4444'}
          strokeWidth={2} dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
