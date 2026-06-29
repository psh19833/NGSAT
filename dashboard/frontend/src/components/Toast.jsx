export default function Toast({ toast, onClose }) {
  if (!toast) return null
  const bg = toast.type === 'success' ? 'bg-ngsat-green/90 text-white' : 'bg-ngsat-accent/90 text-white'

  return (
    <div
      className={'fixed bottom-6 right-6 px-4 py-3 rounded-lg shadow-lg text-sm font-medium z-50 transition-all ' + bg}
      role="alert"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <span>{toast.message}</span>
        <button onClick={onClose} className="ml-2 opacity-60 hover:opacity-100" aria-label="알림 닫기">&times;</button>
      </div>
    </div>
  )
}
