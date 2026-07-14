import { useEffect } from 'react'
import type { Indicators } from '../types'
import LiquidGlassSwitch from './LiquidGlassSwitch'

interface Props {
  open: boolean
  onClose: () => void
  indicators: Indicators
  onToggle: (key: keyof Indicators) => void
}

const INDICATOR_ROWS: { key: keyof Indicators; label: string; color: string }[] = [
  { key: 'session_volume_profile', label: 'Session Volume Profile', color: '#ffffff' },
  { key: 'volume', label: 'Volume Bars', color: '#9ca3af' },
  { key: 'volume_delta_bubbles', label: 'Volume Delta Bubbles', color: '#089981' },
  { key: 'vwap', label: 'VWAP', color: '#60a5fa' },
  { key: 'noise_area', label: 'Noise Area', color: '#f43f5e' },
]

export default function IndicatorsModal({ open, onClose, indicators, onToggle }: Props) {
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
      <div className="relative z-10 w-[500px] h-[300px] bg-[#1A1A1E] backdrop-blur rounded-lg shadow-2xl flex flex-col">
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
          {INDICATOR_ROWS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => onToggle(key)}
              aria-pressed={indicators[key] === true}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-white/5 transition-colors text-left"
            >
              <span className="flex-1 text-sm text-gray-300">{label}</span>
              <LiquidGlassSwitch checked={indicators[key] === true} />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
