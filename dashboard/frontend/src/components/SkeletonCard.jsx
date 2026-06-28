export default function SkeletonCard({ lines = 3, className = '' }) {
  return (
    <div className={`ngsat-card p-6 animate-pulse ${className}`}>
      <div className="space-y-3">
        <div className="h-4 bg-ngsat-border rounded w-1/3" />
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="h-3 bg-ngsat-border rounded"
            style={{ width: `${70 + (i * 10) % 30}%` }}
          />
        ))}
      </div>
    </div>
  )
}

export function SkeletonTable({ rows = 5, cols = 4 }) {
  return (
    <div className="animate-pulse space-y-2">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-6 py-3">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="h-4 bg-ngsat-border rounded flex-1"
              style={{ width: `${60 + (c * 5) % 40}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function SkeletonChart() {
  return (
    <div className="animate-pulse">
      <div className="h-16 bg-ngsat-border rounded w-full" />
    </div>
  )
}
