import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchBacktests } from '../api'
import type { Backtest } from '../types'
import { SpinnerIcon } from './icons'

interface Props {
  open: boolean
  onClose: () => void
  visibleIds: Set<number>
  loadingIds: Set<number>
  onToggle: (id: number) => void
  activeSymbol: string
}

export default function BacktestsModal({ open, onClose, visibleIds, loadingIds, onToggle, activeSymbol }: Props) {
  const { data: backtests, isLoading, isError } = useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
    enabled: open,
    staleTime: Infinity,
  })

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
          <span className="text-xs font-semibold tracking-widest uppercase text-gray-500">Backtests</span>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 transition-colors p-0.5">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="overflow-y-auto px-3 pb-3 space-y-1 mt-3">
          {isLoading && <div className="text-xs text-gray-600 text-center py-6">Loading…</div>}
          {isError && <div className="text-xs text-red-400 text-center py-6">Failed to load backtests</div>}
          {backtests?.filter(b => b.symbol.toLowerCase() === activeSymbol.toLowerCase()).map(b => (
            <BacktestRow
              key={b.id}
              backtest={b}
              visible={visibleIds.has(b.id)}
              loading={loadingIds.has(b.id)}
              onToggle={() => onToggle(b.id)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function BacktestRow({ backtest, visible, loading, onToggle }: {
  backtest: Backtest
  visible: boolean
  loading: boolean
  onToggle: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-1 py-2 rounded-lg">
      <span className="text-xs text-gray-300 truncate">{backtest.strategy} <span className="text-gray-600">#{backtest.id}</span></span>
        {loading ? (
          <SpinnerIcon />
        ) : (
          <button
            onClick={onToggle}
            className={`w-8 h-4 rounded-full transition-colors flex-shrink-0 relative focus:outline-none ${visible ? 'bg-blue-500' : 'bg-gray-700'}`}
          >
            <span
              className={`absolute left-0 top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${visible ? 'translate-x-4' : 'translate-x-0.5'}`}
            />
          </button>
        )}
    </div>
  )
}
