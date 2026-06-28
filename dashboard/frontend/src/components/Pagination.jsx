export default function Pagination({ page, totalPages, onChange }) {
  if (totalPages <= 1) return null

  const pages = []
  const start = Math.max(1, page - 2)
  const end = Math.min(totalPages, page + 2)

  for (let i = start; i <= end; i++) {
    pages.push(i)
  }

  return (
    <div className="flex items-center justify-center gap-1 mt-4">
      <button
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
        className="px-3 py-1.5 text-xs rounded bg-ngsat-border/50 text-ngsat-muted 
          hover:bg-ngsat-border hover:text-ngsat-text transition-all
          disabled:opacity-30 disabled:cursor-not-allowed"
      >
        ←
      </button>

      {start > 1 && (
        <>
          <button onClick={() => onChange(1)}
            className="px-3 py-1.5 text-xs rounded text-ngsat-muted hover:text-ngsat-text">
            1
          </button>
          {start > 2 && <span className="text-ngsat-muted text-xs px-1">...</span>}
        </>
      )}

      {pages.map(p => (
        <button
          key={p}
          onClick={() => onChange(p)}
          className={`px-3 py-1.5 text-xs rounded transition-all
            ${p === page
              ? 'bg-ngsat-accent text-white font-medium'
              : 'text-ngsat-muted hover:text-ngsat-text hover:bg-ngsat-border/50'
            }`}
        >
          {p}
        </button>
      ))}

      {end < totalPages && (
        <>
          {end < totalPages - 1 && <span className="text-ngsat-muted text-xs px-1">...</span>}
          <button onClick={() => onChange(totalPages)}
            className="px-3 py-1.5 text-xs rounded text-ngsat-muted hover:text-ngsat-text">
            {totalPages}
          </button>
        </>
      )}

      <button
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
        className="px-3 py-1.5 text-xs rounded bg-ngsat-border/50 text-ngsat-muted 
          hover:bg-ngsat-border hover:text-ngsat-text transition-all
          disabled:opacity-30 disabled:cursor-not-allowed"
      >
        →
      </button>
    </div>
  )
}
