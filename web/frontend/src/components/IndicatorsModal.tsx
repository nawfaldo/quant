import { useEffect } from 'react'
import type { Indicators } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  indicators: Indicators
  onToggle: (key: keyof Indicators) => void
  isNq: boolean
}

const INDICATOR_ROWS: { key: keyof Indicators; label: string; color: string; nqOnly: boolean }[] = [
  { key: 'vwap', label: 'VWAP', color: '#60a5fa', nqOnly: true },
  { key: 'openingRange', label: 'Opening Range (9:30-10:00)', color: '#ef4444', nqOnly: true },
]

export default function IndicatorsModal({ open, onClose, indicators, onToggle, isNq }: Props) {
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative z-10 w-[500px] h-[300px] bg-[#222831] backdrop-blur rounded-lg shadow-2xl flex flex-col">
        <div className="px-4 py-3 flex items-center justify-between">
          <span className="text-xs font-semibold tracking-widest uppercase text-gray-500">Indicators</span>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 transition-colors p-0.5">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="px-2 py-2">
          {INDICATOR_ROWS.filter(({ nqOnly }) => !nqOnly || isNq).map(({ key, label }) => (
            <button
              key={key}
              onClick={() => onToggle(key)}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-white/5 transition-colors text-left"
            >
              <span className="flex-1 text-sm text-gray-300">{label}</span>
              <span
                className={`w-8 h-4 rounded-full transition-colors flex-shrink-0 relative ${indicators[key] ? 'bg-blue-500' : 'bg-gray-700'}`}
              >
                <span
                  className={`absolute left-0 top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${indicators[key] ? 'translate-x-4' : 'translate-x-0.5'}`}
                />
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
