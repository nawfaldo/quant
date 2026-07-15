import { useEffect, type ReactNode } from 'react'

interface ModalShellProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  className?: string
}

export default function ModalShell({ open, onClose, title, children, className = '' }: ModalShellProps) {
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className={`relative z-10 w-[500px] bg-[#1A1A1E] backdrop-blur rounded-lg shadow-2xl flex flex-col ${className}`}>
        <div className="px-4 py-3 flex items-center justify-between">
          <span className="text-xs font-semibold tracking-widest uppercase text-gray-500">{title}</span>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 transition-colors p-0.5" aria-label={`Close ${title}`}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
