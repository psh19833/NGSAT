import { useEffect, useCallback } from 'react'

export default function ConfirmModal({ open, title, message, confirmLabel, onConfirm, onCancel }) {
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape' && open) {
      onCancel?.()
    }
  }, [open, onCancel])

  useEffect(() => {
    if (open) {
      document.addEventListener('keydown', handleKeyDown)
      return () => document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open, handleKeyDown])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
    >
      <div className="ngsat-card p-6 max-w-sm w-full mx-4 shadow-xl">
        <div className="text-center">
          <div className="text-3xl mb-3">⚠️</div>
          <h3 id="confirm-modal-title" className="text-lg font-semibold text-ngsat-text mb-2">{title}</h3>
          <p className="text-sm text-ngsat-muted mb-6">{message}</p>
          <div className="flex gap-3 justify-center">
            <button
              onClick={onCancel}
              className="px-5 py-2 text-sm rounded-lg border border-ngsat-border text-ngsat-muted hover:text-ngsat-text transition-all"
              aria-label="취소"
            >
              취소
            </button>
            <button
              onClick={onConfirm}
              className="px-5 py-2 text-sm rounded-lg bg-ngsat-red text-white hover:opacity-90 transition-all"
              aria-label={confirmLabel || '실행'}
              autoFocus
            >
              {confirmLabel || '실행'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
