import { LineChart, Line, Area, ResponsiveContainer, YAxis, Tooltip } from 'recharts'

export default function EquityChart({ data, height = 80 }) {
  if (!data || data.length < 2) return null

  const chartData = data.map((v, i) => ({ i, value: v }))
  const isPositive = data[data.length - 1] >= data[0]

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={chartData}>
        <YAxis domain={['dataMin', 'dataMax']} hide />
        <Tooltip
          contentStyle={{
            background: '#1a1d27',
            border: '1px solid #2a2d3a',
            borderRadius: '8px',
            fontSize: '12px',
          }}
          labelStyle={{ color: '#8b8e98' }}
          formatter={(val) => [val.toLocaleString(), '자산']}
        />
        <defs>
          <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0.3} />
            <stop offset="95%" stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone" dataKey="value"
          fill="url(#equityGradient)" stroke="none"
        />
        <Line
          type="monotone" dataKey="value"
          stroke={isPositive ? '#22c55e' : '#ef4444'}
          strokeWidth={2} dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
