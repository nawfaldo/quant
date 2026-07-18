import { useEffect, type ReactNode } from 'react'

interface ModalShellProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  className?: string
  closePosition?: 'left' | 'right'
  headerExtra?: ReactNode
}

export default function ModalShell({
  open,
  onClose,
  title,
  children,
  className = '',
  closePosition = 'right',
  headerExtra,
}: ModalShellProps) {
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  const closeButton = (
    <button
      onClick={onClose}
      className={`group text-gray-400 hover:text-white transition-colors p-1 cursor-pointer flex items-center justify-center shrink-0 ${
        closePosition === 'left' ? '-ml-1' : '-mr-1'
      }`}
      aria-label={`Close ${title}`}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <line x1="2.5" y1="2.5" x2="13.5" y2="13.5" stroke="currentColor" className="stroke-[1.8] group-hover:stroke-[2.6] transition-all duration-150" strokeLinecap="round" />
        <line x1="13.5" y1="2.5" x2="2.5" y2="13.5" stroke="currentColor" className="stroke-[1.8] group-hover:stroke-[2.6] transition-all duration-150" strokeLinecap="round" />
      </svg>
    </button>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className={`relative z-10 w-[580px] h-[480px] bg-[#1A1A1E] backdrop-blur rounded-2xl overflow-hidden border border-white/5 shadow-2xl flex flex-col ${className}`}>
        <div className="px-4 py-3 flex items-center gap-2.5 shrink-0">
          {closePosition === 'left' ? (
            <>
              {closeButton}
              <span className="text-sm font-semibold text-gray-200 shrink-0">{title}</span>
              {headerExtra}
            </>
          ) : (
            <>
              <span className="text-sm font-semibold text-gray-200 shrink-0">{title}</span>
              {headerExtra}
              {closeButton}
            </>
          )}
        </div>
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">{children}</div>
      </div>
    </div>
  )
}
