export default function EvidenceBox({ evidence }) {
  if (!evidence || typeof evidence !== 'object' || Object.keys(evidence).length === 0) {
    return null
  }

  const entries = Object.entries(evidence).slice(0, 10)

  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs mt-3 pt-3 border-t border-ngsat-border/50">
      {entries.map(([key, value]) => (
        <div key={key} className="flex justify-between">
          <span className="text-ngsat-muted">{key.replace(/_/g, ' ')}</span>
          <span className="text-ngsat-text font-mono tabular-nums">
            {typeof value === 'number' ? value.toFixed(2) : String(value)}
          </span>
        </div>
      ))}
      {Object.keys(evidence).length > 10 && (
        <div className="col-span-2 text-ngsat-muted text-[10px] text-center mt-1">
          외 {Object.keys(evidence).length - 10}개 항목
        </div>
      )}
    </div>
  )
}
